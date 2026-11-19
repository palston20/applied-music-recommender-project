/* Soundtrack AI frontend — vanilla JS, talks to src/server.py */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const EXAMPLES = [
    "Give me main character energy.",
    "I want music for romanticizing my life on a rainy day.",
    "Getting ready to go out with my friends — confident, mysterious energy.",
    "I want nostalgic 2000s R&B and pop.",
    "Music for a late-night drive through the city.",
    "Heartbreak hours. Sad, slow songs, nothing upbeat.",
    "High-energy gym workout, no metal.",
    "90s hip hop for a summer cookout.",
    "K-pop for getting ready — confident but cute.",
  ];
  const DISCOVERY_HINTS = {
    "": "Let the DJ read it from your words.",
    familiar: "Songs squarely in your lane.",
    balanced: "Mostly on-target, with a curveball or two.",
    experimental: "Half the list is from genres you didn't ask for — same vibe.",
  };
  const LOADING_LINES = [
    "Reading the room…", "Translating your vibe…", "Digging through the crates…",
    "Ranking every track…", "Running the quality check…", "Naming your playlist…",
  ];
  // mood -> gradient colors for cover art
  const PALETTE = {
    confident: ["#ff7a59", "#8f2bff"], happy: ["#ffd25c", "#ff6fa8"], chill: ["#5ce1e6", "#3a4cc4"],
    focused: ["#7fd1ae", "#2f4858"], intense: ["#ff4d4d", "#3b0d2e"], aggressive: ["#ff3d3d", "#121212"],
    melancholy: ["#5a7bd6", "#1a1f3a"], nostalgic: ["#f6b26b", "#7a4e9e"], wistful: ["#9fb4c7", "#3f4b6e"],
    moody: ["#8f7bff", "#0f1430"], playful: ["#ff8bd1", "#7b5cff"], carefree: ["#ffe066", "#2ec4b6"],
    contemplative: ["#c3b1e1", "#2b2d42"], relaxed: ["#e7c79a", "#6b4f3a"],
    romantic: ["#ff6b8b", "#5a1036"], dreamy: ["#b8a6ff", "#2a3a7a"], euphoric: ["#ffb347", "#ff3c8e"],
  };
  const q = (t) => encodeURIComponent(`${t.title} ${t.artist}`);
  const LISTEN = [
    ["Spotify", (t) => `https://open.spotify.com/search/${q(t)}`],
    ["Apple Music", (t) => `https://music.apple.com/us/search?term=${q(t)}`],
    ["YouTube", (t) => `https://www.youtube.com/results?search_query=${q(t)}`],
  ];
  const listenLinks = (t) => LISTEN.map(([name, url]) =>
    el("a", { class: "listen", href: url(t), target: "_blank", rel: "noopener noreferrer",
              onclick: (e) => e.stopPropagation() }, `${name} ↗`));
  const colors = (mood) => PALETTE[mood] || ["#8f7bff", "#1b1b22"];
  const gradient = (mood, angle = 135) => { const [a, b] = colors(mood); return `linear-gradient(${angle}deg, ${a}, ${b})`; };

  const state = { length: null, current: null, playing: -1, paused: false, loadingTimer: null };

  // ---------- storage (best-effort) ----------
  const store = {
    get() { try { return JSON.parse(localStorage.getItem("soundtrack-history") || "[]"); } catch { return []; } },
    set(items) { try { localStorage.setItem("soundtrack-history", JSON.stringify(items.slice(0, 12))); } catch { /* ignore */ } },
  };

  // ---------- helpers ----------
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "style") node.style.cssText = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const child of children.flat()) {
      if (child == null) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  };
  const titleCase = (s) => (s || "").replace(/\b\w/g, (c) => c.toUpperCase());

  // ---------- setup ----------
  function greet() {
    const h = new Date().getHours();
    $("greeting").textContent = h < 5 ? "Up late?" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  }

  function renderChips() {
    const chips = $("chips");
    EXAMPLES.forEach((text) => chips.append(el("button", {
      class: "chip", type: "button",
      onclick: () => { $("request").value = text; $("request").focus(); },
    }, text)));
  }

  function updateLength(delta) {
    if (state.length === null) state.length = delta > 0 ? 6 : 5;
    else state.length += delta;
    if (state.length > 12) state.length = 12;
    if (state.length < 3) state.length = null; // wraps back to Auto
    $("len-value").textContent = state.length === null ? "Auto" : state.length;
  }

  async function loadStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      const live = data.llm === "gemini";
      $("engine").className = `engine ${live ? "live" : "offline"}`;
      $("engine-label").textContent = live
        ? `Gemini DJ · ${data.songs} songs`
        : `Offline DJ (no API key) · ${data.songs} songs`;
    } catch {
      $("engine-label").textContent = "Server not reachable";
    }
  }

  // ---------- request ----------
  async function spin(requestText) {
    const text = (requestText ?? $("request").value).trim();
    const error = $("error");
    error.hidden = true;
    if (!text) {
      error.textContent = "Tell your DJ what you're in the mood for first.";
      error.hidden = false;
      $("request").focus();
      return;
    }
    const discovery = document.querySelector('input[name="discovery"]:checked').value;
    setLoading(true);
    try {
      const res = await fetch("/api/playlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request: text, discovery_level: discovery || null, playlist_length: state.length }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      saveHistory(data);
      showResult(data);
    } catch (err) {
      error.textContent = err.message || "Something went wrong.";
      error.hidden = false;
    } finally {
      setLoading(false);
    }
  }

  function setLoading(on) {
    $("spin").disabled = on;
    $("loading").hidden = !on;
    clearInterval(state.loadingTimer);
    if (on) {
      $("result").hidden = true;
      let i = 0;
      $("loading-text").textContent = LOADING_LINES[0];
      state.loadingTimer = setInterval(() => {
        i = (i + 1) % LOADING_LINES.length;
        $("loading-text").textContent = LOADING_LINES[i];
      }, 900);
    }
  }

  // ---------- render ----------
  function showResult(data) {
    state.current = data;
    stopPlayback();
    const prefs = data.preferences;
    const mood = prefs.mood;
    const [a] = colors(mood);
    document.documentElement.style.setProperty("--hero-a", a);
    $("cover").style.setProperty("--cover", gradient(mood));
    $("cover-title").textContent = data.title;
    $("pl-title").textContent = data.title;
    $("pl-desc").textContent = data.description;
    $("pl-request").textContent = `“${data.request}”`;
    const discoveries = data.tracks.filter((t) => t.role === "discovery").length;
    $("pl-stats").replaceChildren(
      el("b", {}, "Soundtrack AI"), ` · ${data.tracks.length} songs · energy ${prefs.energy} · ${prefs.discovery_level}`,
      discoveries ? ` · ${discoveries} discover${discoveries > 1 ? "ies" : "y"}` : "",
    );

    const passed = data.evaluation.passed;
    const badge = $("qc-badge");
    badge.className = `badge ${passed ? "pass" : "fail"}`;
    badge.textContent = passed ? "✓ Vibe check passed" : "⚠ Vibe check: needs work";

    const tags = $("tags");
    tags.replaceChildren(
      ...[prefs.mood, ...prefs.related_moods].map((m) => el("span", { class: "tag" }, `#${m}`)),
      ...[prefs.genre, ...prefs.related_genres].map((g) => el("span", { class: "tag" }, g)),
      ...(prefs.context ? [el("span", { class: "tag" }, prefs.context)] : []),
      ...prefs.unavailable.map((u) => el("span", { class: "tag miss", title: "Not in this catalog" }, `no ${u} in catalog`)),
      ...prefs.exclude_genres.map((g) => el("span", { class: "tag miss" }, `no ${g}`)),
    );

    renderTracks(data.tracks);
    renderChecks(data.evaluation, data.notes);
    renderPrefs(prefs, data);
    $("result").hidden = false;
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderTracks(tracks) {
    const list = $("tracks");
    list.replaceChildren(...tracks.map((t, i) => {
      const li = el("li", { class: "track", style: `animation-delay:${i * 45}ms` });
      const row = el("button", {
        class: "track-row", type: "button", "aria-expanded": "false",
        onclick: () => {
          const open = li.classList.toggle("open");
          row.setAttribute("aria-expanded", String(open));
        },
        ondblclick: () => play(i),
      },
        el("span", { class: "t-num" }, i + 1),
        el("span", { class: "t-main" },
          el("span", { class: "t-art", style: `background:${gradient(t.mood, 45 + i * 40)}` }),
          el("span", { class: "t-text" },
            el("div", { class: "t-title" }, t.title, t.role === "discovery" ? el("span", { class: "disc", title: "Discovery pick: outside your genres, same vibe" }, "✦ discovery") : null),
            el("div", { class: "t-artist" }, t.year ? `${t.artist} · ${t.year}` : t.artist))),
        el("span", { class: "t-vibe" }, `${titleCase(t.genre)} · ${t.mood}`),
        el("span", { class: "t-energy" },
          el("span", { class: "meter" }, el("i", { style: `width:${Math.round(t.energy * 100)}%` })),
          `${t.energy.toFixed(2)} · ${Math.round(t.tempo_bpm)} bpm`),
        el("span", { class: "t-match", title: t.role === "discovery" ? "Mood + energy match (genre-blind)" : "Weighted score from the recommender" }, `${Math.round(t.relevance)}%`),
      );
      const why = el("div", { class: "why" }, el("div", {}, el("div", { class: "why-inner" },
        el("p", { class: "why-blurb" }, t.blurb),
        el("p", { class: "why-reasons" }, "Scorer: ", ...t.reasons.map((r) => el("span", {}, r))),
        el("p", { class: "why-listen" }, "Listen: ", ...listenLinks(t)),
      )));
      li.append(row, why);
      return li;
    }));
  }

  function renderChecks(evaluation, notes) {
    $("checks").replaceChildren(...evaluation.checks.map((c) => {
      const cls = c.passed ? "ok" : c.severity === "error" ? "bad" : "warn";
      return el("li", { class: cls },
        el("span", { class: "ic" }, c.passed ? "✓" : c.severity === "error" ? "✗" : "!"),
        el("span", {}, el("b", {}, c.name.replace(/_/g, " ")), el("small", {}, c.detail)));
    }));
    $("notes").replaceChildren(...notes.map((n) => el("li", {}, n)));
  }

  function renderPrefs(p, data) {
    const rows = [
      ["Mood", [p.mood, ...p.related_moods].join(", ")],
      ["Genre", [p.genre, ...p.related_genres].join(", ")],
      ["Energy", `${p.energy}${p.min_energy != null ? ` (min ${p.min_energy})` : ""}${p.max_energy != null ? ` (max ${p.max_energy})` : ""}`],
      ["Context", p.context || "—"],
      ["Aesthetic", p.aesthetic || "—"],
      ["Discovery", p.discovery_level],
      ["Length", `${p.playlist_length} tracks`],
      ["Revisions", `${data.revisions_used} of 1 used`],
      ["Model", data.llm],
    ];
    $("prefs").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
    $("trace").replaceChildren(...data.trace.map((s) =>
      el("li", {}, el("b", {}, s.step), ` — ${s.status}: ${s.detail}`,
        ...(s.failures || s.feedback || []).map((f) => el("div", {}, `↳ ${f}`)))));
  }

  // ---------- fake playback (visual only) ----------
  function play(index) {
    const data = state.current;
    if (!data || !data.tracks.length) return;
    state.playing = (index + data.tracks.length) % data.tracks.length;
    state.paused = false;
    const t = data.tracks[state.playing];
    $("np-title").textContent = t.title;
    $("np-artist").textContent = `${t.artist}${t.year ? ` · ${t.year}` : ""} · ${t.genre}`;
    $("np-listen").href = `https://open.spotify.com/search/${q(t)}`;
    $("np-art").style.background = gradient(t.mood, 45 + state.playing * 40);
    $("nowplaying").hidden = false;
    $("nowplaying").classList.remove("paused");
    $("hero").classList.add("playing");
    setPauseIcon(true);
    document.querySelectorAll(".track").forEach((li, i) => li.classList.toggle("now", i === state.playing));
  }
  function stopPlayback() {
    state.playing = -1;
    $("nowplaying").hidden = true;
    $("hero").classList.remove("playing");
  }
  function togglePause() {
    state.paused = !state.paused;
    $("nowplaying").classList.toggle("paused", state.paused);
    $("hero").classList.toggle("playing", !state.paused);
    setPauseIcon(!state.paused);
  }
  function setPauseIcon(playing) {
    $("np-icon").innerHTML = playing ? '<path d="M6 5h4v14H6zm8 0h4v14h-4z"/>' : '<path d="M8 5v14l11-7z"/>';
    $("np-play").setAttribute("aria-label", playing ? "Pause" : "Play");
  }

  // ---------- history ----------
  function saveHistory(data) {
    const items = store.get().filter((h) => h.request !== data.request);
    items.unshift(data);
    store.set(items);
    renderHistory();
  }
  function renderHistory() {
    const items = store.get();
    $("history-empty").hidden = items.length > 0;
    $("history").replaceChildren(...items.map((h) => el("li", {},
      el("button", { type: "button", onclick: () => showResult(h) },
        el("span", { class: "h-art", style: `background:${gradient(h.preferences.mood)}` }),
        el("span", { class: "h-text" },
          el("div", { class: "h-title" }, h.title),
          el("div", { class: "h-sub" }, `Playlist · ${h.tracks.length} songs`))))));
  }

  // ---------- wire up ----------
  $("vibe-form").addEventListener("submit", (e) => { e.preventDefault(); spin(); });
  $("request").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); spin(); }
  });
  document.querySelectorAll('input[name="discovery"]').forEach((r) =>
    r.addEventListener("change", () => { $("discovery-hint").textContent = DISCOVERY_HINTS[r.value]; }));
  $("len-minus").addEventListener("click", () => updateLength(-1));
  $("len-plus").addEventListener("click", () => updateLength(+1));
  $("play-all").addEventListener("click", () => (state.playing < 0 ? play(0) : togglePause()));
  $("np-play").addEventListener("click", togglePause);
  $("np-next").addEventListener("click", () => play(state.playing + 1));
  $("np-prev").addEventListener("click", () => play(state.playing - 1));
  $("respin").addEventListener("click", () => state.current && spin(state.current.request));
  $("copy-list").addEventListener("click", async () => {
    const d = state.current;
    if (!d) return;
    const text = `${d.title}\n${d.description}\n\n` + d.tracks.map((t, i) => `${i + 1}. ${t.title} — ${t.artist}`).join("\n");
    try { await navigator.clipboard.writeText(text); $("copy-list").textContent = "Copied ✓"; }
    catch { $("copy-list").textContent = "Copy failed"; }
    setTimeout(() => { $("copy-list").textContent = "Copy tracklist"; }, 1500);
  });
  $("nav-new").addEventListener("click", () => {
    $("main").scrollTo({ top: 0, behavior: "smooth" });
    $("request").focus();
  });
  $("clear-history").addEventListener("click", () => { store.set([]); renderHistory(); });

  greet();
  renderChips();
  renderHistory();
  loadStatus();
})();
