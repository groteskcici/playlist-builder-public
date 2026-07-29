# Local Codex CLI smoke tests

All AI steps run in-repo via `codex exec` (ChatGPT/Pro login). No OpenClaw inbox/outbox.

## Prerequisites

```powershell
codex login status
# Unset API keys so Codex uses subscription auth
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:CODEX_API_KEY -ErrorAction SilentlyContinue
```

## Album copy + publish

```powershell
python scripts/run_album_ai_copy.py --include-review --limit 1
python scripts/publish_pending_playlists.py --dry-run --include-review --limit 1
```

Or:

```bash
./ops/run-album-pipeline.sh --dry-run-publish
```

## Research generation + competition + publish

```powershell
python scripts/run_research_generation.py --kind festival
python scripts/run_research_competition.py --limit 1
python scripts/publish_research_playlists.py --require-competition-pass --limit 1 --dry-run
```

Or:

```bash
./ops/run-research-generation.sh
./ops/run-research-pipeline.sh --dry-run-publish
```
