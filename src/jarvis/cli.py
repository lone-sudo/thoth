"""Jarvis CLI: start / stop / continue / status / log / task.

Stdlib only (ADR-001). Every mutating command appends events (ADR-002).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, notes, resume, session, tasks
from .events import emit

DEFAULT_DB = Path.home() / ".jarvis" / "jarvis.db"


def _connect(args: argparse.Namespace):
    return db.connect(getattr(args, "db", None) or DEFAULT_DB)


# --------------------------------------------------------------------------
# command handlers (each returns a process exit code)
# --------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        project = args.project
        cwd = str(Path.cwd())

        open_row = session.current(conn, project)
        if open_row is not None:
            print(f"session already open (id {open_row['id']})")
            return 0

        if project:
            session.set_workdir(conn, project, cwd)
            emit(conn, "workdir.set", {"project": project, "path": cwd})

        task_id = None
        if args.task:
            task_id = tasks.add(conn, args.task, project=project)

        sid = session.start(conn, project=project, task=args.task)
        print(f"session {sid} started" + (f" for project '{project}'" if project else ""))
        if project:
            print(f"  workdir recorded: {cwd}")
        if task_id:
            print(f"  task added: {args.task} (id {task_id})")
        return 0
    finally:
        conn.close()


def cmd_stop(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        open_row = session.current(conn, getattr(args, "project", None))
        if open_row is None:
            print("no open session", file=sys.stderr)
            return 1
        session.stop(conn, open_row["id"], summary=args.summary)
        print(f"session {open_row['id']} stopped")
        if args.summary:
            print(f"  wrap-up note saved: {args.summary}")
        return 0
    finally:
        conn.close()


def cmd_continue(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        info = resume.build(conn, getattr(args, "project", None))
        if getattr(args, "json", False):
            print(json.dumps(info, indent=2, ensure_ascii=False))
        else:
            print(resume.render(info))
        return 0
    finally:
        conn.close()


def cmd_status(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        cur = session.current(conn, getattr(args, "project", None))
        if cur is not None:
            print(f"open session: {cur['id']} (project: {cur['project']}, since {cur['started_at']})")
        else:
            print("no open session")
        n_tasks = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status != 'done'"
        ).fetchone()["n"]
        print(f"open tasks: {n_tasks}")
        return 0
    finally:
        conn.close()


def cmd_log(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        rows = conn.execute(
            "SELECT ts, kind, payload_json FROM events ORDER BY ts DESC, id LIMIT ?",
            (args.limit,),
        ).fetchall()
        for r in rows:
            payload = json.loads(r["payload_json"])
            brief = (
                {k: v for k, v in payload.items() if k != "summary"}
                if r["kind"] == session.K_STOP
                else payload
            )
            print(f"{r['ts']}  {r['kind']:<20} {json.dumps(brief, ensure_ascii=False)}")
        return 0
    finally:
        conn.close()


def cmd_task(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        sub = args.task_cmd

        if sub == "add":
            tid = tasks.add(conn, args.title, project=args.project, depends_on=args.after)
            print(f"task {tid} added: {args.title}" + (f" (after {args.after})" if args.after else ""))
            return 0

        if sub == "list":
            rows = tasks.list_open(conn, args.project)
            if not rows:
                print("no open tasks")
                return 0
            for t in rows:
                dep = f" (after {t['depends_on']})" if t["depends_on"] else ""
                print(f"[{t['status']}] {t['id']}  {t['title']}{dep}")
            return 0

        if sub == "next":
            t = tasks.next_task(conn, args.project)
            if t is None:
                print("no runnable tasks (dependencies pending?)")
                return 1
            print(f"next: [{t['status']}] {t['id']}  {t['title']}")
            return 0

        if sub == "update":
            tasks.update_status(conn, args.id, args.status)
            print(f"task {args.id} -> {args.status}")
            return 0

        print("unknown task subcommand", file=sys.stderr)
        return 2
    finally:
        conn.close()


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def cmd_note(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        if args.note_cmd == "add":
            nid = notes.add(conn, args.body, kind=args.kind, project=args.project)
            print(f"note {nid} added ({args.kind})")
            return 0

        rows = notes.list_open(conn, project=args.project, kind=args.kind)
        if not rows:
            print("no notes")
            return 0
        for n in rows:
            proj = f" [{n['project']}]" if n["project"] else ""
            print(f"({n['kind']}) {n['id']}  {n['body']}{proj}")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="Jarvis — personal AI operating layer")
    p.add_argument("--db", help=f"database path (default {DEFAULT_DB})")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("start", help="start a session")
    sp.add_argument("--project")
    sp.add_argument("--task", help="create a task as part of starting")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help="stop the open session")
    sp.add_argument("--project")
    sp.add_argument("--summary", help="one-line wrap-up (saved as a note)")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("continue", help="where did I leave off?")
    sp.add_argument("--project")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_continue)

    sp = sub.add_parser("status", help="quick state check")
    sp.add_argument("--project")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("log", help="recent events")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_log)

    np = sub.add_parser("note", help="manage notes")
    nsub = np.add_subparsers(dest="note_cmd", required=True)

    nsp = nsub.add_parser("add", help="record an atomic note")
    nsp.add_argument("body")
    nsp.add_argument(
        "--kind",
        choices=sorted(notes.KINDS),
        default="fact",
        help="fact | decision | preference | lesson",
    )
    nsp.add_argument("--project")
    nsp.set_defaults(func=cmd_note)

    nsp = nsub.add_parser("list", help="list active notes")
    nsp.add_argument("--project")
    nsp.add_argument("--kind", choices=sorted(notes.KINDS))
    nsp.set_defaults(func=cmd_note)

    tp = sub.add_parser("task", help="manage tasks")
    tsub = tp.add_subparsers(dest="task_cmd", required=True)

    tsp = tsub.add_parser("add", help="create a task")
    tsp.add_argument("title")
    tsp.add_argument("--project")
    tsp.add_argument("--after", help="task id this one depends on")
    tsp.set_defaults(func=cmd_task)

    tsp = tsub.add_parser("list", help="list open tasks")
    tsp.add_argument("--project")
    tsp.set_defaults(func=cmd_task)

    tsp = tsub.add_parser("next", help="suggest the next runnable task")
    tsp.add_argument("--project")
    tsp.set_defaults(func=cmd_task)

    tsp = tsub.add_parser("update", help="set task status (todo|doing|done)")
    tsp.add_argument("id")
    tsp.add_argument("status", choices=["todo", "doing", "done"])
    tsp.set_defaults(func=cmd_task)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
