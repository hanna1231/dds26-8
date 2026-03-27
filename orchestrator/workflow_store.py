"""
WorkflowStore: generic Redis-persisted workflow state with Lua CAS transitions.

State-agnostic per D-04: performs blind CAS without validating state names.
Strategies own state enums and validate before calling transition().

Key design decisions:
- D-01: {workflow:<workflow_id>} key prefix (not saga: or tpc:)
- D-02: step_N_done flat hash fields via mark_step_done()
- D-04: State-agnostic store -- no VALID_TRANSITIONS dict, no state enum
- TRANSITION_LUA extracted verbatim from saga.py:42-49 (identical in tpc.py:43-50)
- Class form for injectable dependency (aligns with REF-03 in Phase 16)
- 7-day TTL via db.expire()
- No decode_responses=True -- manual byte decode in get()
"""
import json
import time


# ---------------------------------------------------------------------------
# Lua CAS script for atomic state transition
#
# Source: orchestrator/saga.py lines 42-49, orchestrator/tpc.py lines 43-50
# (byte-for-byte identical in both files -- extracted verbatim)
#
# KEYS[1]  -- workflow hash key (e.g. "{workflow:<workflow_id>}")
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
# Source: orchestrator/saga.py lines 42-49, orchestrator/tpc.py lines 43-50 (identical)


class WorkflowStore:
    """Generic Redis-persisted workflow state with Lua CAS transitions.

    State-agnostic per D-04: performs blind CAS without validating state names.
    Strategies own state enums and validate before calling transition().
    """

    def __init__(self, db):
        self._db = db

    @staticmethod
    def _key(workflow_id: str) -> str:
        """Produce Redis hash-tagged key for cluster slot locality (D-01)."""
        return f"{{workflow:{workflow_id}}}"

    async def create(
        self,
        workflow_id: str,
        initial_state: str,
        metadata: dict | None = None,
    ) -> bool:
        """Atomically create a new workflow record.

        Uses HSETNX on 'state' to prevent duplicate creation (exactly-once).
        Returns True if created, False if record already existed.
        Stores optional metadata dict fields alongside system fields.
        Sets a 7-day TTL on the key.
        """
        key = self._key(workflow_id)
        now = str(int(time.time()))

        created = await self._db.hsetnx(key, "state", initial_state)
        if not created:
            return False

        fields: dict = {
            "workflow_id": workflow_id,
            "started_at": now,
            "updated_at": now,
        }
        if metadata:
            for k, v in metadata.items():
                fields[k] = v if isinstance(v, str) else json.dumps(v)

        await self._db.hset(key, mapping=fields)
        await self._db.expire(key, 7 * 24 * 3600)
        return True

    async def transition(
        self,
        workflow_id: str,
        from_state: str,
        to_state: str,
        flag_field: str = "",
        flag_value: str = "",
    ) -> bool:
        """Atomically transition workflow state using Lua CAS.

        State-agnostic: no transition validation. Caller (strategy) must validate
        before calling this method. Returns True if transition applied, False
        if current state did not match from_state.
        """
        key = self._key(workflow_id)
        result = await self._db.eval(
            TRANSITION_LUA, 1, key, from_state, to_state, flag_field, flag_value
        )
        return bool(result)

    async def mark_step_done(self, workflow_id: str, step_index: int) -> None:
        """Write step_N_done = '1' into the workflow hash (D-02, ENG-05).

        Replaces hardcoded field names (stock_reserved, payment_charged).
        """
        key = self._key(workflow_id)
        await self._db.hset(key, f"step_{step_index}_done", "1")

    async def get(self, workflow_id: str) -> dict | None:
        """Retrieve workflow record and decode bytes.

        Returns None if no record exists.
        Manual byte decode -- no decode_responses=True (would break eval() returns).
        """
        raw = await self._db.hgetall(self._key(workflow_id))
        if not raw:
            return None
        return {k.decode(): v.decode() for k, v in raw.items()}
