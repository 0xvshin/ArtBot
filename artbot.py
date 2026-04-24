import os
import re
import json
import time
import hashlib
import schedule
import requests
from datetime import datetime, date
from anthropic import Anthropic
from bs4 import BeautifulSoup

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


def parse_deadline_date(deadline_str: str):
    """مهلت رو به تاریخ تبدیل می‌کنه برای مقایسه."""
    formats = [
        "%B %d, %Y", "%B %d %Y", "%d %B %Y",
        "%b %d, %Y", "%b %d %Y", "%d %b %Y",
        "%B %Y", "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(deadline_str.strip(), fmt).date()
        except:
            continue
    return None


def deadline_passed(deadline_str: str) -> bool:
    """بررسی می‌کنه آیا مهلت گذشته یا نه."""
    if not deadline_str or deadline_str == "نامشخص":
        return False  # اگه نامشخص بود، منتشر می‌کنیم
    d = parse_deadline_date(deadline_str)
    if d is None:
        return False
    return d < date.today()


def get_opportunity_details(artconnect_url: str) -> dict:
    """جزئیات کامل فراخوان رو از صفحه‌اش استخراج می‌کنه."""
    result = {
        "url": artconnect_url,
        "org": "",
        "deadline": "",
        "country": "",
        "description": "",
        "website": ""
    }
    try:
        r = requests.get(artconnect_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        # استخراج توضیحات اصلی برای شخصی‌سازی عنوان
        for tag in soup.find_all(["p", "div"]):
            text = tag.get_text(strip=True)
            if len(text) > 100:
                result["description"] = text[:500]
                break

        # استخراج مهلت
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

        # استخراج کشور
        country_patterns = [
            r"Country[:\s]+([A-Za-z\s]+?)(?:\n|,|\||$)",
            r"Location[:\s]+([A-Za-z\s,]+?)(?:\n|\||$)",
            r"Based in[:\s]+([A-Za-z\s]+?)(?:\n|,|\||$)",
        ]
        for pattern in country_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                country = match.group(1).strip()
                if 2 < len(country) < 40:
                    result["country"] = country
                    break

        # استخراج وبسایت از بخش Contact
        # دنبال لینک‌هایی می‌گرده که با "Website" لیبل شدن
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(strip=True).lower()
            parent_text = ""
            if a.parent:
                parent_text = a.parent.get_text(strip=True).lower()

            if "website" in text or "website" in parent_text:
                if href.startswith("http") and "artconnect.com" not in href:
                    result["website"] = href
                    break

        # اگه website پیدا نشد، دنبال Apply link بگرد
        if not result["website"]:
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                text = a.get_text(strip=True).lower()
                if any(w in text for w in ["apply", "submit", "register"]):
                    if href.startswith("http") and "artconnect.com" not in href:
                        result["website"] = href
                        break

        result["url"] = result["website"] if result["website"] else artconnect_url

        # اسم برگزارکننده از title صفحه
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text()
            if "|" in title_text:
                result["org"] = title_text.split("|")[-1].strip()

    except Exception as e:
        print(f"خطا در خواندن {artconnect_url}: {e}")

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

        # فقط فراخوان‌هایی که مهلتشون نگذشته
        if deadline_passed(details["deadline"]):
            print(f"⏩ مهلت گذشته: {title}")
            continue

        opportunities.append({
            "title": title,
            "org": details["org"] or "نامشخص",
            "deadline": details["deadline"] or "نامشخص",
            "country": details["country"],
            "description": details["description"],
            "url": details["url"]
        })

    print(f"📥 {len(opportunities)} فراخوان فعال دریافت شد.")
    return opportunities[:12]


def translate_and_personalize(op: dict) -> dict:
    time.sleep(8)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system="""You are a Persian content writer specializing in art opportunities.
Given an art opportunity, return ONLY valid JSON (no markdown):
{
  "title_fa": "A natural, descriptive Persian title - NOT a literal translation. Read the description and write a title that clearly explains what the opportunity is about for a Persian-speaking artist. Example: instead of 'Sculpture of the Lawn 2027' write 'فراخوان ساخت مجسمه در فضای باز ۲۰۲۷'",
  "org_fa": "Organization name in Persian or keep original if proper noun",
  "deadline_fa": "Translate the deadline date to Persian. Keep the numbers, just translate month names. Example: 'June 15, 2025' becomes '۱۵ ژوئن ۲۰۲۵'",
  "country_fa": "Country name in Persian, empty string if not provided",
  "summary_fa": "2-3 engaging sentences in Persian describing this opportunity - what it is, who can apply, what they offer"
}""",
        messages=[{"role": "user", "content": json.dumps(op, ensure_ascii=False)}]
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def format_message(op: dict, tr: dict) -> str:
    lines = [f"📢 *{tr['title_fa']}*\n"]
    lines.append(f"🏛 {tr['org_fa']}")
    lines.append(f"📅 مهلت: {tr['deadline_fa']}")
    if tr.get("country_fa"):
        lines.append(f"🌍 {tr['country_fa']}")
    lines.append(f"\n📝 {tr['summary_fa']}\n")
    lines.append(f"🔗 [مشاهده فراخوان اصلی]({op['url']})")
    return "\n".join(lines)


def send_to_telegram(message: str):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHANNEL,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
    )
    if r.status_code != 200:
        print(f"خطا در ارسال: {r.text}")
    else:
        print("✅ پست ارسال شد.")


def run_job():
    print(f"\nشروع کار: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()
    new_count = 0

    try:
        opportunities = fetch_opportunities()
    except Exception as e:
        print(f"خطا در دریافت: {e}")
        return

    for op in opportunities:
        op_id = make_id(op["title"])
        if op_id in seen:
            continue
        try:
            translated = translate_and_personalize(op)
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


# دو بار در روز + هر ۳۰ دقیقه چک می‌کنه برای فراخوان‌های جدید
schedule.every(30).minutes.do(run_job)

if __name__ == "__main__":
    print("ربات فراخوان هنری شروع به کار کرد...")
    run_job()
    while True:
        schedule.run_pending()
        time.sleep(60)
