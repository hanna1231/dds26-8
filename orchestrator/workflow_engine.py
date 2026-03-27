"""
WorkflowEngine: single entry point for all transaction coordination.

Routes to the correct strategy based on WorkflowDefinition.strategy field.
Publishes lifecycle events (workflow_started, workflow_succeeded, workflow_failed).
Calls store.create() with strategy-appropriate initial state before delegating.

Per ENG-03: execute(workflow_id, definition, context) is the only public method.
Per REF-03: WorkflowEngine receives WorkflowStore via constructor (injectable).
"""
from workflow_store import WorkflowStore
from workflow_types import WorkflowDefinition
from saga_strategy import SagaStrategy
from tpc_strategy import TwoPhaseStrategy
from events import publish_event

_STRATEGIES = {
    "saga": SagaStrategy(),
    "2pc": TwoPhaseStrategy(),
}

_INITIAL_STATES = {
    "saga": "STARTED",
    "2pc": "INIT",
}


class WorkflowEngine:
    """Single entry point for all transaction coordination.

    Receives WorkflowStore and Redis db via constructor (injectable dependency).
    execute() routes to the correct strategy and wraps with lifecycle events.
    """

    def __init__(self, store: WorkflowStore, db):
        self._store = store
        self._db = db

    async def execute(self, workflow_id: str, definition: WorkflowDefinition, context: dict) -> dict:
        """Execute a workflow using the strategy specified in the definition.

        1. Validates strategy name exists in registry
        2. Calls store.create() with strategy-appropriate initial state
        3. Publishes workflow_started event
        4. Delegates to strategy.execute()
        5. Publishes workflow_succeeded or workflow_failed event
        6. Returns strategy result dict

        Args:
            workflow_id: Unique workflow identifier.
            definition:  WorkflowDefinition with ordered steps and strategy field.
            context:     Context dict passed to each step (order_id, user_id, items, etc).

        Returns:
            {"success": bool, "error_message": str}

        Raises:
            ValueError: If definition.strategy is not in the registry.
        """
        strategy = _STRATEGIES.get(definition.strategy)
        if strategy is None:
            raise ValueError(f"Unknown strategy: {definition.strategy!r}")

        initial_state = _INITIAL_STATES[definition.strategy]
        # Persist strategy field for recovery (Phase 17 Pitfall 5)
        created = await self._store.create(
            workflow_id, initial_state,
            metadata={**context, "strategy": definition.strategy}
        )
        if not created:
            # Duplicate: read stored result and return
            existing = await self._store.get(workflow_id)
            if existing is None:
                return {"success": False, "error_message": "internal error"}
            state = existing.get("state", "")
            if state in ("COMPLETED", "COMMITTED"):
                return {"success": True, "error_message": ""}
            if state in ("FAILED", "ABORTED"):
                return {"success": False, "error_message": existing.get("error_message", "")}
            return {"success": False, "error_message": "checkout already in progress"}

        await publish_event(self._db, "workflow_started", workflow_id,
                            context.get("order_id", ""), context.get("user_id", ""))

        result = await strategy.execute(workflow_id, definition, context, self._store)

        event_type = "workflow_succeeded" if result.get("success") else "workflow_failed"
        await publish_event(self._db, event_type, workflow_id,
                            context.get("order_id", ""), context.get("user_id", ""))

        return result
