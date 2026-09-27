"""Pytest wrapper for the golden set (runs the same 30 checks via the runner)."""

from __future__ import annotations

from evals.run_golden import run_all


def test_golden_set_all_pass():
    passed, total, results = run_all(verbose=False)
    failed = [(name, detail) for name, ok, detail in results if not ok]
    assert failed == [], (
        f"golden set {passed}/{total}:\n"
        + "\n".join(f"  {n} — {d}" for n, d in failed)
    )
