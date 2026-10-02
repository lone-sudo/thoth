"""The bare `thoth` experience: a numbered menu of today's moves.

`thoth` with no arguments is the zero-typing interface for tired days. It
reads stored state (current session, next runnable task, last parked run)
and dispatches the REAL CLI commands through `cli.main` - every menu move
is literally the documented command, so the menu can never diverge from
the surface it fronts. Still stdlib-only, still no new I/O surface: input()
plus print().

Exit codes: the menu itself always exits 0 (it is a loop around successful
commands); Ctrl-C or the quit option leaves cleanly. `--db` flows through
to every dispatched command because the argv is rebuilt from the one the
user actually passed.
"""

from __future__ import annotations

from . import cli, runner, session, tasks


def _argv(args, tail: list[str]) -> list[str]:
    return ["--db", str(getattr(args, "db", None) or cli.DEFAULT_DB), *tail]


def _dispatch(args, tail: list[str]) -> None:
    """Run the real command. stdout is already the terminal; exit codes of
    dispatched commands are informational inside a menu loop."""
    try:
        cli.main(_argv(args, tail))
    except SystemExit:  # argparse or a fail-closed exit - stay in the menu
        pass


def build_options(conn) -> list[tuple[str, list[str]]]:
    """(label, argv-tail) pairs for today's moves, computed from state."""
    opts: list[tuple[str, list[str]]] = []

    cur = session.current(conn, None)
    opts.append(("close the open session"
                 if cur is not None else "start a session",
                 ["stop"] if cur is not None else ["start"]))

    nxt = tasks.next_task(conn, None)
    if nxt is not None:
        opts.append((f"work on: {nxt['title']} ({nxt['id']})",
                     ["task", "update", nxt["id"], "doing"]))

    parked = runner.last_parked(conn, None)
    if parked is not None:
        opts.append((f"resume parked run {parked['id']} "
                     f"({(parked.get('goal') or 'no goal')[:48]})",
                     ["run", "resume"]))

    opts.append(("morning briefing", ["briefing"]))
    opts.append(("where did I leave off? (continue)", ["continue"]))
    opts.append(("state of everything (browser dashboard)",
                 ["briefing", "--html"]))
    opts.append(("open tasks", ["task", "list"]))
    opts.append(("recent events", ["log", "--limit", "10"]))
    return opts


def render_menu(opts: list[tuple[str, list[str]]]) -> str:
    lines = ["", "Thoth - what now?", "----------------"]
    for i, (label, _tail) in enumerate(opts, 1):
        lines.append(f" {i}. {label}")
    lines.append(" q. quit")
    return "\n".join(lines)


def run_menu(args, conn) -> None:
    """Loop: read, dispatch, repeat. One conn for the state read; dispatched
    commands manage their own connections via cli.main."""
    while True:
        opts = build_options(conn)
        print(render_menu(opts))
        try:
            raw = input("thoth> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if raw in ("q", "quit", "exit"):
            return
        if not raw.isdigit() or not (1 <= int(raw) <= len(opts)):
            print("(pick a number, or q)")
            continue
        _label, tail = opts[int(raw) - 1]
        _dispatch(args, tail)
