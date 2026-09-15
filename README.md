# 채용 공고 자동 알림 봇

관심 채용 사이트(사람인/원티드 등)에서 새 공고가 올라오면 텔레그램·슬랙으로 알림을 보내주고, 채용 사이트가 메일로 보내주는 "맞춤 공고 추천"도 자동으로 전달받는 봇입니다. GitHub Actions에서 3시간마다 자동 실행되며, 개인 컴퓨터를 켜둘 필요가 없습니다.

이 저장소는 **템플릿**입니다. 자기소개서를 대신 써주지는 않지만, 새 공고 알림 밑에 본인 이력서 키워드와 매칭되는 강조 포인트를 슬랙 댓글로 자동 제안하는 기능이 포함돼 있습니다 (AI 없이 키워드 매칭 방식).

## 이 저장소로 시작하기

1. 오른쪽 위 **"Use this template"** 버튼(또는 이 저장소를 Fork)으로 본인 계정에 복사본을 만드세요. **Private**로 만드는 걸 추천합니다.
2. 아래 순서대로 설정을 채웁니다.
3. `job_alert.py` 상단의 `SARAMIN_KEYWORDS`, `WANTED_KEYWORDS`, `GOOGLE_CSE_QUERY_KEYWORDS`를 본인이 찾는 직무 키워드로 바꾸세요.
4. `RESUME_PROFILE` (역시 `job_alert.py` 안에 있음)을 본인의 실제 경력·기술 스택으로 채우세요. 예시 그대로 두면 의미 없는 제안이 달립니다.

## 1. 텔레그램 봇 만들기 (2분)

1. 텔레그램 앱에서 `@BotFather` 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름, 아이디(반드시 `bot`으로 끝나야 함) 입력
3. 완료되면 **봇 토큰**(`123456:ABC-DEF...` 형태)을 줍니다. 복사해두세요.
4. 텔레그램에서 방금 만든 내 봇을 검색해 아무 메시지나 하나 보내세요 (`/start` 등). **이 단계를 꼭 해야** 봇이 나에게 메시지를 보낼 수 있습니다.

### chat_id 확인하기

브라우저에서 아래 주소를 열어보세요 (`<TOKEN>`은 위에서 받은 토큰으로 교체):

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

응답 JSON 안에서 `"chat":{"id":123456789, ...}` 부분의 숫자가 여러분의 **chat_id**입니다.

## 2. 사람인 Open API 키 신청 (승인까지 시간이 걸릴 수 있음, 선택)

1. https://oapi.saramin.co.kr 접속 → 회원가입/로그인
2. **API 이용신청** (`/join`) 메뉴에서 신청서 작성 → 승인 대기
3. 승인 후 로그인 → **Application > 앱 등록**에서 앱 생성 → **내 앱리스트**에서 **access-key** 확인

승인 전(또는 신청 안 함)이어도 `job_alert.py`에 포함된 **검색결과 페이지 조회 방식**(`ENABLE_SARAMIN_SCRAPE = True`)이 자동으로 대체 역할을 합니다. 사람인 `robots.txt`상 이 경로는 크롤링 금지 대상이 아니지만, 공식 API가 아니라서 언제든 막힐 수 있습니다 — 안 되면 그 부분만 조용히 결과가 0건이 됩니다.

## 3. (선택) 슬랙 연동

### 3-1. 알림만 받고 싶다면 — 웹훅

Slack 워크스페이스에서 Incoming Webhook을 하나 만들어 URL을 받아두세요.

### 3-2. 이력서 제안을 "댓글(스레드 답글)"로 받고 싶다면 — 봇 토큰

웹훅으로는 스레드 댓글을 달 수 없어서, 봇 토큰이 필요합니다.

1. https://api.slack.com/apps → **Create New App** → **Blank app** (또는 "From scratch")
2. 앱 이름 정하고 워크스페이스 선택
3. 왼쪽 메뉴 **OAuth & Permissions** → **Bot Token Scopes** → **Add an OAuth Scope** → `chat:write` 추가
4. 같은 페이지에서 **Install to Workspace** 클릭 → 권한 승인
5. 발급된 **Bot User OAuth Token** (`xoxb-...`) 복사
6. 알림 받을 슬랙 채널에서 `/invite @만든봇이름` 으로 봇 초대
7. 그 채널 우클릭 → "채널 세부정보 보기" → **채널 ID**(`C`로 시작) 복사

## 4. 이메일로 오는 맞춤 공고 알림 받기 (선택)

사람인/잡코리아/인크루트 등은 가입 시 "맞춤 공고 추천" 메일을 보내주는 경우가 많습니다. 이 메일을 IMAP으로 읽어서 텔레그램/슬랙으로 전달할 수 있습니다.

- `naver_mail_alert.py` — 네이버메일로 오는 알림용. 네이버 계정에서 2단계 인증 활성화 후 **애플리케이션 비밀번호** 발급
- `gmail_wanted_alert.py` — Gmail로 오는 알림용. Google 계정에서 2단계 인증 활성화 후 **앱 비밀번호** 발급

어떤 사이트가 어떤 메일함으로 알림을 보내는지 확인해서, 각 스크립트 상단의 `ALERT_SENDERS` 리스트를 본인 상황에 맞게 수정하세요.

## 5. GitHub 저장소 Secrets 등록

저장소의 **Settings → Secrets and variables → Actions → New repository secret** 에서 필요한 것만 등록하세요.

| Secret 이름 | 필수 여부 | 값 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 필수 | 1단계 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 필수 | 1단계 chat_id |
| `SARAMIN_ACCESS_KEY` | 선택 | 2단계 access-key (없어도 스크래핑으로 대체 동작) |
| `SLACK_WEBHOOK_URL` | 선택 | 3-1단계 웹훅 URL |
| `SLACK_BOT_TOKEN` | 선택 | 3-2단계 봇 토큰 (이력서 제안 댓글 기능에 필요) |
| `SLACK_CHANNEL_ID` | 선택(봇 토큰과 같이) | 3-2단계 채널 ID |
| `NAVER_EMAIL_ID` / `NAVER_APP_PASSWORD` | 선택 | 4단계 네이버메일 계정/앱 비밀번호 |
| `GMAIL_EMAIL_ADDR` / `GMAIL_APP_PASSWORD` | 선택 | 4단계 Gmail 계정/앱 비밀번호 |
| `GOOGLE_CSE_API_KEY` / `GOOGLE_CSE_ID` | 선택 | 아래 6단계 참고 |

## 6. (선택) 구글 커스텀 검색으로 다른 사이트 커버하기

사람인/원티드 외 사이트(잡코리아, 인크루트 등)는 개인 개발자에게 API를 잘 안 열어줍니다. 대신 구글 커스텀 검색으로 각 사이트의 개별 공고 링크를 찾아내는 기능이 포함돼 있습니다 (`ENABLE_GOOGLE_CSE`).

1. https://programmablesearchengine.google.com/controlpanel/create 접속
2. "검색할 사이트"에 원하는 채용 사이트 도메인들을 등록 (`job_alert.py`의 `GOOGLE_CSE_SITES`와 맞추기)
3. 검색엔진 ID(cx) 복사 → `GOOGLE_CSE_ID`
4. Google Cloud Console에서 Custom Search API를 사용 설정하고 API 키 발급 → `GOOGLE_CSE_API_KEY`

> 참고: 구글이 신규 프로젝트의 Custom Search API 발급을 제한하는 경우가 있습니다. 막혀 있다면 이 기능은 건너뛰고 다른 방식으로 대체하세요. 키가 없어도 스크립트는 에러 없이 이 부분만 건너뜁니다.

## 7. 실행 확인

- **Actions** 탭 → `Job Alert to Telegram` 워크플로 → **Run workflow** 버튼으로 수동 실행해서 알림이 오는지 확인
- 첫 실행은 지금 있는 공고들을 "기준선"으로만 저장하고, "초기 설정 완료" 메시지 하나만 옵니다. **두 번째 실행부터** 새 공고만 알림이 옵니다
- 이후엔 3시간마다 자동 실행됩니다 (`.github/workflows/job-alert.yml`의 cron 값으로 주기 조정 가능)
- 워크플로를 수동으로 자주 재실행하면 상태 파일 커밋이 서로 충돌할 수 있어, `concurrency` 설정으로 한 번에 하나만 돌도록 막아뒀습니다

## 참고 / 한계

- 원티드 검색(`search_wanted`)과 사람인 스크래핑(`search_saramin_scrape`)은 **비공식 방식**입니다. 사이트가 언제든 막거나 바꿀 수 있고, 막히면 그 부분만 조용히 결과가 0건이 됩니다 (전체 스크립트가 죽지 않음)
- 각 사이트의 `robots.txt`를 직접 확인하고, 크롤링이 금지된 경로(대개 상세페이지)는 건드리지 않는 선에서 사용하세요
- 이력서/자소서 원문 같은 개인정보는 이 저장소를 공개(Public)로 전환하기 전에 반드시 확인하세요. `RESUME_PROFILE`에는 요약된 강조 포인트만 넣는 걸 권장합니다
- 자소서 문항 자체를 써주는 기능은 없습니다 — 그건 Claude 대화창에 공고 링크를 붙여넣어 별도로 요청하세요
