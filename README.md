# Employee Bot

Telegram bot for employee break, leave and vacation requests, with PostgreSQL persistence.

## Quick links
- [Deployment and Render setup](DEPLOYMENT.md)
- [Development notes](DEVELOPMENT.md)
- [Security review and remaining production gates](docs/SECURITY_REVIEW.md)
- [Database migrations](migrations/)

## Requirements
- Python 3.11+
- PostgreSQL
- Telegram bot token from BotFather

## Run locally
1. Install: `python -m pip install .`
2. Copy `.env.example` to `.env` and fill in local values.
3. Run: `python bot.py`

The application creates its base PostgreSQL tables on startup. It refuses to start without `TELEGRAM_BOT_TOKEN` or `DATABASE_URL`.

## Render
Follow [DEPLOYMENT.md](DEPLOYMENT.md). Render supplies the web service's public URL automatically; a Background Worker can use long polling without a public URL. Never commit tokens or database URLs.

## Important status
This development branch is not yet production-ready for multi-branch operation. Branch-scoped authorization and database queries, durable timers, transactional request side effects, and PostgreSQL integration tests remain required.
