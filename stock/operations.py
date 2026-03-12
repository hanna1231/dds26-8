import json
from msgspec import msgpack, Struct


class StockValue(Struct):
    stock: int
    price: int


IDEMPOTENCY_ACQUIRE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
redis.call('SET', KEYS[1], '__PROCESSING__', 'EX', ARGV[1])
return '__NEW__'
"""

# Atomic stock reservation Lua script.
# Combines idempotency check + stock read-decrement-write in a single atomic eval.
#
# KEYS[1] = idempotency key  (e.g. {item:UUID}:idempotency:SAGA_KEY)
# KEYS[2] = item key         (e.g. {item:UUID})
# ARGV[1] = quantity to decrement (integer as string)
# ARGV[2] = encoded StockValue bytes WITH new stock (pre-computed by Python)
# ARGV[3] = expected current stock value (integer as string, for CAS check)
# ARGV[4] = idempotency processing TTL (seconds)
# ARGV[5] = idempotency result TTL (seconds)
#
# Returns:
#   "__PROCESSING__"  - concurrent request already in flight
#   JSON string       - cached idempotency result (replay)
#   "OK"              - stock successfully decremented (ARGV[2] written to item key)
#   "FAIL:insufficient stock" - current stock != expected (race condition, Python retries)
#   "FAIL:item not found" - item key does not exist
RESERVE_STOCK_ATOMIC_LUA = """
local ikey = KEYS[1]
local item_key = KEYS[2]
local expected_stock = tonumber(ARGV[3])
local new_bytes = ARGV[2]
local proc_ttl = tonumber(ARGV[4])
local result_ttl = tonumber(ARGV[5])

-- Idempotency check
local existing = redis.call('GET', ikey)
if existing then
    return existing
end
redis.call('SET', ikey, '__PROCESSING__', 'EX', proc_ttl)

-- Check item exists
local raw = redis.call('GET', item_key)
if not raw then
    local fail = '{"success":false,"error_message":"item not found"}'
    redis.call('SET', ikey, fail, 'EX', result_ttl)
    return fail
end

-- CAS: only write if current bytes haven't changed since Python read them
-- We check by comparing the raw bytes with what Python expects
-- Python passes the CURRENT raw bytes as ARGV[6] for comparison
local current_raw = ARGV[6]
if raw ~= current_raw then
    -- Stock changed between Python read and this eval; signal retry
    redis.call('DEL', ikey)
    return 'RETRY'
end

-- Stock hasn't changed; write new value
redis.call('SET', item_key, new_bytes)
local success = '{"success":true,"error_message":""}'
redis.call('SET', ikey, success, 'EX', result_ttl)
return 'OK'
"""


async def reserve_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    item_key = f"{{item:{item_id}}}"
    ikey = f"{{item:{item_id}}}:idempotency:{idempotency_key}"

    # Use CAS loop with atomic Lua script to prevent over-reservation races.
    # The Lua script atomically: checks idempotency, reads current stock, and
    # only writes if current stock bytes match what Python read (CAS pattern).
    while True:
        # Read current item state in Python
        entry: bytes = await db.get(item_key)
        if entry is None:
            # Set idempotency to not-found failure (idempotency check is in EVAL)
            fail = json.dumps({"success": False, "error_message": "item not found"})
            await db.set(ikey, fail, ex=86400)
            return {"success": False, "error_message": "item not found"}

        item_entry: StockValue = msgpack.decode(entry, type=StockValue)
        new_stock = item_entry.stock - quantity
        if new_stock < 0:
            # Not enough stock -- do NOT cache this in idempotency layer because
            # stock could be replenished and the same saga retried after compensation.
            # Clean up any __PROCESSING__ sentinel we may have set.
            await db.delete(ikey)
            return {"success": False, "error_message": "insufficient stock"}

        # Compute new serialized value
        new_entry = msgpack.encode(StockValue(stock=new_stock, price=item_entry.price))

        # Atomic CAS: only update item key if it still contains the bytes we read.
        # If another coroutine modified item_key between our GET and this EVAL, returns RETRY.
        result = await db.eval(
            RESERVE_STOCK_ATOMIC_LUA, 2, ikey, item_key,
            str(quantity), new_entry, str(item_entry.stock),
            "30", "86400", entry
        )
        if isinstance(result, bytes):
            result = result.decode()

        if result == "OK":
            return {"success": True, "error_message": ""}
        if result == "RETRY":
            # Another process modified stock between our read and eval; retry loop
            continue
        if result == "__PROCESSING__":
            # Our own previous CAS attempt set __PROCESSING__ but returned RETRY;
            # we deleted it in the Lua script. If we see it again, another call is in flight.
            return {"success": False, "error_message": "operation in progress, retry"}
        # Any other result is a cached JSON response (idempotency replay)
        try:
            cached = json.loads(result)
            return {"success": cached["success"], "error_message": cached["error_message"]}
        except (json.JSONDecodeError, KeyError):
            return {"success": False, "error_message": "internal error"}


async def release_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    item_key = f"{{item:{item_id}}}"
    ikey = f"{{item:{item_id}}}:idempotency:{idempotency_key}"
    result = await db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
    if isinstance(result, bytes):
        result = result.decode()

    if result == "__PROCESSING__":
        return {"success": False, "error_message": "operation in progress, retry"}

    if result != "__NEW__":
        cached = json.loads(result)
        return {"success": cached["success"], "error_message": cached["error_message"]}

    # First call -- execute business logic
    entry: bytes = await db.get(item_key)
    if entry is None:
        fail = json.dumps({"success": False, "error_message": "item not found"})
        await db.set(ikey, fail, ex=86400)
        return {"success": False, "error_message": "item not found"}

    item_entry: StockValue = msgpack.decode(entry, type=StockValue)
    item_entry.stock += quantity
    await db.set(item_key, msgpack.encode(item_entry))
    success = json.dumps({"success": True, "error_message": ""})
    await db.set(ikey, success, ex=86400)
    return {"success": True, "error_message": ""}


# ---------------------------------------------------------------------------
# 2PC Participant Lua Scripts (prepare/commit/abort)
# ---------------------------------------------------------------------------

# PREPARE: Atomically deduct stock + write hold key.
# KEYS[1] = item key {item:<item_id>}
# KEYS[2] = hold key {item:<item_id>}:hold:<order_id>
# ARGV[1] = quantity (string)
# ARGV[2] = new item bytes (pre-computed with decremented stock)
# ARGV[3] = expected current raw bytes (CAS comparison)
#
# Returns: "OK", "ALREADY_PREPARED", "RETRY"
PREPARE_STOCK_LUA = """
local item_key = KEYS[1]
local hold_key = KEYS[2]
local quantity = ARGV[1]
local new_bytes = ARGV[2]
local expected_raw = ARGV[3]

-- Idempotency: if hold key already exists, this prepare was already done
if redis.call('EXISTS', hold_key) == 1 then
    return 'ALREADY_PREPARED'
end

-- Check item exists
local raw = redis.call('GET', item_key)
if not raw then
    return 'ITEM_NOT_FOUND'
end

-- CAS: only write if current bytes match expected
if raw ~= expected_raw then
    return 'RETRY'
end

-- Atomic: deduct stock + create hold key with 7-day TTL
redis.call('SET', item_key, new_bytes)
redis.call('SET', hold_key, quantity, 'EX', 604800)
return 'OK'
"""

# COMMIT: Delete hold key (stock already deducted, just cleanup).
# KEYS[1] = hold key {item:<item_id>}:hold:<order_id>
# Returns: "OK" (idempotent -- succeeds even if already deleted)
COMMIT_STOCK_LUA = """
redis.call('DEL', KEYS[1])
return 'OK'
"""

# ABORT: Read hold quantity, restore stock, delete hold key.
# KEYS[1] = item key {item:<item_id>}
# KEYS[2] = hold key {item:<item_id>}:hold:<order_id>
# ARGV[1] = new item bytes (stock restored)
# ARGV[2] = expected current raw bytes (CAS comparison)
#
# Returns: "OK", "ALREADY_ABORTED", "RETRY"
ABORT_STOCK_LUA = """
local item_key = KEYS[1]
local hold_key = KEYS[2]
local new_bytes = ARGV[1]
local expected_raw = ARGV[2]

-- If hold key gone, abort already happened
if redis.call('EXISTS', hold_key) == 0 then
    return 'ALREADY_ABORTED'
end

-- CAS: only write if current bytes match expected
local raw = redis.call('GET', item_key)
if not raw then
    return 'ITEM_NOT_FOUND'
end
if raw ~= expected_raw then
    return 'RETRY'
end

-- Atomic: restore stock + delete hold key
redis.call('SET', item_key, new_bytes)
redis.call('DEL', hold_key)
return 'OK'
"""


async def prepare_stock(db, item_id: str, quantity: int, order_id: str) -> dict:
    """2PC PREPARE: Atomically deduct stock and write hold key."""
    item_key = f"{{item:{item_id}}}"
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"

    while True:
        entry: bytes = await db.get(item_key)
        if entry is None:
            return {"success": False, "error_message": "item not found"}

        item_entry: StockValue = msgpack.decode(entry, type=StockValue)
        if item_entry.stock < quantity:
            return {"success": False, "error_message": "insufficient stock"}

        new_stock = item_entry.stock - quantity
        new_entry = msgpack.encode(StockValue(stock=new_stock, price=item_entry.price))

        result = await db.eval(
            PREPARE_STOCK_LUA, 2, item_key, hold_key,
            str(quantity), new_entry, entry
        )
        if isinstance(result, bytes):
            result = result.decode()

        if result == "OK":
            return {"success": True, "error_message": ""}
        if result == "ALREADY_PREPARED":
            return {"success": True, "error_message": ""}
        if result == "RETRY":
            continue
        return {"success": False, "error_message": result}


async def commit_stock(db, item_id: str, order_id: str) -> dict:
    """2PC COMMIT: Delete hold key (stock already deducted)."""
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"

    result = await db.eval(COMMIT_STOCK_LUA, 1, hold_key)
    if isinstance(result, bytes):
        result = result.decode()

    return {"success": True, "error_message": ""}


async def abort_stock(db, item_id: str, order_id: str) -> dict:
    """2PC ABORT: Restore stock from hold key and delete it."""
    item_key = f"{{item:{item_id}}}"
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"

    while True:
        # Check hold key
        hold_val = await db.get(hold_key)
        if hold_val is None:
            return {"success": True, "error_message": ""}

        hold_quantity = int(hold_val)

        # Read current item bytes
        entry: bytes = await db.get(item_key)
        if entry is None:
            return {"success": False, "error_message": "item not found"}

        item_entry: StockValue = msgpack.decode(entry, type=StockValue)
        restored_stock = item_entry.stock + hold_quantity
        new_entry = msgpack.encode(StockValue(stock=restored_stock, price=item_entry.price))

        result = await db.eval(
            ABORT_STOCK_LUA, 2, item_key, hold_key,
            new_entry, entry
        )
        if isinstance(result, bytes):
            result = result.decode()

        if result == "OK":
            return {"success": True, "error_message": ""}
        if result == "ALREADY_ABORTED":
            return {"success": True, "error_message": ""}
        if result == "RETRY":
            continue
        return {"success": False, "error_message": result}


async def check_stock(db, item_id: str) -> dict:
    entry: bytes = await db.get(f"{{item:{item_id}}}")
    if entry is None:
        return {"success": False, "error_message": "item not found", "stock": 0, "price": 0}
    item_entry: StockValue = msgpack.decode(entry, type=StockValue)
    return {"success": True, "error_message": "", "stock": item_entry.stock, "price": item_entry.price}
