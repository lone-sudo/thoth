"""Model matrix scoring core — offline, zero server.

The episode *recording* (run_episode) needs a live llama-server; the scoring
*core* (episode_scores / score_model / render_table) is pure data-in, data-out
and is what these tests pin. Live episodes are exercised by running
`python -m evals.model_matrix` (journal 2026-W39 protocol), never in CI.
"""

from __future__ import annotations

import json

from evals.model_matrix import (CLEAN_PARK_REASONS, MODELS, episode_scores,
                                render_table, score_model)


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
    assert s["json_valid"] == 0.0           # no answers parsed, denominator floors at 1
    assert s["tool_turn"] == 0.0 and s["self_finish"] == 0.0
    assert s["clean_park"] == 1.0           # 'no provider' parks are expected


# --------------------------------------------------------------- aggregation

def test_score_model_averages_episodes():
    eps = [
        {"model": "m", "status": "done", "reason": "r", "planner_attempts": 2,
         "planner_parsed": 2, "verified_turns": 2},
        {"model": "m", "status": "parked", "reason": "max turns",
         "planner_attempts": 2, "planner_parsed": 0, "verified_turns": 0},
    ]
    row = score_model(eps)
    assert row["model"] == "m" and row["n"] == 2
    assert row["json_validity"] == 0.5      # (2/2 + 0/2) / 2
    assert row["tool_turn_rate"] == 0.5
    assert row["park_cleanliness"] == 1.0   # both terminal states expected
    assert row["self_finish"] == 0.5
    assert row["reason"] == "max turns"     # last episode's reason surfaces


def test_score_model_empty_is_zeroed_not_crashing():
    row = score_model([])
    assert row["n"] == 0
    assert row["self_finish"] == 0.0
    assert row["reason"] == "no episodes"


# --------------------------------------------------------------- rendering

def test_render_table_rows_are_stable_and_aligned():
    rows = [
        {"model": "smollm2-135m", "n": 2, "json_validity": 0.5,
         "tool_turn_rate": 0.0, "park_cleanliness": 0.5, "self_finish": 0.0},
        {"model": "qwen2.5-3b", "n": 2, "json_validity": 1.0,
         "tool_turn_rate": 1.0, "park_cleanliness": 1.0, "self_finish": 1.0},
    ]
    out = render_table(rows)
    lines = out.splitlines()
    assert len(lines) == 4                  # header + rule + two rows
    assert all(len(l) == len(lines[0]) for l in lines)   # fixed width
    assert "100%" in lines[3] and "0%" in lines[2]
    assert lines[0].startswith("| model")


def test_matrix_models_catalog():
    """The catalog pins the exact four smoke-session GGUFs (journal 2026-W39)."""
    assert [m["key"] for m in MODELS] == [
        "smollm2-135m", "smollm2-360m", "qwen2.5-0.5b", "qwen2.5-3b"]
    assert [m["file"] for m in MODELS] == [
        "model.gguf", "model360.gguf", "qwen05b.gguf", "qwen3b.gguf"]


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
    assert json.loads(json.dumps(row))["self_finish"] == 1.0
