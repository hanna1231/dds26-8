"""
Workflow data model.

Defines WorkflowStep and WorkflowDefinition dataclasses used by strategies
(Phase 15) and the workflow engine (Phase 16).

Per D-03: minimal dataclass -- no timeout config, retry policy, or metadata fields.
Per D-04: no state enums here -- strategies own those.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any, Literal


@dataclass
class WorkflowStep:
    """A named set of async callables: forward action, compensation, and
    optional commit (used by 2PC phase 2a to finalize a prepared step)."""

    name: str
    action: Callable[..., Awaitable[Any]]
    compensation: Callable[..., Awaitable[Any]]
    commit: Callable[..., Awaitable[Any]] | None = None


@dataclass
class WorkflowDefinition:
    """An ordered sequence of workflow steps with a strategy selector."""

    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    strategy: Literal["saga", "2pc"] = "saga"
