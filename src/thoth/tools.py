"""Tool protocol + registry (ADR-003 §3): declared, leveled, idempotency-flagged.

V0.2 shipped exactly three read-only (level 0) tools:
  shell.read     — allowlisted read-only shell commands
  file.read      — workspace-scoped text file reads
  memory.search  — FTS over notes

V1 adds the first mutating tools (gittools.py, filetools.py):
  git.branch_create, git.checkout, git.commit  (levels 1-2, thoth/* only)
  file.write                                   (level 1, workspace create-only)

Every tool carries its own deterministic verifier; the runner never trusts the model's
judgment about whether an action worked.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import guard
from .events import emit, now_iso

MAX_OUTPUT_CHARS = 8_000


# --------------------------------------------------------------------------
# spec + registry
# --------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """Contract for one tool (ADR-003 §3)."""

    name: str
    description: str
    permission_level: int          # 0 observe .. 4 destructive
    idempotent: bool
    privacy_floor: int             # highest data class the tool may touch
                                   # (guard-enforced at the crossing; ADR-004)
    input_schema: dict[str, str]   # param name -> type tag: "str" | "int" | "path"
    required: set[str] = field(default_factory=set)
    run: Callable[..., dict[str, Any]] = None  # type: ignore[assignment]
    verify: Callable[[dict[str, Any]], "VerifyReport"] = None  # type: ignore[assignment]
    summarize: Callable[[dict[str, Any]], str] | None = None  # per-tool observation; None -> default_summarize


@dataclass
class VerifyReport:
    ok: bool
    detail: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def validate(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate args against the spec's schema; raise ValueError on failure."""
        spec = self.get(name)
        clean: dict[str, Any] = {}
        for param, tag in spec.input_schema.items():
            if param not in args:
                if param in spec.required:
                    raise ValueError(f"{name}: missing required param '{param}'")
                continue
            value = args[param]
            if tag == "int":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{name}: '{param}' must be int")
            elif tag == "str":
                if not isinstance(value, str):
                    raise ValueError(f"{name}: '{param}' must be str")
            elif tag == "path":
                if not isinstance(value, str):
                    raise ValueError(f"{name}: '{param}' must be a path string")
            clean[param] = value
        extra = set(args) - set(spec.input_schema)
        if extra:
            raise ValueError(f"{name}: unexpected params {sorted(extra)}")
        return clean


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------

def _result(ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": ok, "detail": detail}
    out.update(extra)
    return out


def _emit_tool_event(conn: sqlite3.Connection | None, kind: str, payload: dict[str, Any]) -> None:
    if conn is not None:
        emit(conn, kind, payload)


def default_summarize(result: dict[str, Any]) -> str:
    """Semantic-observation floor (journal 2026-W39): every turn checkpoint must
    carry WHAT happened, not just that something did — the planner self-
    terminates on semantic result lines, never on raw payloads (measured with
    Qwen2.5-3B). Tools override via ToolSpec.summarize; this is the fallback."""
    detail = str(result.get("detail") or "")
    payload = str(result.get("output") or result.get("content") or "")
    room = max(0, 200 - len(detail) - 3)
    return f"{detail} | {payload[:room]}" if payload else detail


# --------------------------------------------------------------------------
# shell.read
# --------------------------------------------------------------------------

_SHELL_ALLOWLIST = (
    "git status", "git log", "git diff", "git branch", "git show", "git rev-parse",
    "ls", "pwd", "cat", "head", "tail", "wc", "find", "grep", "python --version",
    "pip list",
)
_CMD_SPLIT = re.compile(r"\s+")
_FLAGISH = re.compile(r"^-[A-Za-z-]+$")


def _tokenize(cmd: str) -> list[str] | None:
    """Whitespace tokenizer; None if quoting tricks are present."""
    if any(ch in cmd for ch in (";", "|", "&", "`", "$", ">", "<", "\n", '"', "'")):
        return None
    return [t for t in _CMD_SPLIT.split(cmd.strip()) if t]


def _command_allowed(cmd: str) -> bool:
    """Allowlist check for the first tokens (flags skipped), no chaining."""
    tokens = _tokenize(cmd)
    if not tokens:
        return False
    head: list[str] = []
    for tok in tokens:
        if _FLAGISH.match(tok):
            continue
        head.append(tok)
        if len(head) == 2:
            break
    if len(head) == 1:
        head.append("")  # single-word commands like `pwd`
    probe = " ".join(head)
    return any(probe == pat or pat.startswith(probe + " ") or probe.startswith(pat + " ")
               for pat in _SHELL_ALLOWLIST)


def _run_shell(command: str, workdir: str | None = None, max_chars: int = MAX_OUTPUT_CHARS,
               _conn: sqlite3.Connection | None = None, **_: Any) -> dict[str, Any]:
    if not _command_allowed(command):
        return _result(False, "command not in read-only allowlist", blocked=True)
    try:
        proc = subprocess.run(
            _tokenize(command) or [],
            cwd=workdir or None,
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return _result(False, "timeout after 15s")
    except OSError as exc:
        return _result(False, f"spawn failed: {exc}")
    output = (proc.stdout + (f"\n[stderr]\n{proc.stderr}" if proc.stderr else "")).strip()
    truncated = len(output) > max_chars
    if truncated:
        output = output[:max_chars]
    result = _result(
        proc.returncode == 0,
        f"exit={proc.returncode}, {len(output)} chars" + (" [truncated]" if truncated else ""),
        exit_code=proc.returncode, output=output,
    )
    _emit_tool_event(_conn, "tool.shell_read", {"command": command, "ok": result["ok"]})
    return result


def _summarize_shell(result: dict[str, Any]) -> str:
    if result.get("blocked"):
        return f"shell.read blocked: {result.get('detail', '')}"
    out = str(result.get("output") or "")
    first = out.splitlines()[0][:120] if out else "(no output)"
    return f"Command finished ({result.get('detail', '')}). First output line: {first}"


def _verify_shell(result: dict[str, Any]) -> VerifyReport:
    if result.get("blocked"):
        return VerifyReport(False, "allowlist rejection")
    if "exit_code" not in result:
        return VerifyReport(False, "no exit code recorded")
    return VerifyReport(result["exit_code"] == 0 and bool(result.get("output")),
                        f"exit={result.get('exit_code')}, chars={len(result.get('output', ''))}")


# --------------------------------------------------------------------------
# file.read
# --------------------------------------------------------------------------

def _summarize_file(result: dict[str, Any]) -> str:
    """What the planner needs to feel done is *what was read* — not a char count
    followed by a mid-word prefix that reads as an unfinished document."""
    if result.get("blocked"):
        return f"file.read blocked: {result.get('detail', '')}"
    path = str(result.get("path") or "unknown path")
    head = str(result.get("content") or "")[:120].replace("\n", " ")
    return f"Read {path} ({result.get('detail', '')}). Starts with: {head}"


def _safe_path(workspace: Path, target: str) -> Path | None:
    """Resolve target inside workspace; None on escape or missing file."""
    try:
        resolved = (workspace / target).resolve()
        resolved.relative_to(workspace.resolve())
    except (ValueError, OSError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _run_file(path: str, workspace: str | None = None, max_chars: int = MAX_OUTPUT_CHARS,
              **_: Any) -> dict[str, Any]:
    ws = Path(workspace) if workspace else Path.cwd()
    resolved = _safe_path(ws, path)
    if resolved is None:
        # Honest diagnostics (journal 2026-W39): an escape attempt and a
        # missing target are different failures — the old single message
        # ("path escapes workspace or is not a file") read as "the file is
        # hidden" to a planner and likely killed its recovery (locate-family
        # finding, 2026-09-30). _safe_path collapses both to None, so
        # re-disambiguate here.
        if not isinstance(path, str) or not path:
            return _result(False, "path must be a non-empty string",
                           blocked=True)
        try:
            escapes = ((ws / path).resolve().relative_to(ws.resolve()) is None)
        except (ValueError, OSError):
            escapes = True  # relative_to raises exactly when it escapes
        if escapes:
            return _result(False, "path escapes the workspace", blocked=True)
        # Honest diagnostics, next increment (locate-family lever, 2026-10-05,
        # operator-approved): a wrong guess should learn what IS there - the
        # same fact a human colleague would give. Files only (file.read reads
        # files), sorted, capped; pure data in a tool result, never an
        # instruction. Matrix-gated before ship (ADR-006 discipline).
        try:
            listing = sorted(p.name for p in ws.iterdir() if p.is_file())[:8]
        except OSError:
            listing = []
        hint = f" (workspace has: {', '.join(listing)})" if listing else ""
        return _result(False, f"not a file in the workspace: {path}{hint}",
                       blocked=True)
    try:
        text = resolved.read_text(errors="replace")
    except OSError as exc:
        return _result(False, f"read failed: {exc}")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return _result(True, f"{len(text)} chars" + (" [truncated]" if truncated else ""),
                   content=text, path=str(resolved))


def _verify_file(result: dict[str, Any]) -> VerifyReport:
    if result.get("blocked"):
        # Carry the tool's own honest detail ("not a file in the workspace:
        # X" vs "path escapes the workspace") — the planner and the park
        # message see THIS string, not the result's.
        return VerifyReport(False, str(result.get("detail", "blocked")))
    ok = result.get("ok") is True and isinstance(result.get("content"), str)
    return VerifyReport(ok, f"chars={len(result.get('content', ''))}")


# --------------------------------------------------------------------------
# memory.search
# --------------------------------------------------------------------------

_FTS_BAD = re.compile(r'["\'^*()]')


def _run_memory(query: str, project: str | None = None, limit: int = 5,
                _conn: sqlite3.Connection | None = None, **_: Any) -> dict[str, Any]:
    """FTS over notes; pure read of the connection the runner already holds."""
    conn = _conn
    if conn is None:
        return _result(False, "no db connection provided")
    clean = _FTS_BAD.sub(" ", query).strip()
    if not clean:
        return _result(True, "empty query", results=[])
    terms = " ".join(f'"{tok}"' for tok in clean.split()[:8])
    sql = (
        "SELECT n.id, n.kind, n.body, n.project, n.created_at "
        "FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
        "WHERE notes_fts MATCH ? AND n.superseded_by IS NULL "
        "ORDER BY rank LIMIT ?"
    )
    params: list[Any] = [terms, int(limit)]
    if project:
        sql = sql.replace("ORDER BY rank", "AND (n.project = ? OR n.project IS NULL) ORDER BY rank")
        params.insert(1, project)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        return _result(False, f"fts error: {exc}")
    results = [
        {"id": r["id"], "kind": r["kind"], "body": r["body"],
         "project": r["project"], "created_at": r["created_at"]}
        for r in rows
    ]
    return _result(True, f"{len(results)} notes", results=results)


def _summarize_memory(result: dict[str, Any]) -> str:
    results = result.get("results") or []
    if not results:
        return f"Memory search: no notes matched ({result.get('detail', '')})."
    tops = "; ".join(str(r.get("body", ""))[:60] for r in results[:3])
    return f"Memory search found {len(results)} notes: {tops}"


def _verify_memory(result: dict[str, Any]) -> VerifyReport:
    return VerifyReport(result.get("ok") is True and "results" in result,
                        f"{len(result.get('results', []))} results")


# --------------------------------------------------------------------------
# default registry (V0.2: three level-0 tools)
# --------------------------------------------------------------------------

def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name="shell.read",
        description="Run an allowlisted read-only shell command (git/ls/cat/grep class).",
        permission_level=0, idempotent=True, privacy_floor=guard.PRIVATE,
        input_schema={"command": "str", "workdir": "str", "max_chars": "int"},
        required={"command"}, run=_run_shell, verify=_verify_shell,
        summarize=_summarize_shell,
    ))
    reg.register(ToolSpec(
        name="file.read",
        description="Read a text file inside the workspace.",
        permission_level=0, idempotent=True, privacy_floor=guard.PRIVATE,
        input_schema={"path": "path", "workspace": "str", "max_chars": "int"},
        required={"path"}, run=_run_file, verify=_verify_file,
        summarize=_summarize_file,
    ))
    reg.register(ToolSpec(
        name="memory.search",
        description="Full-text search over stored notes.",
        permission_level=0, idempotent=True, privacy_floor=guard.PRIVATE,
        input_schema={"query": "str", "project": "str", "limit": "int"},
        required={"query"}, run=_run_memory, verify=_verify_memory,
        summarize=_summarize_memory,
    ))
    # V1: the git branch protocol - the first mutating tools (levels 1-2).
    # Lazy import: gittools imports this module's helpers.
    from .gittools import register as _register_git_tools
    _register_git_tools(reg)
    # V1: the branch protocol's missing half - a content producer. Level 1,
    # inert until the operator raises the file ceiling (default 0).
    from .filetools import register as _register_file_tools
    _register_file_tools(reg)
    return reg
