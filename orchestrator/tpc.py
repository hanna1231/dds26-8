"""
2PC (Two-Phase Commit) state machine module.

Provides Redis-persisted TPC records with atomic state transitions via Lua CAS.
Mirrors the proven saga.py pattern: hsetnx for creation, Lua CAS for transitions.
All functions are async. Manual byte decoding is used (no decode_responses=True).
"""
import json
import time

# ---------------------------------------------------------------------------
# TPC state definitions
# ---------------------------------------------------------------------------

TPC_STATES = {
    "INIT",
    "PREPARING",
    "COMMITTING",
    "ABORTING",
    "COMMITTED",
    "ABORTED",
}

TPC_VALID_TRANSITIONS: dict[str, set[str]] = {
    "INIT": {"PREPARING"},
    "PREPARING": {"COMMITTING", "ABORTING"},
    "COMMITTING": {"COMMITTED"},
    "ABORTING": {"ABORTED"},
}

# ---------------------------------------------------------------------------
# Lua CAS script for atomic state transition
#
# KEYS[1]  -- TPC hash key (e.g. "{tpc:<order_id>}")
# ARGV[1]  -- expected current state
# ARGV[2]  -- new (target) state
# ARGV[3]  -- optional extra field name to set (pass "" to skip)
# ARGV[4]  -- optional extra field value to set
#
# Returns 1 if transition was applied, 0 if current state did not match.
# ---------------------------------------------------------------------------

TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""


# ---------------------------------------------------------------------------
# TPC record CRUD
# ---------------------------------------------------------------------------

async def create_tpc_record(
    db,
    order_id: str,
    user_id: str,
    items: list[dict],
    total_cost: int,
) -> bool:
    """
    Atomically create a new TPC record in Redis.

    Uses HSETNX on the 'state' field to prevent duplicate TPC record creation.
    If the record already exists (HSETNX returns 0), returns False.

    Args:
        db:         redis.asyncio client (no decode_responses).
        order_id:   Unique order identifier.
        user_id:    User placing the order.
        items:      List of dicts: [{"item_id": str, "quantity": int}, ...].
        total_cost: Total order cost in cents.

    Returns:
        True if TPC record was created, False if it already existed.
    """
    tpc_key = f"{{tpc:{order_id}}}"
    now = str(int(time.time()))

    # Atomically claim the record; returns 1 if created, 0 if already existed
    created = await db.hsetnx(tpc_key, "state", "INIT")
    if not created:
        return False

    # Set all remaining fields
    await db.hset(tpc_key, mapping={
        "order_id": order_id,
        "user_id": user_id,
        "total_cost": str(total_cost),
        "items_json": json.dumps(items),
        "protocol": "2pc",
        "stock_prepared": "0",
        "payment_prepared": "0",
        "started_at": now,
        "updated_at": now,
    })

    # Expire after 7 days
    await db.expire(tpc_key, 7 * 24 * 3600)
    return True


async def transition_tpc_state(
    db,
    tpc_key: str,
    from_state: str,
    to_state: str,
    flag_field: str = "",
    flag_value: str = "",
) -> bool:
    """
    Atomically transition the TPC state using a Lua CAS script.

    Validates the transition against TPC_VALID_TRANSITIONS before calling Lua
    to fail fast on invalid state machine paths.

    Args:
        db:         redis.asyncio client.
        tpc_key:    Full Redis key (e.g. "{tpc:<order_id>}").
        from_state: Expected current state (will be verified by Lua).
        to_state:   Target state.
        flag_field: Optional field name to set atomically (e.g. "stock_prepared").
        flag_value: Optional value for flag_field.

    Returns:
        True if transition applied, False if current state did not match.

    Raises:
        ValueError: If from_state -> to_state is not a valid transition.
    """
    allowed = TPC_VALID_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise ValueError(
            f"Invalid TPC transition: {from_state} -> {to_state}. "
            f"Allowed from {from_state}: {allowed}"
        )

    result = await db.eval(
        TRANSITION_LUA,
        1,
        tpc_key,
        from_state,
        to_state,
        flag_field,
        flag_value,
    )
    return bool(result)


async def get_tpc(db, order_id: str) -> dict | None:
    """
    Retrieve TPC record from Redis and decode bytes.

    Args:
        db:       redis.asyncio client (no decode_responses).
        order_id: Order identifier.

    Returns:
        Dict with string keys and values, or None if no record exists.
    """
    raw = await db.hgetall(f"{{tpc:{order_id}}}")
    if not raw:
        return None
    return {k.decode(): v.decode() for k, v in raw.items()}
