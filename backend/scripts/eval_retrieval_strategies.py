"""
Bounded, non-production experiment: can play-level retrieval be rescued, or
does the YouTube corpus simply not contain play-level discussion?

Scope: the 15 games with social_discussion + game_events + l2m_reports.

Steps
-----
1. Build play set:
     - every l2m_calls row rated IC or INC
     - every technical/flagrant in game_events for those games
     - 10 random CC calls (fixed seed) for contrast
2. Keyword recall ceiling: scan ALL comments for the game (not just triage-
   positive, not top-k) for each play with (player surname AND call term) OR
   (ref surname + call term). The fraction of plays with ANY candidate is
   the ceiling retrieval can hope to hit.
3. Strategy comparison, top-5 each:
     A  baseline — current build_query_from_* text, retrieve_for_play
                   (is_relevant=True filter, max 3 per video)
     B  rule rewrite — short natural-language query from structured fields
                       (no raw PBP codes, no raw NBA review text)
     C  LLM rewrite — Ollama produces 2-3 fan-style queries, retrieve each,
                      merge by reciprocal-rank fusion. SKIPPED and labeled
                      if Ollama is unavailable; this script never installs it.
     D  hybrid — Postgres keyword gate on the game's comments, then semantic
                 rank within the survivors (bypasses is_relevant triage gate)
4. Judge each top-5 comment deterministically against the play's own
   surnames / call terms / ref name:
     on-play                 — surname AND call-term, or ref AND call-term,
                               or ref AND surname
     game-officiating-only   — generic ref/foul/call word, but no play-specific
                               tie
     unrelated               — neither
   flagged_unsure=True when the call-term is a generic foul-word only
   ("foul"/"call" without a more specific term) — the heuristic can
   false-positive on games with multiple same-player plays, so these are
   worth spot-checking by hand.

Outputs
-------
- stdout: counts, ceiling, precision@5 per strategy (overall + per category),
  final recommendation
- backend/data/retrieval_eval_judgments.json: every judged top-5 comment with
  its match signals, label, and reason — so judgments can be spot-checked.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from qdrant_client.http import models as qm
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ml.triage_inference import embed_query, embed_comments
from app.models import (
    GameEvent, L2MCall, L2MReport, Player, Referee, SocialDiscussion,
)
from app.retrieval.qdrant import COLLECTION, get_client
from app.retrieval.retrieve import (
    build_query_from_game_event, build_query_from_l2m_call,
    retrieve_for_play, RetrievedComment,
)

CC_SAMPLE_N = 10
CC_SEED = 20261006
TOP_K = 5
MAX_PER_VIDEO = 3


# ---------- play representation ----------

@dataclass
class Play:
    play_id: str                  # synthetic unique id for this eval
    game_id: str
    category: str                 # IC / INC / CC / TECHNICAL
    source_table: str             # l2m_calls / game_events
    source_pk: int
    description: str              # free-text summary (not used as a query)
    call_type: str                # e.g. "Foul: Shooting", "Technical"
    period: str                   # Q4 / "Period 2"
    pc_time: str                  # "01:23.4" / "PT08M44.00S"
    players: list[str]            # readable names (not team names)
    ref_name: str | None          # when known
    # Extracted search terms:
    player_surnames: list[str]    # lowercased last-tokens of players
    ref_surname: str | None
    call_terms: list[str]         # specific terms for this call type
    is_generic_call_term_only: bool  # for flagged_unsure


# ---------- call-term lookup ----------

# All lowercased, word-boundary-matched. The idea: a comment that says
# "travel" plus "Barrett" is plausibly about an RJ Barrett travel. "foul"
# alone is too generic — mark the whole play's call_terms as generic-only so
# downstream judgment flags on-play labels as unsure.

CALL_TERM_BUCKETS: dict[str, list[str]] = {
    # specific terms first — the keys are substring hints we look up against
    # call_type/sub_type (also lowercased)
    "travel": ["travel", "traveling", "walk", "walking", "walked"],
    "out-of-bounds": ["out of bounds", "oob", "out-of-bounds", "possession"],
    "shooting": ["shot", "shooting", "and 1", "and one", "ft", "free throw",
                 "ft line", "shooting foul"],
    "loose ball": ["loose ball", "scramble"],
    "offensive": ["offensive foul", "charge", "charging"],
    "defensive": ["blocking", "block foul", "reach", "ticky tack"],
    "personal": ["foul", "ticky tack"],
    "technical": ["tech", "technical", "t-foul", "ejected", "ejection"],
    "double technical": ["technical", "tech", "double tech"],
    "delay": ["delay"],
    "flagrant": ["flagrant"],
    "goaltending": ["goaltend", "goaltending"],
    "defensive 3-second": ["defensive 3", "defensive three", "3 second",
                            "three second"],
    "away from the play": ["away from the play"],
    "kicked ball": ["kick", "kicked ball"],
    "turnover": ["travel", "walk"],
    "stoppage": ["out of bounds", "oob", "possession"],
    "jump ball": ["jump ball"],
}

GENERIC_OFFICIATING = [
    "ref", "refs", "referee", "referees", "foul", "no call", "no-call",
    "nocall", "whistle", "called", "miss call", "missed call", "blown call",
    "bad call", "good call", "home cooking", "rigged",
]

# Specific markers — if ANY of these appear, the call_term is NOT just
# generic. "foul" / "call" alone are generic. Everything else is specific.
_GENERIC_ONLY_TERMS = {"foul", "call", "called"}


def _call_terms_for(call_type: str, sub_type: str | None) -> tuple[list[str], bool]:
    """Return (terms, is_generic_only). is_generic_only=True when the only
    terms we have for this play are "foul"/"call" (too noisy to firmly judge).
    """
    hay = f"{call_type or ''} {sub_type or ''}".lower()
    terms: list[str] = []
    for key, bucket in CALL_TERM_BUCKETS.items():
        if key in hay:
            terms.extend(bucket)
    # Dedup, preserve order.
    seen = set()
    terms = [t for t in terms if not (t in seen or seen.add(t))]
    if not terms:
        terms = ["foul", "call"]  # last-resort fallback
    specific = [t for t in terms if t not in _GENERIC_ONLY_TERMS]
    return terms, (not specific)


# ---------- play-set construction ----------

def _surname(full_name: str) -> str | None:
    if not full_name:
        return None
    toks = [t for t in full_name.strip().split() if t]
    if not toks:
        return None
    # Treat obvious team-name strings as "no surname". The l2m_calls docstring
    # notes CP/DP can be team names. Simple heuristic: all-caps strings or
    # tokens containing "Team" aren't player surnames.
    last = toks[-1]
    if full_name.isupper() or full_name.islower():
        # Teams in the l2m source come as mixed-case city/nickname forms, but
        # some rows have all-caps team names — skip those.
        if full_name.isupper():
            return None
    return last.lower()


def _player_surnames(cp: str, dp: str) -> list[str]:
    out: list[str] = []
    for n in (cp, dp):
        s = _surname(n or "")
        if s:
            out.append(s)
    return list(dict.fromkeys(out))


def build_play_set(session: Session, overlap_games: list[str]) -> list[Play]:
    plays: list[Play] = []

    # IC / INC L2M calls
    rows = session.execute(
        select(L2MCall, L2MReport.game_id)
        .join(L2MReport, L2MReport.id == L2MCall.l2m_report_id)
        .where(L2MReport.game_id.in_(overlap_games))
        .where(L2MCall.call_rating.in_(("IC", "INC")))
        .order_by(L2MReport.game_id, L2MCall.id)
    ).all()
    for call, gid in rows:
        surnames = _player_surnames(call.committing_player_name,
                                    call.disadvantaged_player_name)
        terms, generic_only = _call_terms_for(call.call_type, None)
        plays.append(Play(
            play_id=f"l2m-{call.id}",
            game_id=gid,
            category=call.call_rating,
            source_table="l2m_calls",
            source_pk=call.id,
            description=(f"{call.committing_player_name} on "
                         f"{call.disadvantaged_player_name}"),
            call_type=call.call_type,
            period=call.period,
            pc_time=call.pc_time,
            players=[n for n in (call.committing_player_name,
                                 call.disadvantaged_player_name) if n],
            ref_name=None,
            player_surnames=surnames,
            ref_surname=None,
            call_terms=terms,
            is_generic_call_term_only=generic_only,
        ))

    # 10 CC calls, fixed seed
    cc_rows = session.execute(
        select(L2MCall, L2MReport.game_id)
        .join(L2MReport, L2MReport.id == L2MCall.l2m_report_id)
        .where(L2MReport.game_id.in_(overlap_games))
        .where(L2MCall.call_rating == "CC")
        .order_by(L2MReport.game_id, L2MCall.id)
    ).all()
    rng = random.Random(CC_SEED)
    cc_sample = rng.sample(cc_rows, min(CC_SAMPLE_N, len(cc_rows)))
    for call, gid in cc_sample:
        surnames = _player_surnames(call.committing_player_name,
                                    call.disadvantaged_player_name)
        terms, generic_only = _call_terms_for(call.call_type, None)
        plays.append(Play(
            play_id=f"l2m-{call.id}",
            game_id=gid,
            category="CC",
            source_table="l2m_calls",
            source_pk=call.id,
            description=(f"{call.committing_player_name} on "
                         f"{call.disadvantaged_player_name}"),
            call_type=call.call_type,
            period=call.period,
            pc_time=call.pc_time,
            players=[n for n in (call.committing_player_name,
                                 call.disadvantaged_player_name) if n],
            ref_name=None,
            player_surnames=surnames,
            ref_surname=None,
            call_terms=terms,
            is_generic_call_term_only=generic_only,
        ))

    # Technicals / flagrants from game_events
    tech_rows = session.execute(
        select(GameEvent)
        .where(GameEvent.game_id.in_(overlap_games))
        .where(GameEvent.sub_type.in_(
            ("Technical", "Double Technical", "Delay Technical",
             "Flagrant Type 1", "Flagrant Type 2")
        ))
        .order_by(GameEvent.game_id, GameEvent.action_number)
    ).scalars().all()
    for ev in tech_rows:
        # Pull player (person_id) and ref (called_by_ref_id) names
        player_name = None
        if ev.person_id:
            player_name = session.execute(
                select(Player.name).where(Player.id == ev.person_id)
            ).scalar_one_or_none()
        ref_name = None
        if ev.called_by_ref_id:
            ref_name = session.execute(
                select(Referee.name).where(Referee.id == ev.called_by_ref_id)
            ).scalar_one_or_none()
        surnames = [s for s in [_surname(player_name or "")] if s]
        ref_sur = _surname(ref_name or "") if ref_name else None
        terms, generic_only = _call_terms_for(ev.action_type, ev.sub_type)
        plays.append(Play(
            play_id=f"evt-{ev.id}",
            game_id=ev.game_id,
            category="TECHNICAL",
            source_table="game_events",
            source_pk=ev.id,
            description=ev.description,
            call_type=f"{ev.action_type} ({ev.sub_type})",
            period=f"Period {ev.period}",
            pc_time=ev.clock,
            players=[player_name] if player_name else [],
            ref_name=ref_name,
            player_surnames=surnames,
            ref_surname=ref_sur,
            call_terms=terms,
            is_generic_call_term_only=generic_only,
        ))

    return plays


# ---------- query rewriters (for strategies A/B/C) ----------

def query_A_baseline(session: Session, play: Play) -> str:
    """Baseline: use the existing build_query_from_* unchanged."""
    if play.source_table == "l2m_calls":
        call = session.execute(
            select(L2MCall).where(L2MCall.id == play.source_pk)
        ).scalar_one()
        return build_query_from_l2m_call(session, call)
    else:
        ev = session.execute(
            select(GameEvent).where(GameEvent.id == play.source_pk)
        ).scalar_one()
        return build_query_from_game_event(session, ev)


def query_B_rule_rewrite(play: Play) -> str:
    """Rule-based rewrite — one short natural-language phrase. No raw PBP
    codes, no raw NBA review text, no ratings tag."""
    parts: list[str] = []
    if play.players:
        parts.append(play.players[0])
    # Call-type as a plain word.
    ct_hay = f"{play.call_type}".lower()
    label = None
    for key in ("travel", "flagrant", "technical", "offensive foul",
                "shooting", "defensive 3", "goaltend", "loose ball",
                "out-of-bounds", "out of bounds"):
        if key in ct_hay:
            label = key.replace("-", " ")
            break
    if label is None:
        label = "foul"
    parts.append(label)
    if len(play.players) >= 2 and play.players[1]:
        parts.append(f"on {play.players[1]}")
    if play.ref_name:
        parts.append(f"called by {play.ref_name}")
    return " ".join(parts)


# ---------- hybrid: keyword gate + semantic rank (strategy D) ----------

_WB = r"(?<![A-Za-z0-9])"  # not alnum before
_WB2 = r"(?![A-Za-z0-9])"  # not alnum after


def _comment_matches_keyword_gate(text: str, play: Play) -> bool:
    """Any play-related keyword in the comment: surname OR ref surname OR
    a non-generic call term."""
    t = text.lower()
    if play.ref_surname and re.search(_WB + re.escape(play.ref_surname) + _WB2, t):
        return True
    for s in play.player_surnames:
        if re.search(_WB + re.escape(s) + _WB2, t):
            return True
    for term in play.call_terms:
        if term in _GENERIC_ONLY_TERMS:
            continue
        # Use word-boundary for single words, plain substring for multi-word
        if " " in term:
            if term in t:
                return True
        else:
            if re.search(_WB + re.escape(term) + _WB2, t):
                return True
    return False


def strategy_D_hybrid(session: Session, play: Play, k: int = TOP_K,
                      max_per_video: int = MAX_PER_VIDEO) -> list[RetrievedComment]:
    """Postgres keyword gate on the game's comments, then semantic rank
    inside the surviving set using the rule-rewrite query. Bypasses the
    triage is_relevant filter — a legitimately play-specific comment may
    still score below 0.4."""
    all_rows = session.execute(
        select(SocialDiscussion)
        .where(SocialDiscussion.game_id == play.game_id)
    ).scalars().all()
    survivors = [r for r in all_rows if _comment_matches_keyword_gate(r.comment_text, play)]
    if not survivors:
        return []

    query_text = query_B_rule_rewrite(play)
    qvec = embed_query(query_text).astype(np.float32)
    # Normalize for cosine
    qn = qvec / (np.linalg.norm(qvec) + 1e-12)

    # Fetch the vectors from Qdrant in one call so we don't re-embed.
    ids = [int(r.id) for r in survivors]
    points = get_client().retrieve(
        collection_name=COLLECTION,
        ids=ids,
        with_vectors=True,
        with_payload=True,
    )
    by_id = {int(p.id): p for p in points}

    scored: list[tuple[float, SocialDiscussion, dict]] = []
    for r in survivors:
        p = by_id.get(int(r.id))
        if p is None or not p.vector:
            continue
        v = np.asarray(p.vector, dtype=np.float32)
        v = v / (np.linalg.norm(v) + 1e-12)
        scored.append((float(qn @ v), r, p.payload or {}))

    scored.sort(key=lambda x: -x[0])

    results: list[RetrievedComment] = []
    per_video: dict[str | None, int] = {}
    for score, row, payload in scored:
        vid = payload.get("video_id")
        if per_video.get(vid, 0) >= max_per_video:
            continue
        per_video[vid] = per_video.get(vid, 0) + 1
        results.append(RetrievedComment(
            social_discussion_id=int(row.id),
            score=score,
            comment_text=row.comment_text,
            game_id=row.game_id,
            video_id=vid,
            source_channel=payload.get("source_channel"),
            published_at=payload.get("published_at"),
            engagement_score=payload.get("engagement_score"),
            triage_prob=float(payload.get("triage_prob", 0.0)),
            retrieval_query_template=payload.get("retrieval_query_template"),
        ))
        if len(results) >= k:
            break
    return results


# ---------- A/B retrieval via the existing retriever ----------

def strategy_A_or_B(session: Session, play: Play, query_text: str,
                    k: int = TOP_K) -> list[RetrievedComment]:
    return retrieve_for_play(session, play.game_id, query_text, k=k,
                             max_per_video=MAX_PER_VIDEO)


# ---------- judgment ----------

@dataclass
class Judgment:
    label: str                 # on_play / officiating_only / unrelated
    reason: str
    flagged_unsure: bool
    surnames_hit: list[str]
    call_terms_hit: list[str]
    ref_hit: bool
    generic_officiating_hit: bool


def judge_comment(text: str, play: Play) -> Judgment:
    t = text.lower()
    surnames_hit: list[str] = []
    for s in play.player_surnames:
        if re.search(_WB + re.escape(s) + _WB2, t):
            surnames_hit.append(s)
    call_terms_hit: list[str] = []
    for term in play.call_terms:
        if " " in term:
            if term in t:
                call_terms_hit.append(term)
        else:
            if re.search(_WB + re.escape(term) + _WB2, t):
                call_terms_hit.append(term)
    ref_hit = bool(
        play.ref_surname
        and re.search(_WB + re.escape(play.ref_surname) + _WB2, t)
    )
    generic_hit = any(
        (re.search(_WB + re.escape(g) + _WB2, t) if " " not in g else g in t)
        for g in GENERIC_OFFICIATING
    )

    specific_call = [c for c in call_terms_hit if c not in _GENERIC_ONLY_TERMS]
    has_call = bool(call_terms_hit)
    has_specific_call = bool(specific_call)
    has_surname = bool(surnames_hit)

    reasons: list[str] = []
    label = "unrelated"
    flagged_unsure = False

    if has_surname and has_call:
        label = "on_play"
        reasons.append(f"surname({','.join(surnames_hit)})"
                       f"+call({','.join(call_terms_hit)})")
        if not has_specific_call:
            flagged_unsure = True
            reasons.append("generic_call_only")
    elif ref_hit and has_call:
        label = "on_play"
        reasons.append(f"ref({play.ref_surname})+call({','.join(call_terms_hit)})")
        if not has_specific_call:
            flagged_unsure = True
            reasons.append("generic_call_only")
    elif ref_hit and has_surname:
        label = "on_play"
        reasons.append(f"ref({play.ref_surname})+surname({','.join(surnames_hit)})")
    elif generic_hit:
        label = "officiating_only"
        if has_surname:
            reasons.append(f"surname({','.join(surnames_hit)})_without_call_tie")
        reasons.append("generic_officiating_lang")
    elif has_surname:
        label = "unrelated"
        reasons.append(f"surname({','.join(surnames_hit)})_no_officiating_lang")
    else:
        reasons.append("no_signals")

    # When play.call_terms are generic-only (just "foul"/"call"), any
    # on_play label on surname+call is particularly weak evidence — flag it.
    if label == "on_play" and play.is_generic_call_term_only and not has_specific_call:
        flagged_unsure = True

    return Judgment(
        label=label,
        reason=" ".join(reasons),
        flagged_unsure=flagged_unsure,
        surnames_hit=surnames_hit,
        call_terms_hit=call_terms_hit,
        ref_hit=ref_hit,
        generic_officiating_hit=generic_hit,
    )


# ---------- recall ceiling ----------

def recall_ceiling(session: Session, plays: list[Play]) -> dict:
    """For each play, scan every comment in its game. Count a play as having
    a candidate if ANY comment has (surname AND call-term) OR (ref AND
    call-term). Also report a looser "any surname mention" count for
    perspective."""
    comments_by_game: dict[str, list[tuple[int, str]]] = defaultdict(list)
    all_rows = session.execute(
        select(SocialDiscussion.game_id, SocialDiscussion.id,
               SocialDiscussion.comment_text)
    ).all()
    for gid, cid, text in all_rows:
        comments_by_game[gid].append((cid, text))

    per_cat_strict: dict[str, list[int]] = defaultdict(list)   # 1 if any candidate
    per_cat_loose: dict[str, list[int]] = defaultdict(list)    # 1 if any surname mention
    per_cat_counts: dict[str, list[int]] = defaultdict(list)   # per-play candidate counts
    for play in plays:
        strict = 0
        loose = 0
        for _, text in comments_by_game.get(play.game_id, []):
            j = judge_comment(text, play)
            if j.label == "on_play":
                strict += 1
            if j.surnames_hit:
                loose += 1
        per_cat_strict[play.category].append(1 if strict else 0)
        per_cat_loose[play.category].append(1 if loose else 0)
        per_cat_counts[play.category].append(strict)

    out = {}
    for cat in sorted(set(p.category for p in plays)):
        n = len(per_cat_strict[cat])
        hit = sum(per_cat_strict[cat])
        loose_hit = sum(per_cat_loose[cat])
        counts = per_cat_counts[cat]
        out[cat] = {
            "plays": n,
            "has_candidate_strict": hit,
            "has_candidate_strict_frac": (hit / n if n else 0.0),
            "has_surname_mention_loose": loose_hit,
            "has_surname_mention_loose_frac": (loose_hit / n if n else 0.0),
            "candidates_per_play_median": int(np.median(counts)) if counts else 0,
            "candidates_per_play_max": max(counts) if counts else 0,
        }
    return out


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/retrieval_eval_judgments.json",
                    help="path for the judgments JSON output "
                         "(relative to backend/)")
    args = ap.parse_args()

    out_path = Path(__file__).resolve().parent.parent / args.out

    with SessionLocal() as session:
        overlap_games = sorted(
            set(session.execute(select(distinct(GameEvent.game_id))).scalars())
            & set(session.execute(select(distinct(SocialDiscussion.game_id))).scalars())
            & set(session.execute(select(distinct(L2MReport.game_id))).scalars())
        )
        print(f"triple-overlap games: {len(overlap_games)}")

        plays = build_play_set(session, overlap_games)
        counts = defaultdict(int)
        for p in plays:
            counts[p.category] += 1
        print(f"\nplay set counts:")
        for c in sorted(counts):
            print(f"  {c:>10}  {counts[c]}")
        print(f"  {'TOTAL':>10}  {sum(counts.values())}")

        print("\n--- recall ceiling (keyword scan over ALL comments per game) ---")
        ceiling = recall_ceiling(session, plays)
        print(f"  {'cat':>10}  plays  strict_has/frac       surname_only_has/frac   med_candidates")
        for cat, row in ceiling.items():
            print(f"  {cat:>10}  {row['plays']:>5}  "
                  f"{row['has_candidate_strict']:>3}/{row['plays']:<3} "
                  f"({row['has_candidate_strict_frac']*100:5.1f}%)   "
                  f"{row['has_surname_mention_loose']:>3}/{row['plays']:<3} "
                  f"({row['has_surname_mention_loose_frac']*100:5.1f}%)   "
                  f"{row['candidates_per_play_median']:>3}")

        print("\n--- strategy comparison, top-5 each ---")
        # Strategy C: Ollama check — never auto-install.
        try:
            import subprocess
            r = subprocess.run(["ollama", "list"], capture_output=True, timeout=5)
            ollama_available = (r.returncode == 0)
        except Exception:  # noqa: BLE001
            ollama_available = False
        if not ollama_available:
            print("  (strategy C skipped — Ollama not installed/running; "
                  "not installing anything)")

        strategies = ["A", "B", "D"]  # C skipped

        # Precompute queries once per play for A/B/D
        all_results: dict[str, dict[str, list[RetrievedComment]]] = {
            s: {} for s in strategies
        }
        strategy_queries: dict[str, dict[str, str]] = {s: {} for s in strategies}

        t0 = time.time()
        for i, play in enumerate(plays, 1):
            qA = query_A_baseline(session, play)
            qB = query_B_rule_rewrite(play)
            strategy_queries["A"][play.play_id] = qA
            strategy_queries["B"][play.play_id] = qB
            strategy_queries["D"][play.play_id] = qB  # D uses the B query for ranking

            all_results["A"][play.play_id] = strategy_A_or_B(session, play, qA)
            all_results["B"][play.play_id] = strategy_A_or_B(session, play, qB)
            all_results["D"][play.play_id] = strategy_D_hybrid(session, play)
            if i % 10 == 0 or i == len(plays):
                print(f"  retrieved {i}/{len(plays)}  elapsed={time.time()-t0:.1f}s",
                      flush=True)

        # Judge each top-5
        judged = []
        prec_per_strategy: dict[str, dict[str, list[int]]] = {
            s: defaultdict(list) for s in strategies
        }  # per-category list of (on_play count) per play, out of 5
        for play in plays:
            for s in strategies:
                results = all_results[s][play.play_id]
                hits = 0
                per_comment = []
                for rank, rc in enumerate(results[:TOP_K], 1):
                    j = judge_comment(rc.comment_text, play)
                    if j.label == "on_play":
                        hits += 1
                    per_comment.append({
                        "rank": rank,
                        "score": rc.score,
                        "triage_prob": rc.triage_prob,
                        "social_discussion_id": rc.social_discussion_id,
                        "video_id": rc.video_id,
                        "source_channel": rc.source_channel,
                        "comment_text": rc.comment_text,
                        "label": j.label,
                        "reason": j.reason,
                        "flagged_unsure": j.flagged_unsure,
                        "surnames_hit": j.surnames_hit,
                        "call_terms_hit": j.call_terms_hit,
                        "ref_hit": j.ref_hit,
                        "generic_officiating_hit": j.generic_officiating_hit,
                    })
                prec_per_strategy[s][play.category].append(hits)
                judged.append({
                    "play_id": play.play_id,
                    "game_id": play.game_id,
                    "category": play.category,
                    "strategy": s,
                    "query_text": strategy_queries[s][play.play_id],
                    "play_description": play.description,
                    "play_call_type": play.call_type,
                    "play_period": play.period,
                    "play_pc_time": play.pc_time,
                    "players": play.players,
                    "ref_name": play.ref_name,
                    "player_surnames": play.player_surnames,
                    "ref_surname": play.ref_surname,
                    "call_terms": play.call_terms,
                    "is_generic_call_term_only": play.is_generic_call_term_only,
                    "results": per_comment,
                })

        # Save JSON
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump({
                "config": {
                    "top_k": TOP_K,
                    "max_per_video": MAX_PER_VIDEO,
                    "cc_sample_n": CC_SAMPLE_N,
                    "cc_seed": CC_SEED,
                    "ollama_strategy_C_available": ollama_available,
                },
                "overlap_games": overlap_games,
                "plays": [asdict(p) for p in plays],
                "ceiling": ceiling,
                "judgments": judged,
            }, f, indent=2, default=str)
        print(f"\nwrote {out_path} ({len(judged)} play-strategy rows)")

        # Precision tables
        print("\n--- precision@5 per strategy (on-play / 5) ---")
        print(f"  {'cat':>10}  {'A_base':>8}  {'B_rule':>8}  "
              f"{'C_llm':>8}  {'D_hybrid':>10}")
        cats = sorted(counts.keys())
        for cat in cats:
            row = f"  {cat:>10}  "
            for s in ["A", "B", "C", "D"]:
                if s == "C":
                    row += f"{'skip':>8}  "
                    continue
                hits = prec_per_strategy[s].get(cat, [])
                if not hits:
                    row += f"{'-':>8}  "
                    continue
                total_hits = sum(hits)
                total_rows = 5 * len(hits)
                pct = total_hits / total_rows * 100 if total_rows else 0
                tag = f"{total_hits}/{total_rows} ({pct:4.1f}%)"
                if s == "D":
                    row += f"{tag:>10}  "
                else:
                    row += f"{tag:>8}  "
            print(row)

        # Overall
        row = f"  {'OVERALL':>10}  "
        for s in ["A", "B", "C", "D"]:
            if s == "C":
                row += f"{'skip':>8}  "
                continue
            total_hits = sum(sum(v) for v in prec_per_strategy[s].values())
            total_rows = sum(5 * len(v) for v in prec_per_strategy[s].values())
            pct = total_hits / total_rows * 100 if total_rows else 0
            tag = f"{total_hits}/{total_rows} ({pct:4.1f}%)"
            if s == "D":
                row += f"{tag:>10}  "
            else:
                row += f"{tag:>8}  "
        print(row)

    return 0


if __name__ == "__main__":
    sys.exit(main())
