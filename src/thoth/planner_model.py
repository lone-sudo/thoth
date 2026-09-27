"""Model-backed Planner (the first real brain) over the provider ladder.

Implements the runner's Planner Protocol. Every decide():
  1. routes the task_class down the degradation ladder (providers.route),
  2. emits `provider.route` (the V3 learned-router dataset starts HERE),
  3. attempts candidates in ladder order, emitting `provider.attempt` per try,
  4. raises PlannerUnavailable when the ladder is empty or every attempt fails —
     the runner catches it and parks the run ("no provider"), the ladder's
     terminal state (Team-A §2; ADR-004 §2).

Scripted mode: pass script=[(tool, args), ...] to return canned Plans; when the
script is exhausted, the planner returns a done-Plan (script-exhausted) — it
NEVER falls through to the provider ladder (same semantics as NoopPlanner).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import providers
from .events import emit
from .runner import PlannerUnavailable, Plan

DEFAULT_TASK_CLASS = "plan"


class ModelPlanner:
    """Ladder-driven planner. Conforms to runner.Planner (decide(context, history))."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        registry: providers.ProviderRegistry | None = None,
        availability: providers.AvailabilityCache | None = None,
        task_class: str = DEFAULT_TASK_CLASS,
        min_context: int = 0,
        script: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        self._conn = conn
        self._registry = registry or providers.default_registry()
        self._availability = availability
        self._task_class = task_class
        self._min_context = min_context
        self._scripted = script is not None
        self._script = list(script or [])

    # -- Planner protocol ------------------------------------------------------

    def decide(self, context: str, history: list[dict[str, Any]]) -> Plan:
        # scripted mode (test/resume path): no providers consulted
        if self._scripted:
            if self._script:
                name, args = self._script.pop(0)
                return Plan(tool=name, args=args,
                            summary=f"scripted {name}",
                            next_intent="continue script")
            return Plan(tool=None, done=True,
                        summary="script exhausted",
                        next_intent="")

        candidates = providers.route(
            self._registry, self._task_class,
            min_context=self._min_context,
            availability=self._availability,
        )
        emit(self._conn, "provider.route", {
            "task_class": self._task_class,
            "min_context": self._min_context,
            "candidates": [s.name for s in candidates],
            "history_turns": len(history),
        })
        self._conn.commit()

        if not candidates:
            raise PlannerUnavailable(
                f"ladder empty for task_class '{self._task_class}'"
                + (f" (min_context={self._min_context})" if self._min_context else ""))

        failures: list[str] = []
        for spec in candidates:
            emit(self._conn, "provider.attempt", {
                "task_class": self._task_class,
                "provider": spec.name,
                "auth_type": spec.auth_type,
            })
            self._conn.commit()
            try:
                # $0 skeleton: raises for every provider; a guard-gated local
                # client replaces providers.attempt's body later — this loop,
                # the ladder, and the parking semantics stay identical.
                response = providers.attempt(spec)
            except providers.ProviderUnavailable as exc:
                failures.append(f"{spec.name}: {exc}")
                emit(self._conn, "provider.outcome", {
                    "task_class": self._task_class,
                    "provider": spec.name,
                    "outcome": "unavailable",
                    "reason": str(exc),
                })
                self._conn.commit()
                continue
            # (future) parse response into a Plan — the only new code a working
            # client requires beyond attempt()'s body.
            return self._plan_from_response(spec, response)

        raise PlannerUnavailable(
            f"all {len(candidates)} candidate(s) unavailable for "
            f"'{self._task_class}': " + "; ".join(failures))

    # -- response → Plan (future client path) -----------------------------------

    def _plan_from_response(self, spec: providers.ProviderSpec, response: str) -> Plan:
        """V1 skeleton: no response can exist yet. Kept as the explicit seam so
        the future JSON-plan parse lands in exactly one place."""
        raise PlannerUnavailable(
            f"provider '{spec.name}' returned a response but plan parsing is "
            "not implemented in this build")
