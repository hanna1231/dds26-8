#!/usr/bin/env python3
"""
Kill-container consistency test.

Populates the system with test data, fires concurrent checkouts,
kills a target container mid-flight, waits for recovery, then
asserts that the final state is consistent (no lost money or items).

Usage:
    python scripts/kill_test.py --service stock-service
    python scripts/kill_test.py --service payment-service
    python scripts/kill_test.py --service order-service
    python scripts/kill_test.py --service orchestrator-service
    python scripts/kill_test.py --all  # test all services sequentially
"""
import argparse
import asyncio
import os
import subprocess
import sys
import time

import aiohttp
import requests

GATEWAY = "http://localhost:8000"
RECOVERY_WAIT = 30  # seconds — matches CONTEXT.md decision
NUM_USERS = 50  # smaller than benchmark (faster, still exercises concurrency)
INITIAL_STOCK = 20
ITEM_PRICE = 1
INITIAL_CREDIT = 1  # per user

SERVICES = ["order-service", "stock-service", "payment-service", "orchestrator-service"]


def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run shell command, print it, return result."""
    print(f"  $ {cmd}")
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


def wait_for_gateway(timeout: int = 60) -> bool:
    """Wait until the gateway is responsive."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{GATEWAY}/stock/find/nonexistent", timeout=2)
            # Any response (even 404) means gateway is up
            return True
        except Exception:
            time.sleep(1)
    return False


def populate() -> tuple[str, list[str]]:
    """
    Seed test data: 1 item with INITIAL_STOCK units at ITEM_PRICE,
    NUM_USERS users with INITIAL_CREDIT each.
    Returns (item_id, [user_ids]).
    """
    # Create item
    r = requests.post(f"{GATEWAY}/stock/item/create/{ITEM_PRICE}")
    r.raise_for_status()
    item_id = r.json()["item_id"]

    # Add stock
    r = requests.post(f"{GATEWAY}/stock/add/{item_id}/{INITIAL_STOCK}")
    r.raise_for_status()

    # Create users with credit
    user_ids = []
    for _ in range(NUM_USERS):
        r = requests.post(f"{GATEWAY}/payment/create_user")
        r.raise_for_status()
        uid = r.json()["user_id"]
        r = requests.post(f"{GATEWAY}/payment/add_funds/{uid}/{INITIAL_CREDIT}")
        r.raise_for_status()
        user_ids.append(uid)

    print(f"  Populated: item={item_id} (stock={INITIAL_STOCK}), {len(user_ids)} users (credit={INITIAL_CREDIT} each)")
    return item_id, user_ids


async def fire_checkouts(item_id: str, user_ids: list[str]) -> list[int]:
    """
    Fire concurrent checkout requests. Each user creates an order,
    adds 1 of the item, and checks out.
    Returns list of status codes.
    """
    async def single_checkout(session: aiohttp.ClientSession, uid: str) -> int:
        try:
            # Create order
            async with session.post(f"{GATEWAY}/orders/create/{uid}") as r:
                if r.status >= 400:
                    return r.status
                data = await r.json()
                order_id = data["order_id"]

            # Add item
            async with session.post(f"{GATEWAY}/orders/addItem/{order_id}/{item_id}/1") as r:
                if r.status >= 400:
                    return r.status

            # Checkout
            async with session.post(f"{GATEWAY}/orders/checkout/{order_id}") as r:
                return r.status
        except Exception as e:
            print(f"    Checkout error for user {uid}: {e}")
            return 500

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [single_checkout(session, uid) for uid in user_ids]
        return await asyncio.gather(*tasks)


def assert_consistency(item_id: str, user_ids: list[str], initial_stock: int, initial_credits: int):
    """
    Assert: credits_deducted == stock_consumed (no money lost, no double-spend).
    This holds regardless of whether recovery ran via compensation or forward completion.
    """
    # Get remaining stock
    r = requests.get(f"{GATEWAY}/stock/find/{item_id}")
    r.raise_for_status()
    remaining_stock = r.json()["stock"]
    stock_consumed = initial_stock - remaining_stock

    # Sum remaining credits
    total_credits = 0
    for uid in user_ids:
        r = requests.get(f"{GATEWAY}/payment/find_user/{uid}")
        r.raise_for_status()
        total_credits += r.json()["credit"]
    credits_deducted = initial_credits - total_credits

    print(f"  Stock: {initial_stock} -> {remaining_stock} (consumed: {stock_consumed})")
    print(f"  Credits: {initial_credits} -> {total_credits} (deducted: {credits_deducted})")

    if credits_deducted != stock_consumed:
        print(f"  FAIL: {credits_deducted} credits deducted but {stock_consumed} stock consumed")
        return False

    print(f"  PASS: consistent ({credits_deducted} credits = {stock_consumed} stock)")
    return True


def flush_data():
    """Flush all Redis data via docker compose to start fresh."""
    # Restart all services to clear in-memory state and flush Redis
    run("docker compose restart", check=False)
    time.sleep(15)  # wait for services to come back up


def run_kill_test(service: str) -> bool:
    """
    Run a single kill-test scenario for the given service.
    Returns True if consistent, False otherwise.
    """
    print(f"\n{'='*60}")
    print(f"KILL TEST: {service}")
    print(f"{'='*60}")

    # Verify gateway is up
    print("\n[1] Waiting for gateway...")
    if not wait_for_gateway():
        print("  FAIL: Gateway not responsive")
        return False

    # Populate fresh data
    print("\n[2] Populating test data...")
    item_id, user_ids = populate()
    initial_credits = NUM_USERS * INITIAL_CREDIT

    # Start checkouts in background
    print(f"\n[3] Firing {NUM_USERS} concurrent checkouts + killing {service}...")

    # Use threading to fire checkouts while killing
    import threading
    results = []

    def do_checkouts():
        loop = asyncio.new_event_loop()
        r = loop.run_until_complete(fire_checkouts(item_id, user_ids))
        results.extend(r)
        loop.close()

    t = threading.Thread(target=do_checkouts)
    t.start()

    # Wait a short time for some checkouts to be in-flight, then kill
    time.sleep(1)
    print(f"\n[4] Killing {service}...")
    run(f"docker compose stop {service}", check=False)

    # Wait for checkout thread to finish (some will fail)
    t.join(timeout=120)

    # Restart the killed service
    print(f"\n[5] Restarting {service}...")
    run(f"docker compose start {service}", check=False)

    # Wait for recovery
    print(f"\n[6] Waiting {RECOVERY_WAIT}s for recovery...")
    time.sleep(RECOVERY_WAIT)

    # Wait for gateway to be responsive after recovery
    if not wait_for_gateway():
        print("  FAIL: Gateway not responsive after recovery")
        return False

    # Assert consistency
    print("\n[7] Asserting consistency...")
    success_count = sum(1 for s in results if 200 <= s < 300)
    fail_count = sum(1 for s in results if s >= 400)
    error_count = sum(1 for s in results if s == 500)
    print(f"  Checkouts: {success_count} succeeded, {fail_count} failed, {error_count} errors")

    return assert_consistency(item_id, user_ids, INITIAL_STOCK, initial_credits)


def main():
    parser = argparse.ArgumentParser(description="Kill-container consistency test")
    parser.add_argument("--service", type=str, help="Service to kill (e.g., stock-service)")
    parser.add_argument("--all", action="store_true", help="Test all services sequentially")
    args = parser.parse_args()

    if not args.service and not args.all:
        parser.print_help()
        sys.exit(1)

    services = SERVICES if args.all else [args.service]
    results = {}

    for svc in services:
        # Clean state between tests by restarting
        if len(services) > 1:
            print(f"\n--- Restarting cluster for fresh state before testing {svc} ---")
            comm_mode = os.environ.get("COMM_MODE", "grpc")
            txn_pattern = os.environ.get("TRANSACTION_PATTERN", "saga")
            run("docker compose down -v", check=False)
            run(
                f"SAGA_STALENESS_SECONDS=10 COMM_MODE={comm_mode} TRANSACTION_PATTERN={txn_pattern} "
                f"ORDER_REDIS_HOST=shared-redis-0 STOCK_REDIS_HOST=shared-redis-0 "
                f"PAYMENT_REDIS_HOST=shared-redis-0 ORCH_REDIS_HOST=shared-redis-0 "
                f"docker compose --profile simple up -d",
                check=False,
            )
            time.sleep(20)  # wait for cluster init

        results[svc] = run_kill_test(svc)

    # Summary
    print(f"\n{'='*60}")
    print("KILL TEST SUMMARY")
    print(f"{'='*60}")
    all_passed = True
    for svc, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {svc}: {status}")
        if not passed:
            all_passed = False

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
