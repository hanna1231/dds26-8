"""
SAGA state machine module.

Provides Redis-persisted SAGA records with atomic state transitions via Lua CAS.
All functions are async. Manual byte decoding is used (no decode_responses=True).
"""
import json
import time

# ---------------------------------------------------------------------------
# SAGA state definitions
# ---------------------------------------------------------------------------

SAGA_STATES = {
    "STARTED",
    "STOCK_RESERVED",
    "PAYMENT_CHARGED",
    "COMPLETED",
    "COMPENSATING",
    "FAILED",
}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "STARTED": {"STOCK_RESERVED", "COMPENSATING"},
    "STOCK_RESERVED": {"PAYMENT_CHARGED", "COMPENSATING"},
    "PAYMENT_CHARGED": {"COMPLETED", "COMPENSATING"},
    "COMPENSATING": {"FAILED"},
}

# ---------------------------------------------------------------------------
# Lua CAS script for atomic state transition
#
# KEYS[1]  — saga hash key (e.g. "{saga:<order_id>}")
# ARGV[1]  — expected current state
# ARGV[2]  — new (target) state
# ARGV[3]  — optional extra field name to set (pass "" to skip)
# ARGV[4]  — optional extra field value to set
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
# SAGA record CRUD
# ---------------------------------------------------------------------------

async def create_saga_record(
    db,
    order_id: str,
    user_id: str,
    items: list[dict],
    total_cost: int,
) -> bool:
    """
    Atomically create a new SAGA record in Redis.

    Uses HSETNX on the 'state' field to prevent duplicate SAGA record creation.
    If the record already exists (HSETNX returns 0), returns False.

    Args:
        db:         redis.asyncio client (no decode_responses).
        order_id:   Unique order identifier.
        user_id:    User placing the order.
        items:      List of dicts: [{"item_id": str, "quantity": int}, ...].
        total_cost: Total order cost in cents.

    Returns:
        True if SAGA was created, False if it already existed.
    """
    saga_key = f"{{saga:{order_id}}}"
    now = str(int(time.time()))

    # Atomically claim the record; returns 1 if created, 0 if already existed
    created = await db.hsetnx(saga_key, "state", "STARTED")
    if not created:
        return False

    # Set all remaining fields
    await db.hset(saga_key, mapping={
        "order_id": order_id,
        "user_id": user_id,
        "total_cost": str(total_cost),
        "items_json": json.dumps(items),
        "stock_reserved": "0",
        "payment_charged": "0",
        "refund_done": "0",
        "stock_restored": "0",
        "error_message": "",
        "started_at": now,
        "updated_at": now,
    })

    # Expire after 7 days
    await db.expire(saga_key, 7 * 24 * 3600)
    return True


async def transition_state(
    db,
    saga_key: str,
    from_state: str,
    to_state: str,
    flag_field: str = "",
    flag_value: str = "",
) -> bool:
    """
    Atomically transition the SAGA state using a Lua CAS script.

    Validates the transition against VALID_TRANSITIONS before calling Lua
    to fail fast on invalid state machine paths.

    Args:
        db:         redis.asyncio client.
        saga_key:   Full Redis key (e.g. "{saga:<order_id>}").
        from_state: Expected current state (will be verified by Lua).
        to_state:   Target state.
        flag_field: Optional field name to set atomically (e.g. "stock_reserved").
        flag_value: Optional value for flag_field.

    Returns:
        True if transition applied, False if current state did not match.

    Raises:
        ValueError: If from_state -> to_state is not a valid transition.
    """
    allowed = VALID_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise ValueError(
            f"Invalid SAGA transition: {from_state} -> {to_state}. "
            f"Allowed from {from_state}: {allowed}"
        )

    result = await db.eval(
        TRANSITION_LUA,
        1,
        saga_key,
        from_state,
        to_state,
        flag_field,
        flag_value,
    )
    return bool(result)


async def get_saga(db, order_id: str) -> dict | None:
    """
    Retrieve SAGA record from Redis and decode bytes.

    Args:
        db:       redis.asyncio client (no decode_responses).
        order_id: Order identifier.

    Returns:
        Dict with string keys and values, or None if no record exists.
    """
    raw = await db.hgetall(f"{{saga:{order_id}}}")
    if not raw:
        return None
    return {k.decode(): v.decode() for k, v in raw.items()}


async def set_saga_error(db, order_id: str, error_message: str) -> None:
    """
    Set the error_message field on an existing SAGA record.

    Args:
        db:            redis.asyncio client.
        order_id:      Order identifier.
        error_message: Error description to store.
    """
    await db.hset(f"{{saga:{order_id}}}", "error_message", error_message)
