import os
import json
import time
import hashlib
import schedule
import requests
from datetime import datetime
from anthropic import Anthropic

# ─── تنظیمات ───────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]   # توکن ربات از BotFather
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"] # مثال: @myartchannel
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"] # کلید API کلود

ARTCONNECT_URL  = "https://www.artconnect.com/opportunities/opencalls"
SEEN_FILE       = "seen_opportunities.json"
# ────────────────────────────────────────────────────────────

client = Anthropic(api_key=ANTHROPIC_KEY)


def load_seen() -> set:
    """فراخوان‌هایی که قبلاً پست شده‌اند را بارگذاری می‌کند."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def make_id(title: str, org: str) -> str:
    """یک شناسه یکتا برای هر فراخوان می‌سازد."""
    return hashlib.md5(f"{title}|{org}".encode()).hexdigest()


def fetch_opportunities() -> list[dict]:
    """فراخوان‌ها را از ArtConnect دریافت می‌کند."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        system="""You are a data extraction assistant.
Search for and fetch https://www.artconnect.com/opportunities/opencalls
Extract ALL visible opportunities and return ONLY a JSON array like this:
[
  {
    "title": "...",
    "org": "...",
    "type": "Open Call / Residency / Prize / Grant",
    "deadline": "...",
    "fee": "FREE or Paid",
    "url": "https://www.artconnect.com/opportunity/..."
  }
]
Return ONLY valid JSON array, no markdown, no explanation.""",
        messages=[{"role": "user", "content": "Fetch artconnect open calls and return JSON array."}]
    )

    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    match_start = text.find("[")
    match_end   = text.rfind("]") + 1
    if match_start == -1:
        print("⚠️ هیچ داده‌ای دریافت نشد.")
        return []
    return json.loads(text[match_start:match_end])


def translate_opportunity(op: dict) -> dict:
    """یک فراخوان را به فارسی ترجمه می‌کند."""
    time.sleep(10)  # جلوگیری از rate limit
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system="""You are a professional Persian translator for art and culture.
Translate the given opportunity JSON to Persian.
Return ONLY valid JSON with these fields:
{
  "title_fa": "...",
  "org_fa": "...",
  "type_fa": "فراخوان عمومی / رزیدنسی / جایزه / گرنت / همکاری",
  "deadline_fa": "...",
  "fee_fa": "رایگان یا دارای هزینه ثبت‌نام",
  "summary_fa": "یک یا دو جمله توضیح جذاب درباره این فراخوان"
}
No markdown, no extra text.""",
        messages=[{"role": "user", "content": json.dumps(op, ensure_ascii=False)}]
    )

    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def format_telegram_message(op: dict, translated: dict) -> str:
    """پیام تلگرام را فرمت می‌کند."""
    type_emoji = {
        "رزیدنسی": "🏠",
        "فراخوان عمومی": "📢",
        "جایزه": "🏆",
        "گرنت": "💰",
        "همکاری": "🤝",
    }.get(translated.get("type_fa", ""), "🎨")

    fee_icon = "✅ رایگان" if "رایگان" in translated.get("fee_fa", "") else "💳 دارای هزینه"

    msg = f"""{type_emoji} *{translated['title_fa']}*

🏛 {translated['org_fa']}
📅 مهلت: {translated['deadline_fa']}
{fee_icon}

📝 {translated['summary_fa']}

🔗 [مشاهده فراخوان اصلی]({op['url']})

#فراخوان\_هنری #{translated['type_fa'].replace(' ', '_')}"""
    return msg


def send_to_telegram(message: str):
    """پیام را به کانال تلگرام ارسال می‌کند."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        print(f"❌ خطا در ارسال: {response.text}")
    else:
        print(f"✅ پست ارسال شد.")


def run_job():
    """وظیفه اصلی: دریافت، ترجمه و ارسال فراخوان‌های جدید."""
    print(f"\n🕐 شروع کار: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()
    new_count = 0

    try:
        opportunities = fetch_opportunities()
        print(f"📥 {len(opportunities)} فراخوان دریافت شد.")
    except Exception as e:
        print(f"❌ خطا در دریافت: {e}")
        return

    for op in opportunities:
        op_id = make_id(op.get("title", ""), op.get("org", ""))
        if op_id in seen:
            continue  # قبلاً پست شده

        try:
            translated = translate_opportunity(op)
            message    = format_telegram_message(op, translated)
            send_to_telegram(message)
            seen.add(op_id)
            new_count += 1
            time.sleep(15)  # جلوگیری از spam
        except Exception as e:
            print(f"⚠️ خطا در پردازش '{op.get('title')}': {e}")
            continue

    save_seen(seen)
    print(f"✅ {new_count} فراخوان جدید ارسال شد.")


# ─── زمان‌بندی: دو بار در روز ──────────────────────────────
schedule.every().day.at("09:00").do(run_job)  # صبح ساعت ۹
schedule.every().day.at("18:00").do(run_job)  # عصر ساعت ۶

if __name__ == "__main__":
    print("🤖 ربات فراخوان هنری شروع به کار کرد...")
    run_job()  # یک بار فوری اجرا می‌شود
    while True:
        schedule.run_pending()
        time.sleep(60)
