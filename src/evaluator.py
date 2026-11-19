"""PlaylistEvaluator: reliability checks run before a playlist is returned.

Each check produces a ``CheckResult``. Checks with severity "error" decide
pass/fail (and can trigger the agent's single revision); "warning" checks are
reported but never block the playlist.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Dict, List, Optional

from .curation import Track, in_decades, violates_constraints
from .preferences import ParseResult, VibePreferences
from .recommender import score_song

# Minimum average relevance (0-100) by discovery level. Experimental
# playlists intentionally trade some relevance for surprise.
RELEVANCE_THRESHOLDS = {"familiar": 55.0, "balanced": 50.0, "experimental": 45.0}
MIN_ALIGNMENT = 0.6        # share of core tracks matching a requested genre or mood
MIN_ARTIST_DIVERSITY = 0.8  # unique artists / tracks
MAX_ENERGY_DRIFT = 0.3      # max |track energy - target| for a track to count as on-vibe
MIN_ENERGY_CONSISTENCY = 0.7


@dataclass
class CheckResult:
    name: str
    passed: bool
    severity: str  # "error" | "warning"
    detail: str
    value: Optional[float] = None


@dataclass
class EvaluationReport:
    checks: List[CheckResult] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def score(self) -> float:
        """Share of all checks passed; used to compare attempts."""
        return sum(c.passed for c in self.checks) / len(self.checks) if self.checks else 0.0

    def feedback(self) -> List[str]:
        return [f"{c.name}: {c.detail}" for c in self.failures]

    def to_dict(self) -> Dict:
        return {"passed": self.passed, "score": round(self.score, 3),
                "checks": [asdict(c) for c in self.checks], "metrics": self.metrics}


def track_relevance(track: Track, preferences: VibePreferences) -> float:
    """Recompute relevance with the original scorer (independent of curation).

    Core tracks: best weighted score across the preference profiles.
    Discovery tracks: genre-blind mood + energy score, since they are meant to
    come from other genres.
    """
    if track.role == "discovery":
        prefs = {"mood": preferences.mood, "energy": preferences.energy}
        if preferences.target_tempo is not None:
            prefs["tempo_bpm"] = preferences.target_tempo
        return score_song(prefs, track.song)[0]
    return max(score_song(p, track.song)[0] * w for _, p, w in preferences.scoring_profiles())


class PlaylistEvaluator:
    def evaluate(self, tracks: List[Track], preferences: VibePreferences,
                 parse_result: Optional[ParseResult] = None,
                 eligible_count: Optional[int] = None,
                 era_available: Optional[int] = None,
                 named_available: Optional[int] = None) -> EvaluationReport:
        report = EvaluationReport()
        add = report.checks.append

        # 1. Validity of the structured LLM output
        if parse_result is not None:
            ok = parse_result.valid
            detail = "LLM preferences matched the schema and catalog vocabulary" if ok \
                else "; ".join(parse_result.errors or ["preferences could not be parsed"])
            add(CheckResult("structured_output", ok, "error", detail))

        n = len(tracks)
        ids = [t.id for t in tracks]
        keys = [(t.song["title"].casefold(), t.song["artist"].casefold()) for t in tracks]

        # 2. Playlist length
        expected = preferences.playlist_length
        if eligible_count is not None:
            expected = min(expected, eligible_count)
        add(CheckResult("length", n == expected and n > 0, "error",
                        f"{n} tracks (expected {expected}"
                        + (f"; {preferences.playlist_length} requested" if expected != preferences.playlist_length else "")
                        + ")", float(n)))

        # 3. Duplicates
        duplicates = (n - len(set(ids))) + (len(set(ids)) - len(set(keys)))
        duplicate_rate = duplicates / n if n else 0.0
        add(CheckResult("duplicates", duplicates == 0, "error",
                        f"{duplicates} duplicate track(s)", duplicate_rate))

        # 4. Artist diversity
        artists = [t.song["artist"].casefold() for t in tracks]
        diversity = len(set(artists)) / n if n else 0.0
        add(CheckResult("artist_diversity", diversity >= MIN_ARTIST_DIVERSITY, "error",
                        f"{len(set(artists))} unique artists across {n} tracks ({diversity:.0%})",
                        diversity))

        # 5. Relevance to the requested vibe (existing weighted scores)
        relevance = mean(track_relevance(t, preferences) for t in tracks) if tracks else 0.0
        threshold = RELEVANCE_THRESHOLDS.get(preferences.discovery_level, 50.0)
        add(CheckResult("relevance", relevance >= threshold, "error",
                        f"average relevance {relevance:.1f} (minimum {threshold:.0f} for "
                        f"{preferences.discovery_level})", relevance))

        # 6. Genre and mood alignment (core tracks)
        core = [t for t in tracks if t.role == "core"] or tracks
        genres, moods = set(preferences.genres), set(preferences.moods)
        aligned = [t for t in core if t.song["genre"].lower() in genres or t.song["mood"].lower() in moods]
        alignment = len(aligned) / len(core) if core else 0.0
        add(CheckResult("genre_mood_alignment", alignment >= MIN_ALIGNMENT, "error",
                        f"{len(aligned)}/{len(core)} core tracks match a requested genre or mood",
                        alignment))

        # 7. Vibe consistency: energy stays near the target (no forced arc)
        on_vibe = [t for t in tracks if abs(t.song["energy"] - preferences.energy) <= MAX_ENERGY_DRIFT]
        consistency = len(on_vibe) / n if n else 0.0
        add(CheckResult("energy_consistency", consistency >= MIN_ENERGY_CONSISTENCY, "warning",
                        f"{len(on_vibe)}/{n} tracks within ±{MAX_ENERGY_DRIFT} of target energy "
                        f"{preferences.energy}", consistency))

        # 8. User constraints (hard)
        violations = [f"{t.song['title']} ({reason})" for t in tracks
                      if (reason := violates_constraints(t.song, preferences))]
        add(CheckResult("constraints", not violations, "error",
                        "all user constraints satisfied" if not violations
                        else "violations: " + ", ".join(violations),
                        1.0 if not violations else 0.0))

        # 9. Named genres: did the listener get the genre they actually asked for?
        if preferences.named_genres:
            named = set(preferences.named_genres)
            hits = sum(t.song["genre"].lower() in named for t in core)
            share = hits / len(core) if core else 0.0
            enough = named_available is None or named_available >= len(core)
            add(CheckResult("named_genres", share >= MIN_ALIGNMENT if enough else hits >= min(len(core), named_available or 0) - 1,
                            "error" if enough else "warning",
                            f"{hits}/{len(core)} core tracks are {' / '.join(preferences.named_genres)}"
                            + ("" if enough else f" (catalog only has {named_available})"), share))

        # 10. Era (only when the listener asked for specific decades)
        era_share = None
        if preferences.decades:
            in_era = sum(in_decades(t.song, preferences.decades) for t in tracks)
            era_share = in_era / n if n else 0.0
            enough = era_available is None or era_available >= n
            label = ", ".join(f"{d}s" for d in preferences.decades)
            add(CheckResult("era", era_share >= MIN_ALIGNMENT if enough else in_era == min(n, era_available or 0),
                            "error" if enough else "warning",
                            f"{in_era}/{n} tracks from the {label}"
                            + ("" if enough else f" (catalog only has {era_available})"), era_share))

        report.metrics = {
            "track_count": n,
            "avg_relevance": round(relevance, 2),
            "artist_diversity": round(diversity, 3),
            "duplicate_rate": round(duplicate_rate, 3),
            "genre_mood_alignment": round(alignment, 3),
            "energy_consistency": round(consistency, 3),
            "constraints_satisfied": not violations,
            "era_share": None if era_share is None else round(era_share, 3),
        }
        return report

    @staticmethod
    def check_copy(copy: Dict, tracks: List[Track]) -> CheckResult:
        """Validate the LLM's title/description/blurbs output."""
        problems = []
        if not isinstance(copy, dict):
            return CheckResult("copy_output", False, "warning", "copy is not a JSON object")
        title = copy.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 60:
            problems.append("title missing or longer than 60 characters")
        if not isinstance(copy.get("description"), str) or not copy["description"].strip():
            problems.append("description missing")
        blurbs = copy.get("blurbs")
        valid_ids = {t.id for t in tracks}
        if not isinstance(blurbs, list):
            problems.append("blurbs missing")
        else:
            seen = set()
            for blurb in blurbs:
                bid = blurb.get("id") if isinstance(blurb, dict) else None
                try:
                    bid = int(bid)
                except (TypeError, ValueError):
                    bid = None
                if bid not in valid_ids:
                    problems.append(f"blurb references unknown track {blurb!r:.60}")
                elif not str(blurb.get("blurb", "")).strip():
                    problems.append(f"empty blurb for track {bid}")
                else:
                    seen.add(bid)
            missing = valid_ids - seen
            if missing:
                problems.append(f"no blurb for {len(missing)} track(s)")
        return CheckResult("copy_output", not problems, "warning",
                           "title, description and blurbs are grounded in the playlist"
                           if not problems else "; ".join(problems))
