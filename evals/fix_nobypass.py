"""One-shot fix + verify for the no-bypass whitelist and the ollama probe fix.

Run:  python evals/fix_nobypass.py
- Asserts tests/test_ollama.py is the real file (expected test names present).
- Asserts src/thoth/ollama.py carries the probe fix (loopback check hoisted
  above the try, so caller errors raise instead of masquerading as downtime).
- Patches tests/test_guard.py: the no-bypass scan must whitelist exactly two
  files (guard.py gates, ollama.py performs I/O) - replaces the exclusion line
  with an asserted, explicit two-file rule.
- ast-parses everything it touches. Fails loudly; never silently "fixes".
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLLAMA = ROOT / "src" / "thoth" / "ollama.py"
GUARD_TESTS = ROOT / "tests" / "test_guard.py"
OLLAMA_TESTS = ROOT / "tests" / "test_ollama.py"

EXPECTED_TEST_NAMES = (
    "test_probe_denied_by_stock_guard",
    "test_attempt_denied_by_stock_guard",
    "test_non_loopback_endpoint_refused",
    "test_plan_from_json_happy",
    "test_plan_from_json_rejects_garbage",
    "test_attempt_happy_path_with_patched_guard",
    "test_urllib_only_in_ollama_module",
)

OLD_EXCLUSION = 'return [p for p in root.rglob("*.py") if p.name != "guard.py"]'
NEW_EXCLUSION = (
    'allowed = {"guard.py", "ollama.py"}  # gate + the one I/O module it gates\n'
    '    return [p for p in root.rglob("*.py") if p.name not in allowed]'
)


def main() -> int:
    # 1. ollama tests file is the real one
    src = OLLAMA_TESTS.read_text(encoding="utf-8")
    missing = [n for n in EXPECTED_TEST_NAMES if n not in src]
    if missing:
        print(f"FAIL: test_ollama.py missing expected tests: {missing}")
        return 1
    print("test_ollama.py: expected tests present")

    # 2. probe fix present in ollama.py (hoisted loopback check)
    osrc = OLLAMA.read_text(encoding="utf-8")
    if "_assert_loopback(endpoint)  # caller error, not an availability condition" not in osrc:
        print("FAIL: ollama.py probe fix not present on disk")
        return 1
    print("ollama.py: probe fix present")

    # 3. patch the guard-test whitelist (idempotent)
    gsrc = GUARD_TESTS.read_text(encoding="utf-8")
    if '"ollama.py"' in gsrc and NEW_EXCLUSION.splitlines()[0] in gsrc:
        print("test_guard.py: whitelist already patched")
    elif OLD_EXCLUSION in gsrc:
        gsrc = gsrc.replace(OLD_EXCLUSION, NEW_EXCLUSION, 1)
        GUARD_TESTS.write_text(gsrc, encoding="utf-8")
        print("test_guard.py: whitelist patched (guard.py + ollama.py allowed)")
    else:
        print("FAIL: expected exclusion line not found in test_guard.py")
        return 1

    # 4. everything parses
    for p in (OLLAMA, GUARD_TESTS, OLLAMA_TESTS):
        ast.parse(p.read_text(encoding="utf-8"))
    print("all touched files parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
