# Playlist Builder

An automated **music operations pipeline** that discovers upcoming releases and live events, evaluates whether a playlist opportunity is worth publishing, assembles tracklists, generates cover art, and publishes to Spotify — on a daily schedule.

Built as a production-style system: durable state, quality gates, failure isolation, operational alerts, and a full test suite.

---

## Why this exists

Music catalogs and live calendars move every day. Manually creating playlists for every album drop, festival, tour, or cultural moment does not scale.

Playlist Builder turns that into a repeatable loop:

**discover → research → compete → build → publish → alert**

Operators run one daily command. The system decides what is worth publishing, what to skip, and how to title and fill each playlist.

---

## Highlights

### End-to-end daily orchestration
A single entry point (`ops/run-daily-pipeline.sh` / `scripts/run_daily_pipeline.py`) runs the full day: album discovery, research generation, competition review, playlist construction, and capped publishing. Leaf scripts remain available for debugging individual stages.

### Four content lanes
| Lane | Source | Playlist focus |
|------|--------|----------------|
| **Albums** | Genius release calendars | Upcoming / newly released albums |
| **Festivals** | AI research + validation | Event-named festival playlists |
| **Tours** | AI research + setlist.fm | Official tour / residency playlists |
| **Moments** | AI research | Time-bound cultural music moments |

### Live competition evaluation
Before publishing, the pipeline searches Spotify for existing playlists on the same opportunity, then uses a structured AI decision to **publish**, **skip**, or **retitle** — with a hard preference for real event and album names over invented phrasing. Crowded opportunities are skipped rather than forced.

### Intelligent track assembly
- **Albums:** pre-release enrichment and catalog matching against Spotify
- **Tours / festivals / moments:** template-driven builds, with **setlist.fm** integration for tour setlists where available
- AI-assisted copy and planning for album publishing paths

### Cover art generation
- **Albums:** promotional artwork uploaded from resolvable track/album images
- **Tours:** a custom dual-beam cover template (Pillow) composed from the headliner’s Spotify artist image, with typography and a muted palette derived from the source art

Example tour cover (*Viva La Lisa*), generated with the dual-beam template:

<p align="center">
  <img src="docs/examples/lisa-viva-la-lisa.jpg" alt="Example playlist cover: Viva La Lisa" width="360" />
</p>

### Multi-account publishing with volume control
Publishes across configured Spotify account slots, with a daily **cap** and **floor**. If the day finishes under the floor after backlog work, Telegram + log alerts fire so operators notice.

### Reliability as a first-class concern
- Genius fetch **proxy failover** for restricted networks
- **Failure isolation** — album discovery can fail without stopping the research/publish lane
- Stretch delays between external API calls to reduce burstiness
- Soft-fail edges (e.g. cover upload) so side paths do not take down the whole run

### Local AI runtime (Codex CLI)
Research generation, competition verdicts, and album planning run through a local Codex CLI session (ChatGPT subscription auth). Applied plans are cached on disk for inspectability and replay.

### Durable state & ops
- SQLite candidate store for discovery, competition, and publish state
- One-shot system cron installer for daily unattended runs
- Deployment notes under `ops/DEPLOY.md`
- Unit tests covering matching, competition, covers, research, and pipeline helpers

---

## Pipeline at a glance

```text
┌─────────────┐   ┌──────────────┐   ┌─────────────────┐
│ Discover    │ → │ Research     │ → │ Competition     │
│ Genius      │   │ festival /   │   │ live Spotify    │
│ albums      │   │ tour / moment│   │ search + AI     │
└─────────────┘   └──────────────┘   └────────┬────────┘
                                              │
                     ┌──────────────┐   ┌─────▼────────┐
                     │ Publish      │ ← │ Build         │
                     │ multi-acct   │   │ tracks+covers │
                     │ cap / floor  │   └──────────────┘
                     └──────────────┘
```

One daily command:

```bash
# Codex CLI must be logged in (unset API keys for this path)
unset OPENAI_API_KEY CODEX_API_KEY
./ops/run-daily-pipeline.sh
```

Or:

```bash
python scripts/run_daily_pipeline.py
```

---

## Tech stack

- **Python** package (`playlist_builder`) with scripts under `scripts/`
- **Spotify** Web API (search, playlist create/update, image upload)
- **Genius** release calendar scraping (Scrapling / Playwright)
- **setlist.fm** for tour setlist resolution
- **Codex CLI** for structured research and competition decisions
- **Pillow** for templated cover generation
- **SQLite** for candidate and publish state
- **Telegram** for operational alerts
- **unittest** suite under `tests/`

---

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
scrapling install
playwright install chromium
mkdir -p data logs data/ai/applied data/research/pending data/research/imported
python scripts/init_db.py
cp .env.example .env   # Spotify / Genius / Telegram / optional fetch proxies
codex login            # ChatGPT subscription auth — do not set OPENAI_API_KEY
```

Deployment: [`ops/DEPLOY.md`](ops/DEPLOY.md).

### Daily cron

```bash
./ops/install-system-cron.sh
```

Example schedule:

```cron
0 7 * * * cd /path/to/playlist_builder && ./ops/run-daily-pipeline.sh >> logs/daily-pipeline.log 2>&1
```

### Environment highlights

| Variable | Purpose |
|----------|---------|
| `GENIUS_FETCH_PROXY_1` … `_5` | Optional HTTP proxies for Genius fetches (failover) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Operational alerts |
| `DAILY_PUBLISH_CAP` / `DAILY_PUBLISH_FLOOR` | Default 20 / 5 |
| `PIPELINE_STRETCH_SECONDS` | Pause between external calls (default 2) |
| `PLAYLIST_REQUIRE_AI_PLAN` | Require AI plan on the daily path (default `1`) |
| `SPOTIFY_PUBLISH_1_*` / `_2_*` | Publish account slots |

### Telegram chat id

1. Message [@BotFather](https://t.me/BotFather) → create a bot → put the token in `TELEGRAM_BOT_TOKEN`
2. Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` into `TELEGRAM_CHAT_ID`

---

## Tests

```bash
python -m unittest discover -s tests -v
```

---

## Project layout

```text
src/playlist_builder/   Core library (discovery, AI, Spotify, covers, persistence)
scripts/                CLI entry points for pipeline stages
ops/                    Cron, deploy, research prompts, cover fonts
tests/                  Unit tests
```
