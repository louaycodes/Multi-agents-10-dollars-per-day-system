# 🔧 Troubleshooting Guide

## Common Issues & Solutions

---

### ❌ `psycopg2.OperationalError: could not connect to server`

**Cause**: PostgreSQL container not ready or wrong credentials.

**Fix**:
```bash
# Check if container is healthy
docker-compose ps

# If not healthy, check logs
docker logs agents_postgres

# Test connection manually
docker exec -it agents_postgres psql -U postgres -d agents_db -c "SELECT 1;"

# Common cause: wrong password in .env
# Ensure DB_PASSWORD in .env matches POSTGRES_PASSWORD in docker-compose.yml
```

---

### ❌ `Ollama not reachable` / LLM timeout

**Cause**: Ollama not started, model not pulled, or container not ready.

**Fix**:
```bash
# Start Ollama
docker-compose up -d ollama

# Wait 30s, then pull model
docker exec agents_ollama ollama pull mistral

# Verify model exists
docker exec agents_ollama ollama list

# Test manually
curl http://localhost:11434/api/generate \
  -d '{"model":"mistral","prompt":"Hello","stream":false}'

# If Ollama is too slow, switch to OpenAI in .env:
# LLM_TYPE=openai
# OPENAI_API_KEY=sk-your-key
```

---

### ❌ `redis.exceptions.ConnectionError`

**Cause**: Redis not running. System falls back to in-memory queue automatically.

**Fix**:
```bash
docker-compose up -d redis
# The system logs: "Redis message queue connected at localhost:6379"
# If Redis stays down, the in-memory fallback is used (no data loss)
```

---

### ❌ `Mturk: An error occurred (RequestError)` / `InvalidClientTokenId`

**Cause**: Wrong Mturk credentials or hitting production endpoint from sandbox.

**Fix**:
```bash
# In .env:
MTURK_ACCESS_KEY=your_real_access_key   # From AWS IAM
MTURK_SECRET_KEY=your_real_secret_key
MTURK_SANDBOX=true    # Keep true until fully tested
MTURK_REGION=us-east-1   # Mturk only available in us-east-1

# Verify credentials work:
python -c "
import boto3
client = boto3.client('mturk',
    region_name='us-east-1',
    aws_access_key_id='YOUR_KEY',
    aws_secret_access_key='YOUR_SECRET',
    endpoint_url='https://mturk-requester-sandbox.us-east-1.amazonaws.com'
)
print(client.get_account_balance())
"
```

---

### ❌ Reddit API: `401 Unauthorized` / `403 Forbidden`

**Cause**: Wrong Reddit credentials or User-Agent format.

**Fix**:
```bash
# 1. Register a Reddit app at: https://www.reddit.com/prefs/apps
# 2. Choose "script" type (not web app or installed app)
# 3. Set redirect_uri to http://localhost:8080
# 4. Copy client_id (under app name) and client_secret

# In .env:
REDDIT_CLIENT_ID=your_14char_id
REDDIT_CLIENT_SECRET=your_27char_secret
REDDIT_USERNAME=your_reddit_username
REDDIT_PASSWORD=your_reddit_password
REDDIT_USER_AGENT=MultiAgentSystem/1.0 by /u/your_username

# Test Reddit auth:
python -c "
import requests
resp = requests.post('https://www.reddit.com/api/v1/access_token',
    auth=('YOUR_CLIENT_ID', 'YOUR_SECRET'),
    data={'grant_type': 'password', 'username': 'USER', 'password': 'PASS'},
    headers={'User-Agent': 'test/1.0'}
)
print(resp.json())
"
```

---

### ❌ Facebook Marketplace: `OAuthException` / `Invalid OAuth access token`

**Cause**: Facebook API token expired or wrong Page ID.

**Fix**:
```bash
# 1. Get a long-lived page access token:
#    Go to: https://developers.facebook.com/tools/explorer/
#    Select your app → Generate Token → select pages_manage_posts permission
# 2. Exchange for long-lived token (60-day expiry):
curl "https://graph.facebook.com/v18.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=YOUR_APP_ID
  &client_secret=YOUR_APP_SECRET
  &fb_exchange_token=SHORT_LIVED_TOKEN"

# 3. Get your Page ID:
curl "https://graph.facebook.com/v18.0/me/accounts?access_token=YOUR_TOKEN"

# In .env:
FACEBOOK_API_TOKEN=your_long_lived_token
FACEBOOK_PAGE_ID=123456789
FACEBOOK_MARKETPLACE_ENABLED=true
```

---

### ❌ `fake_useragent.errors.FakeUserAgentError`

**Cause**: fake-useragent can't download its database (network issue).

**Fix**:
```bash
# The scraper has a built-in fallback UA — this error is non-fatal
# If you want to pre-cache the database:
python -c "from fake_useragent import UserAgent; ua = UserAgent(); ua.update()"
```

---

### ❌ Agents not waking up at scheduled times

**Cause**: Timezone mismatch — system uses UTC by default.

**Fix**:
```bash
# In .env, all times are UTC:
TIMEZONE=UTC
MANAGER_HOUR=6     # 06:00 UTC = 07:00 CET = 02:00 EST

# To use local time, change TIMEZONE:
TIMEZONE=Europe/Paris     # Agent wakes at 06:00 Paris time
TIMEZONE=America/New_York

# Verify current UTC time:
python -c "from datetime import datetime; print(datetime.utcnow())"
```

---

### ❌ `ImportError: No module named 'crewai'`

**Cause**: Virtual environment not activated or packages not installed.

**Fix**:
```bash
source venv/bin/activate
pip install -r requirements.txt

# Verify key packages:
python -c "import crewai; import langchain; import apscheduler; print('All OK')"
```

---

### ❌ LLM returns invalid JSON / `invoke_json returns None`

**Cause**: LLM hallucinated non-JSON text or wrapped JSON in markdown.

**Fix**: This is handled automatically by the validator and retry logic. The system:
1. Strips markdown code fences
2. Searches for first `{...}` block
3. Retries with stricter JSON-only prompt
4. Falls back to rule-based logic after 3 attempts

If JSON failures are persistent:
```bash
# Test LLM JSON quality:
python -c "
from utils.llm_client import invoke_json
result = invoke_json('Return JSON: {\"key\": \"value\"}', expected_keys=['key'])
print(result)
"
```

---

### ❌ `Quora/TikTok scrapers returning empty lists`

**Cause**: These platforms use JavaScript rendering — static HTML scraping is limited.

**Expected**: Both have hardcoded fallback datasets that are still useful for scoring. The system logs `"using fallback trend list"` when this happens.

**Fix (optional)**:
```bash
# TikTok: Install Selenium + ChromeDriver for JS scraping
# (not included by default to avoid ToS issues)

# For now, fallback trends cover 8 product categories
# which is sufficient for Day 1 product listing
```

---

### ❌ Revenue not appearing in P&L

**Cause**: Transactions are in `active` status but platforms haven't confirmed payment.

**Diagnosis**:
```bash
# Check transaction statuses
docker exec -it agents_postgres psql -U postgres -d agents_db \
  -c "SELECT niche, platform, agent_action, status, revenue, created_at FROM transactions WHERE date::date = CURRENT_DATE ORDER BY created_at DESC LIMIT 20;"

# Mturk: HITs are approved within 1-7 days
# Facebook: Orders update in near real-time
# Affiliate: Click revenue tracked asynchronously
```

**Fix**: Wait for Tracker's next 15-min polling cycle. It checks all platforms automatically.

---

### 🐳 Docker-specific

```bash
# Rebuild after code changes
docker-compose build app
docker-compose up -d app

# Access app container shell
docker exec -it agents_app bash

# Force restart
docker-compose restart app

# Full reset (WARNING: deletes DB data)
docker-compose down -v
docker-compose up -d
```
