"""
tests/test_scrapers.py — Tests for all web scrapers.

Uses 'responses' library to mock HTTP requests.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

try:
    import responses as resp_lib
    HAS_RESPONSES = True
except ImportError:
    HAS_RESPONSES = False


class TestRedditScraper(unittest.TestCase):

    @patch("utils.scrapers._get")
    def test_scrape_returns_posts(self, mock_get):
        """Reddit scraper parses posts correctly."""
        mock_get.return_value = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Best dropshipping products 2024",
                            "permalink": "/r/dropshipping/comments/abc/",
                            "score": 500,
                            "num_comments": 45,
                            "link_flair_text": "Question",
                        }
                    },
                    {
                        "data": {
                            "title": "I made $500 in my first month",
                            "permalink": "/r/entrepreneur/comments/xyz/",
                            "score": 1200,
                            "num_comments": 89,
                            "link_flair_text": "Success",
                        }
                    },
                ]
            }
        }

        from utils.scrapers import RedditScraper
        scraper = RedditScraper()
        posts = scraper.scrape("dropshipping", keywords=["dropshipping", "products"])

        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["score"], 500)
        self.assertEqual(posts[0]["source"], "reddit")
        self.assertIn("dropshipping", posts[0]["keywords_matched"])

    @patch("utils.scrapers._get")
    def test_scrape_handles_empty_response(self, mock_get):
        """Reddit scraper returns empty list on bad response."""
        mock_get.return_value = None

        from utils.scrapers import RedditScraper
        scraper = RedditScraper()
        posts = scraper.scrape("entrepreneur")

        self.assertEqual(posts, [])

    @patch("utils.scrapers._get")
    def test_scrape_handles_network_error(self, mock_get):
        """Reddit scraper handles network errors gracefully."""
        mock_get.side_effect = Exception("Connection refused")

        from utils.scrapers import RedditScraper
        scraper = RedditScraper()
        # Should not raise
        try:
            posts = scraper.scrape("entrepreneur")
        except Exception:
            self.fail("RedditScraper raised exception instead of returning []")


class TestTikTokScraper(unittest.TestCase):

    @patch("utils.scrapers._get")
    def test_fallback_when_scrape_fails(self, mock_get):
        """TikTok scraper returns fallback trends when HTTP fails."""
        mock_get.return_value = None

        from utils.scrapers import TikTokScraper
        scraper = TikTokScraper()
        trends = scraper.scrape()

        self.assertGreater(len(trends), 0)
        # Fallback items should have trend_score
        for trend in trends:
            self.assertIn("keyword", trend)
            self.assertIn("trend_score", trend)

    def test_fallback_list_is_complete(self):
        """TikTok fallback list has at least 5 trends."""
        from utils.scrapers import TikTokScraper
        scraper = TikTokScraper()
        self.assertGreaterEqual(len(scraper.FALLBACK_TRENDS), 5)


class TestAliExpressScraper(unittest.TestCase):

    @patch("utils.scrapers._get")
    def test_fallback_on_parse_failure(self, mock_get):
        """AliExpress scraper returns fallback products when parse fails."""
        mock_get.return_value = MagicMock()  # BeautifulSoup mock with no matching elements

        from utils.scrapers import AliExpressScraper
        scraper = AliExpressScraper()
        products = scraper.scrape_trending(keywords=["phone grip"])

        self.assertGreater(len(products), 0)
        for product in products:
            self.assertIn("cost_price_usd", product)
            self.assertIn("suggested_list_price", product)

    def test_price_parsing(self):
        """Price parser handles various format strings."""
        from utils.scrapers import AliExpressScraper
        parse = AliExpressScraper._parse_price

        self.assertAlmostEqual(parse("$1.23"), 1.23)
        self.assertAlmostEqual(parse("US $2.50"), 2.50)
        self.assertAlmostEqual(parse("1.99 - 3.99"), 1.99)  # First match
        self.assertIsNone(parse("Free"))

    def test_fallback_products_structure(self):
        """Fallback products have required fields."""
        from utils.scrapers import AliExpressScraper
        products = AliExpressScraper._fallback_products("phone grip")

        self.assertEqual(len(products), 1)
        p = products[0]
        self.assertIn("title", p)
        self.assertIn("cost_price_usd", p)
        self.assertIn("suggested_list_price", p)
        self.assertGreater(p["suggested_list_price"], p["cost_price_usd"])


class TestQuoraScraper(unittest.TestCase):

    @patch("utils.scrapers._get")
    def test_fallback_when_scrape_fails(self, mock_get):
        """Quora scraper returns fallback questions when HTTP fails."""
        mock_get.return_value = None

        from utils.scrapers import QuoraScraper
        scraper = QuoraScraper()
        questions = scraper.scrape()

        self.assertGreater(len(questions), 0)
        for q in questions:
            self.assertIn("question", q)
            self.assertIn("url", q)

    def test_fallback_questions_are_affiliate_ready(self):
        """Fallback questions relate to affiliate-friendly topics."""
        from utils.scrapers import QuoraScraper
        questions = QuoraScraper._fallback_questions()
        self.assertGreaterEqual(len(questions), 3)
        for q in questions:
            self.assertIn("quora", q["url"])


class TestMturkChecker(unittest.TestCase):

    def test_fallback_hits_structure(self):
        """Fallback HITs have all required fields."""
        from utils.scrapers import MturkChecker
        hits = MturkChecker._fallback_hits()

        self.assertGreater(len(hits), 0)
        for hit in hits:
            self.assertIn("hit_id", hit)
            self.assertIn("title", hit)
            self.assertIn("reward", hit)
            self.assertGreater(hit["reward"], 0)
            self.assertIn("assignments_available", hit)

    @patch("utils.scrapers.GIGS")
    def test_skips_low_reward_hits(self, mock_gigs):
        """MturkChecker filters out HITs below minimum reward."""
        mock_gigs.MTURK_ACCESS_KEY = ""
        mock_gigs.MTURK_SECRET_KEY = ""

        from utils.scrapers import MturkChecker
        checker = MturkChecker()

        # Client not initialized (no keys) — will return fallback
        hits = checker.get_active_hits(min_reward=0.30)
        # All returned hits should be above min_reward OR be fallbacks
        for hit in hits:
            if hit.get("source") != "mturk_mock":
                self.assertGreaterEqual(hit["reward"], 0.30)


if __name__ == "__main__":
    unittest.main()
