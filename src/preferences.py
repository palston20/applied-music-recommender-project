"""Structured vibe preferences and validation of the LLM's JSON output.

The LLM turns a natural-language request into JSON. Nothing from the LLM is
trusted until ``parse_preferences`` has checked it against the catalog's real
genres and moods, so the existing weighted scorer only ever receives values it
can actually match.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

DISCOVERY_LEVELS = ("familiar", "balanced", "experimental")
ENERGY_WORDS = {"very low": 0.15, "low": 0.3, "medium": 0.55, "moderate": 0.55,
                "high": 0.85, "very high": 0.95}
MIN_LENGTH, MAX_LENGTH, DEFAULT_LENGTH = 3, 12, 6
REQUIRED_FIELDS = ("mood", "genre", "energy", "discovery_level", "playlist_length")


@dataclass
class VibePreferences:
    """What the agent hands to the recommender, after validation."""

    mood: str
    genre: str
    energy: float
    context: str = ""
    aesthetic: str = ""
    discovery_level: str = "balanced"
    playlist_length: int = DEFAULT_LENGTH
    target_tempo: Optional[float] = None
    related_genres: List[str] = field(default_factory=list)
    related_moods: List[str] = field(default_factory=list)
    exclude_genres: List[str] = field(default_factory=list)
    exclude_artists: List[str] = field(default_factory=list)
    min_energy: Optional[float] = None
    max_energy: Optional[float] = None
    unavailable: List[str] = field(default_factory=list)
    decades: List[int] = field(default_factory=list)  # e.g. [2000] for "2000s"
    named_genres: List[str] = field(default_factory=list)  # genres the listener explicitly asked for

    @property
    def genres(self) -> List[str]:
        return _unique([self.genre, *self.related_genres])

    @property
    def moods(self) -> List[str]:
        return _unique([self.mood, *self.related_moods])

    def scoring_profiles(self) -> List[Tuple[str, Dict, float]]:
        """Preference dicts in the exact format ``score_song`` expects.

        The primary (genre, mood) pair has full weight. Each related genre or
        mood becomes an alternate profile at 0.9 weight so a blended vibe
        ("2000s R&B and pop") can be scored without changing the algorithm.
        """
        base = {"energy": self.energy}
        if self.target_tempo is not None:
            base["tempo_bpm"] = self.target_tempo
        profiles = [(f"{self.genre} + {self.mood}",
                     {**base, "genre": self.genre, "mood": self.mood}, 1.0)]
        for genre in self.related_genres:
            if genre != self.genre:
                profiles.append((f"{genre} + {self.mood}",
                                 {**base, "genre": genre, "mood": self.mood}, 0.9))
        for mood in self.related_moods:
            if mood != self.mood:
                profiles.append((f"{self.genre} + {mood}",
                                 {**base, "genre": self.genre, "mood": mood}, 0.9))
        return profiles

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ParseResult:
    preferences: Optional[VibePreferences]
    errors: List[str]
    warnings: List[str]
    raw: Optional[Dict] = None

    @property
    def valid(self) -> bool:
        return self.preferences is not None and not self.errors


def _unique(values: Iterable[str]) -> List[str]:
    seen, result = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_json(text: str) -> Dict:
    """Parse a JSON object from LLM text, tolerating ```json fences."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _as_energy(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ENERGY_WORDS:
            return ENERGY_WORDS[text]
        try:
            return float(text)
        except ValueError:
            return None
    return None


def parse_decade(value) -> Optional[int]:
    """'2000s' / '90s' / 'y2k' / 1994 -> starting year of the decade (2000, 1990, ...)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        year = int(value)
    else:
        text = str(value).strip().lower().replace("'", "").replace("’", "")
        if text in ("y2k", "noughties", "aughts"):
            return 2000
        match = re.fullmatch(r"(\d{2}|\d{4})s?", text)
        if not match:
            return None
        year = int(match.group(1))
        if year < 100:
            year += 1900 if year >= 30 else 2000
    if not 1900 <= year <= 2029:
        return None
    return year // 10 * 10


def _string_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def parse_preferences(
    raw_text: str,
    genres: Iterable[str],
    moods: Iterable[str],
    artists: Iterable[str] = (),
    strict: bool = True,
) -> ParseResult:
    """Validate the LLM's JSON against the schema and catalog vocabulary.

    ``strict=True`` reports problems with required fields as errors (which
    trigger the agent's revision step). ``strict=False`` is the last-resort
    fallback: invalid values are replaced with safe defaults and downgraded to
    warnings so the user still gets a playlist.
    """
    genres = {g.lower() for g in genres}
    moods = {m.lower() for m in moods}
    artist_lookup = {a.lower(): a for a in artists}
    errors: List[str] = []
    warnings: List[str] = []

    def problem(message: str) -> None:
        (errors if strict else warnings).append(message)

    try:
        data = extract_json(raw_text)
    except (ValueError, json.JSONDecodeError) as error:
        if strict:
            return ParseResult(None, [f"LLM output is not valid JSON: {error}"], [])
        data = {}
        warnings.append(f"LLM output is not valid JSON ({error}); using defaults")

    for name in REQUIRED_FIELDS:
        if name not in data:
            problem(f"missing required field '{name}'")

    unavailable = _string_list(data.get("unavailable"))

    mood = str(data.get("mood", "")).strip().lower()
    if mood not in moods:
        if "mood" in data:
            problem(f"mood '{mood}' is not in the catalog moods")
        mood = "chill"

    genre = str(data.get("genre", "")).strip().lower()
    if genre not in genres:
        if "genre" in data:
            problem(f"genre '{genre}' is not in the catalog genres")
            if genre and genre not in unavailable:
                unavailable.append(genre)
        genre = "pop"

    energy = _as_energy(data.get("energy"))
    if energy is None or not 0.0 <= energy <= 1.0:
        if "energy" in data:
            problem(f"energy {data.get('energy')!r} must be a number from 0 to 1")
        energy = 0.55

    discovery = str(data.get("discovery_level", "balanced")).strip().lower()
    if discovery not in DISCOVERY_LEVELS:
        problem(f"discovery_level '{discovery}' must be one of {', '.join(DISCOVERY_LEVELS)}")
        discovery = "balanced"

    length = data.get("playlist_length", DEFAULT_LENGTH)
    if isinstance(length, bool) or not isinstance(length, (int, float, str)) \
            or not str(length).strip().isdigit():
        problem(f"playlist_length {length!r} must be a whole number")
        length = DEFAULT_LENGTH
    length = int(length)
    if not MIN_LENGTH <= length <= MAX_LENGTH:
        warnings.append(f"playlist_length {length} clamped to {MIN_LENGTH}-{MAX_LENGTH}")
        length = max(MIN_LENGTH, min(MAX_LENGTH, length))

    tempo = data.get("target_tempo")
    if tempo is not None:
        try:
            tempo = float(tempo)
            if not 40 <= tempo <= 220:
                raise ValueError
        except (TypeError, ValueError):
            warnings.append(f"ignored target_tempo {data.get('target_tempo')!r}")
            tempo = None

    def vocab_list(key: str, vocab: set) -> List[str]:
        values = _string_list(data.get(key))
        kept = [v for v in values if v in vocab]
        for dropped in (v for v in values if v not in vocab):
            warnings.append(f"dropped {key} entry '{dropped}' (not in catalog)")
            if key == "related_genres" and dropped not in unavailable:
                unavailable.append(dropped)
        return _unique(kept)

    related_genres = vocab_list("related_genres", genres)
    related_moods = vocab_list("related_moods", moods)
    exclude_genres = vocab_list("exclude_genres", genres)
    named_genres = vocab_list("named_genres", genres)
    exclude_artists = [artist_lookup.get(a, a) for a in _string_list(data.get("exclude_artists"))]

    decades = []
    raw_decades = data.get("decades") or []
    if not isinstance(raw_decades, list):
        raw_decades = [raw_decades]
    for item in raw_decades:
        decade = parse_decade(item)
        if decade is None:
            warnings.append(f"ignored decade {item!r}")
        elif decade not in decades:
            decades.append(decade)

    bounds = {}
    for key in ("min_energy", "max_energy"):
        value = _as_energy(data.get(key)) if data.get(key) is not None else None
        if value is not None and not 0.0 <= value <= 1.0:
            warnings.append(f"ignored {key} {value} (outside 0-1)")
            value = None
        bounds[key] = value
    if bounds["min_energy"] is not None and bounds["max_energy"] is not None \
            and bounds["min_energy"] > bounds["max_energy"]:
        problem("min_energy is greater than max_energy")
        bounds = {"min_energy": None, "max_energy": None}

    if genre in exclude_genres:
        problem(f"genre '{genre}' is also listed in exclude_genres")
        exclude_genres = [g for g in exclude_genres if g != genre]
    related_genres = [g for g in related_genres if g not in exclude_genres]

    preferences = VibePreferences(
        mood=mood,
        genre=genre,
        energy=round(energy, 3),
        context=str(data.get("context", "")).strip(),
        aesthetic=str(data.get("aesthetic", "")).strip(),
        discovery_level=discovery,
        playlist_length=length,
        target_tempo=tempo,
        related_genres=related_genres,
        related_moods=related_moods,
        exclude_genres=exclude_genres,
        exclude_artists=exclude_artists,
        min_energy=bounds["min_energy"],
        max_energy=bounds["max_energy"],
        unavailable=_unique(unavailable),
        decades=sorted(decades),
        named_genres=[g for g in named_genres if g not in exclude_genres],
    )
    return ParseResult(preferences, errors, warnings, raw=data)
