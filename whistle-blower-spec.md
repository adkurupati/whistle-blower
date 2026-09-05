# WhistleBlower — NBA Officiating Accountability Platform — Project Spec

## Overview

A dashboard-first platform tracking NBA referee accuracy and controversy. Every referee gets two scores side by side — an Audience Score (community per-game votes, sentiment) and an Official Score (accuracy computed from the NBA's own Last Two Minute Reports) — and every game has a detail view showing who reffed, that crew's season-long missed-call record, their history against the teams playing, and player stat splits under that ref. Users can optionally create an account to follow teams and get a next-day email digest when official reports confirm missed calls affecting them.

## Core Features

1. Referee analytics — accuracy stats computed from official L2M reports
2. Game detail view — officiating crew, season missed-call history, ref-vs-team record, player-under-ref stat splits
3. Three-layer scoring per referee/play — Official Score (L2M ground truth, narrow coverage), Audience Score (community per-game voting, sentiment), and AI Verdict (LLM+RAG synthesis of social discussion, broad coverage, validated against Official Score where they overlap)
4. AI Verdict engine — LLM reads retrieved fan discussion (source not yet fixed — see Fan Discussion Sourcing) for a given play and synthesizes a category (correct / controversial / wrong) with a confidence rating and justification, rather than just measuring complaint volume
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
| Frontend | React (Vite) + TypeScript | Dashboard-first UI, clean separation from the backend. TypeScript chosen over plain JS specifically to get a typed contract with the backend — types are generated from FastAPI's OpenAPI schema via `openapi-typescript` (`npm run gen:types` → `src/api/schema.ts`, committed), so frontend types can't silently drift from `schemas.py` |
| Styling | Tailwind CSS + shadcn/ui | Utility-first, fast to build presentable tables/cards without hand-rolling a design system |
| Data fetching | TanStack Query (React Query) | Caching, loading/error states, retry logic (with 404s explicitly excluded from retry) — standard pattern for a multi-page app pulling from a REST API |
| Routing | React Router | Standard choice, no real alternative considered |
| Primary DB | PostgreSQL | Relational data (games, refs, votes, followed teams) with real join-heavy queries (player-under-ref stats) |
| Vector DB | Qdrant (self-hosted, Docker) | RAG explainer, still free and standalone |
| LLM | Ollama, local | RAG explainer + agent chat, zero recurring cost |
| ML (near-term) | PyTorch | Lightweight controversy/sentiment classifier trained on fan discussion text — real, available data, no CV-style data scarcity problem. Source TBD (see Fan Discussion Sourcing) — Reddit's own ToS complicates using its text for ML training specifically, so this row is deliberately source-agnostic |
| Agent tooling | MCP | Tool server(s) over the structured officiating dataset — lower risk than the fantasy version since queries map to defined DB lookups, not open-ended reasoning |
| Deployment | Docker (modular monolith) on AWS free tier | Same reasoning as always |
| Data sources | L2M reports, nba_api (box scores, ref assignments), fan discussion source — TBD | Free, official or public; X/Twitter ruled out (no free tier as of Feb 2026, pay-per-use). Fan discussion source under active evaluation — see Fan Discussion Sourcing — candidates include Reddit, Bluesky, YouTube comments |
| Notifications | Batch job + email (AWS SES free tier) | Next-day digest only for v1 — L2M reports themselves are only published ~24h after games, so nothing here can be truly live anyway |

**Not used in this project:** C++ (reserved for a separate systems-heavy project — no natural fit here), Kubernetes/microservices (same reasoning as always — monolith first, not worth the operational cost solo), Redis (originally planned as a pub/sub + cache layer for a live in-game "controversy meter," but that feature was designed-but-never-built and the platform has since settled on everything running post-game — batch L2M ingestion, next-day email digest, no live push. Same category of unjustified complexity as the microservices split above. Cut before it accreted any dependents — Phases 1–5 never actually connected to it, the container just idled in `docker-compose.yml`. If a real-time feature ever gets scoped later, re-introduce a real-time layer then, chosen against its actual requirements rather than pre-committed here).

## Data Model

```
-- Accounts (all optional / opt-in)
users              id, email, password_hash, created_at
followed_teams     user_id, team_id
notification_prefs user_id, digest_enabled

-- Core league data (public sources)
teams              id, name
                   (use NBA's own canonical integer team IDs directly, e.g. 1610612752 = NYK
                   -- confirmed via nba_api, no need to invent our own team primary keys)
games              id (VARCHAR, zero-padded, e.g. "0042500405" -- confirmed via nba_api's
                   LeagueGameFinder; first 3 digits encode season type: 002 = regular season,
                   004 = playoffs -- store as VARCHAR to preserve this, not INTEGER),
                   date, home_team_id, away_team_id, season,
                   home_score, away_score (both nullable ints -- split from a single
                   final_score field during migration; separate integer columns make margin/
                   winner/average-score queries trivial without string parsing, and nullable
                   covers scheduled/in-progress games that don't have a final score yet)
players            id, name
                   (use NBA's own canonical personId directly, same pattern as teams --
                   was missing from this schema until now; player_game_stats.player_id
                   needs somewhere to actually point)
referees           id, name
                   (use NBA's own canonical personId directly, same pattern as teams/players)
game_officials     game_id, referee_id
                   (no role field -- confirmed via nba_api's BoxScoreSummaryV3 that role/
                   assignment isn't reliably populated, and no planned feature needs crew
                   chief vs. referee vs. umpire distinction anyway. Just track who worked
                   the game. CREW SIZE VARIES -- confirmed 3 officials in an ordinary
                   regular-season game vs. 4 in a Finals game (likely an alternate).
                   Ingestion code must not assume a fixed count.)
player_game_stats  game_id, player_id, points, minutes,
                   fouls_personal (committed -- from nba_api BoxScoreTraditionalV3.foulsPersonal),
                   fouls_drawn (from nba_api BoxScoreMiscV3.foulsDrawn),
                   etc.
                   (two endpoints, joined on game_id + player_id -- lets us show how a
                   player benefits/struggles by the whistle, fouls committed vs. drawn)

-- Official accuracy data (confirmed live via official.nba.com/l2m/json/{game_id}.json --
-- JSON API, not PDF, for any game from roughly 2023 onward; older seasons are PDF-only,
-- deferred -- see Data Ingestion Notes)
l2m_reports        id, game_id, home_score, away_score, published_at
l2m_calls          id, game_id, period, pc_time (game clock at call),
                   call_type (e.g. "Foul: Shooting", raw "Category: Subtype" from source),
                   call_rating (CC / CNC / IC / INC / null -- null means the play wasn't
                   graded, e.g. "not detectable without technology". NOT collapsed to a
                   bool -- four real values plus null, kept raw; any 3-bucket display
                   simplification happens at the API/frontend layer, not in storage),
                   committing_player_name, disadvantaged_player_name (raw strings from
                   the source -- EITHER side can be a whole team, not a player, confirmed
                   via live sample: possession/out-of-bounds rulings like "Stoppage:
                   Out-of-Bounds" put team names in both CP and DP, not just DP. Not
                   predictable from call_type alone),
                   committing_player_id(nullable), disadvantaged_player_id(nullable)
                   (resolved from the raw names at ingest time via a join against
                   `players` only -- NOT against `teams`. When a side is a team name,
                   just leave the player_id null; don't try to force it into a team
                   reference here. `pos_team_id`/`team_id_in_favor` below are already
                   real structured team IDs on every row, so team-level signal isn't
                   lost -- a team's running good/bad-call tally is a future computed
                   aggregate over pos_team_id + call_rating, same pattern as the
                   referee's crew-level fouls aggregate on /referees/{id}, not a new
                   stored table),
                   pos_team_id, team_id_in_favor(nullable),
                   nba_comment (freeform explanation text -- used for AI Verdict
                   validation and UI display), video_link_id(nullable)
                   -- NO ref_id column. Officials are not present in the L2M source at
                   -- all -- confirmed via live fetch. A specific call cannot be
                   -- attributed to one of the 3-4 crew members from this data. Official
                   -- Score is therefore computed at crew level: every official on
                   -- `game_officials` for this game_id shares credit/blame for every
                   -- call in it. This is not a guess or an inferred approximation --
                   -- it's an accurate representation of what the ground-truth data
                   -- actually supports, just not attributed more precisely than that.
                   -- (Rejected alternative: using an LLM to spot a referee's name
                   -- mentioned in fan discussion of the play, then attributing the
                   -- call to that specific ref. Rejected because it would launder a
                   -- crowd guess into the "official" ground-truth layer, undermining
                   -- the whole premise that the Official Score is objective. That signal
                   -- is still worth capturing -- see ai_verdicts.mentioned_referee_id
                   -- below -- just correctly labeled as inference, not fact.)

-- Community layer
ref_votes          id, user_id, referee_id, game_id, rating_value, created_at
                   (unique on user_id + referee_id + game_id — one vote per game)

-- Detailed foul/event log (confirmed via nba_api PlayByPlayV3)
game_events        id, game_id, action_number, period, clock, team_id, person_id,
                   action_type, sub_type, called_by_ref_id(nullable), description
                   (one row per foul event, every game, not just L2M's narrow last-2-minutes
                   coverage. called_by_ref_id comes from regex-parsing the calling ref's name
                   out of the free-text description field, disambiguated against the known
                   4-ref crew for that game from game_officials -- log unmatched cases rather
                   than assuming the parse always succeeds. This is a superset of what l2m_calls
                   captures, but does NOT include correctness grading -- only L2M tells us
                   whether a call was right or wrong. This table is the precise, ref-attributed
                   substrate the AI Verdict engine matches fan discussion against.)

-- AI Verdict engine
social_discussion  id, game_id, approx_game_clock, comment_text, source,
                   source_channel, source_item_id, video_id,
                   retrieval_query_template, window_start, window_end,
                   engagement_score, created_at
                   (source = which platform this row came from, e.g. "reddit" /
                   "bluesky" / "youtube" -- lets multiple sources feed the same
                   table without a schema change per source. source_channel is
                   the generic version of "which subreddit/community/video this
                   came from" -- was source_sub when this table was Reddit-only.
                   source_item_id is the platform-native id of the comment/post
                   itself (YouTube commentId, Bluesky post uri, Reddit t1_xxxxx)
                   -- added to the original field list to enable a
                   UNIQUE(source, source_item_id) constraint so adapters can
                   safely re-run with ON CONFLICT DO NOTHING for idempotent
                   re-ingestion. Ingestion bookkeeping, not downstream feature
                   data. engagement_score generalizes "upvotes" -- Reddit
                   upvotes, Bluesky likes, YouTube likes all map onto it. Stored
                   as raw counts (Integer); per-source normalization, if needed,
                   happens at read time rather than in storage. Ingestion is
                   adapter-based: one adapter per source writes into this same
                   table, so switching sources or running more than one at once
                   is an ingestion-layer change, not a schema/downstream one.
                   See Fan Discussion Sourcing for why this isn't locked to
                   Reddit.)
                   (embeddings live in Qdrant, keyed by discussion snippet id --
                   i.e. this table's autoincrement `id` doubles as the Qdrant
                   point id, so each social_discussion row and its vector are
                   1:1)
                   (video_id is the YouTube-specific container of the comment,
                   distinct from source_channel which stores the uploading
                   channel's display name -- e.g. video_id="99hcdemdiOg",
                   source_channel="Chaz NBA". Nullable so Bluesky/Reddit
                   adapters can leave it empty. Populated for all YouTube
                   rows from ingest_youtube_game.py onward.)
                   (retrieval_query_template captures which rendered search
                   query surfaced the row during ingestion -- calibration
                   data for the multi-template YouTube search strategy, where
                   query phrasing swings result volume 55x (see Fan Discussion
                   Sourcing YouTube findings). Nullable so non-search-based
                   adapters, or future retrieval passes that don't use
                   templated search, can leave it empty. First real
                   distribution on game 0022500696: recap 795, technical foul
                   681, GSW referee controversy 168, teams+referee 155, DET
                   referee controversy 5 -- confirms both broad-match and
                   narrow-match templates contribute non-trivial volume, no
                   single template dominates so pruning to one would lose real
                   corpus.)
                   (schema landed in migration ade1baf744d5, 2026-08-22 --
                   indexes on game_id and source, FK to games.id. video_id
                   and retrieval_query_template added in migration 62c5fbf380b8,
                   same day.)
ai_verdicts        id, game_id, referee_id(nullable), l2m_call_id(nullable),
                   category(correct/controversial/wrong), confidence,
                   justification_text, mentioned_referee_id(nullable), created_at
                   (l2m_call_id populated only when this play overlaps official
                   coverage — that's what enables the validation metric)
                   (category stays a 3-way enum, NOT collapsed to a single percent --
                   a raw confidence percent alone would conflate two different things:
                   how likely the call was correct vs. how confident the model is in
                   that estimate. "Controversial" is also doing real semantic work --
                   it means the discussion itself was genuinely split, not just that the
                   model is unsure. Keep both fields, category is the primary
                   human-readable label, confidence is the secondary metric)
                   (mentioned_referee_id: if the retrieved discussion explicitly names a
                   referee, resolved against `referees.name`, confidence-scored like the
                   rest of the verdict. This is fan inference about who made a call, NOT
                   ground truth -- must be displayed as clearly labeled speculation, kept
                   separate from Official Score / l2m_calls attribution)
                   (exact timestamp-alignment + discussion-window logic: open item)

-- RAG source material
news_articles      id, referee_id(nullable), game_id(nullable), source, url,
                   headline, published_at
                   (embeddings live in Qdrant, keyed by article_id)
```

## Data Ingestion Notes

- **nba_api rate limiting, confirmed empirically**: stats.nba.com throttles after roughly 4 rapid calls (30s read timeout). Ingestion needs ~0.6s pacing between calls and per-game commits (not one big transaction) so partial progress survives a mid-run failure. Confirmed working end-to-end on a full day (10 games, 2024-10-23) via `backend/scripts/ingest_one_day.py`, idempotent via `ON CONFLICT DO NOTHING` — safe to re-run against the same date. This pacing requirement applies to any future nba_api ingestion, not just this script — full-season backfill, ongoing daily ingestion, and the deferred play-by-play/game_events work in Phase 7 will all need it too.
- **Backfill validated at scale**: `backend/scripts/ingest_month.py` reuses `ingest_game()` from `ingest_one_day.py` and pulled all of November 2024 — 222 games, 0 failures, ~2.6s/game. Cumulative totals (Oct + Nov 2024): 30 teams, 500 players, 78 referees, 232 games, 696 `game_officials` rows (exactly 3/game, no dropped crew members), 6,131 `player_game_stats` rows. Cross-check: summing `fouls_personal`/`fouls_drawn` across all games a given referee worked produces equal totals (verified on Scott Foster: 496 = 496) — expected, since every personal foul is a drawn foul on the other end, and it validates the two-endpoint (BoxScoreTraditionalV3 + BoxScoreMiscV3) join is coherent.
- **L2M ingestion verified on 2 real games** (40 calls total, `backend/scripts/ingest_l2m_one_game.py`): committing/disadvantaged player-name resolution works correctly against `players`, including the team-name-instead-of-player-name cases (left null, not force-resolved). Ratings distribution across both games: CC 10, CNC 28, INC 2, no CC/IC — confirms `call_rating` parses all four real values correctly.
- **`team_id_in_favor`/`errorInFavor` confirmed always empty**: scanned 40 close November 2024 games including several with real IC/INC ratings — `teamIdInFavor` is null and `errorInFavor` is `""` on every single call, even confirmed-incorrect ones. The API exposes these fields but the NBA doesn't appear to populate them (at least not in the 2024-25 regular season data checked so far). Kept in the schema in case this changes (e.g. playoffs, later seasons), but don't build any feature that assumes this field has real data without re-checking first.
- **`nba_comment` stores raw HTML entities** (`&apos;` etc.) as-is from the source, unescaped at write time to preserve raw fidelity. Unescape at read time (API layer) if display-clean text is needed.
- **L2M coverage rate, confirmed at scale**: `backend/scripts/ingest_l2m_month.py` checked all 222 November 2024 games — only 76 (34%) had an L2M report at all (games have to be close late to qualify), yielding 1,327 `l2m_calls` rows. Real baseline for how much sample size the Verified Ranking has to work with per month/season — most games contribute zero calls. Call rating distribution: CC 341 (25.7%), CNC 920 (69.3%), IC 10 (0.8%), INC 56 (4.2%) — ~95% correct rate across reviewed windows. The API returns HTTP 403 (not 404) for games with no L2M report, indistinguishable from an invalid game_id by status code alone — fine since we only ever query real game_ids from our own DB, but noted in case that assumption ever changes.
- **All ingestion pipelines run as plain sequential Python scripts, not a task queue**: `ingest_one_day.py`, `ingest_month.py`, `ingest_l2m_one_game.py`, `ingest_l2m_month.py`, and `compute_digest.py` are all straight-line scripts that iterate → per-item commit → sleep for pacing → repeat, invoked directly (`python scripts/foo.py`) or via cron in production. No Celery/RQ/queue infrastructure, no worker pool, no broker. This is deliberate given the actual workload shape: nba_api's ~0.6s pacing requirement (see first bullet) means a single-threaded ingester is already at the throttle ceiling — a queue can't make it faster, only add moving parts. The Phase 6 fan-discussion ingestion (YouTube, confirmed per Fan Discussion Sourcing) follows the same pattern: `backend/scripts/ingest_youtube_batch.py` walks a given date's games, hits `search.list` + `commentThreads.list` per game with polite pacing, and commits per-game to `social_discussion` — same idempotency + resumability guarantees as the existing scripts, no queue added.
- **Batch YouTube ingestion confirmed at small scale (2026-08-22)**: `ingest_youtube_batch.py` run against all 9 games on 2026-01-30, 0 failures, 69s total, 5,476 comments fetched / 3,596 net-new rows inserted (the gap is expected — see cross-game attribution note below, plus one game re-ingested from a prior session). `social_discussion` sits at 5,400 rows across 9 games post-run.
- **Cross-game video attribution quirk, confirmed**: the `UNIQUE(source, source_item_id)` constraint is global, not per-game — when the same YouTube video (e.g. an ESPN "top plays of the night" recap spanning multiple games) surfaces under more than one game's search-template run, its comments get attributed to whichever game processed first; the second game's ingestion run reports those comments as `skipped`, not inserted under its own `game_id`. This is correct behavior for avoiding duplicate rows, but means per-game corpus completeness isn't guaranteed when multi-game videos exist. Not an issue for the weak-labeling pass below; worth revisiting if Phase 7 retrieval ever assumes a game's `social_discussion` rows are exhaustive for that game specifically.

## API Surface (Phase 1)

Read endpoints, all backed by Postgres (not live nba_api calls):
- `GET /teams` — full list, alphabetized
- `GET /games/{game_id}` — game detail: both teams, score, officiating crew, both teams' box scores (sorted by points desc)
- `GET /referees/{referee_id}` — profile: games officiated, list of those games, aggregate `total_fouls_personal`/`total_fouls_drawn`

**Important semantics caveat on the referee endpoint**: `total_fouls_personal`/`total_fouls_drawn` sum every player's fouls across every game that ref worked — since a 3-person crew shares credit for the same game, this is a crew-level aggregate, not a per-ref call count. Real per-ref attribution requires parsing the calling official's name out of play-by-play descriptions, which is exactly the `game_events.called_by_ref_id` regex work already scoped for Phase 7. Don't let this field get relabeled as "calls made by this ref" on the frontend later without that work being done first.

Auth (JWT, resolves the open item below):
- `POST /auth/signup`, `POST /auth/login` — return a bearer token; `GET /me` — first protected route, proves `get_current_user` works as a reusable FastAPI dependency for future routes
- Passwords hashed with bcrypt via passlib. **Known pin**: `bcrypt<5.0` required — passlib 1.7.4 (unmaintained since 2020) breaks against bcrypt 5.x's version probing. Revisit if passlib is ever swapped for calling bcrypt directly.
- Token: HS256, 7-day expiry, no refresh rotation, secret from `.env` (`JWT_SECRET`). Chose `HTTPBearer` over `OAuth2PasswordBearer` — plain JSON login body instead of OAuth2 form-encoded, simpler surface for a React frontend later, and full OAuth2 spec compliance isn't needed for a personal project
- **Gap, not yet addressed**: no rate limiting on `/auth/login` — fine for now, worth fixing before this is ever public-facing

## Season Ranking System

Every referee gets a season-long rank plus a single "overall rating" number (2K-style, e.g. 87 OVR) — turns raw accuracy stats into something skimmable instead of a spreadsheet.

**Two separate rankings, not one blended score** — deliberately not averaging Official/Audience/AI Verdict together, since the gap between them is the product's whole point (see Three-Layer Scoring below):
- **Verified Ranking** — built purely from Official Score (L2M ground truth). Narrow coverage (only close games' last 2 minutes), but objective. Ships with Phase 2.
- **Unverified Ranking** — built from AI Verdict output. Covers every play all season, not just close-game endings, but inherently an estimate rather than fact. Cannot exist until Phase 7 (AI Verdict engine) ships — this ranking arrives in a later phase than Verified, not alongside it.
- Audience Score stays separate from both rankings, not folded in — keeps its existing Rotten-Tomatoes framing (clearly opinion, not accuracy).

**Small-sample problem, and the fix**: L2M only grades a handful of calls per game, so even across a full season a given ref may only accumulate a few dozen graded calls — a single bad call early on could swing a naive rating hard. Fix: shrinkage — blend a ref's own accuracy rate with the league-average rate, weighted by how many calls they've actually been graded on, so low-sample refs regress toward the middle instead of swinging on noise.

**Formula, implemented and verified** (`backend/app/scoring.py`): `shrunk_rate = (correct + k * league_avg) / (total + k)`, `k = 20` (equivalent-observation weight on the league prior). Verified Ranking live at `GET /referees/rankings`, same value also exposed as `official_score` on `GET /referees/{id}`. Confirmed working as intended on real November 2024 data (73 of 78 referees had at least 1 graded call): refs tied at raw=1.000 get separated by sample size (47 graded calls ranks above 22 graded calls, both otherwise perfect); a ref with only 13 graded calls and a rough 0.846 raw rate gets pulled up to 0.909 rather than sitting artificially near the bottom; a well-sampled ref (60 graded calls) barely moves from their raw rate (0.9500 → 0.9501). League-wide correct rate (the prior): 0.9503.

## Season Scope (Live Site vs. Training/Calibration Data)

- The live, public-facing site starts from the **2026-27 NBA season** onward — that's the only data users see displayed. Keeps the production dataset's size and player/team churn bounded going forward, rather than holding every historical season indefinitely.
- Historical data (the Oct/Nov 2024 backfill already ingested, and any further backfill done for development) is **training/calibration data only** — used to: (1) train the PyTorch Reddit-sentiment triage classifier, which needs comment text paired with known outcomes from past games; (2) validate the Official Score and AI Verdict methodology before the 2026-27 season starts; (3) seed the Verified/Unverified Ranking shrinkage prior, so week 1 of the new season isn't wide-open noise. It does not need to live permanently in the same production dataset the live site queries — could be a separate dev DB or a one-time calibration pass.

## Three-Layer Scoring System

- **Official Score**: computed directly from `l2m_calls` — correct-vs-incorrect call rate for plays involving that referee. Objective, but narrow — L2M only covers the final two minutes of close games
- **Audience Score**: aggregated `ref_votes`, per-game granularity, rolling average — explicitly framed like a Rotten Tomatoes audience score, i.e. clearly sentiment, not a claim of accuracy
- **AI Verdict**: per-play LLM+RAG synthesis of social discussion (see below) — category (correct / controversial / wrong) plus a confidence rating, covering far more plays than L2M ever will
- All three shown together where they overlap. The gap between "fans think this ref is terrible," "the AI's read of the discussion," and "official review says this ref is actually accurate" is the interesting part of the product, not something to reconcile away
- Known risk on the Audience Score specifically: crowd voting is vulnerable to brigading after one unpopular-but-correct call. Mitigated by framing (clearly labeled as opinion) rather than by trying to algorithmically strip out bias in v1

## AI Verdict Engine (LLM + RAG over Social Discussion)

This is the core differentiator, and it's a genuine LLM+RAG reasoning problem, not just sentiment counting:

- **Retrieval**: for a given flagged play (game + approximate time window), pull the relevant fan discussion for that window — comments/posts from around the game, ideally spanning both teams' fanbases, not just a general feed — and embed it in Qdrant. Exact platform is TBD (see Fan Discussion Sourcing below); retrieval and everything downstream operates on the normalized `social_discussion` table, not a platform-specific shape
- **Synthesis**: Ollama reads the retrieved discussion and produces a structured verdict: category (correct / controversial / wrong), a confidence rating, and a short justification citing what actually stood out in the discussion (rule citations, replay-referenced comments, whether both fanbases agree or it's one-sided)
- **Bias mitigation, built into the synthesis, not bolted on after**: cross-fanbase agreement is a much stronger signal than one team's fans complaining alone — the retrieval and prompt should actively surface whether sentiment is one-sided or bipartisan, and weight confidence accordingly
- **Validation methodology, and this is the important part**: for the subset of plays that are also covered by an official L2M ruling, compare the AI Verdict's category against the real outcome. That gives a genuine, reportable accuracy metric — "the AI Verdict agreed with official rulings on X% of last-two-minute plays it was tested against" — rather than an unvalidated black box. For plays with no L2M coverage (the vast majority of the game), the AI Verdict is the only estimate available, and should be presented as exactly that — an estimate, with its confidence rating and its calibration accuracy (from the validated subset) shown alongside it
- Near-term ML companion: a lightweight classifier (PyTorch) trained on fan discussion text can handle cheap, high-volume triage (is this moment even worth running the full LLM synthesis on) before the more expensive LLM+RAG step runs — real abundant text data, no copyright or labeled-violation-scarcity problem like the CV route had. Training-data source has real ToS implications depending on platform — see Fan Discussion Sourcing
- **Timestamp alignment, largely resolved**: `game_events` (from PlayByPlayV3) gives precise, structured foul events with exact period/clock — fan discussion can now be matched against a specific known event instead of a fuzzy game-clock window. Still open: what constitutes a discussion "spike" worth analyzing vs. normal per-team chatter baseline (a blowout game thread behaves very differently from a one-possession game), and this baseline will need per-source calibration once a source is picked (see Fan Discussion Sourcing)

## Fan Discussion Sourcing

The AI Verdict engine and its PyTorch triage classifier both need a real corpus of fan discussion text tied to specific plays/games. Which platform supplies that text is intentionally not locked in — evaluate any candidate against three things: (1) API access terms — is there an approval gate, and how likely is a personal project to clear it; (2) ToS compatibility with ML training specifically, not just inference-time retrieval; (3) actual discussion volume for an average regular-season game, not just marquee/playoff games.

**Reddit** — originally the default (r/nba game threads are the closest analog to what this feature wants: real-time, high-volume, per-game). Two problems surfaced after the original tech-stack decision, both sourced directly from Reddit's Responsible Builder Policy (support.reddithelp.com, updated June 2026):
- Registering a script app at reddit.com/prefs/apps no longer grants API access by itself — access now routes through a separate manual approval step, decoupled from registration, with approvals reportedly skewed toward established/commercial use cases. Real risk of never getting approved for a personal project, not just a delay.
- The same policy states data must not be used to train ML/AI models without express written approval, and applies this to non-commercial use too — this hits the Phase 6 PyTorch triage classifier directly, not just the API-access question. The AI Verdict engine's RAG retrieval (inference-time context, not training) is probably a meaningfully different case, but not confidently cleared either.
- Important nuance given the Bluesky volume finding below: the ML-training clause hits *only* the PyTorch triage classifier, not the RAG retrieval layer. A **Reddit-for-retrieval-only** shape — source the classifier's training text from a different platform (e.g. YouTube comments), use r/nba game-thread text purely as inference-time RAG context — cleanly separates the two concerns and stays inside the ToS reading above. Doesn't resolve the API-access approval question, which remains the harder blocker, but means the ML-training clause alone doesn't rule Reddit out for the actual retrieval use case where its per-game volume advantage matters most.

**Bluesky** — free AT Protocol API, no approval process, no review queue at all — just an account and an app password. Rate limit 5,000 points/hour (a post costs 3), plenty of headroom for this scale. ToS does not carry Reddit's ML-training restriction. Original hypothesis was "thinner than Reddit but real" — that turned out to be too generous for the officiating-retrieval use case specifically. **Volume, confirmed via live fetch via `backend/scripts/check_bluesky_volume.py`** on two real games (Lakers/Warriors 2025-26 opening night, and Warriors @ Pistons 2026-01-31 with the Draymond Green / J.T. Orr referee incident — a real reported controversy with a national-headline named ref): raw game-attention exists (Lakers-alone query 877 posts on opening night's 8h window, Lakers-Warriors 318, Warriors-Pistons 138 on the Jan 31 window), but officiating-specific queries collapse 20–100x — `Lakers referee` 1 post on the game night / 17 across all of Oct 2025, `Warriors referee` 1 / 12, and even the marquee named-ref incident (`Draymond referee` 5 on the night / 26 over 6 weeks; `J.T. Orr` 2 / 3 over 6 weeks) barely produced double digits, heavily aggregator-driven, near-zero native engagement (likes 0–6). Wide-window sanity checks confirmed Bluesky's search index reaches back through both windows — the sparsity is real, not an index cutoff. Well below the threshold where cross-fanbase agreement vs. one-sided sentiment (the AI Verdict engine's bias-mitigation signal) is measurable. **Disqualified as the primary source for the AI Verdict engine's retrieval layer** — fine for general game chatter, not for officiating-tied per-play discussion. Its place in the source-agnostic adapter design still holds as a secondary source for playoff-window volume spikes (where the spec already predicted Bluesky sports discussion clusters), just not as the Phase 6 primary.

**YouTube Data API v3** — also free, no approval process, 10,000 quota units/day. `search.list` = 100 units/call, `commentThreads.list` = 1 unit/page (100 comments/page), `videos.list` = 1 unit. Different shape of discussion than a live game thread — comments cluster on highlight/recap videos hours-to-days after the fact rather than in real time during the game — which changes the timestamp-alignment story, but is still real fan reaction text tied to specific games/plays.

**Volume, confirmed via live fetch via `backend/scripts/check_youtube_volume.py`** on the same Jan 31 2026 Warriors @ Pistons / Draymond Green / J.T. Orr incident used for the Bluesky probe, plus a routine Warriors @ Blazers game for contrast. Real findings, more nuanced than Bluesky's flat-line result:
- **Naive shape ("pull comments off the game recap video") fails the same way Bluesky did.** The official NBA channel's Warriors/Pistons recap for the JT Orr incident night has 690 reported comments — but skimming them surfaces Podz, Curry turnovers, Detroit defense; officiating is buried or absent. Routine-game highlight videos (Warriors @ Blazers) had literally 0 comments — not sparse, zero. Same signal-in-noise problem as Bluesky's `<team> referee` collapse.
- **Shape that works: search for controversy-specific analysis/reaction videos (debate shows, commentary channels) published in the 1–7 days after a game, then pull comments off those.** For the JT Orr incident, `Draymond Green tech referee` published 2026-01-31 → 2026-02-07 surfaced Speakeasy debate (718 reported / 515 fetched comments), Whitlock (474/248), Sporting News (159/79), all specifically about the incident — 844 fetched, 1,356 reported total across 5 videos. Comments were on-topic by construction (the videos are *about* the controversy), included cross-viewpoint disagreement (fans arguing both sides of whether the ref put his hand up), and provided exactly the corpus the AI Verdict engine's retrieval + bias-mitigation logic needs.
- **New risk not previously flagged**: `search.list` ranking is opaque and query-sensitive. `Draymond Green referee JT Orr` (specific) returned only small niche channels, 15 comments fetched; `Draymond Green tech referee` (broader) returned the mainstream channels above, 844 fetched — same underlying question, ~55x volume gap purely from phrasing. Production ingestion needs a small library of query templates per controversy type, not a single "correct" query, or it will silently under-retrieve.
- **Cost check**: the search-then-comments shape is ~100 quota units per (game × controversy) probe. Daily 10k budget supports ~100 probes/day, well above the ~10 games/day in-season rate. Comment retrieval itself is essentially free (1 unit / 100 comments).

**Viable as Phase 6 primary**, contingent on ingestion doing the two-step search-then-comment shape with multiple query templates per game — not the naive one-step "comments-on-the-recap" shape.

**Decision needed before Phase 6 starts**, not before now: pick a primary source, and keep the `social_discussion` schema and ingestion layer source-agnostic (one adapter per source, see Data Model above) so this isn't a rewrite if the answer changes later or a second source gets added for volume. Current standing after both spot-checks:
- **YouTube** — leading candidate for Phase 6 primary. Passes volume bar (see YouTube paragraph above) contingent on two-step search-then-comment ingestion shape. Timestamp-alignment story is delayed-not-live (comments arrive hours-to-days post-game); acceptable since L2M reports are next-day anyway.
- **Reddit-for-retrieval-only** — live alternative if API approval ever comes through. Advantage over YouTube: per-game subreddit thread structure sidesteps YouTube's query-sensitivity risk (one thread per game is unambiguous; a `search.list` query is a guess). Blocker remains API-access approval, not the ML-training clause.
- **Bluesky** — out as primary, retained as secondary adapter for playoff-window volume spikes where its sparsity issue eases.

Reddit stays a viable adapter to add later if/when approval comes through — nothing here rules it out permanently.

## Weak Labeling for the Triage Classifier

The PyTorch triage classifier (Phase 6) needs labeled training examples — comment text tagged as "likely about the officiating controversy" vs. "general game chatter" — and no hand-labeled dataset exists. Rather than manually label a training set, `backend/scripts/label_social_discussion.py` computes a weak label on the fly from ingestion metadata already captured on each row, not stored as a schema column (so the rule can be tuned without a migration each time):

- **label = 1** if `retrieval_query_template` is anything other than a "recap"-style template, OR the comment text keyword-matches (word-boundary regex, common variants included) terms like ref/referee/foul/call/no-call/whistle/technical/ejected/flagrant/blown call, OR it names a game official from `game_officials` for that game
- **label = 0** otherwise

**First real run, confirmed 2026-08-22, across all 5,400 ingested rows (9 games)**: 30.5% labeled 1 overall (1,645 rows), concentrated exactly where expected — the JT Orr incident game at 57.3%, a second real-controversy game (Cavs @ Suns) at 86.9%, uneventful games near-zero (Kings/Celtics at 0.8%). Directionally correct: the rule tracks real controversy, not noise. One null result worth noting: zero rows matched on official name specifically (fans apparently say "the ref" or a bare surname, not full names) — the name-matching branch may be dead weight, not urgent to fix.

**Real risk, flagged before classifier training starts, not after**: the label is almost perfectly predictable from `retrieval_query_template` alone — non-recap templates score ~100% label 1, recap templates score ~1–11%. A classifier trained naively on this data would likely just learn to reproduce which search template found a comment, information already known at ingestion time, rather than learning to read comment content — training a model to re-derive a fact you already have isn't useful work. The genuine value a trained classifier could add is catching the harder cases: officiating-relevant comments buried inside recap-template videos that the keyword rule barely catches (201 of 5,400 rows, the keyword-matched-in-a-recap-template slice). **Training set was deliberately restricted to the recap-template slice** (via `backend/scripts/build_classifier_dataset.py`) so the model can only score by reading the comment text — template provenance is held constant across the whole dataset, forcing genuine language learning rather than a template-lookup shortcut.

## Triage Classifier — Trained Model (Phase 6)

**Model** (`backend/app/ml/triage_classifier.py`): small feedforward net on 384-dim `all-MiniLM-L6-v2` embeddings — 384 → 64 (ReLU, dropout 0.3) → 1 logit. Deliberately tiny; the ~10k-row training corpus wouldn't support anything bigger without overfitting harder than it already does.

**Training** (`backend/scripts/train_triage_classifier.py`): 8,560 train / 2,213 test rows (game-level split — 4 games held out entirely, no comment leakage). `BCEWithLogitsLoss(pos_weight=22.2)` to handle the 4.3% imbalance without discarding any of the 369 train positives. Adam, lr 1e-3, batch 128, weight decay 1e-4, early stopping on test loss (patience 4). Test loss bottoms at epoch 11 (0.2816); train loss keeps falling past that but the ranking metric is stable, so early stopping + restore-best-checkpoint is the right shape here.

**Test-set results** (99 positives, 4.47% base rate — accuracy deliberately not reported, meaningless at this imbalance):

- **PR-AUC 0.9239** (random-baseline reference: 0.045)
- Threshold sweep (precision / recall / F1): 0.4 → 0.482 / 0.939 / 0.637; 0.5 → 0.580 / 0.919 / 0.711; 0.7 → 0.761 / 0.899 / 0.824; 0.8 → 0.869 / 0.869 / 0.869.

**Chosen operating threshold: 0.4** (documented in `backend/app/ml/triage_inference.py` as `DEFAULT_THRESHOLD`). Rationale — a triage filter's error costs are asymmetric: a missed controversy is silently dropped before the LLM ever sees it and can't be recovered downstream, while a false positive just costs one LLM inference. At 0.4 the model keeps recall at 0.939 while cutting the LLM's input volume by ~91% (193 of 2,213 test rows kept). Not final — revisit once Phase 7 knows real per-comment LLM cost; if that's expensive, sliding to 0.6–0.7 trades ~4 points of recall for meaningful precision gains.

**Eyeball check finding, more informative than any single metric**: on 10 real test-set comments spanning TP/FP/FN, the trained classifier *corrects* the weak label's mistakes in both directions rather than reproducing them —
- **Catches missed positives** the keyword rule dropped because "officiating"/"callers" aren't in the regex (e.g. *"Pathetic Officiating…Shame on the NBA"*, *"That last 24 seconds of officiating sucks"*): scored 0.92–0.99, weak-labeled 0.
- **Rejects false positives** the keyword rule triggered on bare "call"/"foul" mentions that aren't about officiating at all (e.g. *"people called me a casual"*, *"coming back to ts bc people call me a bandwagon"*): scored 0.23–0.39, weak-labeled 1.

This is exactly the "learn language, not template provenance" outcome the Weak Labeling risk called for. Metric-wise the model looks like it has messy precision at 0.4; qualitatively, a chunk of the "FPs" are actually true positives the weak label missed and a chunk of the "FNs" are actually true negatives the weak label wrongly marked — the real precision/recall are almost certainly better than the numbers against the noisy labels show.

**Inference wrapper** (`backend/app/ml/triage_inference.py`): loads model + embedder lazily and caches (once per process), exposes `classify_comment(text)` / `classify_comments(texts)` returning `TriageResult(probability, is_relevant)`. Batched form for hot paths; single-string form for one-off calls. This is what Phase 7 retrieval / synthesis and future ingestion-time filtering should call — the training script is calibration, not the runtime.

## RAG Explainer

Same architecture as previously designed for other projects: news/rule-explanation/commentary articles embedded into Qdrant, retrieved per flagged play, Ollama generates a contextual explanation ("here's the rule, here's what analysts said about this call").

## Era-Based Referee Personas (RAG Explainer extension, deferred)

Idea: let a flagged play be graded by "referee personas" from different rule eras (e.g. 70s / 90s / 00s / today), each judging the same play against that era's actual rulebook and enforcement emphasis rather than today's — e.g. hand-checking was legal pre-2004, travel enforcement has tightened significantly since the 2019 "gather step" clarification, defensive three seconds didn't exist before 2001.

**Deliberately scoped separate from the AI Verdict engine**, not a mode of it. `ai_verdicts.category` derives its credibility from validation against L2M ground truth (see AI Verdict Engine section) — there is no ground truth for "how would a 1995 crew have called this," it's inherently counterfactual. Folding era-personas into the same table/category would blur the "this is validated" claim that's the actual differentiator. This lives as its own feature.

**Where it actually fits**: an extension of the RAG Explainer (Phase 8), reusing the same Qdrant + Ollama retrieval architecture — different system prompt per era persona, retrieving from a new, era-conditioned rule/enforcement-history corpus instead of (or alongside) `news_articles`. That corpus needs real sourcing before this is buildable — actual rule-change history (hand-checking 2004, defensive three seconds 2001, restricted-area circle enlarged 2010, gather-step travel clarification 2019, flagrant-foul point system changes, periodic league enforcement-emphasis memos) — not left to the LLM to invent from training knowledge alone.

**Open question, not yet decided**: single-play mode (take one already-flagged play from `game_events`, run it through all four era prompts — cheap, and probably the funnier version: one controversial play judged by four decades of refs side by side) vs. whole-game mode (every play re-graded per era — much higher LLM cost, not clearly worth it). Leaning toward single-play given cost, not committed.

**Not factored into Phase 8's time estimate yet** — logged here as a scoped idea, not scheduled. Revisit once Phase 8's base RAG Explainer is built and the era-corpus sourcing effort can be estimated for real.

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

**CV-based referee jersey-number attribution (separate idea from the above)** — reading a referee's jersey number from broadcast footage at the moment of a call, to attribute that specific call to that specific ref. Different in kind from the foul/travel detection work above: the call's correctness is already known from L2M, this only identifies who made it — closer to an OCR/person-identification task than a judgment-call task, and more deterministic in principle. Would be the first approach that could resolve the crew-vs-individual-ref attribution gap with actual fact, rather than the current crew-level credit-sharing or the `ai_verdicts.mentioned_referee_id` crowd-guess signal. Shares the same footage-access/copyright blocker as the rest of this section, plus its own new one: officials move constantly and get occluded by players, broadcast cameras aren't dedicated to tracking them, so even with footage, legible-number-at-the-exact-call-moment would only cover a subset of calls, not all of them. Not scoped, not timelined — logged here for later.

## Timeline

Same pace as established earlier — 20+ hrs/week, no fixed deadline.

| Phase | Focus | Est. time | Output |
|---|---|---|---|
| 1 | Foundations — FastAPI skeleton, Postgres schema, auth, ingest games/teams/refs/box scores via nba_api | 2 wks est. — **done in ~1 day** | Core league data flowing in (232 games backfilled), read endpoints + JWT auth live |
| 2 | L2M report ingestion + Official Score computation | 1.5 wks | Referee accuracy stats computed from official data |
| 3 | React frontend — dashboard, game detail view, referee profile pages | 2 wks est. — **done in 1 day** | Usable public dashboard, no account needed. Three pages live: `/` (Verified Ranking table), `/referees/:id` (profile + games, handles null official_score), `/games/:id` (score, crew, both box scores) — all typed from generated OpenAPI schema, cross-linked, with loading/error/404 states throughout |
| 4 | Per-game voting (Audience Score) + lightweight accounts | 1 wk est. — **done in 1 day** | Community scoring live. `ref_votes` (Postgres upsert, one vote per user/ref/game), `audience_score` on `/referees/{id}`, full auth UI (login/signup/global auth state), 5-star voting widget on the game detail page gated behind login with a redirect-back-after-login flow |
| 5 | Personalization — follow teams, next-day email digest | 1 wk est. — **done in 1 day** | Batch notification pipeline. `followed_teams`/`notification_prefs` (composite-PK follow table, upsert prefs), digest computation (`backend/scripts/compute_digest.py`) verified against real November 2024 IC/INC data, delivery isolated behind `deliver_digest()` — logs for now, swaps for real AWS SES in Phase 10. Teams browse page + follow/unfollow UI, reusing the auth-gate modal (now parameterized after being caught hardcoded to voting copy) |
| 6 | Fan discussion ingestion (YouTube, confirmed — see Fan Discussion Sourcing) — **done**; PyTorch triage classifier — **done** | 1.5 wks | Ingestion: `social_discussion` populated across 24 games in the recap-template slice (10,773 rows used for classifier training/eval), idempotent batch script, weak-labeling script confirming real signal (see Weak Labeling). Classifier: trained model at PR-AUC 0.9239, threshold 0.4 default (recall-prioritized), inference wrapper at `app/ml/triage_inference.py` — see Triage Classifier — Trained Model section for full results |
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
| Databases | Postgres (relational). Redis was originally planned as a real-time/cache layer and has since been cut — see "Not used in this project" above for why |
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
| Threads / async | Concurrent ingestion pipelines (L2M, fan discussion, box scores), batch digest processing |
| Web scraping | L2M reports, fan discussion source (TBD) |
| Agents | Chat/query agent over the officiating dataset |
| MCP | Tool server(s) over structured data |
| Computer vision | Deferred — travel/double-dribble detection, far-future, contingent on real feasibility |

## Open Items

- ~~Regular-season game data completeness~~ — resolved: verified just as complete as the Finals game across all endpoints (identical column sets, no missing fields). Two structural differences found, neither a regression: officials crew size varies (3 vs. 4, see game_officials note above), and BoxScoreSummaryV3's inactive-players sub-frame is populated in regular season but was empty in the sampled Finals game -- not currently used by any planned feature, available if a "DNP/inactive list" feature is wanted later.
- `game_events` ref-name parsing: log/handle cases where a foul description's calling-ref name doesn't cleanly match one of the game's known 4-ref crew, rather than assuming the regex always succeeds
- ~~Full play-by-play ingestion scope decision~~ — decided: deferred out of Phase 1. `game_events`/foul-event ingestion (schema already defined above) lands in Phase 7 alongside the AI Verdict engine, where it's actually consumed. Not forgotten — just sequenced later on purpose.
- Fan discussion signal mechanics: timestamp alignment (comment post-time to game clock), what counts as a "spike" vs. normal per-team chatter baseline — will need per-source calibration once a source is picked (see Fan Discussion Sourcing)
- ~~Exact L2M report parsing approach~~ — resolved: live JSON API at `official.nba.com/l2m/json/{game_id}.json`, not PDF, for roughly 2023-onward games (older seasons are PDF-only, deferred — not needed given the season-scope decision above). Requires `Referer`/`User-Agent` headers or the server 4xxs. Schema updated above to match confirmed fields.
- Chart/visualization library for the referee accuracy trends
- ~~Auth approach (JWT lifetime, refresh tokens, password reset flow)~~ — resolved: JWT, HS256, 7-day expiry, no refresh rotation, no password reset yet (see API Surface section above). Password reset flow still genuinely open, just not urgent pre-launch.
- Whether to add TypeScript to the React frontend
- Rate limiting on `/auth/login` — not yet implemented, needed before any public deployment. Scoped to an in-process (in-memory) or Postgres-backed limiter, **not Redis** — Redis was cut from the stack (see "Not used in this project"), and login rate limiting alone doesn't justify re-adding it. Trivial scale-up path if the deploy ever needs to run more than one backend replica: move the limiter's counter table into Postgres, still no Redis dependency.
- ~~Which platform supplies fan discussion data for the AI Verdict engine / triage classifier~~ — no longer just a Reddit-specific risk note. Moved to its own section, **Fan Discussion Sourcing** (above, under AI Verdict Engine) — covers Reddit's approval gate + ML-training ToS restriction, plus Bluesky and YouTube as source-agnostic alternatives. Still an open decision, due before Phase 6 starts.
- Exact shrinkage formula for the Verified/Unverified Ranking (how much weight low-sample refs' league-average prior gets vs. their own rate)
- Where training/calibration data physically lives (separate dev DB vs. a one-time pass against the same Postgres instance) — leaning toward separate, not decided
- `TeamOut` only exposes `id`/`name`, no tricode — game detail page shows full team names ("Boston Celtics @ Charlotte Hornets") instead of a compact "BOS @ CHA" scoreboard style. Add a `tricode` field to the backend + re-run `gen:types` if the compact style is wanted later.
- **Frontend verification note**: Vite's dev server transpiles TS via esbuild, which strips types without checking them — a clean dev server log is NOT proof of type correctness. Always verify frontend changes with `npm run build` (runs `tsc -b` first) or `npx tsc --noEmit` from inside `frontend/`, not just "the page loaded."
- **Visual polish, deliberately deferred**: functionally styled (real shadcn components, consistent spacing, responsive grid) but not polished — `index.html` still has the default Vite title/favicon, loading states are plain text not skeletons, no dark mode toggle. Doesn't block Phase 4+; revisit before this is ever screenshotted for a portfolio/resume.
- **Frontend polish backlog** (running list, not scoped/ordered yet): rankings table color-coding + summary stat cards + rank badges (in progress), NBA team logos (check for an official hotlink-by-team-id CDN pattern before building anything — logos are trademarked, hotlinking the NBA's own hosted images is more defensible than rehosting copies), referee headshots (real sourcing gap — no obvious free public source of ref photos the way there is for players/teams, likely needs an initials-avatar fallback for refs without a found photo), back navigation, more to be added as ideas come up.

~~Where historical referee assignment data actually comes from beyond current-season sources~~ — resolved: nba_api's `BoxScoreSummaryV3` covers officials data for historical games, not just current season.

- Cross-game YouTube video attribution — see Data Ingestion Notes. A comment from a video covering multiple games gets credited to whichever game's ingestion run processed it first; not a correctness bug (avoids duplicate rows) but means per-game `social_discussion` completeness isn't guaranteed when multi-game recap/highlight videos exist. Revisit if Phase 7 retrieval ever needs to assume exhaustive per-game coverage.
- Classifier training-set design — see Weak Labeling for the Triage Classifier. The weak label is highly correlated with `retrieval_query_template` alone; naive training would let the model learn template provenance instead of comment content. Must deliberately rebalance toward recap-context positives before training, not after.

