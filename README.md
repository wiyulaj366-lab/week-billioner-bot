# week-billioner-bot

MVP for a 24/7 event-driven Polymarket assistant:
- monitors world events (RSS feeds),
- monitors open Polymarket events,
- maps events -> candidate markets,
- sends each event packet to 3 LLM analyzers,
- aggregates decision with risk guardrails,
- sends Telegram notification with event, decision, reasoning, and stake,
- stores full audit trail in SQLite.

Important: default mode is safe (`DRY_RUN=true`, `AUTO_EXECUTE=false`), so no live orders are sent.

## Architecture

- `app/services/world_events.py`: world news ingestion from RSS feeds.
- `app/services/polymarket.py`: Polymarket events ingestion from public API.
- `app/services/ingestion.py`: event-to-market matching.
- `app/services/analysis.py`: 3-model analysis (OpenAI-compatible chat endpoints).
- `app/services/decision.py`: consensus + guardrails (`min confidence`, `min volume`, `max daily loss`).
- `app/services/execution.py`: dry-run execution placeholder (live adapter hook).
- `app/services/notifier.py`: Telegram alerts.
- `app/services/admin_bot.py`: Telegram admin bot (private access by Telegram user ID).
- `app/services/storage.py`: SQLite audit/log tables.
- `app/services/runtime_config.py`: runtime overrides (change config without manual file edits).
- `app/pipeline.py`: one full processing cycle.
- `app/main.py`: FastAPI app + scheduler.

## Quick Start (Docker, recommended for 24/7)

1. Copy config:
```bash
cp .env.example .env
```

2. Fill required values in `.env`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- LLM credentials (`LLM_1_*`, `LLM_2_*`, `LLM_3_*`)
- Optional but recommended for admin:
  - `ADMIN_TELEGRAM_BOT_TOKEN`
  - `ADMIN_TELEGRAM_USER_ID`

3. Start:
```bash
docker compose up -d --build
```

4. Check health:
```bash
curl http://localhost:8000/health
```

5. Trigger manual cycle:
```bash
curl -X POST http://localhost:8000/run-once
```

## Local Run (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Environment Variables

Core:
- `POLL_INTERVAL_SECONDS`: scheduler interval.
- `MAX_EVENTS_PER_CYCLE`: max event packets per run.
- `DATABASE_PATH`: SQLite path.

Risk controls:
- `DRY_RUN=true|false`
- `AUTO_EXECUTE=true|false`
- `MAX_BET_USD`
- `MIN_CONFIDENCE`
- `MAX_DAILY_LOSS_USD`
- `MIN_MARKET_VOLUME`

Data feeds:
- `POLYMARKET_EVENTS_URL`
- `WORLD_FEEDS` (comma-separated RSS list)

LLM analyzers:
- `LLM_1_NAME`, `LLM_1_BASE_URL`, `LLM_1_MODEL`, `LLM_1_API_KEY`
- `LLM_2_*`
- `LLM_3_*`

Admin bot:
- `ADMIN_TELEGRAM_BOT_TOKEN`: bot token for admin commands (recommended separate bot).
- `ADMIN_TELEGRAM_USER_ID`: your Telegram numeric user ID; only this ID can manage config.

## Telegram Admin Commands

If admin bot is configured, send commands to your admin bot:

- `/help` - commands list.
- `/status` - current runtime mode and thresholds.
- `/keys` - editable keys.
- `/set KEY VALUE` - set runtime value immediately.
- `/show KEY` - view current override value (secret keys masked).
- `/mode safe` - `DRY_RUN=true`, `AUTO_EXECUTE=false`.
- `/mode live` - `DRY_RUN=false`, `AUTO_EXECUTE=true`.

Examples:

```text
/set MIN_CONFIDENCE 0.72
/set MAX_BET_USD 50
/set LLM_1_API_KEY sk-...
/mode safe
```

## Production Notes

- Keep `DRY_RUN=true` until live execution adapter is implemented and tested.
- Run paper mode for at least 2-4 weeks.
- Add observability stack (Prometheus/Grafana/Sentry) before real capital.
- Add position reconciliation and market exposure limits before enabling auto-execution.

## CI/CD (GitHub Actions -> Server)

Implemented workflows:
- `.github/workflows/ci.yml` - dependency install + import smoke test.
- `.github/workflows/deploy.yml` - run deploy directly on self-hosted runner (`weekbot` label) using `docker compose up -d --build`.

Server requirements:
- Docker + Docker Compose installed.
- `/opt/week-billioner-bot/.env` present with your runtime secrets.
- GitHub self-hosted runner installed and online on the same server.

Deployment flow:
1. Push to `main`.
2. GitHub Actions schedules `Deploy To Server` on self-hosted runner.
3. Runner syncs code to `/opt/week-billioner-bot` and runs `docker compose up -d --build`.

