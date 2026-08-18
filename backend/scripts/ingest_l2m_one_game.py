"""
Ingest L2M reports for two hand-picked November 2024 games and print
every inserted row next to the raw source for manual verification.

Games:
  - 0022400044 (MIN 93, LAC 92 — 1 pt): all-CC/CNC "clean" game, baseline
  - 0022400002 (has 2 INCs): confirms IC/INC ratings and their surrounding
    fields (teamIdInFavor, errorInFavor) parse when a game actually has
    incorrect calls

Idempotent: existing l2m_reports for these game_ids are deleted first, and
the ON DELETE CASCADE on l2m_calls.l2m_report_id cleans up child rows.

Run from backend/:
    python scripts/ingest_l2m_one_game.py
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import L2MCall, L2MReport, Player

GAME_IDS = ["0022400044", "0022400002"]

HEADERS = {
    "Referer": "https://official.nba.com/",
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


# ---------- fetch ----------

def fetch_l2m(game_id: str) -> dict:
    req = urllib.request.Request(
        f"https://official.nba.com/l2m/json/{game_id}.json", headers=HEADERS
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


# ---------- resolve player name → id ----------

def resolve_player(session: Session, name: str | None) -> int | None:
    """Return players.id for an exact name match, or None."""
    if not name:
        return None
    return session.execute(
        select(Player.id).where(Player.name == name)
    ).scalar_one_or_none()


# ---------- ingest one game ----------

def ingest(
    session: Session, game_id: str
) -> tuple[L2MReport, list[tuple[dict, L2MCall]], dict]:
    payload = fetch_l2m(game_id)
    game_meta = payload["game"][0]
    raw_calls = payload["l2m"]

    # Idempotent: nuke any prior L2M for this game (cascade deletes calls).
    existing = session.execute(
        select(L2MReport).where(L2MReport.game_id == game_id)
    ).scalar_one_or_none()
    if existing is not None:
        session.delete(existing)
        session.flush()

    report = L2MReport(
        game_id=game_meta["GameId"],
        home_score=game_meta.get("HomeTeamScore"),
        away_score=game_meta.get("VisitorTeamScore"),
        published_at=datetime.now(timezone.utc),
    )
    session.add(report)
    session.flush()  # get report.id

    paired: list[tuple[dict, L2MCall]] = []
    for c in raw_calls:
        call = L2MCall(
            l2m_report_id=report.id,
            period=c["PeriodName"],
            pc_time=c["PCTime"],
            call_type=c["CallType"],
            call_rating=c.get("CallRatingName") or None,
            committing_player_name=c.get("CP") or "",
            disadvantaged_player_name=c.get("DP") or "",
            committing_player_id=resolve_player(session, c.get("CP")),
            disadvantaged_player_id=resolve_player(session, c.get("DP")),
            pos_team_id=c["posTeamId"],
            team_id_in_favor=c.get("teamIdInFavor"),
            nba_comment=c.get("Comment") or "",
            video_link_id=str(c["VideolLink"]) if c.get("VideolLink") else None,
        )
        session.add(call)
        paired.append((c, call))

    session.flush()
    return report, paired, game_meta


# ---------- pretty-print raw-vs-ingested ----------

def print_report_header(report: L2MReport, game_meta: dict, n_calls: int):
    print("\n" + "=" * 90)
    print(f"L2M REPORT  game_id={report.game_id}  "
          f"{game_meta['Away_team_abbr']} @ {game_meta['Home_team_abbr']}  "
          f"{game_meta['GameDateOut']}")
    print(f"  final: home={report.home_score}  away={report.away_score}")
    print(f"  l2m_reports.id = {report.id}   ingested {n_calls} calls")
    print("=" * 90)


def print_call(i: int, total: int, raw: dict, call: L2MCall):
    print(f"\n--- call {i}/{total}  (l2m_calls.id={call.id}) ---")
    print(
        "  RAW:  "
        f"period={raw['PeriodName']!r} pc={raw['PCTime']!r}  "
        f"type={raw['CallType']!r} rating={raw['CallRatingName']!r}"
    )
    print(
        f"        CP={raw.get('CP')!r}  DP={raw.get('DP')!r}  "
        f"posTeamId={raw.get('posTeamId')}  teamIdInFavor={raw.get('teamIdInFavor')!r}  "
        f"errorInFavor={raw.get('errorInFavor')!r}  VideolLink={raw.get('VideolLink')!r}"
    )
    print(f"        comment: {(raw.get('Comment') or '').strip()[:110]}")
    print(
        "  INS:  "
        f"period={call.period!r} pc={call.pc_time!r}  "
        f"type={call.call_type!r} rating={call.call_rating!r}"
    )
    print(
        f"        committing_player_name={call.committing_player_name!r} → id={call.committing_player_id}   "
        f"disadvantaged_player_name={call.disadvantaged_player_name!r} → id={call.disadvantaged_player_id}"
    )
    print(
        f"        pos_team_id={call.pos_team_id}  team_id_in_favor={call.team_id_in_favor}  "
        f"video_link_id={call.video_link_id!r}"
    )
    print(f"        nba_comment: {call.nba_comment.strip()[:110]}")


def main():
    with SessionLocal() as session:
        for gid in GAME_IDS:
            report, paired, game_meta = ingest(session, gid)
            session.commit()

            print_report_header(report, game_meta, len(paired))
            for i, (raw, call) in enumerate(paired, 1):
                print_call(i, len(paired), raw, call)


if __name__ == "__main__":
    main()
