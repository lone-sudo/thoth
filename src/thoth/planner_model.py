"""Model-backed Planner over the provider ladder — now with a working local path.

decide() flow per turn:
  1. route the task_class down the ladder (providers.route; local-first,
     fail-closed availability),
  2. emit `provider.route` (V3 learned-router dataset),
  3. for each candidate: emit `provider.attempt`, then
     - **local** providers go through `ollama.attempt` (guard-gated; the Guard's
       `local:` allow-branch is what makes this legal) and the answer is parsed
       by `ollama.plan_from_json`, then the proposed tool+args are validated
       against the tool registry *before* becoming a Plan — the model proposes,
       the registry disposes; malformed answers count as an unavailable attempt
       and the ladder continues;
     - cloud providers raise ProviderUnavailable from providers.attempt (no
       clients, $0-structural) and are recorded honestly as unavailable;
  4. an exhausted ladder raises PlannerUnavailable -> the runner parks ("no
     provider") — degradation, never a paid fallback.

Scripted mode unchanged: script=[(tool, args), ...] returns canned Plans and an
exhausted script finishes the run without touching the ladder.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import ollama, providers
from .events import emit
from .runner import PlannerUnavailable, Plan
from .tools import ToolRegistry

DEFAULT_TASK_CLASS = "plan"

# provider name -> client callable(conn, prompt) -> answer text.
# Only local entries may appear here; the registry + guard keep cloud out.
_CLIENTS: dict[str, Any] = {
    "ollama-local": ollama.attempt,
}

# Sampling-variance budget (journal 2026-W39): a stochastic local model can emit
# one malformed/invalid answer on an otherwise-capable day. Retry the SAME
# candidate before falling through — structural failures (no client) never retry.
_ATTEMPTS_PER_PROVIDER = 2


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
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._conn = conn
        self._registry = registry or providers.default_registry()
        self._availability = availability
        self._task_class = task_class
        self._min_context = min_context
        self._scripted = script is not None
        self._script = list(script or [])
        self._tools = tool_registry

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
            attempts_left = _ATTEMPTS_PER_PROVIDER
            while attempts_left > 0:
                attempts_left -= 1
                try:
                    answer = self._attempt(spec, context, history)
                    plan_dict = ollama.plan_from_json(answer)
                    plan = self._to_plan(plan_dict, spec)
                except providers.ProviderUnavailable as exc:
                    failures.append(f"{spec.name}: {exc}")
                    self._outcome(spec, "unavailable", str(exc))
                    break  # structural — retrying cannot help
                except ollama.OllamaUnavailable as exc:
                    failures.append(f"{spec.name}: {exc}")
                    self._outcome(spec, "error", str(exc))
                    if attempts_left:
                        emit(self._conn, "provider.retry", {
                            "task_class": self._task_class,
                            "provider": spec.name,
                            "attempts_left": attempts_left,
                            "reason": str(exc)[:200],
                        })
                        self._conn.commit()
                    continue
                self._outcome(spec, "ok", plan_dict.get("summary", ""))
                return plan

        raise PlannerUnavailable(
            f"all {len(candidates)} candidate(s) unavailable for "
            f"'{self._task_class}': " + "; ".join(failures))

    # -- pieces ------------------------------------------------------------------

    def _attempt(self, spec: providers.ProviderSpec, context: str,
                 history: list[dict[str, Any]] | None = None) -> str:
        """One model call for one ladder candidate. Cloud providers raise
        ProviderUnavailable (no clients, $0-structural); the local provider goes
        through the guard-gated ollama client."""
        if spec.is_local:
            client = _CLIENTS.get(spec.name)
            if client is None:
                raise providers.ProviderUnavailable(
                    f"local provider '{spec.name}' has no client registered")
            prompt = self._build_prompt(context, history)
            return client(self._conn, prompt)
        return providers.attempt(spec)  # cloud: raises, honestly

    def _build_prompt(self, context: str,
                      history: list[dict[str, Any]] | None = None) -> str:
        tool_lines = []
        if self._tools is not None:
            for spec in self._tools.all():
                req = ", ".join(sorted(spec.required)) if spec.required else ""
                suffix = f" (required args: {req})" if req else ""
                tool_lines.append(f"- {spec.name}: {spec.description}{suffix}")
        tools_block = "\n".join(tool_lines) if tool_lines else "- (tool list unavailable)"
        out = ["CONTEXT:\n", context, "\n\nTOOL LIST:\n", tools_block]
        if history:
            lines = []
            for i, turn in enumerate(history, 1):
                tool = turn.get("tool") or "(finish)"
                verify = turn.get("verify") or {}
                ok = verify.get("ok") if isinstance(verify, dict) else None
                note = "ok" if ok else "failed"
                # outcome first: the model finishes when it can read what happened
                # (live finding, journal 2026-W39); older checkpoints fall back
                note_txt = str(turn.get("output") or turn.get("summary")
                               or turn.get("next_intent") or "")
                lines.append(f"  turn {i}: {tool} [{note}] "
                             f"{note_txt[:100]}")
            out.append("\n\nPREVIOUS TURNS (already executed and verified):\n"
                       + "\n".join(lines))
        out.append("\n\nDecide the next single action. Results of previous "
                   "turns appear under PREVIOUS TURNS - never repeat an action "
                   "to obtain a result you already have. If the goal is already "
                   "achieved, set done=true. Reply with the JSON plan only.")
        return "".join(out)

    def _to_plan(self, plan_dict: dict[str, Any],
                 spec: providers.ProviderSpec) -> Plan:
        """JSON dict -> runner.Plan. The registry validates tool + args: a model
        proposal is a *suggestion* until the registry accepts it (ADR-003 §3)."""
        tool = plan_dict.get("tool")
        if plan_dict.get("done") or tool is None:
            return Plan(tool=None, done=True,
                        summary=plan_dict.get("summary", "planner finished"),
                        next_intent=plan_dict.get("next_intent", ""))
        if self._tools is None:
            raise ollama.OllamaUnavailable("no tool registry wired into the planner")
        try:
            clean_args = self._tools.validate(tool, plan_dict.get("args", {}))
        except (KeyError, ValueError) as exc:
            raise ollama.OllamaUnavailable(
                f"model proposed an invalid action ({exc})") from exc
        return Plan(tool=tool, args=clean_args,
                    summary=plan_dict.get("summary", ""),
                    next_intent=plan_dict.get("next_intent", ""))

    def _outcome(self, spec: providers.ProviderSpec, outcome: str, reason: str) -> None:
        emit(self._conn, "provider.outcome", {
            "task_class": self._task_class,
            "provider": spec.name,
            "outcome": outcome,
            "reason": reason[:300],
        })
        self._conn.commit()
