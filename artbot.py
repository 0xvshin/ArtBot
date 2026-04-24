import os  # v2
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


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def make_id(title: str, org: str) -> str:
    return hashlib.md5(f"{title}|{org}".encode()).hexdigest()


def fetch_opportunities() -> list[dict]:
    """فراخوان‌ها را مستقیم از ArtConnect می‌خواند."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.get(ARTCONNECT_URL, headers=headers, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")

    opportunities = []
    links = soup.find_all("a", href=True)

    for link in links:
        href = link.get("href", "")
        if "/opportunity/" not in href:
            continue

        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        url = href if href.startswith("http") else f"https://www.artconnect.com{href}"

        # پیدا کردن سازمان و مهلت از محتوای اطراف
        parent = link.find_parent()
        org = ""
        deadline = ""
        fee = "FREE"

        if parent:
            text = parent.get_text(" ", strip=True)
            if "Deadline" in text or "deadline" in text:
                deadline = "نامشخص"
            if "Yes" in text:
                fee = "Paid"

        opportunities.append({
            "title": title,
            "org": org or "ArtConnect",
            "type": "Open Call",
            "deadline": deadline or "نامشخص",
            "fee": fee,
            "url": url
        })

    # حذف موارد تکراری
    seen_urls = set()
    unique = []
    for op in opportunities:
        if op["url"] not in seen_urls and len(op["title"]) > 10:
            seen_urls.add(op["url"])
            unique.append(op)

    print(f"📥 {len(unique)} فراخوان دریافت شد.")
    return unique[:15]  # حداکثر ۱۵ تا


def translate_opportunity(op: dict) -> dict:
    """یک فراخوان را به فارسی ترجمه می‌کند."""
    time.sleep(8)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system="""You are a Persian translator for art opportunities.
Translate the given JSON to Persian. Return ONLY valid JSON with these fields:
{
  "title_fa": "Persian title",
  "org_fa": "Persian org name",
  "type_fa": "فراخوان عمومی",
  "deadline_fa": "Persian deadline",
  "fee_fa": "رایگان or دارای هزینه ثبت‌نام",
  "summary_fa": "1-2 sentence Persian description based on the title"
}
No markdown, no extra text.""",
        messages=[{"role": "user", "content": json.dumps(op, ensure_ascii=False)}]
    )

    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def format_message(op: dict, tr: dict) -> str:
    type_emoji = {
        "رزیدنسی": "🏠",
        "فراخوان عمومی": "📢",
        "جایزه": "🏆",
        "گرنت": "💰",
    }.get(tr.get("type_fa", ""), "🎨")

    fee_icon = "✅ رایگان" if "رایگان" in tr.get("fee_fa", "") else "💳 دارای هزینه"

    hashtag = tr.get("type_fa", "فراخوان").replace(" ", "_")

    return f"""{type_emoji} *{tr['title_fa']}*

🏛 {tr['org_fa']}
📅 مهلت: {tr['deadline_fa']}
{fee_icon}

📝 {tr['summary_fa']}

🔗 [مشاهده فراخوان اصلی]({op['url']})

#فراخوان_هنری #{hashtag}"""


def send_to_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print(f"❌ خطا در ارسال: {r.text}")
    else:
        print("✅ پست ارسال شد.")


def run_job():
    print(f"\n🕐 شروع کار: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()
    new_count = 0

    try:
        opportunities = fetch_opportunities()
    except Exception as e:
        print(f"❌ خطا در دریافت: {e}")
        return

    for op in opportunities:
        op_id = make_id(op.get("title", ""), op.get("org", ""))
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
            print(f"⚠️ خطا در '{op.get('title', '')}': {e}")
            continue

    save_seen(seen)
    print(f"✅ {new_count} فراخوان جدید ارسال شد.")


# زمان‌بندی: دو بار در روز
schedule.every().day.at("09:00").do(run_job)
schedule.every().day.at("18:00").do(run_job)

if __name__ == "__main__":
    print("🤖 ربات فراخوان هنری شروع به کار کرد...")
    run_job()
    while True:
        schedule.run_pending()
        time.sleep(60)
