"""Golden-set runner: `python -m evals.run_golden` (or via pytest wrapper).

Pass = Thoth's stored-state surfaces answer all 30 questions correctly from the DB
alone, zero AI calls. Exit code 0 only on a full pass. Run after any schema or
surface change; the Team-A stance is weekly and before every merge.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from .golden_set import GOLDEN
from .seed_world import seed


def run_all(verbose: bool = True) -> tuple[int, int, list[tuple[str, bool, str]]]:
    tmp = Path(tempfile.mkdtemp(prefix="thoth-golden-"))
    try:
        facts = seed(tmp)
        import sqlite3
        conn = sqlite3.connect(facts.db_path)
        conn.row_factory = sqlite3.Row
        results: list[tuple[str, bool, str]] = []
        for check in GOLDEN:
            ok, detail = check(conn, facts)
            results.append((check.__name__, ok, detail))
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # Windows may hold WAL handles briefly
    passed = sum(1 for _, ok, _ in results if ok)
    if verbose:
        for name, ok, detail in results:
            mark = "PASS" if ok else "FAIL"
            line = f"[{mark}] {name}"
            if detail and not ok:
                line += f"  — {detail}"
            print(line)
        print(f"\n{passed}/{len(results)} golden checks passed")
    return passed, len(results), results


def main() -> int:
    passed, total, results = run_all()
    failed = [r for r in results if not r[1]]
    if failed:
        print("\nFAILED:")
        for name, _, detail in failed:
            print(f"  {name}" + (f" — {detail}" if detail else ""))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
