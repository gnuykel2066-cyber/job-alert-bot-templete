#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
텔레그램 채용 공고 알림 봇
- 사람인 공식 Open API로 관심 키워드의 새 공고를 검색 (승인 대기 중이면 자동으로 건너뜀)
- 사람인 공식 API 승인 전 임시 대체 수단으로, 사람인 검색결과 페이지가 내부적으로 쓰는
  비공식 엔드포인트(zf_user/search/get-recruit-list)를 직접 조회해 같은 키워드로 공고를 가져옴
  (robots.txt상 이 검색 경로는 크롤링 금지 대상이 아님 -- 금지 대상은 상세페이지인 zf_user/recruit/view/.
  다만 공식 API가 아니라서 예고 없이 응답 형식이 바뀌거나 막힐 수 있어 방어적으로 처리)
- (선택) 원티드 비공식 검색 API도 함께 조회 (사람인만으로 부족할 때 대비한 보조 수단.
  원티드의 공식 지원 API가 아니라서 예고 없이 막히거나 응답 형식이 바뀔 수 있음 -- 실패해도
  전체 스크립트가 죽지 않도록 예외 처리되어 있음)
- 이전 실행 때 이미 알려준 공고는 seen_jobs.json 에 기록해두고 건너뜀
- 새 공고가 있으면 텔레그램으로 메시지 전송

필요한 환경변수 (GitHub Actions Secrets 로 주입):
  SARAMIN_ACCESS_KEY   - 사람인 Open API access-key (https://oapi.saramin.co.kr) -- 승인 전이면 없어도 됨
  TELEGRAM_BOT_TOKEN   - @BotFather 에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID     - 알림을 받을 telegram chat id
  SLACK_WEBHOOK_URL    - (선택) 슬랙 인커밍 웹훅 URL. 이것만 있으면 기존처럼 알림만 감
  SLACK_BOT_TOKEN      - (선택) 슬랙 봇 토큰(xoxb-...). 이걸 넣어야 "이력서 강조 포인트 제안"이
                          공고 알림 밑에 댓글(스레드 답글)로 달림. 발급: api.slack.com/apps ->
                          앱 생성 -> OAuth & Permissions -> Bot Token Scopes에 chat:write 추가
                          -> Install to Workspace -> Bot User OAuth Token 복사
  SLACK_CHANNEL_ID     - (선택, SLACK_BOT_TOKEN과 같이 필요) 알림 보낼 슬랙 채널 ID.
                          해당 채널에 봇을 초대(/invite @봇이름)해둬야 함
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # requirements.txt에 beautifulsoup4가 없던 예전 환경 대비
    BeautifulSoup = None

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

SEEN_FILE = Path(__file__).parent / "seen_jobs.json"
SEEN_RETENTION_DAYS = 60          # 이 기간이 지난 기록은 파일에서 정리
MAX_NOTIFY_PER_RUN = 15           # 한 번 실행에 알려줄 최대 신규 공고 수 (너무 많으면 스팸이 되므로 상한)

# 관심 직무 키워드 (사람인 keywords 파라미터에 하나씩 순차 검색)
# ↓↓↓ 여기를 본인이 찾고 있는 직무 키워드로 바꾸세요 (예시입니다) ↓↓↓
SARAMIN_KEYWORDS = [
    "백엔드 개발자",
    "프론트엔드 개발자",
    "데이터 분석가",
]

# 원티드 보조 검색 키워드 (비공식 API, 실패해도 무시)
WANTED_KEYWORDS = [
    "백엔드 개발자",
    "프론트엔드 개발자",
]

ENABLE_WANTED_SUPPLEMENT = False  # 원티드 비공식 API가 GitHub Actions에서도 403으로 막혀 꺼둠 (구글 CSE로 원티드 대체 커버)

# 구글 커스텀 검색(CSE)으로 사람인/잡코리아/인크루트를 함께 훑는 기능 (사람인 API 승인 여부와 무관하게 동작)
ENABLE_GOOGLE_CSE = False  # 구글 Custom Search API가 신규 프로젝트에 막혀 있어 꺼둠 (사람인·잡코리아·인크루트·원티드는 Claude 쪽 3시간 자동 알림이 웹서치로 이미 커버)
GOOGLE_CSE_SITES = ["saramin.co.kr", "jobkorea.co.kr", "incruit.com", "wanted.co.kr"]
# 사이트당 쿼리 1개로 합쳐서 무료 한도(하루 100건)를 아낀다 (OR 검색)
GOOGLE_CSE_QUERY_KEYWORDS = "(백엔드 개발자 OR 프론트엔드 개발자 OR 데이터 분석가) 채용"
GOOGLE_CSE_RESULTS_PER_QUERY = 10  # 구글 CSE 한 번 호출당 결과 수 (최대 10)

SARAMIN_ACCESS_KEY = os.environ.get("SARAMIN_ACCESS_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

# 슬랙에 "댓글(스레드 답글)"을 달려면 웹훅(SLACK_WEBHOOK_URL)만으로는 안 되고, 봇 토큰으로
# chat.postMessage를 호출해서 방금 보낸 메시지의 ts(타임스탬프)를 받아와야 함.
# 슬랙 앱(api.slack.com/apps) > OAuth & Permissions에서 chat:write 권한으로 발급받은
# "Bot User OAuth Token"과, 알림을 보낼 채널의 ID를 각각 넣어준다.
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")

HEADERS_BROWSER_LIKE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


# ---------------------------------------------------------------------------
# 상태 파일 (이미 알려준 공고 목록) 읽기/쓰기
# ---------------------------------------------------------------------------

def load_seen():
    if not SEEN_FILE.exists():
        return {"seen": [], "initialized": False}
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen": [], "initialized": False}
    data.setdefault("seen", [])
    # 예전 파일(하위호환)에 이미 seen 항목이 있으면 초기화된 것으로 간주
    data.setdefault("initialized", bool(data["seen"]))
    return data


def save_seen(seen_ids_with_date, initialized=True):
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)
    pruned = [
        item
        for item in seen_ids_with_date
        if _safe_parse_date(item.get("date")) >= cutoff
    ]
    SEEN_FILE.write_text(
        json.dumps(
            {
                "seen": pruned,
                "initialized": initialized,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def _safe_parse_date(date_str):
    try:
        return datetime.fromisoformat(date_str)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 사람인 Open API
# ---------------------------------------------------------------------------

def search_saramin(keyword, count=30):
    """사람인 Open API로 공고 검색. 실패 시 빈 리스트 반환."""
    if not SARAMIN_ACCESS_KEY:
        print("[saramin] SARAMIN_ACCESS_KEY 가 설정되지 않아 건너뜁니다.", file=sys.stderr)
        return []

    url = "https://oapi.saramin.co.kr/job-search"
    params = {
        "access-key": SARAMIN_ACCESS_KEY,
        "keywords": keyword,
        "count": count,
        "sort": "dd",  # 등록일 최신순
    }
    try:
        resp = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"[saramin] '{keyword}' 검색 실패: {e}", file=sys.stderr)
        return []

    jobs = data.get("jobs", {}).get("job", [])
    if isinstance(jobs, dict):  # 결과가 1건이면 리스트가 아니라 dict로 옴
        jobs = [jobs]

    results = []
    for job in jobs:
        try:
            job_id = f"saramin:{job['id']}"
            title = job.get("position", {}).get("title", "제목 미상")
            company = job.get("company", {}).get("detail", {}).get("name", "회사명 미상")
            job_url = job.get("url", "")
            results.append({"id": job_id, "title": title, "company": company, "url": job_url, "platform": "사람인"})
        except Exception:  # noqa: BLE001
            continue
    return results


# ---------------------------------------------------------------------------
# 사람인 검색결과 페이지 직접 조회 (비공식 - 공식 API 승인 전 임시 대체/보조 수단)
# 사람인 검색 페이지가 화면 갱신을 위해 내부적으로 호출하는 엔드포인트를 그대로 사용.
# ---------------------------------------------------------------------------

ENABLE_SARAMIN_SCRAPE = True  # 사람인 공식 API 승인 대기 중이라, 승인 전까지는 이 방식으로 대체
SARAMIN_SCRAPE_URL = "https://www.saramin.co.kr/zf_user/search/get-recruit-list"
SARAMIN_SCRAPE_MAX_PAGES = 1  # 키워드당 몇 페이지까지 볼지 (1페이지 = 최대 40건)


def search_saramin_scrape(keyword, max_pages=SARAMIN_SCRAPE_MAX_PAGES):
    """사람인 공식 API 대신, 검색결과 페이지가 쓰는 비공식 엔드포인트로 공고를 가져온다.
    id 형식을 search_saramin()과 동일하게 "saramin:숫자"로 맞춰서, 나중에 공식 API 승인이 나서
    두 방식이 같은 공고를 함께 찾아내도 자동으로 중복 제거되게 한다."""
    if BeautifulSoup is None:
        print("[saramin_scrape] beautifulsoup4가 설치되지 않아 건너뜁니다.", file=sys.stderr)
        return []

    results = []
    for page in range(1, max_pages + 1):
        params = {
            "searchType": "search",
            "searchword": keyword,
            "recruitPage": page,
            "recruitSort": "relation",
            "recruitPageCount": 40,
            "search_optional_item": "y",
            "search_done": "y",
            "panel_count": "y",
            "preview": "y",
            "mainSearch": "n",
        }
        try:
            resp = requests.get(SARAMIN_SCRAPE_URL, params=params, headers=HEADERS_BROWSER_LIKE, timeout=20)
            if resp.status_code != 200:
                print(f"[saramin_scrape] '{keyword}' 응답 코드 {resp.status_code}, 건너뜁니다.", file=sys.stderr)
                break
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            print(f"[saramin_scrape] '{keyword}' 요청 실패(무시): {e}", file=sys.stderr)
            break

        inner_html = data.get("innerHTML", "")
        if not inner_html:
            break

        soup = BeautifulSoup(inner_html, "html.parser")
        items = soup.select("div.item_recruit")
        if not items:
            break

        for item in items:
            try:
                job_id = (item.get("value") or "").strip()
                title_tag = item.select_one("div.area_job h2.job_tit a")
                company_tag = item.select_one("div.area_corp strong.corp_name a")
                if not job_id or not title_tag:
                    continue
                title = title_tag.get("title") or title_tag.get_text(strip=True)
                company = company_tag.get_text(strip=True) if company_tag else "회사명 미상"
                href = title_tag.get("href", "")
                job_url = f"https://www.saramin.co.kr{href}" if href.startswith("/") else href
                results.append({
                    "id": f"saramin:{job_id}",
                    "title": title,
                    "company": company,
                    "url": job_url,
                    "platform": "사람인",
                })
            except Exception:  # noqa: BLE001
                continue

        time.sleep(0.3)

    return results


# ---------------------------------------------------------------------------
# 원티드 보조 검색 (비공식 - 실패해도 무시)
# ---------------------------------------------------------------------------

def search_wanted(keyword, limit=20):
    """원티드 비공식 검색 API. 언제든 바뀌거나 막힐 수 있어 최대한 방어적으로 처리."""
    url = "https://www.wanted.co.kr/api/v4/jobs"
    params = {"query": keyword, "country": "kr", "job_sort": "job.latest_order"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS_BROWSER_LIKE, timeout=15)
        if resp.status_code != 200:
            print(f"[wanted] '{keyword}' 검색 응답 코드 {resp.status_code}, 건너뜁니다.", file=sys.stderr)
            return []
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"[wanted] '{keyword}' 검색 실패(무시): {e}", file=sys.stderr)
        return []

    results = []
    for item in data.get("data", [])[:limit]:
        try:
            job_id = f"wanted:{item['id']}"
            title = item.get("position", "제목 미상")
            company = item.get("company", {}).get("name", "회사명 미상")
            job_url = f"https://www.wanted.co.kr/wd/{item['id']}"
            results.append({"id": job_id, "title": title, "company": company, "url": job_url, "platform": "원티드"})
        except Exception:  # noqa: BLE001
            continue
    return results


# ---------------------------------------------------------------------------
# 구글 커스텀 검색(CSE) — 사람인/잡코리아/인크루트 개별 공고 링크 찾기
# 승인 대기가 필요한 사람인/잡코리아 자체 API 대신, 구글 검색 결과를 통해 우회.
# GOOGLE_CSE_API_KEY, GOOGLE_CSE_ID 가 없으면 조용히 건너뜀.
# ---------------------------------------------------------------------------

LISTING_PAGE_HINTS = ("search", "joblist", "job-category", "job-industry", "recently-list", "jobdb_list")

SARAMIN_ID_PATTERN = re.compile(r"rec_idx=(\d+)")
JOBKOREA_ID_PATTERN = re.compile(r"/GI_Read/(\d+)")
WANTED_ID_PATTERN = re.compile(r"/wd/(\d+)")


def parse_job_from_url(url, title, company_guess):
    url_lower = url.lower()
    if any(hint in url_lower for hint in LISTING_PAGE_HINTS):
        return None  # 개별 공고가 아니라 목록/검색 페이지로 보임

    if "saramin.co.kr" in url_lower:
        m = SARAMIN_ID_PATTERN.search(url)
        if not m:
            return None
        return {"id": f"saramin:{m.group(1)}", "title": title, "company": company_guess,
                "url": url, "platform": "사람인"}

    if "jobkorea.co.kr" in url_lower:
        m = JOBKOREA_ID_PATTERN.search(url)
        if not m:
            return None
        return {"id": f"jobkorea:{m.group(1)}", "title": title, "company": company_guess,
                "url": url, "platform": "잡코리아"}

    if "incruit.com" in url_lower:
        # 인크루트는 URL 구조가 덜 일정해서, 목록성 페이지만 걸러내고 URL 자체를 id로 사용
        return {"id": f"incruit:{url}", "title": title, "company": company_guess,
                "url": url, "platform": "인크루트"}

    if "wanted.co.kr" in url_lower:
        m = WANTED_ID_PATTERN.search(url)
        if not m:
            return None
        # search_wanted()가 만드는 id("wanted:숫자")와 형식을 맞춰서, 같은 공고면 자동으로 중복 제거됨
        return {"id": f"wanted:{m.group(1)}", "title": title, "company": company_guess,
                "url": url, "platform": "원티드"}

    return None


def search_google_cse(query, num=GOOGLE_CSE_RESULTS_PER_QUERY):
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        print("[google_cse] GOOGLE_CSE_API_KEY 또는 GOOGLE_CSE_ID 가 없어 건너뜁니다.", file=sys.stderr)
        return []
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GOOGLE_CSE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": min(num, 10)}
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"[google_cse] '{query}' 검색 응답 코드 {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return []
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"[google_cse] '{query}' 검색 실패(무시): {e}", file=sys.stderr)
        return []

    results = []
    for item in data.get("items", []):
        link = item.get("link", "")
        title = item.get("title", "제목 미상")
        company_guess = item.get("displayLink", "회사명 미상")
        job = parse_job_from_url(link, title, company_guess)
        if job:
            results.append(job)
    return results


# ---------------------------------------------------------------------------
# 텔레그램 알림
# ---------------------------------------------------------------------------

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] BOT_TOKEN 또는 CHAT_ID 가 없어 전송을 건너뜁니다.", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] 메시지 전송 실패: {e}", file=sys.stderr)
        return False


def send_slack_bot_message(text, thread_ts=None):
    """슬랙 봇 토큰(chat.postMessage)으로 메시지를 보낸다. 웹훅과 달리 보낸 메시지의
    ts(타임스탬프)를 돌려받을 수 있어서, 이 ts를 thread_ts로 다시 넘기면 그 메시지 밑에
    '댓글(스레드 답글)'을 달 수 있다. SLACK_BOT_TOKEN/SLACK_CHANNEL_ID가 없으면 None 반환."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        return None
    payload = {"channel": SLACK_CHANNEL_ID, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"[slack_bot] 메시지 전송 실패: {data.get('error')}", file=sys.stderr)
            return None
        return data.get("ts")
    except Exception as e:  # noqa: BLE001
        print(f"[slack_bot] 메시지 전송 실패(예외): {e}", file=sys.stderr)
        return None


def send_slack_message(text):
    if SLACK_WEBHOOK_URL:
        try:
            resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
            resp.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[slack] 웹훅 전송 실패: {e}", file=sys.stderr)
            return False
    # 웹훅이 없으면 봇 토큰 방식으로라도 시도 (단, 이 경로는 스레드 답글은 못 닮)
    if SLACK_BOT_TOKEN and SLACK_CHANNEL_ID:
        return send_slack_bot_message(text) is not None
    print("[slack] SLACK_WEBHOOK_URL, SLACK_BOT_TOKEN 모두 없어 전송을 건너뜁니다.", file=sys.stderr)
    return False


def notify(text_telegram, text_slack=None):
    """텔레그램과 슬랙 양쪽에 알림을 보낸다 (설정 안 된 채널은 조용히 건너뜀)."""
    send_telegram_message(text_telegram)
    send_slack_message(text_slack if text_slack is not None else _strip_html(text_telegram))


# ---------------------------------------------------------------------------
# 이력서/자소서 수정 제안 (AI 없이 키워드 매칭 방식)
# 본인의 실제 경력·이력서 내용만 근거로 채워 넣으세요 (아래는 예시입니다).
# 공고 제목의 키워드와 아래 카테고리를 단순 매칭해서, 가장 관련 있는 이력서 강조 포인트를
# 골라준다. AI를 쓰지 않으므로 완벽하지 않을 수 있고, 매칭되는 카테고리가 없으면
# 억지로 제안을 달지 않고 조용히 건너뛴다.
#
# ↓↓↓ 아래 예시를 지우고, 본인의 실제 경력·기술 스택으로 카테고리를 만드세요 ↓↓↓
# ---------------------------------------------------------------------------

RESUME_PROFILE = [
    {
        "category": "백엔드 개발",
        "keywords": ["백엔드", "서버", "api", "backend"],
        "highlights": [
            "(예시) OOO 서비스 백엔드 API 설계 및 운영 경험",
            "(예시) 트래픽 급증 대응을 위한 캐싱/DB 최적화 경험",
            "(예시) Python/Java/Go 등 사용 가능 언어와 프레임워크",
        ],
    },
    {
        "category": "프론트엔드 개발",
        "keywords": ["프론트엔드", "frontend", "react", "vue"],
        "highlights": [
            "(예시) React/Vue 기반 서비스 화면 개발 경험",
            "(예시) 성능 최적화 및 접근성 개선 경험",
            "(예시) 디자인 시스템 구축/협업 경험",
        ],
    },
    {
        "category": "데이터 분석",
        "keywords": ["데이터", "분석", "data", "sql"],
        "highlights": [
            "(예시) SQL/Python으로 지표 대시보드 구축 경험",
            "(예시) A/B 테스트 설계 및 분석 경험",
            "(예시) 데이터 기반 의사결정 지원 경험",
        ],
    },
]


def suggest_resume_points(job_title):
    """공고 제목의 키워드로 가장 관련 있는 이력서 강조 포인트 카테고리를 찾는다.
    일치하는 게 없으면 None을 반환해서, 안 맞는 제안을 억지로 달지 않게 한다."""
    title_lower = (job_title or "").lower()
    best = None
    best_score = 0
    for entry in RESUME_PROFILE:
        score = sum(1 for kw in entry["keywords"] if kw.lower() in title_lower)
        if score > best_score:
            best_score = score
            best = entry
    return best


def format_resume_suggestion_message(entry):
    lines = [f"📝 이력서/자소서 강조 포인트 제안 ({entry['category']} 관련 공고로 보여요)", ""]
    for point in entry["highlights"]:
        lines.append(f"• {point}")
    lines.append("")
    lines.append("(공고 제목 키워드로 자동 매칭한 것이라 정확하지 않을 수 있어요. 원문 확인 후 다듬어 쓰세요.)")
    return "\n".join(lines)


def _strip_html(text):
    # 슬랙은 <b> 같은 텔레그램 HTML 태그를 못 쓰므로 슬랙용은 태그만 제거한 텍스트로 보냄
    return text.replace("<b>", "*").replace("</b>", "*")


def format_job_message(job):
    return (
        f"🆕 <b>{job['company']}</b>\n"
        f"{job['title']}\n"
        f"({job['platform']})\n"
        f"{job['url']}"
    )


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    state = load_seen()
    seen_list = state.get("seen", [])
    seen_ids = {item["id"] for item in seen_list}
    # 예전엔 "seen 목록이 비어있으면 첫 실행"으로 판단해서, 매 검색이 0건으로 끝나면
    # (예: API 키 문제) 영원히 "첫 실행"으로 착각해 매번 초기화 알림이 반복 발송되는 버그가 있었음.
    # 이제는 "한 번이라도 정상적으로 초기화된 적 있는지"를 별도로 기록해서 판단.
    is_first_run = not state.get("initialized", False)

    all_jobs = {}

    for kw in SARAMIN_KEYWORDS:
        for job in search_saramin(kw):
            all_jobs[job["id"]] = job
        time.sleep(0.3)  # API 과호출 방지용 살짝 텀

    if ENABLE_SARAMIN_SCRAPE:
        for kw in SARAMIN_KEYWORDS:
            for job in search_saramin_scrape(kw):
                all_jobs[job["id"]] = job
            time.sleep(0.3)

    if ENABLE_WANTED_SUPPLEMENT:
        for kw in WANTED_KEYWORDS:
            for job in search_wanted(kw):
                all_jobs[job["id"]] = job
            time.sleep(0.3)

    if ENABLE_GOOGLE_CSE:
        for site in GOOGLE_CSE_SITES:
            query = f"site:{site} {GOOGLE_CSE_QUERY_KEYWORDS}"
            for job in search_google_cse(query):
                all_jobs[job["id"]] = job
            time.sleep(0.5)  # 구글 CSE 무료 한도(하루 100건) 아끼기용 텀

    new_jobs = [job for job_id, job in all_jobs.items() if job_id not in seen_ids]

    now_iso = datetime.now(timezone.utc).isoformat()

    if is_first_run:
        # 첫 실행: 전부 기준선으로만 저장하고 알림은 보내지 않음 (안 그러면 기존 공고가 전부 "신규"로 쏟아짐)
        # 0건이었더라도 initialized=True로 저장해서, 다음 실행부터는 더 이상 "첫 실행"으로 취급하지 않음
        updated_seen = seen_list + [{"id": job_id, "date": now_iso} for job_id in all_jobs]
        save_seen(updated_seen, initialized=True)
        notify(
            f"✅ 채용 공고 알림 봇 초기 설정 완료.\n총 {len(all_jobs)}건을 기준으로 저장했습니다.\n"
            f"다음 실행부터 새로운 공고만 알려드릴게요."
        )
        print(f"첫 실행: {len(all_jobs)}건 기준선 저장, 알림 없음")
        return

    if not new_jobs:
        print("새 공고 없음")
        # 상태 파일은 그대로 두되, 혹시 몰라 최신 갱신시각만 남겨서 저장
        save_seen(seen_list)
        return

    to_notify = new_jobs[:MAX_NOTIFY_PER_RUN]

    header = f"📢 새 채용 공고 {len(new_jobs)}건 발견"
    if len(new_jobs) > len(to_notify):
        header += f" (상위 {len(to_notify)}건만 표시)"
    notify(header)

    for job in to_notify:
        job_text = format_job_message(job)
        send_telegram_message(job_text)

        # 슬랙 봇 토큰이 설정돼 있으면: 공고 알림을 봇 토큰으로 보내서 ts를 받아오고,
        # 그 ts를 이용해 이력서/자소서 강조 포인트 제안을 "댓글(스레드 답글)"로 붙인다.
        # 봇 토큰이 없으면(웹훅만 있으면) 기존처럼 그냥 알림만 보내고 제안은 생략한다.
        slack_ts = None
        if SLACK_BOT_TOKEN and SLACK_CHANNEL_ID:
            slack_ts = send_slack_bot_message(_strip_html(job_text))
        else:
            send_slack_message(_strip_html(job_text))

        if slack_ts:
            suggestion = suggest_resume_points(job.get("title", ""))
            if suggestion:
                send_slack_bot_message(format_resume_suggestion_message(suggestion), thread_ts=slack_ts)

        time.sleep(0.5)  # 텔레그램/슬랙 rate limit 대비

    updated_seen = seen_list + [{"id": job_id, "date": now_iso} for job_id in all_jobs]
    save_seen(updated_seen)
    print(f"신규 공고 {len(new_jobs)}건 알림 전송 완료")


if __name__ == "__main__":
    main()
