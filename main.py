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
CHANNEL_LINK = "👉 Join [@YourPersonalChannel](https://t.me/YourPersonalChannel) for more updates!"

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
    if not is_daytime():
        print("⏰ Outside working hours. Skipping...")
        return

    print("🔎 Checking for new articles...")
    posted = load_posted()
    articles = scrape_latest_articles()

    for art in articles:
        article_id = art["url"].split("/")[-2]
        if article_id in posted:
            continue

        msg = rephrase_article(art["title"], art["url"])
        if msg:
            post_to_telegram(msg, art["image"])
            save_posted(article_id)
            print(f"✅ Posted: {art['title']}")
            time.sleep(random.randint(60, 180))  # pause between posts

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
    return "✅ Telegram News Bot is running."

@app.route("/run_now", methods=["GET"])
def run_now():
    job()
    return "✅ Manual scrape triggered successfully!"

# ------------------------------
# 🏁 Entry Point
# ------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    Thread(target=start_scheduler).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
