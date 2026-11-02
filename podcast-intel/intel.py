#!/usr/bin/env python3
"""East West Homes Podcast Intel: read every new episode, keep what matters.

Commands
  python intel.py daily                       # read new episodes from config/shows.yaml, write briefs/YYYY-MM-DD.md
  python intel.py analyze --transcript f.txt  # run both reads on a local transcript (no Podscan needed)
  python intel.py discover                    # search transcripts by config/topics.yaml, list candidate shows

Pass 1 (Sonnet) reads each full transcript and flags passages generously.
Pass 2 (Opus) re-reads the full transcript, verifies each flag against the context
document, separates "what they said" from "what we conclude", and keeps only items worth acting on.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).resolve().parent

FLAG_SCHEMA = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": "string"},
                    "speaker": {"type": "string"},
                    "passage": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["position", "speaker", "passage", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["flags"],
    "additionalProperties": False,
}

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "kind": {"type": "string", "enum": ["confirms", "teaches", "warns", "contact"]},
                    "speaker": {"type": "string"},
                    "speaker_credibility": {"type": "string"},
                    "position": {"type": "string"},
                    "what_they_said": {"type": "string"},
                    "what_we_conclude": {"type": "string"},
                    "conflicts_with_our_notes": {"type": "string"},
                    "owner": {"type": "string"},
                    "next_check": {"type": "string"},
                    "primary_source_to_verify": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": [
                    "headline", "kind", "speaker", "speaker_credibility", "position",
                    "what_they_said", "what_we_conclude", "conflicts_with_our_notes",
                    "owner", "next_check", "primary_source_to_verify", "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "corrections": {"type": "array", "items": {"type": "string"}},
        "dropped_count": {"type": "integer"},
    },
    "required": ["items", "corrections", "dropped_count"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- config

def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config/config.yaml").read_text())


def anthropic_key() -> str:
    """PODCAST_ANTHROPIC_KEY first, then ANTHROPIC_API_KEY (some sessions don't pass the latter through)."""
    key = os.environ.get("PODCAST_ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("Set PODCAST_ANTHROPIC_KEY (or ANTHROPIC_API_KEY) to call Claude.")
    return key


def load_context(cfg: dict) -> str:
    p = ROOT / cfg["context_file"]
    if not p.exists():
        print(f"note: {p.name} not found; using context.example.md. Fill in the real one.", file=sys.stderr)
        p = ROOT / "context/context.example.md"
    return p.read_text()


# ---------------------------------------------------------------- Claude calls

class Reader:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=anthropic_key())
        context = load_context(cfg)
        # The context document is identical on every call, so cache it.
        self.context_block = {
            "type": "text",
            "text": f"<company_context>\n{context}\n</company_context>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
        self.first_prompt = (ROOT / "prompts/first_read.md").read_text()
        self.deep_prompt = (ROOT / "prompts/deep_read.md").read_text()

    def _call(self, model: str, effort: str, instructions: str, user: str, schema: dict) -> dict:
        with self.client.beta.messages.stream(
            model=model,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[self.context_block, {"type": "text", "text": instructions}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"model declined: {msg.stop_details}")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("output hit max_tokens; raise the limit")
        text = next(b.text for b in msg.content if b.type == "text")
        return json.loads(text)

    @staticmethod
    def _episode_block(ep: dict) -> str:
        posted = ep["posted_at"].date().isoformat() if ep.get("posted_at") else "unknown"
        return (
            f"<episode show=\"{ep['show']}\" title=\"{ep['title']}\" posted=\"{posted}\" url=\"{ep.get('url','')}\">\n"
            f"<transcript>\n{ep['transcript']}\n</transcript>\n</episode>"
        )

    def first_read(self, ep: dict) -> list[dict]:
        m = self.cfg["models"]
        out = self._call(m["first_read"], m["first_read_effort"], self.first_prompt,
                         self._episode_block(ep), FLAG_SCHEMA)
        return out["flags"]

    def deep_read(self, ep: dict, flags: list[dict]) -> dict:
        m = self.cfg["models"]
        user = (self._episode_block(ep)
                + "\n\n<first_reader_flags>\n" + json.dumps(flags, indent=2) + "\n</first_reader_flags>")
        return self._call(m["deep_read"], m["deep_read_effort"], self.deep_prompt, user, ITEM_SCHEMA)


# ---------------------------------------------------------------- brief output

def render_brief(results: list[tuple[dict, list, dict]], day: str) -> str:
    episodes = len(results)
    flagged = sum(len(f) for _, f, _ in results)
    kept = [(ep, it) for ep, _, r in results for it in r["items"]]
    corrections = sum(len(r["corrections"]) for _, _, r in results)
    lines = [
        f"# Podcast Brief: {day}",
        "",
        f"_{episodes} episodes read in full · {flagged} passages flagged · "
        f"**{len(kept)} kept** · {corrections} corrections caught on second read_",
        "",
    ]
    order = {"warns": 0, "teaches": 1, "confirms": 2, "contact": 3}
    for ep, it in sorted(kept, key=lambda x: (order.get(x[1]["kind"], 9), x[1]["confidence"] != "high")):
        lines += [
            f"## {it['headline']}",
            f"**{it['kind'].upper()}** · confidence: {it['confidence']} · owner: **{it['owner']}**",
            "",
            f"- **Source:** {ep['show']}, \"{ep['title']}\" @ {it['position']} {ep.get('url','')}",
            f"- **Speaker:** {it['speaker']} ({it['speaker_credibility']})",
            f"- **What they said:** {it['what_they_said']}",
            f"- **What we conclude:** {it['what_we_conclude']}",
        ]
        if it["conflicts_with_our_notes"].strip():
            lines.append(f"- **Conflicts with our notes:** {it['conflicts_with_our_notes']}")
        lines += [
            f"- **Next check:** {it['next_check']}",
            f"- **Verify against:** {it['primary_source_to_verify']}",
            "",
        ]
    if not kept:
        lines.append("Nothing worth acting on today. That's normal; keep reading.")
    return "\n".join(lines) + "\n"


def process(reader: Reader, ep: dict) -> tuple[dict, list, dict]:
    print(f"  reading: {ep['show']} | {ep['title']}", file=sys.stderr)
    flags = reader.first_read(ep)
    result = reader.deep_read(ep, flags) if flags else {"items": [], "corrections": [], "dropped_count": 0}
    print(f"    {len(flags)} flagged → {len(result['items'])} kept", file=sys.stderr)
    return ep, flags, result


# ---------------------------------------------------------------- commands

def cmd_analyze(args, cfg):
    text = Path(args.transcript).read_text()
    ep = {"id": "local", "title": args.title or Path(args.transcript).stem, "show": args.show or "local file",
          "posted_at": datetime.now(timezone.utc), "url": "", "transcript": text}
    reader = Reader(cfg)
    res = [process(reader, ep)]
    print(render_brief(res, datetime.now().date().isoformat()))


def cmd_daily(args, cfg):
    from podscan import Podscan

    out_dir = ROOT / cfg["output_dir"]
    out_dir.mkdir(exist_ok=True)
    state_path = ROOT / cfg["state_file"]
    seen = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    since = datetime.now(timezone.utc) - timedelta(hours=cfg["lookback_hours"])

    ps = Podscan(cfg["podscan"])
    reader = Reader(cfg)
    shows = [s for s in yaml.safe_load((ROOT / cfg["shows_file"]).read_text())["shows"] if s.get("podscan_id")]
    if not shows:
        raise SystemExit("No shows have a podscan_id yet; fill in config/shows.yaml (try `intel.py discover`).")

    results = []
    for show in shows:
        try:
            for ep in ps.new_episodes(str(show["podscan_id"]), since, show["name"]):
                if ep["id"] in seen:
                    continue
                ep = ps.with_transcript(ep)
                if not ep["transcript"]:
                    print(f"  no transcript yet: {ep['title']}", file=sys.stderr)
                    continue
                results.append(process(reader, ep))
                seen.add(ep["id"])
        except Exception as e:  # one bad show must not kill the morning run
            print(f"  ERROR on {show['name']}: {e}", file=sys.stderr)

    day = datetime.now().date().isoformat()
    path = out_dir / f"{day}.md"
    path.write_text(render_brief(results, day))
    (out_dir / f"{day}.json").write_text(json.dumps(
        [{"episode": {k: v for k, v in ep.items() if k != "transcript"} | {"posted_at": str(ep.get("posted_at"))},
          "flags": f, "result": r} for ep, f, r in results], indent=2))
    state_path.write_text(json.dumps(sorted(seen)))
    print(f"wrote {path}")


def cmd_discover(args, cfg):
    from podscan import Podscan

    ps = Podscan(cfg["podscan"])
    topics = yaml.safe_load((ROOT / "config/topics.yaml").read_text())
    queries = [q for group in topics.values() for q in group]
    hits, names, examples = Counter(), {}, {}
    for q in queries:
        try:
            for ep in ps.search_episodes(q):
                pid = str(ep["podcast_id"])
                hits[pid] += 1
                names[pid] = ep["show"]
                examples.setdefault(pid, []).append(f"{q} → {ep['title']}")
        except Exception as e:
            print(f"  search failed for {q!r}: {e}", file=sys.stderr)
    out = ROOT / cfg["output_dir"] / "show_candidates.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["podscan_id", "show", "topic_hits", "example_matches"])
        for pid, n in hits.most_common():
            w.writerow([pid, names[pid], n, " | ".join(examples[pid][:5])])
    print(f"wrote {out}: review guests and content, not audience size, then add keepers to shows.yaml")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daily")
    sub.add_parser("discover")
    a = sub.add_parser("analyze")
    a.add_argument("--transcript", required=True)
    a.add_argument("--title")
    a.add_argument("--show")
    args = ap.parse_args()
    cfg = load_cfg()
    {"daily": cmd_daily, "discover": cmd_discover, "analyze": cmd_analyze}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
