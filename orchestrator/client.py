import os

import grpc.aio

from stock_pb2 import ReserveStockRequest, ReleaseStockRequest, CheckStockRequest
from stock_pb2_grpc import StockServiceStub
from payment_pb2 import ChargePaymentRequest, RefundPaymentRequest, CheckPaymentRequest
from payment_pb2_grpc import PaymentServiceStub

_stock_channel = None
_payment_channel = None
_stock_stub: StockServiceStub = None
_payment_stub: PaymentServiceStub = None

RPC_TIMEOUT = 5.0  # seconds, per locked decision

STOCK_ADDR = os.environ.get("STOCK_GRPC_ADDR", "stock-service:50051")
PAYMENT_ADDR = os.environ.get("PAYMENT_GRPC_ADDR", "payment-service:50051")


async def init_grpc_clients(stock_addr: str = None, payment_addr: str = None) -> None:
    """Initialise module-level channels and stubs. Call once at application startup."""
    global _stock_channel, _payment_channel, _stock_stub, _payment_stub

    stock_target = stock_addr or STOCK_ADDR
    payment_target = payment_addr or PAYMENT_ADDR

    _stock_channel = grpc.aio.insecure_channel(stock_target)
    _payment_channel = grpc.aio.insecure_channel(payment_target)

    _stock_stub = StockServiceStub(_stock_channel)
    _payment_stub = PaymentServiceStub(_payment_channel)


async def close_grpc_clients() -> None:
    """Close module-level channels. Call once at application shutdown."""
    global _stock_channel, _payment_channel

    if _stock_channel is not None:
        await _stock_channel.close()
        _stock_channel = None

    if _payment_channel is not None:
        await _payment_channel.close()
        _payment_channel = None


# ---------------------------------------------------------------------------
# Stock wrappers
# ---------------------------------------------------------------------------

async def reserve_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    resp = await _stock_stub.ReserveStock(
        ReserveStockRequest(item_id=item_id, quantity=quantity, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}


async def release_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    resp = await _stock_stub.ReleaseStock(
        ReleaseStockRequest(item_id=item_id, quantity=quantity, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}


async def check_stock(item_id: str) -> dict:
    resp = await _stock_stub.CheckStock(
        CheckStockRequest(item_id=item_id),
        timeout=RPC_TIMEOUT,
    )
    return {
        "success": resp.success,
        "error_message": resp.error_message,
        "stock": resp.stock,
        "price": resp.price,
    }


# ---------------------------------------------------------------------------
# Payment wrappers
# ---------------------------------------------------------------------------

async def charge_payment(user_id: str, amount: int, idempotency_key: str) -> dict:
    resp = await _payment_stub.ChargePayment(
        ChargePaymentRequest(user_id=user_id, amount=amount, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}


async def refund_payment(user_id: str, amount: int, idempotency_key: str) -> dict:
    resp = await _payment_stub.RefundPayment(
        RefundPaymentRequest(user_id=user_id, amount=amount, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}


async def check_payment(user_id: str) -> dict:
    resp = await _payment_stub.CheckPayment(
        CheckPaymentRequest(user_id=user_id),
        timeout=RPC_TIMEOUT,
    )
    return {
        "success": resp.success,
        "error_message": resp.error_message,
        "credit": resp.credit,
    }
