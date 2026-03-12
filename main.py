import requests
import time
import feedparser
from bs4 import BeautifulSoup

# ==============================
# CONFIG
# ==============================

TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

CHECK_INTERVAL = 300   # seconds (5 minutes)

NEWS_FEED = "https://news.google.com/rss/search?q=SpaceX+IPO"

SEC_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=Space+Exploration+Technologies&owner=exclude&count=40"

sent_links = set()

# ==============================
# TELEGRAM FUNCTION
# ==============================

def send_telegram(msg):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }

    requests.post(url, data=payload)


# ==============================
# CHECK NEWS
# ==============================

def check_news():

    feed = feedparser.parse(NEWS_FEED)

    for entry in feed.entries:

        if entry.link not in sent_links:

            sent_links.add(entry.link)

            msg = f"""
🚨 *SpaceX IPO News Detected*

Title: {entry.title}

Link:
{entry.link}
"""

            send_telegram(msg)


# ==============================
# CHECK SEC FILINGS
# ==============================

def check_sec():

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(SEC_URL, headers=headers)

    soup = BeautifulSoup(r.text, "html.parser")

    rows = soup.find_all("tr")

    for row in rows:

        text = row.get_text()

        if "S-1" in text or "IPO" in text:

            if text not in sent_links:

                sent_links.add(text)

                msg = f"""
🚨 *POSSIBLE SPACEX IPO SEC FILING*

{text}

Check immediately:
https://www.sec.gov
"""

                send_telegram(msg)


# ==============================
# MAIN LOOP
# ==============================

def main():

    send_telegram("🚀 SpaceX IPO Alert Bot Started")

    while True:

        try:

            check_news()
            check_sec()

        except Exception as e:

            send_telegram(f"Bot error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
