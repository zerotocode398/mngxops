from django.test import SimpleTestCase

from utils.search import split_search_tags


class SplitSearchTagsTests(SimpleTestCase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(split_search_tags(""), [])
        self.assertEqual(split_search_tags(None), [])

    def test_single_tag(self):
        self.assertEqual(split_search_tags("web-1"), ["web-1"])

    def test_multiple_tags_ascii_comma(self):
        self.assertEqual(split_search_tags("web-1,10.0.0.1"), ["web-1", "10.0.0.1"])

    def test_multiple_tags_full_width_comma(self):
        self.assertEqual(split_search_tags("web-1，10.0.0.1"), ["web-1", "10.0.0.1"])

    def test_mixed_commas(self):
        self.assertEqual(
            split_search_tags("web-1，10.0.0.1,prod"),
            ["web-1", "10.0.0.1", "prod"],
        )

    def test_strips_whitespace_and_drops_empty(self):
        self.assertEqual(
            split_search_tags("  web-1 , , 10.0.0.1  , "),
            ["web-1", "10.0.0.1"],
        )
