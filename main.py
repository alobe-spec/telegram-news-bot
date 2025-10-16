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
import schedule

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
# 🌐 Web Scraping
# ------------------------------
def scrape_articles():
    """Scrape latest articles from MyJoyOnline with improved selectors"""
    logger.info(f"🔍 Starting to scrape articles from {BASE_URL}")
    
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        logger.info(f"✅ Successfully fetched page (Status: {response.status_code})")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to fetch page: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Multiple selector strategies for better coverage
    articles = []
    
    # Strategy 1: Top featured articles (larger cards)
    top_articles = soup.select("div.col-lg-6.col-md-6.col-sm-6.col-xs-12.mb-4")
    logger.info(f"📊 Found {len(top_articles)} top featured articles")
    
    # Strategy 2: Grid articles (smaller cards)
    grid_articles = soup.select("div.col-lg-3.col-md-6.col-sm-6.col-xs-6.mb-4")
    logger.info(f"📊 Found {len(grid_articles)} grid articles")
    
    # Strategy 3: Generic article containers (fallback)
    generic_articles = soup.select("div.item-details")
    logger.info(f"📊 Found {len(generic_articles)} generic article containers")
    
    all_article_containers = top_articles + grid_articles + generic_articles
    logger.info(f"📊 Total article containers found: {len(all_article_containers)}")
    
    for idx, article in enumerate(all_article_containers, 1):
        try:
            # Extract link and title
            link_tag = article.select_one("a[href]")
            title_tag = article.select_one("h4, h3, h2, .entry-title")
            
            if not link_tag:
                logger.debug(f"⚠️ Article {idx}: No link found, skipping")
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
                logger.debug(f"⚠️ Article {idx}: No title found, skipping")
                continue
            
            # Skip if no valid data
            if not title or not link or len(title) < 10:
                logger.debug(f"⚠️ Article {idx}: Invalid title or link, skipping")
                continue
            
            # Skip video content
            if "video" in link.lower() or "video" in title.lower():
                logger.debug(f"⚠️ Article {idx}: Video content, skipping")
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
            
            # Check if already posted
            if link in posted_articles:
                logger.debug(f"⏭️ Article {idx}: Already posted, skipping")
                continue
            
            articles.append({
                "title": title,
                "url": link,
                "image": image_url
            })
            
            logger.info(f"✅ Article {idx}: '{title[:50]}...'")
            
        except Exception as e:
            logger.error(f"❌ Error parsing article {idx}: {e}")
            continue
    
    logger.info(f"🎯 Successfully extracted {len(articles)} new articles")
    return articles

# ------------------------------
# 🤖 AI Content Enhancement
# ------------------------------
def enhance_with_ai(title, url):
    """Use Groq AI to create engaging summary"""
    logger.info(f"🤖 Attempting to enhance article with AI: {title[:50]}...")
    
    if not GROQ_API_KEY:
        logger.warning("⚠️ No Groq API key found, skipping AI enhancement")
        return None
    
    try:
        # Fetch article content
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract article paragraphs
        paragraphs = []
        for p in soup.select("article p, .entry-content p, .post-content p"):
            text = p.get_text(strip=True)
            if text and len(text) > 50:  # Filter out short/empty paragraphs
                paragraphs.append(text)
        
        if not paragraphs:
            logger.warning("⚠️ No content paragraphs found in article")
            return None
        
        # Limit content for AI processing
        content = " ".join(paragraphs[:5])[:1500]  # First 5 paragraphs, max 1500 chars
        
        logger.info(f"📝 Extracted {len(paragraphs)} paragraphs ({len(content)} chars)")
        
        # Call Groq API
        prompt = f"""Summarize this Ghana news article in 2-3 engaging sentences. 
Be clear, informative, and professional. Ignore any promotional content or ads.

Title: {title}
Content: {content}"""
        
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "mixtral-8x7b-32768",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 200
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        res = requests.post(groq_url, headers=headers, json=payload, timeout=15)
        
        if res.status_code == 200:
            data = res.json()
            summary = data["choices"][0]["message"]["content"].strip()
            logger.info(f"✅ AI summary generated successfully ({len(summary)} chars)")
            return summary
        else:
            logger.error(f"❌ Groq API error ({res.status_code}): {res.text[:200]}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error in AI enhancement: {e}")
        return None

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
            response = requests.post(f"{base_url}/sendPhoto", data=payload, timeout=10)
        else:
            logger.info(f"📝 Sending text message to Telegram")
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": content,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            response = requests.post(f"{base_url}/sendMessage", json=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info("✅ Successfully sent message to Telegram")
            return True
        else:
            logger.error(f"❌ Telegram API error ({response.status_code}): {response.text[:200]}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending to Telegram: {e}")
        return False

# ------------------------------
# 🎯 Main Job Function
# ------------------------------
def job():
    """Main scraping and posting job"""
    logger.info("=" * 60)
    logger.info("🚀 STARTING NEWS SCRAPING JOB")
    logger.info("=" * 60)
    logger.info(f"⏰ Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Scrape articles
    articles = scrape_articles()
    
    if not articles:
        logger.warning("⚠️ No new articles found")
        return
    
    logger.info(f"📰 Processing {len(articles)} new articles...")
    
    posted_count = 0
    
    for idx, article in enumerate(articles, 1):
        try:
            logger.info(f"\n--- Processing Article {idx}/{len(articles)} ---")
            logger.info(f"Title: {article['title'][:60]}...")
            logger.info(f"URL: {article['url']}")
            logger.info(f"Image: {'Yes' if article['image'] else 'No'}")
            
            # Try AI enhancement first
            ai_summary = enhance_with_ai(article['title'], article['url'])
            
            if ai_summary:
                message = f"📰 *{article['title']}*\n\n{ai_summary}\n\n[Read full article]({article['url']})\n\n{CHANNEL_LINK}"
            else:
                # Fallback to simple format
                message = f"📰 *{article['title']}*\n\n[Read full article]({article['url']})\n\n{CHANNEL_LINK}"
            
            # Send to Telegram
            success = send_to_telegram(message, article['image'])
            
            if success:
                posted_articles.add(article['url'])
                posted_count += 1
                logger.info(f"✅ Article {idx} posted successfully")
                
                # Save after each successful post
                save_posted_articles(posted_articles)
                
                # Rate limiting - wait between posts
                if idx < len(articles):
                    wait_time = 5  # 5 seconds between posts
                    logger.info(f"⏸️ Waiting {wait_time} seconds before next post...")
                    time.sleep(wait_time)
            else:
                logger.error(f"❌ Failed to post article {idx}")
                
        except Exception as e:
            logger.error(f"❌ Error processing article {idx}: {e}")
            continue
    
    logger.info("=" * 60)
    logger.info(f"✅ JOB COMPLETED: Posted {posted_count}/{len(articles)} articles")
    logger.info(f"📊 Total articles in database: {len(posted_articles)}")
    logger.info("=" * 60)

# ------------------------------
# ⏱️ Scheduler
# ------------------------------
def run_scheduler():
    """Run scheduled jobs in background"""
    logger.info("⏰ Scheduler initialized")
    
    # Schedule job every 3 hours
    schedule.every(3).hours.do(job)
    
    logger.info("📅 Job scheduled to run every 3 hours")
    logger.info("🔄 Running initial job...")
    
    # Run immediately on startup
    try:
        job()
    except Exception as e:
        logger.error(f"❌ Error in initial job run: {e}")
    
    logger.info("♾️ Entering scheduler loop...")
    
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"❌ Error in scheduler loop: {e}")
            time.sleep(60)

# ------------------------------
# 🌐 Flask Routes
# ------------------------------
@app.route("/")
def home():
    """Health check endpoint"""
    logger.info("🏠 Home endpoint accessed")
    uptime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"""
    <h1>✅ Telegram News Bot is Running</h1>
    <p>⏰ Current time: {uptime}</p>
    <p>📊 Articles tracked: {len(posted_articles)}</p>
    <p>🔗 <a href="/run_now">Trigger manual scrape</a></p>
    <p>🔗 <a href="/ping">Ping test</a></p>
    <p>🔗 <a href="/status">Status check</a></p>
    """

@app.route("/run_now", methods=["GET", "POST"])
def run_now():
    """Manual trigger endpoint"""
    logger.info("🎯 Manual scrape triggered via /run_now")
    Thread(target=job, daemon=True).start()
    return "✅ Manual scrape triggered! Check logs for progress."

@app.route("/ping")
def ping():
    """Simple ping endpoint"""
    logger.info("🏓 Ping endpoint accessed")
    return "pong"

@app.route("/status")
def status():
    """Status information endpoint"""
    logger.info("📊 Status endpoint accessed")
    return {
        "status": "running",
        "posted_articles_count": len(posted_articles),
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
    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Scheduler thread started")
    
    # Start Flask app
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🌐 Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
