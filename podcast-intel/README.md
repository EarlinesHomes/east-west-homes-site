# Podcast Intel: East West Homes

Every morning, Claude reads every new episode from the shows we track, in full, and writes a short brief of what's worth acting on. The first focus is **turning our vacancies into VA and home-health housing** (start with [`LEARNING-PRIMER.md`](LEARNING-PRIMER.md)).

> ⚠️ **This repo is public and served by GitHub Pages.** The real context document (`context/context.md`) and all briefs (`briefs/`) are git-ignored on purpose. Never commit deals, vacancies, people notes or API keys here. Consider moving this folder to a private repo.

## How it maps to the playbook

| Step | Where it lives |
|---|---|
| 1. Write down what you care about (go wide) | `config/topics.yaml` |
| 2. Build the show list (judge by guests and content, not audience) | `config/shows.yaml`, `python intel.py discover` |
| 3. **Write the context document** (the whole trick) | `context/context.md`, copied from `context/context.example.md` |
| 4. Read everything, in full, no keyword filters. Sonnet first, Opus deep | `intel.py` → `first_read` / `deep_read` |
| 5. Check the work: re-read the full transcript, test against our notes, trace big claims to primary sources, keep "said" separate from "concluded" | `prompts/deep_read.md` |
| 6. Correct it: team corrections go into the context doc right away | "Corrections log" section of the context doc |

Built-in lessons from the post: pull the actual passages with timestamps, names and numbers; have the second read catch transcript and first-read errors; **don't** let the agents rank or drop shows after a couple of quiet episodes.

## Setup

```bash
cd podcast-intel
pip install -r requirements.txt
cp context/context.example.md context/context.md   # then fill in the private sections
export ANTHROPIC_API_KEY=...    # console.anthropic.com
export PODSCAN_API_KEY=...      # podscan.fm (has a free trial)
```

1. **Try it on one episode first, no Podscan needed:** paste any transcript into a text file and run
   `python intel.py analyze --transcript episode.txt --show "Show name" --title "Episode title"`
2. **Verify the Podscan endpoints** in `config/config.yaml` against Podscan's docs. All Podscan code is in `podscan.py`.
3. **Find shows:** `python intel.py discover` searches transcripts for every topic and writes `briefs/show_candidates.csv`. Add the good ones (with `podscan_id`) to `config/shows.yaml`.
4. **Daily run:** `python intel.py daily` writes `briefs/YYYY-MM-DD.md` (the brief) and `.json` (everything flagged, for auditing). Schedule it with cron, or ask Claude Code to set up a scheduled task.

## Cost notes
- The context document is prompt-cached, so you pay full price for it once per hour, not once per episode.
- Rough guide: a 1-hour episode is about 12–15k tokens. Sonnet's first read on every episode is the bulk of the cost. Opus only runs on episodes that had flags.
- Podscan pricing tiers are on their site. Check whether transcript fetches count against the daily request limit.

## Files
```
LEARNING-PRIMER.md        VA + home-health housing: what it is, how you get paid, 30-day plan
intel.py                  the pipeline (daily / analyze / discover)
podscan.py                Podscan client (verify endpoints)
prompts/first_read.md     Sonnet instructions: read everything, flag generously
prompts/deep_read.md      Opus instructions: verify, correct, separate said vs. concluded, assign owner
config/                   config, topics, show list
context/context.example.md   public-safe template for the context document
```
