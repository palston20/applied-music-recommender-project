import json

from src.preferences import parse_preferences

VALID = {"mood": "chill", "genre": "lofi", "energy": 0.4, "discovery_level": "familiar",
         "playlist_length": 5, "context": "studying", "aesthetic": "cozy"}


def parse(data, genres, moods, **kw):
    text = data if isinstance(data, str) else json.dumps(data)
    return parse_preferences(text, genres, moods, **kw)


def test_valid_output_parses(genres, moods):
    result = parse(VALID, genres, moods)
    assert result.valid
    assert result.preferences.genre == "lofi"
    assert result.preferences.playlist_length == 5


def test_json_inside_code_fence_is_accepted(genres, moods):
    result = parse("```json\n" + json.dumps(VALID) + "\n```", genres, moods)
    assert result.valid


def test_non_json_is_an_error(genres, moods):
    result = parse("Sure! Here's a chill playlist.", genres, moods)
    assert not result.valid
    assert "not valid JSON" in result.errors[0]


def test_genre_outside_catalog_is_flagged_and_recorded(genres, moods):
    result = parse({**VALID, "genre": "polka"}, genres, moods)
    assert not result.valid
    assert any("polka" in e for e in result.errors)
    assert "polka" in result.preferences.unavailable


def test_missing_required_field_is_error(genres, moods):
    data = dict(VALID)
    del data["energy"]
    assert not parse(data, genres, moods).valid


def test_energy_words_and_out_of_range(genres, moods):
    assert parse({**VALID, "energy": "high"}, genres, moods).preferences.energy == 0.85
    assert not parse({**VALID, "energy": 1.7}, genres, moods).valid


def test_length_is_clamped_with_warning(genres, moods):
    result = parse({**VALID, "playlist_length": 40}, genres, moods)
    assert result.valid
    assert result.preferences.playlist_length == 12
    assert result.warnings


def test_invalid_related_entries_are_dropped_not_fatal(genres, moods):
    result = parse({**VALID, "related_genres": ["jazz", "polka"]}, genres, moods)
    assert result.valid
    assert result.preferences.related_genres == ["jazz"]


def test_non_strict_mode_recovers_with_defaults(genres, moods):
    result = parse({"mood": "euphoric", "genre": "lofi"}, genres, moods, strict=False)
    assert result.valid
    assert result.preferences.genre == "lofi"
    assert result.preferences.mood in moods
    assert result.warnings


def test_scoring_profiles_match_score_song_keys(genres, moods):
    prefs = parse({**VALID, "related_genres": ["jazz"], "related_moods": ["focused"]},
                  genres, moods).preferences
    profiles = prefs.scoring_profiles()
    assert profiles[0][1] == {"genre": "lofi", "mood": "chill", "energy": 0.4}
    assert profiles[0][2] == 1.0
    assert all(weight == 0.9 for _, _, weight in profiles[1:])
    assert len(profiles) == 3


def test_decades_are_normalized(genres, moods):
    result = parse({**VALID, "decades": ["2000s", "90s", "y2k", "the future"]}, genres, moods)
    assert result.valid
    assert result.preferences.decades == [1990, 2000]
    assert any("the future" in w for w in result.warnings)
