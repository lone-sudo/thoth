"""The static dashboard: the whole state of Thoth in one local HTML file.

`thoth briefing --html` writes a single self-contained file you open in a
browser - no server, no port, no daemon, no JavaScript, no external
assets (ADR-001: CLI-first, stdlib-only; ADR-004: no new I/O surface).
It answers the at-a-glance question the sequential CLI cannot: what is
open, what is parked, what happened recently - straight from stored
state, same sources as the briefing and continue.
"""

from __future__ import annotations

import html as _html
import json
import sqlite3
from typing import Any

from . import briefing as briefing_mod
from . import runner, session, tasks
from .events import now_iso

STYLE = """\
body{font-family:Segoe UI,system-ui,sans-serif;margin:0;background:#f4f2ee;
color:#1f1d1a}main{max-width:780px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:13px;letter-spacing:.08em;
text-transform:uppercase;color:#8a8378;margin:28px 0 8px}
.sub{color:#8a8378;font-size:13px;margin:0 0 20px}
.card{background:#fff;border:1px solid #e4e0d8;border-radius:8px;
padding:12px 16px;margin:0 0 20px}.card ul{margin:6px 0;padding-left:18px}
.card li{margin:4px 0;line-height:1.45}.muted{color:#8a8378}
code{background:#efece5;padding:1px 5px;border-radius:4px;font-size:.92em}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:4px 8px;border-bottom:1px solid #efece5;vertical-align:top}
td.k{white-space:nowrap;color:#8a8378}
.none{color:#8a8378;font-style:italic}
.foot{color:#8a8378;font-size:12px;margin-top:28px}\
"""


def _esc(s: Any) -> str:
    return _html.escape(str(s))


def build(conn: sqlite3.Connection, project: str | None = None) -> dict[str, Any]:
    """Assemble the dashboard data (pure read; no network, no model)."""
    report: dict[str, Any] = {"generated": now_iso(), "project": project,
                              "sections": []}

    cur = session.current(conn, project)
    report["sections"].append((
        "now",
        [f"open session {cur['id']} on '{cur['project']}' since {cur['started_at']}"
         if cur is not None else "no open session (thoth start to begin one)"],
    ))

    nxt = tasks.next_task(conn, project)
    open_rows = tasks.list_open(conn, project)
    now_items: list[str] = []
    if nxt is not None:
        deadline = f", deadline {nxt['deadline']}" if nxt["deadline"] else ""
        dep = f" (after {nxt['depends_on']})" if nxt["depends_on"] else ""
        now_items.append(f"NEXT: [{nxt['status']}] {nxt['title']} ({nxt['id']}"
                         f"{dep}{deadline})")
    now_items.append(f"{len(open_rows)} open task(s)" if open_rows
                     else "no open tasks")
    for t in open_rows[:9]:
        deadline = f" - due {t['deadline']}" if t["deadline"] else ""
        now_items.append(f"[{t['status']}] {t['title']} ({t['id']}{deadline})")
    if len(open_rows) > 9:
        now_items.append(f"... and {len(open_rows) - 9} more (thoth task list)")
    report["sections"].append(("work", now_items))

    running = conn.execute(
        "SELECT id, project, goal FROM runs WHERE status = 'running' "
        "AND (? IS NULL OR project = ?) LIMIT 1", (project, project)).fetchone()
    # the park reason lives in the run.parked EVENT (ADR-002: events are
    # truth, tables are views) - same correlated subquery as runner.last_parked
    parked_rows = conn.execute(
        "SELECT r.id, r.project, r.goal, r.updated_at, "
        "(SELECT e.payload_json FROM events e WHERE e.kind = 'run.parked' "
        " AND json_extract(e.payload_json, '$.run_id') = r.id "
        " ORDER BY e.ts DESC LIMIT 1) AS park_payload "
        "FROM runs r WHERE r.status = 'parked' "
        "AND (? IS NULL OR r.project = ?) "
        "ORDER BY r.updated_at DESC LIMIT 10", (project, project)).fetchall()
    run_items: list[str] = []
    if running is not None:
        run_items.append(f"RUNNING: {running['id']} - {running['goal'] or '(no goal)'} "
                         f"[{running['project']}]")
    for r in parked_rows:
        reason = ""
        if r["park_payload"]:
            reason = f" - {json.loads(r['park_payload']).get('reason', '')}"
        run_items.append(f"PARKED: {r['id']} - {r['goal'] or '(no goal)'}{reason} "
                         f"resume: thoth run resume --project {r['project']}")
    if not run_items:
        run_items.append("no running or parked runs")
    report["sections"].append(("runs", run_items))

    kinds = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM notes WHERE superseded_by IS NULL "
        "GROUP BY kind ORDER BY n DESC").fetchall()
    kinds_txt = " - ".join(f"{r['n']} {r['kind']}s" for r in kinds) or "no notes yet"
    report["sections"].append(("memory", [f"{kinds_txt} active (thoth note list)"]))

    events = [
        (r["ts"], r["kind"], json.dumps(json.loads(r["payload_json"]),
                                        ensure_ascii=True))
        for r in conn.execute(
            "SELECT ts, kind, payload_json FROM events "
            "ORDER BY ts DESC, id DESC LIMIT 20")
    ]
    report["events"] = events
    return report


def render_html(report: dict[str, Any]) -> str:
    """The full standalone page. Every dynamic value passes through _esc;
    the style sheet is the only embedded asset."""
    parts = [
        "<!DOCTYPE html>", "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Thoth - state of everything</title>",
        f"<style>{STYLE}</style></head><body><main>",
        "<h1>Thoth - state of everything</h1>",
        f"<p class=\"sub\">generated {_esc(report['generated'])}"
        + (f" - project {_esc(report['project'])}" if report["project"] else "")
        + "</p>",
    ]
    for title, items in report["sections"]:
        parts.append(f"<h2>{_esc(title)}</h2><div class=\"card\"><ul>")
        for item in items:
            parts.append(f"<li>{_esc(item)}</li>")
        parts.append("</ul></div>")
    parts.append("<h2>recent events (last 20)</h2><div class=\"card\">")
    events = report.get("events") or []
    if events:
        parts.append("<table>")
        for ts, kind, payload in events:
            parts.append(f"<tr><td class=\"k\">{_esc(ts)}</td>"
                         f"<td><code>{_esc(kind)}</code> {_esc(payload)}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<span class=\"none\">no events yet</span>")
    parts.append("</div>")
    parts.append("<p class=\"foot\">generated from stored state only - $0, "
                 "no network, no model - thoth briefing --html</p>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def write_html(conn: sqlite3.Connection, project: str | None,
               path: Any = None) -> Any:
    """Build, render, and write the dashboard file. Default location is
    next to the database in ~/.thoth/ - outside any repo, like the DB
    itself. Returns the path written."""
    from pathlib import Path
    out = Path(path) if path is not None else Path.home() / ".thoth" / "thoth-dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(build(conn, project)), encoding="utf-8")
    return out
