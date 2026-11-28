"""Offline checks for the two GitHub profile cards."""

import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_cover
import gen_metrics

NOW = dt.datetime(2026, 11, 15, tzinfo=dt.timezone.utc)


class ProfileAssetsTest(unittest.TestCase):
    def test_cover_keeps_commits_and_restricted_contributions_separate(self):
        profile = {
            "login": "example", "name": "R&D", "createdAt": "2025-12-01T00:00:00Z",
            "repositories": {"totalCount": 1}, "followers": {"totalCount": 2},
            "pullRequests": {"totalCount": 3}, "repositoriesContributedTo": {"totalCount": 1},
            "pinnedItems": {"nodes": [{"name": "example", "description": "Tools <3",
                                       "stargazerCount": 5, "primaryLanguage": None}]},
        }
        years = {"y2025": {"totalCommitContributions": 10, "restrictedContributionsCount": 5},
                 "y2026": {"totalCommitContributions": 20, "restrictedContributionsCount": 8}}
        with patch.object(gen_cover, "now_utc", return_value=NOW), patch.object(
            gen_cover, "api", side_effect=[{"user": profile}, {"user": years}]
        ):
            data = gen_cover.collect("example", "test-token")
        svg = ET.fromstring(gen_cover.render(data, "Lead SRE", "Example", ["example.org"]))
        self.assertEqual(data["commits"], 30)
        self.assertEqual(data["restricted_contributions"], 13)
        for label, value in [("COMMITS", "30"), ("RESTRICTED CONTRIBUTIONS", "13")]:
            self.assertEqual(svg.findtext(f".//{{*}}g[{{*}}text='{label}']/{{*}}text"), value)
        self.assertIn("Tools <3", list(svg.itertext()))
        self.assertIn("R&D", svg.attrib["aria-label"])

    def test_metrics_counts_only_commits_and_labels_language_groups(self):
        activity = {
            "login": "example", "name": None,
            "followers": {"totalCount": 2}, "gists": {"totalCount": 0},
            "contributionsCollection": {
                "totalCommitContributions": 15, "restrictedContributionsCount": 10,
                "totalPullRequestContributions": 5, "totalPullRequestReviewContributions": 1,
                "totalIssueContributions": 2, "totalRepositoriesWithContributedCommits": 1,
                "contributionCalendar": {"totalContributions": 33, "weeks": []},
            },
        }
        for count in (0, 3, 8, 9, 12):
            with self.subTest(language_count=count):
                edges = [{"size": i + 1, "node": {"name": f"Language {i}", "color": None}}
                         for i in range(count)]
                page = {"totalCount": 1, "pageInfo": {"hasNextPage": False},
                        "nodes": [{"stargazerCount": 3, "forkCount": 1, "languages": {"edges": edges}}]}
                with patch.object(gen_metrics, "now_utc", return_value=NOW), patch.object(
                    gen_metrics, "api", side_effect=[{"user": activity}, {"user": {"repositories": page}}]
                ):
                    data = gen_metrics.collect("example", "test-token")
                svg = ET.fromstring(gen_metrics.render(data))
                text = " ".join(svg.itertext())
                self.assertEqual(data["commits"], 15)
                self.assertIn(f"top {min(8, count)} of {count}", text)
                self.assertEqual("Other" in text, count > 8)


if __name__ == "__main__":
    unittest.main()
