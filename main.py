import os
import time
import random
import logging
from datetime import datetime
from threading import Thread

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask
import schedule

# ------------------------------
# 🔧 Load environment variables
# ------------------------------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Flask app (for Render/Railway web service)
app = Flask(__name__)

# ------------------------------
# 🧠 Configuration
# ------------------------------
BASE_URL = "https://www.myjoyonline.com/news/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
POSTED_LOG = "posted_articles.txt"
CHANNEL_LINK = "👉 Join [@trending_gh](https://t.me/trending_gh)"

# ------------------------------
# 🧾 Helpers
# ------------------------------
def load_posted():
    if not os.path.exists(POSTED_LOG):
        return set()
    with open(POSTED_LOG, "r") as f:
        return set(f.read().splitlines())

def save_posted(article_id):
    with open(POSTED_LOG, "a") as f:
        f.write(article_id + "\n")

def is_daytime():
    hour = datetime.now().hour
    return 7 <= hour <= 17  # Between 7 AM and 5 PM

# ------------------------------
# 📰 Scrape MyJoyOnline
# ------------------------------
def scrape_latest_articles():
    resp = requests.get(BASE_URL, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    for post in soup.select("div.item-details"):
        title_tag = post.select_one("h3 a")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        url = title_tag["href"]

        # Skip videos
        if "video" in url.lower():
            continue

        # Try to get image
        image_tag = post.find_previous_sibling("div").find("img") if post.find_previous_sibling("div") else None
        image_url = image_tag["src"] if image_tag else None

        articles.append({"title": title, "url": url, "image": image_url})
    return articles

# ------------------------------
# 🤖 AI Rephrase/Summarize
# ------------------------------
def rephrase_article(title, url):
    try:
        content = requests.get(url, headers=HEADERS).text
        soup = BeautifulSoup(content, "html.parser")

        # Extract paragraphs
        paragraphs = [p.get_text(strip=True) for p in soup.select("article p") if p.get_text(strip=True)]
        text = " ".join(paragraphs[:5])  # Limit to first few

        if not text:
            return None

        prompt = f"Summarize or rephrase this Ghana news article clearly and engagingly, ignoring promotional text or links:\n\n{text}"
        groq_url = "https://api.groq.com/openai/v1/chat/completions"

        payload = {
            "model": "mixtral-8x7b-32768",
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        res = requests.post(groq_url, headers=headers, json=payload)

        if res.status_code == 200:
            data = res.json()
            summary = data["choices"][0]["message"]["content"].strip()
            return f"📰 *{title}*\n\n{summary}\n\n[Read more here]({url})\n\n{CHANNEL_LINK}"
        else:
            logging.error(f"Groq API error: {res.text}")
            return None
    except Exception as e:
        logging.error(f"Error rephrasing article: {e}")
        return None

# ------------------------------
# 📢 Telegram Posting
# ------------------------------
def post_to_telegram(message, image_url=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    if image_url:
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "caption": message,
            "parse_mode": "Markdown",
            "photo": image_url,
        }
        res = requests.post(f"{base_url}/sendPhoto", data=payload)
    else:
        payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": message, "parse_mode": "Markdown"}
        res = requests.post(f"{base_url}/sendMessage", data=payload)

    if res.status_code != 200:
        logging.error(f"Telegram Error: {res.text}")

# ------------------------------
# 🕒 Main Scheduler Job
# ------------------------------
def job():
    try:
        logging.info("🟢 job() started running manually or by schedule!")

        url = "https://www.myjoyonline.com/news/"
        headers = {"User-Agent": "Mozilla/5.0"}
        logging.info(f"Fetching articles from {url} ...")

        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            logging.error(f"❌ Failed to fetch page, status code: {res.status_code}")
            return

        soup = BeautifulSoup(res.text, "html.parser")
        articles = soup.select("article")  # Adjust selector if needed

        logging.info(f"Found {len(articles)} articles total before filtering.")

        posted_count = 0
        for article in articles[:5]:  # limit for testing, remove [:5] later
            title_el = article.select_one("h2, h3, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = title_el.get("href")

            # Skip if no valid link or is a video
            if not link or "video" in link.lower():
                logging.info(f"Skipping video or invalid link: {link}")
                continue

            # Make link absolute
            if link.startswith("/"):
                link = "https://www.myjoyonline.com" + link

            # Get image if available
            img_el = article.select_one("img")
            image_url = img_el.get("src") if img_el else None

            logging.info(f"📰 Processing article: {title}")
            logging.info(f"Link: {link}")

            # Fetch article content
            try:
                article_res = requests.get(link, headers=headers, timeout=10)
                article_soup = BeautifulSoup(article_res.text, "html.parser")
                paragraphs = article_soup.select("p")
                content = " ".join([p.get_text(strip=True) for p in paragraphs])
                content = content[:900]  # keep concise
            except Exception as e:
                logging.error(f"Failed to fetch article text: {e}")
                continue

            # Basic rephrase (you can replace this with GPT if you like)
            summary = content.replace("Read more:", "").split("—")[0]
            message = f"📰 *{title}*\n\n{summary}\n\n🔗 Read more: {link}\n\n📢 Follow @yourchannelhandle"

            # Send to Telegram
            try:
                bot.send_message(chat_id="@yourchannelhandle", text=message, parse_mode="Markdown")
                if image_url:
                    bot.send_photo(chat_id="@yourchannelhandle", photo=image_url, caption=message)
                posted_count += 1
                logging.info(f"✅ Posted successfully: {title}")
                time.sleep(2)
            except Exception as e:
                logging.error(f"❌ Failed to post to Telegram: {e}")

        logging.info(f"✅ job() completed successfully. Posted {posted_count} new articles.")
    except Exception as e:
        logging.error(f"❌ job() crashed: {e}", exc_info=True)

# ------------------------------
# ⏱️ Scheduler Thread
# ------------------------------
def start_scheduler():
    schedule.every(5).hours.do(job)
    job()  # run once at startup
    while True:
        schedule.run_pending()
        time.sleep(60)

# ------------------------------
# 🚀 Flask Web Routes
# ------------------------------
@app.route("/")
def home():
    logging.info("Home route accessed.")
    return "✅ Telegram News Bot is running."

@app.route("/run_now", methods=["GET"])
def run_now():
    logging.info("Manual scrape triggered via /run_now.")
    Thread(target=job).start()
    return "🕒 Manual scrape triggered in background!"

@app.route("/ping")
def ping():
    return "pong"


# ------------------------------
# 🏁 Entry Point
# ------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logging.info("Bot starting up... 🚀")
    Thread(target=start_scheduler).start()
    logging.info("Scheduler thread started.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
