"""
Throwaway: fetch one real game's L2M report as raw JSON and pretty-print it.

Game picked: 0022400044 — Timberwolves 93, Clippers 92 (2024-11-29). Chosen
by scanning our ingested November data for the smallest final margin (1 pt),
which is a reliable proxy for "score was close late" and therefore has L2M
coverage.

Run from backend/:
    python scripts/explore_l2m_one_game.py
"""

import json
import sys
import urllib.request

GAME_ID = "0022400044"
URL = f"https://official.nba.com/l2m/json/{GAME_ID}.json"

# The endpoint rejects requests without a valid Referer + User-Agent.
HEADERS = {
    "Referer": "https://official.nba.com/",
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


def main() -> None:
    print(f"GET {URL}", file=sys.stderr)
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    data = json.loads(raw)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
