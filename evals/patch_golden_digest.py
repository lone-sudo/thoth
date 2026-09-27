"""One-shot patch: append golden checks 34-38 (digest) to golden_set.py.

Hardened against drift (this session had anchor mismatches):
- rewrites the `from thoth import ...` line via regex, whatever its member list
- anchors the GOLDEN-list tail on the _c33 line + its closing bracket, asserting
  nothing unexpected sits between them
- appends the check functions at end of file, then ast-parses the result
Idempotent: exits 0 without touching the file if _c34 is already present.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "golden_set.py"


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")

    if re.search(r"\b_c34\b", src):
        print("already patched")
        return 0

    # 1. import line: ensure `digest` is among the thoth imports (regex, any order)
    m = re.search(r"^from thoth import (.+)$", src, re.MULTILINE)
    if not m:
        raise SystemExit("no 'from thoth import ...' line found")
    members = [x.strip() for x in m.group(1).split(",")]
    if "digest" not in members:
        members.append("digest")
        src = src.replace(m.group(0), "from thoth import " + ", ".join(members), 1)
        print("import line updated:", members)

    # 2. list tail: anchor strictly on the _c33 line followed by the closing bracket
    tail_pat = re.compile(
        r'(    _check\("33\.briefing respects the 7-item cap", _c33\),\n)\]',
        re.MULTILINE)
    if not tail_pat.search(src):
        raise SystemExit("anchor missing: _c33 line + closing bracket not adjacent")
    src = tail_pat.sub(lambda mo: NEW_LIST_TAIL, src, count=1)

    TARGET.write_text(src, encoding="utf-8")
    ast.parse(TARGET.read_text(encoding="utf-8"))
    print("patched + parses OK")
    return 0


NEW_LIST_TAIL = '''    _check("33.briefing respects the 7-item cap", _c33),
    _check("34.digest flags overdue rcc task", _c34),
    _check("35.digest orders two overdue by deadline", _c35),
    _check("36.digest upcoming window excludes far dates", _c36),
    _check("37.digest accomplished-today from event log", _c37),
    _check("38.digest quiet day output and cap", _c38),
]


# ===========================================================================
# G. digest (34-38) - deadline-aware end-of-day report
# ===========================================================================

def _c34(conn, f):
    report = digest.build(conn, "rcc-suite", today="2026-09-27")
    flat = [i for _, items in report["sections"] for i in items]
    return any("OVERDUE" in i and "Fix PG16 migration" in i for i in flat), str(flat[:2])


def _c35(conn, f):
    report = digest.build(conn, None, today="2026-09-27")
    over = next((items for t, items in report["sections"] if "overdue" in t), [])
    ok = (len(over) >= 2 and "Backfill_fact_orders" in over[0]
          and "Fix PG16 migration" in over[1])
    return ok, str(over)  # eng (09-21) sorts before rcc (09-25)


def _c36(conn, f):
    tasks.add(conn, "Far future", project="rcc-suite", deadline="2027-06-01")
    report = digest.build(conn, "rcc-suite", today="2026-09-27")
    flat = [i for _, items in report["sections"] for i in items]
    return not any("Far future" in i for i in flat), "far-future leaked into digest"


def _c37(conn, f):
    tasks.update_status(conn, f.eng_task2, "done")
    report = digest.build(conn, "data-eng", today="2026-09-27")
    flat = [i for _, items in report["sections"] for i in items]
    ok = any("Backfill_fact_orders" in i for i in flat)
    tasks.update_status(conn, f.eng_task2, "todo")  # restore world (log keeps event)
    return ok, "done-today missing from digest"


def _c38(conn, f):
    big = digest.build(conn, None, today="2026-09-27")
    assert big["item_count"] <= 7, f"cap exceeded: {big['item_count']}"
    quiet = digest.build(conn, "nonexistent", today="2026-09-27")
    rendered = digest.render(quiet)
    return "Quiet day" in rendered, f"{rendered!r}"
'''


if __name__ == "__main__":
    raise SystemExit(main())
