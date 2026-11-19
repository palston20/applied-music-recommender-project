# 🎧 Model Card: Soundtrack AI (built on VibeMatch 1.0)

## 1. Model Name

**Soundtrack AI 2.0**: an AI DJ that turns natural-language vibes into playlists. It extends **VibeMatch 1.0**, the original weighted-score music recommender, which still does all of the ranking.

---

## 2. Intended Use

Soundtrack AI is a classroom project for exploring how an LLM can coordinate a transparent, rule-based recommender. A user describes a mood, activity, aesthetic, or feeling. The system returns a short playlist from a 171-song catalog of real songs, with a title, a description, a reason for each song, and a quality report.

It is **not** meant for real listeners at scale, for decisions about people, or as a replacement for a streaming service. It assumes that a few labels (genre, mood, energy) can approximate a vibe.

---

## 3. How the Model Works

**Two parts with separate jobs:**

1. **LLM (Google Gemini).** It reads the request and returns JSON preferences: mood, genre, energy (0–1), context, aesthetic, discovery level, playlist length, related genres and moods, exclusions, and anything the catalog lacks. In a later step it writes the playlist title, description, and one blurb per song. Every LLM output is validated before it is used.
2. **The original weighted scorer (VibeMatch).** Genre has weight 3 (exact match), mood 2 (exact match), energy 2 (full credit at the target, zero credit at ±0.5), and optional tempo 1 (±40 BPM). The weighted average is scaled to 0–100. This code is unchanged.

**What the agent adds around the scorer:**
- It scores each song against several profiles (the primary genre and mood, plus related ones at 0.9×) and keeps the best score.
- It applies hard constraints before ranking: excluded genres and artists, and energy bounds.
- It filters by era when the user names decades (e.g. "2000s"), as long as the catalog has enough songs from those decades.
- It allows one song per artist.
- It adds discovery picks: songs from genres the user didn't name that still match the mood and energy. They make up 0%, about 25%, or about 50% of the playlist for familiar, balanced, or experimental.
- It runs a PlaylistEvaluator. If the playlist fails, the agent makes **one** controlled revision.

---

## 4. Data

The catalog is **171 real songs** released between 1888 and 2024, covering **22 genres** and **15 moods**. The 2000s are the best-covered decade (44 songs), followed by the 2010s (53). Columns are the original ones plus `year`.

- **Factual:** title, artist, and release year.
- **Hand-assigned:** one genre and one mood per song.
- **Hand-estimated:** energy, tempo, valence, danceability, and acousticness, on Spotify-style scales. They are *not* official audio-analysis values, because Spotify's audio-features API is closed to new apps.

The original 18 fictional songs are preserved in `data/songs_original.csv`. Genres with only 3 songs (lofi, ambient, funk) and styles with none (punk, gospel, opera, regional and non-English music beyond K-pop, reggaeton, and afrobeats) are the main gaps.

---

## 5. Strengths

- **Transparent ranking.** Every song shows the original scorer's reasons ("matches your preferred mood (chill); energy is 0.02 from your target"). The LLM never ranks songs.
- **Natural-language input.** Abstract requests like "main character energy" or "romanticizing my life on a rainy day" become concrete, inspectable preferences.
- **Guardrails around the LLM.** Schema and vocabulary validation, a repair prompt for malformed output, a deterministic guard for "no X" exclusions, grounding checks on the generated copy, and an offline fallback if the API is down.
- **Honest output.** When the catalog can't satisfy a request, the playlist carries notes and a "needs work" badge instead of claiming success.

---

## 6. Limitations and Bias

- **Catalog bias.** The 162 songs were chosen as widely known hits. That skews toward English-language, chart-famous music from the 2000s–2020s, and toward what one curator considers iconic. Listeners whose taste lives outside that canon get approximations.
- **Estimated features.** Energy and the other numeric values are human judgments, so two curators would score the same song differently. These numbers drive the ranking.
- **Exact label matching.** Genre and mood still need exact matches. "Indie pop" earns no genre credit when the profile asks for "pop" unless the LLM lists it as a related genre.
- **Genre outweighs mood** (3 vs 2), so a genre match can beat a better mood match. The edge case from VibeMatch, where a non-melancholy rock song beats the only melancholy song, still applies to single-profile requests.
- **LLM interpretation bias.** The model maps phrases like "main character," "confident," or "mysterious" to moods based on its training, which may reflect particular cultural associations. Two users with the same words may mean different things.
- **Unused features.** Valence, danceability, and acousticness are not used in scoring.

---

## 7. Evaluation

**Automated harness:** `python -m evaluation.run_evaluation` runs 16 predefined requests with mocked LLM responses. Each case has hand-labeled expectations that don't depend on the LLM: acceptable genres and moods, an energy range, the length, constraints, and decades.

| Metric (mocked LLM) | Result |
|---|---|
| Overall pass rate | 16/16 |
| Average relevance (0–100) | 89.9 (was 63.4 on the 18-song catalog) |
| Average expectation alignment | 100% |
| Artist diversity | 100% |
| Duplicate rate | 0% |
| Constraint + era satisfaction | 100% (5 constrained cases) |
| First-try valid LLM output | 87% |
| Cases needing the one revision | 3 |

**Live Gemini** (`--llm gemini`, same 16 cases): 16/16 pass, average relevance 82.1, 98% expectation alignment, 100% first-try valid JSON, 0 revisions needed. Google returned 503/504 errors on 5 of 16 copy-writing calls; the template fallback kept every playlist complete.

**Baseline comparison:** the offline keyword interpreter also passes all 16 cases (relevance 87.1). The suite is therefore better at catching specific bugs and regressions than at ranking interpreters against each other; harder and more ambiguous cases would be needed for that.

**Surprising result:** before a fix, the offline heuristic read *"Sad, slow songs, nothing upbeat"* as a **happy** request and *"no country"* as a request **for** country. The in-loop evaluator *passed* the heartbreak playlist, because it judges the playlist against the system's own interpretation. Only the independent expectations caught it. A later catalog-specific bug, *"windows down"* matching the rainy-day keyword *"window"*, was caught the same way. **Lesson:** self-consistency checks can't detect a misunderstood request. Ground-truth evaluation is necessary.

**Unit tests:** 40 pytest tests cover parsing and validation (including decades), ranking parity with the original `recommend_songs`, curation rules, every evaluator check, each agent path (happy path, repair, revision, the one-revision cap, overrides, constraint guard, copy patching, LLM outage), and the harness itself.

**Caveats:** the repair and revision paths were triggered only by scripted mock failures; live Gemini never produced invalid output in testing. The copy check verifies that blurbs refer to real playlist tracks, not that their claims are true. In one live run Gemini described Mr. Brightside as having "swapped guitars for glitter".

---

## 8. Future Work

- Replace the hand-estimated audio features with measured ones from a licensed source, and allow multiple genre and mood tags per song.
- Expand the catalog with underrepresented genres and non-English music, and let users import their own favorites.
- Use valence, danceability, and acousticness, for example "acoustic" or "danceable" as soft preferences.
- Let users rate playlists in the UI and log ratings to tune the evaluator's thresholds against real human judgments.
- Add live-LLM regression runs that track pass rate over time and across models.

---

## 9. Personal Reflection

VibeMatch taught me that weights can push a song to the top even when it misses something the user cares about. Soundtrack AI taught me a related lesson about AI systems: an LLM can be confidently wrong, and a checker that trusts the LLM's interpretation will be confidently wrong along with it. Keeping the ranking in plain Python made every recommendation explainable. Building the independent evaluation harness was what exposed the real failure modes.

---

## 10. AI Collaboration and Misuse

**Collaboration:** an AI coding agent helped design and implement the agent workflow, evaluator, harness, and frontend. See [ai_interactions.md](ai_interactions.md). One helpful suggestion was to check each case against independent expectations rather than only against the evaluator's own checks. One flawed result was the AI's first offline heuristic, which mishandled negation ("nothing upbeat" became happy). The evaluation caught that bug and it was fixed.

**Misuse and safeguards:** the risks are low, since the system only recommends songs from a fixed catalog. Free-text input could include prompt-injection attempts, but LLM output can only select catalog genres and moods (anything else is rejected), the copy is checked for grounding, and all text is rendered as text, never as HTML. API keys are read from environment variables and `.env` is git-ignored.
