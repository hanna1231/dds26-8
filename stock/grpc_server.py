import json
import grpc
import grpc.aio
from msgspec import msgpack, Struct
from stock_pb2 import StockResponse, CheckStockResponse
from stock_pb2_grpc import StockServiceServicer as StockServiceServicerBase, add_StockServiceServicer_to_server


class StockValue(Struct):
    stock: int
    price: int


IDEMPOTENCY_ACQUIRE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
redis.call('SET', KEYS[1], '__PROCESSING__', 'EX', ARGV[1])
return '__NEW__'
"""


class StockServiceServicer(StockServiceServicerBase):
    def __init__(self, db):
        self.db = db

    async def ReserveStock(self, request, context):
        ikey = f"idempotency:{request.idempotency_key}"
        result = await self.db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
        if isinstance(result, bytes):
            result = result.decode()

        if result == "__PROCESSING__":
            return StockResponse(success=False, error_message="operation in progress, retry")

        if result != "__NEW__":
            # Cached result — return it
            cached = json.loads(result)
            return StockResponse(success=cached["success"], error_message=cached["error_message"])

        # First call — execute business logic
        entry: bytes = await self.db.get(request.item_id)
        if entry is None:
            fail = json.dumps({"success": False, "error_message": "item not found"})
            await self.db.set(ikey, fail, ex=86400)
            return StockResponse(success=False, error_message="item not found")

        item_entry: StockValue = msgpack.decode(entry, type=StockValue)
        item_entry.stock -= request.quantity
        if item_entry.stock < 0:
            fail = json.dumps({"success": False, "error_message": "insufficient stock"})
            await self.db.set(ikey, fail, ex=86400)
            return StockResponse(success=False, error_message="insufficient stock")

        await self.db.set(request.item_id, msgpack.encode(item_entry))
        success = json.dumps({"success": True, "error_message": ""})
        await self.db.set(ikey, success, ex=86400)
        return StockResponse(success=True, error_message="")

    async def ReleaseStock(self, request, context):
        ikey = f"idempotency:{request.idempotency_key}"
        result = await self.db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
        if isinstance(result, bytes):
            result = result.decode()

        if result == "__PROCESSING__":
            return StockResponse(success=False, error_message="operation in progress, retry")

        if result != "__NEW__":
            cached = json.loads(result)
            return StockResponse(success=cached["success"], error_message=cached["error_message"])

        # First call — execute business logic
        entry: bytes = await self.db.get(request.item_id)
        if entry is None:
            fail = json.dumps({"success": False, "error_message": "item not found"})
            await self.db.set(ikey, fail, ex=86400)
            return StockResponse(success=False, error_message="item not found")

        item_entry: StockValue = msgpack.decode(entry, type=StockValue)
        item_entry.stock += request.quantity
        await self.db.set(request.item_id, msgpack.encode(item_entry))
        success = json.dumps({"success": True, "error_message": ""})
        await self.db.set(ikey, success, ex=86400)
        return StockResponse(success=True, error_message="")

    async def CheckStock(self, request, context):
        entry: bytes = await self.db.get(request.item_id)
        if entry is None:
            return CheckStockResponse(success=False, error_message="item not found", stock=0, price=0)
        item_entry: StockValue = msgpack.decode(entry, type=StockValue)
        return CheckStockResponse(success=True, error_message="", stock=item_entry.stock, price=item_entry.price)


_grpc_server: grpc.aio.Server = None


async def serve_grpc(db) -> None:
    global _grpc_server
    _grpc_server = grpc.aio.server()
    add_StockServiceServicer_to_server(StockServiceServicer(db), _grpc_server)
    _grpc_server.add_insecure_port("[::]:50051")
    await _grpc_server.start()
    await _grpc_server.wait_for_termination()


async def stop_grpc_server():
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5.0)
