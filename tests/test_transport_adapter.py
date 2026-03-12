"""
Unit tests for the transport adapter module (orchestrator/transport.py).

Tests verify that COMM_MODE env var controls which backend (gRPC client.py
or queue queue_client.py) is re-exported, and that the default is gRPC.
"""
import importlib
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# sys.path setup (same pattern as test_queue_infrastructure.py)
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")

if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DOMAIN_FUNCTIONS = [
    "reserve_stock", "release_stock", "check_stock",
    "charge_payment", "refund_payment", "check_payment",
    "prepare_stock", "commit_stock", "abort_stock",
    "prepare_payment", "commit_payment", "abort_payment",
]


def _fresh_import_transport():
    """Clear transport from module cache and re-import to pick up new COMM_MODE."""
    sys.modules.pop("transport", None)
    import transport  # noqa: E402
    return transport


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_grpc_mode_exports(monkeypatch):
    """With COMM_MODE=grpc, transport exports the same objects as client.py."""
    monkeypatch.setenv("COMM_MODE", "grpc")
    transport = _fresh_import_transport()
    import client

    for fn_name in DOMAIN_FUNCTIONS:
        assert getattr(transport, fn_name) is getattr(client, fn_name), (
            f"transport.{fn_name} should be client.{fn_name} in gRPC mode"
        )


def test_queue_mode_exports(monkeypatch):
    """With COMM_MODE=queue, transport exports the same objects as queue_client.py."""
    monkeypatch.setenv("COMM_MODE", "queue")
    transport = _fresh_import_transport()
    import queue_client

    for fn_name in DOMAIN_FUNCTIONS:
        assert getattr(transport, fn_name) is getattr(queue_client, fn_name), (
            f"transport.{fn_name} should be queue_client.{fn_name} in queue mode"
        )


def test_default_mode_is_grpc(monkeypatch):
    """With COMM_MODE unset, transport defaults to gRPC mode."""
    monkeypatch.delenv("COMM_MODE", raising=False)
    transport = _fresh_import_transport()
    assert transport.COMM_MODE == "grpc"


def test_all_expected_names_exported(monkeypatch):
    """__all__ contains COMM_MODE plus all 6 domain function names."""
    monkeypatch.setenv("COMM_MODE", "grpc")
    transport = _fresh_import_transport()
    expected = {"COMM_MODE"} | set(DOMAIN_FUNCTIONS)
    assert set(transport.__all__) == expected
