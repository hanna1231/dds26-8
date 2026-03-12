import json
from msgspec import msgpack, Struct


class UserValue(Struct):
    credit: int


IDEMPOTENCY_ACQUIRE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
redis.call('SET', KEYS[1], '__PROCESSING__', 'EX', ARGV[1])
return '__NEW__'
"""

# Atomic CAS payment charge Lua script.
# Combines idempotency check + credit read-decrement-write in one atomic eval.
#
# KEYS[1] = idempotency key  (e.g. {user:UUID}:idempotency:SAGA_KEY)
# KEYS[2] = user key         (e.g. {user:UUID})
# ARGV[1] = new user bytes (pre-computed by Python: credit decremented)
# ARGV[2] = current raw user bytes (for CAS comparison)
# ARGV[3] = idempotency processing TTL (seconds)
# ARGV[4] = idempotency result TTL (seconds)
#
# Returns:
#   "__PROCESSING__"   - concurrent request in flight (from a previous call)
#   JSON string        - cached idempotency result (replay)
#   "OK"               - credit successfully decremented
#   "RETRY"            - current bytes differ from expected (CAS miss, Python retries)
#   JSON fail string   - user not found or insufficient credit
CHARGE_PAYMENT_ATOMIC_LUA = """
local ikey = KEYS[1]
local user_key = KEYS[2]
local new_bytes = ARGV[1]
local expected_raw = ARGV[2]
local proc_ttl = tonumber(ARGV[3])
local result_ttl = tonumber(ARGV[4])

-- Idempotency check (atomic: check+set)
local existing = redis.call('GET', ikey)
if existing then
    return existing
end
redis.call('SET', ikey, '__PROCESSING__', 'EX', proc_ttl)

-- Check user exists
local raw = redis.call('GET', user_key)
if not raw then
    local fail = '{"success":false,"error_message":"user not found"}'
    redis.call('SET', ikey, fail, 'EX', result_ttl)
    return fail
end

-- CAS: only write if user bytes haven't changed since Python read them
if raw ~= expected_raw then
    redis.call('DEL', ikey)
    return 'RETRY'
end

-- Bytes match; write new value
redis.call('SET', user_key, new_bytes)
local success = '{"success":true,"error_message":""}'
redis.call('SET', ikey, success, 'EX', result_ttl)
return 'OK'
"""


async def charge_payment(db, user_id: str, amount: int, idempotency_key: str) -> dict:
    user_key = f"{{user:{user_id}}}"
    ikey = f"{{user:{user_id}}}:idempotency:{idempotency_key}"

    # Use CAS loop with atomic Lua script to prevent double-charging race conditions.
    # The Lua script atomically: checks idempotency, then only writes if user bytes
    # match what Python read (compare-and-swap pattern).
    while True:
        entry: bytes = await db.get(user_key)
        if entry is None:
            fail = json.dumps({"success": False, "error_message": "user not found"})
            await db.set(ikey, fail, ex=86400, nx=True)
            return {"success": False, "error_message": "user not found"}

        user_entry: UserValue = msgpack.decode(entry, type=UserValue)
        new_credit = user_entry.credit - amount
        if new_credit < 0:
            # Use SETNX to avoid overwriting a concurrent call's cached result
            fail = json.dumps({"success": False, "error_message": "insufficient credit"})
            await db.set(ikey, fail, ex=86400, nx=True)
            # Read back to get whatever result was stored
            cached_bytes = await db.get(ikey)
            if cached_bytes:
                cached_str = cached_bytes.decode() if isinstance(cached_bytes, bytes) else cached_bytes
                if cached_str not in ("__PROCESSING__",):
                    try:
                        cached = json.loads(cached_str)
                        return {"success": cached["success"],
                                "error_message": cached["error_message"]}
                    except (json.JSONDecodeError, KeyError):
                        pass
            return {"success": False, "error_message": "insufficient credit"}

        new_entry = msgpack.encode(UserValue(credit=new_credit))

        result = await db.eval(
            CHARGE_PAYMENT_ATOMIC_LUA, 2, ikey, user_key,
            new_entry, entry, "30", "86400"
        )
        if isinstance(result, bytes):
            result = result.decode()

        if result == "OK":
            return {"success": True, "error_message": ""}
        if result == "RETRY":
            # Another process modified user between our read and eval; retry
            continue
        if result == "__PROCESSING__":
            return {"success": False, "error_message": "operation in progress, retry"}
        # Cached JSON response (idempotency replay)
        try:
            cached = json.loads(result)
            return {"success": cached["success"], "error_message": cached["error_message"]}
        except (json.JSONDecodeError, KeyError):
            return {"success": False, "error_message": "internal error"}


async def refund_payment(db, user_id: str, amount: int, idempotency_key: str) -> dict:
    user_key = f"{{user:{user_id}}}"
    ikey = f"{{user:{user_id}}}:idempotency:{idempotency_key}"
    result = await db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
    if isinstance(result, bytes):
        result = result.decode()

    if result == "__PROCESSING__":
        return {"success": False, "error_message": "operation in progress, retry"}

    if result != "__NEW__":
        cached = json.loads(result)
        return {"success": cached["success"], "error_message": cached["error_message"]}

    # First call -- execute business logic
    entry: bytes = await db.get(user_key)
    if entry is None:
        fail = json.dumps({"success": False, "error_message": "user not found"})
        await db.set(ikey, fail, ex=86400)
        return {"success": False, "error_message": "user not found"}

    user_entry: UserValue = msgpack.decode(entry, type=UserValue)
    user_entry.credit += amount
    await db.set(user_key, msgpack.encode(user_entry))
    success = json.dumps({"success": True, "error_message": ""})
    await db.set(ikey, success, ex=86400)
    return {"success": True, "error_message": ""}


async def check_payment(db, user_id: str) -> dict:
    entry: bytes = await db.get(f"{{user:{user_id}}}")
    if entry is None:
        return {"success": False, "error_message": "user not found", "credit": 0}
    user_entry: UserValue = msgpack.decode(entry, type=UserValue)
    return {"success": True, "error_message": "", "credit": user_entry.credit}
