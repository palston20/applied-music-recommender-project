from src.curation import curate, rank_catalog
from src.evaluator import PlaylistEvaluator
from src.preferences import VibePreferences
from src.recommender import recommend_songs


def prefs(**kw):
    base = dict(mood="chill", genre="lofi", energy=0.4, discovery_level="familiar", playlist_length=5)
    base.update(kw)
    return VibePreferences(**base)


def test_ranking_uses_original_scores(songs):
    p = prefs()
    ranked = rank_catalog(p, songs)
    original = recommend_songs({"genre": "lofi", "mood": "chill", "energy": 0.4}, songs, k=1)[0]
    assert ranked[0].song["title"] == original[0]["title"]
    assert ranked[0].score == original[1]


def test_curation_has_no_duplicates_and_caps_artists(songs):
    tracks = curate(prefs(playlist_length=8), songs).tracks
    ids = [t.id for t in tracks]
    artists = [t.song["artist"] for t in tracks]
    assert len(ids) == len(set(ids)) == 8
    assert len(artists) == len(set(artists))


def test_constraints_are_hard_filters(songs):
    p = prefs(genre="rock", mood="intense", energy=0.9, exclude_genres=["metal"], min_energy=0.7)
    result = curate(p, songs)
    assert all(t.song["genre"] != "metal" and t.song["energy"] >= 0.7 for t in result.tracks)
    assert any("metal" in reason for _, reason in result.excluded)


def test_length_shrinks_when_constraints_leave_too_few_songs(songs):
    result = curate(prefs(playlist_length=10, max_energy=0.15), songs)
    assert len(result.tracks) == result.eligible_count < 10
    assert result.notes


def test_familiar_has_no_discovery_and_experimental_does(songs):
    familiar = curate(prefs(discovery_level="familiar", playlist_length=6), songs).tracks
    experimental = curate(prefs(discovery_level="experimental", playlist_length=6), songs).tracks
    assert all(t.role == "core" for t in familiar)
    discovery = [t for t in experimental if t.role == "discovery"]
    assert discovery
    assert all(t.song["genre"] not in ("lofi",) for t in discovery)


def test_evaluator_passes_a_good_playlist(songs):
    p = prefs(related_genres=["ambient", "jazz"])
    report = PlaylistEvaluator().evaluate(curate(p, songs).tracks, p)
    assert report.passed, report.feedback()


def test_evaluator_flags_duplicates_and_diversity(songs):
    p = prefs()
    tracks = curate(p, songs).tracks
    bad = tracks + [tracks[0]]
    report = PlaylistEvaluator().evaluate(bad, p)
    failed = {c.name for c in report.failures}
    assert {"duplicates", "length"} <= failed


def test_evaluator_flags_irrelevant_playlist(songs):
    p = prefs()
    metal = prefs(genre="metal", mood="aggressive", energy=0.95)
    tracks = curate(metal, songs).tracks
    report = PlaylistEvaluator().evaluate(tracks, p)
    failed = {c.name for c in report.failures}
    assert "relevance" in failed and "genre_mood_alignment" in failed


def test_evaluator_flags_constraint_violation(songs):
    p = prefs(genre="metal", mood="aggressive", energy=0.95, discovery_level="familiar")
    tracks = curate(p, songs).tracks
    constrained = prefs(genre="metal", mood="aggressive", energy=0.95, exclude_genres=["metal"])
    report = PlaylistEvaluator().evaluate(tracks, constrained)
    assert "constraints" in {c.name for c in report.failures}


def test_copy_check_rejects_unknown_track_ids(songs):
    tracks = curate(prefs(), songs).tracks
    good = {"title": "Soft Focus", "description": "Calm.",
            "blurbs": [{"id": t.id, "blurb": "fits"} for t in tracks]}
    bad = {**good, "blurbs": good["blurbs"] + [{"id": 999, "blurb": "invented"}]}
    assert PlaylistEvaluator.check_copy(good, tracks).passed
    assert not PlaylistEvaluator.check_copy(bad, tracks).passed


def test_catalog_is_real_songs_with_years(songs):
    assert len(songs) >= 100
    assert all(str(s["year"]).isdigit() for s in songs)


def test_era_request_restricts_to_decade(songs):
    for s in songs:
        s["year"] = int(s["year"])
    p = prefs(genre="r&b", mood="romantic", energy=0.55, related_genres=["pop"],
              discovery_level="balanced", playlist_length=6, decades=[2000])
    result = curate(p, songs)
    assert all(2000 <= t.song["year"] <= 2009 for t in result.tracks)
    report = PlaylistEvaluator().evaluate(result.tracks, p, era_available=result.era_available)
    assert next(c for c in report.checks if c.name == "era").passed


def test_era_with_too_few_songs_is_noted(songs):
    p = prefs(genre="jazz", mood="relaxed", energy=0.3, playlist_length=6, decades=[1880])
    result = curate(p, songs)
    assert result.era_available < 6
    assert any("1880s" in n for n in result.notes)


def test_named_genre_check_catches_wrong_genre(songs):
    """Without named-genre priority, related genres crowd out the one the user asked for."""
    loose = prefs(genre="hyperpop", mood="playful", energy=0.85, playlist_length=5,
                  related_genres=["pop", "r&b", "hip hop"])
    strict = prefs(genre="hyperpop", mood="playful", energy=0.85, playlist_length=5,
                   related_genres=["pop", "r&b", "hip hop"], named_genres=["hyperpop"])
    loose_tracks = curate(loose, songs).tracks
    report = PlaylistEvaluator().evaluate(loose_tracks, strict)
    assert "named_genres" in {c.name for c in report.failures}
    strict_tracks = curate(strict, songs).tracks
    assert sum(t.song["genre"] == "hyperpop" for t in strict_tracks) >= 4
