import grpc
import grpc.aio
import operations
from payment_pb2 import PaymentResponse, CheckPaymentResponse
from payment_pb2_grpc import PaymentServiceServicer as PaymentServiceServicerBase, add_PaymentServiceServicer_to_server


class PaymentServiceServicer(PaymentServiceServicerBase):
    def __init__(self, db):
        self.db = db

    async def ChargePayment(self, request, context):
        result = await operations.charge_payment(
            self.db, request.user_id, request.amount, request.idempotency_key
        )
        return PaymentResponse(success=result["success"], error_message=result["error_message"])

    async def RefundPayment(self, request, context):
        result = await operations.refund_payment(
            self.db, request.user_id, request.amount, request.idempotency_key
        )
        return PaymentResponse(success=result["success"], error_message=result["error_message"])

    async def CheckPayment(self, request, context):
        result = await operations.check_payment(self.db, request.user_id)
        return CheckPaymentResponse(
            success=result["success"], error_message=result["error_message"],
            credit=result["credit"]
        )


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
