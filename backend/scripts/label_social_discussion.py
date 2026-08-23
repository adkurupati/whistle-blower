"""
Weak-label every `social_discussion` row as 1 (likely officiating-related)
or 0 (probably not). Read-only — labels are NOT persisted; this exists to
generate the training-target signal for tomorrow's PyTorch triage
classifier and to sanity-check the rule's behavior before it does.

Rule (spec: Phase 6 weak-labeling pass):
    label = 1 IF ANY of:
      - retrieval_query_template is anything other than a "recap"-style template
      - comment_text case-insensitive match on: ref, referee, foul, call,
        no-call, whistle, technical, ejected, flagrant, blown call
        (matched with word boundaries + common plural/tense variants so
         "reference" / "callback" / "reflect" don't overcount)
      - comment_text contains an official's full name for that game_id
        (from game_officials JOIN referees)
    label = 0 otherwise.

Run from backend/:
    python scripts/label_social_discussion.py
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import GameOfficial, Referee, SocialDiscussion


# Word-boundary regex covering the keyword list + common variants. Deliberately
# includes plural/tense forms (refs, fouled, calls, whistled, ejects, flagrants)
# so bare-stem matching doesn't miss the obvious variants, but keeps \b on both
# sides so "reference", "callback", "reflect", "technicality" don't score.
KEYWORDS_RE = re.compile(
    r"\b(?:"
    r"refs?|referees?|refereed|refereeing"
    r"|fouls?|fouled|fouling"
    r"|calls?|called|calling|no[-\s]?calls?"
    r"|whistles?|whistled|whistling"
    r"|technicals?|tech"
    r"|ejects?|ejected|ejection"
    r"|flagrants?"
    r"|blown[-\s]calls?"
    r")\b",
    re.IGNORECASE,
)


def is_recap_template(template: str | None) -> bool:
    return bool(template) and "recap" in template.lower()


def load_officials_by_game(session) -> dict[str, list[str]]:
    """game_id -> [official_full_name, ...] for the crew that worked that game."""
    out: dict[str, list[str]] = defaultdict(list)
    for row in session.execute(
        select(GameOfficial.game_id, Referee.name).join(
            Referee, Referee.id == GameOfficial.referee_id
        )
    ):
        out[row.game_id].append(row.name)
    return dict(out)


def classify(row, officials_by_game: dict[str, list[str]]) -> tuple[int, str]:
    """Return (label, reason) — reason is the first rule that fired, useful for the report."""
    template = row.retrieval_query_template
    if not is_recap_template(template):
        return 1, "non-recap template"

    text = row.comment_text or ""
    if KEYWORDS_RE.search(text):
        return 1, "keyword"

    text_lower = text.lower()
    for name in officials_by_game.get(row.game_id, ()):
        if name.lower() in text_lower:
            return 1, "official-name"

    return 0, "no-match"


def print_dist(title: str, groups: dict) -> None:
    print("\n" + title)
    print("-" * len(title))
    header = f"{'key':<60}  {'total':>7}  {'label_1':>7}  {'label_0':>7}  {'%_1':>6}"
    print(header)
    for key, counts in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
        total = counts["total"]
        ones = counts["ones"]
        zeros = total - ones
        pct = (100.0 * ones / total) if total else 0.0
        print(f"{str(key)[:60]:<60}  {total:>7}  {ones:>7}  {zeros:>7}  {pct:>5.1f}%")


def main() -> int:
    with SessionLocal() as session:
        officials_by_game = load_officials_by_game(session)
        rows = list(session.execute(select(SocialDiscussion)).scalars())

    if not rows:
        print("social_discussion is empty — nothing to label.")
        return 0

    by_reason: dict[str, int] = defaultdict(int)
    by_game: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "ones": 0})
    by_template: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "ones": 0})

    total = 0
    ones = 0
    for r in rows:
        label, reason = classify(r, officials_by_game)
        total += 1
        ones += label
        by_reason[reason] += 1
        g = by_game[r.game_id]; g["total"] += 1; g["ones"] += label
        t = by_template[r.retrieval_query_template or "<null>"]
        t["total"] += 1; t["ones"] += label

    zeros = total - ones
    pct_1 = 100.0 * ones / total
    print("=" * 78)
    print(f"OVERALL  rows={total}  label_1={ones} ({pct_1:.1f}%)  "
          f"label_0={zeros} ({100 - pct_1:.1f}%)")
    print("=" * 78)

    print("\nWhich rule fired (mutually exclusive — first-match wins):")
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<25}  {n:>7}  ({100.0 * n / total:.1f}%)")

    print_dist("Per game", by_game)
    print_dist("Per retrieval_query_template", by_template)

    return 0


if __name__ == "__main__":
    sys.exit(main())
