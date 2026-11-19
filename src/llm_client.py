"""LLM clients used by the Soundtrack AI agent.

Every client exposes the same tiny interface::

    client.generate(task: str, system: str, prompt: str) -> str

``task`` names the agent step ("interpret", "repair", "revise", "copy") so mock
clients can return scripted responses without parsing prompts. The agent is
responsible for parsing and validating whatever text comes back.

Clients:
- GeminiClient: the real LLM (Google Gemini via ``google-genai``).
- MockLLMClient: scripted responses for tests and the evaluation harness.
- OfflineVibeClient: a keyword heuristic that imitates the LLM so the CLI and
  web demo still work when no API key is configured.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Dict, List, Optional, Union

try:  # python-dotenv is optional; environment variables work without it.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - depends on the local environment
    pass


class LLMError(Exception):
    """Raised when an LLM client cannot produce a response."""


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

DEFAULT_MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest")


class GeminiClient:
    """Calls Google Gemini and asks for JSON output.

    The API key is read from ``GEMINI_API_KEY`` (or ``LLM_API_KEY``). Set
    ``GEMINI_MODEL`` to override the model; otherwise the defaults are tried in
    order so an overloaded model falls back to a lighter one.
    """

    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY")
        if not api_key:
            raise LLMError("Missing API key. Set GEMINI_API_KEY in your environment or .env file.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:  # pragma: no cover - environment dependent
            raise LLMError("google-genai is not installed. Run: pip install -r requirements.txt") from error

        self._types = types
        timeout_ms = int(float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20")) * 1000)
        self._client = genai.Client(api_key=api_key,
                                    http_options=types.HttpOptions(timeout=timeout_ms))
        env_model = os.getenv("GEMINI_MODEL")
        self.models = list(models or ([env_model] if env_model else DEFAULT_MODELS))
        self.calls = 0

    def generate(self, task: str, system: str, prompt: str) -> str:
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.9 if task == "copy" else 0.3,
        )
        last_error: Optional[Exception] = None
        for model in self.models:
            try:
                self.calls += 1
                response = self._client.models.generate_content(
                    model=model, contents=prompt, config=config
                )
                if response.text:
                    return response.text
                last_error = LLMError(f"{model} returned an empty response")
            except Exception as error:  # SDK raises several error types
                last_error = error
        raise LLMError(f"Gemini request failed for task '{task}': {last_error}")


# --------------------------------------------------------------------------- #
# Mock (tests + evaluation)
# --------------------------------------------------------------------------- #

Response = Union[str, dict, Callable[[str], str]]


class MockLLMClient:
    """Returns scripted responses per task, in order.

    ``responses`` maps a task name to a single response or a list of responses
    (consumed one per call; the last one repeats). A response can be a string,
    a dict (serialized to JSON), or a callable that receives the prompt.
    Tasks without a script fall back to ``default`` if provided.
    """

    name = "mock"

    def __init__(self, responses: Dict[str, Union[Response, List[Response]]],
                 default: Optional["MockLLMClient | OfflineVibeClient"] = None):
        self._responses = {
            task: list(value) if isinstance(value, list) else [value]
            for task, value in responses.items()
        }
        self._default = default
        self.calls: List[Dict[str, str]] = []

    def generate(self, task: str, system: str, prompt: str) -> str:
        self.calls.append({"task": task, "prompt": prompt})
        queue = self._responses.get(task)
        if not queue:
            if self._default is not None:
                return self._default.generate(task, system, prompt)
            raise LLMError(f"MockLLMClient has no scripted response for task '{task}'")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(response):
            return response(prompt)
        if isinstance(response, dict):
            return json.dumps(response)
        return response


# --------------------------------------------------------------------------- #
# Offline heuristic (demo without an API key)
# --------------------------------------------------------------------------- #

# keyword -> (field, value). Values are catalog moods. Order matters: first hit wins.
_KEYWORDS = [
    (("main character", "confident", "boss", "unstoppable", "baddie", "that girl"), "mood", "confident"),
    (("workout", "gym", "run", "hype", "pump", "intense"), "mood", "intense"),
    (("angry", "rage", "aggressive", "scream"), "mood", "aggressive"),
    (("sad", "heartbreak", "cry", "breakup", "lonely", "heartbroken"), "mood", "melancholy"),
    (("rain", "rainy", "romanticiz", "wistful", "window seat"), "mood", "wistful"),
    (("love", "romantic", "date", "crush", "slow dance", "candle"), "mood", "romantic"),
    (("study", "focus", "work", "coding", "concentrate", "reading"), "mood", "focused"),
    (("nostalgic", "nostalgia", "throwback", "memories", "y2k"), "mood", "nostalgic"),
    (("dream", "float", "ethereal", "stargaz", "space"), "mood", "dreamy"),
    (("night drive", "late-night", "late night", "mysterious", "moody", "city", "dark"), "mood", "moody"),
    (("party", "dance", "going out", "fun", "playful", "flirty"), "mood", "playful"),
    (("euphoric", "festival", "rave", "scream the lyrics", "windows down", "road trip"), "mood", "euphoric"),
    (("happy", "joy", "sunny", "good mood", "upbeat", "beach", "summer"), "mood", "happy"),
    (("coffee", "sunday", "brunch", "cook", "slow morning"), "mood", "relaxed"),
    (("chill", "calm", "relax", "unwind", "cozy", "sleep"), "mood", "chill"),
]
_GENRE_WORDS = ["hyperpop", "k-pop", "hip hop", "r&b", "pop", "indie", "rock", "metal", "electronic",
                "synthwave", "disco", "funk", "soul", "jazz", "lofi", "classical", "ambient",
                "country", "folk", "reggaeton", "afrobeats", "reggae"]
# genre -> (mood, energy) used when the request names a genre but no mood keyword
_GENRE_MOOD = {
    "hyperpop": ("playful", 0.85), "metal": ("aggressive", 0.92), "lofi": ("chill", 0.45),
    "jazz": ("relaxed", 0.3), "k-pop": ("confident", 0.75), "classical": ("focused", 0.15),
    "hip hop": ("confident", 0.7), "r&b": ("romantic", 0.5), "electronic": ("euphoric", 0.8),
    "country": ("nostalgic", 0.5), "folk": ("wistful", 0.4), "reggae": ("happy", 0.55),
    "afrobeats": ("romantic", 0.6), "reggaeton": ("playful", 0.78), "disco": ("euphoric", 0.82),
    "rock": ("intense", 0.8), "indie": ("dreamy", 0.55), "soul": ("romantic", 0.5),
    "funk": ("confident", 0.6), "ambient": ("chill", 0.08), "synthwave": ("moody", 0.65),
    "pop": ("happy", 0.75),
}
_GENRE_RELATED = {"hyperpop": ["electronic", "pop"], "metal": ["rock"], "k-pop": ["pop"],
                  "lofi": ["hip hop", "jazz"], "synthwave": ["electronic"], "afrobeats": ["reggaeton", "r&b"]}
_GENRE_ALIASES = {"hyper pop": "hyperpop", "hyper-pop": "hyperpop", "pc music": "hyperpop", "rnb": "r&b", "rap": "hip hop", "hip-hop": "hip hop", "kpop": "k-pop",
                  "edm": "electronic", "house": "electronic", "latin": "reggaeton",
                  "afrobeat": "afrobeats", "lo-fi": "lofi", "piano": "classical"}
_UNAVAILABLE_WORDS = ["punk", "gospel", "opera", "bluegrass", "polka", "drill", "dubstep", "blues"]
_MOOD_DEFAULTS = {  # mood -> (genre, energy, related genres)
    "confident": ("pop", 0.78, ["hip hop", "r&b"]),
    "happy": ("pop", 0.75, ["soul", "disco"]),
    "chill": ("lofi", 0.4, ["r&b", "hip hop"]),
    "focused": ("lofi", 0.35, ["classical", "electronic"]),
    "intense": ("hip hop", 0.85, ["rock", "electronic"]),
    "aggressive": ("metal", 0.92, ["rock"]),
    "melancholy": ("r&b", 0.45, ["folk", "pop"]),
    "romantic": ("r&b", 0.45, ["soul", "afrobeats"]),
    "nostalgic": ("pop", 0.7, ["r&b", "synthwave"]),
    "wistful": ("indie", 0.45, ["folk", "r&b"]),
    "dreamy": ("indie", 0.5, ["electronic", "synthwave"]),
    "moody": ("r&b", 0.6, ["synthwave", "indie"]),
    "playful": ("pop", 0.8, ["hip hop", "disco"]),
    "euphoric": ("pop", 0.82, ["rock", "electronic"]),
    "relaxed": ("jazz", 0.35, ["folk", "soul"]),
}
_TITLE_WORDS = {
    "confident": ("Main Character Hours", "Heads turn when this plays."),
    "happy": ("Golden Static", "Bright, easy serotonin on repeat."),
    "chill": ("Soft Focus", "Low light, slow breath, nowhere to be."),
    "focused": ("Deep Work Mode", "Steady sounds that keep the noise out."),
    "intense": ("Redline", "Full throttle, no cooldown."),
    "aggressive": ("Static Shock", "Loud, heavy, unapologetic."),
    "melancholy": ("Blue Hour Letters", "For the feelings you don't text back."),
    "romantic": ("Slow Burn", "Low lights, close talk, no rush."),
    "nostalgic": ("Rewind Club", "Warm, familiar, a little bittersweet."),
    "wistful": ("Window Seat Weather", "Raindrops on glass, a story in your head."),
    "dreamy": ("Cloud Nine Static", "Soft-focus songs for drifting."),
    "moody": ("After Hours Neon", "City lights smeared across the windshield."),
    "playful": ("Glitter Theory", "Getting ready is half the party."),
    "euphoric": ("Windows Down, Volume Up", "Songs built for screaming along."),
    "relaxed": ("Slow Sunday", "Coffee steam and nowhere to rush."),
}


def _extract_tracklist(prompt: str) -> List[dict]:
    """Pull the JSON tracklist the agent embeds in its copy prompt."""
    match = re.search(r"<tracks>\s*(.*?)\s*</tracks>", prompt, re.S)
    if not match:
        return []
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return []


def _extract_request(prompt: str) -> str:
    match = re.search(r"<request>\s*(.*?)\s*</request>", prompt, re.S)
    return match.group(1) if match else prompt


def template_copy(prompt: str, title: Optional[str] = None, description: Optional[str] = None,
                  mood: Optional[str] = None) -> str:
    """Build copy JSON for the tracks embedded in a copy prompt (used by mocks)."""
    tracks = _extract_tracklist(prompt)
    if mood is None:
        mood_match = re.search(r'"mood":\s*"([^"]+)"', prompt)
        mood = mood_match.group(1) if mood_match else "chill"
    default_title, default_desc = _TITLE_WORDS.get(mood, ("Your Soundtrack", "Built around your vibe."))
    blurbs = []
    for track in tracks:
        lean = "a left-field pick that still holds the mood" if track.get("role") == "discovery" \
            else f"{track.get('mood')} {track.get('genre')} right on the vibe"
        blurbs.append({"id": track.get("id"),
                       "blurb": f"{track.get('title')} brings {lean} (energy {track.get('energy')})."})
    return json.dumps({"title": title or default_title,
                       "description": description or default_desc,
                       "blurbs": blurbs})


class OfflineVibeClient:
    """Keyword-based stand-in for the LLM. Deterministic and free to run."""

    name = "offline"

    def generate(self, task: str, system: str, prompt: str) -> str:
        if task == "copy":
            return template_copy(prompt)
        full_request = _extract_request(prompt).lower()
        # Drop negated phrases ("nothing upbeat", "no country") before matching
        # positive keywords, so a negation never selects the thing it rules out.
        request = re.sub(r"\b(?:no|not|nothing|without|avoid|skip)\s+(?:too\s+|any\s+)?[\w&]+(?:\s+[\w&]+)?",
                         " ", full_request)
        mood = None
        # Ignore genre names while matching moods ("hyperpop" must not read as "hype").
        mood_text = request
        for alias, canonical in _GENRE_ALIASES.items():
            mood_text = re.sub(rf"\b{re.escape(alias)}\b", canonical, mood_text)
        for word in _GENRE_WORDS:
            mood_text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", " ", mood_text)
        for words, _, value in _KEYWORDS:
            if any(re.search(rf"\b{re.escape(word)}", mood_text) for word in words):
                mood = value
                break
        mood_from_keyword = mood is not None
        mood = mood or "chill"
        genre, energy, related = _MOOD_DEFAULTS[mood]
        for alias, canonical in _GENRE_ALIASES.items():
            request = re.sub(rf"\b{re.escape(alias)}\b", canonical, request)
        named = [w for w in _GENRE_WORDS if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", request)]
        if named:
            related = list(dict.fromkeys(g for g in [*named[1:], genre, *related] if g != named[0]))[:3]
            genre = named[0]
            if not mood_from_keyword and genre in _GENRE_MOOD:
                mood, energy = _GENRE_MOOD[genre]
                related = [g for g in [*named[1:], *_GENRE_RELATED.get(genre, _MOOD_DEFAULTS[mood][2])]
                           if g != genre][:3]
        unavailable = [w for w in _UNAVAILABLE_WORDS if re.search(rf"\b{w}\b", request)]
        decades = sorted({int(m) // 10 * 10 if len(m) == 4 else (1900 if int(m) >= 30 else 2000) + int(m) // 10 * 10
                          for m in re.findall(r"\b'?(\d{2}|\d{4})s\b", request)})
        if "y2k" in request and 2000 not in decades:
            decades.append(2000)
        if any(w in request for w in ("high energy", "hype", "loud")):
            energy = max(energy, 0.85)
        if any(w in request for w in ("low energy", "quiet", "soft", "sleep")):
            energy = min(energy, 0.35)
        max_energy = 0.5 if re.search(r"\b(?:nothing|no|not)\s+(?:upbeat|hype|loud)", full_request) else None
        if max_energy is not None:
            energy = min(energy, max_energy)
        discovery = "balanced"
        if any(w in request for w in ("new", "surprise", "experimental", "discover", "unexpected")):
            discovery = "experimental"
        if any(w in request for w in ("familiar", "favorites", "safe")):
            discovery = "familiar"
        length_match = re.search(r"\b(\d{1,2})\s*(songs|tracks)\b", request)
        exclude = [g for g in _GENRE_WORDS
                   if re.search(rf"\b(?:no|without|avoid|skip) {re.escape(g)}\b", full_request)]
        return json.dumps({
            "mood": mood,
            "genre": genre,
            "energy": energy,
            "context": "general listening",
            "aesthetic": mood,
            "discovery_level": discovery,
            "playlist_length": int(length_match.group(1)) if length_match else 6,
            "related_genres": [g for g in related if g not in exclude],
            "related_moods": [],
            "exclude_genres": exclude,
            "unavailable": unavailable,
            "decades": decades,
            "named_genres": named,
            "max_energy": max_energy,
        })


def make_llm(mode: str = "auto"):
    """Build an LLM client. ``auto`` uses Gemini when a key is set, else offline."""
    mode = (mode or "auto").lower()
    if mode == "offline":
        return OfflineVibeClient()
    if mode == "gemini":
        return GeminiClient()
    if mode == "auto":
        if os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY"):
            try:
                return GeminiClient()
            except LLMError:
                pass
        return OfflineVibeClient()
    raise ValueError(f"Unknown LLM mode '{mode}' (use auto, gemini, or offline)")
