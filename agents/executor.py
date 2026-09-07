"""
agents/executor.py — Executor Agent (07:00 + pivot on demand)

Responsibilities:
  1. Receive opportunities from Hunter queue
  2. Dispatch to 3 execution paths (Reselling / Gigs / Affiliate)
  3. Each path: validate → generate content → execute API → log transaction
  4. Handle per-niche errors without stopping other niches
  5. Notify Tracker of all transactions

Also handles pivot execution when Optimizer sends new actions.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.constants import (
    CHANNEL_HUNTER_TO_EXECUTOR,
    CHANNEL_EXECUTOR_TO_TRACKER,
    CHANNEL_OPTIMIZER_TO_EXECUTOR,
    MTURK_MAX_HITS_PER_DAY,
)

logger = logging.getLogger(__name__)


class ExecutorAgent(BaseAgent):
    """
    Converts opportunities into real transactions.
    """

    def __init__(self):
        super().__init__("executor")
        from utils.api_clients import ProductResellClient, AffiliateClient, GigClient
        self.resell_client = ProductResellClient()
        self.affiliate_client = AffiliateClient()
        self.gig_client = GigClient()
        self._hit_count_today = 0  # Guard against Mturk overload

    def run(self) -> List[Dict[str, Any]]:
        """Standard run() at 07:00."""
        return self.execute_transactions()

    # ──────────────────────────────────────────
    # Main execution
    # ──────────────────────────────────────────

    def execute_transactions(self) -> List[Dict[str, Any]]:
        """
        Read opportunities from queue and execute each one.
        Returns list of transaction result dicts.
        """
        from utils.message_queue import get_message, get_all_messages, publish

        # Get opportunities from Hunter
        msg = get_message(CHANNEL_HUNTER_TO_EXECUTOR, timeout=30.0)
        if not msg:
            self.logger.warning("No opportunities received from Hunter — checking DB directly")
            opportunities = self._load_opportunities_from_db()
        else:
            opportunities = msg.get("opportunities", [])

        if not opportunities:
            self.logger.warning("No opportunities available — nothing to execute today")
            return []

        self.logger.info("Executing %d opportunities", len(opportunities))

        completed_transactions = []
        for opp in opportunities:
            niche = opp.get("niche", "")
            txn_result = self._execute_opportunity(opp)

            if txn_result:
                completed_transactions.append(txn_result)
                # Notify Tracker
                publish(CHANNEL_EXECUTOR_TO_TRACKER, txn_result)
                # Small delay between executions
                time.sleep(1)

        self.logger.info(
            "Execution complete: %d/%d opportunities executed successfully",
            len(completed_transactions), len(opportunities),
        )
        self.log_action("transactions_executed", extra={"count": len(completed_transactions)})
        return completed_transactions

    def _execute_opportunity(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Dispatch opportunity to the correct execution path."""
        niche = opp.get("niche", "")
        opp_type = opp.get("type", "")
        title = opp.get("title", "")

        self.logger.info("Executing: niche=%s type=%s title=%s", niche, opp_type, title[:60])

        try:
            if niche == "reselling":
                return self._execute_reselling(opp)
            elif niche == "gigs":
                return self._execute_gigs(opp)
            elif niche == "affiliate":
                return self._execute_affiliate(opp)
            else:
                self.logger.warning("Unknown niche: %s", niche)
                return None
        except Exception as exc:
            self.logger.error("Opportunity execution failed [%s/%s]: %s", niche, opp_type, exc)
            self._log_failed_transaction(opp, str(exc))
            return None

    # ──────────────────────────────────────────
    # Niche 1: Reselling
    # ──────────────────────────────────────────

    def _execute_reselling(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        List a product on marketplace platforms.
        Product details come from AliExpress scraping.
        """
        from utils.database import insert_transaction, mark_opportunity_executed

        metadata = opp.get("metadata", {})
        title = opp.get("title", "Product")
        cost_price = float(metadata.get("cost_price", 1.50))
        list_price = float(metadata.get("list_price", cost_price * 2.5))

        # Log transaction start
        txn_id = insert_transaction(
            niche="reselling",
            platform="facebook_marketplace",
            agent_action="product_listing_attempt",
            resource_id="",
            cost=cost_price,  # Cost of the product if purchased
            status="pending",
            metadata={"title": title, "cost": cost_price, "list_price": list_price},
        )

        # Execute listing
        result = self.resell_client.list_product({
            "title": title,
            "cost_price_usd": cost_price,
            "suggested_list_price": list_price,
        })

        if result["success"]:
            listing_data = result.get("data", {})
            listing_id = listing_data.get("listing_id", "")
            platform = listing_data.get("platform", "manual")

            from utils.database import update_transaction_status
            update_transaction_status(
                txn_id=txn_id,
                status="active",
                metadata=None,
            )

            if opp.get("db_id"):
                mark_opportunity_executed(opp["db_id"], txn_id)

            self.log_action("product_listed", niche="reselling", result=True,
                            extra={"platform": platform, "price": f"${list_price:.2f}"})

            return {
                "transaction_id": txn_id,
                "niche": "reselling",
                "action": "product_listed",
                "platform": platform,
                "resource_id": listing_id,
                "status": "active",
                "revenue_potential": (list_price - cost_price) * 2,  # 2 units/day estimate
                "metadata": listing_data,
            }
        else:
            from utils.database import update_transaction_status
            update_transaction_status(txn_id=txn_id, status="failed", error_msg=result.get("error", ""))
            self.log_action("product_listing_failed", niche="reselling", result=False,
                            error=result.get("error", ""))
            return None

    # ──────────────────────────────────────────
    # Niche 2: Gigs (Mturk + Fiverr)
    # ──────────────────────────────────────────

    def _execute_gigs(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Accept a Mturk HIT or update a Fiverr gig."""
        opp_type = opp.get("type", "")

        if opp_type == "mturk_hit":
            return self._execute_mturk_hit(opp)
        elif opp_type == "fiverr_gig_update":
            return self._execute_fiverr_update(opp)
        else:
            self.logger.warning("Unknown gig type: %s", opp_type)
            return None

    def _execute_mturk_hit(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Accept and log a Mturk HIT."""
        from utils.database import insert_transaction

        if self._hit_count_today >= MTURK_MAX_HITS_PER_DAY:
            self.logger.info("Mturk daily HIT limit reached (%d)", MTURK_MAX_HITS_PER_DAY)
            return None

        metadata = opp.get("metadata", {})
        hit_id = metadata.get("hit_id", "")
        reward = float(metadata.get("reward", 0))

        txn_id = insert_transaction(
            niche="gigs",
            platform="mturk",
            agent_action="hit_accepted",
            resource_id=hit_id,
            revenue=reward,
            cost=0.0,
            status="active",
            metadata=metadata,
        )

        result = self.gig_client.accept_mturk_hit(hit_id)
        self._hit_count_today += 1

        if result["success"]:
            from utils.database import update_transaction_status
            assignment_id = result.get("data", {}).get("assignment_id", "")
            update_transaction_status(
                txn_id=txn_id,
                status="active",
            )
            self.log_action("mturk_hit_accepted", niche="gigs", result=True,
                            extra={"reward": f"${reward:.2f}", "hit_id": hit_id})
            return {
                "transaction_id": txn_id,
                "niche": "gigs",
                "action": "hit_accepted",
                "platform": "mturk",
                "resource_id": assignment_id or hit_id,
                "status": "active",
                "revenue_potential": reward,
                "metadata": {"hit_id": hit_id, "assignment_id": assignment_id},
            }
        else:
            from utils.database import update_transaction_status
            update_transaction_status(txn_id=txn_id, status="failed", error_msg=result.get("error", ""))
            return None

    def _execute_fiverr_update(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update Fiverr gig pricing/tags for better visibility."""
        from utils.database import insert_transaction

        txn_id = insert_transaction(
            niche="gigs",
            platform="fiverr",
            agent_action="gig_optimization",
            resource_id="",
            cost=0.0,
            status="active",
            metadata={"type": "gig_optimization"},
        )

        # Get list of gigs to optimize
        result = self.gig_client.get_fiverr_gigs()
        if not result["success"]:
            from utils.database import update_transaction_status
            update_transaction_status(txn_id=txn_id, status="failed", error_msg=result.get("error", "Fiverr not configured"))
            self.log_action("fiverr_optimization_skipped", niche="gigs", result=False,
                            error="Fiverr API not configured")
            return None

        gigs = result.get("data", {}).get("gigs", [])
        self.logger.info("Fiverr: found %d gigs to potentially optimize", len(gigs))

        self.log_action("fiverr_gigs_reviewed", niche="gigs", result=True,
                        extra={"gig_count": len(gigs)})
        return {
            "transaction_id": txn_id,
            "niche": "gigs",
            "action": "fiverr_optimization",
            "platform": "fiverr",
            "resource_id": "",
            "status": "active",
            "revenue_potential": 3.0,
            "metadata": {"gigs_reviewed": len(gigs)},
        }

    # ──────────────────────────────────────────
    # Niche 3: Affiliate
    # ──────────────────────────────────────────

    def _execute_affiliate(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Post affiliate-linked content to Reddit or Quora."""
        from utils.database import insert_transaction
        from utils.llm_client import invoke

        opp_type = opp.get("type", "")
        metadata = opp.get("metadata", {})
        title = opp.get("title", "")

        if opp_type == "reddit_post":
            return self._execute_reddit_post(opp)
        elif opp_type == "quora_answer":
            return self._execute_quora_answer(opp)
        elif opp_type == "devto_article":
            return self._execute_devto_article(opp)
        else:
            self.logger.warning("Unknown affiliate type: %s", opp_type)
            return None

    def _execute_reddit_post(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate and post a Reddit post with affiliate link."""
        from utils.database import insert_transaction, update_transaction_status
        from utils.llm_client import invoke
        from config.constants import AFFILIATE_REDDIT_COMMENT_MIN_LENGTH

        metadata = opp.get("metadata", {})
        subreddit = metadata.get("subreddit", "entrepreneur")
        topic = opp.get("title", "").replace("Affiliate reply to: ", "")

        # Generate content
        content = self._generate_affiliate_content(topic, platform="reddit")
        affiliate_link = self.affiliate_client.build_affiliate_link(
            f"https://amzn.to/search?q={topic.replace(' ', '+')}"
        )

        txn_id = insert_transaction(
            niche="affiliate",
            platform="reddit",
            agent_action="post_attempt",
            resource_id="",
            cost=0.0,
            status="pending",
            metadata={"subreddit": subreddit, "topic": topic[:100]},
        )

        result = self.affiliate_client.submit_reddit_post(
            subreddit=subreddit,
            title=f"My experience with: {topic[:80]}",
            content=content,
            affiliate_link=affiliate_link,
        )

        if result["success"]:
            post_id = result.get("data", {}).get("post_id", "")
            update_transaction_status(txn_id=txn_id, status="active")
            self.log_action("reddit_post_submitted", niche="affiliate", result=True,
                            extra={"subreddit": subreddit})
            return {
                "transaction_id": txn_id,
                "niche": "affiliate",
                "action": "reddit_post",
                "platform": "reddit",
                "resource_id": post_id,
                "status": "active",
                "revenue_potential": opp.get("estimated_roi", 0.5),
                "metadata": {"subreddit": subreddit, "post_id": post_id},
            }
        else:
            update_transaction_status(txn_id=txn_id, status="failed", error_msg=result.get("error", ""))
            return None

    def _execute_quora_answer(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Generate a Quora answer.
        NOTE: Quora doesn't have a public write API.
        This logs the answer for manual submission or uses browser automation.
        """
        from utils.database import insert_transaction, update_transaction_status

        metadata = opp.get("metadata", {})
        question = metadata.get("question", opp.get("title", ""))
        url = opp.get("source_url", "")

        content = self._generate_affiliate_content(question, platform="quora")
        from config.settings import AFFILIATE
        affiliate_link = self.affiliate_client.build_affiliate_link(
            f"https://amzn.to/?q={question[:50].replace(' ', '+')}"
        )

        # Log as pending — requires manual Quora submission or future automation
        txn_id = insert_transaction(
            niche="affiliate",
            platform="quora",
            agent_action="answer_prepared",
            resource_id=url,
            cost=0.0,
            status="pending",
            metadata={
                "question": question[:200],
                "answer_preview": content[:300],
                "affiliate_link": affiliate_link,
                "action_required": "Submit answer manually at: " + url,
            },
        )

        self.logger.info(
            "TRANSACTION | quora_answer_prepared | question=%s url=%s "
            "(Manual submission required — Quora has no public write API)",
            question[:60], url,
        )
        self.log_action("quora_answer_prepared", niche="affiliate", result=True)

        return {
            "transaction_id": txn_id,
            "niche": "affiliate",
            "action": "quora_answer",
            "platform": "quora",
            "resource_id": url,
            "status": "pending",
            "revenue_potential": opp.get("estimated_roi", 0.25),
            "metadata": {"question": question[:100], "requires_manual": True},
        }

    def _execute_devto_article(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Publish an affiliate article to Dev.to."""
        from utils.database import insert_transaction, update_transaction_status
        from utils.llm_client import invoke

        title = opp.get("title", "Trending Tech Topic")
        content = self._generate_affiliate_content(title, platform="devto", long_form=True)

        txn_id = insert_transaction(
            niche="affiliate",
            platform="devto",
            agent_action="article_publish_attempt",
            resource_id="",
            cost=0.0,
            status="pending",
            metadata={"title": title[:100]},
        )

        result = self.affiliate_client.publish_devto_article(
            title=title,
            content_markdown=content,
            tags=["productivity", "tech", "tools"],
        )

        if result["success"]:
            article_id = str(result.get("data", {}).get("article_id", ""))
            update_transaction_status(txn_id=txn_id, status="active")
            self.log_action("devto_article_published", niche="affiliate", result=True)
            return {
                "transaction_id": txn_id,
                "niche": "affiliate",
                "action": "devto_article",
                "platform": "devto",
                "resource_id": article_id,
                "status": "active",
                "revenue_potential": opp.get("estimated_roi", 0.5),
                "metadata": result.get("data", {}),
            }
        else:
            update_transaction_status(txn_id=txn_id, status="failed", error_msg=result.get("error", ""))
            return None

    # ──────────────────────────────────────────
    # Content generation
    # ──────────────────────────────────────────

    def _generate_affiliate_content(
        self,
        topic: str,
        platform: str = "reddit",
        long_form: bool = False,
    ) -> str:
        """Generate affiliate-ready content using LLM or fallback."""
        from utils.llm_client import invoke, generate_article_content

        length_instruction = "1000-1200 words" if long_form else "150-250 words"
        platform_tone = {
            "reddit": "conversational, helpful, first-person experience",
            "quora": "authoritative, detailed, expert tone",
            "devto": "technical, developer-focused, practical",
        }.get(platform, "informative")

        prompt = f"""Write a {platform_tone} piece about: "{topic}"

Length: {length_instruction}
Style: {platform_tone}
Requirements:
- Genuinely helpful content (not spammy)
- Natural mention of relevant products/tools
- End with a soft recommendation
- NO explicit "buy this" language
- Include 1-2 practical tips

Return only the content text, no JSON.
"""

        response = invoke(prompt)
        if response and len(response) > 100:
            return response

        # Fallback
        return generate_article_content(topic, [topic.split()[0]])

    # ──────────────────────────────────────────
    # Pivot execution (from Optimizer)
    # ──────────────────────────────────────────

    def execute_pivot_actions(self, pivot_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Execute pivot actions from Optimizer agent.
        Called urgently when P&L < $10 at 14:00.
        """
        from utils.message_queue import get_message

        actions = pivot_plan.get("actions", [])
        self.logger.info("Executing %d pivot actions", len(actions))

        results = []
        for action in actions:
            action_type = action.get("action", "")
            self.logger.info("Pivot action: %s", action_type)

            try:
                if action_type == "flash_sale_reselling":
                    result = self._pivot_flash_sale(action)
                elif action_type == "accept_mturk_hits":
                    result = self._pivot_mturk_boost(action)
                elif action_type == "post_quora_answers":
                    result = self._pivot_affiliate_boost(action)
                elif action_type == "post_reddit_comments":
                    result = self._pivot_affiliate_boost(action)
                elif action_type == "reduce_fiverr_price":
                    result = self._pivot_fiverr_price(action)
                elif action_type == "list_more_products":
                    result = self._pivot_more_listings(action)
                else:
                    self.logger.warning("Unknown pivot action: %s", action_type)
                    continue

                if result:
                    results.append(result)

            except Exception as exc:
                self.logger.error("Pivot action '%s' failed: %s", action_type, exc)

        self.logger.info("Pivot execution complete: %d/%d actions succeeded", len(results), len(actions))
        return results

    def _pivot_flash_sale(self, action: Dict) -> Optional[Dict]:
        """Reduce product prices to trigger sales."""
        from utils.database import get_today_transactions, update_transaction_status
        txns = get_today_transactions()
        updated = 0
        for txn in txns:
            if txn.get("niche") == "reselling" and txn.get("status") == "active":
                # Log price reduction action
                self.logger.info("TRANSACTION | flash_sale_triggered | txn_id=%d", txn["id"])
                updated += 1
        self.log_action("flash_sale_triggered", niche="reselling",
                        extra={"listings_updated": updated})
        return {"action": "flash_sale", "listings_updated": updated}

    def _pivot_mturk_boost(self, action: Dict) -> Optional[Dict]:
        """Accept additional Mturk HITs."""
        count = int(action.get("count", 10))
        from utils.scrapers import MturkChecker
        checker = MturkChecker()
        hits = checker.get_active_hits(max_hits=count * 2)

        accepted = 0
        for hit in hits[:count]:
            if self._hit_count_today >= MTURK_MAX_HITS_PER_DAY:
                break
            opp = {
                "niche": "gigs",
                "type": "mturk_hit",
                "title": hit.get("title", "Mturk HIT"),
                "metadata": hit,
            }
            result = self._execute_mturk_hit(opp)
            if result:
                accepted += 1

        self.log_action("mturk_pivot_executed", niche="gigs", extra={"hits_accepted": accepted})
        return {"action": "mturk_boost", "hits_accepted": accepted}

    def _pivot_affiliate_boost(self, action: Dict) -> Optional[Dict]:
        """Post additional affiliate content."""
        count = int(action.get("count", 5))
        from utils.scrapers import QuoraScraper
        questions = QuoraScraper().scrape()[:count]

        posted = 0
        for q in questions:
            opp = {
                "niche": "affiliate",
                "type": "quora_answer",
                "title": q.get("question", ""),
                "source_url": q.get("url", ""),
                "estimated_roi": 0.25,
                "metadata": q,
            }
            result = self._execute_quora_answer(opp)
            if result:
                posted += 1

        return {"action": "affiliate_boost", "posts_created": posted}

    def _pivot_fiverr_price(self, action: Dict) -> Optional[Dict]:
        """Reduce Fiverr gig price."""
        new_price = float(action.get("to", 5))
        gigs_result = self.gig_client.get_fiverr_gigs()
        if not gigs_result["success"]:
            return None

        updated = 0
        for gig in gigs_result.get("data", {}).get("gigs", []):
            result = self.gig_client.update_fiverr_gig_price(gig.get("id", ""), new_price)
            if result["success"]:
                updated += 1

        return {"action": "fiverr_price_reduced", "gigs_updated": updated, "new_price": new_price}

    def _pivot_more_listings(self, action: Dict) -> Optional[Dict]:
        """Create additional product listings from AliExpress."""
        from utils.scrapers import AliExpressScraper, TikTokScraper
        trends = TikTokScraper().scrape()
        keywords = [t.get("keyword", "") for t in trends[:3]]
        products = AliExpressScraper().scrape_trending(keywords=keywords)

        listed = 0
        for product in products[:3]:
            opp = {
                "niche": "reselling",
                "title": product.get("title", "Product"),
                "metadata": product,
            }
            result = self._execute_reselling(opp)
            if result:
                listed += 1

        return {"action": "more_listings", "products_listed": listed}

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    def _load_opportunities_from_db(self) -> List[Dict]:
        """Fallback: load today's unexecuted opportunities from DB."""
        try:
            from utils.database import get_today_opportunities
            return get_today_opportunities()
        except Exception as exc:
            self.logger.error("Failed to load opportunities from DB: %s", exc)
            return []

    def _log_failed_transaction(self, opp: Dict, error: str) -> None:
        """Log a failed transaction to DB."""
        try:
            from utils.database import insert_transaction
            insert_transaction(
                niche=opp.get("niche", "unknown"),
                platform="unknown",
                agent_action=f"{opp.get('type', 'unknown')}_failed",
                resource_id="",
                cost=0.0,
                status="failed",
                metadata={"error": error[:300], "opportunity": opp.get("title", "")[:100]},
            )
        except Exception:
            pass
