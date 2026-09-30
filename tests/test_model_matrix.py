"""Model matrix scoring core — offline, zero server.

The episode *recording* (run_episode) needs a live llama-server; the scoring
*core* (wilson / episode_scores / score_model / render_table) and the ADR-006
gate pieces (_candidate_entry / gate_row / gate_verdict, the goal-family
catalog, plus the main() audition flow with the server monkeypatched) are
pure data-in, data-out and are what these tests pin. Live episodes are exercised by running `python -m evals.model_matrix`
(journal 2026-W39 protocol), never in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evals import model_matrix as mm
from evals.model_matrix import (CLEAN_PARK_REASONS, DEFAULT_TEMPERATURE,
                                EPISODES_PER_MODEL, MODELS, episode_scores,
                                render_table, score_model, wilson)


# --------------------------------------------------------------- Wilson intervals

def test_wilson_edges_are_honest():
    """The whole point: 0/5 and 5/5 must NOT read as certainty. Wilson's
    construction pins the boundary-touching side exactly (lo=0 at k=0, hi=1 at
    k=n); the *informative* side is the opposite bound."""
    lo, hi = wilson(0, 5)
    assert lo == 0.0 and 0.0 < hi < 0.6    # 0/5 says "below ~57%", not "zero"
    lo5, hi5 = wilson(5, 5)
    assert lo5 > 0.4 and hi5 == 1.0        # 5/5 says "above ~43%", not "always"


def test_wilson_midpoint_and_clamping():
    lo, hi = wilson(2, 4)
    assert lo < 0.5 < hi                   # brackets the point estimate
    assert wilson(0, 0) == (0.0, 0.0)      # n=0 floor, no division error
    lo1, hi1 = wilson(1, 1)                # single success: honest, wide
    assert 0.05 < lo1 and hi1 == 1.0
    for k in range(6):                     # never escapes [0, 1]
        lo, hi = wilson(k, 5)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_narrows_with_n():
    """Same proportion, more data -> tighter interval (the n=2 -> n=5 story)."""
    lo2, hi2 = wilson(1, 2)
    lo5, hi5 = wilson(3, 5)                # 0.6 vs 0.5 — close enough for width
    assert (hi2 - lo2) > (hi5 - lo5) - 0.01


# --------------------------------------------------------------- episode scores

def test_scores_done_episode():
    ep = {"model": "m", "status": "done", "reason": "goal achieved",
          "planner_attempts": 3, "planner_parsed": 3, "verified_turns": 1}
    s = episode_scores(ep)
    assert s == {"json_valid": 1.0, "tool_turn": 1.0,
                 "clean_park": 1.0, "self_finish": 1.0}


def test_scores_partial_json_validity():
    ep = {"model": "m", "status": "parked", "reason": "max turns",
          "planner_attempts": 4, "planner_parsed": 1, "verified_turns": 1}
    s = episode_scores(ep)
    assert s["json_valid"] == 0.25          # 1 of 4 answers parsed
    assert s["tool_turn"] == 1.0            # it did act through a tool
    assert s["clean_park"] == 1.0           # 'max turns' is a diagnostic park
    assert s["self_finish"] == 0.0          # budget parked, model never said done


def test_scores_crash_park_is_unclean():
    ep = {"model": "m", "status": "parked", "reason": "timeout after 15s",
          "planner_attempts": 2, "planner_parsed": 2, "verified_turns": 1}
    assert episode_scores(ep)["clean_park"] == 0.0


def test_scores_crash_park_prefix_match_only():
    """Prefix discipline: 'repeat-breaker: ...' is clean; a reason that merely
    *contains* clean words elsewhere is not."""
    ok = {"model": "m", "status": "parked",
          "reason": "repeat-breaker: identical idempotent action repeated",
          "planner_attempts": 2, "planner_parsed": 2, "verified_turns": 1}
    assert episode_scores(ok)["clean_park"] == 1.0
    sneaky = dict(ok, reason="crashed on: repeat-breaker rule")
    assert episode_scores(sneaky)["clean_park"] == 0.0


def test_scores_zero_attempts_still_bounded():
    ep = {"model": "m", "status": "parked", "reason": "no provider: ladder empty",
          "planner_attempts": 0, "planner_parsed": 0, "verified_turns": 0}
    s = episode_scores(ep)
    assert s["json_valid"] == 0.0           # no answers parsed
    assert s["tool_turn"] == 0.0 and s["self_finish"] == 0.0
    assert s["clean_park"] == 1.0           # 'no provider' parks are expected


# --------------------------------------------------------------- aggregation

def test_score_model_pools_answers_and_averages_episodes():
    """json_validity pools raw answers; the other three average episodes.
    Both carry Wilson 95% interval keys."""
    eps = [
        {"model": "m", "status": "done", "reason": "r", "planner_attempts": 2,
         "planner_parsed": 2, "verified_turns": 2},
        {"model": "m", "status": "parked", "reason": "max turns",
         "planner_attempts": 2, "planner_parsed": 0, "verified_turns": 0},
    ]
    row = score_model(eps)
    assert row["model"] == "m" and row["n"] == 2
    assert row["json_validity"] == 0.5      # pooled: 2 parsed of 4 attempted
    assert row["tool_turn_rate"] == 0.5
    assert row["park_cleanliness"] == 1.0   # both terminal states expected
    assert row["self_finish"] == 0.5
    assert row["reason"] == "max turns"     # last episode's reason surfaces
    for key in ("json_validity", "tool_turn_rate", "park_cleanliness",
                "self_finish"):
        assert 0.0 <= row[f"{key}_lo"] <= row[key] <= row[f"{key}_hi"] <= 1.0


def test_score_model_pooling_differs_from_mean_of_ratios():
    """The pooling rationale (journal 2026-W39): an episode with 1 attempt is
    not worth 1/2 of an episode with 12. Mean-of-ratios would say 0.5 here;
    pooling says 12/13."""
    eps = [
        {"model": "m", "status": "parked", "reason": "max turns",
         "planner_attempts": 12, "planner_parsed": 12, "verified_turns": 1},
        {"model": "m", "status": "parked", "reason": "max turns",
         "planner_attempts": 1, "planner_parsed": 0, "verified_turns": 0},
    ]
    row = score_model(eps)
    assert abs(row["json_validity"] - 12 / 13) < 1e-9
    # mean-of-ratios would have said 0.5 — pooling is the honest number here
    assert abs(row["json_validity"] - 0.5) > 0.4
    # the interval brackets its own point estimate (same trials, journal W39)
    assert row["json_validity_lo"] < row["json_validity"] < row["json_validity_hi"]


def test_score_model_empty_is_zeroed_not_crashing():
    row = score_model([])
    assert row["n"] == 0 and row["model"] == "?"
    assert row["self_finish"] == 0.0
    assert row["reason"] == "no episodes"


def test_score_model_perfect_run_keeps_honest_interval():
    """100% at n=5 must not read as certainty: lo stays well below 1."""
    eps = [{"model": "m", "status": "done", "reason": "r",
            "planner_attempts": 2, "planner_parsed": 2,
            "verified_turns": 1} for _ in range(5)]
    row = score_model(eps)
    assert row["self_finish"] == 1.0
    assert row["self_finish_lo"] > 0.5 and row["self_finish_hi"] == 1.0
    assert row["json_validity"] == 1.0


# --------------------------------------------------------------- rendering

def test_render_table_rows_are_stable_and_aligned():
    rows = [
        {"model": "smollm2-135m", "n": 5, "json_validity": 0.5,
         "json_validity_lo": 0.2, "json_validity_hi": 0.8,
         "tool_turn_rate": 0.0, "tool_turn_rate_lo": 0.0,
         "tool_turn_rate_hi": 0.6, "park_cleanliness": 0.5,
         "park_cleanliness_lo": 0.2, "park_cleanliness_hi": 0.8,
         "self_finish": 0.0, "self_finish_lo": 0.0, "self_finish_hi": 0.6},
        {"model": "qwen2.5-3b", "family": "memory", "n": 5, "json_validity": 1.0,
         "json_validity_lo": 0.5, "json_validity_hi": 1.0,
         "tool_turn_rate": 1.0, "tool_turn_rate_lo": 0.5,
         "tool_turn_rate_hi": 1.0, "park_cleanliness": 1.0,
         "park_cleanliness_lo": 0.5, "park_cleanliness_hi": 1.0,
         "self_finish": 1.0, "self_finish_lo": 0.5, "self_finish_hi": 1.0},
    ]
    out = render_table(rows)
    lines = out.splitlines()
    assert len(lines) == 4                  # header + rule + two rows
    assert all(len(l) == len(lines[0]) for l in lines)   # fixed width
    assert "[50,100]" in lines[3]           # Wilson brackets visible
    assert "[0,60]" in lines[2]
    assert lines[0].startswith("| model")
    assert "| family" in lines[0]           # the family column exists
    assert "| read " in lines[2]            # row families rendered (default read)
    assert "| memory " in lines[3]          # and explicit on the row itself


# --------------------------------------------------------------- protocol pins

def test_matrix_models_catalog():
    """The catalog pins the exact four smoke-session GGUFs (journal 2026-W39)."""
    assert [m["key"] for m in MODELS] == [
        "smollm2-135m", "smollm2-360m", "qwen2.5-0.5b", "qwen2.5-3b"]
    assert [m["file"] for m in MODELS] == [
        "model.gguf", "model360.gguf", "qwen05b.gguf", "qwen3b.gguf"]


def test_goal_families_catalog():
    """Two goal families (ADR-006 amended 2026-09-30), both hint-free and
    ASCII-safe (cp1252 consoles). Family 'read' IS the smoke driver's exported
    PLAIN_GOAL verbatim — single source of truth, so read-family rows stay
    comparable with the --plain benchmark; 'memory' is retrieval via FTS."""
    assert tuple(mm.GOAL_FAMILIES) == ("read", "memory")
    assert mm.FAMILY_ORDER == ("read", "memory")
    import evals.smoke_local_planner as smoke
    assert mm.GOAL_FAMILIES["read"]["goal"] == smoke.PLAIN_GOAL
    assert mm.GOAL_FAMILIES["read"]["describe"] == "file.read"
    assert mm.GOAL_FAMILIES["memory"]["describe"] == "memory.search"
    for spec in mm.GOAL_FAMILIES.values():
        assert spec["goal"].isascii()
        assert spec["describe"].isascii()


def test_score_model_tags_family_and_refuses_mix():
    """Rows carry their family; mixed-family episode lists are refused — the
    gate scores each family separately (per-family admissibility)."""
    row = score_model([{"model": "m", "family": "memory", "status": "done",
                        "reason": "r", "planner_attempts": 1,
                        "planner_parsed": 1, "verified_turns": 1}])
    assert row["family"] == "memory"
    mixed = [{"model": "m", "family": "read", "status": "done", "reason": "r",
              "planner_attempts": 1, "planner_parsed": 1, "verified_turns": 1},
             {"model": "m", "family": "memory", "status": "done", "reason": "r",
              "planner_attempts": 1, "planner_parsed": 1, "verified_turns": 1}]
    with pytest.raises(ValueError, match="mixes goal families"):
        score_model(mixed)


def test_stabilized_protocol_defaults():
    """Publication-grade protocol (journal 2026-W39): 5 episodes, temperature
    pinned at the production planner's 0.2 — not the smoke demo's 0.4."""
    assert EPISODES_PER_MODEL == 5
    assert DEFAULT_TEMPERATURE == 0.2
    import evals.smoke_local_planner as smoke
    assert "temperature" in smoke._FLAGS    # shim reads the pin
    assert smoke._FLAGS["temperature"] == 0.4  # demo default unchanged


def test_clean_park_reasons_are_pure_ascii_prefixes():
    """Console output stays cp1252-safe (Windows quirk, journal 2026-W39)."""
    for reason in CLEAN_PARK_REASONS:
        assert reason.isascii()
    assert "repeat-breaker" in CLEAN_PARK_REASONS
    assert "max turns" in CLEAN_PARK_REASONS


def test_score_rows_are_json_serializable():
    """--json output must serialize (it feeds the roadmap decision log)."""
    row = score_model([{"model": "m", "status": "done", "reason": "r",
                        "planner_attempts": 1, "planner_parsed": 1,
                        "verified_turns": 1}])
    parsed = json.loads(json.dumps(row))
    assert parsed["self_finish"] == 1.0
    assert parsed["self_finish_hi"] == 1.0


# --------------------------------------------------------- ADR-006 gate pieces

def _gate_rows(model: str = "m", *, read_ok: bool = True,
               memory_ok: bool = True, n: int = 5) -> list[dict]:
    """Two family rows in gate order; *_ok=False degrades tool% only."""
    def _row(fam: str, ok: bool) -> dict:
        return {"model": model, "family": fam, "n": n,
                "json_validity": 1.0, "json_validity_lo": 0.5,
                "json_validity_hi": 1.0,
                "tool_turn_rate": 1.0 if ok else 0.0,
                "tool_turn_rate_lo": 0.0, "tool_turn_rate_hi": 1.0,
                "park_cleanliness": 1.0, "park_cleanliness_lo": 0.5,
                "park_cleanliness_hi": 1.0,
                "self_finish": 1.0 if ok else 0.0,
                "self_finish_lo": 0.0, "self_finish_hi": 1.0}
    return [_row("read", read_ok), _row("memory", memory_ok)]


def test_gate_row_folds_family_rows():
    flat = mm.gate_row(_gate_rows())
    assert flat["model"] == "m" and flat["n"] == 5
    assert flat["families"] == ["read", "memory"]
    assert flat["json_validity:read"] == 1.0
    assert flat["tool_turn_rate:memory"] == 1.0


def test_gate_row_refuses_duplicate_family():
    rows = _gate_rows()
    with pytest.raises(ValueError, match="duplicate family"):
        mm.gate_row(rows + [dict(rows[0])])


def test_gate_verdict_requires_every_family():
    """Amended ADR-006 section 4, as code: json% = tool% = 100% WITHIN EVERY
    goal family. A model that reads but cannot retrieve FAILS — the exact
    generalization catch the amendment exists for."""
    assert mm.gate_verdict(mm.gate_row(_gate_rows())) == "PASS"
    assert mm.gate_verdict(mm.gate_row(_gate_rows(memory_ok=False))) == "FAIL"
    assert mm.gate_verdict(mm.gate_row(_gate_rows(read_ok=False))) == "FAIL"
    rows = _gate_rows()                     # 99% JSON in ONE family: still fail
    rows[1]["json_validity"] = 0.99
    assert mm.gate_verdict(mm.gate_row(rows)) == "FAIL"
    assert mm.gate_verdict(mm.gate_row(_gate_rows()[:1])) == "incomplete"
    rows = _gate_rows()                     # n = the weakest family's link
    rows[1]["n"] = 0
    assert mm.gate_verdict(mm.gate_row(rows)) == "incomplete"


def test_gate_verdict_fails_loudly_on_malformed_record():
    """Every family present but a metric key missing -> KeyError. The gate
    fails loudly, never silently passes on malformed input."""
    flat = mm.gate_row(_gate_rows())
    del flat["tool_turn_rate:memory"]
    with pytest.raises(KeyError):
        mm.gate_verdict(flat)


def test_candidate_entry_resolves_absolute_path(tmp_path):
    gguf = tmp_path / "cand.gguf"
    gguf.write_bytes(b"x")
    e = mm._candidate_entry("cand", str(gguf), "1.5B")
    assert e["key"] == "cand" and e["params"] == "1.5B"
    assert e["file"] == "cand.gguf"
    assert Path(e["gguf"]).is_absolute() and Path(e["gguf"]).is_file()


def test_candidate_entry_refuses_duplicate_and_reserved_keys(capsys):
    for key in ("smollm2-135m", "qwen2.5-3b"):
        with pytest.raises(SystemExit) as ei:
            mm._candidate_entry(key, "Z:/nope/x.gguf", "?")
        assert ei.value.code == 2
        assert "refusing" in capsys.readouterr().out


def test_candidate_entry_refuses_missing_gguf(capsys):
    with pytest.raises(SystemExit) as ei:
        mm._candidate_entry("cand", "Z:/nope/missing.gguf", "?")
    assert ei.value.code == 2
    assert "GGUF not found" in capsys.readouterr().out


def test_main_audition_pass_flow(monkeypatch, tmp_path, capsys):
    """The self-serve gate end to end, server monkeypatched: --add resolves
    the GGUF, swaps the server ONCE, scores EVERY goal family, prints the
    verdict over the folded record."""
    gguf = tmp_path / "cand.gguf"
    gguf.write_bytes(b"x")

    def _ep(model_key, family):
        return {"model": model_key, "family": family, "status": "done",
                "reason": "earned finish", "planner_attempts": 2,
                "planner_parsed": 2, "verified_turns": 1, "finish_probes": 0}

    swaps: list[Path] = []
    monkeypatch.setattr(mm, "_swap_model", lambda s, g, port=mm.PORT: swaps.append(g))
    monkeypatch.setattr(mm, "run_episode", _ep)
    monkeypatch.setattr(sys, "argv",
                        ["model_matrix", "--add", "cand", str(gguf), "1.5B",
                         "--episodes", "1"])
    rc = mm.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert swaps == [gguf.resolve()]          # ONE server load, both families
    assert "ADR-006 gate verdict for cand: PASS" in out
    assert "family: read" in out and "family: memory" in out
    assert "1.5B" in out


def test_main_audition_fail_flow(monkeypatch, tmp_path, capsys):
    """A protocol-perfect non-actor FAILs the gate even at 100% JSON —
    the exact qwen2.5-0.5b shape, now rejectable by one command."""
    gguf = tmp_path / "liar.gguf"
    gguf.write_bytes(b"x")

    def _ep(model_key, family):
        return {"model": model_key, "family": family, "status": "parked",
                "reason": "finish floor: done claimed with zero verified turns",
                "planner_attempts": 1, "planner_parsed": 1,
                "verified_turns": 0, "finish_probes": 0}

    monkeypatch.setattr(mm, "_swap_model", lambda s, g, port=mm.PORT: None)
    monkeypatch.setattr(mm, "run_episode", _ep)
    monkeypatch.setattr(sys, "argv",
                        ["model_matrix", "--add", "liar", str(gguf), "?",
                         "--episodes", "1"])
    rc = mm.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "ADR-006 gate verdict for liar: FAIL" in out
    assert "not routable" in out


def test_main_audition_without_all_families_is_incomplete(monkeypatch, tmp_path,
                                                          capsys):
    """A --family-restricted audition honestly reports 'incomplete' instead of
    dressing a partial run up as a verdict."""
    gguf = tmp_path / "half.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(mm, "_swap_model", lambda s, g, port=mm.PORT: None)
    monkeypatch.setattr(mm, "run_episode",
                        lambda key, fam: {"model": key, "family": fam,
                                          "status": "done", "reason": "r",
                                          "planner_attempts": 1,
                                          "planner_parsed": 1,
                                          "verified_turns": 1,
                                          "finish_probes": 0})
    monkeypatch.setattr(sys, "argv",
                        ["model_matrix", "--add", "half", str(gguf), "?",
                         "--episodes", "1", "--family", "read"])
    rc = mm.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "gate verdict for half: incomplete" in out
    assert "rerun without --family" in out


def test_main_rejects_unknown_family(monkeypatch, tmp_path, capsys):
    gguf = tmp_path / "cand.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(sys, "argv",
                        ["model_matrix", "--add", "cand", str(gguf), "?",
                         "--family", "nope"])
    assert mm.main() == 2
    assert "unknown goal family(s): nope" in capsys.readouterr().out


def test_run_episode_rejects_unknown_family(monkeypatch):
    """Family validation happens before any I/O — no db, no server."""
    def _boom(*a, **k):
        raise AssertionError("no db access may happen for an unknown family")
    monkeypatch.setattr(mm.db, "connect", _boom)
    with pytest.raises(ValueError, match="unknown goal family"):
        mm.run_episode("m", "nope")
