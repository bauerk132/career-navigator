"""Synthetic acceptance tests for non-lossy dedup keys and visible uncertainty (JF-44, A2)."""
import unittest

from career_navigator.dedup import dup_key, group_duplicates
from career_navigator.models import Posting


def posting(url, title="Cafe Manager", company="Acme", location="Pittsburgh, PA", provider_id=None):
    return Posting(
        url=url,
        title=title,
        company=company,
        location=location,
        provider="indeed" if provider_id else "other",
        provider_id=provider_id,
    )


class DupKeyTests(unittest.TestCase):
    def test_case_and_whitespace_normalized(self):
        self.assertEqual(
            dup_key("  Café   Manager ", "ACME", "Pittsburgh"),
            dup_key("café manager", "acme", "pittsburgh"),
        )

    def test_accents_preserved_not_stripped(self):
        self.assertNotEqual(
            dup_key("Café Manager", "Acme", "Pittsburgh"),
            dup_key("Cafe Manager", "Acme", "Pittsburgh"),
        )

    def test_distinct_fields_distinct_keys(self):
        self.assertNotEqual(
            dup_key("Cafe Manager", "Acme", "Pittsburgh"),
            dup_key("Cafe Manager", "Acme", "Philadelphia"),
        )


class GroupDuplicatesTests(unittest.TestCase):
    def test_same_key_different_urls_grouped_exact_both_visible(self):
        a = posting("https://x.example/1")
        b = posting("https://x.example/2")
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].confidence, "exact")
        self.assertEqual(len(groups[0].members), 2)

    def test_same_provider_id_different_title_is_uncertain_not_merged(self):
        a = posting("https://x.example/1", title="Cafe Manager", provider_id="J1")
        b = posting("https://x.example/2", title="Café Supervisor", provider_id="J1")
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].confidence, "uncertain")
        self.assertEqual(len(groups[0].members), 2)

    def test_same_title_company_different_location_is_uncertain(self):
        a = posting("https://x.example/1", location="Pittsburgh, PA")
        b = posting("https://x.example/2", location="Philadelphia, PA")
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].confidence, "uncertain")

    def test_distinct_postings_stay_separate(self):
        a = posting("https://x.example/1", title="Cafe Manager")
        b = posting("https://x.example/2", title="Line Cook", company="Diner")
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(g.confidence == "single" for g in groups))

    def test_grouping_is_deterministic(self):
        a = posting("https://x.example/2")
        b = posting("https://x.example/1")
        first = group_duplicates([a, b])
        second = group_duplicates([b, a])
        self.assertEqual(
            [tuple(m.url for m in g.members) for g in first],
            [tuple(m.url for m in g.members) for g in second],
        )


if __name__ == "__main__":
    unittest.main()