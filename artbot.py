import os
import re
import json
import time
import hashlib
import schedule
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date
from anthropic import Anthropic
from bs4 import BeautifulSoup

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

SEEN_FILE = "seen_opportunities.json"
client = Anthropic(api_key=ANTHROPIC_KEY)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── منابع ──────────────────────────────────────────────────────────────
RSS_SOURCES = [
    # Colossal: مقاله‌های ماهانه با ده‌ها فراخوان
    {"url": "https://www.thisiscolossal.com/category/opportunities/feed/", "type": "article"},
    {"url": "https://www.thisiscolossal.com/feed/",                        "type": "article"},
]

SCRAPE_SOURCES = [
    # ArtJobs: هر فراخوان صفحه جداگانه دارد
    {"url": "https://www.artjobs.com/open-calls", "type": "list", "base": "https://www.artjobs.com"},
    # NYFA: معتبر، روزانه آپدیت
    {"url": "https://www.nyfa.org/opportunities/", "type": "list", "base": "https://www.nyfa.org"},
]
# ────────────────────────────────────────────────────────────────────────


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def make_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def parse_deadline_date(deadline_str: str):
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
    if not deadline_str or deadline_str in ("نامشخص", ""):
        return False
    d = parse_deadline_date(deadline_str)
    if d is None:
        return False
    return d < date.today()


# ── دریافت از RSS (Colossal) ─────────────────────────────────────────────
def fetch_rss_articles() -> list:
    articles = []
    seen_urls = set()
    for src in RSS_SOURCES:
        try:
            r = requests.get(src["url"], headers=HEADERS, timeout=15)
            root = ET.fromstring(r.content)
            channel = root.find("channel")
            if not channel:
                continue
            for item in channel.findall("item"):
                title = item.findtext("title", "").strip()
                link  = item.findtext("link",  "").strip()
                if not link or link in seen_urls:
                    continue
                if any(w in title.lower() for w in ["opportunit","open call","grant","residenc","fellowship","award"]):
                    seen_urls.add(link)
                    articles.append({"title": title, "url": link})
            time.sleep(1)
        except Exception as e:
            print(f"خطا در RSS {src['url']}: {e}")
    print(f"📡 Colossal: {len(articles)} مقاله")
    return articles


def extract_from_article(article: dict) -> list:
    """Claude همه فراخوان‌ها رو از یه مقاله کلوسال استخراج می‌کنه."""
    try:
        r = requests.get(article["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        content = ""
        for tag in soup.find_all(["article","main","div"]):
            t = tag.get_text(" ", strip=True)
            if len(t) > 500:
                content = t[:8000]
                break
        if not content:
            return []

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system="""Extract ALL individual art opportunities from the article.
Return ONLY a valid JSON array (no markdown):
[{"title":"...","org":"...","deadline":"e.g. May 15, 2026 or empty","country":"or empty","description":"1-2 sentences","url":"direct application URL or empty"}]
org must be the actual organizer, NOT Colossal. Return [] if none found.""",
            messages=[{"role":"user","content":f"Article: {article['title']}\n\n{content}"}]
        )
        text = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        ops = json.loads(text)
        print(f"  ✅ {len(ops)} فراخوان از مقاله")
        return ops
    except Exception as e:
        print(f"خطا در مقاله {article['url']}: {e}")
        return []


# ── دریافت از صفحات لیست (ArtJobs, NYFA) ────────────────────────────────
def fetch_list_opportunities(src: dict) -> list:
    """لینک‌های فراخوان رو از صفحه لیست می‌گیره."""
    try:
        r = requests.get(src["url"], headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if len(text) < 8:
                continue
            # لینک‌هایی که به صفحه فراخوان می‌رن
            if any(w in href for w in ["/open-call","/opportunity","/call-for","/residenc","/grant","/award","/fellowship"]):
                full = href if href.startswith("http") else src["base"] + href
                if full not in seen and full != src["url"]:
                    seen.add(full)
                    urls.append(full)
        print(f"🔗 {src['url'].split('/')[2]}: {len(urls)} لینک")
        return urls[:40]
    except Exception as e:
        print(f"خطا در {src['url']}: {e}")
        return []


def parse_single_opportunity(url: str) -> dict:
    """Claude اطلاعات یه فراخوان رو از صفحه‌اش استخراج می‌کنه."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)[:3000]

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system="""Extract art opportunity info from page. Return ONLY valid JSON (no markdown):
{"title":"...","org":"actual organizer name","deadline":"e.g. May 15, 2026 or empty","country":"or empty","description":"1-2 sentences","website":"direct application URL not this page URL, or empty"}
org must be the actual organizer institution, not a platform name.""",
            messages=[{"role":"user","content":f"URL: {url}\n\n{text}"}]
        )
        text_resp = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        data = json.loads(text_resp)
        data["url"] = data.get("website") or url
        return data
    except Exception as e:
        print(f"خطا در parse {url}: {e}")
        return {}


# ── ترجمه ────────────────────────────────────────────────────────────────
def translate_and_personalize(op: dict) -> dict:
    time.sleep(12)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system="""Persian content writer for art opportunities. Return ONLY valid JSON (no markdown):
{
  "title_fa": "Creative Persian title - NOT literal. Based on description write natural descriptive Persian title. e.g. 'BEERS London Group Exhibitions' → 'فراخوان شرکت در نمایشگاه گروهی گالری بیرز لندن'",
  "org_fa": "Keep proper nouns. Only translate generic names.",
  "deadline_fa": "Translate month to Persian, keep numbers as Persian digits. 'May 15, 2026'→'۱۵ می ۲۰۲۶'. Empty string if no deadline.",
  "country_fa": "Country in Persian. Empty if not provided.",
  "summary_fa": "2-3 engaging Persian sentences for a Persian artist about this opportunity."
}""",
        messages=[{"role":"user","content":json.dumps(op, ensure_ascii=False)}]
    )
    text = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
    return json.loads(text)


def format_message(op: dict, tr: dict) -> str:
    url = op.get("url","")
    lines = [f"📢 *{tr['title_fa']}*\n"]
    lines.append(f"🏛 {tr['org_fa']}")
    if tr.get("deadline_fa"):
        lines.append(f"📅 مهلت: {tr['deadline_fa']}")
    if tr.get("country_fa"):
        lines.append(f"🌍 {tr['country_fa']}")
    lines.append(f"\n📝 {tr['summary_fa']}\n")
    if url:
        lines.append(f"🔗 [مشاهده فراخوان اصلی]({url})")
    return "\n".join(lines)


def send_to_telegram(message: str):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id":TELEGRAM_CHANNEL,"text":message,"parse_mode":"Markdown","disable_web_page_preview":False}
    )
    if r.status_code != 200:
        print(f"خطا: {r.text}")
    else:
        print("✅ پست ارسال شد.")


def process_opportunity(op: dict, seen: set) -> bool:
    """یه فراخوان رو پردازش و پست می‌کنه. True اگه پست شد."""
    op_id = make_id(op.get("title","") + op.get("org",""))
    if op_id in seen:
        return False
    if deadline_passed(op.get("deadline","")):
        print(f"⏩ مهلت گذشته: {op.get('title','')}")
        seen.add(op_id)
        return False
    try:
        translated = translate_and_personalize(op)
        message = format_message(op, translated)
        send_to_telegram(message)
        seen.add(op_id)
        save_seen(seen)
        time.sleep(20)
        return True
    except Exception as e:
        print(f"خطا در '{op.get('title','')}': {e}")
        return False


def run_job():
    print(f"\nشروع کار: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()
    new_count = 0

    # ── منبع ۱: Colossal (RSS + استخراج از مقاله) ──
    articles = fetch_rss_articles()
    for article in articles:
        art_id = make_id(article["url"])
        if art_id in seen:
            continue
        print(f"\n📖 {article['title']}")
        ops = extract_from_article(article)
        time.sleep(3)
        for op in ops:
            if process_opportunity(op, seen):
                new_count += 1
        seen.add(art_id)
        save_seen(seen)

    # ── منبع ۲ و ۳: ArtJobs و NYFA (صفحه لیست) ──
    for src in SCRAPE_SOURCES:
        print(f"\n🔍 {src['url'].split('/')[2]}")
        urls = fetch_list_opportunities(src)
        for url in urls:
            url_id = make_id(url)
            if url_id in seen:
                continue
            op = parse_single_opportunity(url)
            if not op:
                seen.add(url_id)
                save_seen(seen)
                continue
            time.sleep(2)
            if process_opportunity(op, seen):
                new_count += 1
            seen.add(url_id)
            save_seen(seen)

    print(f"\n✅ {new_count} فراخوان جدید ارسال شد.")


schedule.every(6).hours.do(run_job)

if __name__ == "__main__":
    print("ربات فراخوان هنری شروع به کار کرد...")
    run_job()
    while True:
        schedule.run_pending()
        time.sleep(60)
