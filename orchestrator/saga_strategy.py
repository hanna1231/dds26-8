"""
SagaStrategy: sequential forward execution with reverse compensation.

Implements the SAGA execution strategy using WorkflowDefinition and WorkflowStore.
Per D-01 (forward execution with bounded retry), D-02 (reverse compensation with
infinite retry), D-05 (SAGA_STATES and VALID_TRANSITIONS), D-06 (transition
validation raises ValueError), D-07 (stateless strategy class).

State sequence for forward execution:
  STARTED -> STOCK_RESERVED -> PAYMENT_CHARGED -> COMPLETED

On step failure:
  current_state -> COMPENSATING -> FAILED
"""
from workflow_types import WorkflowDefinition, WorkflowStep  # noqa: F401
from workflow_store import WorkflowStore
from retry import retry_forward, retry_forever


# ---------------------------------------------------------------------------
# SAGA state definitions (copied verbatim from saga.py:14-28 per D-05)
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

# State sequence for forward execution: step i transitions STATE_SEQUENCE[i] -> STATE_SEQUENCE[i+1]
STATE_SEQUENCE = ["STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPLETED"]


class SagaStrategy:
    """Stateless SAGA execution strategy.

    Executes WorkflowDefinition steps sequentially with bounded retry on forward
    path, and reverse-order infinite-retry compensation on failure.
    """

    def _validate_transition(self, from_state: str, to_state: str) -> None:
        """Validate that the state transition is allowed.

        Raises:
            ValueError: If the transition is not in VALID_TRANSITIONS.
        """
        if not (from_state in VALID_TRANSITIONS and to_state in VALID_TRANSITIONS[from_state]):
            raise ValueError(f"Invalid SAGA transition: {from_state} -> {to_state}")

    async def execute(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
    ) -> dict:
        """Execute workflow steps sequentially with bounded retry.

        Starts from STATE_SEQUENCE[0] (STARTED) and transitions through states
        after each successful step. On step failure, transitions to COMPENSATING
        and calls compensate().

        Args:
            workflow_id: Unique workflow identifier.
            definition:  WorkflowDefinition with ordered steps.
            context:     Context dict passed to each step action/compensation.
            store:       WorkflowStore for state persistence.

        Returns:
            {"success": True, "error_message": ""} on full completion.
            {"success": False, "error_message": str} on step failure after compensation.
        """
        completed_step_indices: list[int] = []

        for i, step in enumerate(definition.steps):
            result = await retry_forward(lambda s=step, c=context: s.action(c))

            if not result.get("success"):
                # Transition to COMPENSATING
                current_state = STATE_SEQUENCE[i]
                self._validate_transition(current_state, "COMPENSATING")
                await store.transition(workflow_id, current_state, "COMPENSATING")

                # Run compensation for all completed steps in reverse
                await self.compensate(
                    workflow_id,
                    definition,
                    context,
                    store,
                    completed_indices=completed_step_indices,
                )
                return {"success": False, "error_message": result.get("error_message", "")}

            # Step succeeded: record completion and transition state
            await store.mark_step_done(workflow_id, i)
            completed_step_indices.append(i)

            # Transition state: STATE_SEQUENCE[i] -> STATE_SEQUENCE[i+1]
            from_state = STATE_SEQUENCE[i]
            to_state = STATE_SEQUENCE[i + 1]
            self._validate_transition(from_state, to_state)
            await store.transition(workflow_id, from_state, to_state)

        return {"success": True, "error_message": ""}

    async def compensate(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
        *,
        completed_indices: list[int] | None = None,
    ) -> dict:
        """Run compensations in reverse order of completed steps with infinite retry.

        If completed_indices is None (recovery path), reads step_N_done flags
        from store to determine which steps completed.

        Args:
            workflow_id:       Unique workflow identifier.
            definition:        WorkflowDefinition with ordered steps.
            context:           Context dict passed to each step compensation.
            store:             WorkflowStore for state persistence and recovery.
            completed_indices: Indices of completed steps, or None to read from store.

        Returns:
            {"success": False, "error_message": "compensated"} always.
        """
        if completed_indices is None:
            # Recovery path: read step_N_done flags from store (Pitfall 2)
            current = await store.get(workflow_id)
            completed_indices = [
                i
                for i in range(len(definition.steps))
                if current is not None and current.get(f"step_{i}_done") == "1"
            ]

        # Compensate in reverse order of completion
        for i in reversed(completed_indices):
            step = definition.steps[i]
            await retry_forever(lambda s=step, c=context: s.compensation(c))

        # Transition COMPENSATING -> FAILED
        self._validate_transition("COMPENSATING", "FAILED")
        await store.transition(workflow_id, "COMPENSATING", "FAILED")

        return {"success": False, "error_message": "compensated"}

    async def resume(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
        state: str,
    ) -> dict:
        """Resume a partially-completed SAGA from its current state.

        Forward states (STARTED, STOCK_RESERVED, PAYMENT_CHARGED): re-run from
        current position in STATE_SEQUENCE using retry_forward. On failure,
        transition to COMPENSATING and compensate.

        COMPENSATING: run compensate() with completed_indices=None (reads
        step_N_done flags from store).

        Mirrors recovery.py:resume_saga() but expressed through strategy class.
        """
        if state == "COMPENSATING":
            return await self.compensate(
                workflow_id, definition, context, store, completed_indices=None
            )

        # Forward recovery: find current position in STATE_SEQUENCE
        if state not in STATE_SEQUENCE:
            return {"success": False, "error_message": f"unrecoverable state: {state}"}

        start_index = STATE_SEQUENCE.index(state)

        # Execute steps from current position
        completed_step_indices: list[int] = []

        # Read already-completed steps from store
        current = await store.get(workflow_id)
        for i in range(len(definition.steps)):
            if current and current.get(f"step_{i}_done") == "1":
                completed_step_indices.append(i)

        for i in range(start_index, len(definition.steps)):
            if i in completed_step_indices:
                continue  # Already completed, skip

            step = definition.steps[i]
            result = await retry_forward(lambda s=step, c=context: s.action(c))

            if not result.get("success"):
                current_state = STATE_SEQUENCE[i]
                self._validate_transition(current_state, "COMPENSATING")
                await store.transition(workflow_id, current_state, "COMPENSATING")
                return await self.compensate(
                    workflow_id, definition, context, store,
                    completed_indices=completed_step_indices + [j for j in range(start_index, i) if j not in completed_step_indices],
                )

            await store.mark_step_done(workflow_id, i)
            completed_step_indices.append(i)

            from_state = STATE_SEQUENCE[i]
            to_state = STATE_SEQUENCE[i + 1]
            self._validate_transition(from_state, to_state)
            await store.transition(workflow_id, from_state, to_state)

        return {"success": True, "error_message": ""}
