#!/usr/bin/env python3
"""Generate assets/cover.svg for the profile README from live GitHub data.

Everything the cover shows — lifetime commits, opened PRs, repository and
follower counts, years on GitHub and the three pinned projects — is pulled
from the GitHub GraphQL API and rendered into a self-contained SVG.

Auth: pass --token, or set GITHUB_TOKEN / GH_TOKEN. A classic token needs
`read:user` (add `repo` to count commits in private repositories); the
workflow's built-in token is enough for public numbers.

Usage:
    GITHUB_TOKEN=$(gh auth token) python3 scripts/gen_cover.py
    python3 scripts/gen_cover.py --user jtprogru --output assets/cover.svg
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any
from xml.sax.saxutils import escape

from ghlib import (
    ACCENT,
    SEP,
    CARD,
    DIM,
    FONT_MONO,
    FONT_SANS,
    GREEN,
    LINE,
    LINE_SOFT,
    MUTED,
    TEXT,
    YELLOW,
    api,
    ellipsize,
    emit,
    dot_join,
    mono_width,
    now_utc,
    shell,
    thousands,
    token_from_env,
    wrap,
)

# Layout constants of the cover. Change them here, not in the rendered SVG.
WIDTH = 1200
HEIGHT = 420
CARD_X = 660
CARD_W = 476
CARD_H = 92
CARD_Y = (92, 196, 300)  # top edge of each pinned card
STAT_X = (64, 194, 334, 444)  # left edge of each stat column
META_RIGHT = 1116  # right edge of the "<language> ★ <stars>" line
META_GAP = 28  # space between the language dot and its label
DESC_CHARS = 52  # characters that fit on one description line of a card
DESC_LINES = 2

DEFAULT_LANG_COLOR = MUTED  # for languages GitHub hands us no colour for

PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    createdAt
    followers { totalCount }
    repositories(ownerAffiliations: [OWNER]) { totalCount }
    pullRequests { totalCount }
    repositoriesContributedTo(
      contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]
      includeUserRepositories: true
    ) { totalCount }
    pinnedItems(first: 3, types: [REPOSITORY]) {
      nodes {
        ... on Repository {
          name
          description
          stargazerCount
          primaryLanguage { name color }
        }
      }
    }
  }
}
"""


def contributions_query(years: list[int]) -> str:
    """One aliased query per year: contributionsCollection covers 1 year max."""
    fields = "\n".join(
        f'    y{year}: contributionsCollection('
        f'from: "{year}-01-01T00:00:00Z", to: "{year}-12-31T23:59:59Z"'
        ") { totalCommitContributions restrictedContributionsCount }"
        for year in years
    )
    return "query($login: String!) {\n  user(login: $login) {\n" + fields + "\n  }\n}"


def collect(login: str, token: str, include_private: bool) -> dict[str, Any]:
    user = api(PROFILE_QUERY, {"login": login}, token)["user"]
    if user is None:
        sys.exit(f"user {login} not found")

    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = now_utc()
    years = list(range(created.year, now.year + 1))

    commits = 0
    # 20 aliases per request keeps the query under GitHub's node limits.
    for chunk_start in range(0, len(years), 20):
        chunk = years[chunk_start : chunk_start + 20]
        data = api(contributions_query(chunk), {"login": login}, token)["user"]
        for year in chunk:
            bucket = data[f"y{year}"]
            commits += bucket["totalCommitContributions"]
            if include_private:
                commits += bucket["restrictedContributionsCount"]

    # Whole years, not a difference of year numbers: an account opened in
    # December must not gain a year on 1 January.
    tenure = now.year - created.year - ((now.month, now.day) < (created.month, created.day))

    pinned = []
    for node in user["pinnedItems"]["nodes"]:
        language = node.get("primaryLanguage") or {}
        pinned.append(
            {
                "name": node["name"],
                "description": (node.get("description") or "").strip(),
                "stars": node["stargazerCount"],
                "language": language.get("name") or "—",
                "color": language.get("color") or DEFAULT_LANG_COLOR,
            }
        )

    return {
        "login": user["login"],
        "name": user["name"] or user["login"],
        "commits": commits,
        "prs": user["pullRequests"]["totalCount"],
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "contributed_to": user["repositoriesContributedTo"]["totalCount"],
        "years": tenure,
        "pinned": pinned,
        "generated_at": now.replace(microsecond=0).isoformat(),
    }


def card(index: int, repo: dict[str, Any]) -> str:
    top = CARD_Y[index]
    meta = f"{repo['language']}  ★ {repo['stars']}"
    # Right-aligned meta text, so the language dot is placed by measuring it.
    dot_x = META_RIGHT - mono_width(meta, 12) - META_GAP
    # A long repo name would otherwise run into that dot.
    name = ellipsize(repo["name"], dot_x - 704, 16)
    lines = wrap(repo["description"], DESC_CHARS, DESC_LINES)
    desc = "\n".join(
        f'      <text x="680" y="{top + 54 + i * 18}" font-family="{FONT_SANS}"'
        f' font-size="12.5" fill="{MUTED}">{escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    return f"""    <g>
      <rect x="{CARD_X}" y="{top}" width="{CARD_W}" height="{CARD_H}" rx="10" fill="{CARD}" stroke="{LINE}"/>
      <text x="680" y="{top + 30}" font-family="{FONT_MONO}" font-size="16" font-weight="700" fill="{ACCENT}">{escape(name)}</text>
{desc}
      <g text-anchor="end">
        <circle cx="{dot_x:.0f}" cy="{top + 26}" r="5" fill="{escape(repo['color'])}"/>
        <text x="{META_RIGHT}" y="{top + 30}" font-family="{FONT_MONO}" font-size="12" fill="{MUTED}">{escape(meta)}</text>
      </g>
    </g>"""


def stat(index: int, value: str, label: str) -> str:
    x = STAT_X[index]
    return f"""      <g>
        <text x="{x}" y="288" font-size="26" font-weight="700" fill="{TEXT}">{escape(value)}</text>
        <text x="{x}" y="308" font-size="11" fill="{MUTED}" letter-spacing="1">{escape(label)}</text>
      </g>"""


def render(data: dict[str, Any], tagline: str, handle_title: str, links: list[str]) -> str:
    pinned_names = ", ".join(repo["name"] for repo in data["pinned"])
    aria = (
        f"{data['name']} ({handle_title}), @{data['login']} — {tagline}."
        f" Pinned projects: {pinned_names}"
    )
    stats = "\n".join(
        [
            stat(0, thousands(data["commits"]), "COMMITS"),
            stat(1, thousands(data["prs"]), "PRs OPENED"),
            stat(2, thousands(data["repos"]), "REPOS"),
            stat(3, thousands(data["followers"]), "FOLLOWERS"),
        ]
    )
    cards = "\n\n".join(card(i, repo) for i, repo in enumerate(data["pinned"]))
    link_line = dot_join([escape(link) for link in links], LINE)
    footnote = (
        f"{data['years']} years on GitHub{SEP}"
        f"contributed to {data['contributed_to']} repositories"
    )

    body = f"""    <!-- ============ left column: identity ============ -->
    <text x="64" y="72" font-family="{FONT_MONO}" font-size="15" fill="{GREEN}">$ whoami</text>

    <text x="64" y="124" font-family="{FONT_SANS}" font-size="44" font-weight="700" fill="{TEXT}">{escape(data['name'])}</text>

    <text x="64" y="158" font-family="{FONT_SANS}" font-size="22" font-weight="600" fill="{YELLOW}">{escape(handle_title)}</text>

    <text x="64" y="190" font-family="{FONT_MONO}" font-size="16" fill="{ACCENT}">@{escape(data['login'])}</text>

    <text x="64" y="220" font-family="{FONT_SANS}" font-size="17" fill="{MUTED}">{escape(tagline)}</text>

    <line x1="64" y1="246" x2="560" y2="246" stroke="{LINE}"/>

    <!-- stats -->
    <g font-family="{FONT_MONO}">
{stats}
    </g>

    <text x="64" y="352" font-family="{FONT_MONO}" font-size="13" fill="{MUTED}">{link_line}</text>

    <text x="64" y="380" font-family="{FONT_MONO}" font-size="11" fill="{DIM}">{escape(footnote)}</text>

    <!-- ============ right column: pinned ============ -->
    <line x1="624" y1="48" x2="624" y2="372" stroke="{LINE_SOFT}"/>

    <text x="660" y="72" font-family="{FONT_MONO}" font-size="15" fill="{GREEN}">$ ls ~/pinned</text>

{cards}"""

    return shell(
        width=WIDTH,
        height=HEIGHT,
        aria=aria,
        generator="gen_cover.py",
        body=body,
        generated_at=data["generated_at"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--user", default="jtprogru", help="GitHub login (default: jtprogru)")
    parser.add_argument("--output", default="assets/cover.svg", help="where to write the SVG")
    parser.add_argument("--token", help="GitHub token (default: $GITHUB_TOKEN or $GH_TOKEN)")
    parser.add_argument("--title", default="Мишка на сервере", help="the yellow line under the name")
    parser.add_argument(
        "--tagline",
        default="SRE · Infrastructure Engineer · Cloud Engineer",
        help="the grey line under the handle",
    )
    parser.add_argument(
        "--link",
        action="append",
        dest="links",
        help="footer link, repeatable (default: jtprog.ru, savinmi.ru, t.me/jtprogru_channel)",
    )
    parser.add_argument(
        "--exclude-private",
        action="store_true",
        help="count only public commits (private ones are included by default)",
    )
    parser.add_argument("--check", action="store_true", help="exit 1 if the file would change")
    parser.add_argument("--print-stats", action="store_true", help="dump the collected numbers as JSON")
    args = parser.parse_args()

    token = token_from_env(args.token)
    links = args.links or ["jtprog.ru", "savinmi.ru", "t.me/jtprogru_channel"]
    data = collect(args.user, token, include_private=not args.exclude_private)
    if args.print_stats:
        print(json.dumps({k: v for k, v in data.items() if k != "pinned"}, ensure_ascii=False, indent=2))
    svg = render(data, tagline=args.tagline, handle_title=args.title, links=links)
    return emit(args.output, svg, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
