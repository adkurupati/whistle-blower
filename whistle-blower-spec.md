# WhistleBlower — NBA Officiating Accountability Platform — Project Spec

## Overview

A dashboard-first platform tracking NBA referee accuracy and controversy. Every referee gets two scores side by side — an Audience Score (community per-game votes, sentiment) and an Official Score (accuracy computed from the NBA's own Last Two Minute Reports) — and every game has a detail view showing who reffed, that crew's season-long missed-call record, their history against the teams playing, and player stat splits under that ref. Users can optionally create an account to follow teams and get a next-day email digest when official reports confirm missed calls affecting them.

## Core Features

1. Referee analytics — accuracy stats computed from official L2M reports
2. Game detail view — officiating crew, season missed-call history, ref-vs-team record, player-under-ref stat splits
3. Three-layer scoring per referee/play — Official Score (L2M ground truth, narrow coverage), Audience Score (community per-game voting, sentiment), and AI Verdict (LLM+RAG synthesis of social discussion, broad coverage, validated against Official Score where they overlap)
4. AI Verdict engine — LLM reads retrieved Reddit/social discussion for a given play and synthesizes a category (correct / controversial / wrong) with a confidence rating and justification, rather than just measuring complaint volume
5. RAG explainer — context on any flagged play (relevant rule, analyst commentary)
6. Agent/chat interface — natural-language queries over the officiating dataset
7. Personalization — follow teams, get a next-day email digest on confirmed missed calls affecting them
8. No account required for the public dashboard — accounts are opt-in, only needed for voting/following/digests

**Deferred (in the doc, far out in the timeline, not part of the initial build):**
- Live in-game push notifications (v2+, once the core signal and account system are proven)
- CV-based rule detection for travels/double-dribbles — explicitly postponed until real feasibility (footage sourcing, tracking accuracy) is tested; not timelined until the core platform exists

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python (FastAPI) | Same reasoning as always — fast to build, natural fit for ML/scraping/agent pieces |
| Frontend | React (Vite) | Dashboard-first UI, clean separation from the backend |
| Primary DB | PostgreSQL | Relational data (games, refs, votes, followed teams) with real join-heavy queries (player-under-ref stats) |
| Vector DB | Qdrant (self-hosted, Docker) | RAG explainer, still free and standalone |
| Real-time layer | Redis (pub/sub + cache) | Live "controversy meter" during an active game — genuinely real-time this time, not bolted on |
| LLM | Ollama, local | RAG explainer + agent chat, zero recurring cost |
| ML (near-term) | PyTorch | Lightweight controversy/sentiment classifier trained on Reddit comment text — real, available data, no CV-style data scarcity problem |
| Agent tooling | MCP | Tool server(s) over the structured officiating dataset — lower risk than the fantasy version since queries map to defined DB lookups, not open-ended reasoning |
| Deployment | Docker (modular monolith) on AWS free tier | Same reasoning as always |
| Data sources | L2M reports, nba_api (box scores, ref assignments), Reddit API | Free, official or public; X/Twitter ruled out (no free tier as of Feb 2026, pay-per-use) |
| Notifications | Batch job + email (AWS SES free tier) | Next-day digest only for v1 — L2M reports themselves are only published ~24h after games, so nothing here can be truly live anyway |

**Not used in this project:** C++ (reserved for a separate systems-heavy project — no natural fit here), Kubernetes/microservices (same reasoning as always — monolith first, not worth the operational cost solo).

## Data Model

```
-- Accounts (all optional / opt-in)
users              id, email, password_hash, created_at
followed_teams     user_id, team_id
notification_prefs user_id, digest_enabled

-- Core league data (public sources)
teams              id, name
games              id, date, home_team_id, away_team_id, season, final_score
referees           id, name
game_officials     game_id, referee_id, role (crew chief / referee / umpire)
player_game_stats  game_id, player_id, points, fouls_drawn, etc.

-- Official accuracy data
l2m_reports        id, game_id, source_url, published_at
l2m_calls          id, l2m_report_id, play_description, call_type,
                   correct(bool), ref_id(nullable), team_id, player_id(nullable),
                   game_clock_time

-- Community layer
ref_votes          id, user_id, referee_id, game_id, rating_value, created_at
                   (unique on user_id + referee_id + game_id — one vote per game)

-- AI Verdict engine
reddit_discussion  id, game_id, approx_game_clock, comment_text, source_sub,
                   window_start, window_end, upvotes
                   (embeddings live in Qdrant, keyed by discussion snippet id)
ai_verdicts        id, game_id, referee_id(nullable), l2m_call_id(nullable),
                   category(correct/controversial/wrong), confidence,
                   justification_text, created_at
                   (l2m_call_id populated only when this play overlaps official
                   coverage — that's what enables the validation metric)
                   (exact timestamp-alignment + discussion-window logic: open item)

-- RAG source material
news_articles      id, referee_id(nullable), game_id(nullable), source, url,
                   headline, published_at
                   (embeddings live in Qdrant, keyed by article_id)
```

## Three-Layer Scoring System

- **Official Score**: computed directly from `l2m_calls` — correct-vs-incorrect call rate for plays involving that referee. Objective, but narrow — L2M only covers the final two minutes of close games
- **Audience Score**: aggregated `ref_votes`, per-game granularity, rolling average — explicitly framed like a Rotten Tomatoes audience score, i.e. clearly sentiment, not a claim of accuracy
- **AI Verdict**: per-play LLM+RAG synthesis of social discussion (see below) — category (correct / controversial / wrong) plus a confidence rating, covering far more plays than L2M ever will
- All three shown together where they overlap. The gap between "fans think this ref is terrible," "the AI's read of the discussion," and "official review says this ref is actually accurate" is the interesting part of the product, not something to reconcile away
- Known risk on the Audience Score specifically: crowd voting is vulnerable to brigading after one unpopular-but-correct call. Mitigated by framing (clearly labeled as opinion) rather than by trying to algorithmically strip out bias in v1

## AI Verdict Engine (LLM + RAG over Social Discussion)

This is the core differentiator, and it's a genuine LLM+RAG reasoning problem, not just sentiment counting:

- **Retrieval**: for a given flagged play (game + approximate time window), pull the relevant Reddit discussion — comments from the game thread in that window, ideally from both teams' subreddits, not just a general one — and embed it in Qdrant
- **Synthesis**: Ollama reads the retrieved discussion and produces a structured verdict: category (correct / controversial / wrong), a confidence rating, and a short justification citing what actually stood out in the discussion (rule citations, replay-referenced comments, whether both fanbases agree or it's one-sided)
- **Bias mitigation, built into the synthesis, not bolted on after**: cross-fanbase agreement is a much stronger signal than one team's fans complaining alone — the retrieval and prompt should actively surface whether sentiment is one-sided or bipartisan, and weight confidence accordingly
- **Validation methodology, and this is the important part**: for the subset of plays that are also covered by an official L2M ruling, compare the AI Verdict's category against the real outcome. That gives a genuine, reportable accuracy metric — "the AI Verdict agreed with official rulings on X% of last-two-minute plays it was tested against" — rather than an unvalidated black box. For plays with no L2M coverage (the vast majority of the game), the AI Verdict is the only estimate available, and should be presented as exactly that — an estimate, with its confidence rating and its calibration accuracy (from the validated subset) shown alongside it
- Near-term ML companion: a lightweight classifier (PyTorch) trained on Reddit comment text can handle cheap, high-volume triage (is this moment even worth running the full LLM synthesis on) before the more expensive LLM+RAG step runs — real abundant text data, no copyright or labeled-violation-scarcity problem like the CV route had
- **Open item**: exact mechanics of aligning comment post-time to game clock, and what constitutes a discussion "spike" worth analyzing vs. normal per-team chatter baseline (a blowout game thread behaves very differently from a one-possession game) — not resolved yet, to be worked out during implementation

## RAG Explainer

Same architecture as previously designed for other projects: news/rule-explanation/commentary articles embedded into Qdrant, retrieved per flagged play, Ollama generates a contextual explanation ("here's the rule, here's what analysts said about this call").

## Agent + MCP

Tool server(s) over the structured dataset — natural-language queries like "which ref has the worst record against the Warriors" or "show me the most disputed calls this month." Lower technical risk than an open-ended agent, since these queries map cleanly onto defined SQL lookups rather than requiring the agent to reason about ambiguous, unstructured requests.

## Personalization / Notifications

- Users optionally create an account, follow teams
- Batch job triggered when new L2M report data is ingested: match referenced teams against `followed_teams`, send an email digest ("3 missed calls affected your team last night") via AWS SES free tier
- No live push for v1 — L2M reports are inherently next-day, so there's nothing to make live here anyway. Live push is reserved for a much later phase built around the Reddit signal instead, once that's proven out (see Deferred below)
- Public dashboard requires no account at all — accounts only unlock voting, following, and digests

## Deferred / Far-Future Work

**Live in-game push notifications** — Web Push API, pushing live controversy spikes to users who aren't currently on the site. Real infrastructure (service worker, permission flow), reserved for well after the core platform and Reddit signal are working and proven useful.

**CV-based missed-call detection** — explicitly postponed, not timelined. Broader than just travels/double dribbles — the long-term ambition covers fouls and other missed calls too, not only rule-based violations. Travels/double dribbles remain the more tractable entry point if this is ever picked up (deterministic rules, not a judgment call, so it doesn't need a labeled-violation dataset that doesn't exist — pose + ball tracking, then apply the actual rule algorithmically). Fouls are a fundamentally harder version of this problem — genuine judgment calls, occlusion-heavy, no clean rule to apply algorithmically, real professional systems (Hawk-Eye, SkillCorner) exist specifically because this is hard even with purpose-built multi-camera rigs, which a broadcast-video hobby project won't have access to. Real open questions before any of this is worth scoping: footage sourcing/copyright, and whether broadcast-video tracking accuracy is good enough to trust at all. Not part of the initial build in any way.

## Timeline

Same pace as established earlier — 20+ hrs/week, no fixed deadline.

| Phase | Focus | Est. time | Output |
|---|---|---|---|
| 1 | Foundations — FastAPI skeleton, Postgres schema, auth, ingest games/teams/refs/box scores via nba_api | 2 wks | Core league data flowing in |
| 2 | L2M report ingestion + Official Score computation | 1.5 wks | Referee accuracy stats computed from official data |
| 3 | React frontend — dashboard, game detail view, referee profile pages | 2 wks | Usable public dashboard, no account needed |
| 4 | Per-game voting (Audience Score) + lightweight accounts | 1 wk | Community scoring live |
| 5 | Personalization — follow teams, next-day email digest | 1 wk | Batch notification pipeline |
| 6 | Reddit ingestion + PyTorch triage classifier (cheap first pass on what's worth analyzing) | 1.5 wks | Filtered discussion feed, ready for LLM synthesis |
| 7 | AI Verdict engine — RAG retrieval + Ollama synthesis, category + confidence, validated against L2M where they overlap | 2.5–3 wks | Working verdict system with a real, reportable accuracy metric |
| 8 | RAG explainer (Qdrant + Ollama) | 1.5 wks | "Why was this called" feature — shares infra with the Verdict engine |
| 9 | Agent/MCP chat interface | 2–2.5 wks | Natural-language queries over the dataset |
| 10 | Docker + AWS deployment | 1 wk | Live, deployed platform |
| — (way later) | Live push notifications | TBD | Only after core platform is proven |
| — (way later) | CV travel/double-dribble detection | TBD, research-flavored | Only if data/tracking feasibility checks out |

Core build (1–10): ~16.5–17.5 weeks (~4 months) at 20 hrs/week.

## Skill / Buzzword Coverage

| Item | Where it lives |
|---|---|
| Vector databases | Qdrant, RAG explainer + AI Verdict retrieval over social discussion |
| RAG | Explainer feature, AI Verdict engine (the more substantive use — synthesizing a judgment, not just retrieving context) |
| Docker | Full stack containerized |
| Kubernetes | Not used — same reasoning as always |
| Databases | Postgres (relational), Redis (real-time/cache) |
| REST APIs | FastAPI backend, MCP tool servers |
| AWS | EC2 + RDS free tier, SES for email |
| LLMs | Ollama — RAG explainer, agent chat, and AI Verdict synthesis (reasoning over retrieved discussion to produce a category + confidence, not just answering questions) |
| PyTorch / TensorFlow | Near-term: cheap triage classifier ahead of the AI Verdict engine. Far-term: CV detection (deferred) |
| C++ | Not used — reserved for a separate systems-heavy project |
| Python | Backend, scraping, ML |
| JS | React frontend |
| End to end | Whole platform |
| Microservices | Deferred, monolith first |
| Inference | Ollama local inference, PyTorch classifier inference |
| Memory | Lighter touch here than other project ideas — mainly short-term chat context for the agent, no complex persistent-memory system needed |
| Threads / async | Concurrent ingestion pipelines (L2M, Reddit, box scores), batch digest processing |
| Web scraping | L2M reports, Reddit game threads |
| Agents | Chat/query agent over the officiating dataset |
| MCP | Tool server(s) over structured data |
| Computer vision | Deferred — travel/double-dribble detection, far-future, contingent on real feasibility |

## Open Items

- Reddit signal mechanics: timestamp alignment (comment post-time to game clock), what counts as a "spike" vs. normal per-team chatter baseline
- Where historical referee assignment data actually comes from beyond current-season sources
- Exact L2M report parsing approach (format has been fairly consistent but worth confirming before building the parser)
- Chart/visualization library for the referee accuracy trends
- Auth approach (JWT lifetime, refresh tokens, password reset flow)
- Whether to add TypeScript to the React frontend

