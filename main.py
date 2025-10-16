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
POSTING_START_HOUR = 7  # Start posting at 7 AM
POSTING_END_HOUR = 17  # Stop posting at 5 PM (17:00 in 24-hour format)
POSTS_PER_DAY = 5  # Number of posts per day
CHANNEL_HANDLE = "@trending_gh"
CHANNEL_LINK = f"👉 Join [{CHANNEL_HANDLE}](https://t.me/trending_gh)"

# Calculate posting times (evenly distributed between 7 AM and 5 PM)
# 7 AM to 5 PM = 10 hours = 600 minutes
# 5 posts over 10 hours = every 2.5 hours (150 minutes)
POSTING_TIMES = []
start_minutes = POSTING_START_HOUR * 60  # 7 AM in minutes (420)
end_minutes = POSTING_END_HOUR * 60  # 5 PM in minutes (1020)
interval_minutes = (end_minutes - start_minutes) / (POSTS_PER_DAY - 1)  # Divide by 4 to get 5 posts

for i in range(POSTS_PER_DAY):
    post_time_minutes = start_minutes + (i * interval_minutes)
    hour = int(post_time_minutes // 60)
    minute = int(post_time_minutes % 60)
    POSTING_TIMES.append((hour, minute))

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

# Track daily posts
daily_post_count = 0
last_post_date = None

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
        
        # Extract article paragraphs with better selectors
        paragraphs = []
        
        # Try multiple content selectors
        content_selectors = [
            "article p",
            ".entry-content p",
            ".post-content p",
            ".article-body p",
            ".story-body p",
            "div.content p",
            "p"
        ]
        
        for selector in content_selectors:
            found_paragraphs = soup.select(selector)
            if found_paragraphs:
                for p in found_paragraphs:
                    text = p.get_text(strip=True)
                    # Filter out short paragraphs, ads, and promotional content
                    if (text and 
                        len(text) > 80 and 
                        not any(skip in text.lower() for skip in ['subscribe', 'newsletter', 'follow us', 'advertisement', 'read more', 'click here'])):
                        paragraphs.append(text)
                if len(paragraphs) >= 3:  # If we found good content, stop searching
                    break
        
        if not paragraphs:
            logger.warning("⚠️ No content paragraphs found, using basic format")
            return f"📰 *{title}*\n\n{CHANNEL_LINK}"
        
        # Get more content for better summary (first 8 paragraphs, max 3000 chars)
        content = " ".join(paragraphs[:8])[:3000]
        
        logger.info(f"📝 Extracted {len(paragraphs)} paragraphs ({len(content)} chars) for AI")
        
        # Improved AI prompt with clear instructions
        prompt = f"""You are writing a Telegram news post for a Ghanaian news channel. Your job is to make the news engaging and informative.

ORIGINAL TITLE: {title}

ARTICLE CONTENT:
{content}

INSTRUCTIONS:
1. Create a SHORT, CATCHY title (5-8 words max) that captures the essence of the story. Do NOT just copy the original title.
2. Write EXACTLY 2 clear sentences that explain what happened, who is involved, and why it matters.
3. Use simple, direct language that anyone can understand.
4. Do NOT use hashtags, emojis, or promotional language.
5. Focus on facts from the article.

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:
[Your catchy title here]

[First sentence about the main point]. [Second sentence with important details].

EXAMPLE:
President Announces New Policy

President Akufo-Addo unveiled a comprehensive education reform program aimed at making senior high school education more accessible. The initiative will provide free textbooks and digital learning tools to over 500,000 students across the country."""
        
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama-3.3-70b-versatile",  # Updated to current model
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 300
        }
        headers_api = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        logger.info("📡 Sending request to Groq AI...")
        res = requests.post(groq_url, headers=headers_api, json=payload, timeout=30)
        
        if res.status_code == 200:
            data = res.json()
            ai_content = data["choices"][0]["message"]["content"].strip()
            logger.info(f"✅ AI content generated: {len(ai_content)} chars")
            logger.info(f"📄 AI Response Preview: {ai_content[:100]}...")
            
            # Clean up the AI response
            ai_content = ai_content.strip()
            
            # Remove any markdown formatting
            ai_content = ai_content.replace('**', '')
            ai_content = ai_content.replace('*', '')
            
            # Format the final message
            message = f"{ai_content}\n\n{CHANNEL_LINK}"
            
            logger.info(f"✅ Final message created successfully")
            return message
        else:
            logger.error(f"❌ Groq API error ({res.status_code}): {res.text[:300]}")
            logger.warning("⚠️ Falling back to basic format")
            return f"📰 *{title}*\n\n{CHANNEL_LINK}"
            
    except requests.exceptions.Timeout:
        logger.error(f"❌ Timeout fetching article or calling AI")
        return f"📰 *{title}*\n\n{CHANNEL_LINK}"
    except Exception as e:
        logger.error(f"❌ Error creating AI content: {type(e).__name__} - {str(e)}")
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
# 🔄 Main Job - Post Latest Article at Scheduled Times
# ------------------------------
def post_latest_article():
    """Check and post latest article if it's new"""
    global daily_post_count, last_post_date
    
    logger.info("=" * 60)
    logger.info("🚀 CHECKING FOR LATEST ARTICLE TO POST")
    logger.info("=" * 60)
    logger.info(f"⏰ Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📊 Already posted: {len(posted_articles)} articles")
    logger.info(f"📅 Today's post count: {daily_post_count}/{POSTS_PER_DAY}")
    
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
    logger.info(f"📰 Original Title: {latest_article['title'][:80]}...")
    logger.info(f"🔗 URL: {latest_article['url']}")
    logger.info(f"📸 Image: {'Yes ✅' if latest_article['image'] else 'No ❌'}")
    
    try:
        # Create enhanced content
        message = create_post_content(latest_article['title'], latest_article['url'])
        
        logger.info("=" * 60)
        logger.info("📝 FINAL MESSAGE TO POST:")
        logger.info(message)
        logger.info("=" * 60)
        
        # Send to Telegram
        success = send_to_telegram(message, latest_article['image'])
        
        if success:
            posted_articles.add(latest_article['url'])
            save_posted_articles(posted_articles)
            daily_post_count += 1
            logger.info(f"✅ Article posted successfully!")
            logger.info(f"📊 Total articles posted: {len(posted_articles)}")
            logger.info(f"📅 Today's posts: {daily_post_count}/{POSTS_PER_DAY}")
        else:
            logger.error(f"❌ Failed to post article")
            
    except Exception as e:
        logger.error(f"❌ Error posting article: {e}")
    
    logger.info("=" * 60)

# ------------------------------
# ⏱️ Scheduler with Daily Posting Times
# ------------------------------
def is_posting_time():
    """Check if current time matches any posting schedule"""
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    
    for post_hour, post_minute in POSTING_TIMES:
        # Check if we're within 1 minute of a scheduled post time
        if current_hour == post_hour and abs(current_minute - post_minute) <= 0:
            return True
    return False

def get_next_post_time():
    """Get the next scheduled posting time"""
    now = datetime.now()
    current_minutes = now.hour * 60 + now.minute
    
    for post_hour, post_minute in POSTING_TIMES:
        post_minutes = post_hour * 60 + post_minute
        if post_minutes > current_minutes:
            return f"{post_hour:02d}:{post_minute:02d}"
    
    # If no more posts today, return first post time tomorrow
    return f"{POSTING_TIMES[0][0]:02d}:{POSTING_TIMES[0][1]:02d} (tomorrow)"

def run_keepalive_and_scheduler():
    """Keep bot alive and run scheduled posts at specific times"""
    global daily_post_count, last_post_date
    
    logger.info("=" * 60)
    logger.info("🚀 SCHEDULER STARTED")
    logger.info("=" * 60)
    logger.info(f"⏱️ Keep-alive ping: Every {KEEPALIVE_INTERVAL} seconds")
    logger.info(f"📅 Posting schedule: {POSTS_PER_DAY} times per day")
    logger.info(f"🕐 Posting hours: {POSTING_START_HOUR}:00 AM - {POSTING_END_HOUR}:00 PM")
    logger.info(f"📍 Posting times:")
    for hour, minute in POSTING_TIMES:
        logger.info(f"   • {hour:02d}:{minute:02d}")
    logger.info(f"📊 Already posted: {len(posted_articles)} articles")
    
    # Initialize daily post count
    last_post_date = datetime.now().date()
    daily_post_count = 0
    
    check_count = 0
    last_check_time = None
    
    while True:
        try:
            check_count += 1
            now = datetime.now()
            current_time = now.strftime('%H:%M:%S')
            current_date = now.date()
            current_hour = now.hour
            current_minute = now.minute
            
            # Reset daily counter at midnight
            if current_date != last_post_date:
                logger.info(f"🌅 New day! Resetting daily post counter")
                daily_post_count = 0
                last_post_date = current_date
            
            # Log keep-alive ping every 10 checks (every 5 minutes)
            if check_count % 10 == 0:
                next_post = get_next_post_time()
                if POSTING_START_HOUR <= current_hour < POSTING_END_HOUR:
                    logger.info(f"💚 Keep-alive #{check_count} - Next post: {next_post} | Today: {daily_post_count}/{POSTS_PER_DAY}")
                else:
                    logger.info(f"🌙 Keep-alive #{check_count} - Outside posting hours (Next: {next_post})")
            
            # Check if we're in posting hours
            if current_hour >= POSTING_START_HOUR and current_hour < POSTING_END_HOUR:
                # Check if current time matches a scheduled post time
                for post_hour, post_minute in POSTING_TIMES:
                    # Check if we're at the exact minute
                    if current_hour == post_hour and current_minute == post_minute:
                        # Prevent duplicate posts in the same minute
                        check_key = f"{current_date}-{post_hour:02d}:{post_minute:02d}"
                        if last_check_time != check_key:
                            logger.info(f"⏰ SCHEDULED POST TIME: {post_hour:02d}:{post_minute:02d}")
                            post_latest_article()
                            last_check_time = check_key
                            break
            
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
    next_post = get_next_post_time()
    
    posting_schedule = "<br>".join([f"• {h:02d}:{m:02d}" for h, m in POSTING_TIMES])
    
    return f"""
    <h1>✅ Telegram News Bot is Running</h1>
    <p>⏰ Current time: {uptime}</p>
    <p>📊 Articles posted: {len(posted_articles)}</p>
    <p>📅 Today's posts: {daily_post_count}/{POSTS_PER_DAY}</p>
    <p>⏰ Next post: {next_post}</p>
    <hr>
    <h3>📍 Daily Posting Schedule:</h3>
    <p>{posting_schedule}</p>
    <p><em>(Posts only between {POSTING_START_HOUR}:00 AM - {POSTING_END_HOUR}:00 PM)</em></p>
    <hr>
    <p>🔗 <a href="/status">Detailed Status</a></p>
    <p>🔗 <a href="/ping">Ping Test</a></p>
    <p>🔗 <a href="/post_now">Post Latest Article Now</a></p>
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
    posting_times_str = [f"{h:02d}:{m:02d}" for h, m in POSTING_TIMES]
    return {
        "status": "running",
        "posted_articles_count": len(posted_articles),
        "daily_posts": f"{daily_post_count}/{POSTS_PER_DAY}",
        "posting_times": posting_times_str,
        "posting_hours": f"{POSTING_START_HOUR}:00 - {POSTING_END_HOUR}:00",
        "next_post": get_next_post_time(),
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
