#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gmail로 오는 원티드 추천 공고 메일을 읽어서 텔레그램/슬랙으로 전달.
(사람인/인크루트/잡코리아는 네이버메일로 오므로 naver_mail_alert.py가 담당하고,
 원티드만 Gmail로 오기 때문에 이 스크립트를 따로 둠. 로직은 naver_mail_alert.py와 동일하고
 IMAP 서버와 발신자 목록만 다름.)

필요한 환경변수 (GitHub Actions Secrets 로 주입):
  GMAIL_EMAIL_ADDR     - Gmail 전체 주소 (예: myaccount@gmail.com)
  GMAIL_APP_PASSWORD   - 2단계 인증 후 발급받은 앱 비밀번호 (일반 로그인 비밀번호 아님)
  TELEGRAM_BOT_TOKEN   - job_alert.py 와 동일
  TELEGRAM_CHAT_ID     - job_alert.py 와 동일
  SLACK_WEBHOOK_URL    - job_alert.py 와 동일 (선택)

채용 알림을 보내는 발신자 목록은 아래 ALERT_SENDERS 에서 관리 (이메일 주소 일부만 포함되면 매칭).
"""

import email
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from html import unescape
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

SEEN_FILE = Path(__file__).parent / "seen_gmail_mail.json"
SEEN_RETENTION_DAYS = 60
LOOKBACK_DAYS = 3          # 최근 며칠치 메일을 확인할지 (누락 방지용으로 실행 주기보다 넉넉하게)
MAX_LINKS_PER_MAIL = 15    # 메일 한 통에서 뽑아낼 최대 링크 수

# 채용 알림을 보내는 발신자 (이메일 주소에 이 문자열이 포함되면 매칭)
ALERT_SENDERS = [
    "mail.wantedlab.com",  # 원티드 추천 공고 메일
]

GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993

GMAIL_EMAIL_ADDR = os.environ.get("GMAIL_EMAIL_ADDR", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

LINK_PATTERN = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


# ---------------------------------------------------------------------------
# 상태 파일
# ---------------------------------------------------------------------------

def load_seen():
    if not SEEN_FILE.exists():
        return {"seen": [], "initialized": False}
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen": [], "initialized": False}
    data.setdefault("seen", [])
    data.setdefault("initialized", bool(data["seen"]))
    return data


def save_seen(seen_list, initialized=True):
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)
    pruned = [item for item in seen_list if _safe_parse_date(item.get("date")) >= cutoff]
    SEEN_FILE.write_text(
        json.dumps(
            {"seen": pruned, "initialized": initialized, "updated_at": datetime.now(timezone.utc).isoformat()},
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
# 메일 처리
# ---------------------------------------------------------------------------

def decode_mime_words(s):
    if not s:
        return ""
    parts = decode_header(s)
    decoded = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            decoded += text.decode(enc or "utf-8", errors="ignore")
        else:
            decoded += text
    return decoded


def extract_links_from_html(html_content):
    links = LINK_PATTERN.findall(html_content)
    seen = set()
    unique_links = []
    for link in links:
        clean = unescape(link)
        if clean not in seen:
            seen.add(clean)
            unique_links.append(clean)
    return unique_links


def get_message_body_links_and_text(msg):
    html_parts = []
    text_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
            except Exception:  # noqa: BLE001
                continue
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="ignore")
            except (LookupError, UnicodeDecodeError):
                decoded = payload.decode("utf-8", errors="ignore")
            if ctype == "text/html":
                html_parts.append(decoded)
            elif ctype == "text/plain":
                text_parts.append(decoded)
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="ignore") if payload else ""
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                text_parts.append(decoded)
        except Exception:  # noqa: BLE001
            pass

    links = []
    for html_content in html_parts:
        links.extend(extract_links_from_html(html_content))

    return links, "\n".join(text_parts)


def is_from_alert_sender(from_header):
    from_lower = (from_header or "").lower()
    return any(sender in from_lower for sender in ALERT_SENDERS)


# ---------------------------------------------------------------------------
# 텔레그램 / 슬랙
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


def send_slack_message(text):
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL 이 없어 전송을 건너뜁니다.", file=sys.stderr)
        return False
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[slack] 메시지 전송 실패: {e}", file=sys.stderr)
        return False


def notify(text):
    send_telegram_message(text)
    send_slack_message(text.replace("<b>", "*").replace("</b>", "*"))


def format_mail_message(subject, sender, links):
    lines = [f"📧 <b>원티드 추천 공고 메일 도착</b>", f"제목: {subject}", f"발신: {sender}", ""]
    if links:
        lines.append("링크:")
        for link in links[:MAX_LINKS_PER_MAIL]:
            lines.append(link)
    else:
        lines.append("(메일 안에서 링크를 찾지 못했어요. 메일함에서 직접 확인해주세요.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    if not GMAIL_EMAIL_ADDR or not GMAIL_APP_PASSWORD:
        print("[gmail] GMAIL_EMAIL_ADDR 또는 GMAIL_APP_PASSWORD 가 없어 건너뜁니다.", file=sys.stderr)
        return

    state = load_seen()
    seen_list = state.get("seen", [])
    seen_ids = {item["id"] for item in seen_list}
    is_first_run = not state.get("initialized", False)

    try:
        imap = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        imap.login(GMAIL_EMAIL_ADDR, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
    except Exception as e:  # noqa: BLE001
        print(f"[gmail] IMAP 로그인/연결 실패: {e}", file=sys.stderr)
        return

    since_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")

    new_mails = []  # (uid, subject, sender, links)

    try:
        status, data = imap.search(None, f'(SINCE {since_date})')
        if status != "OK":
            print(f"[gmail] 메일 검색 실패: {status}", file=sys.stderr)
            imap.logout()
            return

        mail_ids = data[0].split()

        for mail_id in mail_ids:
            status, msg_data = imap.fetch(mail_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_header = decode_mime_words(msg.get("From", ""))
            if not is_from_alert_sender(from_header):
                continue

            message_id = msg.get("Message-ID", "").strip() or f"uid:{mail_id.decode()}"
            if message_id in seen_ids:
                continue

            subject = decode_mime_words(msg.get("Subject", "(제목 없음)"))
            links, _ = get_message_body_links_and_text(msg)

            new_mails.append({"id": message_id, "subject": subject, "sender": from_header, "links": links})
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass

    now_iso = datetime.now(timezone.utc).isoformat()

    if is_first_run:
        updated_seen = seen_list + [{"id": m["id"], "date": now_iso} for m in new_mails]
        save_seen(updated_seen)
        print(f"[gmail] 첫 실행: {len(new_mails)}건 기준선 저장, 알림 없음")
        return

    if not new_mails:
        print("[gmail] 새 원티드 추천 메일 없음")
        save_seen(seen_list)
        return

    notify(f"📬 새 원티드 추천 메일 {len(new_mails)}건 도착 (Gmail)")
    for m in new_mails:
        notify(format_mail_message(m["subject"], m["sender"], m["links"]))
        time.sleep(0.5)

    updated_seen = seen_list + [{"id": m["id"], "date": now_iso} for m in new_mails]
    save_seen(updated_seen)
    print(f"[gmail] 신규 메일 {len(new_mails)}건 알림 전송 완료")


if __name__ == "__main__":
    main()
