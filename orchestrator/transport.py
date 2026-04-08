"""
Transport adapter: re-exports domain functions from the queue client.

All orchestrator -> payment/stock communication goes over Redis Streams.
Callers import from this module and stay transport-agnostic.
init/close are NOT re-exported -- they have different signatures and are
handled directly in app.py.
"""
from queue_client import (  # noqa: F401
    reserve_stock,
    release_stock,
    check_stock,
    charge_payment,
    refund_payment,
    check_payment,
    prepare_stock,
    commit_stock,
    abort_stock,
    prepare_payment,
    commit_payment,
    abort_payment,
)

__all__ = [
    "reserve_stock",
    "release_stock",
    "check_stock",
    "charge_payment",
    "refund_payment",
    "check_payment",
    "prepare_stock",
    "commit_stock",
    "abort_stock",
    "prepare_payment",
    "commit_payment",
    "abort_payment",
]
