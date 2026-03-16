import os
import json
import time
import hashlib
import urllib.parse
import feedparser
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from newspaper import Article
from openai import OpenAI

# =========================
# 1. 사용자 설정
# =========================
OPENAI_API_KEY = "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"
EMAIL_ADDRESS = "han0408334@gmail.com"
EMAIL_APP_PASSWORD = "mjrt txac cdbc csmf"
TO_EMAIL = "han0408334@gmail.com"

NEWS_LIMIT_PER_SYMBOL = 3
SENT_FILE = "sent_news.json"

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# 2. 종목 / 키워드 / RSS 설정
# =========================
# notes:
# - ticker가 있는 미국 상장사는 Yahoo RSS 사용
# - Infleqtion은 비상장이라 키워드형 RSS만 사용
# - 로킷헬스케어는 국내 키워드형 RSS 위주
# - Bitcoin은 Yahoo + CoinDesk + Google News 혼합

TRACKERS = [
    {
        "name": "Rocket Lab",
        "display_name": "RKLB",
        "query_keywords": ["Rocket Lab", "RKLB", "Neutron"],
        "rss_urls": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=RKLB&region=US&lang=en-US",
            "https://news.google.com/rss/search?q=Rocket%20Lab%20OR%20RKLB&hl=en-US&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "IonQ",
        "display_name": "IONQ",
        "query_keywords": ["IonQ", "IONQ"],
        "rss_urls": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=IONQ&region=US&lang=en-US",
            "https://news.google.com/rss/search?q=IonQ%20OR%20IONQ&hl=en-US&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Infleqtion",
        "display_name": "INFQ",
        "query_keywords": ["Infleqtion", "ColdQuanta", "quantum"],
        "rss_urls": [
            "https://news.google.com/rss/search?q=Infleqtion%20OR%20ColdQuanta&hl=en-US&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Hims & Hers",
        "display_name": "HIMS",
        "query_keywords": ["Hims & Hers", "HIMS"],
        "rss_urls": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=HIMS&region=US&lang=en-US",
            "https://news.google.com/rss/search?q=Hims%20%26%20Hers%20OR%20HIMS&hl=en-US&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "로킷헬스케어",
        "display_name": "로킷헬스케어",
        "query_keywords": ["로킷헬스케어", "Rokit Healthcare", "ROKIT Healthcare"],
        "rss_urls": [
            "https://news.google.com/rss/search?q=%EB%A1%9C%ED%82%B7%ED%97%AC%EC%8A%A4%EC%BC%80%EC%96%B4%20OR%20%22Rokit%20Healthcare%22&hl=ko&gl=KR&ceid=KR:ko",
            "https://news.google.com/rss/search?q=%22ROKIT%20Healthcare%22&hl=en-US&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Bitcoin",
        "display_name": "비트코인",
        "query_keywords": ["Bitcoin", "BTC", "crypto"],
        "rss_urls": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://news.google.com/rss/search?q=Bitcoin%20OR%20BTC&hl=en-US&gl=US&ceid=US:en",
        ],
    },
]


# =========================
# 3. 보낸 뉴스 기록
# =========================
def load_sent_news():
    if not os.path.exists(SENT_FILE):
        return set()

    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_sent_news(sent_links):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_links), f, ensure_ascii=False, indent=2)


# =========================
# 4. 유틸
# =========================
def normalize_text(text):
    return " ".join((text or "").strip().lower().split())


def make_entry_key(link, title):
    base = (link or "").strip()
    if not base:
        base = normalize_text(title)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def format_publish_date(entry):
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            return f"{dt.year}년 {dt.month}월 {dt.day}일"
        return "날짜 정보 없음"
    except Exception:
        return "날짜 정보 없음"


def clean_json_text(raw_text):
    cleaned = raw_text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    return cleaned


def title_matches_tracker(title, keywords):
    t = normalize_text(title)
    for kw in keywords:
        if normalize_text(kw) in t:
            return True
    return False


# =========================
# 5. RSS 수집
# =========================
def fetch_tracker_news(tracker, sent_keys, limit=3):
    collected = []
    current_seen_keys = set()

    for rss_url in tracker["rss_urls"]:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()

            if not title and not link:
                continue

            # Google News / CoinDesk 같은 범용 소스는 키워드 매칭 한번 더
            if "google.com/rss/search" in rss_url or "coindesk.com" in rss_url:
                if not title_matches_tracker(title, tracker["query_keywords"]):
                    continue

            entry_key = make_entry_key(link, title)

            if entry_key in sent_keys:
                continue

            if entry_key in current_seen_keys:
                continue

            current_seen_keys.add(entry_key)
            collected.append(entry)

            if len(collected) >= limit:
                return collected

        time.sleep(0.3)

    return collected[:limit]


# =========================
# 6. 기사 본문 가져오기
# =========================
def get_article_text(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        text = article.text.strip()

        if not text:
            return "본문 추출 실패: 기사 본문이 비어 있습니다."

        return text[:5000]
    except Exception as e:
        return f"본문 추출 실패: {e}"


# =========================
# 7. AI 분석
# =========================
def summarize_news(company_name, original_title, link, text):
    prompt = f"""
다음 뉴스 기사를 한국어로 분석해줘.

대상 기업/자산:
{company_name}

목적은 매수/매도 판단이 아니라,
이 기업(또는 자산)의 펀더멘털/핵심 논리가 유지되는지, 강화되는지, 흔들리는지 추적하는 것이다.

반드시 JSON 형식만 출력해.
설명 문장, 코드블록, 추가 코멘트는 절대 넣지 마.

형식:
{{
  "korean_title": "한국어 기사 제목",
  "summary": [
    "핵심 내용 요약 1",
    "핵심 내용 요약 2"
  ],
  "fundamental_impact": [
    "이 뉴스가 펀더멘털에 주는 의미 1",
    "이 뉴스가 펀더멘털에 주는 의미 2"
  ],
  "watch_points": [
    "앞으로 체크해야 할 변화 1",
    "앞으로 체크해야 할 변화 2"
  ],
  "conclusion": "펀더멘털 관점 한줄 결론"
}}

작성 원칙:
- 과장하지 말 것
- 모르면 단정하지 말 것
- 주가 전망, 매수/매도, 점수화 금지
- 기사에 근거해 사업, 제품, 고객, 경쟁력, 실행력, 수요, 규제, 수주, 채택, 재무적 함의를 요약
- 비트코인의 경우 기업 대신 네트워크/제도/수요/매크로 관점으로 요약
- 결론은 짧고 분명하게 작성

기사 제목:
{original_title}

기사 링크:
{link}

기사 본문:
{text}
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    raw = clean_json_text(response.output_text)

    try:
        data = json.loads(raw)
        return {
            "korean_title": data.get("korean_title", original_title),
            "summary": data.get("summary", []),
            "fundamental_impact": data.get("fundamental_impact", []),
            "watch_points": data.get("watch_points", []),
            "conclusion": data.get("conclusion", "결론 없음"),
        }
    except Exception:
        return {
            "korean_title": original_title,
            "summary": ["AI 분석 결과를 읽는 데 실패했습니다."],
            "fundamental_impact": [],
            "watch_points": [],
            "conclusion": "AI 분석 파싱 실패",
        }


# =========================
# 8. 본문 조립 보조
# =========================
def append_section(body, section_title, items):
    body.append(section_title)
    if items:
        for item in items:
            body.append(f"- {item}")
    else:
        body.append("- 내용 없음")
    body.append("")


# =========================
# 9. 이메일 본문 생성
# =========================
def build_email_body(all_tracker_news):
    body = []
    body.append("📩 펀더멘털 트래킹 리포트")
    body.append("=" * 80)
    body.append("")
    body.append("포함 종목/자산: RKLB, IONQ, INFQ(Infleqtion), HIMS, 로킷헬스케어, 비트코인")
    body.append(f"종목별 최대 기사 수: {NEWS_LIMIT_PER_SYMBOL}")
    body.append("")

    for tracker_name, entries in all_tracker_news.items():
        if not entries:
            continue

        body.append(f"■ {tracker_name}")
        body.append("-" * 80)
        body.append("")

        for idx, item in enumerate(entries, start=1):
            title = getattr(item, "title", "").strip()
            link = getattr(item, "link", "").strip()
            publish_date = format_publish_date(item)

            print(f"[{tracker_name}] {idx}/{len(entries)} 기사 처리 중")

            article_text = get_article_text(link)
            ai_result = summarize_news(tracker_name, title, link, article_text)

            korean_title = ai_result.get("korean_title", title)
            summary = ai_result.get("summary", [])
            fundamental_impact = ai_result.get("fundamental_impact", [])
            watch_points = ai_result.get("watch_points", [])
            conclusion = ai_result.get("conclusion", "결론 없음")

            body.append(f"{idx}. {korean_title}")
            body.append(f"기사 날짜: {publish_date}")
            body.append(f"원문 링크: {link}")
            body.append("")

            append_section(body, "핵심 내용 요약", summary)
            append_section(body, "펀더멘털 영향", fundamental_impact)
            append_section(body, "체크할 변화", watch_points)

            body.append("한줄 결론")
            body.append(f"- {conclusion}")
            body.append("")
            body.append("." * 80)
            body.append("")

        body.append("")

    return "\n".join(body)


# =========================
# 10. 이메일 발송
# =========================
def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        server.send_message(msg)


# =========================
# 11. 메인 실행
# =========================
def main():
    sent_keys = load_sent_news()
    all_tracker_news = {}
    new_sent_keys = set(sent_keys)

    total_new_count = 0

    for tracker in TRACKERS:
        entries = fetch_tracker_news(
            tracker=tracker,
            sent_keys=sent_keys,
            limit=NEWS_LIMIT_PER_SYMBOL
        )

        if entries:
            all_tracker_news[tracker["display_name"]] = entries
            total_new_count += len(entries)

            for entry in entries:
                key = make_entry_key(
                    getattr(entry, "link", "").strip(),
                    getattr(entry, "title", "").strip()
                )
                new_sent_keys.add(key)

    if total_new_count == 0:
        print("새로운 뉴스 없음")
        return

    email_body = build_email_body(all_tracker_news)

    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"📩 펀더멘털 트래킹 리포트 ({today})"

    send_email(subject, email_body)
    save_sent_news(new_sent_keys)

    print("이메일 발송 완료")


if __name__ == "__main__":
    main()