# 📘 API Reference — Autonomous Revenue Agent System

## Agents

### `ManagerAgent` — [`agents/manager.py`](../agents/manager.py)

| Method | Returns | Description |
|--------|---------|-------------|
| `generate_daily_plan()` | `dict` | Creates 06:00 allocation plan. Saves to DB + publishes to queue. |
| `daily_close()` | `dict` | Creates 20:00 final analysis. Calls LLM for tomorrow recommendations. |

**`generate_daily_plan()` output:**
```json
{
  "date": "2024-01-15",
  "target": 10.0,
  "allocations": {"reselling": 0.50, "gigs": 0.35, "affiliate": 0.15},
  "priority_niches": ["reselling", "gigs", "affiliate"],
  "strategy": "Scale reselling — 67% of yesterday's revenue",
  "expected_revenue": 11.5,
  "llm_reasoning": "Reselling outperformed all niches..."
}
```

---

### `HunterAgent` — [`agents/hunter.py`](../agents/hunter.py)

| Method | Returns | Description |
|--------|---------|-------------|
| `find_opportunities()` | `list[dict]` | Runs scrapers, scores results, saves top 8 to DB + queue. |
| `_score_and_rank(opps)` | `list[dict]` | Applies `(roi * feasibility * time_to_profit) / effort_hours`. |
| `_filter_opportunities(opps)` | `list[dict]` | Removes high-risk + low-feasibility opportunities. |

**Opportunity dict format:**
```json
{
  "niche": "reselling",
  "type": "product_listing",
  "title": "Anti-slip phone grip",
  "estimated_roi": 6.0,
  "feasibility_score": 8.5,
  "effort_hours": 0.5,
  "time_to_profit": 1.0,
  "competition": "low",
  "capital_required": 1.50,
  "risk_level": "low",
  "composite_score": 102.0
}
```

---

### `ExecutorAgent` — [`agents/executor.py`](../agents/executor.py)

| Method | Returns | Description |
|--------|---------|-------------|
| `execute_transactions()` | `list[dict]` | Reads queue, dispatches to 3 niches, logs all transactions. |
| `execute_pivot_actions(plan)` | `list[dict]` | Runs Optimizer pivot actions urgently. |

**Transaction result format:**
```json
{
  "transaction_id": 42,
  "niche": "reselling",
  "action": "product_listed",
  "platform": "facebook_marketplace",
  "resource_id": "LISTING_ABC123",
  "status": "active",
  "revenue_potential": 4.50
}
```

---

### `TrackerAgent` — [`agents/tracker.py`](../agents/tracker.py)

| Method | Returns | Description |
|--------|---------|-------------|
| `update_pnl()` | `dict` | Polls APIs, updates transactions, recalculates P&L. |
| `check_optimizer_trigger()` | `bool` | At 14:00: returns True if P&L < target and Optimizer triggered. |
| `generate_daily_report()` | `dict` | At 20:00: final report + DB save. |
| `get_current_pnl()` | `dict` | Returns current P&L snapshot from DB. |

**P&L snapshot format:**
```json
{
  "date": "2024-01-15",
  "last_update": "2024-01-15T14:00:00",
  "revenue_total": 8.50,
  "revenue_by_niche": {"reselling": 5.0, "gigs": 2.5, "affiliate": 1.0},
  "cost_total": 3.00,
  "profit": 5.50,
  "target": 10.0,
  "target_met": false,
  "alert": "P&L = $5.50, $4.50 remaining"
}
```

---

### `OptimizerAgent` — [`agents/optimizer.py`](../agents/optimizer.py)

| Method | Returns | Description |
|--------|---------|-------------|
| `run()` | `dict\|None` | Checks queue, generates pivot if alert exists. |
| `generate_pivot(alert)` | `dict` | Full pivot analysis + LLM/rule-based actions. |

**Optimization plan format:**
```json
{
  "alert_time": "2024-01-15T14:00:00",
  "current_pnl": 6.50,
  "gap": 3.50,
  "severity": "medium",
  "actions": [
    {"action": "accept_mturk_hits", "count": 8, "expected_revenue": 2.0},
    {"action": "flash_sale_reselling", "discount": 0.30, "expected_revenue": 2.0}
  ],
  "expected_additional_revenue": 4.0,
  "confidence": 0.80
}
```

---

## Utils

### `utils/database.py`

| Function | Returns | Description |
|----------|---------|-------------|
| `init_db()` | `None` | Initializes connection pool. Call once at startup. |
| `get_yesterday_results()` | `dict` | Previous day's P&L. Returns zeroed baseline on Day 1. |
| `get_historical_performance(days=7)` | `list[dict]` | Last N days of daily_results. |
| `upsert_daily_result(...)` | `None` | Insert or update today's daily result. |
| `save_daily_plan(plan)` | `None` | Persist Manager's daily plan. |
| `get_daily_plan(date=None)` | `dict\|None` | Retrieve today's plan. |
| `insert_transaction(...)` | `int` | Insert transaction, returns row ID. |
| `update_transaction_status(id, status, revenue, ...)` | `None` | Update transaction after payment. |
| `get_today_transactions()` | `list[dict]` | All today's transactions. |
| `get_current_pnl()` | `dict` | Real-time P&L from paid transactions. |
| `insert_opportunity(opp)` | `int` | Save Hunter opportunity to DB. |
| `get_today_opportunities()` | `list[dict]` | Unexecuted opportunities for today. |
| `insert_alert(...)` | `int` | Log a system alert. |
| `resolve_alert(id, action)` | `None` | Mark alert as resolved. |
| `get_performance_metrics()` | `list[dict]` | Latest rolling metrics per niche. |
| `upsert_performance_metrics(...)` | `None` | Update niche rolling averages. |

---

### `utils/message_queue.py`

| Function | Returns | Description |
|----------|---------|-------------|
| `init_queue()` | `None` | Connect to Redis or enable in-memory fallback. |
| `publish(channel, payload)` | `bool` | Send message to channel. |
| `get_message(channel, timeout)` | `dict\|None` | Get next message (blocking). |
| `get_all_messages(channel)` | `list[dict]` | Drain all pending messages. |
| `peek_latest(channel)` | `dict\|None` | Read latest without removing (Redis only). |
| `queue_size(channel)` | `int` | Count pending messages. |

**Channel names (from `config/constants.py`):**

| Constant | Value | Direction |
|----------|-------|-----------|
| `CHANNEL_MANAGER_TO_ALL` | `manager:daily_plan` | Manager → All |
| `CHANNEL_HUNTER_TO_EXECUTOR` | `hunter:opportunities` | Hunter → Executor |
| `CHANNEL_EXECUTOR_TO_TRACKER` | `executor:transactions` | Executor → Tracker |
| `CHANNEL_TRACKER_TO_OPTIMIZER` | `tracker:alerts` | Tracker → Optimizer |
| `CHANNEL_OPTIMIZER_TO_EXECUTOR` | `optimizer:pivot_actions` | Optimizer → Executor |

---

### `utils/llm_client.py`

| Function | Returns | Description |
|----------|---------|-------------|
| `init_llm()` | `str` | Detect available backend, return name (`'ollama'`, `'openai'`, `'fallback'`). |
| `invoke(prompt)` | `str` | Call best available LLM, return raw text. |
| `invoke_json(prompt, expected_keys)` | `dict\|None` | Call LLM, parse+validate JSON, retry on failure. |
| `generate_manager_plan_fallback(yesterday, metrics)` | `dict` | Rule-based plan without LLM. |
| `generate_article_content(topic, keywords)` | `str` | Basic article template without LLM. |

---

### `utils/scrapers.py`

| Class | Method | Returns | Description |
|-------|--------|---------|-------------|
| `RedditScraper` | `scrape(subreddit, keywords)` | `list[dict]` | Posts with score, comments, matched keywords |
| `RedditScraper` | `scrape_multiple(keywords)` | `list[dict]` | All configured subreddits |
| `TikTokScraper` | `scrape()` | `list[dict]` | Trending keywords (with fallback) |
| `AliExpressScraper` | `scrape_trending(keywords)` | `list[dict]` | Products with cost + suggested price |
| `QuoraScraper` | `scrape()` | `list[dict]` | Questions with estimated views |
| `MturkChecker` | `get_active_hits(min_reward, max_hits)` | `list[dict]` | Available HITs |
| `MturkChecker` | `get_account_balance()` | `float` | Mturk account balance |

---

### `utils/api_clients.py`

| Class | Method | Returns | Description |
|-------|--------|---------|-------------|
| `ProductResellClient` | `list_product(product)` | `dict` | List on Facebook/Shopee/manual |
| `ProductResellClient` | `create_facebook_listing(...)` | `dict` | Facebook Graph API listing |
| `ProductResellClient` | `check_facebook_orders(listing_id)` | `dict` | Check sold orders + revenue |
| `AffiliateClient` | `submit_reddit_post(subreddit, title, content, link)` | `dict` | Post to Reddit |
| `AffiliateClient` | `post_reddit_comment(subreddit, post_id, content, link)` | `dict` | Comment on Reddit post |
| `AffiliateClient` | `publish_devto_article(title, content, tags)` | `dict` | Publish to Dev.to |
| `AffiliateClient` | `build_affiliate_link(url, tag)` | `str` | Append affiliate tag |
| `GigClient` | `get_fiverr_gigs()` | `dict` | List active Fiverr gigs |
| `GigClient` | `update_fiverr_gig_price(gig_id, price)` | `dict` | Update gig price via API |
| `GigClient` | `accept_mturk_hit(hit_id)` | `dict` | Accept HIT assignment |
| `GigClient` | `submit_mturk_hit(assignment_id, answers)` | `dict` | Submit HIT answers |
| `GigClient` | `get_mturk_balance()` | `float` | Mturk account balance |
| `GigClient` | `get_mturk_assignment_status(assignment_id)` | `dict` | Check if approved/paid |

---

## CLI Arguments

```bash
python main.py              # Start scheduled system (normal mode)
python main.py --now        # Run full morning cycle immediately (testing)
python main.py --agent manager    # Run only Manager agent
python main.py --agent hunter     # Run only Hunter agent
python main.py --agent executor   # Run only Executor agent
python main.py --agent tracker    # Run only Tracker agent
python main.py --agent optimizer  # Run only Optimizer check
```
