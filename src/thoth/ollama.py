"""The local Ollama client — the first real brain, guard-gated (ADR-004 §2).

Design:
- **One I/O module.** Every network byte in the V1 build flows through here
  (`urllib.request`); the CI no-bypass scan (tests/test_guard.py) whitelists this
  file alone. The guard gates the *decision*, this module performs the *action*.
- **Gate before bytes.** `probe()` and `attempt()` each call
  `guard.check_egress("provider", "local:<name>")` first. The current Guard denies
  all egress, so on a stock V1 tree every call below returns a guarded denial —
  that is the correct, testable behavior until the Guard's `local:` allow-branch
  lands (ADR-004 lists it as the explicitly-planned branch).
- **$0 by construction.** `endpoint` defaults to loopback (`127.0.0.1:11434`);
  a non-loopback endpoint raises unless explicitly allowed, so "local" cannot
  silently mean "someone else's server".
- **JSON-plan contract.** The model must answer with
  `{"tool": "<name>|null", "args": {...}, "summary": "...", "next_intent": "...",
  "done": bool}` — parsed by `plan_from_json`, which never trusts the model:
  tool names/args are validated by the runner's registry *after* parsing, and any
  malformed answer becomes PlannerUnavailable (park), never a guessed action.
"""

from __future__ import annotations

import json
from typing import Any
from urllib import request as _urlrequest
from urllib.parse import urlparse
from urllib.error import URLError  # noqa: F401  (re-exported for tests to patch)

from .guard import Guard, KIND_PROVIDER, PUBLIC

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
# The V3 planner brain (decision record in docs/ROADMAP.md, 2026-09-29;
# amended for the locate family 2026-09-30; fourth verdict 2026-10-05;
# fifth verdict same day):
# Qwen2.5-3B-Instruct is the pinned brain and GATE-PASSING across all three
# goal families (read, memory, locate: json% = tool% = 100% in every one).
# The 2026-09-30 demotion to best-available was overturned by the
# operator-approved diagnostic lever (file.read misses now carry the
# workspace's real file names - data in a tool result, not a prompt
# instruction; the matrix re-run passed 60/60 clean parks, locate 5/5 done).
# Fifth verdict, same day: file.write's menu line alone collapsed locate
# (90% -> 5% verified turns at n=20, non-overlapping Wilson intervals vs
# the six-tool control - and the fourth verdict's 100% was an n=5
# overestimate; the six-tool truth is ~90% [70,97]). Operator decision:
# the action menu is capability-gated (advertises only tools within the
# operator's ceiling; the guard stays the authority), and the gate
# re-passed on the final harness - canonical 60-episode run 100% within
# every family, n=20 refinement: memory 100% [84,100], locate 90% [70,97].
# Sixth verdict, 2026-10-07: git.add (8th tool, ships inert behind the
# git ceiling) left the default-ceiling menu byte-identical - canonical
# 60-episode re-run passed 100% within every family, no re-ranking.
# Gate-passing stands on the canonical gate with the locate variance
# honestly on the record. The model did not change; the harness did.
# Changing this constant re-opens the V3 decision: run the matrix on the
# new candidate first, then update the decision record, then this tag.
DEFAULT_MODEL = "qwen2.5:3b-instruct"
_TIMEOUT_S = 60

PLAN_SYSTEM = (
    "You are Thoth's planner. Reply with ONE JSON object only, no prose:\n"
    '{"tool": "<tool name or null>", "args": {...}, "summary": "<one line>",\n'
    ' "next_intent": "<one line>", "done": <true|false>}\n'
    "Choose a tool from the provided TOOL LIST only. If the goal is complete, "
    'set "done": true and "tool": null. Never invent tools.'
)
# Prompt experiments are measured, not vibes: a discover-then-act heuristic
# ("never guess a name you have not seen ... discover first, then act") was
# tried here on 2026-09-30 against the locate-family failure and REVERTED
# after the matrix run - no locate improvement (tool% still 0%), and the
# memory family wobbled for the first time on the incumbent (json 100 -> 80).
# Full story in the journal (2026-W39) and ADR-006's record. Re-adding a
# prompt change means re-running the matrix before it ships.

FINISH_SYSTEM = (
    "You are Thoth's planner performing a FINISH CHECK. No tools are available. "
    "Given the goal and the verified results so far, reply with ONE JSON object "
    'only, no prose: {"done": <true|false>, "summary": "<one line - the answer '
    'or what was accomplished>"}'
)


class OllamaUnavailable(RuntimeError):
    """The local server is unreachable, the model is missing, or the answer is
    not parseable. All three park the run (ADR-004: degradation, not guessing)."""


def _assert_loopback(endpoint: str) -> None:
    host = urlparse(endpoint).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise OllamaUnavailable(
            f"refusing non-loopback endpoint '{endpoint}': local means local")


def _guarded(conn, target: str) -> None:
    """Ask the guard before any byte leaves the process. Deny raises."""
    g = Guard(conn, actor="ollama-client")
    decision = g.check_egress(KIND_PROVIDER, target, data_class=PUBLIC)
    if not decision.allowed:
        raise OllamaUnavailable(f"guard denied egress: {decision.reason} "
                                f"(rule={decision.rule})")


def _post_json(endpoint: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    _assert_loopback(endpoint)
    url = endpoint.rstrip("/") + path
    req = _urlrequest.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with _urlrequest.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310 (loopback only)
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# public surface
# ---------------------------------------------------------------------------


def probe(conn, endpoint: str = DEFAULT_ENDPOINT) -> tuple[bool, str]:
    """Availability probe for the ladder's cache: /api/tags must list models.
    Guard-gated; safe to call from the scheduler (never from route()).

    Caller errors (non-loopback endpoint) raise; only *availability* failures
    (unreachable, no models) come back as (False, reason)."""
    _assert_loopback(endpoint)  # caller error, not an availability condition
    target = f"local:ollama@{endpoint}"
    try:
        _guarded(conn, target)
        data = _post_json(endpoint, "/api/tags", {})
        names = [m.get("name", "") for m in data.get("models", [])]
        if not names:
            return False, "ollama reachable but no models pulled"
        return True, f"models: {', '.join(names[:3])}"
    except OllamaUnavailable as exc:
        return False, str(exc)
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        return False, f"ollama probe failed: {type(exc).__name__}: {exc}"


def attempt(conn, prompt: str, *, endpoint: str = DEFAULT_ENDPOINT,
            model: str = DEFAULT_MODEL, timeout_s: int = _TIMEOUT_S,
            system: str = PLAN_SYSTEM, temperature: float = 0.2) -> str:
    """One guarded chat completion; returns the model's text answer.
    `system`/`temperature` are overridable so the finish-confirmation probe can
    ask a tool-free decision question (journal 2026-W39: with an action menu
    visible, small models re-act instead of finishing — 0/6 vs 2/2 measured)."""
    target = f"local:ollama@{endpoint}"
    _guarded(conn, target)
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": temperature},
    }
    try:
        data = _post_json(endpoint, "/api/chat", payload)
    except (URLError, OSError) as exc:
        raise OllamaUnavailable(f"ollama chat failed: {type(exc).__name__}: {exc}") from exc
    answer = (data.get("message") or {}).get("content", "")
    if not answer.strip():
        raise OllamaUnavailable("ollama returned an empty answer")
    return answer


def plan_from_json(answer: str) -> dict[str, Any]:
    """Parse the model's answer into a plan dict. Raises OllamaUnavailable on
    malformed output — parking is always preferable to guessing."""
    text = answer.strip()
    if text.startswith("```"):  # tolerate fenced answers
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise OllamaUnavailable(f"no JSON object in planner answer: {answer[:120]!r}")
    try:
        plan, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise OllamaUnavailable(f"planner answer not valid JSON: {exc}") from exc
    if not isinstance(plan, dict):
        raise OllamaUnavailable("planner answer is not a JSON object")
    tool = plan.get("tool")
    if tool is not None and not isinstance(tool, str):
        raise OllamaUnavailable(f"planner tool must be string or null: {tool!r}")
    if not isinstance(plan.get("args", {}), dict):
        raise OllamaUnavailable("planner args must be an object")
    plan["args"] = plan.get("args") or {}
    plan["done"] = bool(plan.get("done", False))
    plan["summary"] = str(plan.get("summary", ""))[:200]
    plan["next_intent"] = str(plan.get("next_intent", ""))[:200]
    return plan
