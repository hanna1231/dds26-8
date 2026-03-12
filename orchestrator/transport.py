"""
Transport adapter: conditional re-export of domain functions based on COMM_MODE.

COMM_MODE=grpc (default)  -> functions from client.py  (gRPC transport)
COMM_MODE=queue           -> functions from queue_client.py (Redis Streams transport)

Callers import from this module and stay transport-agnostic.
init/close are NOT re-exported -- they have different signatures and are
handled directly in app.py.
"""
import logging
import os

COMM_MODE = os.environ.get("COMM_MODE", "grpc")
logging.info("Transport adapter: COMM_MODE=%s", COMM_MODE)

if COMM_MODE == "queue":
    from queue_client import (  # noqa: F401
        reserve_stock,
        release_stock,
        check_stock,
        charge_payment,
        refund_payment,
        check_payment,
    )
else:
    from client import (  # noqa: F401
        reserve_stock,
        release_stock,
        check_stock,
        charge_payment,
        refund_payment,
        check_payment,
    )

__all__ = [
    "COMM_MODE",
    "reserve_stock",
    "release_stock",
    "check_stock",
    "charge_payment",
    "refund_payment",
    "check_payment",
]
