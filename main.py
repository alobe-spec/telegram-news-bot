import os
import time
import json
import logging
from datetime import datetime
from threading import Thread

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask

# ------------------------------
# 🔧 Configuration & Setup
# ------------------------------
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)

# Constants
BASE_URL = "https://www.myjoyonline.com/news/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}
POSTED_FILE = "posted_articles.json"
KEEPALIVE_INTERVAL = 30  # Ping every 30 seconds to keep bot alive
POST_INTERVAL = 3 * 60 * 60  # Post every 3 hours (in seconds)
CHANNEL_HANDLE = "@trending_gh"
CHANNEL_LINK = f"👉 Join [{CHANNEL_HANDLE}](https://t.me/trending_gh)"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s",
    handlers=[
        logging.FileHandler("news_bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ------------------------------
# 📁 Data Persistence
# ------------------------------
def load_posted_articles():
    """Load previously posted article URLs from JSON file"""
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(f"✅ Loaded {len(data)} posted articles from file")
                return set(data)
        except json.JSONDecodeError as e:
            logger.error(f"❌ Error reading posted articles file: {e}")
            return set()
    else:
        logger.info("📝 No posted articles file found, creating new one")
        return set()

def save_posted_articles(articles_set):
    """Save posted articles to disk"""
    try:
        with open(POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(articles_set), f, indent=2)
        logger.info(f"💾 Saved {len(articles_set)} posted articles to file")
    except Exception as e:
        logger.error(f"❌ Error saving posted articles: {e}")

# Initialize posted articles set
posted_articles = load_posted_articles()

# ------------------------------
# 🌐 Web Scraping - Get LATEST article
# ------------------------------
def get_latest_article():
    """Get the very latest article from the website"""
    logger.info(f"🔍 Fetching latest article from {BASE_URL}")
    
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        logger.debug(f"✅ Successfully fetched page (Status: {response.status_code})")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to fetch page: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Multiple selector strategies - prioritize top/featured articles
    # Strategy 1: Top featured articles (larger cards) - these are usually newest
    top_articles = soup.select("div.col-lg-6.col-md-6.col-sm-6.col-xs-12.mb-4")
    
    # Strategy 2: Grid articles (smaller cards)
    grid_articles = soup.select("div.col-lg-3.col-md-6.col-sm-6.col-xs-6.mb-4")
    
    # Strategy 3: Generic article containers (fallback)
    generic_articles = soup.select("div.item-details")
    
    # Prioritize top articles first (they're usually the latest)
    all_article_containers = top_articles + grid_articles + generic_articles
    
    logger.info(f"📊 Found {len(all_article_containers)} total articles on page")
    
    # Try to get the first valid article
    for idx, article in enumerate(all_article_containers, 1):
        try:
            # Extract link and title
            link_tag = article.select_one("a[href]")
            title_tag = article.select_one("h4, h3, h2, .entry-title")
            
            if not link_tag:
                continue
            
            link = link_tag.get("href", "").strip()
            
            # Ensure full URL
            if link and not link.startswith("http"):
                link = "https://www.myjoyonline.com" + link
            
            # Extract title
            if title_tag:
                title = title_tag.get_text(strip=True)
            elif link_tag:
                title = link_tag.get_text(strip=True)
            else:
                continue
            
            # Skip if no valid data
            if not title or not link or len(title) < 10:
                continue
            
            # Skip video content
            if "video" in link.lower() or "video" in title.lower():
                continue
            
            # Extract image
            img_tag = article.select_one("img")
            if not img_tag:
                # Try to find image in parent or sibling elements
                parent = article.find_parent()
                if parent:
                    img_tag = parent.select_one("img")
            
            image_url = None
            if img_tag:
                image_url = img_tag.get("src") or img_tag.get("data-src")
                if image_url and not image_url.startswith("http"):
                    image_url = "https://www.myjoyonline.com" + image_url
            
            logger.info(f"✅ Latest article found: {title[:60]}...")
            logger.info(f"🔗 URL: {link}")
            logger.info(f"📸 Image: {'Yes' if image_url else 'No'}")
            
            return {
                "title": title,
                "url": link,
                "image": image_url
            }
            
        except Exception as e:
            logger.debug(f"⚠️ Error parsing article {idx}: {e}")
            continue
    
    logger.warning("⚠️ No valid articles found on page")
    return None

# ------------------------------
# 🤖 AI Enhancement - Rephrase + Summarize
# ------------------------------
def create_post_content(title, url):
    """Create post with rephrased title and 2-sentence summary"""
    logger.info(f"🤖 Creating AI-enhanced post for: {title[:50]}...")
    
    if not GROQ_API_KEY:
        logger.warning("⚠️ No Groq API key, using basic format")
        return f"📰 *{title}*\n\n{CHANNEL_LINK}"
    
    try:
        # Fetch article content
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract article paragraphs
        paragraphs = []
        for p in soup.select("article p, .entry-content p, .post-content p, .article-body p"):
            text = p.get_text(strip=True)
            if text and len(text) > 50:  # Filter out short/empty paragraphs
                paragraphs.append(text)
        
        if not paragraphs:
            logger.warning("⚠️ No content paragraphs found, using basic format")
            return f"📰 *{title}*\n\n{CHANNEL_LINK}"
        
        # Limit content for AI processing
        content = " ".join(paragraphs[:6])[:2000]  # First 6 paragraphs, max 2000 chars
        
        logger.info(f"📝 Extracted {len(paragraphs)} paragraphs ({len(content)} chars)")
        
        # Call Groq API with specific instructions
        prompt = f"""You are a professional Ghanaian news editor. Create a Telegram post for this article.

TITLE: {title}

ARTICLE CONTENT:
{content}

REQUIREMENTS:
1. First line: Rephrase the title to be engaging and clear (max 15 words)
2. Next: Write EXACTLY 2 sentences summarizing the main points
3. Be professional, clear, and informative
4. Focus on the key facts
5. Do not add emojis or extra commentary

Format:
[Rephrased Title]

[Two sentence summary]"""
        
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "mixtral-8x7b-32768",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6,
            "max_tokens": 250
        }
        headers_api = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        res = requests.post(groq_url, headers=headers_api, json=payload, timeout=20)
        
        if res.status_code == 200:
            data = res.json()
            ai_content = data["choices"][0]["message"]["content"].strip()
            logger.info(f"✅ AI content generated successfully")
            
            # Format the final message
            message = f"📰 {ai_content}\n\n{CHANNEL_LINK}"
            return message
        else:
            logger.error(f"❌ Groq API error ({res.status_code}): {res.text[:200]}")
            return f"📰 *{title}*\n\n{CHANNEL_LINK}"
            
    except Exception as e:
        logger.error(f"❌ Error creating AI content: {e}")
        return f"📰 *{title}*\n\n{CHANNEL_LINK}"

# ------------------------------
# 📢 Telegram Posting
# ------------------------------
def send_to_telegram(content, image_url=None):
    """Send message to Telegram channel"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("❌ Missing Telegram credentials")
        return False
    
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    
    try:
        if image_url:
            logger.info(f"📸 Sending message with image to Telegram")
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "caption": content,
                "photo": image_url,
                "parse_mode": "Markdown"
            }
            response = requests.post(f"{base_url}/sendPhoto", data=payload, timeout=15)
        else:
            logger.info(f"📝 Sending text message to Telegram")
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": content,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            response = requests.post(f"{base_url}/sendMessage", json=payload, timeout=15)
        
        if response.status_code == 200:
            logger.info("✅ Successfully sent message to Telegram")
            return True
        else:
            logger.error(f"❌ Telegram API error ({response.status_code}): {response.text[:300]}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending to Telegram: {e}")
        return False

# ------------------------------
# 🔄 Main Job - Post Latest Article Every 3 Hours
# ------------------------------
def post_latest_article():
    """Check and post latest article if it's new"""
    logger.info("=" * 60)
    logger.info("🚀 CHECKING FOR LATEST ARTICLE TO POST")
    logger.info("=" * 60)
    logger.info(f"⏰ Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📊 Already posted: {len(posted_articles)} articles")
    
    # Get the latest article from website
    latest_article = get_latest_article()
    
    if not latest_article:
        logger.warning("⚠️ No article found on website")
        logger.info("=" * 60)
        return
    
    # Check if already posted
    if latest_article['url'] in posted_articles:
        logger.info(f"⏭️ Latest article already posted: {latest_article['title'][:60]}...")
        logger.info("=" * 60)
        return
    
    logger.info(f"🆕 NEW ARTICLE TO POST!")
    logger.info(f"📰 Title: {latest_article['title'][:80]}...")
    logger.info(f"🔗 URL: {latest_article['url']}")
    logger.info(f"📸 Image: {'Yes ✅' if latest_article['image'] else 'No ❌'}")
    
    try:
        # Create enhanced content
        message = create_post_content(latest_article['title'], latest_article['url'])
        
        # Send to Telegram
        success = send_to_telegram(message, latest_article['image'])
        
        if success:
            posted_articles.add(latest_article['url'])
            save_posted_articles(posted_articles)
            logger.info(f"✅ Article posted successfully!")
            logger.info(f"📊 Total articles posted: {len(posted_articles)}")
        else:
            logger.error(f"❌ Failed to post article")
            
    except Exception as e:
        logger.error(f"❌ Error posting article: {e}")
    
    logger.info("=" * 60)

# ------------------------------
# ⏱️ Keep Alive & Scheduler
# ------------------------------
def run_keepalive_and_scheduler():
    """Keep bot alive and run scheduled posts"""
    logger.info("=" * 60)
    logger.info("🚀 SCHEDULER STARTED")
    logger.info("=" * 60)
    logger.info(f"⏱️ Keep-alive ping: Every {KEEPALIVE_INTERVAL} seconds")
    logger.info(f"📅 Post schedule: Every 3 hours")
    logger.info(f"📊 Already posted: {len(posted_articles)} articles")
    
    # Run immediately on startup
    logger.info("🔄 Running initial check...")
    try:
        post_latest_article()
    except Exception as e:
        logger.error(f"❌ Error in initial run: {e}")
    
    last_post_time = time.time()
    check_count = 0
    
    while True:
        try:
            check_count += 1
            current_time = time.time()
            time_since_last_post = current_time - last_post_time
            time_until_next_post = POST_INTERVAL - time_since_last_post
            
            # Log keep-alive ping
            if check_count % 10 == 0:  # Log every 10th check (every 5 minutes)
                hours_since_last = time_since_last_post / 3600
                logger.info(f"💚 Keep-alive #{check_count} - {hours_since_last:.2f} hours since last post")
            
            # Check if it's time to post (every 3 hours)
            if time_since_last_post >= POST_INTERVAL:
                logger.info(f"⏰ 3 hours elapsed - time to check for latest article!")
                post_latest_article()
                last_post_time = time.time()
            else:
                logger.debug(f"⏳ Next post in {time_until_next_post/60:.1f} minutes")
            
            # Wait before next keep-alive check
            time.sleep(KEEPALIVE_INTERVAL)
            
        except Exception as e:
            logger.error(f"❌ Error in scheduler loop: {e}")
            time.sleep(KEEPALIVE_INTERVAL)

# ------------------------------
# 🌐 Flask Routes (Keep bot alive)
# ------------------------------
@app.route("/")
def home():
    """Health check endpoint"""
    logger.debug("🏠 Home endpoint accessed")
    uptime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"""
    <h1>✅ Telegram News Bot is Running</h1>
    <p>⏰ Current time: {uptime}</p>
    <p>📊 Articles posted: {len(posted_articles)}</p>
    <p>⏱️ Posts every: 3 hours</p>
    <p>🔗 <a href="/status">Detailed Status</a></p>
    <p>🔗 <a href="/ping">Ping Test</a></p>
    <p>🔗 <a href="/post_now">Post Latest Article Now</a></p>
    <hr>
    <p><em>Bot checks for latest article every 3 hours and posts if new...</em></p>
    """

@app.route("/post_now", methods=["GET", "POST"])
def post_now():
    """Manual trigger to post latest article"""
    logger.info("🎯 Manual post triggered via /post_now")
    Thread(target=post_latest_article, daemon=True).start()
    return "✅ Checking for latest article now! Check logs for progress."

@app.route("/ping")
def ping():
    """Simple ping endpoint"""
    logger.debug("🏓 Ping endpoint accessed")
    return "pong"

@app.route("/status")
def status():
    """Status information endpoint"""
    logger.debug("📊 Status endpoint accessed")
    return {
        "status": "running",
        "posted_articles_count": len(posted_articles),
        "post_interval": "Every 3 hours",
        "keepalive_interval_seconds": KEEPALIVE_INTERVAL,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID),
        "groq_configured": bool(GROQ_API_KEY),
        "timestamp": datetime.now().isoformat()
    }

# ------------------------------
# 🚀 Main Entry Point
# ------------------------------
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 TELEGRAM NEWS BOT STARTING")
    logger.info("=" * 60)
    
    # Validate environment variables
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set!")
    else:
        logger.info("✅ Telegram bot token configured")
    
    if not TELEGRAM_CHANNEL_ID:
        logger.error("❌ TELEGRAM_CHANNEL_ID not set!")
    else:
        logger.info("✅ Telegram channel ID configured")
    
    if not GROQ_API_KEY:
        logger.warning("⚠️ GROQ_API_KEY not set (AI features disabled)")
    else:
        logger.info("✅ Groq API key configured")
    
    # Start scheduler in background thread
    scheduler_thread = Thread(target=run_keepalive_and_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Scheduler thread started")
    
    # Start Flask app (keeps service alive on Render)
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🌐 Starting Flask app on port {port}")
    logger.info("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
