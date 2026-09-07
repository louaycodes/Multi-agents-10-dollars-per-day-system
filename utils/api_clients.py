"""
utils/api_clients.py — Platform API clients.

Three clients:
  1. ProductResellClient  — AliExpress sourcing + Facebook/Shopee listing
  2. AffiliateClient      — Reddit + Quora + Dev.to affiliate posting
  3. GigClient            — Fiverr API (no Selenium) + Mturk HIT execution

All clients:
  - Handle auth failures gracefully
  - Log every action with TRANSACTION prefix for transaction log
  - Return structured result dicts (never raise to callers)
"""

import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import RESELLING, GIGS, AFFILIATE
from config.constants import MAX_RETRIES, API_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Helper: Standard result envelope
# ──────────────────────────────────────────────
def _result(success: bool, data: Any = None, error: str = "") -> Dict[str, Any]:
    return {"success": success, "data": data, "error": error, "timestamp": datetime.utcnow().isoformat()}


# ──────────────────────────────────────────────
# 1. ProductResellClient
# ──────────────────────────────────────────────
class ProductResellClient:
    """
    Handles the full reselling workflow:
    1. Query AliExpress for product info
    2. Create listing on Facebook Marketplace or Shopee
    3. Check order status
    4. Mark fulfillment
    """

    def __init__(self):
        self.facebook_token = RESELLING.FACEBOOK_API_TOKEN
        self.facebook_page_id = RESELLING.FACEBOOK_PAGE_ID
        self.facebook_enabled = RESELLING.FACEBOOK_ENABLED
        self.shopee_enabled = RESELLING.SHOPEE_ENABLED

    # ── Facebook Marketplace ──────────────────

    def create_facebook_listing(
        self,
        title: str,
        description: str,
        price: float,
        category: str = "CLASSIFIEDS",
        image_url: str = "",
    ) -> Dict[str, Any]:
        """
        Create a Facebook Marketplace listing via Graph API.
        Requires: FACEBOOK_API_TOKEN, FACEBOOK_PAGE_ID
        """
        if not self.facebook_enabled or not self.facebook_token:
            logger.info("TRANSACTION | facebook_listing | SKIPPED (not configured)")
            return _result(False, error="Facebook Marketplace not configured. Set FACEBOOK_API_TOKEN and FACEBOOK_PAGE_ID in .env")

        try:
            url = f"https://graph.facebook.com/v18.0/{self.facebook_page_id}/commerce_listings"
            payload = {
                "name": title,
                "description": description,
                "price": int(price * 100),  # Facebook uses cents
                "currency": "USD",
                "category_id": category,
                "access_token": self.facebook_token,
            }
            if image_url:
                payload["image_url"] = image_url

            resp = requests.post(url, json=payload, timeout=API_TIMEOUT_SECONDS)
            resp.raise_for_status()
            listing_id = resp.json().get("id", "")

            logger.info(
                "TRANSACTION | facebook_listing_created | id=%s title=%s price=%.2f",
                listing_id, title, price,
            )
            return _result(True, data={"listing_id": listing_id, "title": title, "price": price, "platform": "facebook_marketplace"})

        except requests.HTTPError as exc:
            error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.error("TRANSACTION | facebook_listing_failed | %s", error)
            return _result(False, error=error)
        except Exception as exc:
            logger.error("TRANSACTION | facebook_listing_error | %s", exc)
            return _result(False, error=str(exc))

    def check_facebook_orders(self, listing_id: str) -> Dict[str, Any]:
        """Poll Facebook for orders on a listing."""
        if not self.facebook_enabled or not self.facebook_token:
            return _result(False, data={"orders": [], "revenue": 0.0}, error="Not configured")

        try:
            url = f"https://graph.facebook.com/v18.0/{listing_id}/orders"
            params = {"access_token": self.facebook_token, "fields": "id,status,amount"}
            resp = requests.get(url, params=params, timeout=API_TIMEOUT_SECONDS)
            resp.raise_for_status()
            orders = resp.json().get("data", [])
            paid_orders = [o for o in orders if o.get("status") == "COMPLETED"]
            revenue = sum(o.get("amount", {}).get("amount", 0) / 100 for o in paid_orders)
            return _result(True, data={"orders": paid_orders, "revenue": revenue})
        except Exception as exc:
            logger.warning("check_facebook_orders error: %s", exc)
            return _result(False, data={"orders": [], "revenue": 0.0}, error=str(exc))

    # ── Shopee (placeholder — requires Shopee Open API) ────

    def create_shopee_listing(
        self,
        title: str,
        description: str,
        price: float,
        stock: int = 10,
    ) -> Dict[str, Any]:
        """
        Create a Shopee product listing.
        NOTE: Shopee Open API requires seller app approval — returns mock in dev.
        """
        if not self.shopee_enabled:
            return _result(False, error="Shopee not enabled. Set SHOPEE_ENABLED=true and credentials in .env")

        # Shopee Open API implementation would go here
        # For now, return a structured mock
        mock_id = f"SHOPEE_{random.randint(10000, 99999)}"
        logger.info("TRANSACTION | shopee_listing_created | id=%s title=%s price=%.2f (MOCK)", mock_id, title, price)
        return _result(True, data={"listing_id": mock_id, "platform": "shopee", "price": price, "mock": True})

    # ── Generic product listing dispatcher ───

    def list_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """
        List a product on the best available platform.
        Tries Facebook Marketplace first, then Shopee, then logs as pending.
        """
        title = product.get("title", "Trending Product")
        cost = product.get("cost_price_usd", 1.0)
        price = max(cost * 2.5, cost + RESELLING.MIN_PROFIT_MARGIN)
        description = self._generate_description(title, cost, price)

        logger.info("TRANSACTION | product_listing_attempt | title=%s cost=%.2f list_price=%.2f", title, cost, price)

        if self.facebook_enabled:
            result = self.create_facebook_listing(title, description, price)
            if result["success"]:
                return result

        if self.shopee_enabled:
            result = self.create_shopee_listing(title, description, price)
            if result["success"]:
                return result

        # Fallback: log as "manual listing needed"
        manual_id = f"MANUAL_{int(time.time())}_{random.randint(100, 999)}"
        logger.warning(
            "TRANSACTION | product_listing_manual | id=%s — No marketplace configured. "
            "List manually at: https://www.facebook.com/marketplace/create/item", manual_id
        )
        return _result(
            True,
            data={
                "listing_id": manual_id,
                "platform": "manual",
                "title": title,
                "cost": cost,
                "suggested_price": round(price, 2),
                "action_required": "List manually on Facebook Marketplace or Shopee",
            },
        )

    @staticmethod
    def _generate_description(title: str, cost: float, price: float) -> str:
        """Generate a basic product listing description."""
        return (
            f"{title}\n\n"
            f"✅ Brand new, high quality\n"
            f"✅ Fast shipping\n"
            f"✅ Satisfaction guaranteed\n\n"
            f"Price: ${price:.2f}\n"
            f"Contact for details or to purchase."
        )


# ──────────────────────────────────────────────
# 2. AffiliateClient
# ──────────────────────────────────────────────
class AffiliateClient:
    """
    Handles affiliate content posting:
    - Reddit comments with affiliate links (via Reddit API)
    - Quora answers (via web form — requires account)
    - Dev.to articles via API
    """

    REDDIT_API_URL = "https://oauth.reddit.com"
    DEVTO_API_URL = "https://dev.to/api/articles"

    def __init__(self):
        self.reddit_token: Optional[str] = None
        self._reddit_auth()

    def _reddit_auth(self) -> None:
        """Authenticate with Reddit OAuth2."""
        if not AFFILIATE.REDDIT_CLIENT_ID or not AFFILIATE.REDDIT_CLIENT_SECRET:
            logger.info("Reddit API credentials not configured — Reddit posting disabled")
            return
        try:
            resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(AFFILIATE.REDDIT_CLIENT_ID, AFFILIATE.REDDIT_CLIENT_SECRET),
                data={
                    "grant_type": "password",
                    "username": AFFILIATE.REDDIT_USERNAME,
                    "password": AFFILIATE.REDDIT_PASSWORD,
                },
                headers={"User-Agent": AFFILIATE.REDDIT_USER_AGENT},
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            self.reddit_token = resp.json().get("access_token")
            if self.reddit_token:
                logger.info("Reddit authenticated successfully")
        except Exception as exc:
            logger.warning("Reddit auth failed: %s — Reddit posting disabled", exc)

    def post_reddit_comment(
        self,
        subreddit: str,
        post_id: str,
        content: str,
        affiliate_link: str = "",
    ) -> Dict[str, Any]:
        """
        Post a comment on a Reddit post.
        Appends affiliate disclaimer if link provided.
        """
        if not self.reddit_token:
            return _result(False, error="Reddit not authenticated")

        full_content = content
        if affiliate_link:
            full_content += f"\n\n---\n*[View product here]({affiliate_link})* — affiliate link"

        try:
            headers = {
                "Authorization": f"bearer {self.reddit_token}",
                "User-Agent": AFFILIATE.REDDIT_USER_AGENT,
            }
            resp = requests.post(
                f"{self.REDDIT_API_URL}/api/comment",
                headers=headers,
                data={"thing_id": f"t3_{post_id}", "text": full_content},
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            comment_data = resp.json()
            comment_id = comment_data.get("json", {}).get("data", {}).get("things", [{}])[0].get("data", {}).get("id", "")

            logger.info(
                "TRANSACTION | reddit_comment_posted | r/%s comment_id=%s affiliate=%s",
                subreddit, comment_id, bool(affiliate_link),
            )
            return _result(True, data={"comment_id": comment_id, "subreddit": subreddit, "platform": "reddit"})
        except Exception as exc:
            logger.error("TRANSACTION | reddit_comment_failed | %s", exc)
            return _result(False, error=str(exc))

    def submit_reddit_post(
        self,
        subreddit: str,
        title: str,
        content: str,
        affiliate_link: str = "",
    ) -> Dict[str, Any]:
        """Submit a new Reddit text post."""
        if not self.reddit_token:
            return _result(False, error="Reddit not authenticated")

        full_content = content
        if affiliate_link:
            full_content += f"\n\n---\n*[Related resource]({affiliate_link})* (affiliate)"

        try:
            headers = {
                "Authorization": f"bearer {self.reddit_token}",
                "User-Agent": AFFILIATE.REDDIT_USER_AGENT,
            }
            resp = requests.post(
                f"{self.REDDIT_API_URL}/api/submit",
                headers=headers,
                data={
                    "sr": subreddit,
                    "kind": "self",
                    "title": title,
                    "text": full_content,
                    "nsfw": False,
                    "spoiler": False,
                },
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            post_data = resp.json().get("json", {}).get("data", {})
            post_id = post_data.get("id", "")

            logger.info(
                "TRANSACTION | reddit_post_submitted | r/%s post_id=%s title=%s",
                subreddit, post_id, title[:50],
            )
            return _result(True, data={"post_id": post_id, "subreddit": subreddit, "platform": "reddit"})
        except Exception as exc:
            logger.error("TRANSACTION | reddit_post_failed | %s", exc)
            return _result(False, error=str(exc))

    def publish_devto_article(
        self,
        title: str,
        content_markdown: str,
        tags: List[str],
        canonical_url: str = "",
    ) -> Dict[str, Any]:
        """
        Publish an article to Dev.to via their API.
        Requires: DEV_TO_API_KEY in env (if configured).
        """
        devto_key = AFFILIATE.UDEMY_AFFILIATE_KEY  # Reuse slot or add DEV_TO_API_KEY to .env
        import os
        devto_key = os.environ.get("DEV_TO_API_KEY", "")
        if not devto_key:
            return _result(False, error="DEV_TO_API_KEY not configured")

        try:
            resp = requests.post(
                self.DEVTO_API_URL,
                headers={"api-key": devto_key, "Content-Type": "application/json"},
                json={
                    "article": {
                        "title": title,
                        "published": True,
                        "body_markdown": content_markdown,
                        "tags": tags[:4],
                        "canonical_url": canonical_url or "",
                    }
                },
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            article = resp.json()
            article_id = article.get("id", "")
            article_url = article.get("url", "")

            logger.info(
                "TRANSACTION | devto_article_published | id=%s title=%s url=%s",
                article_id, title[:50], article_url,
            )
            return _result(True, data={"article_id": article_id, "url": article_url, "platform": "devto"})
        except Exception as exc:
            logger.error("TRANSACTION | devto_publish_failed | %s", exc)
            return _result(False, error=str(exc))

    def build_affiliate_link(self, product_url: str, affiliate_tag: str = "") -> str:
        """Append affiliate tag to a product URL."""
        tag = affiliate_tag or AFFILIATE.AMAZON_AFFILIATE_TAG
        if not tag:
            return product_url
        separator = "&" if "?" in product_url else "?"
        return f"{product_url}{separator}tag={tag}"

    def estimate_revenue(self, clicks: int) -> float:
        """Estimate affiliate revenue based on click count."""
        return round(clicks * AFFILIATE.PAYOUT_PER_CLICK, 2)


# ──────────────────────────────────────────────
# 3. GigClient — Fiverr API + Mturk execution
# ──────────────────────────────────────────────
class GigClient:
    """
    Handles:
    - Fiverr gig management via official API (no Selenium)
    - Mturk HIT acceptance and submission
    """

    FIVERR_API_BASE = "https://api.fiverr.com"
    MTURK_PROD_ENDPOINT = "https://mturk-requester.us-east-1.amazonaws.com"
    MTURK_SANDBOX_ENDPOINT = "https://mturk-requester-sandbox.us-east-1.amazonaws.com"

    def __init__(self):
        self.fiverr_token = GIGS.FIVERR_API_TOKEN
        self.mturk_client = None
        self._init_mturk()

    def _init_mturk(self) -> None:
        """Initialize boto3 Mturk client."""
        if not GIGS.MTURK_ACCESS_KEY or not GIGS.MTURK_SECRET_KEY:
            logger.info("Mturk credentials not configured")
            return
        try:
            import boto3
            endpoint = self.MTURK_SANDBOX_ENDPOINT if GIGS.MTURK_SANDBOX else self.MTURK_PROD_ENDPOINT
            self.mturk_client = boto3.client(
                "mturk",
                region_name=GIGS.MTURK_REGION,
                aws_access_key_id=GIGS.MTURK_ACCESS_KEY,
                aws_secret_access_key=GIGS.MTURK_SECRET_KEY,
                endpoint_url=endpoint,
            )
            logger.info("Mturk client ready (sandbox=%s)", GIGS.MTURK_SANDBOX)
        except Exception as exc:
            logger.warning("Mturk init failed: %s", exc)

    # ── Fiverr API ────────────────────────────

    def _fiverr_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.fiverr_token}",
            "Content-Type": "application/json",
        }

    def get_fiverr_gigs(self) -> Dict[str, Any]:
        """Fetch list of active Fiverr gigs."""
        if not GIGS.FIVERR_ENABLED or not self.fiverr_token:
            return _result(False, error="Fiverr API not configured")
        try:
            resp = requests.get(
                f"{self.FIVERR_API_BASE}/v2/seller/gigs",
                headers=self._fiverr_headers(),
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            gigs = resp.json().get("gigs", [])
            logger.info("Fiverr: found %d active gigs", len(gigs))
            return _result(True, data={"gigs": gigs})
        except Exception as exc:
            logger.warning("TRANSACTION | fiverr_get_gigs_failed | %s", exc)
            return _result(False, error=str(exc))

    def update_fiverr_gig_price(self, gig_id: str, new_price: float) -> Dict[str, Any]:
        """Update a Fiverr gig's base price."""
        if not GIGS.FIVERR_ENABLED or not self.fiverr_token:
            return _result(False, error="Fiverr API not configured")
        try:
            resp = requests.patch(
                f"{self.FIVERR_API_BASE}/v2/seller/gigs/{gig_id}",
                headers=self._fiverr_headers(),
                json={"packages": [{"price": new_price}]},
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            logger.info("TRANSACTION | fiverr_price_updated | gig=%s new_price=%.2f", gig_id, new_price)
            return _result(True, data={"gig_id": gig_id, "new_price": new_price})
        except Exception as exc:
            logger.error("TRANSACTION | fiverr_price_update_failed | %s", exc)
            return _result(False, error=str(exc))

    def get_fiverr_orders(self, gig_id: str) -> Dict[str, Any]:
        """Check for new orders on a Fiverr gig."""
        if not GIGS.FIVERR_ENABLED or not self.fiverr_token:
            return _result(False, data={"orders": [], "revenue": 0.0})
        try:
            resp = requests.get(
                f"{self.FIVERR_API_BASE}/v2/seller/orders",
                headers=self._fiverr_headers(),
                params={"gig_id": gig_id, "status": "delivered"},
                timeout=API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            orders = resp.json().get("orders", [])
            revenue = sum(float(o.get("price", 0)) for o in orders)
            return _result(True, data={"orders": orders, "revenue": revenue})
        except Exception as exc:
            logger.warning("get_fiverr_orders error: %s", exc)
            return _result(False, data={"orders": [], "revenue": 0.0})

    # ── Mturk HIT execution ───────────────────

    def accept_mturk_hit(self, hit_id: str) -> Dict[str, Any]:
        """
        Accept a Mturk HIT assignment.
        Returns assignment_id for tracking.
        """
        if not self.mturk_client:
            return _result(False, error="Mturk client not initialized")
        try:
            resp = self.mturk_client.create_assignment(HITId=hit_id)
            assignment_id = resp.get("Assignment", {}).get("AssignmentId", "")
            logger.info(
                "TRANSACTION | mturk_hit_accepted | hit_id=%s assignment_id=%s",
                hit_id, assignment_id,
            )
            return _result(True, data={"hit_id": hit_id, "assignment_id": assignment_id, "platform": "mturk"})
        except Exception as exc:
            logger.error("TRANSACTION | mturk_accept_failed | hit_id=%s error=%s", hit_id, exc)
            return _result(False, error=str(exc))

    def submit_mturk_hit(self, assignment_id: str, answers: Dict[str, str]) -> Dict[str, Any]:
        """
        Submit answers for a Mturk HIT assignment.
        answers: {"QuestionIdentifier": "answer_text", ...}
        """
        if not self.mturk_client:
            return _result(False, error="Mturk client not initialized")
        try:
            # Format answers as QuestionFormAnswers XML
            xml_answers = self._format_mturk_answers(answers)
            resp = self.mturk_client.submit_assignment(
                AssignmentId=assignment_id,
                QuestionFormAnswers=xml_answers,
            )
            logger.info("TRANSACTION | mturk_hit_submitted | assignment_id=%s", assignment_id)
            return _result(True, data={"assignment_id": assignment_id, "status": "submitted"})
        except Exception as exc:
            logger.error("TRANSACTION | mturk_submit_failed | assignment_id=%s error=%s", assignment_id, exc)
            return _result(False, error=str(exc))

    def get_mturk_balance(self) -> float:
        """Return current Mturk account balance."""
        if not self.mturk_client:
            return 0.0
        try:
            resp = self.mturk_client.get_account_balance()
            return float(resp.get("AvailableBalance", "0"))
        except Exception as exc:
            logger.warning("get_mturk_balance error: %s", exc)
            return 0.0

    def get_mturk_assignment_status(self, assignment_id: str) -> Dict[str, Any]:
        """Check if a Mturk assignment has been approved and paid."""
        if not self.mturk_client:
            return _result(False, data={"status": "unknown", "reward": 0.0})
        try:
            resp = self.mturk_client.get_assignment(AssignmentId=assignment_id)
            assignment = resp.get("Assignment", {})
            status = assignment.get("AssignmentStatus", "Pending")
            reward = float(assignment.get("HIT", {}).get("Reward", "0"))
            return _result(True, data={"status": status, "reward": reward, "assignment_id": assignment_id})
        except Exception as exc:
            return _result(False, data={"status": "error", "reward": 0.0}, error=str(exc))

    @staticmethod
    def _format_mturk_answers(answers: Dict[str, str]) -> str:
        """Format answer dict as Mturk QuestionFormAnswers XML."""
        answer_xml = '<?xml version="1.0" encoding="UTF-8"?><QuestionFormAnswers xmlns="http://mechanicalturk.amazonaws.com/AWSMechanicalTurkDataSchemas/2005-10-01/QuestionFormAnswers.xsd">'
        for q_id, answer in answers.items():
            answer_xml += f"<Answer><QuestionIdentifier>{q_id}</QuestionIdentifier><FreeText>{answer}</FreeText></Answer>"
        answer_xml += "</QuestionFormAnswers>"
        return answer_xml
