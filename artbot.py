import os
import re
import json
import time
import hashlib
import schedule
import requests
from datetime import datetime
from anthropic import Anthropic
from bs4 import BeautifulSoup

# تنظیمات
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

ARTCONNECT_URL = "https://www.artconnect.com/opportunities/opencalls"
SEEN_FILE      = "seen_opportunities.json"

client = Anthropic(api_key=ANTHROPIC_KEY)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def make_id(title: str) -> str:
    return hashlib.md5(title.encode()).hexdigest()


def get_opportunity_details(artconnect_url: str) -> dict:
    """از صفحه فراخوان، جزئیات کامل رو استخراج می‌کنه."""
    result = {"url": artconnect_url, "org": "", "deadline": ""}
    try:
        r = requests.get(artconnect_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        # استخراج مهلت با الگوهای مختلف
        patterns = [
            r"Deadline[:\s]+([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})",
            r"Deadline[:\s]+(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            r"Deadline[:\s]+([A-Za-z]+\s+\d{4})",
            r"Apply by[:\s]+([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})",
            r"Due[:\s]+([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})",
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            r"([A-Za-z]+ \d{1,2},? \d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                result["deadline"] = match.group(1).strip()
                break

        # استخراج لینک مستقیم
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(strip=True).lower()
            if any(w in text for w in ["apply", "visit website", "official website", "submit"]):
                if href.startswith("http") and "artconnect.com" not in href:
                    result["url"] = href
                    break

        # استخراج اسم برگزارکننده از title یا meta
        meta = soup.find("meta", {"property": "og:site_name"})
        if meta and meta.get("content"):
            result["org"] = meta["content"]
        else:
            # از عنوان صفحه
            title_tag = soup.find("title")
            if title_tag:
                title_text = title_tag.get_text()
                if "|" in title_text:
                    result["org"] = title_text.split("|")[-1].strip()

    except Exception as e:
        print(f"خطا در خواندن صفحه {artconnect_url}: {e}")

    return result


def fetch_opportunities() -> list:
    response = requests.get(ARTCONNECT_URL, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")

    opportunities = []
    seen_urls = set()

    links = soup.find_all("a", href=True)
    for link in links:
        href = link.get("href", "")
        if "/opportunity/" not in href:
            continue
        title = link.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        artconnect_url = href if href.startswith("http") else f"https://www.artconnect.com{href}"
        if artconnect_url in seen_urls:
            continue
        seen_urls.add(artconnect_url)

        details = get_opportunity_details(artconnect_url)
        time.sleep(2)

        opportunities.append({
            "title": title,
            "org": details["org"] or "نامشخص",
            "type": "Open Call",
            "deadline": details["deadline"] or "نامشخص",
            "fee": "FREE",
            "url": details["url"]
        })

    print(f"📥 {len(opportunities)} فراخوان دریافت شد.")
    return opportunities[:12]


def translate_opportunity(op: dict) -> dict:
    time.sleep(8)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system="""You are a Persian translator for art opportunities.
Translate the given JSON to Persian. Return ONLY valid JSON:
{
  "title_fa": "Persian translation of title",
  "org_fa": "Persian or original org name",
  "type_fa": "فراخوان عمومی",
  "deadline_fa": "translate deadline date to Persian, keep the date numbers",
  "fee_fa": "رایگان",
  "summary_fa": "2 sentence Persian description based on the title"
}
No markdown, no extra text.""",
        messages=[{"role": "user", "content": json.dumps(op, ensure_ascii=False)}]
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def format_message(op: dict, tr: dict) -> str:
    fee_icon = "رایگان" if "رایگان" in tr.get("fee_fa", "") else "دارای هزینه"
    return (
        f"📢 *{tr['title_fa']}*\n\n"
        f"🏛 {tr['org_fa']}\n"
        f"📅 مهلت: {tr['deadline_fa']}\n"
        f"💰 {fee_icon}\n\n"
        f"📝 {tr['summary_fa']}\n\n"
        f"🔗 [مشاهده فراخوان اصلی]({op['url']})"
    )


def send_to_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHANNEL,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    })
    if r.status_code != 200:
        print(f"خطا در ارسال: {r.text}")
    else:
        print("پست ارسال شد.")


def run_job():
    print(f"شروع کار: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()
    new_count = 0

    try:
        opportunities = fetch_opportunities()
    except Exception as e:
        print(f"خطا در دریافت: {e}")
        return

    for op in opportunities:
        op_id = make_id(op.get("title", ""))
        if op_id in seen:
            continue
        try:
            translated = translate_opportunity(op)
            message = format_message(op, translated)
            send_to_telegram(message)
            seen.add(op_id)
            new_count += 1
            time.sleep(15)
        except Exception as e:
            print(f"خطا در '{op.get('title', '')}': {e}")
            continue

    save_seen(seen)
    print(f"{new_count} فراخوان جدید ارسال شد.")


schedule.every().day.at("09:00").do(run_job)
schedule.every().day.at("18:00").do(run_job)

if __name__ == "__main__":
    print("ربات فراخوان هنری شروع به کار کرد...")
    run_job()
    while True:
        schedule.run_pending()
        time.sleep(60)
