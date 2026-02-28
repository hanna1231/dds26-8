import json
import grpc
import grpc.aio
from msgspec import msgpack, Struct
from payment_pb2 import PaymentResponse, CheckPaymentResponse
from payment_pb2_grpc import PaymentServiceServicer as PaymentServiceServicerBase, add_PaymentServiceServicer_to_server


class UserValue(Struct):
    credit: int


IDEMPOTENCY_ACQUIRE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
redis.call('SET', KEYS[1], '__PROCESSING__', 'EX', ARGV[1])
return '__NEW__'
"""


class PaymentServiceServicer(PaymentServiceServicerBase):
    def __init__(self, db):
        self.db = db

    async def ChargePayment(self, request, context):
        ikey = f"idempotency:{request.idempotency_key}"
        result = await self.db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
        if isinstance(result, bytes):
            result = result.decode()

        if result == "__PROCESSING__":
            return PaymentResponse(success=False, error_message="operation in progress, retry")

        if result != "__NEW__":
            cached = json.loads(result)
            return PaymentResponse(success=cached["success"], error_message=cached["error_message"])

        # First call — execute business logic
        entry: bytes = await self.db.get(request.user_id)
        if entry is None:
            fail = json.dumps({"success": False, "error_message": "user not found"})
            await self.db.set(ikey, fail, ex=86400)
            return PaymentResponse(success=False, error_message="user not found")

        user_entry: UserValue = msgpack.decode(entry, type=UserValue)
        user_entry.credit -= request.amount
        if user_entry.credit < 0:
            fail = json.dumps({"success": False, "error_message": "insufficient credit"})
            await self.db.set(ikey, fail, ex=86400)
            return PaymentResponse(success=False, error_message="insufficient credit")

        await self.db.set(request.user_id, msgpack.encode(user_entry))
        success = json.dumps({"success": True, "error_message": ""})
        await self.db.set(ikey, success, ex=86400)
        return PaymentResponse(success=True, error_message="")

    async def RefundPayment(self, request, context):
        ikey = f"idempotency:{request.idempotency_key}"
        result = await self.db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
        if isinstance(result, bytes):
            result = result.decode()

        if result == "__PROCESSING__":
            return PaymentResponse(success=False, error_message="operation in progress, retry")

        if result != "__NEW__":
            cached = json.loads(result)
            return PaymentResponse(success=cached["success"], error_message=cached["error_message"])

        # First call — execute business logic
        entry: bytes = await self.db.get(request.user_id)
        if entry is None:
            fail = json.dumps({"success": False, "error_message": "user not found"})
            await self.db.set(ikey, fail, ex=86400)
            return PaymentResponse(success=False, error_message="user not found")

        user_entry: UserValue = msgpack.decode(entry, type=UserValue)
        user_entry.credit += request.amount
        await self.db.set(request.user_id, msgpack.encode(user_entry))
        success = json.dumps({"success": True, "error_message": ""})
        await self.db.set(ikey, success, ex=86400)
        return PaymentResponse(success=True, error_message="")

    async def CheckPayment(self, request, context):
        entry: bytes = await self.db.get(request.user_id)
        if entry is None:
            return CheckPaymentResponse(success=False, error_message="user not found", credit=0)
        user_entry: UserValue = msgpack.decode(entry, type=UserValue)
        return CheckPaymentResponse(success=True, error_message="", credit=user_entry.credit)


_grpc_server: grpc.aio.Server = None


async def serve_grpc(db) -> None:
    global _grpc_server
    _grpc_server = grpc.aio.server()
    add_PaymentServiceServicer_to_server(PaymentServiceServicer(db), _grpc_server)
    _grpc_server.add_insecure_port("[::]:50051")
    await _grpc_server.start()
    await _grpc_server.wait_for_termination()


async def stop_grpc_server():
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5.0)
