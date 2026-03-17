import os
import json
import time
import hashlib
import feedparser
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from newspaper import Article
from openai import OpenAI

# =========================
# 환경 변수
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")

NEWS_LIMIT_PER_SYMBOL = 3
SENT_FILE = "sent_news.json"

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# 종목 설정
# =========================
TRACKERS = [
    {
        "name": "Rocket Lab",
        "display_name": "RKLB",
        "keywords": ["Rocket Lab", "RKLB"],
        "rss": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=RKLB&region=US&lang=en-US",
            "https://news.google.com/rss/search?q=Rocket%20Lab%20OR%20RKLB&hl=en-US&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Hims & Hers",
        "display_name": "HIMS",
        "keywords": ["Hims", "HIMS"],
        "rss": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=HIMS&region=US&lang=en-US",
        ],
    },
    {
        "name": "로킷헬스케어",
        "display_name": "로킷헬스케어",
        "keywords": ["로킷헬스케어", "ROKIT"],
        "rss": [
            "https://news.google.com/rss/search?q=로킷헬스케어&hl=ko&gl=KR&ceid=KR:ko",
        ],
    },
    {
        "name": "Bitcoin",
        "display_name": "비트코인",
        "keywords": ["Bitcoin", "BTC"],
        "rss": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
        ],
    },
]

# =========================
# 유틸
# =========================
def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    with open(SENT_FILE, "r") as f:
        return set(json.load(f))


def save_sent(data):
    with open(SENT_FILE, "w") as f:
        json.dump(list(data), f)


def make_key(link, title):
    base = link or title
    return hashlib.sha256(base.encode()).hexdigest()


def format_date(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6])
        return f"{dt.year}년 {dt.month}월 {dt.day}일"
    return "날짜 없음"


# =========================
# 뉴스 수집
# =========================
def fetch_news(tracker, sent):
    result = []
    seen = set()

    for url in tracker["rss"]:
        feed = feedparser.parse(url)

        for e in feed.entries:
            title = getattr(e, "title", "")
            link = getattr(e, "link", "")

            key = make_key(link, title)

            if key in sent or key in seen:
                continue

            seen.add(key)
            result.append(e)

            if len(result) >= NEWS_LIMIT_PER_SYMBOL:
                return result

    return result


# =========================
# 본문 가져오기
# =========================
def get_text(url):
    try:
        a = Article(url)
        a.download()
        a.parse()
        return a.text[:3000]
    except:
        return ""


# =========================
# AI 분석 (결론만)
# =========================
def analyze(title, text, company):

    prompt = f"""
다음 뉴스 기사를 한국어로 분석해.

목표: 이 기업의 핵심 흐름이 유지되는지 한줄로 판단

JSON만 출력:

{{
 "korean_title": "...",
 "conclusion": "..."
}}

기사 제목:
{title}

본문:
{text}
"""

    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    raw = res.output_text.strip().replace("```", "")

    try:
        data = json.loads(raw)
        return data
    except:
        return {
            "korean_title": title,
            "conclusion": "분석 실패"
        }


# =========================
# 이메일 생성
# =========================
def build_body(all_news):

    body = []
    body.append("📩 핵심 흐름 리포트\n")
    body.append("="*60 + "\n")

    for name, items in all_news.items():

        body.append(f"\n■ {name}\n")

        for i, item in enumerate(items, 1):

            title = item.title
            link = item.link
            date = format_date(item)

            text = get_text(link)
            result = analyze(title, text, name)

            body.append(f"{i}. {result['korean_title']}")
            body.append(f"날짜: {date}")
            body.append(f"링크: {link}\n")

            body.append("결론")
            body.append(f"- {result['conclusion']}\n")

            body.append("."*60 + "\n")

    return "\n".join(body)


# =========================
# 이메일 발송
# =========================
def send(subject, body):

    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        s.send_message(msg)


# =========================
# 메인
# =========================
def main():

    sent = load_sent()
    new_sent = set(sent)

    all_news = {}

    for t in TRACKERS:

        items = fetch_news(t, sent)

        if items:
            all_news[t["display_name"]] = items

            for i in items:
                new_sent.add(make_key(i.link, i.title))

    if not all_news:
        print("새 뉴스 없음")
        return

    body = build_body(all_news)

    today = datetime.now().strftime("%Y-%m-%d")
    send(f"📩 핵심 흐름 리포트 {today}", body)

    save_sent(new_sent)

    print("메일 전송 완료")


if __name__ == "__main__":
    main()
