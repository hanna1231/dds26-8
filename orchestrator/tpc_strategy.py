"""
TwoPhaseStrategy: concurrent prepare, WAL decision write, phase-2 commit/abort.

Implements the 2PC execution strategy using WorkflowDefinition and WorkflowStore.
Per D-01 (concurrent prepare via asyncio.gather), D-03 (abort integral to execute,
no separate compensate method), D-05 (TPC_STATES and TPC_VALID_TRANSITIONS),
D-06 (transition validation raises ValueError), D-07 (stateless strategy class).

Protocol:
  Phase 1 (PREPARE): Call all step actions concurrently via asyncio.gather.
  Phase 2a (COMMIT): Write COMMITTING WAL first, then send commits to all steps.
  Phase 2b (ABORT):  Write ABORTING WAL first, then call compensations for all steps.
"""
import asyncio
import logging

from workflow_types import WorkflowDefinition, WorkflowStep  # noqa: F401
from workflow_store import WorkflowStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TPC state definitions (copied verbatim from tpc.py:15-29 per D-05)
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


class TwoPhaseStrategy:
    """Stateless 2PC execution strategy.

    Sends all prepare requests concurrently (asyncio.gather). On unanimous
    success, writes COMMITTING WAL before sending phase-2 commit messages.
    On any failure or exception, writes ABORTING WAL before sending phase-2
    abort (compensation) messages.

    Per D-03: abort is integral to execute() -- no separate compensate method.
    Per D-07: stateless (no constructor params) for testability and thread safety.
    """

    def _validate_transition(self, from_state: str, to_state: str) -> None:
        """Validate that the 2PC state transition is allowed.

        Raises:
            ValueError: If the transition is not in TPC_VALID_TRANSITIONS.
        """
        if not (from_state in TPC_VALID_TRANSITIONS and to_state in TPC_VALID_TRANSITIONS[from_state]):
            raise ValueError(f"Invalid 2PC transition: {from_state} -> {to_state}")

    async def execute(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
    ) -> dict:
        """Execute workflow steps via Two-Phase Commit protocol.

        Phase 1: Transition INIT -> PREPARING, call all step actions concurrently.
        Evaluate votes. If all_yes: write COMMITTING WAL, send commits, finalize.
        If any_no: write ABORTING WAL, send compensations, finalize.

        Args:
            workflow_id: Unique workflow identifier.
            definition:  WorkflowDefinition with ordered steps (same type as SagaStrategy).
            context:     Context dict passed to each step action/compensation.
            store:       WorkflowStore for state persistence (WAL writes).

        Returns:
            {"success": True, "error_message": ""} on COMMITTED.
            {"success": False, "error_message": str} on ABORTED.
        """
        # --- Phase 1: INIT -> PREPARING ---
        self._validate_transition("INIT", "PREPARING")
        await store.transition(workflow_id, "INIT", "PREPARING")

        # Build prepare futures (call at comprehension time -- captures step correctly)
        futures = [step.action(context) for step in definition.steps]
        results = await asyncio.gather(*futures, return_exceptions=True)

        # --- Collect votes (pattern from grpc_server.py:411-424) ---
        all_yes = True
        first_error = ""
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                all_yes = False
                if not first_error:
                    first_error = str(r)
            elif not r.get("success"):
                all_yes = False
                if not first_error:
                    first_error = r.get("error_message", "prepare failed")
            else:
                # Successful prepare vote: record in store
                await store.mark_step_done(workflow_id, i)

        if all_yes:
            # --- WAL: persist COMMITTING BEFORE sending phase-2 commits (Pattern 3) ---
            self._validate_transition("PREPARING", "COMMITTING")
            await store.transition(workflow_id, "PREPARING", "COMMITTING")

            # --- Phase 2a: Send COMMITs concurrently ---
            commit_futures = [step.action(context) for step in definition.steps]
            await asyncio.gather(*commit_futures, return_exceptions=True)

            # --- Finalize ---
            self._validate_transition("COMMITTING", "COMMITTED")
            await store.transition(workflow_id, "COMMITTING", "COMMITTED")
            return {"success": True, "error_message": ""}

        else:
            # --- WAL: persist ABORTING BEFORE sending phase-2 aborts (Pattern 3) ---
            self._validate_transition("PREPARING", "ABORTING")
            await store.transition(workflow_id, "PREPARING", "ABORTING")

            # --- Phase 2b: Send ABORTs (compensations) concurrently ---
            abort_futures = [step.compensation(context) for step in definition.steps]
            await asyncio.gather(*abort_futures, return_exceptions=True)

            # --- Finalize ---
            self._validate_transition("ABORTING", "ABORTED")
            await store.transition(workflow_id, "ABORTING", "ABORTED")
            return {"success": False, "error_message": first_error}

    async def resume(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
        state: str,
    ) -> dict:
        """Resume a partially-completed 2PC from its current state.

        COMMITTING: re-send commits (phase 2a), finalize to COMMITTED.
        INIT/PREPARING: presumed abort -- transition to ABORTING, send aborts.
        ABORTING: re-send aborts (phase 2b), finalize to ABORTED.

        Mirrors recovery.py:resume_tpc() but expressed through strategy class.
        """
        if state == "COMMITTING":
            # Re-send commits (phase 2a)
            commit_futures = [step.action(context) for step in definition.steps]
            await asyncio.gather(*commit_futures, return_exceptions=True)

            self._validate_transition("COMMITTING", "COMMITTED")
            await store.transition(workflow_id, "COMMITTING", "COMMITTED")
            logger.info("2PC %s: re-sent commits -> COMMITTED", workflow_id)
            return {"success": True, "error_message": ""}

        if state in ("INIT", "PREPARING"):
            # Presumed abort
            if state == "INIT":
                self._validate_transition("INIT", "PREPARING")
                await store.transition(workflow_id, "INIT", "PREPARING")
            self._validate_transition("PREPARING", "ABORTING")
            await store.transition(workflow_id, "PREPARING", "ABORTING")
            state = "ABORTING"  # fall through

        if state == "ABORTING":
            # Re-send aborts (phase 2b)
            abort_futures = [step.compensation(context) for step in definition.steps]
            await asyncio.gather(*abort_futures, return_exceptions=True)

            self._validate_transition("ABORTING", "ABORTED")
            await store.transition(workflow_id, "ABORTING", "ABORTED")
            logger.info("2PC %s: re-sent aborts -> ABORTED", workflow_id)
            return {"success": False, "error_message": "presumed abort"}

        return {"success": False, "error_message": f"unrecoverable 2PC state: {state}"}
