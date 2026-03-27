"""
Orchestrator gRPC server.

Implements StartCheckout RPC which delegates to WorkflowEngine:
  - Engine routes to SagaStrategy or TwoPhaseStrategy based on TRANSACTION_PATTERN
  - Exactly-once semantics and compensation handled inside strategy classes
  - Circuit breaker errors handled inside SagaStrategy.execute()
"""
import logging
import os

import grpc
import grpc.aio

from orchestrator_pb2 import CheckoutResponse
from orchestrator_pb2_grpc import (
    OrchestratorServiceServicer as OrchestratorServiceServicerBase,
    add_OrchestratorServiceServicer_to_server,
)
from workflow_engine import WorkflowEngine
from checkout_workflow import make_checkout_workflow

TRANSACTION_PATTERN = os.environ.get("TRANSACTION_PATTERN", "saga")


# ---------------------------------------------------------------------------
# gRPC servicer
# ---------------------------------------------------------------------------

class OrchestratorServiceServicer(OrchestratorServiceServicerBase):
    def __init__(self, db, engine: WorkflowEngine):
        self.db = db
        self.engine = engine

    async def StartCheckout(self, request, context):
        items = [{"item_id": item.item_id, "quantity": item.quantity} for item in request.items]
        workflow_context = {
            "order_id": request.order_id,
            "user_id": request.user_id,
            "items": items,
            "total_cost": request.total_cost,
        }
        definition = make_checkout_workflow(TRANSACTION_PATTERN)
        result = await self.engine.execute(request.order_id, definition, workflow_context)
        return CheckoutResponse(
            success=result["success"],
            error_message=result["error_message"],
        )


# ---------------------------------------------------------------------------
# gRPC server lifecycle — port 50053 (Stock=50051, Payment=50052)
# ---------------------------------------------------------------------------

_grpc_server: grpc.aio.Server = None


async def serve_grpc(db, engine) -> None:
    global _grpc_server
    _grpc_server = grpc.aio.server()
    add_OrchestratorServiceServicer_to_server(OrchestratorServiceServicer(db, engine), _grpc_server)
    _grpc_server.add_insecure_port("[::]:50053")
    await _grpc_server.start()
    await _grpc_server.wait_for_termination()


async def stop_grpc_server() -> None:
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5.0)
