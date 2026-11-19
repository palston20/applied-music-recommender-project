import json

import pytest

from src.dj_agent import AgentError, SoundtrackAgent
from src.llm_client import LLMError, MockLLMClient, template_copy

GOOD = {"mood": "chill", "genre": "lofi", "energy": 0.4, "discovery_level": "balanced",
        "playlist_length": 5, "context": "studying", "aesthetic": "cozy",
        "related_genres": ["ambient", "jazz"]}


def copy_response(prompt):
    return template_copy(prompt, title="Soft Focus", description="Calm loops.")


def agent_with(songs, **responses):
    responses.setdefault("copy", copy_response)
    llm = MockLLMClient(responses)
    return SoundtrackAgent(llm, songs=songs), llm


def test_happy_path_runs_all_steps(songs):
    agent, llm = agent_with(songs, interpret=GOOD)
    result = agent.run("cozy study session")
    assert result.passed
    assert result.title == "Soft Focus"
    assert len(result.tracks) == 5
    assert all(t.blurb for t in result.tracks)
    assert result.revisions_used == 0
    assert [c["task"] for c in llm.calls] == ["interpret", "copy"]


def test_invalid_output_uses_single_repair(songs):
    agent, llm = agent_with(songs, interpret="not json", repair=GOOD)
    result = agent.run("cozy study session")
    assert not result.first_output_valid
    assert result.revisions_used == 1
    assert result.passed
    assert [c["task"] for c in llm.calls] == ["interpret", "repair", "copy"]


def test_repair_failure_falls_back_without_a_second_revision(songs):
    agent, llm = agent_with(songs, interpret="nope", repair="still nope")
    result = agent.run("cozy study session")
    assert result.revisions_used == 1
    assert "revise" not in [c["task"] for c in llm.calls]
    assert result.tracks  # user still gets a playlist
    assert not result.passed  # but it is honestly marked as failing structured_output


# An interpretation that caps energy at 0.3 for an aggressive request shuts out
# every matching song, so the playlist fails evaluation.
BAD_ANGRY = {**GOOD, "genre": "metal", "mood": "aggressive", "energy": 0.9,
             "discovery_level": "familiar", "related_genres": [], "max_energy": 0.3}


def test_failed_playlist_triggers_one_revision(songs):
    bad = BAD_ANGRY
    good = {**GOOD, "genre": "metal", "mood": "aggressive", "energy": 0.92,
            "discovery_level": "familiar", "playlist_length": 4,
            "related_genres": ["rock", "pop"], "related_moods": ["intense"]}
    agent, llm = agent_with(songs, interpret={**bad, "playlist_length": 4}, revise=good)
    result = agent.run("angry music")
    tasks = [c["task"] for c in llm.calls]
    assert tasks.count("revise") == 1
    assert result.passed
    assert result.preferences.genre == "metal"


def test_revision_is_capped_at_one(songs):
    bad = BAD_ANGRY
    agent, llm = agent_with(songs, interpret=bad, revise=bad)
    result = agent.run("angry music")
    assert [c["task"] for c in llm.calls].count("revise") == 1
    assert not result.passed
    assert any("did not pass" in n for n in result.notes)


def test_user_overrides_beat_llm(songs):
    agent, _ = agent_with(songs, interpret=GOOD)
    result = agent.run("cozy", discovery_level="experimental", playlist_length=7)
    assert result.preferences.discovery_level == "experimental"
    assert len(result.tracks) == 7
    assert any(t.role == "discovery" for t in result.tracks)


def test_constraint_guard_enforces_missed_exclusion(songs):
    agent, _ = agent_with(songs, interpret={**GOOD, "related_genres": ["jazz", "ambient"]})
    result = agent.run("cozy study music, no jazz")
    assert "jazz" in result.preferences.exclude_genres
    assert all(t.song["genre"] != "jazz" for t in result.tracks)


def test_bad_copy_is_patched(songs):
    bad_copy = json.dumps({"title": "", "blurbs": [{"id": 999, "blurb": "?"}]})
    agent, _ = agent_with(songs, interpret=GOOD, copy=bad_copy)
    result = agent.run("cozy")
    assert result.title  # template fallback
    assert all(t.blurb for t in result.tracks)
    copy_check = next(c for c in result.evaluation.checks if c.name == "copy_output")
    assert not copy_check.passed and copy_check.severity == "warning"


def test_llm_outage_falls_back_to_offline(songs):
    class Down:
        name = "down"

        def generate(self, task, system, prompt):
            raise LLMError("503")

    result = SoundtrackAgent(Down(), songs=songs).run("rainy day daydreaming")
    assert result.tracks and result.title
    assert any("offline" in n for n in result.notes)


def test_empty_request_rejected(songs):
    agent, _ = agent_with(songs, interpret=GOOD)
    with pytest.raises(AgentError):
        agent.run("   ")


def test_named_genre_leads_the_playlist(songs):
    hyperpop = {"mood": "playful", "genre": "hyperpop", "energy": 0.85, "discovery_level": "familiar",
                "playlist_length": 5, "related_genres": ["pop", "r&b", "hip hop"],
                "named_genres": ["hyperpop"]}
    agent, _ = agent_with(songs, interpret=hyperpop)
    result = agent.run("give me a hyperpop playlist")
    assert all(t.song["genre"] == "hyperpop" for t in result.tracks)
    check = next(c for c in result.evaluation.checks if c.name == "named_genres")
    assert check.passed
