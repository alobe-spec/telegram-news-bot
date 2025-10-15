import os
from flask import Flask, request
import telebot

# Load environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Your Render domain + /webhook

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Example command
@bot.message_handler(commands=["start"])
def start_command(message):
    bot.reply_to(message, "👋 Hello! The bot is running successfully on Render!")

# Example message handler
@bot.message_handler(func=lambda m: True)
def echo_message(message):
    bot.reply_to(message, f"You said: {message.text}")

# Flask route for Telegram webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# Root route (optional)
@app.route("/")
def home():
    return "Telegram bot is live 🚀"

# Set webhook on startup
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
