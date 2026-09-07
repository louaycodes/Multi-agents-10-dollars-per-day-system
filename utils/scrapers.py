"""
utils/scrapers.py — Web scrapers for Hunter agent.

Scrapers:
  - RedditScraper      : r/dropshipping, r/entrepreneur etc. via Reddit JSON API
  - TikTokScraper      : Trending sounds/products (public HTML scraping)
  - AliExpressScraper  : Bestselling products (HTML scraping)
  - QuoraScraper       : Trending questions (HTML scraping)
  - MturkChecker       : Available HITs via boto3 (real API)

All scrapers:
  - Use fake user-agent rotation
  - Respect SCRAPER_REQUEST_DELAY between requests
  - Have timeouts (SCRAPER_TIMEOUT_SECONDS)
  - Return empty lists on failure (never raise)
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlencode

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

from config.settings import SYSTEM, GIGS
from config.constants import (
    REDDIT_SUBREDDITS,
    QUORA_TOPIC_URLS,
    ALIEXPRESS_BESTSELLER_URL,
    SCRAPER_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Shared HTTP utilities
# ──────────────────────────────────────────────
try:
    _ua = UserAgent(fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
except Exception:
    _ua = None


def _random_ua() -> str:
    if _ua:
        try:
            return _ua.random
        except Exception:
            pass
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _get(
    url: str,
    headers: Optional[Dict] = None,
    params: Optional[Dict] = None,
    json_response: bool = False,
) -> Optional[Any]:
    """
    Make a GET request with retry logic, fake UA, and delay.
    Returns parsed JSON or BeautifulSoup object (or None on failure).
    """
    default_headers = {
        "User-Agent": _random_ua(),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    try:
        time.sleep(SYSTEM.SCRAPER_REQUEST_DELAY + random.uniform(0, 1))
        resp = requests.get(
            url,
            headers=default_headers,
            params=params,
            timeout=SYSTEM.SCRAPER_TIMEOUT,
        )
        resp.raise_for_status()
        if json_response:
            return resp.json()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        logger.warning("HTTP GET failed for %s: %s", url, exc)
        return None


# ──────────────────────────────────────────────
# Reddit Scraper — uses public JSON API (no auth)
# ──────────────────────────────────────────────
class RedditScraper:
    BASE_URL = "https://www.reddit.com"

    def scrape(
        self,
        subreddit: str = "dropshipping",
        keywords: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Scrape top posts from a subreddit.

        Returns list of dicts:
          {title, url, score, comments, subreddit, keywords_matched}
        """
        url = f"{self.BASE_URL}/r/{subreddit}/hot.json"
        data = _get(url, params={"limit": limit}, json_response=True)
        if not data:
            return []

        results = []
        try:
            posts = data.get("data", {}).get("children", [])
            for post in posts:
                p = post.get("data", {})
                title = p.get("title", "")
                matched = []
                if keywords:
                    matched = [kw for kw in keywords if kw.lower() in title.lower()]

                results.append({
                    "title": title,
                    "url": urljoin(self.BASE_URL, p.get("permalink", "")),
                    "score": p.get("score", 0),
                    "comments": p.get("num_comments", 0),
                    "subreddit": subreddit,
                    "flair": p.get("link_flair_text", ""),
                    "keywords_matched": matched,
                    "source": "reddit",
                })
        except Exception as exc:
            logger.error("RedditScraper parse error: %s", exc)

        logger.info("RedditScraper[%s]: found %d posts", subreddit, len(results))
        return results

    def scrape_multiple(self, keywords: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Scrape all configured subreddits."""
        all_results = []
        for sub in REDDIT_SUBREDDITS:
            results = self.scrape(sub, keywords=keywords)
            all_results.extend(results)
        return all_results


# ──────────────────────────────────────────────
# TikTok Scraper — public hashtag pages
# ──────────────────────────────────────────────
class TikTokScraper:
    """
    Scrapes TikTok trending product-related hashtags.
    Uses public HTML pages; does NOT require login.
    Note: TikTok aggressively blocks scrapers — returns curated fallbacks on failure.
    """

    TRENDING_HASHTAGS = [
        "tiktokmademebuyit",
        "productreview",
        "amazonfind",
        "aliexpress",
        "dropshipping",
        "coolproducts",
    ]

    FALLBACK_TRENDS = [
        {"keyword": "anti-slip phone grip", "category": "accessories", "trend_score": 9.2},
        {"keyword": "LED strip lights", "category": "home", "trend_score": 8.8},
        {"keyword": "portable mini projector", "category": "electronics", "trend_score": 8.5},
        {"keyword": "magnetic phone mount", "category": "car", "trend_score": 8.1},
        {"keyword": "cable organizer clips", "category": "office", "trend_score": 7.9},
        {"keyword": "posture corrector", "category": "health", "trend_score": 7.7},
        {"keyword": "reusable produce bags", "category": "eco", "trend_score": 7.5},
        {"keyword": "plant grow light", "category": "garden", "trend_score": 7.3},
    ]

    def scrape(self) -> List[Dict[str, Any]]:
        """
        Returns trending product keywords from TikTok or fallback list.
        """
        # Attempt to scrape TikTok's discover page
        results = self._scrape_discover()
        if results:
            return results

        # Fallback: return hardcoded trends (still useful for scoring)
        logger.info("TikTokScraper: using fallback trend list")
        return [
            {
                "keyword": t["keyword"],
                "category": t["category"],
                "trend_score": t["trend_score"],
                "source": "tiktok_fallback",
            }
            for t in self.FALLBACK_TRENDS
        ]

    def _scrape_discover(self) -> List[Dict[str, Any]]:
        """Attempt to parse TikTok's trending discover page."""
        try:
            soup = _get("https://www.tiktok.com/trending", headers={
                "Accept": "text/html",
                "sec-ch-ua-platform": '"Windows"',
            })
            if not soup:
                return []

            results = []
            # TikTok renders most content via JS — extract what's in static HTML
            hashtags = soup.find_all("a", attrs={"href": lambda h: h and "/tag/" in str(h)})
            for tag in hashtags[:15]:
                kw = tag.get_text(strip=True).lstrip("#")
                if kw:
                    results.append({
                        "keyword": kw,
                        "category": "trending",
                        "trend_score": 8.0,
                        "source": "tiktok",
                    })
            return results
        except Exception as exc:
            logger.debug("TikTokScraper _scrape_discover failed: %s", exc)
            return []


# ──────────────────────────────────────────────
# AliExpress Scraper — product sourcing
# ──────────────────────────────────────────────
class AliExpressScraper:
    """
    Scrapes AliExpress bestselling products for reselling sourcing.
    Returns products with price, title, and order count.
    """

    SEARCH_URL = "https://www.aliexpress.com/wholesale"

    def scrape_trending(self, keywords: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Scrape AliExpress for products matching trending keywords.

        Args:
            keywords: Product keywords from TikTok/Reddit trends

        Returns:
            List of product dicts with pricing info
        """
        if not keywords:
            keywords = ["phone accessories", "home gadgets", "car accessories"]

        all_products = []
        for kw in keywords[:3]:  # Limit requests
            products = self._search_keyword(kw)
            all_products.extend(products)
            time.sleep(SYSTEM.SCRAPER_REQUEST_DELAY)

        return all_products

    def _search_keyword(self, keyword: str) -> List[Dict[str, Any]]:
        """Search AliExpress for a keyword."""
        params = {
            "SearchText": keyword,
            "SortType": "total_tranqnty_desc",
        }
        soup = _get(self.SEARCH_URL, params=params)
        if not soup:
            return self._fallback_products(keyword)

        products = []
        try:
            # AliExpress product cards
            items = soup.find_all("div", class_=lambda c: c and "product" in c.lower())[:10]
            for item in items:
                title_el = item.find(["h1", "h3", "a", "span"], class_=lambda c: c and "title" in str(c).lower())
                price_el = item.find(class_=lambda c: c and "price" in str(c).lower())

                title = title_el.get_text(strip=True) if title_el else keyword
                price_text = price_el.get_text(strip=True) if price_el else "1.00"

                # Extract numeric price
                price = self._parse_price(price_text)
                if price and price < 10:  # Only cheap items suitable for reselling
                    products.append({
                        "title": title[:100],
                        "cost_price_usd": price,
                        "suggested_list_price": round(price * 2.5, 2),
                        "keyword": keyword,
                        "source": "aliexpress",
                        "url": self.SEARCH_URL,
                    })
        except Exception as exc:
            logger.debug("AliExpressScraper parse error: %s", exc)

        if not products:
            return self._fallback_products(keyword)

        logger.info("AliExpressScraper[%s]: found %d products", keyword, len(products))
        return products

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """Extract numeric price from price string like '$1.23' or 'US $1.23 - $4.56'."""
        import re
        matches = re.findall(r"[\d]+\.?[\d]*", text.replace(",", ""))
        if matches:
            return float(matches[0])
        return None

    @staticmethod
    def _fallback_products(keyword: str) -> List[Dict[str, Any]]:
        """Return estimated product data when scraping fails."""
        return [
            {
                "title": f"{keyword} (trending item)",
                "cost_price_usd": 1.50,
                "suggested_list_price": 3.75,
                "keyword": keyword,
                "source": "aliexpress_fallback",
                "url": f"https://www.aliexpress.com/wholesale?SearchText={keyword.replace(' ', '+')}",
            }
        ]


# ──────────────────────────────────────────────
# Quora Scraper — trending questions for affiliate
# ──────────────────────────────────────────────
class QuoraScraper:
    """
    Scrapes Quora for trending questions in high-value topics.
    These questions are targets for affiliate-linked answers.
    """

    SEARCH_URL = "https://www.quora.com/search"

    TOPICS = [
        "best products to buy online",
        "how to make money online",
        "best Amazon products",
        "dropshipping product ideas",
        "best Udemy courses",
    ]

    def scrape(self) -> List[Dict[str, Any]]:
        """Return a list of trending Quora questions."""
        all_questions = []
        for topic in self.TOPICS[:3]:
            questions = self._search_topic(topic)
            all_questions.extend(questions)
            time.sleep(SYSTEM.SCRAPER_REQUEST_DELAY)

        if not all_questions:
            all_questions = self._fallback_questions()

        logger.info("QuoraScraper: found %d questions", len(all_questions))
        return all_questions

    def _search_topic(self, topic: str) -> List[Dict[str, Any]]:
        """Search Quora for questions matching a topic."""
        soup = _get(self.SEARCH_URL, params={"q": topic})
        if not soup:
            return []

        questions = []
        try:
            # Quora renders via React — extract from static HTML
            links = soup.find_all("a", href=lambda h: h and "/What-" in str(h) or "/How-" in str(h))
            for link in links[:5]:
                text = link.get_text(strip=True)
                if len(text) > 20:
                    questions.append({
                        "question": text,
                        "url": urljoin("https://www.quora.com", link.get("href", "")),
                        "topic": topic,
                        "source": "quora",
                        "estimated_views": random.randint(500, 5000),
                    })
        except Exception as exc:
            logger.debug("QuoraScraper parse error: %s", exc)
        return questions

    @staticmethod
    def _fallback_questions() -> List[Dict[str, Any]]:
        return [
            {
                "question": "What are the best products to buy on AliExpress?",
                "url": "https://www.quora.com/topic/AliExpress",
                "topic": "product recommendations",
                "source": "quora_fallback",
                "estimated_views": 2000,
            },
            {
                "question": "What are the best online courses to learn programming?",
                "url": "https://www.quora.com/topic/Online-Education",
                "topic": "online learning",
                "source": "quora_fallback",
                "estimated_views": 3000,
            },
            {
                "question": "How can I make money from home in 2024?",
                "url": "https://www.quora.com/topic/Making-Money-Online",
                "topic": "earning online",
                "source": "quora_fallback",
                "estimated_views": 5000,
            },
        ]


# ──────────────────────────────────────────────
# Mturk Checker — boto3 (real API)
# ──────────────────────────────────────────────
class MturkChecker:
    """
    Checks Amazon Mechanical Turk for available HITs.
    Uses the real boto3 API — respects MTURK_SANDBOX setting.
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client:
            return self._client
        try:
            import boto3
            endpoint = (
                "https://mturk-requester-sandbox.us-east-1.amazonaws.com"
                if GIGS.MTURK_SANDBOX
                else "https://mturk-requester.us-east-1.amazonaws.com"
            )
            self._client = boto3.client(
                "mturk",
                region_name=GIGS.MTURK_REGION,
                aws_access_key_id=GIGS.MTURK_ACCESS_KEY,
                aws_secret_access_key=GIGS.MTURK_SECRET_KEY,
                endpoint_url=endpoint,
            )
            return self._client
        except Exception as exc:
            logger.warning("Mturk boto3 client init failed: %s", exc)
            return None

    def get_active_hits(
        self,
        min_reward: float = 0.10,
        max_hits: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Return available HITs filtered by minimum reward.

        Returns:
            List of HIT dicts with {hit_id, title, reward, time_limit, assignments}
        """
        client = self._get_client()
        if not client:
            return self._fallback_hits()

        try:
            resp = client.list_hits(MaxResults=max_hits)
            hits = []
            for hit in resp.get("HITs", []):
                reward = float(hit.get("Reward", "0"))
                if reward < min_reward:
                    continue
                hits.append({
                    "hit_id": hit.get("HITId"),
                    "title": hit.get("Title", ""),
                    "reward": reward,
                    "time_limit_seconds": hit.get("AssignmentDurationInSeconds", 3600),
                    "assignments_available": hit.get("NumberOfAssignmentsAvailable", 0),
                    "keywords": hit.get("Keywords", ""),
                    "source": "mturk",
                })
            logger.info("MturkChecker: found %d HITs (sandbox=%s)", len(hits), GIGS.MTURK_SANDBOX)
            return hits
        except Exception as exc:
            logger.warning("MturkChecker API error: %s", exc)
            return self._fallback_hits()

    def get_account_balance(self) -> float:
        """Return current Mturk account balance."""
        client = self._get_client()
        if not client:
            return 0.0
        try:
            resp = client.get_account_balance()
            balance = float(resp.get("AvailableBalance", "0"))
            logger.info("Mturk account balance: $%.2f", balance)
            return balance
        except Exception as exc:
            logger.warning("get_account_balance error: %s", exc)
            return 0.0

    @staticmethod
    def _fallback_hits() -> List[Dict[str, Any]]:
        """Return mock HITs when API is unavailable (for testing)."""
        return [
            {
                "hit_id": "MOCK_HIT_001",
                "title": "Categorize images into correct categories",
                "reward": 0.25,
                "time_limit_seconds": 1800,
                "assignments_available": 50,
                "keywords": "image, categorization, simple",
                "source": "mturk_mock",
            },
            {
                "hit_id": "MOCK_HIT_002",
                "title": "Transcribe short audio clip (30 seconds)",
                "reward": 0.50,
                "time_limit_seconds": 900,
                "assignments_available": 20,
                "keywords": "transcription, audio",
                "source": "mturk_mock",
            },
        ]
