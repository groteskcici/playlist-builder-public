# VPS Deploy — Playlist Builder

Target path: `~/clawd/projects/playlist_builder`

Primary runbook: root [`README.md`](../README.md). This file is VPS install detail.

## Install

```bash
git clone https://github.com/groteskcici/playlist_builder.git ~/clawd/projects/playlist_builder
cd ~/clawd/projects/playlist_builder
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
scrapling install
playwright install chromium
mkdir -p data logs data/ai/applied data/research/pending data/research/imported
python scripts/init_db.py
```

## `.env`

Copy from `.env.example`. Critical keys:

```env
DATABASE_URL=sqlite:///data/playlist_builder.db
PLAYLIST_REQUIRE_AI_PLAN=1
PLAYLIST_MARKET=DE
GENIUS_FETCH_PROXY_1=
GENIUS_FETCH_PROXY_2=
GENIUS_FETCH_PROXY_3=
GENIUS_FETCH_PROXY_4=
GENIUS_FETCH_PROXY_5=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DAILY_PUBLISH_CAP=20
DAILY_PUBLISH_FLOOR=5
PIPELINE_STRETCH_SECONDS=2
```

Never commit `.env`. Do not set `OPENAI_API_KEY` / `CODEX_API_KEY` (Codex uses ChatGPT login).

## One daily command

```bash
chmod +x ops/*.sh
unset OPENAI_API_KEY CODEX_API_KEY
./ops/run-daily-pipeline.sh
./ops/install-system-cron.sh
```

Cron installed:

```cron
0 7 * * * cd ~/clawd/projects/playlist_builder && ./ops/run-daily-pipeline.sh >> logs/daily-pipeline.log 2>&1
```

Leaf scripts (`run-album-scheduler.sh`, `run-research-pipeline.sh`, etc.) remain for debugging only.

## Pipeline map

| Step | Behavior |
|------|----------|
| Discover | Genius (+ due refresh); soft-fail + Telegram/log if proxies fail |
| Research | Codex festival/tour/moment JSON → import |
| Compete | Live Spotify search + Codex verdict (albums + research); one retitle retry |
| Build | Albums: prerelease+catalog; research: templates / setlist.fm |
| Publish | Cap 20 / floor 5; dual publish accounts |

## Telegram setup

1. BotFather → bot token → `TELEGRAM_BOT_TOKEN`
2. Message the bot, then `getUpdates` → `chat.id` → `TELEGRAM_CHAT_ID`
3. Alerts also append to `logs/pipeline-alerts.log`

## Smoke Test

```bash
source .venv/bin/activate
unset OPENAI_API_KEY CODEX_API_KEY
codex login status
python scripts/run_daily_pipeline.py --dry-run --skip-discover --skip-research-gen --skip-publish
```

## Safe DB Snapshot

```bash
sqlite3 data/playlist_builder.db ".backup data/playlist_builder.snapshot.db"
```

## Known Limits

- Genius Cloudflare on datacenter IPs: set `GENIUS_FETCH_PROXY_1`…`_5`.
- If all Genius proxies fail, the daily run continues with research and alerts.
