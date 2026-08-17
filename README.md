# WhistleBlower

A dashboard tracking NBA referee accuracy and controversy. Every referee gets three scores: an **Official Score** (computed from the NBA's own Last Two Minute Reports), an **Audience Score** (community per-game voting), and an **AI Verdict** (an LLM + RAG system that reads social discussion about a play and synthesizes a judgment — correct, controversial, or wrong — validated against official rulings wherever the two overlap).

## Status

Early build. See Roadmap below for what's done vs. in progress.

## Why this exists

Existing NBA referee stats sites (NBAstuffer, RefMetrics) report accuracy from official data. Existing AI sports tools generally don't validate their output against ground truth. This project does both: an LLM-driven verdict system that's actually checked against the NBA's own officiating reports where they overlap, instead of an unvalidated black box.

## Tech Stack

- **Backend**: Python, FastAPI
- **Frontend**: React (Vite)
- **Database**: PostgreSQL
- **Vector DB**: Qdrant (self-hosted)
- **Cache / real-time**: Redis (pub/sub + cache)
- **LLM**: Ollama (local)
- **Agent tooling**: MCP
- **ML**: PyTorch (discussion triage classifier)
- **Deployment**: Docker, AWS free tier

## Architecture

```
 ┌─────────────┐      ┌──────────────┐      ┌─────────────┐
 │   React     │◄────►│   FastAPI    │◄────►│  PostgreSQL │
 │  (frontend) │      │  (backend)   │      │  (primary)  │
 └─────────────┘      └──────┬───────┘      └─────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         ┌────────┐    ┌─────────┐    ┌──────────┐
         │ Qdrant │    │  Redis  │    │  Ollama  │
         │(vector)│    │(pub/sub)│    │  (LLM)   │
         └────────┘    └─────────┘    └──────────┘
```

Data sources: NBA Last Two Minute Reports, `nba_api` (box scores, officiating assignments), Reddit API (game-thread discussion). See the spec doc for why X/Twitter was ruled out (no free API tier as of Feb 2026) and why CV-based call detection is explicitly deferred.

## Roadmap

- [ ] Phase 1 — Foundations (schema, auth, league data ingestion)
- [ ] Phase 2 — L2M report ingestion + Official Score
- [ ] Phase 3 — React dashboard + game/referee detail views
- [ ] Phase 4 — Per-game voting (Audience Score)
- [ ] Phase 5 — Team following + email digest
- [ ] Phase 6 — Reddit ingestion + PyTorch triage classifier
- [ ] Phase 7 — AI Verdict engine (RAG + Ollama, validated against L2M)
- [ ] Phase 8 — RAG explainer
- [ ] Phase 9 — Agent/MCP chat interface
- [ ] Phase 10 — Docker + AWS deployment
- [ ] (later) Live push notifications
- [ ] (later, exploratory) CV-based call detection

## Local Development

```bash
docker-compose up -d        # Postgres, Redis, Qdrant
cd backend && pip install -r requirements.txt
cd frontend && npm install
```

(Setup instructions will fill in as each piece comes online.)

## License

MIT
