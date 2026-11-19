from evaluation.run_evaluation import load_cases, run_case, summarize


def test_harness_has_at_least_ten_cases():
    assert len(load_cases()) >= 10


def test_mocked_evaluation_meets_targets():
    rows = [run_case(case, "mock") for case in load_cases()]
    summary = summarize(rows)
    assert summary["duplicate_rate"] == 0
    assert summary["constraint_satisfaction_rate"] == 1.0
    assert summary["avg_artist_diversity"] >= 0.8
    assert summary["avg_relevance"] >= 50
    assert summary["overall_pass_rate"] == 1.0, [r["id"] for r in rows if not r["overall_passed"]]
    # The scripted failure cases really exercise the revision path.
    assert summary["cases_using_revision"] >= 3
