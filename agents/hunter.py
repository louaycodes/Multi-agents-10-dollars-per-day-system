"""
agents/hunter.py — Hunter Agent (06:30 daily)

Responsibilities:
  1. Receive daily plan from Manager
  2. Run 4 scrapers in parallel threads:
     - RedditScraper (trending products/topics)
     - TikTokScraper (trending product keywords)
     - AliExpressScraper (product pricing for reselling)
     - QuoraScraper (affiliate-ready questions)
     + MturkChecker (available HITs)
  3. Score each opportunity: (roi * feasibility * time_to_profit) / effort_hours
  4. Filter blocked risk levels
  5. Return top MAX_OPPORTUNITIES opportunities
  6. Save to DB + publish to Executor queue
"""

import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import date
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.constants import (
    CHANNEL_HUNTER_TO_EXECUTOR,
    CHANNEL_MANAGER_TO_ALL,
    MAX_OPPORTUNITIES,
    MIN_FEASIBILITY_SCORE,
    MIN_ESTIMATED_ROI,
    RISK_LEVELS_BLOCKED,
    MTURK_MAX_HITS_PER_DAY,
    MTURK_MIN_APPROVAL_RATE,
    MTURK_MIN_PAY_PER_HOUR,
    REDDIT_SUBREDDITS,
)
from config.settings import SYSTEM

logger = logging.getLogger(__name__)


class HunterAgent(BaseAgent):
    """
    Discovers and scores daily opportunities across all 3 niches.
    """

    def __init__(self):
        super().__init__("hunter")
        from utils.scrapers import (
            RedditScraper,
            TikTokScraper,
            AliExpressScraper,
            QuoraScraper,
            MturkChecker,
        )
        self.reddit = RedditScraper()
        self.tiktok = TikTokScraper()
        self.aliexpress = AliExpressScraper()
        self.quora = QuoraScraper()
        self.mturk = MturkChecker()

    def run(self) -> List[Dict[str, Any]]:
        """Standard run() at 06:30."""
        return self.find_opportunities()

    # ──────────────────────────────────────────
    # Main opportunity discovery
    # ──────────────────────────────────────────

    def find_opportunities(self) -> List[Dict[str, Any]]:
        """
        Run all scrapers in parallel, score results, save top opportunities.

        Returns:
            List of top opportunity dicts
        """
        from utils.message_queue import get_message, publish
        from utils.database import insert_opportunity

        # Get today's plan (for context on which niches to prioritize)
        plan = get_message(CHANNEL_MANAGER_TO_ALL, timeout=3.0) or {}
        priority_niches = plan.get("priority_niches", ["reselling", "gigs", "affiliate"])
        allocations = plan.get("allocations", {"reselling": 0.5, "gigs": 0.35, "affiliate": 0.15})

        self.logger.info("Starting parallel scraping (priority: %s)", priority_niches)

        # Run scrapers concurrently
        raw_data = self._run_scrapers_parallel()

        # Convert raw scrape data to opportunities
        all_opportunities = []
        all_opportunities.extend(self._process_reselling_data(raw_data))
        all_opportunities.extend(self._process_gig_data(raw_data))
        all_opportunities.extend(self._process_affiliate_data(raw_data))

        # Filter and score
        filtered = self._filter_opportunities(all_opportunities)
        scored = self._score_and_rank(filtered)

        # Apply priority boost based on Manager's allocations
        scored = self._apply_priority_boost(scored, allocations)

        # Take top N
        top = scored[:MAX_OPPORTUNITIES]

        self.logger.info("Found %d raw opportunities → %d after filtering → top %d selected",
                         len(all_opportunities), len(filtered), len(top))

        # Save to DB
        for opp in top:
            try:
                opp_id = insert_opportunity(opp)
                opp["db_id"] = opp_id
            except Exception as exc:
                self.logger.warning("Failed to save opportunity: %s", exc)

        # Publish to Executor
        publish(CHANNEL_HUNTER_TO_EXECUTOR, {"opportunities": top, "date": str(date.today())})
        self.logger.info("Published %d opportunities to executor queue", len(top))

        self.log_action("opportunities_found", extra={"count": len(top)})
        return top

    # ──────────────────────────────────────────
    # Parallel scraping
    # ──────────────────────────────────────────

    def _run_scrapers_parallel(self) -> Dict[str, Any]:
        """Run all scrapers in parallel threads. Returns raw data dict."""
        raw = {
            "reddit": [],
            "tiktok": [],
            "aliexpress": [],
            "quora": [],
            "mturk_hits": [],
        }

        scraper_tasks = {
            "reddit": lambda: self.reddit.scrape_multiple(
                keywords=["trending", "dropshipping", "winning product", "best seller"]
            ),
            "tiktok": lambda: self.tiktok.scrape(),
            "quora": lambda: self.quora.scrape(),
            "mturk_hits": lambda: self.mturk.get_active_hits(min_reward=0.10, max_hits=20),
        }

        # TikTok runs first to get keywords for AliExpress
        with ThreadPoolExecutor(max_workers=SYSTEM.SCRAPER_MAX_THREADS, thread_name_prefix="scraper") as executor:
            futures = {executor.submit(fn): key for key, fn in scraper_tasks.items()}

            for future in as_completed(futures, timeout=60):
                key = futures[future]
                try:
                    raw[key] = future.result(timeout=5)
                    self.logger.info("Scraper[%s]: %d results", key, len(raw[key]))
                except TimeoutError:
                    self.logger.warning("Scraper[%s]: timed out", key)
                except Exception as exc:
                    self.logger.warning("Scraper[%s] error: %s", key, exc)

        # AliExpress: use TikTok keywords
        tiktok_keywords = [t.get("keyword", "") for t in raw["tiktok"][:5]]
        try:
            raw["aliexpress"] = self.aliexpress.scrape_trending(keywords=tiktok_keywords or None)
            self.logger.info("Scraper[aliexpress]: %d products", len(raw["aliexpress"]))
        except Exception as exc:
            self.logger.warning("AliExpress scraper error: %s", exc)

        return raw

    # ──────────────────────────────────────────
    # Opportunity builders per niche
    # ──────────────────────────────────────────

    def _process_reselling_data(self, raw: Dict) -> List[Dict]:
        """Convert AliExpress + TikTok data into reselling opportunities."""
        opportunities = []
        products = raw.get("aliexpress", [])
        tiktok_trends = {t.get("keyword", "").lower(): t for t in raw.get("tiktok", [])}

        for product in products[:8]:
            title = product.get("title", "Trending Product")
            cost = float(product.get("cost_price_usd", 1.50))
            list_price = float(product.get("suggested_list_price", cost * 2.5))
            profit_per_unit = list_price - cost
            margin = profit_per_unit / list_price if list_price > 0 else 0

            if margin < 0.40:  # Skip low-margin products
                continue

            # Check if in TikTok trends
            keyword_lower = product.get("keyword", "").lower()
            trend_score = 1.0
            if any(kw in keyword_lower for kw in tiktok_trends):
                trend_score = 1.3  # Boost trending items

            estimated_daily_units = 2  # Conservative: 2 sales/day
            estimated_roi = profit_per_unit * estimated_daily_units

            opportunities.append({
                "niche": "reselling",
                "type": "product_listing",
                "title": title[:200],
                "source_url": product.get("url", ""),
                "estimated_roi": round(estimated_roi, 2),
                "feasibility_score": min(9.0, 6.0 + (margin * 5) + (trend_score - 1.0) * 3),
                "effort_hours": 0.5,
                "time_to_profit": 1.0,  # Same day revenue
                "competition": "low" if margin > 0.6 else "medium",
                "capital_required": cost,
                "risk_level": "low",
                "metadata": {
                    "cost_price": cost,
                    "list_price": list_price,
                    "margin": round(margin, 2),
                    "keyword": product.get("keyword", ""),
                    "source": product.get("source", "aliexpress"),
                },
            })

        return opportunities

    def _process_gig_data(self, raw: Dict) -> List[Dict]:
        """Convert Mturk HITs into gig opportunities."""
        opportunities = []
        hits = raw.get("mturk_hits", [])

        for hit in hits[:MTURK_MAX_HITS_PER_DAY]:
            reward = float(hit.get("reward", 0))
            time_limit = int(hit.get("time_limit_seconds", 3600))
            effort_hours = time_limit / 3600

            # Estimate hourly rate
            hourly_rate = (reward / effort_hours) if effort_hours > 0 else reward
            if hourly_rate < MTURK_MIN_PAY_PER_HOUR:
                continue

            # Feasibility: prefer short, high-reward HITs
            feasibility = min(10.0, 5.0 + (reward * 4) - (effort_hours * 2))

            opportunities.append({
                "niche": "gigs",
                "type": "mturk_hit",
                "title": hit.get("title", "Mturk Task")[:200],
                "source_url": "https://worker.mturk.com",
                "estimated_roi": reward,
                "feasibility_score": max(4.0, feasibility),
                "effort_hours": round(effort_hours, 2),
                "time_to_profit": 1.0,  # Approved same/next day
                "competition": "low",
                "capital_required": 0.0,
                "risk_level": "low",
                "metadata": {
                    "hit_id": hit.get("hit_id"),
                    "reward": reward,
                    "time_limit_seconds": time_limit,
                    "assignments_available": hit.get("assignments_available", 0),
                    "keywords": hit.get("keywords", ""),
                    "source": hit.get("source", "mturk"),
                },
            })

        # Add Fiverr gig optimization as a low-effort opportunity
        opportunities.append({
            "niche": "gigs",
            "type": "fiverr_gig_update",
            "title": "Optimize existing Fiverr gigs (update prices, tags, descriptions)",
            "source_url": "https://www.fiverr.com",
            "estimated_roi": 3.0,
            "feasibility_score": 7.0,
            "effort_hours": 0.25,
            "time_to_profit": 0.5,  # May take 1-2 days for orders
            "competition": "medium",
            "capital_required": 0.0,
            "risk_level": "low",
            "metadata": {"source": "fiverr_api"},
        })

        return opportunities

    def _process_affiliate_data(self, raw: Dict) -> List[Dict]:
        """Convert Reddit + Quora data into affiliate posting opportunities."""
        opportunities = []

        # Quora questions → affiliate answer opportunities
        quora_questions = raw.get("quora", [])
        for q in quora_questions[:5]:
            estimated_views = int(q.get("estimated_views", 1000))
            click_rate = 0.02  # 2% CTR is conservative
            estimated_clicks = estimated_views * click_rate
            from config.settings import AFFILIATE
            estimated_revenue = estimated_clicks * AFFILIATE.PAYOUT_PER_CLICK

            opportunities.append({
                "niche": "affiliate",
                "type": "quora_answer",
                "title": q.get("question", "Quora Question")[:200],
                "source_url": q.get("url", ""),
                "estimated_roi": round(estimated_revenue, 2),
                "feasibility_score": 7.5,
                "effort_hours": 0.25,
                "time_to_profit": 0.75,  # 1-2 days for click revenue
                "competition": "low",
                "capital_required": 0.0,
                "risk_level": "low",
                "metadata": {
                    "question": q.get("question", ""),
                    "source": "quora",
                    "estimated_views": estimated_views,
                },
            })

        # Reddit posts from entrepreneur subreddit
        reddit_posts = raw.get("reddit", [])
        high_engagement = [
            p for p in reddit_posts
            if p.get("score", 0) > 50 and p.get("subreddit") in ["entrepreneur", "Flipping", "dropshipping"]
        ]
        for post in high_engagement[:3]:
            opportunities.append({
                "niche": "affiliate",
                "type": "reddit_post",
                "title": f"Affiliate reply to: {post.get('title', '')[:100]}",
                "source_url": post.get("url", ""),
                "estimated_roi": 0.50,
                "feasibility_score": 6.5,
                "effort_hours": 0.2,
                "time_to_profit": 0.75,
                "competition": "low",
                "capital_required": 0.0,
                "risk_level": "low",
                "metadata": {
                    "reddit_score": post.get("score", 0),
                    "subreddit": post.get("subreddit", ""),
                    "source": "reddit",
                },
            })

        return opportunities

    # ──────────────────────────────────────────
    # Scoring & filtering
    # ──────────────────────────────────────────

    def _filter_opportunities(self, opps: List[Dict]) -> List[Dict]:
        """Remove blocked risk levels and low-quality opportunities."""
        filtered = []
        for opp in opps:
            if opp.get("risk_level") in RISK_LEVELS_BLOCKED:
                continue
            if opp.get("feasibility_score", 0) < MIN_FEASIBILITY_SCORE:
                continue
            if opp.get("estimated_roi", 0) < MIN_ESTIMATED_ROI:
                continue
            filtered.append(opp)
        return filtered

    def _score_and_rank(self, opps: List[Dict]) -> List[Dict]:
        """
        Calculate composite_score and sort descending.
        Formula: (roi * feasibility * time_to_profit) / effort_hours
        """
        for opp in opps:
            roi = float(opp.get("estimated_roi", 0))
            feasibility = float(opp.get("feasibility_score", 5))
            time_to_profit = float(opp.get("time_to_profit", 0.5))
            effort = max(float(opp.get("effort_hours", 1.0)), 0.1)
            opp["composite_score"] = round((roi * feasibility * time_to_profit) / effort, 2)

        return sorted(opps, key=lambda o: o.get("composite_score", 0), reverse=True)

    def _apply_priority_boost(
        self,
        opps: List[Dict],
        allocations: Dict[str, float],
    ) -> List[Dict]:
        """
        Boost composite_score for niches with higher allocation.
        Keeps top-ranked opportunities from Manager-prioritized niches at the front.
        """
        for opp in opps:
            niche = opp.get("niche", "")
            boost = allocations.get(niche, 0.33)
            opp["composite_score"] = round(opp.get("composite_score", 0) * (1 + boost), 2)

        return sorted(opps, key=lambda o: o.get("composite_score", 0), reverse=True)
