"""
One-off diagnostic: does Bluesky have enough per-game discussion volume to
serve as the Phase 6 fan-discussion source? See spec > Fan Discussion Sourcing.

NOT wired into the ingestion pipeline. Prints a distinct post count and a
small sample so volume can be eyeballed alongside the raw count.

Auth uses BSKY_IDENTIFIER / BSKY_APP_PASSWORD from backend/.env, loaded via
the same Settings pattern as JWT_SECRET / DATABASE_URL (app/config.py).

Run from backend/:
    python scripts/check_bluesky_volume.py \
        --query "Knicks referee" \
        --since 2024-11-01T00:00:00Z \
        --until 2024-11-02T06:00:00Z
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

CREATE_SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
SEARCH_POSTS_URL = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"

PAGE_LIMIT = 100  # max allowed by searchPosts
MAX_PAGES = 200   # hard cap so a broken cursor can't loop forever


def _http_json(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from {req.full_url}\n{body}", file=sys.stderr)
        raise


def create_session(identifier: str, app_password: str) -> str:
    body = json.dumps({"identifier": identifier, "password": app_password}).encode()
    req = urllib.request.Request(
        CREATE_SESSION_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _http_json(req)["accessJwt"]


def search_posts(
    access_jwt: str, query: str, since: str, until: str
) -> list[dict]:
    """Paginate searchPosts for [since, until], de-dup by post uri."""
    seen: dict[str, dict] = {}
    cursor: str | None = None
    for page in range(MAX_PAGES):
        params = {
            "q": query,
            "since": since,
            "until": until,
            "limit": str(PAGE_LIMIT),
            "sort": "latest",
        }
        if cursor:
            params["cursor"] = cursor
        url = SEARCH_POSTS_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {access_jwt}"}
        )
        data = _http_json(req)
        posts = data.get("posts", [])
        for p in posts:
            uri = p.get("uri")
            if uri and uri not in seen:
                seen[uri] = p
        cursor = data.get("cursor")
        # Stop when the server stops handing back a cursor or a page is empty.
        if not cursor or not posts:
            break
        # Be polite; searchPosts is cheap but we're paging aggressively.
        time.sleep(0.2)
    else:
        print(f"WARNING: hit MAX_PAGES={MAX_PAGES}, cursor may still have more",
              file=sys.stderr)
    return list(seen.values())


def print_sample(posts: list[dict], n: int = 10) -> None:
    """Print ~n posts: truncated text, like/reply counts, timestamp."""
    if not posts:
        print("  (no posts to sample)")
        return
    # Sort by likes desc so the sample surfaces posts worth eyeballing, not
    # just the newest N. Fall back to created_at for stability.
    def sort_key(p: dict):
        rec = p.get("record", {}) or {}
        return (p.get("likeCount", 0), rec.get("createdAt", ""))

    for i, p in enumerate(sorted(posts, key=sort_key, reverse=True)[:n], 1):
        rec = p.get("record", {}) or {}
        text = (rec.get("text") or "").replace("\n", " ")
        if len(text) > 160:
            text = text[:157] + "..."
        author = (p.get("author") or {}).get("handle", "?")
        print(
            f"  [{i:2d}] {rec.get('createdAt', '?'):<28}  "
            f"likes={p.get('likeCount', 0):<4} "
            f"replies={p.get('replyCount', 0):<4} "
            f"reposts={p.get('repostCount', 0):<4} "
            f"@{author}"
        )
        print(f"        {text}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True,
                    help='searchPosts q (e.g. "Knicks referee")')
    ap.add_argument("--since", required=True,
                    help="ISO8601 UTC, e.g. 2024-11-01T00:00:00Z")
    ap.add_argument("--until", required=True,
                    help="ISO8601 UTC, e.g. 2024-11-02T06:00:00Z")
    args = ap.parse_args()

    if not settings.bsky_identifier or not settings.bsky_app_password:
        print(
            "ERROR: BSKY_IDENTIFIER and BSKY_APP_PASSWORD must be set in "
            "backend/.env (see .env.example).",
            file=sys.stderr,
        )
        return 2

    print(f"Authenticating as {settings.bsky_identifier} ...")
    jwt = create_session(settings.bsky_identifier, settings.bsky_app_password)

    print(f'Searching: q={args.query!r}  since={args.since}  until={args.until}')
    posts = search_posts(jwt, args.query, args.since, args.until)

    print()
    print("=" * 72)
    print(f"DISTINCT POSTS: {len(posts)}")
    print("=" * 72)
    print_sample(posts, n=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
