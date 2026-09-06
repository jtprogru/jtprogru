#!/usr/bin/env python3
"""Generate assets/metrics.svg — the panel under the cover in the README.

Replaces lowlighter/metrics: the contribution calendar for the last 12 months,
the activity breakdown next to it, the language mix across own repositories
and a totals line, all pulled from the GitHub GraphQL API.

Auth: pass --token, or set GITHUB_TOKEN / GH_TOKEN. A classic token needs
`read:user`; add `repo` to include private repositories in the language mix
and private commits in the activity numbers.

Usage:
    GITHUB_TOKEN=$(gh auth token) python3 scripts/gen_metrics.py
    python3 scripts/gen_metrics.py --user jtprogru --output assets/metrics.svg
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
    CARD,
    DIM,
    FONT_MONO,
    FONT_SANS,
    GREEN,
    LEVELS,
    LINE,
    LINE_SOFT,
    MUTED,
    TEXT,
    api,
    dot_join,
    ellipsize,
    emit,
    mono_width,
    now_utc,
    shell,
    thousands,
    token_from_env,
)

WIDTH = 1200
HEIGHT = 520

# Contribution calendar: 53 weeks by 7 days of 11px cells on a 14px pitch.
CAL_X = 100
CAL_Y = 152
CELL = 11
PITCH = 14
CAL_RIGHT = CAL_X + 53 * PITCH - (PITCH - CELL)

# Activity list to the right of the calendar.
SPLIT_X = 872  # vertical divider
ACT_X = 900  # labels
ACT_RIGHT = 1136  # values, right-aligned
ACT_Y = 150  # first row baseline
ACT_STEP = 26

# Language bar and its legend.
BAR_X = 64
BAR_RIGHT = 1136
BAR_Y = 336
BAR_H = 14
LEGEND_X = (64, 424, 784)  # three columns
LEGEND_Y = (390, 414, 438)  # three rows
LEGEND_W = 340

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}

DEFAULT_LANG_COLOR = MUTED
TOP_LANGUAGES = 8

ACTIVITY_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    login
    name
    followers { totalCount }
    gists { totalCount }
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks {
          firstDay
          contributionDays { weekday contributionCount contributionLevel }
        }
      }
    }
  }
}
"""

REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(
      first: 100
      after: $cursor
      isFork: false
      ownerAffiliations: [OWNER]
      orderBy: { field: STARGAZERS, direction: DESC }
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        forkCount
        languages(first: 12, orderBy: { field: SIZE, direction: DESC }) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


def collect(login: str, token: str, include_private: bool) -> dict[str, Any]:
    now = now_utc()
    frm = now - dt.timedelta(days=365)
    activity = api(
        ACTIVITY_QUERY,
        {
            "login": login,
            "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        token,
    )["user"]
    if activity is None:
        sys.exit(f"user {login} not found")

    contributions = activity["contributionsCollection"]
    commits = contributions["totalCommitContributions"]
    if include_private:
        commits += contributions["restrictedContributionsCount"]

    sizes: dict[str, int] = {}
    colors: dict[str, str] = {}
    stars = forks = 0
    cursor = None
    repo_total = 0
    for _ in range(12):  # 1200 repositories is well past any realistic profile
        page = api(REPOS_QUERY, {"login": login, "cursor": cursor}, token)["user"]["repositories"]
        repo_total = page["totalCount"]
        for repo in page["nodes"]:
            stars += repo["stargazerCount"]
            forks += repo["forkCount"]
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                sizes[name] = sizes.get(name, 0) + edge["size"]
                colors.setdefault(name, edge["node"].get("color") or DEFAULT_LANG_COLOR)
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    total_bytes = sum(sizes.values())
    ranked = sorted(sizes.items(), key=lambda item: item[1], reverse=True)
    languages = [
        {"name": name, "share": size / total_bytes * 100, "color": colors[name]}
        for name, size in ranked[:TOP_LANGUAGES]
    ]
    rest = sum(size for _, size in ranked[TOP_LANGUAGES:])
    if rest:
        languages.append({"name": "Other", "share": rest / total_bytes * 100, "color": LINE})

    return {
        "login": activity["login"],
        "name": activity["name"] or activity["login"],
        "from": frm.date().isoformat(),
        "to": now.date().isoformat(),
        "contributions": contributions["contributionCalendar"]["totalContributions"],
        "commits": commits,
        "prs": contributions["totalPullRequestContributions"],
        "reviews": contributions["totalPullRequestReviewContributions"],
        "issues": contributions["totalIssueContributions"],
        "active_repos": contributions["totalRepositoriesWithContributedCommits"],
        "weeks": contributions["contributionCalendar"]["weeks"],
        "repos": repo_total,
        "stars": stars,
        "forks": forks,
        "followers": activity["followers"]["totalCount"],
        "gists": activity["gists"]["totalCount"],
        "languages": languages,
        "language_count": len(sizes),
        "generated_at": now.replace(microsecond=0).isoformat(),
    }


def calendar(weeks: list[dict[str, Any]]) -> str:
    cells = []
    labels = []
    previous_month = None
    for week_index, week in enumerate(weeks):
        x = CAL_X + week_index * PITCH
        month = dt.date.fromisoformat(week["firstDay"]).month
        # Label a month once, on the first week that is mostly inside it.
        if month != previous_month and week_index < len(weeks) - 2:
            if previous_month is not None or week_index == 0:
                labels.append(
                    f'      <text x="{x}" y="136" font-family="{FONT_MONO}"'
                    f' font-size="10" fill="{DIM}">{MONTHS[month - 1]}</text>'
                )
            previous_month = month
        for day in week["contributionDays"]:
            y = CAL_Y + day["weekday"] * PITCH
            fill = LEVELS.get(day["contributionLevel"], LEVELS["NONE"])
            cells.append(
                f'      <rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2"'
                f' fill="{fill}"/>'
            )

    weekdays = "\n".join(
        f'      <text x="64" y="{CAL_Y + weekday * PITCH + 9}" font-family="{FONT_MONO}"'
        f' font-size="9" fill="{DIM}">{label}</text>'
        for weekday, label in WEEKDAY_LABELS.items()
    )

    # Swatches sit between the two labels: "Less" ends at -107, "More" starts at -24.
    swatch_x0 = CAL_RIGHT - 99
    swatches = "\n".join(
        f'      <rect x="{swatch_x0 + i * PITCH}" y="{CAL_Y + 7 * PITCH + 8}" width="{CELL}"'
        f' height="{CELL}" rx="2" fill="{color}"/>'
        for i, color in enumerate(LEVELS.values())
    )

    return f"""    <g>
{chr(10).join(labels)}
{weekdays}
{chr(10).join(cells)}
      <text x="{swatch_x0 - 8}" y="{CAL_Y + 7 * PITCH + 17}" text-anchor="end" font-family="{FONT_MONO}" font-size="10" fill="{DIM}">Less</text>
{swatches}
      <text x="{CAL_RIGHT}" y="{CAL_Y + 7 * PITCH + 17}" text-anchor="end" font-family="{FONT_MONO}" font-size="10" fill="{DIM}">More</text>
    </g>"""


def activity_rows(data: dict[str, Any]) -> str:
    rows = [
        ("Contributions", data["contributions"]),
        ("Commits", data["commits"]),
        ("Pull requests", data["prs"]),
        ("Reviews", data["reviews"]),
        ("Issues", data["issues"]),
        ("Active repos", data["active_repos"]),
    ]
    out = []
    for index, (label, value) in enumerate(rows):
        y = ACT_Y + index * ACT_STEP
        color = TEXT if index == 0 else MUTED
        weight = "700" if index == 0 else "400"
        out.append(
            f'      <text x="{ACT_X}" y="{y}" font-family="{FONT_SANS}" font-size="13"'
            f' fill="{MUTED}">{escape(label)}</text>\n'
            f'      <text x="{ACT_RIGHT}" y="{y}" text-anchor="end" font-family="{FONT_MONO}"'
            f' font-size="15" font-weight="{weight}" fill="{color}">{thousands(value)}</text>'
        )
    return "\n".join(out)


def language_bar(languages: list[dict[str, Any]]) -> str:
    width = BAR_RIGHT - BAR_X
    segments = []
    offset = 0.0
    for index, language in enumerate(languages):
        span = width * language["share"] / 100
        # Round corners only on the outer ends of the bar.
        first, last = index == 0, index == len(languages) - 1
        radius = 7 if (first or last) else 0
        segments.append(
            f'      <rect x="{BAR_X + offset:.1f}" y="{BAR_Y}" width="{max(span, 1):.1f}"'
            f' height="{BAR_H}" rx="{radius}" fill="{escape(language["color"])}"/>'
        )
        if radius and not (first and last):
            # Square off the inner side of the rounded segment.
            patch_x = BAR_X + offset + (7 if first else 0)
            segments.append(
                f'      <rect x="{patch_x:.1f}" y="{BAR_Y}" width="{max(span - 7, 1):.1f}"'
                f' height="{BAR_H}" fill="{escape(language["color"])}"/>'
            )
        offset += span

    legend = []
    for index, language in enumerate(languages):
        column, row = index % 3, index // 3
        if row >= len(LEGEND_Y):
            break
        x = LEGEND_X[column]
        y = LEGEND_Y[row]
        text = f'{language["name"]}  {language["share"]:.1f}%'
        legend.append(
            f'      <circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{escape(language["color"])}"/>\n'
            f'      <text x="{x + 20}" y="{y}" font-family="{FONT_MONO}" font-size="13"'
            f' fill="{MUTED}">{escape(ellipsize(text, LEGEND_W - 20, 13))}</text>'
        )

    return "\n".join(segments) + "\n" + "\n".join(legend)


def render(data: dict[str, Any]) -> str:
    top = ", ".join(f'{lang["name"]} {lang["share"]:.1f}%' for lang in data["languages"][:3])
    aria = (
        f'GitHub metrics for @{data["login"]}: {data["contributions"]} contributions '
        f'between {data["from"]} and {data["to"]}, {data["repos"]} own repositories, '
        f'{data["stars"]} stars. Top languages: {top}'
    )
    totals = dot_join(
        [
            f'{thousands(data["repos"])} own repos',
            f'{thousands(data["stars"])} stars',
            f'{thousands(data["forks"])} forks',
            f'{thousands(data["followers"])} followers',
            f'{thousands(data["gists"])} gists',
        ],
        DIM,
    )

    body = f"""    <!-- ============ activity ============ -->
    <text x="64" y="72" font-family="{FONT_MONO}" font-size="15" fill="{GREEN}">$ gh contributions --since=1y</text>

    <text x="64" y="108" font-family="{FONT_SANS}" font-size="17" fill="{TEXT}">{dot_join(["Last 12 months", f'<tspan fill="{DIM}">{escape(data["from"])} → {escape(data["to"])}</tspan>'], DIM)}</text>

{calendar(data['weeks'])}

    <line x1="{SPLIT_X}" y1="96" x2="{SPLIT_X}" y2="288" stroke="{LINE_SOFT}"/>

{activity_rows(data)}

    <line x1="64" y1="304" x2="{BAR_RIGHT}" y2="304" stroke="{LINE}"/>

    <!-- ============ languages ============ -->
    <text x="64" y="{BAR_Y - 14}" font-family="{FONT_SANS}" font-size="17" fill="{TEXT}">{dot_join(["Languages", f'<tspan fill="{DIM}">top {len(data["languages"])} of {data["language_count"]} across {data["repos"]} own repositories</tspan>'], DIM)}</text>

{language_bar(data['languages'])}

    <line x1="64" y1="464" x2="{BAR_RIGHT}" y2="464" stroke="{LINE}"/>

    <text x="64" y="{HEIGHT - 26}" font-family="{FONT_MONO}" font-size="13" fill="{MUTED}">{totals}</text>

    <text x="{BAR_RIGHT}" y="{HEIGHT - 26}" text-anchor="end" font-family="{FONT_MONO}" font-size="11" fill="{DIM}">{dot_join([f"@{escape(data['login'])}", f"updated {escape(data['generated_at'][:10])}"], LINE)}</text>"""

    return shell(
        width=WIDTH,
        height=HEIGHT,
        aria=aria,
        generator="gen_metrics.py",
        body=body,
        generated_at=data["generated_at"],
        glow=(300, 40, 460, 300),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--user", default="jtprogru", help="GitHub login (default: jtprogru)")
    parser.add_argument("--output", default="assets/metrics.svg", help="where to write the SVG")
    parser.add_argument("--token", help="GitHub token (default: $GITHUB_TOKEN or $GH_TOKEN)")
    parser.add_argument(
        "--exclude-private",
        action="store_true",
        help="count only public commits (private ones are included by default)",
    )
    parser.add_argument("--check", action="store_true", help="exit 1 if the file would change")
    parser.add_argument("--print-stats", action="store_true", help="dump the collected numbers as JSON")
    args = parser.parse_args()

    token = token_from_env(args.token)
    data = collect(args.user, token, include_private=not args.exclude_private)
    if args.print_stats:
        skip = {"weeks", "languages"}
        print(json.dumps({k: v for k, v in data.items() if k not in skip}, ensure_ascii=False, indent=2))
    return emit(args.output, render(data), args.check)


if __name__ == "__main__":
    raise SystemExit(main())
