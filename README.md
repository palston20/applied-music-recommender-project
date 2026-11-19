# 🎧 Soundtrack AI — your vibe-based personal DJ

Soundtrack AI turns a natural-language vibe into a playlist. Describe a mood, a moment, an aesthetic, or who you want to feel like — *"give me main character energy"*, *"romanticizing my life on a rainy day"*, *"late-night drive through the city"* — and an AI DJ interprets it, ranks songs with the project's **original weighted-score recommender**, curates a playlist that holds one consistent vibe, names it, and explains every pick.

It grows out of the Module 3 **Music Recommender Simulation** (VibeMatch 1.0). The original scoring algorithm is preserved unchanged; the LLM interprets and writes, while Python ranks. The catalog is now **171 real songs** (1888–2024) instead of the original 18 fictional ones.

---

## Base project

This extends my **Module 3 project, the Music Recommender Simulation ("VibeMatch 1.0")**. Its goal was to recommend songs that fit a listener's taste from a small catalog. Given a profile (favorite genre, favorite mood, target energy, optional target tempo), it scored every song in an 18-song CSV with a weighted formula (genre 3, mood 2, energy 2, tempo 1), ranked them, and returned the top 5 with a plain-text reason for each ("matches your preferred mood (chill); energy is 0.02 from your target"). It had no natural-language input, no AI, no diversity or discovery logic, and no automated evaluation beyond two unit tests. Soundtrack AI keeps that scoring function unchanged and builds the AI layer around it. The original runner (`python -m src.main`) still works.

---

## The catalog

[data/songs.csv](data/songs.csv) holds 171 well-known real songs across 22 genres (pop, hyperpop, R&B, hip hop, rock, indie, electronic, folk, disco, jazz, K-pop, soul, metal, country, synthwave, classical, reggaeton, afrobeats, reggae, funk, lofi, ambient) and 15 moods, with deep 2000s coverage (44 songs) for throwback requests. It uses the same columns as before plus a `year` column, so era requests ("2000s R&B", "90s hip hop") work.

- **Title, artist, and year** are real.
- **Genre and mood** are single hand-assigned labels (songs often span several).
- **Energy, tempo, valence, danceability, acousticness** are **hand-estimated** on Spotify-style 0–1 scales. They are not official audio features: Spotify's audio-features API is closed to new apps.
- Every track in the UI has **Listen on Spotify / Apple Music / YouTube** links. These are *search* links; no track IDs are invented.
- The original 18 fictional songs are kept in [data/songs_original.csv](data/songs_original.csv).

To add your own favorites, append rows to `data/songs.csv` with the same columns. Any genre or mood you use becomes part of the vocabulary the LLM is allowed to choose from.

---

## How it works

```
"I'm getting ready to go out. Confident, mysterious energy."
        │
        ▼
1. Interpret   Gemini → JSON preferences (mood, genre, energy, context, aesthetic,
               discovery level, length, related genres/moods, exclusions)
               └─ validated against the catalog's real genres/moods (preferences.py)
2. Guard       explicit "no X / without X" in the request is enforced even if the LLM missed it
3. Rank        ORIGINAL score_song / recommend_songs, once per scoring profile (curation.py)
4. Curate      hard constraints → era filter → 1 song per artist → discovery slots → evenly spread
5. Evaluate    PlaylistEvaluator: structure, length, duplicates, diversity, relevance,
               genre/mood alignment, energy consistency, constraints (evaluator.py)
6. Revise      if anything failed: ONE controlled revision (repair bad JSON, or re-interpret
               with the evaluator's feedback); the better attempt is kept
7. Write       Gemini → title, description, one blurb per track; checked for grounding
               (no blurbs for songs that aren't in the playlist), templates fill gaps
```

See [diagrams/architecture.md](diagrams/architecture.md) for the system diagram.

### The original algorithm (unchanged)

`score_song` in [src/recommender.py](src/recommender.py) compares a song to a preference dict with a weighted average on a 0–100 scale:

| Feature | Weight | Similarity |
|---|---|---|
| genre | 3 | exact match → 1, else 0 |
| mood | 2 | exact match → 1, else 0 |
| energy | 2 | `max(0, 1 − |diff| / 0.5)` |
| tempo (optional) | 1 | `max(0, 1 − |diff| / 40)` |

Preferences that are missing (`None`) are skipped and drop out of the denominator. `recommend_songs` sorts by score (ties by title) and returns the top *k*.

### How Soundtrack AI uses it

- **Blended vibes.** The LLM returns a primary genre/mood plus related genres/moods. Each combination becomes a scoring profile passed to the unchanged `recommend_songs`; related profiles count at 0.9× so the primary vibe leads. Each song keeps its best score.
- **Discovery level.** `familiar` = 0 discovery slots, `balanced` ≈ 25%, `experimental` ≈ 50%. Discovery picks come from genres the listener did *not* ask for, ranked by the same `score_song` with genre left out (mood + energy only), and must score ≥ 40 on that vibe match. They're spread through the list rather than bunched at the end.
- **Consistent vibe, no forced arc.** There is no intro/build/outro structure; the evaluator checks that energy stays near the target instead.
- **Named genres come first.** If you explicitly name a genre ("give me hyperpop"), songs in that genre are ranked ahead of related-genre songs, even when a related song scores higher on the weighted formula. Without this, a playful R&B song (genre-related match, mood match) outscored hyperpop songs with a different mood, and a hyperpop request came back mostly hip hop. A regression case in the evaluation guards against it.
- **Eras.** The LLM extracts `decades` (e.g. `["2000s"]`). If the catalog has enough songs from those decades, the playlist is restricted to them; otherwise in-era songs go first and a note says how many exist. The ranking itself is still your scorer.
- **Honest catalog gaps.** If you ask for something the catalog doesn't have (e.g. punk or gospel), it's recorded as `unavailable`, shown to you, and the closest catalog matches are used.

---

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then add your GEMINI_API_KEY
```

Get a free key at https://aistudio.google.com/apikey and put it in `.env` as `GEMINI_API_KEY=...` (`.env` is git-ignored). The client tries `gemini-flash-lite-latest` first, then `gemini-flash-latest`, with a 20-second timeout per call. Override these with `GEMINI_MODEL` / `GEMINI_TIMEOUT_SECONDS`.

Without an API key everything still runs: the app falls back to an **offline keyword interpreter** (clearly labeled as such) so the CLI and web UI work for demos and grading.

### Web app

```bash
python -m src.server               # http://127.0.0.1:8000
python -m src.server --llm offline # force the no-API-key mode
```

A dark, music-app-style UI (plain HTML/CSS/JS, no build step) with a vibe prompt, example chips, a Familiar / Balanced / Experimental switch, a track-count stepper, generated cover art, an expandable "why this song" per track, the DJ quality-check report, the agent's step trace, and local history of your playlists. The now-playing bar is a visual preview only — the catalog has no audio.

### Command line

```bash
python -m src.cli "Give me main character energy."
python -m src.cli "late-night drive through the city" --discovery experimental --length 8 --trace
python -m src.cli --offline "heartbreak hours, nothing upbeat"
python -m src.cli --json "beach day with my besties"
python -m src.cli --log "rainy day romanticizing"   # also append the full agent trace to logs/agent_traces.md
python -m src.cli                  # interactive mode
```

The original Module 3 runner still works unchanged: `python -m src.main`.

### Tests and evaluation

```bash
pytest                                   # 40 unit/integration tests, no API calls
python -m evaluation.run_evaluation      # 16 scripted requests, mocked LLM → evaluation/results.json + agent_traces.md
python -m evaluation.run_evaluation -v   # include tracklists + agent traces
python -m evaluation.run_evaluation --llm offline   # heuristic baseline
python -m evaluation.run_evaluation --llm gemini    # same cases against live Gemini → results_gemini.json + agent_traces_gemini.md
```

---

## Example output

**Live Gemini run** (`python -m src.cli --llm gemini --log "I want nostalgic 2000s R&B and pop"`), unedited:

```text
🎧  Velour Tracksuits and Soulful Melodies
    Step back into the golden era of MTV with smooth vocals and beats that defined the decade.

    vibe: nostalgic · r&b (+pop, hip hop, soul) · energy 0.6 · familiar · throwback listening session

 1. Fallin' — Alicia Keys (2001)
    r&b / melancholy · energy ██████     0.60 · 96 bpm · match 71
    ↳ Those powerful piano chords instantly transport you right back to 2001 with heavy soulful emotion.
      scorer: does not match your preferred mood (nostalgic); matches your preferred genre (r&b); energy is 0 from your target (0.6)
 2. Foolish — Ashanti (2002)
    r&b / wistful · energy ██████     0.58 · 90 bpm · match 70
    ↳ A wistful mid-tempo rhythm that captures the absolute essence of early 2000s heartbreak.
      scorer: does not match your preferred mood (nostalgic); matches your preferred genre (r&b); energy is 0.02 from your target (0.6)
 3. So Sick — Ne-Yo (2005)
    r&b / melancholy · energy ██████     0.58 · 93 bpm · match 70
    ↳ The ultimate late-night melancholy anthem for anyone missing the golden days of mid-2000s radio.
      scorer: does not match your preferred mood (nostalgic); matches your preferred genre (r&b); energy is 0.02 from your target (0.6)
 4. Dilemma — Nelly (2002)
    r&b / romantic · energy ██████     0.55 · 84 bpm · match 69
    ↳ Pure romantic nostalgia with a gentle bounce that takes you straight back to the front seat of a sedan.
      scorer: does not match your preferred mood (nostalgic); matches your preferred genre (r&b); energy is 0.05 from your target (0.6)
 5. Let Me Love You — Mario (2004)
    r&b / romantic · energy ██████     0.55 · 95 bpm · match 69
    ↳ A velvety smooth mid-tempo classic that defined slow jams in 2004.
      scorer: does not match your preferred mood (nostalgic); matches your preferred genre (r&b); energy is 0.05 from your target (0.6)
 6. We Belong Together — Mariah Carey (2005)
    r&b / melancholy · energy █████      0.46 · 140 bpm · match 63
    ↳ That sweeping emotional crescendo is the definitive sound of mid-decade R&B radio dominance.
      scorer: does not match your preferred mood (nostalgic); matches your preferred genre (r&b); energy is 0.14 from your target (0.6)

Quality check: PASS ✅  (revisions used: 0, llm: gemini)
   ✓ structured_output      LLM preferences matched the schema and catalog vocabulary
   ✓ length                 6 tracks (expected 6)
   ✓ duplicates             0 duplicate track(s)
   ✓ artist_diversity       6 unique artists across 6 tracks (100%)
   ✓ relevance              average relevance 68.8 (minimum 55 for familiar)
   ✓ genre_mood_alignment   6/6 core tracks match a requested genre or mood
   ✓ energy_consistency     6/6 tracks within ±0.3 of target energy 0.6
   ✓ constraints            all user constraints satisfied
   ✓ named_genres           6/6 core tracks are r&b / pop
   ✓ era                    6/6 tracks from the 2000s
   ✓ copy_output            title, description and blurbs are grounded in the playlist

(trace appended to logs/agent_traces.md)
```

Two more live runs are logged in [logs/agent_traces.md](logs/agent_traces.md). "Road trip with the windows down, no country please" gave Teenage Dream, Riptide, Dynamite ✦, Sweet Child O' Mine, September ✦, Mr. Brightside, with 0 country songs. "Give me a hyperpop playlist" gave 4/4 hyperpop core tracks. During that run Google returned `504 DEADLINE_EXCEEDED` on the copy step, and the agent fell back to template copy, so the playlist still came through.

**Offline mode** (no API key):

```text
$ python -m src.cli --offline "Give me main character energy"

🎧  Main Character Hours
    Heads turn when this plays.

    vibe: confident · pop (+hip hop, r&b) · energy 0.78 · balanced

 1. Espresso — Sabrina Carpenter (2024)
    pop / confident · energy ████████   0.76 · 104 bpm · match 99
 2. Hollaback Girl — Gwen Stefani (2005)
    pop / confident · energy ████████   0.76 · 110 bpm · match 99
 3. Don't Start Now — Dua Lipa (2019) ✦ discovery
    disco / confident · energy ████████   0.79 · 124 bpm · match 99
 ...
```

A few mocked-evaluation playlists:

- **"Heartbreak hours. Sad, slow songs, nothing upbeat."** → We Belong Together (Mariah Carey), Ex-Factor (Lauryn Hill), Creep (Radiohead ✦), So Sick (Ne-Yo)
- **"I want nostalgic 2000s R&B and pop."** → Dilemma, Let Me Love You, 505 ✦, Crazy in Love, Untitled (How Does It Feel) ✦, Yeah! (all 2000–2007)
- **"90s hip hop for a summer cookout."** → Electric Relaxation, Nuthin' But a 'G' Thang, Juicy, Ex-Factor, No Scrubs (all 1992–1999)

---

## Reliability and evaluation

### PlaylistEvaluator ([src/evaluator.py](src/evaluator.py))

| Check | Severity | Rule |
|---|---|---|
| structured_output | error | LLM JSON parses, has required fields, uses only catalog genres/moods, energy 0–1, valid discovery level |
| length | error | track count = requested (or every eligible song, if constraints leave fewer) |
| duplicates | error | no repeated id or title+artist |
| artist_diversity | error | unique artists / tracks ≥ 80% |
| relevance | error | average relevance ≥ 55 / 50 / 45 (familiar / balanced / experimental); core tracks use the full weighted score, discovery tracks the genre-blind vibe score |
| genre_mood_alignment | error | ≥ 60% of core tracks match a requested genre or mood |
| constraints | error | no excluded genre/artist, energy within any requested bounds |
| era | error (warning if the catalog lacks enough songs) | ≥ 60% of tracks from the requested decades |
| named_genres | error (warning if the catalog lacks enough songs) | when you explicitly name a genre ("hyperpop"), ≥ 60% of core tracks are that genre |
| energy_consistency | warning | ≥ 70% of tracks within ±0.3 of the target energy |
| copy_output | warning | title/description present, every blurb points at a real track in the playlist |

If any error-level check fails, the agent gets **one** revision. The budget is shared: malformed JSON spends it on a repair prompt; otherwise it's available to re-interpret the request with the evaluator's feedback and a summary of what the catalog contains. If the revision doesn't score better, the original is kept, and anything still failing is shown to the user rather than hidden.

### Evaluation harness ([evaluation/](evaluation/))

16 predefined requests in [evaluation/cases.json](evaluation/cases.json), each with scripted (mocked) LLM responses **and hand-labeled expectations** (acceptable genres/moods, energy range, length, constraints, decades) that are independent of the LLM's own interpretation. Five cases deliberately exercise failure paths: the era jammed into the genre field ("2000s r&b"), chatty non-JSON output, an interpretation that wrongly caps energy at 0.3 for an "angry" request (needs the revision), an LLM that forgets "no country", and copy that references a track not in the playlist.

Results on the same 16 cases (`python -m evaluation.run_evaluation [--llm gemini|offline]`):

| Metric | Mocked LLM | **Live Gemini** | Offline keywords |
|---|---|---|---|
| Overall pass rate (evaluator **and** expectations) | 16/16 | **16/16** | 16/16 |
| Average relevance (0–100) | 90.1 | **82.1** | 87.1 |
| Average expectation alignment | 100% | **98%** | 99% |
| Average artist diversity | 100% | **100%** | 100% |
| Duplicate rate | 0% | **0%** | 0% |
| Constraint + era satisfaction (5 constrained cases) | 100% | **100%** | 100% |
| First-try valid LLM output | 88% (2 scripted failures) | **100%** | — |
| Cases that used the one revision | 3 (scripted) | **0** | 0 |
| Copy output valid without patching | 94% | **69%** (5 Google 503/504 errors → template fallback) | — |

Sample of the printed summary:

```text
$ python -m evaluation.run_evaluation --llm gemini
case                        pass  eval  expect     rel  align   div  rev  title
-------------------------------------------------------------------------------
main_character              ✅     ok    ok        ...
hyperpop_named_genre        ✅     ok    ok        93.2   100%  100%    0  Glitch Out the Orbit

Summary
  Overall pass rate                             100%
  Average relevance (0-100)                     82.14
  Constraint satisfaction (constrained cases)   100%
  First-try valid LLM output                    100%
  Copy output valid (no patch needed)           69%
  Cases                                         16
```

**How to read this.** Live Gemini never produced malformed JSON in this run, so the repair and revision paths are proven by the scripted mock cases, not by Gemini. The offline keyword mode also passes these 16 cases, so the suite does **not** strongly separate a good interpreter from a weak one. Its real value has been catching specific bugs (below) and preventing regressions. The live run did exercise a guardrail for real: Google returned 503/504 errors on 5 copy-writing calls, and the agent fell back to template copy instead of failing.

Moving from the 18 fictional songs to the real catalog raised average relevance (mocked) from **63 to 90**.

**What evaluation caught:**
- The offline heuristic read *"nothing upbeat"* as **happy** and *"no country"* as **country**. The in-loop evaluator *passed* that playlist because it agreed with the wrong interpretation; only the independent expectations exposed it.
- *"windows down"* matched the rainy-day keyword *"window"*, and *"hyperpop"* matched *"hype"*.
- A related-genre ranking flaw made a hyperpop request come back mostly hip hop. This was a user report, now a regression case.
- My own harness was wrong: it expected 5 tracks for requests that never asked for a number. Live Gemini's default of 6 exposed it, and length is now checked only when the request states one.

### Guardrails in action

Each example shows the input, what the system did, and the result. Full step-by-step traces are in [evaluation/agent_traces.md](evaluation/agent_traces.md) (mocked) and [evaluation/agent_traces_gemini.md](evaluation/agent_traces_gemini.md) (live).

| Input | LLM behavior | Guardrail response | Result |
|---|---|---|---|
| "Something to clean my room to, upbeat but not too crazy." | Returned prose: *"Sure! For cleaning I'd go with upbeat pop…"* | `structured_output` invalid → **repair prompt** with the parse error (uses the one revision) | Valid JSON on retry; playlist passes 10/10 checks |
| "I want nostalgic 2000s R&B and pop." | `"genre": "2000s r&b"` (not a catalog genre) | Validation error *"genre '2000s r&b' is not in the catalog genres"* → **repair** | `r&b (+pop)`, `decades: 2000s`; all 6 tracks from 2000–2007 |
| "I need angry music to blow off steam, just 4 songs." | Added `max_energy: 0.3` (misread as calming down) | Evaluator fails: *relevance 0.0; 0/4 core tracks match* → **one revision** with that feedback | Revised prefs drop the cap → Chop Suey!, Duality, Master of Puppets, Smells Like Teen Spirit; 8/8 checks pass |
| "Road trip with the windows down, no country please." | Picked `genre: country`, no exclusion | **Constraint guard** (regex on "no X") adds `exclude_genres: [country]` and swaps the primary genre | 0 country songs; constraint check passes |
| "Beach day with my besties…" | Copy included a blurb for track id 999 | `copy_output` check flags the unknown id → **template blurbs** fill gaps | Every track has a grounded blurb; issue shown as a warning |
| Any request during a Google outage (live) | `503 UNAVAILABLE` / `504 DEADLINE_EXCEEDED` | 20-second timeout + model fallback; copy falls back to templates; interpretation falls back to offline | User still gets a playlist, with a note explaining what happened |
| "Give me a hyperpop playlist." | Related genres (pop, hip hop) | `named_genres` priority + check (≥ 60% of core tracks must be the named genre) | 4/4 core tracks hyperpop (was 2/6 before the fix) |

---

## Project structure

```
data/songs.csv              171 real songs (with year)
data/songs_original.csv     the original 18 fictional songs
src/recommender.py          original weighted scorer (unchanged)
src/main.py                 original Module 3 runner (unchanged)
src/preferences.py          VibePreferences schema + LLM output validation
src/llm_client.py           GeminiClient, MockLLMClient, OfflineVibeClient
src/curation.py             ranking via recommend_songs + playlist curation
src/evaluator.py            PlaylistEvaluator
src/dj_agent.py             SoundtrackAgent: the multi-step workflow
src/cli.py                  command-line interface
src/server.py               stdlib HTTP server + JSON API
web/                        frontend (index.html, styles.css, app.js)
evaluation/                 cases.json, run_evaluation.py, results*.json, agent_traces*.md
logs/agent_traces.md        traces appended by `python -m src.cli --log`
tests/                      pytest suite (original tests kept)
```

---

## Limitations and risks

- **Estimated audio features.** Energy, tempo, valence, danceability, and acousticness were estimated by hand, not measured, so a song's "energy 0.76" is a judgment call.
- **One label per song.** Real songs blend genres and moods; each gets a single genre and mood, so matching is coarser than how people actually hear music.
- **Catalog bias.** 162 songs skew toward English-language, chart-famous Western music, especially 2000s–2020s pop and R&B. Some genres (lofi, ambient, funk) have only 3 songs, and many styles (punk, gospel, Bollywood, opera) have none.
- **Generated copy can be inaccurate.** The copy check confirms each blurb refers to a track in the playlist, not that what it says is true. In one live run Gemini described Mr. Brightside as having "swapped guitars for glitter".
- **LLM interpretation is subjective.** "Main character energy" could reasonably map to several moods; the evaluator checks internal consistency, not whether the user agrees.
- **Mocked metrics are optimistic.** The scripted responses are what a good LLM *should* return; run `--llm gemini` for real-model numbers.
- **The evaluator can only check constraints it knows about.** A constraint the LLM misses is caught only if it's an explicit "no/without X" (the guard) or by the offline expectations.

---

## Original project: Music Recommender Simulation (Module 3)

The base system recommends five songs using the weighted scorer above. The results below came from the original 18-song catalog (now [data/songs_original.csv](data/songs_original.csv)); `python -m src.main` now runs the same four profiles against the real-song catalog, so its output differs.

- **High-Energy Pop** (`pop`, `happy`, 0.9): Sunrise City, Gym Hero, Rooftop Lights, Golden Hour Run, Storm Runner
- **Chill Lofi** (`lofi`, `chill`, 0.4): Midnight Coding, Library Rain, Focus Flow, Spacewalk Thoughts, Last Call Blues
- **Deep Intense Rock** (`rock`, `intense`, 0.9): Storm Runner, Gym Hero, Golden Hour Run, Mirrorball Fever, Iron Horizon
- **Edge case: High-Energy Melancholy Rock** (`rock`, `melancholy`, 0.95): Storm Runner (69.14) still beats the only melancholy song, Last Call Blues (28.57), because genre weight 3 plus close energy outweighs the mood mismatch.

**Weight-shift experiment:** raising genre from 2 → 3 and lowering mood from 3 → 2 moved Gym Hero ahead of Rooftop Lights (pop) and Focus Flow ahead of Spacewalk Thoughts (lofi), while other top-five orderings stayed the same — weight changes shift trade-offs without necessarily reordering everything.

## Reflection

Real recommenders combine item features with behavior (plays, skips, likes) and patterns across similar listeners. This project showed me that a recommendation is a score turned into a ranking — not understanding. Soundtrack AI adds understanding *of the request* through an LLM, but keeps the ranking transparent and testable in Python, and the evaluation showed how easily an interpreter can be confidently wrong without independent checks.

See the [Model Card](model_card.md) and [AI interactions log](ai_interactions.md).
