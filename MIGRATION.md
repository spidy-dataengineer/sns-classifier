# MIGRATION — SNS 분류기 새 PC 이전 순서

이 zip 하나가 전부임. 압축 풀고 §2 부터 순서대로 진행.
읽는 대상: 사람 또는 다른 Claude session.

- 기술 함정 상세: `image_classifier/RUNBOOK.md` §5
- 전체 구조·설계 의도: `image_classifier/PROJECT_SUMMARY.md`
- 작성 2026-09-21 / 원본 PC: Windows 11, Python 3.13.5

---

## 0. 무엇을 하는 물건인가

Instagram·LinkedIn 저장 게시물 자동 수집 → 이미지 OCR → Claude 로 분류·요약 → Notion 적재.

**전부 local 실행.** 어디 cloud 에서 도는 게 아님. Windows Task Scheduler 가 매일 02:00 `run_daily.bat` 실행.
바깥으로 나가는 통신은 셋뿐 — Instagram/LinkedIn 수집, Notion API 적재, 분류 단계에서 `claude -p` 가 게시물 text 를 Anthropic 에 전송.

크롬 창이 실제로 떠야 하는 구조라 **02:00 에 PC 켜져 있고 화면 잠금 해제**돼 있어야 그날 실행됨.

---

## 1. zip 내용

| 들어있음 | 설명 |
|---|---|
| `image_classifier/src/` | 파이썬 17개. pipeline 전체 |
| `image_classifier/data/` | `posts.json` 외 핵심 7개, 14MB |
| `image_classifier/.env` | **secret 포함** (NOTION_TOKEN 등). 전송 경로 주의 |
| 문서 3개 | RUNBOOK.md / PROJECT_SUMMARY.md / CLASSIFICATION_REVIEW.md |
| `run_daily.bat` | 매일 실행 진입점. CRLF 로 저장돼 있음 |

| 의도적으로 뺌 | 이유 |
|---|---|
| `data/images/` (3,462장 513MB) | OCR 결과는 `posts.json` 안에 이미 있음. Notion 적재는 `post_url` 만 씀. 필요하면 원본 PC 에서 직접 복사 |
| `.pw-profile/` | Instagram·LinkedIn login session. 기기 바뀌면 보안확인 떠서 새로 login 하는 게 안전 (§5) |
| `data/*.bak*.json`, `data/.tmp/`, `logs/`, `__pycache__/` | 불필요 |

images 없이 시작해도 손실은 아직 OCR 안 된 36건뿐. 새로 받는 이미지는 정상 처리됨.

---

## 2. 새 PC 사전 설치

| 항목 | 비고 |
|---|---|
| Python 3.13.x | 3.13.5 에서 검증됨 |
| Google Chrome | 필수. Playwright 가 `channel="chrome"` 로 system Chrome 재사용 |
| Node.js + npm | 다음 단계 claude CLI 설치용 |

---

## 3. 폴더 배치 + 가상환경

**`.venv` 는 `image_classifier` 의 부모 폴더에 만듦.** `run_daily.bat` 이 `..\.venv\Scripts\python.exe` 를 8군데서 부름.
zip 구조가 이미 그 모양이니 **푼 자리 그대로 두고** 그 위치에서 venv 생성.

```
SNS_CLASSIFIER\            <- 여기서 python -m venv .venv
├── MIGRATION.md           (이 파일)
└── image_classifier\
    ├── src\  data\  run_daily.bat  .env  ...
```

```powershell
cd <원하는경로>\SNS_CLASSIFIER
python -m venv .venv
.\.venv\Scripts\pip install -r image_classifier\requirements.txt
```

첫 OCR 실행 때 PaddleOCR model 수백 MB 자동 download. 인터넷 필요.

---

## 4. Claude Code CLI — 새 계정 login (사람이 1회)

```powershell
npm i -g @anthropic-ai/claude-code
cd <경로>\SNS_CLASSIFIER\image_classifier
claude
```

이 폴더 안에서 직접 실행해 **login → folder 신뢰 승인 → 온보딩 끝까지** 넘긴 뒤 종료.

왜 필요: 분류 단계가 `claude -p` 를 subprocess 로 부름 (`src/run_classify.py:75`).
무인 실행 중 login 창이나 신뢰 확인 창이 뜨면 20분 timeout 후 강제 종료 → **그날 분류 전량 실패**.

API key 안 씀. 구독 계정 사용량에서 차감. repo 안에 Claude 계정 정보 없음 — CLI login 이 전부임.

---

## 5. Instagram / LinkedIn login (사람이 1회)

크롬 창이 뜨면 직접 login. 코드가 비밀번호 안 다룸. session 은 `.pw-profile/` 에 저장돼 다음부터 자동.

```powershell
cd <경로>\SNS_CLASSIFIER\image_classifier
..\.venv\Scripts\python.exe -u src\ig_saved_api.py --incremental
..\.venv\Scripts\python.exe -u src\collect_playwright.py --platform linkedin --incremental
```

기기가 바뀌어서 보안확인(코드 입력) 뜰 수 있음. 정상.

---

## 6. Notion

`.env` 의 NOTION_TOKEN 그대로, `data/notion_map.json` 의 database id 18개 그대로 유효.
**같은 Notion 계정이면 할 일 없음.**

계정이 바뀌는 경우에만: database 새로 만들고 `notion_map.json` 의 `database_id` 교체 + 각 database 에서 `···` → Connect to → integration 연결.

---

## 7. 매일 자동 실행 등록

원본 PC 설정: 매일 02:00, 로그인 상태에서만 실행(InteractiveToken).

```powershell
schtasks /create /tn SavedPostsDaily /sc daily /st 02:00 /f /tr "<경로>\SNS_CLASSIFIER\image_classifier\run_daily.bat"
```

### 7-1. 놓친 실행 따라잡기 (새 PC 에서 꼭 할 것)

원본 PC 는 아래 3개가 전부 꺼져 있어서 **02:00 에 PC 가 꺼져 있으면 그날치를 영영 건너뜀**. 새 PC 에서는 켜둘 것.

```powershell
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
$s.DisallowStartIfOnBatteries = $false
$s.StopIfGoingOnBatteries = $false
Set-ScheduledTask -TaskName SavedPostsDaily -Settings $s
```

| 설정 | 효과 |
|---|---|
| `StartWhenAvailable` | 02:00 놓쳤으면 PC 켜진 직후 자동 실행. 핵심 |
| `WakeToRun` | 절전 상태면 깨워서 실행. 완전히 꺼진 PC 는 못 깨움 |
| `DisallowStartIfOnBatteries = false` | 노트북 배터리 상태에서도 실행 |

확인: `Get-ScheduledTask SavedPostsDaily | Select -Expand Settings`

---

## 8. 검증

```powershell
cd <경로>\SNS_CLASSIFIER\image_classifier
..\.venv\Scripts\python.exe -u src\check_daily.py                  # 데이터 상태 요약
..\.venv\Scripts\python.exe -u src\notion_sync_all.py --dry-run    # Notion 연결·권한 확인, 실제 안 씀
.\run_daily.bat                                                    # 전체 1회 수동 실행
```

`logs\daily.log` 에 `[1]`~`[8]` 전부 찍히면 성공.
새 게시물 없으면 분류 단계가 `분류할 새 게시물 없음 → 스킵` 을 찍는 게 정상.

---

## 9. 지킬 것

1. **`run_daily.bat` 편집 시 CRLF 유지** — LF 로 저장되면 Task Scheduler 가 실행 못 함. 되돌리려면 `sed -i 's/$/\r/' run_daily.bat`.
2. **`data/posts.json` 이 원장** — 전체 기록 + 중복 수집 방지 둘 다 담당. 덮어쓰기·재생성 금지. 손상되면 전량 재수집·재분류.
3. **PaddleOCR `enable_mkldnn=False` 제거 금지** — paddle 3.x oneDNN 버그 우회. 코드에 이미 반영. RUNBOOK §5-1.
4. **`.env` 에 secret 있음** — zip 전송 경로 주의.
5. **분류는 API key 가 아니라 CLI login 으로 됨** — `.env` 의 GEMINI/ANTHROPIC key 는 구버전 경로 잔재라 지금 pipeline 은 안 씀.

---

## 10. 이전 시점 데이터 상태 (2026-09-21)

| 항목 | 값 |
|---|---|
| `posts.json` | 1,337건 (Instagram 1,212 / LinkedIn 125) |
| category | 1,337건 전부 부여됨. 미분류 0 |
| `ocr_text` 없음 | 93건 (그중 36건은 이미지가 있었으나 미처리) |
| `instagram_posts.json` | 1,210건 (수집 ledger) |
| `notion_map.json` | category 18개 ↔ Notion database |
| 마지막 자동 실행 | 2026-09-21 02:00 |

새 PC 첫 실행은 이 숫자에서 이어짐. 이미 있는 건 다시 안 받음.

---

## 11. cloud 에서 돌릴 수 있나 — 조건부 가능, 핵심 1개 미검증 (2026-09-21 재검토)

이전 결론은 "불가" 였음. 수집기를 쿠키 방식으로 바꾸면서 전제가 바뀌어 다시 확인했음.

**바뀐 전제:** 수집에 브라우저·`.pw-profile` 이 더 이상 필요 없음. `src/collect_cookie.py` 가
두 내부 API 를 HTTPS 로 직접 호출함. 하루 요청 2~4회.

| 단계 | cloud | 근거 |
|---|---|---|
| 수집 (Instagram / LinkedIn) | 설정하면 가능 | 브라우저 불필요해짐. cloud 기본 network 등급은 패키지 저장소만 허용하지만, Custom network access 로 두 도메인을 열 수 있음 |
| OCR | 가능 | |
| 분류 | 가능 | cloud session 자체가 Claude |
| Notion 적재 | 가능 | REST API. network 허용만 필요 |
| 데이터 보존 | 제약 그대로 | cloud VM 은 쓰고 버림. `posts.json` 을 repo 에 commit 해야 이어짐. 이미지 829MB 는 git 에 부적합 |

**필요한 준비 3가지**

1. repo — cloud 는 GitHub 연결 repo 이거나, commit 최소 1개 있는 로컬 repo 의 bundle 업로드
2. network — 환경 편집기에서 Network access = Custom, allowed domains 에
   `www.instagram.com` / `www.linkedin.com` 한 줄씩
3. 쿠키 — `IG_COOKIE` / `LI_COOKIE` 를 환경변수로. 이게 유일한 방법임

**아직 확인 못 한 것 (여기가 갈림길)**

cloud 데이터센터 IP 에서 이 쿠키가 실제로 통하는지 **아직 시험 못 했음.** 문서는 network 를
열 수 있다고만 말하지, Instagram 이 그 IP 를 받아줄지는 말해주지 않음. 집에서 쓰던 session 이
갑자기 해외 데이터센터 IP 에서 쓰이면 계정 탈취와 구분이 안 되는 형태임 — 이건 추론이고
실측 아님. 2026-09-21 에 이미 자동화 경고를 한 번 맞은 계정이라 더 조심할 이유가 있음.

**받아들여야 하는 것**

- 환경변수 값은 그 환경에 접근 가능한 사람이 그대로 읽을 수 있음
- 모든 통신이 Anthropic 보안 proxy 를 거치고, 요청한 호스트의 DNS 기록이 남음

**대안 — 집에 상시 켜두는 기기 (권장도 높음)**

라즈베리파이·NAS·미니PC 에 이 폴더를 올림. 브라우저가 필요 없어졌으므로 GUI 없는 기기로 충분함.
집 IP 가 그대로라 위의 미검증 위험이 아예 없음. "PC 가 꺼져 있을까봐" 라는 원래 목적도 그대로 해결됨.

PC 를 계속 쓸 경우의 보완책:

1. §7-1 설정 — 놓친 실행을 PC 켜질 때 따라잡음
2. 실행 시각을 사람이 PC 를 쓰는 시간대로 옮김 (02:00 → 21:00 등)
