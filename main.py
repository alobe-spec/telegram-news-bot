#!/usr/bin/env python3
"""
Telegram News Bot (Render/Railway-ready)
- Scrapes news sources (default: MyJoyOnline)
- Skips video posts
- Fetches image and article content
- Calls Groq API to produce HEADLINE + SUMMARY
- Posts to Telegram channel (image when available) with HTML formatting
- Scheduler posts at configured POST_TIMES (defaults: 07:00,10:00,12:30,14:30,17:00)
- Runs a Flask web endpoint for keep-alive / optional webhook
"""

import os
import time
import json
import logging
import hashlib
import random
import threading
from datetime import datetime
from itertools import cycle

import requests
from bs4 import BeautifulSoup
import schedule
from flask import Flask, request
from dotenv import load_dotenv

# Load local .env (for local dev). In production use host's env vars.
load_dotenv()

# -------------------------
# Configuration (env)
# -------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@trending_gh")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
NEWS_SOURCES = os.getenv(
    "NEWS_SOURCES",
    "https://www.myjoyonline.com/news/"
).split(',')
POST_TIMES = os.getenv("POST_TIMES", "07:00,10:00,12:30,14:30,17:00").split(',')
POSTED_ARTICLES_FILE = os.getenv("POSTED_ARTICLES_FILE", "posted_articles.json")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "30"))
MAX_FETCH_LINKS = int(os.getenv("MAX_FETCH_LINKS", "30"))
MAX_STORED = int(os.getenv("MAX_STORED", "500"))
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("news_bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger("newsbot")

# Safety checks for required secrets
if not TELEGRAM_BOT_TOKEN:
    logger.error("Missing TELEGRAM_BOT_TOKEN. Set it in environment variables.")
    raise SystemExit("Missing TELEGRAM_BOT_TOKEN")
if not GROQ_API_KEY:
    logger.error("Missing GROQ_API_KEY. Set it in environment variables.")
    raise SystemExit("Missing GROQ_API_KEY")

# -------------------------
# Flask keep-alive
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "🟢 Telegram News Bot — running"

# optional webhook endpoint (Telegram can post updates here if you set WEBHOOK_URL)
@app.route("/webhook", methods=["POST"])
def webhook():
    # This script will not implement webhook handling beyond accepting updates
    # because posting is scheduled. If you want webhook-based commands, add logic here.
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    # debug False for production
    app.run(host="0.0.0.0", port=port)

# -------------------------
# Persistence helpers
# -------------------------
def load_posted_articles():
    if os.path.exists(POSTED_ARTICLES_FILE):
        try:
            with open(POSTED_ARTICLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    if len(data) > MAX_STORED:
                        data = data[-MAX_STORED:]
                        save_posted_articles(data)
                    return data
        except Exception as e:
            logger.error(f"Error loading posted articles: {e}")
            return []
    return []

def save_posted_articles(posted_ids):
    try:
        with open(POSTED_ARTICLES_FILE, "w", encoding="utf-8") as f:
            json.dump(posted_ids[-MAX_STORED:], f, indent=2)
    except Exception as e:
        logger.error(f"Error saving posted articles: {e}")

def generate_article_id(title, link):
    return hashlib.md5(f"{title}{link}".encode("utf-8")).hexdigest()

# -------------------------
# Scraping helpers
# -------------------------
def is_video_article(link, title):
    indicators = ['/video/', '/videos/', '/watch/', 'youtube.com', 'youtu.be']
    title_video_words = ['video:', 'watch:', '[video]', 'video -', '- video']
    l = (link or "").lower()
    t = (title or "").lower()
    if any(i in l for i in indicators):
        return True
    if any(w in t for w in title_video_words):
        return True
    return False

def fetch_links_from_source(source_url, num_articles=MAX_FETCH_LINKS):
    try:
        logger.info(f"Fetching links from {source_url}")
        r = requests.get(source_url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if r.status_code != 200:
            logger.warning(f"Non-200 from {source_url}: {r.status_code}")
            return []
        soup = BeautifulSoup(r.content, "html.parser")
        anchors = soup.find_all("a", href=True)
        results = []
        seen_titles = set()
        for a in anchors:
            if len(results) >= num_articles:
                break
            href = a.get("href", "").strip()
            if not href or href.startswith("javascript:") or href == "#":
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 20 or title in seen_titles:
                continue
            # normalize to absolute
            if not href.startswith("http"):
                href = requests.compat.urljoin(source_url, href)
            # restrict to same domain to avoid linking off-site
            if source_url.split("/")[2] not in href:
                continue
            if is_video_article(href, title):
                continue
            # try to get image nearby
            image_url = ""
            parent = a.parent
            img = None
            for _ in range(4):
                if parent:
                    img = parent.find("img")
                    if img:
                        break
                    parent = parent.parent
            if img:
                image_url = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
                if image_url and not image_url.startswith("http"):
                    image_url = requests.compat.urljoin(source_url, image_url)
            article_id = generate_article_id(title, href)
            results.append({"id": article_id, "title": title, "link": href, "image_url": image_url})
            seen_titles.add(title)
        logger.info(f"Found {len(results)} articles on {source_url}")
        return results
    except Exception as e:
        logger.error(f"Error fetching links from {source_url}: {e}")
        return []

def fetch_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if r.status_code != 200:
            logger.debug(f"Failed to fetch article page: {r.status_code} {url}")
            return ""
        soup = BeautifulSoup(r.content, "html.parser")
        selectors = [
            ("article", {}),
            ("div", {"class": "entry-content"}),
            ("div", {"class": "article-content"}),
            ("div", {"class": "post-content"}),
            ("div", {"class": "content"}),
            ("div", {"class": "td-post-content"}),
        ]
        article_body = None
        for tag, attrs in selectors:
            article_body = soup.find(tag, attrs)
            if article_body:
                break
        if not article_body:
            paragraphs = soup.find_all("p")
            content = " ".join([p.get_text(strip=True) for p in paragraphs])
            return content[:10000]
        if article_body.find("iframe") or article_body.find("video"):
            logger.debug("Article contains embedded media; skipping")
            return ""
        paragraphs = article_body.find_all("p")
        content = " ".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        words = content.split()
        if len(words) > 500:
            content = " ".join(words[:500])
        return content
    except Exception as e:
        logger.debug(f"Error fetching article content: {e}")
        return ""

# -------------------------
# Groq summarizer
# -------------------------
def rephrase_with_groq(title, content):
    try:
        logger.debug("Calling Groq for headline+summary...")
        article_text = f"Title: {title}\n\nContent: {content}" if content else f"Title: {title}"
        prompt = f"""Based on this Ghanaian news article, create:
1. A short, catchy headline (5-8 words)
2. A 2-sentence summary of the main points

{article_text}

Format your response as:
HEADLINE: [your headline]
SUMMARY: [your 2 sentences]"""
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You are a news editor. Create concise headlines and summaries."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 200
        }
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=GROQ_TIMEOUT)
        if r.status_code != 200:
            logger.error(f"Groq API returned {r.status_code}: {r.text}")
            return None, None
        data = r.json()
        ai_response = data["choices"][0]["message"]["content"].strip()
        headline = ""
        summary = ""
        for line in ai_response.splitlines():
            if line.upper().startswith("HEADLINE:"):
                headline = line.split(":", 1)[1].strip()
            elif line.upper().startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()
        if not headline or not summary:
            lines = [l.strip() for l in ai_response.splitlines() if l.strip()]
            headline = lines[0] if lines else title
            summary = " ".join(lines[1:]) if len(lines) > 1 else ""
        return headline, summary
    except Exception as e:
        logger.error(f"Error calling Groq: {e}")
        return None, None

# -------------------------
# Telegram posting
# -------------------------
def send_to_telegram(headline, summary, image_url=None):
    try:
        full_text = f"<b>{headline}</b>\n\n{summary}\n\n📢 Subscribe: {TELEGRAM_CHANNEL_ID}"
        if image_url and image_url.startswith("http"):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            data = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url,
                "caption": full_text[:1024],
                "parse_mode": "HTML"
            }
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": full_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
        r = requests.post(url, data=data, timeout=15)
        if r.status_code == 200:
            logger.info("Posted to Telegram successfully")
            return True
        else:
            logger.error(f"Telegram API error {r.status_code}: {r.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending to Telegram: {e}")
        return False

# -------------------------
# Main posting workflow
# -------------------------
source_cycle = cycle([s.strip() for s in NEWS_SOURCES if s.strip()])

def post_news():
    try:
        logger.info("="*70)
        logger.info("RUNNING SCHEDULED NEWS POST")
        logger.info(f"Time: {datetime.now().isoformat()}")
        logger.info("="*70)

        posted_ids = load_posted_articles()

        # round-robin select a source
        source = next(source_cycle)
        articles = fetch_links_from_source(source, num_articles=MAX_FETCH_LINKS)
        if not articles:
            logger.warning("No articles found for this source; skipping run.")
            return

        # filter new
        new_articles = [a for a in articles if a["id"] not in posted_ids]
        if not new_articles:
            logger.info("No new articles to post.")
            return

        article = random.choice(new_articles)
        logger.info(f"Selected article: {article['title']} - {article['link']}")

        content = fetch_article_content(article["link"])
        if not content or len(content.split()) < 60:
            logger.warning("Article content too short; trying another if available.")
            other = [a for a in new_articles if a["id"] != article["id"]]
            if other:
                article = random.choice(other)
                content = fetch_article_content(article["link"])

        headline, summary = rephrase_with_groq(article["title"], content)
        if not headline or not summary:
            logger.warning("Groq failed; falling back to title.")
            headline = article["title"]
            summary = "Stay informed with the latest news."

        success = send_to_telegram(headline, summary, article.get("image_url"))
        if success:
            posted_ids.append(article["id"])
            save_posted_articles(posted_ids)
            logger.info("Post completed and saved.")
        else:
            logger.error("Failed to post to Telegram.")
    except Exception as e:
        logger.error(f"Unexpected error in post_news: {e}", exc_info=True)

# -------------------------
# Scheduler & start
# -------------------------
def schedule_jobs():
    # schedule at specified times (within 07:00-17:00)
    for t in POST_TIMES:
        schedule.every().day.at(t.strip()).do(post_news)
        logger.info(f"Scheduled post at {t.strip()}")
    # optional immediate run on startup
    try:
        post_news()
    except Exception:
        pass
    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    logger.info("Starting Telegram News Bot (Render-ready)")
    # start flask server and scheduler in threads
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=schedule_jobs, daemon=True).start()
    # keep the main thread alive
    while True:
        time.sleep(60)
