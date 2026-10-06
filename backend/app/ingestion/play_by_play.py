"""
Fetch + persist game_events (foul rows only) from nba_api PlayByPlayV3.

Flow per game:
  1. Fetch PlayByPlayV3 (one API call per game).
  2. Keep only rows where actionType=='Foul'. Confirmed empirically via
     scripts/explore_pbp_fouls.py that technicals/flagrants come through as
     Foul rows with subType in {Technical, Delay Technical, Double Technical,
     Flagrant Type 1} — so filtering on actionType alone captures them.
  3. Regex-parse the calling referee's name from the free-text `description`
     — in PBP V3 the ref name is the final parenthesized group of the
     description, format `(F.LastName)` (e.g. `(J.Orr)`, `(T.Brothers)`).
     Occasionally missing entirely (e.g. "WARRIORS Delay" for a bench
     technical) — those rows persist with called_by_ref_id = NULL and get
     logged as unmatched.
  4. Resolve the parsed name against ONLY that game's crew (from
     game_officials), not the full referees table. If exactly one crew
     member matches, set called_by_ref_id. Zero or multiple matches →
     NULL + logged, per the "log unmatched cases rather than assuming the
     parse always succeeds" direction from the spec's game_events note.

Idempotent via UNIQUE(game_id, action_number) + ON CONFLICT DO NOTHING.
Caller is responsible for committing (per-game, per the nba_api pacing +
partial-progress-survives-failure pattern established in ingest_one_day.py).
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from nba_api.stats.endpoints import playbyplayv3
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import GameEvent, GameOfficial, Referee

API_SLEEP_SEC = 0.6  # same pacing as ingest_one_day.py / ingest_month.py

# Last parenthesized group at end of description, only when it looks like a
# ref name — must start with a letter and contain only letters, dots, spaces,
# apostrophes, hyphens. This excludes the foul-count group like `(P1.T1)` or
# `(P2.PN)` because those contain digits.
_REF_PAREN = re.compile(r"\(([A-Za-z][A-Za-z.\s'-]*)\)\s*$")

# Captures the leading "F." / "F.T." / "F. T." initials block from a parsed
# ref token; the rest is the surname. Multi-token surnames ("Van Duyne",
# "De La Rosa") stay intact because the pattern only greedily eats initial
# letters followed by a dot (optionally followed by a space).
_INITIALS_PREFIX = re.compile(r"^((?:[A-Za-z]\.\s?)*)(.+)$")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedRef:
    """`(J.T.Orr)` → initials=\"JT\" last=\"Orr\"; `(Brothers)` → initials=\"\" last=\"Brothers\"."""
    raw: str
    initials: str  # upper-cased letters, no dots
    last: str


@dataclass(frozen=True)
class _CrewMember:
    id: int
    name: str
    initials: str  # upper-cased, e.g. "JT" for "J.T. Orr", "T" for "Tony Brothers"
    last: str      # lower-cased surname


# ---------- parsing ----------

def _crew_initials(name: str) -> tuple[str, str]:
    """("J.T. Orr") -> ("JT", "orr"). ("Tony Brothers") -> ("T", "brothers").
    ("Justin Van Duyne") -> ("J", "van duyne") — only the FIRST space-separated
    token is treated as the given name; everything after is the surname, so
    multi-token surnames like "Van Duyne" stay intact for comparison against
    the broadcast format "J.Van Duyne".
    """
    tokens = name.strip().split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return "", tokens[0].lower()
    first = tokens[0]
    last = " ".join(tokens[1:]).lower()
    # Dotted first names ("J.T.") → "JT"; whole first names ("Tony") → "T".
    first_clean = first.replace(".", "")
    initials = first_clean.upper()
    return initials, last


def parse_ref_token(description: str) -> ParsedRef | None:
    """Pull "F.LastName" style ref token off the end of a PBP description.

    Returns None if the description has no trailing ref-name paren
    (e.g. "WARRIORS Delay"). The surname can be multi-token
    ("J.Van Duyne" → initials="J", last="Van Duyne"); initials can be
    multi-letter ("J.T.Orr" → initials="JT", last="Orr").
    """
    m = _REF_PAREN.search(description)
    if not m:
        return None
    raw = m.group(1).strip()
    im = _INITIALS_PREFIX.match(raw)
    if not im:
        return None
    initials_part, last = im.group(1), im.group(2).strip()
    initials = "".join(c for c in initials_part if c.isalpha()).upper()
    if not last:
        return None
    return ParsedRef(raw=raw, initials=initials, last=last.lower())


def resolve_against_crew(
    parsed: ParsedRef, crew: list[_CrewMember]
) -> tuple[int | None, str]:
    """Return (ref_id, status). status is one of {"matched","ambiguous","unmatched"}."""
    last_lower = parsed.last.lower()
    candidates = [m for m in crew if m.last == last_lower]
    if not candidates:
        return None, "unmatched"
    if parsed.initials:
        # Keep crew members whose initials start with the broadcast initials.
        # Broadcast format is single-initial for every real-world case seen,
        # but allowing a prefix means "(J.T.Orr)" matches "J.T. Orr" too.
        narrowed = [m for m in candidates if m.initials.startswith(parsed.initials)]
        if narrowed:
            candidates = narrowed
    if len(candidates) == 1:
        return candidates[0].id, "matched"
    return None, "ambiguous"


# ---------- fetch + persist ----------

def _load_crew(session: Session, game_id: str) -> list[_CrewMember]:
    rows = session.execute(
        select(Referee.id, Referee.name)
        .join(GameOfficial, GameOfficial.referee_id == Referee.id)
        .where(GameOfficial.game_id == game_id)
    ).all()
    members: list[_CrewMember] = []
    for rid, rname in rows:
        initials, last = _crew_initials(rname)
        members.append(_CrewMember(id=rid, name=rname, initials=initials, last=last))
    return members


def _fetch_fouls(game_id: str):
    """Return the foul-row subset of PlayByPlayV3 as a DataFrame."""
    ep = playbyplayv3.PlayByPlayV3(game_id=game_id)
    df = ep.get_data_frames()[0]
    return df[df["actionType"].str.contains("foul", case=False, na=False)].copy()


@dataclass
class IngestStats:
    foul_rows: int
    matched: int
    ambiguous: int
    unmatched: int
    inserted: int
    skipped: int
    unmatched_samples: list[tuple[int, str]]  # (action_number, description)
    ambiguous_samples: list[tuple[int, str]]


def ingest_game_events(
    session: Session, game_id: str, logger: logging.Logger | None = None
) -> IngestStats:
    """Fetch PBP fouls for one game, resolve ref names, upsert into game_events.

    Caller must commit after this returns.
    """
    logger = logger or log
    crew = _load_crew(session, game_id)
    if not crew:
        raise LookupError(
            f"game_id={game_id!r} has no game_officials rows — ingest the game's "
            f"box score first (backend/scripts/ingest_one_day.py)."
        )

    fouls = _fetch_fouls(game_id)
    matched = ambiguous = unmatched = 0
    unmatched_samples: list[tuple[int, str]] = []
    ambiguous_samples: list[tuple[int, str]] = []
    rows: list[dict] = []
    for _, r in fouls.iterrows():
        description = str(r.get("description") or "")
        action_number = int(r["actionNumber"])
        parsed = parse_ref_token(description)
        if parsed is None:
            unmatched += 1
            if len(unmatched_samples) < 20:
                unmatched_samples.append((action_number, description))
            logger.info(
                "ref-parse: NO_PAREN game=%s action=%s desc=%r",
                game_id, action_number, description,
            )
            called_by_ref_id = None
        else:
            called_by_ref_id, status = resolve_against_crew(parsed, crew)
            if status == "matched":
                matched += 1
            elif status == "ambiguous":
                ambiguous += 1
                if len(ambiguous_samples) < 20:
                    ambiguous_samples.append((action_number, description))
                logger.info(
                    "ref-parse: AMBIGUOUS game=%s action=%s parsed=%r desc=%r",
                    game_id, action_number, parsed.raw, description,
                )
            else:
                unmatched += 1
                if len(unmatched_samples) < 20:
                    unmatched_samples.append((action_number, description))
                logger.info(
                    "ref-parse: UNMATCHED game=%s action=%s parsed=%r desc=%r",
                    game_id, action_number, parsed.raw, description,
                )

        team_id_raw = r.get("teamId")
        person_id_raw = r.get("personId")
        rows.append({
            "game_id": game_id,
            "action_number": action_number,
            "period": int(r["period"]),
            "clock": str(r.get("clock") or ""),
            "team_id": int(team_id_raw) if team_id_raw else None,
            "person_id": int(person_id_raw) if person_id_raw else None,
            "action_type": str(r.get("actionType") or ""),
            "sub_type": (str(r["subType"]) if r.get("subType") else None),
            "called_by_ref_id": called_by_ref_id,
            "description": description,
        })

    inserted = 0
    if rows:
        stmt = (
            pg_insert(GameEvent)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["game_id", "action_number"],
            )
            .returning(GameEvent.id)
        )
        inserted = len(session.execute(stmt).scalars().all())

    return IngestStats(
        foul_rows=len(rows),
        matched=matched,
        ambiguous=ambiguous,
        unmatched=unmatched,
        inserted=inserted,
        skipped=len(rows) - inserted,
        unmatched_samples=unmatched_samples,
        ambiguous_samples=ambiguous_samples,
    )


# ---------- throwaway CLI demo ----------

def _demo(game_id: str) -> None:
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SessionLocal() as session:
        stats = ingest_game_events(session, game_id)
        session.commit()
    print(f"\n{game_id}: {stats.foul_rows} fouls, matched={stats.matched} "
          f"unmatched={stats.unmatched} ambiguous={stats.ambiguous} "
          f"(inserted={stats.inserted}, skipped={stats.skipped})")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if len(sys.argv) != 2:
        print("usage: python -m app.ingestion.play_by_play <game_id>", file=sys.stderr)
        sys.exit(2)
    _demo(sys.argv[1])
    time.sleep(API_SLEEP_SEC)
