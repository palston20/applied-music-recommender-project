"""Soundtrack AI command-line interface.

Examples:
    python -m src.cli "Give me main character energy"
    python -m src.cli "late-night drive through the city" --discovery experimental --length 8
    python -m src.cli --offline "rainy day romanticizing my life"
    python -m src.cli            # interactive mode
"""

from __future__ import annotations

import argparse
import json
import sys

from datetime import datetime

from .dj_agent import PROJECT_ROOT, AgentError, PlaylistResult, SoundtrackAgent, trace_markdown
from .llm_client import LLMError, make_llm
from .preferences import DISCOVERY_LEVELS

BAR = "█"


def render(result: PlaylistResult, show_trace: bool = False) -> str:
    p = result.preferences
    lines = [
        "",
        f"🎧  {result.title}",
        f"    {result.description}",
        "",
        f"    vibe: {p.mood} · {p.genre}"
        + (f" (+{', '.join(p.related_genres)})" if p.related_genres else "")
        + f" · energy {p.energy} · {p.discovery_level} · {p.context or 'any setting'}",
        "",
    ]
    for i, t in enumerate(result.tracks, 1):
        s = t.song
        tag = " ✦ discovery" if t.role == "discovery" else ""
        energy_bar = BAR * round(s["energy"] * 10)
        year = f" ({s['year']})" if s.get("year") else ""
        lines.append(f"{i:>2}. {s['title']} — {s['artist']}{year}{tag}")
        lines.append(f"    {s['genre']} / {s['mood']} · energy {energy_bar:<10} {s['energy']:.2f}"
                     f" · {int(s['tempo_bpm'])} bpm · match {t.relevance:.0f}")
        lines.append(f"    ↳ {t.blurb}")
        lines.append(f"      scorer: {'; '.join(t.reasons)}")
    report = result.evaluation
    lines += ["", f"Quality check: {'PASS ✅' if report.passed else 'NEEDS WORK ⚠️'}"
              f"  (revisions used: {result.revisions_used}, llm: {result.llm})"]
    for c in report.checks:
        mark = "✓" if c.passed else ("✗" if c.severity == "error" else "!")
        lines.append(f"   {mark} {c.name:<22} {c.detail}")
    for note in result.notes:
        lines.append(f"   • {note}")
    if show_trace:
        lines += ["", "Agent trace:"]
        for step in result.trace:
            lines.append(f"   [{step['step']}] {step['status']}: {step['detail']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Soundtrack AI — your vibe-based personal DJ")
    parser.add_argument("request", nargs="*", help="describe your vibe (omit for interactive mode)")
    parser.add_argument("--discovery", choices=DISCOVERY_LEVELS, help="familiar, balanced, or experimental")
    parser.add_argument("--length", type=int, help="number of tracks (3-12)")
    parser.add_argument("--llm", default="auto", choices=("auto", "gemini", "offline"),
                        help="auto uses Gemini when GEMINI_API_KEY is set")
    parser.add_argument("--offline", action="store_true", help="shortcut for --llm offline")
    parser.add_argument("--trace", action="store_true", help="show each agent step")
    parser.add_argument("--json", action="store_true", help="print the raw JSON result")
    parser.add_argument("--log", action="store_true",
                        help="append the full agent trace to logs/agent_traces.md")
    args = parser.parse_args(argv)

    try:
        agent = SoundtrackAgent(make_llm("offline" if args.offline else args.llm))
    except LLMError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    def handle(text: str) -> None:
        result = agent.run(text, discovery_level=args.discovery, playlist_length=args.length)
        print(json.dumps(result.to_dict(), indent=2) if args.json else render(result, args.trace))
        if args.log:
            log = PROJECT_ROOT / "logs" / "agent_traces.md"
            log.parent.mkdir(exist_ok=True)
            new = not log.exists()
            with log.open("a", encoding="utf-8") as f:
                if new:
                    f.write("# Soundtrack AI — agent trace log\n\nAppended by `python -m src.cli --log`.\n\n")
                f.write(trace_markdown(result, f"{datetime.now():%Y-%m-%d %H:%M} — {result.request}") + "\n---\n\n")
            print(f"\n(trace appended to {log.relative_to(PROJECT_ROOT)})")

    if args.request:
        try:
            handle(" ".join(args.request))
        except AgentError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0

    print(f"Soundtrack AI 🎧  (llm: {agent.llm.name}) — describe a vibe, or 'quit' to exit.")
    while True:
        try:
            text = input("\nvibe> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in {"quit", "exit", "q"}:
            return 0
        try:
            handle(text)
        except AgentError as error:
            print(f"Error: {error}")


if __name__ == "__main__":
    sys.exit(main())
