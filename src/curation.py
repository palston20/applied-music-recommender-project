"""Ranking and curation built on the ORIGINAL weighted-score recommender.

Ranking: every song is scored by ``recommender.recommend_songs`` (and therefore
``score_song``) once per scoring profile; each song keeps its best weighted
score. No LLM is involved in ranking.

Curation turns the ranking into a playlist that holds one consistent vibe:
- applies hard user constraints (excluded genres/artists, energy bounds),
- caps songs per artist for diversity,
- reserves "discovery" slots depending on the discovery level,
- spreads discovery picks through the list (no forced intro/build/outro arc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .preferences import VibePreferences
from .recommender import recommend_songs

DISCOVERY_SHARE = {"familiar": 0.0, "balanced": 0.25, "experimental": 0.5}
# A discovery pick must still fit the mood/energy (genre-blind score, 0-100).
DISCOVERY_MIN_VIBE = 40.0
# A song in a genre the listener explicitly named jumps the queue if it scores at least this.
NAMED_GENRE_MIN = 40.0


@dataclass
class Track:
    song: Dict
    score: float
    reasons: List[str]
    profile: str
    role: str = "core"  # "core" or "discovery"
    blurb: str = ""
    vibe_score: float = 0.0  # genre-blind mood/energy score from score_song

    @property
    def relevance(self) -> float:
        """Core picks are judged on the full profile; discovery picks on vibe."""
        return self.vibe_score if self.role == "discovery" else self.score

    @property
    def id(self) -> int:
        return int(self.song["id"])

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.song["title"],
            "artist": self.song["artist"],
            "genre": self.song["genre"],
            "mood": self.song["mood"],
            "energy": self.song["energy"],
            "tempo_bpm": self.song["tempo_bpm"],
            "valence": self.song.get("valence"),
            "danceability": self.song.get("danceability"),
            "acousticness": self.song.get("acousticness"),
            "year": song_year(self.song),
            "score": round(self.score, 2),
            "vibe_score": round(self.vibe_score, 2),
            "relevance": round(self.relevance, 2),
            "role": self.role,
            "profile": self.profile,
            "reasons": self.reasons,
            "blurb": self.blurb,
        }


@dataclass
class CurationResult:
    tracks: List[Track]
    eligible_count: int
    excluded: List[Tuple[str, str]] = field(default_factory=list)  # (title, reason)
    notes: List[str] = field(default_factory=list)
    era_available: Optional[int] = None  # songs in the requested decades, if any
    named_available: Optional[int] = None  # eligible songs in explicitly named genres


def song_year(song: Dict) -> Optional[int]:
    try:
        return int(song.get("year"))
    except (TypeError, ValueError):
        return None


def in_decades(song: Dict, decades: List[int]) -> bool:
    year = song_year(song)
    return year is not None and year // 10 * 10 in decades


def rank_catalog(preferences: VibePreferences, songs: List[Dict]) -> List[Track]:
    """Rank all songs with the existing algorithm, best profile per song."""
    best: Dict[int, Track] = {}
    for label, prefs, weight in preferences.scoring_profiles():
        for song, score, explanation in recommend_songs(prefs, songs, k=len(songs)):
            weighted = score * weight
            song_id = int(song["id"])
            if song_id not in best or weighted > best[song_id].score:
                best[song_id] = Track(song, weighted, explanation.split("; "), label)
    return sorted(best.values(), key=lambda t: (-t.score, t.song["title"].casefold()))


def violates_constraints(song: Dict, preferences: VibePreferences) -> str:
    """Return a reason string if the song breaks a hard user constraint."""
    if song["genre"].lower() in preferences.exclude_genres:
        return f"genre '{song['genre']}' excluded"
    if song["artist"].lower() in {a.lower() for a in preferences.exclude_artists}:
        return f"artist '{song['artist']}' excluded"
    if preferences.min_energy is not None and song["energy"] < preferences.min_energy:
        return f"energy {song['energy']} below minimum {preferences.min_energy}"
    if preferences.max_energy is not None and song["energy"] > preferences.max_energy:
        return f"energy {song['energy']} above maximum {preferences.max_energy}"
    return ""


def discovery_slots(level: str, length: int) -> int:
    share = DISCOVERY_SHARE.get(level, 0.25)
    if share == 0 or length < 3:
        return 0
    return max(1, round(length * share))


def _discovery_ranking(preferences: VibePreferences, songs: List[Dict]) -> List[Track]:
    """Genre-blind ranking: same scorer, genre omitted, so mood+energy lead.

    This surfaces songs from genres the listener did not ask for that still
    share the requested mood/energy -- the "unexpected but fits" picks.
    """
    prefs = {"mood": preferences.mood, "energy": preferences.energy}
    if preferences.target_tempo is not None:
        prefs["tempo_bpm"] = preferences.target_tempo
    ranked = recommend_songs(prefs, songs, k=len(songs))
    return [Track(song, score, explanation.split("; "), f"discovery: {preferences.mood} energy")
            for song, score, explanation in ranked]


def _take(candidates: List[Track], count: int, chosen: List[Track],
          artist_counts: Dict[str, int], max_per_artist: int, role: str) -> None:
    chosen_ids = {t.id for t in chosen}
    for track in candidates:
        if count <= 0:
            return
        artist = track.song["artist"].lower()
        if track.id in chosen_ids or artist_counts.get(artist, 0) >= max_per_artist:
            continue
        track.role = role
        chosen.append(track)
        chosen_ids.add(track.id)
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        count -= 1


def curate(preferences: VibePreferences, songs: List[Dict]) -> CurationResult:
    """Build the playlist from the ranked catalog."""
    excluded = []
    eligible = []
    for song in songs:
        reason = violates_constraints(song, preferences)
        if reason:
            excluded.append((song["title"], reason))
        else:
            eligible.append(song)

    notes = []
    length = min(preferences.playlist_length, len(eligible))
    if length < preferences.playlist_length:
        notes.append(f"Only {len(eligible)} songs satisfy your constraints, "
                     f"so the playlist has {length} tracks instead of {preferences.playlist_length}.")

    # Era: restrict to the requested decades when the catalog has enough songs;
    # otherwise put in-era songs first and say so.
    pool, era_available, era_first = eligible, None, False
    if preferences.decades:
        label = ", ".join(f"{d}s" for d in preferences.decades)
        era_songs = [s for s in eligible if in_decades(s, preferences.decades)]
        era_available = len(era_songs)
        if era_available >= length:
            pool = era_songs
        else:
            era_first = True
            notes.append(f"Only {era_available} catalog songs are from the {label}; "
                         "the rest come from other eras.")

    ranked = rank_catalog(preferences, pool)
    if era_first:
        ranked.sort(key=lambda t: not in_decades(t.song, preferences.decades))  # stable
    # Named genres: if the listener asked for "hyperpop", hyperpop songs come
    # before related-genre songs even when a related song scores higher.
    named_available = None
    if preferences.named_genres:
        named = set(preferences.named_genres)
        named_available = sum(1 for s in pool if s["genre"].lower() in named)
        ranked.sort(key=lambda t: not (t.song["genre"].lower() in named
                                       and t.score >= NAMED_GENRE_MIN))  # stable
        if named_available < length:
            notes.append(f"The catalog has only {named_available} "
                         f"{' / '.join(preferences.named_genres)} song(s); the rest are close matches.")
    vibe_ranked = _discovery_ranking(preferences, pool)
    vibe_lookup = {t.id: t.score for t in vibe_ranked}
    for track in ranked:
        track.vibe_score = vibe_lookup.get(track.id, 0.0)
    requested_genres = set(preferences.genres)
    n_discovery = min(discovery_slots(preferences.discovery_level, length), length)
    n_core = length - n_discovery

    for max_per_artist in (1, 2, length):  # relax the artist cap only if needed
        chosen: List[Track] = []
        artist_counts: Dict[str, int] = {}
        _take(ranked, n_core, chosen, artist_counts, max_per_artist, "core")
        if n_discovery:
            core_ids = {t.id for t in chosen}
            # Order comes from the genre-blind ranking, but each pick keeps its
            # full-profile score so relevance is reported honestly.
            full = {t.id: t for t in ranked}
            discovery_pool = [full[t.id] for t in vibe_ranked
                              if t.song["genre"].lower() not in requested_genres
                              and t.id not in core_ids
                              and t.score >= DISCOVERY_MIN_VIBE]
            _take(discovery_pool, n_discovery, chosen, artist_counts, max_per_artist, "discovery")
        # Backfill from the main ranking if discovery candidates ran out.
        _take(ranked, length - len(chosen), chosen, artist_counts, max_per_artist, "core")
        if len(chosen) >= length:
            break
    if max_per_artist > 1:
        notes.append(f"Allowed up to {max_per_artist} songs per artist to reach the requested length.")

    found = sum(1 for t in chosen if t.role == "discovery")
    if found < n_discovery:
        notes.append(f"Found {found} of {n_discovery} discovery picks that still fit the vibe; "
                     "filled the rest with close matches.")
    core = [t for t in chosen if t.role == "core"]
    discoveries = [t for t in chosen if t.role == "discovery"]
    ordered = _interleave(core, discoveries)
    if preferences.unavailable:
        notes.append("Not in this catalog: " + ", ".join(preferences.unavailable)
                     + ". Closest matches were used instead.")
    return CurationResult(ordered, len(eligible), excluded, notes, era_available, named_available)


def _interleave(core: List[Track], discoveries: List[Track]) -> List[Track]:
    """Spread discovery picks evenly so the vibe stays consistent throughout."""
    if not discoveries:
        return core
    result = list(core)
    step = (len(core) + len(discoveries)) / len(discoveries)
    for index, track in enumerate(discoveries):
        position = min(len(result), int(round(step * (index + 0.5))))
        result.insert(position, track)
    return result
