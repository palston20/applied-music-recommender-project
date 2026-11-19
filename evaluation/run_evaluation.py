"""Automated evaluation for Soundtrack AI.

Runs every case in ``evaluation/cases.json`` through the full agent and reports
real metrics. By default the LLM is MOCKED with each case's scripted responses,
so results are deterministic and cost nothing. Use ``--llm gemini`` to run the
same cases against the live model, or ``--llm offline`` for the heuristic.

Two layers of checking:
- In-loop: the agent's PlaylistEvaluator (what a user would see).
- Independent: hand-labeled expectations per case (acceptable genres/moods,
  energy range, length, constraints) that do NOT depend on the LLM's own
  interpretation, so a misread request is still caught.

    python -m evaluation.run_evaluation            # mocked, prints table
    python -m evaluation.run_evaluation --verbose  # include tracklists
    python -m evaluation.run_evaluation --llm gemini
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dj_agent import PlaylistResult, SoundtrackAgent, trace_markdown  # noqa: E402
from src.llm_client import MockLLMClient, make_llm, template_copy  # noqa: E402

CASES_PATH = Path(__file__).with_name("cases.json")
def results_path(llm_mode: str) -> Path:
    return Path(__file__).with_name("results.json" if llm_mode == "mock" else f"results_{llm_mode}.json")


def traces_path(llm_mode: str) -> Path:
    return Path(__file__).with_name("agent_traces.md" if llm_mode == "mock" else f"agent_traces_{llm_mode}.md")
MIN_EXPECTATION_ALIGNMENT = 0.6


def load_cases(path: Path = CASES_PATH) -> List[Dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def mock_client_for(case: Dict) -> MockLLMClient:
    """Build a mock LLM from a case's scripted responses."""
    script = dict(case["mock"])
    copy = script.pop("copy", None)
    responses = {task: value for task, value in script.items()}
    if copy == "BAD_COPY":
        responses["copy"] = lambda prompt: json.dumps({
            "title": "Salt & Sunshine",
            "description": "Sandals on, phone on do-not-disturb.",
            "blurbs": [{"id": 999, "blurb": "A song that is not in this playlist."}],
        })
    else:
        copy = copy or {}
        responses["copy"] = lambda prompt, c=copy: template_copy(
            prompt, title=c.get("title"), description=c.get("description"))
    return MockLLMClient(responses)


def check_expectations(result: PlaylistResult, expect: Dict) -> Dict:
    """Independent ground-truth checks for one case."""
    tracks = result.tracks
    genres, moods = set(expect["genres"]), set(expect["moods"])
    lo, hi = expect["energy"]
    aligned = [t for t in tracks
               if (t.song["genre"] in genres or t.song["mood"] in moods)
               and lo <= t.song["energy"] <= hi]
    alignment = len(aligned) / len(tracks) if tracks else 0.0

    violations = []
    constraints = expect.get("constraints", {})
    for t in tracks:
        s = t.song
        if s["genre"] in constraints.get("exclude_genres", []):
            violations.append(f"{s['title']}: excluded genre {s['genre']}")
        if s["artist"] in constraints.get("exclude_artists", []):
            violations.append(f"{s['title']}: excluded artist")
        if "min_energy" in constraints and s["energy"] < constraints["min_energy"]:
            violations.append(f"{s['title']}: energy {s['energy']} < {constraints['min_energy']}")
        if "max_energy" in constraints and s["energy"] > constraints["max_energy"]:
            violations.append(f"{s['title']}: energy {s['energy']} > {constraints['max_energy']}")
        if expect.get("decades") and int(s.get("year") or 0) // 10 * 10 not in expect["decades"]:
            violations.append(f"{s['title']}: {s.get('year')} is outside the requested era")

    if expect.get("named_genres"):
        core = [t for t in tracks if t.role == "core"] or tracks
        share = sum(t.song["genre"] in expect["named_genres"] for t in core) / len(core)
        if share < expect.get("named_share", 0.5):
            violations.append(f"only {share:.0%} of core tracks are {'/'.join(expect['named_genres'])}")

    # Only check length when the request states one (None = any reasonable length).
    length_ok = expect["length"] is None or len(tracks) == expect["length"]
    return {
        "expectation_alignment": round(alignment, 3),
        "constraints_satisfied": not violations,
        "constraint_violations": violations,
        "length_ok": length_ok,
        "passed": alignment >= MIN_EXPECTATION_ALIGNMENT and not violations and length_ok,
    }


def run_case(case: Dict, llm_mode: str) -> Dict:
    llm = mock_client_for(case) if llm_mode == "mock" else make_llm(llm_mode)
    agent = SoundtrackAgent(llm)
    result = agent.run(case["request"], discovery_level=case.get("discovery"),
                       playlist_length=case.get("length"))
    expectations = check_expectations(result, case["expect"])
    tracks = result.tracks
    artists = [t.song["artist"] for t in tracks]
    ids = [t.id for t in tracks]
    copy_check = next((c for c in result.evaluation.checks if c.name == "copy_output"), None)
    return {
        "id": case["id"],
        "request": case["request"],
        "trace_markdown": trace_markdown(result, f"{case['id']} — {case['request']}"),
        "title": result.title,
        "evaluator_passed": result.passed,
        "expectations": expectations,
        "has_constraints": bool(case["expect"].get("constraints") or case["expect"].get("decades")),
        "overall_passed": result.passed and expectations["passed"],
        "avg_relevance": round(mean(t.relevance for t in tracks), 2) if tracks else 0.0,
        "artist_diversity": round(len(set(artists)) / len(artists), 3) if artists else 0.0,
        "duplicates": len(ids) - len(set(ids)),
        "track_count": len(tracks),
        "discovery_tracks": sum(t.role == "discovery" for t in tracks),
        "first_output_valid": result.first_output_valid,
        "revisions_used": result.revisions_used,
        "copy_valid": bool(copy_check and copy_check.passed),
        "failed_checks": result.evaluation.feedback(),
        "trace": [f"{s['step']}: {s['status']}" for s in result.trace],
        "tracks": [f"{t.song['title']} — {t.song['artist']} ({t.song['genre']}/{t.song['mood']}, "
                   f"{t.song['energy']}, {t.song.get('year')}){' ✦' if t.role == 'discovery' else ''}" for t in tracks],
    }


def summarize(rows: List[Dict]) -> Dict:
    n = len(rows)
    total_tracks = sum(r["track_count"] for r in rows)
    constrained = [r for r in rows if r["has_constraints"]] or rows
    return {
        "cases": n,
        "overall_pass_rate": round(sum(r["overall_passed"] for r in rows) / n, 3),
        "evaluator_pass_rate": round(sum(r["evaluator_passed"] for r in rows) / n, 3),
        "expectation_pass_rate": round(sum(r["expectations"]["passed"] for r in rows) / n, 3),
        "avg_relevance": round(mean(r["avg_relevance"] for r in rows), 2),
        "avg_expectation_alignment": round(mean(r["expectations"]["expectation_alignment"] for r in rows), 3),
        "avg_artist_diversity": round(mean(r["artist_diversity"] for r in rows), 3),
        "duplicate_rate": round(sum(r["duplicates"] for r in rows) / total_tracks, 3) if total_tracks else 0.0,
        "constrained_cases": len(constrained),
        "constraint_satisfaction_rate": round(
            sum(r["expectations"]["constraints_satisfied"] for r in constrained) / len(constrained), 3),
        "length_accuracy": round(sum(r["expectations"]["length_ok"] for r in rows) / n, 3),
        "first_try_valid_output_rate": round(sum(r["first_output_valid"] for r in rows) / n, 3),
        "cases_using_revision": sum(r["revisions_used"] > 0 for r in rows),
        "copy_valid_rate": round(sum(r["copy_valid"] for r in rows) / n, 3),
    }


def print_report(rows: List[Dict], summary: Dict, verbose: bool) -> None:
    header = f"{'case':<28}{'pass':<6}{'eval':<6}{'expect':<8}{'rel':>6}{'align':>7}{'div':>6}{'rev':>5}  title"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['id']:<28}{'✅' if r['overall_passed'] else '❌':<5} "
              f"{'ok' if r['evaluator_passed'] else 'fail':<6}{'ok' if r['expectations']['passed'] else 'fail':<8}"
              f"{r['avg_relevance']:>6.1f}{r['expectations']['expectation_alignment']:>7.0%}"
              f"{r['artist_diversity']:>6.0%}{r['revisions_used']:>5}  {r['title']}")
        if verbose or not r["overall_passed"]:
            for line in r["failed_checks"] + r["expectations"]["constraint_violations"]:
                print(f"{'':<4}! {line}")
        if verbose:
            print(f"{'':<4}trace: {' → '.join(r['trace'])}")
            for t in r["tracks"]:
                print(f"{'':<6}{t}")
    print("\nSummary")
    labels = {
        "overall_pass_rate": "Overall pass rate",
        "evaluator_pass_rate": "In-loop evaluator pass rate",
        "expectation_pass_rate": "Ground-truth expectation pass rate",
        "avg_relevance": "Average relevance (0-100)",
        "avg_expectation_alignment": "Avg expectation alignment",
        "avg_artist_diversity": "Avg artist diversity",
        "duplicate_rate": "Duplicate rate",
        "constraint_satisfaction_rate": "Constraint satisfaction (constrained cases)",
        "length_accuracy": "Playlist length accuracy",
        "first_try_valid_output_rate": "First-try valid LLM output",
        "cases_using_revision": "Cases that used the revision",
        "copy_valid_rate": "Copy output valid (no patch needed)",
    }
    for key, label in labels.items():
        value = summary[key]
        shown = f"{value:.0%}" if isinstance(value, float) and value <= 1 and key not in ("avg_relevance",) else value
        print(f"  {label:<46}{shown}")
    print(f"  {'Cases':<46}{summary['cases']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Soundtrack AI on predefined requests")
    parser.add_argument("--llm", default="mock", choices=("mock", "offline", "gemini"))
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-save", action="store_true", help="don't write evaluation/results.json")
    args = parser.parse_args(argv)

    rows = [run_case(case, args.llm) for case in load_cases()]
    summary = summarize(rows)
    print(f"Soundtrack AI evaluation — llm: {args.llm}\n")
    print_report(rows, summary, args.verbose)
    if not args.no_save:
        out = results_path(args.llm)
        traces = traces_path(args.llm)
        out.write_text(json.dumps({"llm": args.llm, "summary": summary,
                                   "cases": [{k: v for k, v in r.items() if k != "trace_markdown"} for r in rows]},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        header = (f"# Agent traces — evaluation run (llm: {args.llm})\n\n"
                  f"Generated by `python -m evaluation.run_evaluation --llm {args.llm}`. Each section shows the "
                  "request, the interpreted preferences, every intermediate agent step (LLM calls, "
                  "validation, repair/revision, ranking, evaluation), and the final playlist.\n\n"
                  f"**Summary:** {summary['cases']} cases · overall pass rate {summary['overall_pass_rate']:.0%} · "
                  f"avg relevance {summary['avg_relevance']} · revisions used in {summary['cases_using_revision']} cases\n\n---\n\n")
        traces.write_text(header + "\n---\n\n".join(r["trace_markdown"] for r in rows), encoding="utf-8")
        print(f"\nSaved detailed results to {out.relative_to(ROOT)} and traces to {traces.relative_to(ROOT)}")
    return 0 if summary["overall_pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
