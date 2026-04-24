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

SEEN_FILE = "seen_opportunities.json"

client = Anthropic(api_key=ANTHROPIC_KEY)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

PAGES = [
    "https://www.artconnect.com/opportunities",
    "https://www.artconnect.com/opportunities/opencalls",
    "https://www.artconnect.com/opportunities/residencies",
    "https://www.artconnect.com/opportunities/prizes",
    "https://www.artconnect.com/opportunities/grants",
]


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def parse_deadline_date(deadline_str: str):
    formats = [
        "%B %d, %Y", "%B %d %Y", "%d %B %Y",
        "%b %d, %Y", "%b %d %Y", "%d %b %Y",
        "%B %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(deadline_str.strip(), fmt).date()
        except:
            continue
    return None


def deadline_passed(deadline_str: str) -> bool:
    if not deadline_str or deadline_str in ("نامشخص", ""):
        return False
    d = parse_deadline_date(deadline_str)
    if d is None:
        return False
    return d < date.today()


def fetch_opportunity_urls() -> list:
    """لینک‌های فراخوان رو از همه صفحات ArtConnect می‌گیره."""
    urls = []
    seen_urls = set()

    for page_url in PAGES:
        try:
            r = requests.get(page_url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            count = 0
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/opportunity/" in href:
                    full = href if href.startswith("http") else f"https://www.artconnect.com{href}"
                    if full not in seen_urls and len(a.get_text(strip=True)) > 5:
                        seen_urls.add(full)
                        urls.append(full)
                        count += 1
            print(f"  {page_url.split('/')[-1]}: {count} لینک")
            time.sleep(2)
        except Exception as e:
            print(f"خطا در {page_url}: {e}")
            continue

    print(f"📋 جمعاً {len(urls)} فراخوان پیدا شد.")
    return urls


def parse_opportunity_with_claude(artconnect_url: str) -> dict:
    """Claude صفحه فراخوان رو می‌خونه و اطلاعات رو استخراج می‌کنه."""
    try:
        r = requests.get(artconnect_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)[:3000]

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            system="""You are a data extraction assistant for art opportunity pages.
Extract information from the page text and return ONLY valid JSON (no markdown):
{
  "title": "original English title of the opportunity",
  "org": "name of the organizing institution/company (NOT ArtConnect)",
  "deadline": "deadline date in English, e.g. 'May 15, 2025'. Empty string if not found.",
  "country": "country where the opportunity is based. Empty string if not found.",
  "description": "2-3 sentences describing what this opportunity is about",
  "website": "direct website URL from the Contact section labeled 'Website'. Empty string if not found. Must NOT be artconnect.com"
}
Important: org should be the actual organizer, not ArtConnect.""",
            messages=[{"role": "user", "content": f"Page URL: {artconnect_url}\n\nPage content:\n{page_text}"}]
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        data["artconnect_url"] = artconnect_url
        data["final_url"] = data.get("website") or artconnect_url
        return data
    except Exception as e:
        print(f"خطا در parse {artconnect_url}: {e}")
        return {}


def translate_and_personalize(op: dict) -> dict:
    """ترجمه و شخصی‌سازی فارسی."""
    time.sleep(15)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system="""You are a Persian content writer for art opportunities.
Given an art opportunity JSON, return ONLY valid JSON (no markdown):
{
  "title_fa": "Creative Persian title that DESCRIBES the opportunity naturally - not a literal translation. Read the description and org to understand what it is, then write a clear engaging Persian title. For example 'Sculpture of the Lawn 2027' with description about public art in a park becomes 'فراخوان ساخت مجسمه در فضای سبز عمومی ۲۰۲۷'",
  "org_fa": "Keep the organization name as-is if it's a proper noun/brand. Only translate if it's a generic descriptive name.",
  "deadline_fa": "Translate only the month name to Persian equivalent, keep numbers as Persian digits. 'April 29, 2026' becomes '۲۹ آوریل ۲۰۲۶'",
  "country_fa": "Country in Persian. Empty string if country field is empty.",
  "summary_fa": "2-3 natural engaging Persian sentences about this opportunity - what it is, who can apply, what's offered. Write as if talking to a Persian artist."
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
    lines.append(f"🔗 [مشاهده فراخوان اصلی]({op['final_url']})")
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

    urls = fetch_opportunity_urls()

    for url in urls:
        op_id = make_id(url)
        if op_id in seen:
            continue

        op = parse_opportunity_with_claude(url)
        if not op:
            seen.add(op_id)
            save_seen(seen)
            continue

        time.sleep(2)

        if deadline_passed(op.get("deadline", "")):
            print(f"⏩ مهلت گذشته: {op.get('title', url)}")
            seen.add(op_id)
            save_seen(seen)
            continue

        try:
            translated = translate_and_personalize(op)
            message = format_message(op, translated)
            send_to_telegram(message)
            seen.add(op_id)
            new_count += 1
            save_seen(seen)
            time.sleep(20)
        except Exception as e:
            print(f"خطا در '{op.get('title', '')}': {e}")
            continue

    print(f"✅ {new_count} فراخوان جدید ارسال شد.")


schedule.every(30).minutes.do(run_job)

if __name__ == "__main__":
    print("ربات فراخوان هنری شروع به کار کرد...")
    run_job()
    while True:
        schedule.run_pending()
        time.sleep(60)
