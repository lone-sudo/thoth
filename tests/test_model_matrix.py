"""Model matrix scoring core — offline, zero server.

The episode *recording* (run_episode) needs a live llama-server; the scoring
*core* (wilson / episode_scores / score_model / render_table) is pure data-in,
data-out and is what these tests pin. Live episodes are exercised by running
`python -m evals.model_matrix` (journal 2026-W39 protocol), never in CI.
"""

from __future__ import annotations

import json

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
        {"model": "qwen2.5-3b", "n": 5, "json_validity": 1.0,
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


# --------------------------------------------------------------- protocol pins

def test_matrix_models_catalog():
    """The catalog pins the exact four smoke-session GGUFs (journal 2026-W39)."""
    assert [m["key"] for m in MODELS] == [
        "smollm2-135m", "smollm2-360m", "qwen2.5-0.5b", "qwen2.5-3b"]
    assert [m["file"] for m in MODELS] == [
        "model.gguf", "model360.gguf", "qwen05b.gguf", "qwen3b.gguf"]


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
