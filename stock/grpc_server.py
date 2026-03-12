import grpc
import grpc.aio
import operations
from stock_pb2 import StockResponse, CheckStockResponse
from stock_pb2_grpc import StockServiceServicer as StockServiceServicerBase, add_StockServiceServicer_to_server


class StockServiceServicer(StockServiceServicerBase):
    def __init__(self, db):
        self.db = db

    async def ReserveStock(self, request, context):
        result = await operations.reserve_stock(
            self.db, request.item_id, request.quantity, request.idempotency_key
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])

    async def ReleaseStock(self, request, context):
        result = await operations.release_stock(
            self.db, request.item_id, request.quantity, request.idempotency_key
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])

    async def CheckStock(self, request, context):
        result = await operations.check_stock(self.db, request.item_id)
        return CheckStockResponse(
            success=result["success"], error_message=result["error_message"],
            stock=result["stock"], price=result["price"]
        )

    async def PrepareStock(self, request, context):
        result = await operations.prepare_stock(
            self.db, request.item_id, request.quantity, request.order_id
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])

    async def CommitStock(self, request, context):
        result = await operations.commit_stock(
            self.db, request.item_id, request.order_id
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])

    async def AbortStock(self, request, context):
        result = await operations.abort_stock(
            self.db, request.item_id, request.order_id
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])


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
