"""Soundtrack AI DJ agent: the multi-step workflow.

    request
      -> 1. interpret   (LLM -> JSON preferences, validated against the catalog)
      -> 2. rank        (ORIGINAL weighted scorer via recommend_songs)
      -> 3. curate      (constraints, artist cap, discovery slots, consistent vibe)
      -> 4. evaluate    (PlaylistEvaluator)
      -> 5. revise      (at most ONE controlled revision, only if a check failed)
      -> 6. write copy  (LLM title, description, per-song blurbs; validated)

The LLM interprets and writes; Python ranks. The revision budget is shared:
if the first interpretation is malformed, the single revision is spent
repairing it; otherwise it is available if the playlist fails evaluation.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

from .curation import CurationResult, Track, curate
from .evaluator import CheckResult, EvaluationReport, PlaylistEvaluator
from .llm_client import LLMError, OfflineVibeClient, template_copy
from .preferences import (DISCOVERY_LEVELS, MAX_LENGTH, MIN_LENGTH, ParseResult,
                          VibePreferences, extract_json, parse_preferences)
from .recommender import load_songs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = PROJECT_ROOT / "data" / "songs.csv"

INTERPRET_SYSTEM = """You are Soundtrack AI, a personal DJ. Translate a listener's \
natural-language vibe into structured preferences for a music recommender.
Return ONLY one JSON object with these fields:
  "mood": one catalog mood (required)
  "genre": one catalog genre (required)
  "energy": number 0.0-1.0 (0.2 sleepy, 0.5 mid, 0.9 hype) (required)
  "context": short activity/setting, e.g. "late-night drive"
  "aesthetic": short phrase for the feeling, e.g. "neon, mysterious, cinematic"
  "discovery_level": "familiar" | "balanced" | "experimental" (required)
  "playlist_length": integer 3-12, default 6 (required)
  "target_tempo": optional BPM number, or null
  "related_genres": up to 3 other catalog genres that also fit
  "related_moods": up to 2 other catalog moods that also fit
  "exclude_genres": catalog genres the listener said to avoid
  "exclude_artists": artists the listener said to avoid
  "min_energy" / "max_energy": optional hard energy bounds, or null
  "named_genres": catalog genres the listener explicitly named (e.g. "hyperpop", "R&B"); \
[] if they only described a mood or moment
  "decades": eras the listener asked for, e.g. ["2000s"] or ["90s", "2000s"]; [] if none
  "unavailable": things the listener asked for that are NOT in the catalog lists
Use ONLY genres and moods from the provided catalog lists. If the listener names \
a genre that is missing, add it to "unavailable" and choose the closest catalog \
values instead. The catalog contains real, well-known songs with release years. Only add exclusions or energy bounds the listener \
actually asked for."""

COPY_SYSTEM = """You are Soundtrack AI, a witty, warm personal DJ. Write playlist copy.
Return ONLY JSON: {"title": str, "description": str, "blurbs": [{"id": int, "blurb": str}]}
Rules: title max 6 words and evocative (not just the genre name); description 1-2 \
sentences; one blurb per track id given, max 25 words, explaining why that song fits \
the vibe. These are real songs, but rely on the facts provided (genre, mood, energy, \
tempo, year, role); do not quote lyrics or state chart/award trivia. Discovery tracks \
are intentional curveballs: say why they still fit."""


@dataclass
class PlaylistResult:
    request: str
    preferences: VibePreferences
    tracks: List[Track]
    title: str
    description: str
    evaluation: EvaluationReport
    notes: List[str] = field(default_factory=list)
    trace: List[Dict] = field(default_factory=list)
    revisions_used: int = 0
    first_output_valid: bool = True
    llm: str = ""

    @property
    def passed(self) -> bool:
        return self.evaluation.passed

    def to_dict(self) -> Dict:
        return {
            "request": self.request,
            "title": self.title,
            "description": self.description,
            "preferences": self.preferences.to_dict(),
            "tracks": [t.to_dict() for t in self.tracks],
            "evaluation": self.evaluation.to_dict(),
            "notes": self.notes,
            "trace": self.trace,
            "revisions_used": self.revisions_used,
            "first_output_valid": self.first_output_valid,
            "llm": self.llm,
        }


class AgentError(Exception):
    pass


def trace_markdown(result: "PlaylistResult", heading: str = "") -> str:
    """Render one agent run (inputs, every intermediate step, output) as markdown."""
    p = result.preferences
    lines = [f"### {heading or result.request}", "",
             f"**Request:** “{result.request}”  ",
             f"**LLM:** {result.llm} · **revisions used:** {result.revisions_used} · "
             f"**first LLM output valid:** {result.first_output_valid} · "
             f"**quality check:** {'PASS' if result.passed else 'NEEDS WORK'}", "",
             "**Interpreted preferences:** "
             + f"mood `{p.mood}`, genre `{p.genre}`"
             + (f" (+{', '.join(p.related_genres)})" if p.related_genres else "")
             + f", energy `{p.energy}`, discovery `{p.discovery_level}`, length `{p.playlist_length}`"
             + (f", named genres `{', '.join(p.named_genres)}`" if p.named_genres else "")
             + (f", decades `{', '.join(f'{d}s' for d in p.decades)}`" if p.decades else "")
             + (f", excluded `{', '.join(p.exclude_genres)}`" if p.exclude_genres else ""), "",
             "| # | step | status | detail |", "|---|---|---|---|"]
    for i, step in enumerate(result.trace, 1):
        extra = step.get("failures") or step.get("feedback") or step.get("warnings") or []
        detail = step["detail"] + "".join(f"<br>↳ {x}" for x in extra)
        lines.append(f"| {i} | {step['step']} | {step['status']} | {detail.replace('|', '/')} |")
    lines += ["", f"**Output — {result.title}:** {result.description}", ""]
    for i, t in enumerate(result.tracks, 1):
        s = t.song
        lines.append(f"{i}. {s['title']} — {s['artist']} ({s.get('year', '?')}) · {s['genre']}/{s['mood']}"
                     f"{' · ✦ discovery' if t.role == 'discovery' else ''} — {t.blurb}")
    failed = [f"`{c.name}`" for c in result.evaluation.checks if not c.passed]
    lines += ["", "**Checks failed:** " + (", ".join(failed) if failed else "none"), ""]
    for note in result.notes:
        lines.append(f"> {note}  ")
    return "\n".join(lines) + "\n"


class SoundtrackAgent:
    def __init__(self, llm, songs: Optional[List[Dict]] = None,
                 evaluator: Optional[PlaylistEvaluator] = None, max_revisions: int = 1):
        self.llm = llm
        self.songs = songs if songs is not None else load_songs(str(DEFAULT_CATALOG))
        for song in self.songs:  # load_songs leaves the optional year column as text
            if str(song.get("year", "")).strip().isdigit():
                song["year"] = int(song["year"])
        self.evaluator = evaluator or PlaylistEvaluator()
        self.max_revisions = max_revisions
        self.genres = sorted({s["genre"].lower() for s in self.songs})
        self.moods = sorted({s["mood"].lower() for s in self.songs})
        self.artists = sorted({s["artist"] for s in self.songs})

    # ------------------------------------------------------------------ #
    def run(self, request: str, discovery_level: Optional[str] = None,
            playlist_length: Optional[int] = None) -> PlaylistResult:
        request = (request or "").strip()
        if not request:
            raise AgentError("Describe a vibe, mood, or moment to build a playlist.")
        if discovery_level is not None and discovery_level not in DISCOVERY_LEVELS:
            raise AgentError(f"discovery_level must be one of {', '.join(DISCOVERY_LEVELS)}")
        if playlist_length is not None:
            playlist_length = max(MIN_LENGTH, min(MAX_LENGTH, int(playlist_length)))

        trace: List[Dict] = []
        notes: List[str] = []
        revisions_left = self.max_revisions
        overrides = {"discovery_level": discovery_level, "playlist_length": playlist_length}

        # 1. Interpret ------------------------------------------------------
        raw = self._call("interpret", INTERPRET_SYSTEM,
                         self._interpret_prompt(request, overrides), trace, notes)
        parsed = self._parse(raw, overrides)
        first_valid = parsed.valid
        self._log(trace, "interpret", "ok" if parsed.valid else "invalid",
                  self._summary(parsed.preferences) if parsed.valid else "; ".join(parsed.errors),
                  warnings=parsed.warnings)

        if not parsed.valid and revisions_left:
            revisions_left -= 1
            raw = self._call("repair", INTERPRET_SYSTEM,
                             self._repair_prompt(request, raw, parsed.errors, overrides), trace, notes)
            parsed = self._parse(raw, overrides)
            self._log(trace, "repair", "ok" if parsed.valid else "invalid",
                      self._summary(parsed.preferences) if parsed.valid else "; ".join(parsed.errors))

        if not parsed.valid:
            # Last resort: keep the valid parts, replace the rest with safe defaults.
            fallback = self._parse(raw, overrides, strict=False)
            notes.append("The DJ's interpretation was partly invalid; safe defaults were used for: "
                         + "; ".join(parsed.errors))
            parsed = ParseResult(fallback.preferences, parsed.errors,
                                 fallback.warnings, fallback.raw)
            self._log(trace, "fallback", "degraded", self._summary(parsed.preferences))

        self._apply_constraint_guard(request, parsed, trace)

        # 2-4. Rank, curate, evaluate -------------------------------------
        curation, report = self._build(parsed, trace, label="attempt 1")
        best = (parsed, curation, report)

        # 5. One controlled revision if the playlist failed a check --------
        if not report.passed and revisions_left:
            revisions_left -= 1
            raw = self._call("revise", INTERPRET_SYSTEM,
                             self._revise_prompt(request, parsed.preferences, report, overrides),
                             trace, notes)
            revised = self._parse(raw, overrides)
            if revised.valid:
                self._apply_constraint_guard(request, revised, trace)
            self._log(trace, "revise", "ok" if revised.valid else "invalid",
                      self._summary(revised.preferences) if revised.valid else "; ".join(revised.errors),
                      feedback=report.feedback())
            if revised.valid:
                curation2, report2 = self._build(revised, trace, label="attempt 2")
                if (report2.passed, report2.score) >= (report.passed, report.score):
                    best = (revised, curation2, report2)
                else:
                    self._log(trace, "select", "kept attempt 1",
                              "the revision scored lower, so the original playlist was kept")

        parsed, curation, report = best
        notes.extend(curation.notes)
        if not report.passed:
            notes.append("Heads up: this playlist did not pass every quality check: "
                         + "; ".join(report.feedback()))

        # 6. Title, description, blurbs ------------------------------------
        title, description = self._write_copy(request, parsed.preferences, curation.tracks,
                                              report, trace, notes)

        return PlaylistResult(
            request=request,
            preferences=parsed.preferences,
            tracks=curation.tracks,
            title=title,
            description=description,
            evaluation=report,
            notes=notes,
            trace=trace,
            revisions_used=self.max_revisions - revisions_left,
            first_output_valid=first_valid,
            llm=getattr(self.llm, "name", type(self.llm).__name__),
        )

    # ------------------------------------------------------------------ #
    def _build(self, parsed: ParseResult, trace: List[Dict], label: str):
        prefs = parsed.preferences
        curation = curate(prefs, self.songs)
        self._log(trace, "rank+curate", label,
                  f"{len(curation.tracks)} tracks from {curation.eligible_count} eligible songs "
                  f"({sum(t.role == 'discovery' for t in curation.tracks)} discovery)",
                  excluded=[f"{t}: {r}" for t, r in curation.excluded])
        report = self.evaluator.evaluate(curation.tracks, prefs, parsed, curation.eligible_count,
                                         curation.era_available, curation.named_available)
        self._log(trace, "evaluate", "pass" if report.passed else "fail",
                  f"{sum(c.passed for c in report.checks)}/{len(report.checks)} checks passed",
                  failures=report.feedback())
        return curation, report

    def _apply_constraint_guard(self, request: str, parsed: ParseResult, trace: List[Dict]) -> None:
        """Enforce explicit "no X" / "without X" exclusions even if the LLM missed them."""
        prefs = parsed.preferences
        text = request.lower()
        negation = r"\b(?:no|without|avoid|skip|minus|not into|hold the)\s+(?:any\s+|more\s+)?"
        added_genres = [g for g in self.genres if g not in prefs.exclude_genres
                        and re.search(negation + re.escape(g) + r"\b", text)]
        added_artists = [a for a in self.artists
                         if a.lower() not in {x.lower() for x in prefs.exclude_artists}
                         and re.search(negation + re.escape(a.lower()) + r"\b", text)]
        if not added_genres and not added_artists:
            return
        prefs.exclude_genres = [*prefs.exclude_genres, *added_genres]
        prefs.exclude_artists = [*prefs.exclude_artists, *added_artists]
        prefs.related_genres = [g for g in prefs.related_genres if g not in prefs.exclude_genres]
        if prefs.genre in prefs.exclude_genres:
            # The LLM chose a genre the listener ruled out; fall back to a related one.
            prefs.genre = prefs.related_genres.pop(0) if prefs.related_genres else \
                next(g for g in self.genres if g not in prefs.exclude_genres)
        self._log(trace, "constraint guard", "added",
                  "enforced exclusions the LLM missed: " + ", ".join(added_genres + added_artists))

    def _parse(self, raw: str, overrides: Dict, strict: bool = True) -> ParseResult:
        result = parse_preferences(raw, self.genres, self.moods, self.artists, strict=strict)
        if result.preferences is not None:
            updates = {k: v for k, v in overrides.items() if v is not None}
            if updates:
                result.preferences = replace(result.preferences, **updates)
        return result

    def _call(self, task: str, system: str, prompt: str, trace: List[Dict], notes: List[str]) -> str:
        start = time.perf_counter()
        try:
            text = self.llm.generate(task, system, prompt)
        except LLMError as error:
            self._log(trace, f"llm:{task}", "failed",
                      f"{(time.perf_counter() - start) * 1000:.0f} ms — {str(error)[:120]}")
            if task == "copy":
                raise
            notes.append(f"LLM unavailable ({error}); used the offline interpreter instead.")
            text = OfflineVibeClient().generate(task, system, prompt)
        self._log(trace, f"llm:{task}", "called", f"{(time.perf_counter() - start) * 1000:.0f} ms")
        return text

    def _write_copy(self, request, prefs, tracks, report, trace, notes):
        payload = [{"id": t.id, "title": t.song["title"], "artist": t.song["artist"],
                    "genre": t.song["genre"], "mood": t.song["mood"], "energy": t.song["energy"],
                    "tempo_bpm": t.song["tempo_bpm"], "year": t.song.get("year"),
                    "role": t.role} for t in tracks]
        prompt = (f"<request>{request}</request>\n"
                  f"Interpreted vibe: {json.dumps(self._vibe_brief(prefs))}\n"
                  f"<tracks>{json.dumps(payload)}</tracks>")
        try:
            copy = extract_json(self._call("copy", COPY_SYSTEM, prompt, trace, notes))
        except (LLMError, ValueError, json.JSONDecodeError) as error:
            copy = {}
            notes.append(f"Playlist copy fell back to templates ({error}).")

        check = self.evaluator.check_copy(copy, tracks)
        report.checks.append(check)
        fallback = json.loads(template_copy(prompt, mood=prefs.mood))
        title = copy.get("title") if isinstance(copy.get("title"), str) \
            and 0 < len(copy["title"].strip()) <= 60 else fallback["title"]
        description = copy.get("description") if isinstance(copy.get("description"), str) \
            and copy["description"].strip() else fallback["description"]
        blurbs = {}
        for item in copy.get("blurbs") or []:
            if isinstance(item, dict) and str(item.get("blurb", "")).strip():
                try:
                    blurbs[int(item.get("id"))] = str(item["blurb"]).strip()
                except (TypeError, ValueError):
                    continue
        for track in tracks:
            track.blurb = blurbs.get(track.id) or self._default_blurb(track, prefs)
        self._log(trace, "write copy", "ok" if check.passed else "patched", check.detail)
        return title.strip(), description.strip()

    # ------------------------------------------------------------------ #
    def _catalog_brief(self) -> str:
        counts = Counter((s["genre"], s["mood"]) for s in self.songs)
        lines = [f"- {g} / {m}: {n} song(s)" for (g, m), n in sorted(counts.items())]
        return "\n".join(lines)

    def _interpret_prompt(self, request: str, overrides: Dict) -> str:
        fixed = {k: v for k, v in overrides.items() if v is not None}
        return (f"Catalog genres: {', '.join(self.genres)}\n"
                f"Catalog moods: {', '.join(self.moods)}\n"
                + (f"The listener already chose: {json.dumps(fixed)}\n" if fixed else "")
                + f"<request>{request}</request>")

    def _repair_prompt(self, request, raw, errors, overrides) -> str:
        return (self._interpret_prompt(request, overrides)
                + f"\n\nYour previous JSON was rejected:\n{raw}\n"
                + "Problems:\n- " + "\n- ".join(errors)
                + "\nReturn a corrected JSON object only.")

    def _revise_prompt(self, request, prefs: VibePreferences, report: EvaluationReport, overrides) -> str:
        return (self._interpret_prompt(request, overrides)
                + f"\n\nYour previous preferences:\n{json.dumps(prefs.to_dict())}\n"
                + "The playlist built from them failed these quality checks:\n- "
                + "\n- ".join(report.feedback())
                + "\n\nCatalog songs by genre / mood (energy values):\n" + self._catalog_brief()
                + "\nRevise the preferences so the catalog can satisfy the vibe better "
                  "(e.g. a genre/mood pair that exists, closer energy, useful related genres "
                  "or moods). Keep the listener's explicit constraints. Return JSON only.")

    @staticmethod
    def _vibe_brief(prefs: VibePreferences) -> Dict:
        return {"mood": prefs.mood, "genre": prefs.genre, "energy": prefs.energy,
                "context": prefs.context, "aesthetic": prefs.aesthetic,
                "discovery_level": prefs.discovery_level}

    @staticmethod
    def _summary(prefs: Optional[VibePreferences]) -> str:
        if prefs is None:
            return "no preferences"
        extra = f" (+{', '.join(prefs.related_genres)})" if prefs.related_genres else ""
        return (f"{prefs.genre}{extra} / {prefs.mood}, energy {prefs.energy}, "
                f"{prefs.discovery_level}, {prefs.playlist_length} tracks")

    @staticmethod
    def _default_blurb(track: Track, prefs: VibePreferences) -> str:
        song = track.song
        if track.role == "discovery":
            return (f"A {song['genre']} curveball: different genre, but its {song['mood']} mood and "
                    f"{song['energy']} energy keep the {prefs.mood} vibe going.")
        return f"{song['mood'].capitalize()} {song['genre']} at {song['energy']} energy, right on your {prefs.mood} vibe."

    @staticmethod
    def _log(trace: List[Dict], step: str, status: str, detail: str, **extra) -> None:
        entry = {"step": step, "status": status, "detail": detail}
        entry.update({k: v for k, v in extra.items() if v})
        trace.append(entry)
