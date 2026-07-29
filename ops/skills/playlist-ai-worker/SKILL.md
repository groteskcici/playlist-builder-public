---
name: playlist-ai-worker
description: DEPRECATED — all playlist AI now uses local Codex CLI in playlist_builder.
disable-model-invocation: true
---

# DEPRECATED

Do not use OpenClaw for playlist AI.

```bash
python3 scripts/run_album_ai_copy.py --include-review
python3 scripts/run_research_generation.py --kind all
python3 scripts/run_research_competition.py
./ops/run-album-pipeline.sh
./ops/run-research-generation.sh
```

Clean up legacy crons:

```bash
./ops/setup-cron.sh
./ops/setup-research-cron.sh
```
