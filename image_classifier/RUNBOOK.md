# RUNBOOK — image_classifier 재현 지침

> **이 문서의 목적**: 이 프로젝트를 **다른 PC로 옮기거나, 다른 Claude 세션이 이어받아도 동일하게 동작**하도록 만드는 단일 기준 문서.
> 사람이 읽어도, 새 Claude 세션이 읽어도 그대로 재현 가능해야 한다.
>
> ⚠️ 기존 `README.md` 는 **구버전 흐름**(Gemini/Claude API 로 이미지 분류)을 설명한다. 현재 방향은 아래대로 **로컬 무료 추출 → 구독제 Claude 세션 분류 → Notion 적재** 이며, 충돌 시 **이 RUNBOOK 이 우선**한다.

---

## 0. 무엇을 하는 프로젝트인가

LinkedIn / Instagram 의 **내가 저장한 게시물**을 자동 수집해서, 모든 텍스트(메타데이터 + 본문 + 이미지 OCR + 영상 자막)를 뽑아 하나의 리치 저장소로 모은 뒤, **AI가 분류·정리**해서 최종적으로 **Notion DB** 에 올린다.

3단계로 나뉜다:

| Phase | 하는 일 | 도구 | 비용 |
|---|---|---|---|
| **1. 추출** | 저장글 수집 → 메타+본문+이미지+OCR 을 `posts.json` 으로 적재 | Playwright + PaddleOCR (로컬) | 무료 |
| **2. 분류** | `posts.json` 을 읽어 각 글에 `category` + `tech_tags` 부여 | **구독제 Claude 세션(=이 대화)** | 구독제 |
| **3. 적재** | 분류 결과를 Notion 데이터베이스에 생성/업데이트 | Notion MCP | 무료 |

**설계 원칙**: 유료 LLM API 는 쓰지 않는다. 추출은 로컬 무료 도구, 분류는 구독제 세션, Notion 연동은 무료 MCP.

---

## 1. 환경 요구사항

- **OS**: Windows 11 (개발·검증 환경). 다른 OS 는 PaddleOCR / Playwright 동작을 재확인해야 함.
- **Python**: 3.13.5
- **Google Chrome**: **최초 로그인 1회에만** 필요. 수집은 쿠키로 HTTP 요청만 하므로 평소엔 크롬을 띄우지 않음. 로그인 때만 Playwright 가 `channel="chrome"` 로 시스템 크롬을 재사용 — chromium 별도 다운로드 안 함.
- **가상환경 위치**: `RAG_AI_AGENT/.venv` (즉 `image_classifier` 의 **상위 폴더**). 프로젝트 명령은 `../.venv/Scripts/python.exe` 로 이 파이썬을 사용한다.

---

## 2. 새 PC 설치 순서

```powershell
# (1) RAG_AI_AGENT 폴더를 새 PC로 복사 (아래 §4 '옮길 때 함께' 참고)
cd RAG_AI_AGENT

# (2) 가상환경 생성 + 활성화
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# (3) 의존성 설치
pip install -r image_classifier\requirements.txt

# (4) Playwright: 최초 로그인 1회에만 쓰며 시스템 Chrome 사용(channel="chrome") → 별도 설치 불필요.
#     쿠키를 IG_COOKIE / LI_COOKIE 환경변수로 직접 넣으면 Playwright 자체가 필요 없음.
```

> PaddleOCR 모델 파일(수백 MB)은 `ocr_images.py` **첫 실행 시 인터넷으로 자동 다운로드**된다. 첫 실행은 네트워크 필요.

---

## 3. 실행 순서 (Phase 별)

### Phase 1 — 추출 (로컬, 무료)

```powershell
cd image_classifier

# 1) LinkedIn 저장글 수집 (쿠키 HTTP 요청, 크롬 안 띄움)
#    최초 1회만 로그인 필요: src\collect_playwright.py --platform linkedin 을 한 번 돌리면
#    열리는 크롬 창에서 직접 로그인하고 세션이 .pw-profile/ 에 남는다. 코드는 비밀번호를 다루지 않음.
#    이후부터는 아래 한 줄이 .pw-profile 에서 쿠키만 읽어 쓴다. 쿠키 수명 약 1년.
..\.venv\Scripts\python.exe src\collect_cookie.py --platform linkedin
#    → data\linkedin_posts.json (게시물별 메타+본문+이미지URL)

# 2) 이미지 다운로드 + 저장소 생성
..\.venv\Scripts\python.exe src\download_posts.py data\linkedin_posts.json
#    → data\images\ (이미지), data\posts_index.csv (이미지↔본문), data\posts.json (리치 저장소)

# 3) 이미지 OCR (로컬 PaddleOCR, 한국어+영어)
..\.venv\Scripts\python.exe -u src\ocr_images.py
#    → data\posts.json 의 각 레코드에 ocr_text 추가 (이어하기 지원)

# 4) Instagram 저장글 (쿠키 수집 → 다운로드 → OCR)
..\.venv\Scripts\python.exe src\collect_cookie.py --platform instagram
..\.venv\Scripts\python.exe src\download_posts.py data\instagram_posts.json --referer https://www.instagram.com/ --prefix ig --merge
..\.venv\Scripts\python.exe -u src\ocr_images.py
```

### 2차 이후 — 증분 실행 (ID 원장, 비파괴)

이미 처리한 것은 건너뛰고 **새로 저장한 것만** 처리. `posts.json`(원장)의 stable ID 로 판별.

**가장 간단(원클릭)**: `run_daily.bat` 더블클릭 (수집부터 Notion 반영까지 전 단계). 수집만 하려면 `src\collect_cookie.py` 를 `--platform both --incremental` 로 돌린다.

개별 명령으로 하려면:

```powershell
# LinkedIn 새 저장글만
..\.venv\Scripts\python.exe src\collect_cookie.py --platform linkedin --incremental
..\.venv\Scripts\python.exe src\download_posts.py data\linkedin_posts.json --append
..\.venv\Scripts\python.exe -u src\ocr_images.py

# Instagram 새 저장글만
..\.venv\Scripts\python.exe src\collect_cookie.py --platform instagram --incremental
..\.venv\Scripts\python.exe src\download_posts.py data\instagram_posts.json --referer https://www.instagram.com/ --prefix ig --append
..\.venv\Scripts\python.exe -u src\ocr_images.py
```
- `--incremental`: 저장목록 최신순이라 아는 항목 구간에 닿으면 **조기 종료**(전체 재스크롤 안 함)
- `--append`: 기존 `posts.json` 전체 유지 + **id 기준 신규만** 추가. 파일명도 ID 기반이라 충돌 없음
- 식별 키: LinkedIn `urn:li:activity:…`, IG `media_id` — 로직은 `pipeline_ids.py` 한 곳
- 분류(Phase 2)·Notion 은 `category` 없는 신규만 처리(업서트). **저장목록은 안 건드림(unsave X)**

**완전자동(분류까지, 구독제)**: `run_auto.bat` = Phase 1(python) + Phase 2 를 `claude -p` 로 **구독제 실행(API 키 불필요)**.
- 검증된 권한 조합: `claude -p "..." --allowedTools "Read Edit Write" --permission-mode acceptEdits` (headless 에서 파일수정 자동승인, 정상 동작 확인됨).
- `--dangerously-skip-permissions` 는 *에이전트가 스폰* 할 땐 안전장치에 막히지만 사용자가 직접 실행하면 됨 — 다만 `acceptEdits` 가 더 안전·검증됨.
- 구독제도 사용량 한도(5시간/주간)가 있으니 **소량(증분)에 최적**. 대용량 백로그는 대화형 세션이 안전.

**매일 자동(스케줄)**: `run_daily.bat`(증분 수집+다운+OCR → `claude -p` 분류+학습요약 → 릴스50 예정)을 Windows 작업 스케줄러로.
```powershell
schtasks /Create /TN "SavedPostsDaily" /TR "C:\Users\<사용자>\IdeaProjects\RAG_AI_AGENT\image_classifier\run_daily.bat" /SC DAILY /ST 09:00 /F
```
- 결과는 `logs\daily.log` 에 누적 → **며칠에 한 번 확인(반자동)**. 로그인 만료·체크포인트 뜨면 무인 실행은 그날 실패/대기하므로.
- 삭제 `schtasks /Delete /TN "SavedPostsDaily" /F` · 시간변경 `taskschd.msc`.

### Phase 2 — 분류 (구독제 Claude 세션)

새 Claude 세션에 **이 RUNBOOK 과 `posts.json`** 을 주고 아래를 요청한다:

> "RUNBOOK.md §9 taxonomy 대로 posts.json 의 각 레코드를 분류해서 `category` 와 `tech_tags` 를 채워줘."

세션이 지켜야 할 규칙 (재현성 핵심):
1. 입력 근거 = 각 레코드의 `text` + `ocr_text` + `hashtags` 를 **종합**해서 판단.
2. `category` = §9 의 플랫폼별 카테고리 중 **정확히 하나**.
3. `tech_tags` = 자유 태그 리스트 (기술스택/주제). 없으면 `[]`.
4. 결과는 각 레코드에 필드로 추가하고 `posts.json` 에 다시 기록 (원본 필드 보존).
5. 애매하면 `기타` 로 두고 사유를 남기지 말 것(불필요한 방어적 주석 금지).

> 이번에 실제로 쓴 방법(재현용): 컴팩트 입력 생성(`scratchpad/prep_phase2.py`) → Claude Code **Workflow** 로 8배치 병렬 분류(sonnet) → tech_tags 정규화 → 경계/누락 opus 재검증 → `scratchpad/merge_phase2.py` 로 `posts.json` 병합. Workflow 없이 세션이 직접 분류해도 결과는 동일해야 함.

### Phase 3 — Notion 적재 (REST API, 자동화)

**방식**: Notion **내부 integration 토큰 + REST API** 로 카테고리별 Notion DB 에 **증분 업서트**. MCP 는 대화형 OAuth 라 무인 스케줄에서 불안정(§5-9) → 자동화는 REST 로 한다. 스크립트: `src/notion_sync.py`.

준비(1회):
1. https://www.notion.so/my-integrations → **Internal** integration 생성 → **Internal Integration Secret**(`ntn_...`) 를 `.env` 에 `NOTION_TOKEN=` 로 저장(gitignore 됨). *OAuth 아님, access token.*
2. 적재할 상위 페이지(예: `language`)에서 `···` → **Connections** → 해당 integration 연결(하위 DB 까지 상속).
3. 카테고리별 대상 DB 를 만들고(스키마는 아래 §Phase 4 학습 컬럼 포함), `data/notion_map.json` 에 매핑:
   ```json
   {"영어공부": {"database_id": "<db id>"}, "중국어공부": {"database_id": "<db id>"}}
   ```

실행(파이프라인이 데이터를 뽑을 때마다 재실행 = 증분):
```powershell
..\.venv\Scripts\python.exe src\notion_sync.py --category 영어공부 --dry-run   # 계획만: 신규/갱신/자막추가/변경없음 건수
..\.venv\Scripts\python.exe src\notion_sync.py --category 영어공부 [--limit N]  # 실제 반영
```
- 유니크 키 = `Source URL`(post_url). **신규만 생성**, 기존은 **변경분만** 갱신(불필요한 재기록 없음), 전사 새로 도착 시 본문에 자막 블록 append(`자막있음` False→True 전이 시 1회).
- **학습 컬럼**(영어 `표현/뜻(KO)/예문(EN)/예문(KO)/Style`, 중국어 `한자/병음/뜻(KO)/예문` — Phase 4 정제분)은 sync 가 **건드리지 않음** → 정제 결과 보존.
- DB 스키마(유형 Select·학습 컬럼 등)는 없으면 스크립트가 자동 추가(`add_properties`).
- 카테고리별 빌더는 `BUILDERS` dict 에 등록(현재 `영어공부`·`중국어공부`). 새 카테고리는 `build_*` 함수 + 스키마 추가.

> 구버전/대안: `notion_export.py` → `notion_import.csv` 를 Notion 에 직접 Import, 또는 MCP 세션(§6)에 직접 요청. 결과는 유사하나 **멱등성·자동화는 REST 가 우위**.

### Phase 4 — 카테고리별 정제 (구독제 세션 / claude -p)

적재된 행을 카테고리 특성에 맞게 세분화(=학습 컬럼 채우기). Source URL 로 매칭, 이미 채워진 행은 스킵(멱등).
- **영어**(`영어 저장게시물` DB): OCR/자막을 읽어 `표현·뜻(KO)·예문(EN)·예문(KO)·Style(everyday/business/idiom/slang…)`.
- **중국어**(`중국어 저장게시물` DB): `한자(汉字)·병음(pīnyīn)·뜻(KO)·예문`. 한자는 주로 transcript, 병음은 summary 에서 추출.
- 추출은 LLM 판단 → 무인 자동화 시 `claude -p` 단계. 결과를 REST(`notion_sync` 확장)로 해당 행에 update.

#### Phase 3 상세 — 새 세션용 작업 지시문 (2026-07-08)

릴스 전사 러너가 도는 세션은 재시작 금지 → **새 터미널에서 `claude` 를 열어** 아래를 그대로 요청한다. (새 세션엔 Notion MCP 도구가 로드됨)

> image_classifier 프로젝트의 Notion 지식베이스를 만들어줘. 데이터는 `data/notion_import.json`(880행; 컬럼: 제목·platform·category·tech_tags·media_type·post_url·summary·본문·ocr_text·transcript·frame_text). **데이터 파일은 읽기만** 하고, 다른 세션이 돌리는 러너/브라우저는 건드리지 말 것.
>
> 1. Notion MCP 도구 로드 확인(안 잡히면 `claude mcp list` 로 등록 확인 후 알려줘).
> 2. 최상위 페이지 "📚 저장 콘텐츠 지식베이스" + 그 아래 DB 1개(880행). 속성: 제목(title), platform·category·media_type(Select), tech_tags(Multi-select), post_url(URL), summary(Text). **긴 전문(transcript·frame_text·ocr_text·본문)은 속성 말고 페이지 본문 블록**에 넣기(2000자 속성 제한 회피).
> 3. 카테고리별 뷰: 학습형(영어공부·중국어공부)=표뷰(summary+전문 중심), 수집형(맛집·운동·스포츠·주식·투자·기타)=갤러리뷰(post_url+summary 중심), LinkedIn 3종=표뷰.
> 4. 먼저 **카테고리별 3건씩만 샘플 업로드**해서 모양 확인받고, 승인 후 나머지 일괄(레이트리밋 ~3req/s 고려).
> 5. transcript/frame_text 는 아직 채워지는 중(러너 진행 중)이므로, 완주 후 `post_url` 기준 업서트로 재동기화할 것.

---

## 4. 다른 PC 로 옮길 때 (중요)

`.gitignore` 때문에 **git 으로는 안 따라오는** 것들. 폴더 복사 시 별도 취급:

| 항목 | 옮기나? | 처리 |
|---|---|---|
| `.py` 코드, `labels.json`, `RUNBOOK.md` | ✅ | 복사만 하면 그대로 동작 |
| 파이썬 패키지 | ❌ | 새 PC 에서 `pip install -r requirements.txt` |
| Google Chrome | 필요 | 새 PC 에 설치 |
| `.env` (API 키) | 🔒 비밀 | Phase 2 를 구독제로 하면 **불필요**. Gemini/Claude API 쓸 때만 수동 복사 |
| `.pw-profile/` (로그인 세션) | 🔒 **복사 비권장** | 새 PC 에서 **1회 재로그인**. 기기 바뀌면 IG/LinkedIn 보안확인 뜰 수 있어 새로 하는 게 안전 |
| `data/` 전체(`posts.json`, `*_posts.json`, `posts_index.csv`, `reel_transcripts.json`, `saved_map.json`, `images/`) | ❌ | 유지하려면 **`data/` 폴더째 수동 복사**, 아니면 새 PC 에서 재수집 |

> **폴더 구조**: 코드는 전부 `src/`, 산출물·데이터는 전부 `data/` (gitignore), 실행 진입점 `.bat` 3개는 루트, 구버전은 `legacy/`. 아래 파일 목록의 스크립트는 모두 `src/` 안에 있음(명령 예시의 `src\` 접두사 참고).

---

## 5. 반드시 지켜야 하는 기술적 함정 (hard-won)

이 목록이 "동일하게 동작" 의 핵심이다. 다른 세션이 놓치면 재현이 깨진다. **코드에 이미 반영돼 있으니 제거하지 말 것.**

1. **PaddleOCR — oneDNN 크래시 우회**: `enable_mkldnn=False` + 환경변수 `FLAGS_use_mkldnn=0` **필수**. paddle 3.x 의 oneDNN/PIR 런타임 버그(Windows/Py3.13) 로 안 하면 `ConvertPirAttribute2RuntimeAttribute` 에러. `ocr_images.py` 에 반영됨.
2. **콘솔 인코딩**: 모든 스크립트는 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`. 일회성 파이썬 명령은 `PYTHONIOENCODING=utf-8` 붙일 것. 안 하면 이모지/한글에서 cp949 크래시.
3. **백그라운드 실행은 `-u`**: 실시간 로그가 보이게(파이썬 출력 버퍼링 해제).
4. **Playwright 전용 프로필**: `launch_persistent_context(user_data_dir=".pw-profile", channel="chrome", headless=False)`. 로그인 세션이 프로필에 유지되어 다음 실행부터 자동.
5. **가상화 무한스크롤**: 저장 페이지는 DOM 노드를 재활용(virtualized) → 스크롤하며 **증분 수집**해야 함(`_merge`). 끝까지 스크롤 후 한 번에 긁으면 대부분 유실.
6. **LinkedIn CDN URL 만료**: `media.licdn.com` 이미지 URL 은 세션 만료가 있어 **수집 직후 같은 흐름에서** `download_posts.py` 실행(403 방지).
7. **Gemini 무료 한도(구버전 API 경로만 해당)**: `gemini-2.5-flash` = **하루 20 요청**. 무료 쿼터는 **모델별 개별 버킷**. `backends.py` 의 모델 폴백 체인이 한도 소진 시 다음 모델로 자동 전환. Phase 2 를 구독제로 하면 무관.
8. **셀렉터**: LinkedIn = `[data-chameleon-result-urn]`(카드). Instagram = `[style*="background-image"]` 의 scontent URL(그리드 썸네일). IG 그리드엔 permalink/캡션/alt 가 없음(모달 방식).
9. **MCP 도구 로딩**: `claude mcp add` 로 서버를 추가해도 **실행 중이던 Claude Code 세션엔 도구가 즉시 안 붙는다** → 세션을 재시작해야 `notion` 도구가 잡힘(`claude mcp list` 로 연결상태 확인 가능).
10. **Instagram 전체 훑기 금지 — 계정 자동화 경고**: 저장글 전량(약 950건, 50건씩 20페이지)을 짧은 간격으로 받으면 Instagram 이 자동화로 감지한다. 2026-09-21 에 두 번 훑다가 걸렸고, API 가 `{"message":"checkpoint_required","lock":true}` 로 막혔다. 브라우저를 열면 `instagram.com/accounts/scraping_warning/` 경고문("자동화된 행동으로 의심되는 활동... 영구 비활성화")이 떠 있고, **`닫기` 를 눌러야 API 가 풀린다**(눌렀더니 즉시 복구됨). 그래서 `collect_cookie.py` 의 Instagram 은 `--incremental` 유무와 무관하게 **항상 증분**이다(하루 1~2 요청). 이 경로를 되살리지 말 것. 원장에 전량이 이미 있다.
11. **LinkedIn 영상**: mp4 직링크 없음 → HLS/DASH(`dms.licdn.com/playlist/vid/...`). Playwright `page.on("response")` 로 매니페스트 URL 캡처 → imageio-ffmpeg 로 `-vn -ac 1 -ar 16000` WAV 추출 → faster-whisper(`language="ko"`, `vad_filter=False`) 전사. 일부 AAC(`TNS filter order>12`)는 번들 ffmpeg 가 깨끗이 못 풀 수 있음(무음/깨짐).

---

## 6. Notion MCP 연결 (Phase 3)

출처: developers.notion.com/guides/mcp · github.com/makenotion/notion-mcp-server · code.claude.com/docs/en/mcp

```powershell
# (1) MCP 서버 등록 (PowerShell)
claude mcp add --transport http notion https://mcp.notion.com/mcp
```

```
# (2) Claude Code 세션에서 OAuth 로그인
/mcp
#  목록에서 notion 선택 → 브라우저 OAuth 승인("Allow") → 완료. Bearer 토큰 불필요.
```

**(3) Notion 측 권한** — 연동할 DB/페이지에서 `···` → **"Connect to"** → 방금 승인한 integration 선택. (이 단계 없으면 MCP 가 그 DB 를 읽고/쓸 수 없음.)

**(4) 확인**: `/mcp` 에서 notion 이 `connected` 이면 완료.

---

## 7. 현재 진행 상태 & 남은 작업  (최종 업데이트: 2026-07-06)

- **LinkedIn Phase 1(추출)**: ✅ 완료 — 109 게시물, 이미지 88장, OCR 77건. `posts.json`.
  - media_type: image 66 / article 14 / text 21 / video 2 / document 6.
- **LinkedIn Phase 2(분류)**: ✅ 완료 — 109개 전부 `category`+`tech_tags` 부여(`posts.json`).
  - 카테고리 분포: 데이터엔지니어링 62 / 채용·이직·커리어 24 / AI·LLM·에이전트 14 / 기타 8 / 제품·도구홍보 1.
  - 상위 태그: Spark 33, Airflow 26, Kafka 16, Agent 13, ETL 13, Databricks 13, dbt 11, MCP 10, Snowflake 10.
- **Notion export**: ✅ `notion_export.py` → `notion_import.csv` / `notion_import.json`(109행, 분류 포함).
- **Notion 적재(Phase 3)**: ⏳ MCP 서버 등록·인증 완료(`claude mcp list` = ✔ Connected). 단 서버가 세션 도중 추가돼 **도구가 현재 세션에 로드 안 됨 → Claude Code 재시작 후** 적재(§5-9, §6).
- **Instagram Phase 1(추출)**: ⏳ 구현 완료(썸네일 방식), 실행/검증 중.
  - 구조: 저장 그리드 = `background-image` 썸네일 + `role=button` 모달. 그리드에 `/p/` permalink·캡션·alt 없음. 로그인 유저 = `instpjh`.
  - 방식: `INSTAGRAM_EXTRACT_JS` 로 scontent 배경이미지 수집(프로필사진 `t51.75761-19` 제외). 캡션/URL/고해상도는 후속(모달 클릭) 과제. **썸네일 해상도가 낮으면 OCR 품질 확인 필요**.
- **영상 자막**:
  - **LinkedIn 영상 2건** = 말(speech) 없음(1건 −91dB 무음, 1건 배경음/코덱깨짐) → 자막 없음, 본문 캡션으로 대체.
  - **IG 릴스 = 전사 됨(실증 완료)**: 저장 그리드에서 릴스 열기(첫 셀 클릭 → `ArrowRight` 로 넘김) → 네트워크에서 영상 스트림 URL + `/p/` permalink 포착 → imageio-ffmpeg 오디오 추출(`-user_agent`/`Referer` 헤더 필요) → faster-whisper(`ko`). **LinkedIn과 달리 코덱 문제 없고 말 내용 풍부**(한/중/영 정확 전사 확인).
  - 764 중 **581이 릴스**. 도구: `reel_transcribe.py`(페이싱: 랜덤지연·중간쉼·회당 --limit·챌린지시 중단) + `reel_merge.py`. `reel_transcripts.json` 에 shortcode 키로 누적(재개; 실패는 attempts 로 3회까지 재시도).
  - **✅ 오디오 캡처 해결**: 저장 그리드에서 릴스 열고(첫 셀 클릭 → `ArrowRight`) **muted 재생 + `currentTime` seek + 9초 dwell** 로 progressive(오디오 포함) 스트림을 네트워크에서 확보 → imageio-ffmpeg(`-user_agent`/`Referer`) → faster-whisper(ko). 검증 **6/6 성공**(한/중/영 정확). 각 후보 스트림을 오디오 있을 때까지 시도(vids≥2 면 성공). (IG API 응답본문 파싱 코드도 있으나 실제 URL은 네트워크 캡처가 잡음.)
  - **✅ `run_daily.bat [3]` 에 연결됨**: 매일 스케줄이 `reel_transcribe.py --limit 50` 실행 → 약 12일이면 581 릴스 소진.
  - **✅ 매칭 해결**: `ig_saved_api.py` 가 저장 그리드 API 응답에서 **shortcode(code)+media_id(pk)+caption+캐러셀 전 슬라이드+장소** 를 함께 확보 → shortcode 로 posts.json 과 매칭(`ig_enrich_posts.py`). 단 posts.json 의 기존 `id`(썸네일 파싱값)는 pk 와 불일치하므로 **조인 키는 shortcode**.
- **✅ IG 캡션/캐러셀 수집(Phase 1 보강)**: 과거엔 IG 본문·해시태그·2번째 이후 슬라이드가 전부 미수집(0)이었음 → `ig_saved_api.py`(API 응답 캡처)로 caption 758/771·장소 166·캐러셀 슬라이드 확보. `run_daily.bat [1b][1c]` 에 증분 연결. 이후 이 캡션으로 IG 재분류(§Phase 2, `reclass_*`) 완료.
- **taxonomy 조정 / RAG 검색 레이어**: 이후.

---

## 8. 파일 인벤토리

| 파일 | 분류 | 역할 |
|---|---|---|
| `collect_cookie.py` | 코드 | **현재 쓰는 수집기**. 쿠키만으로 두 플랫폼 저장글을 HTTP 로 받음(크롬 안 띄움). `--platform` / `--incremental`. 쿠키는 `IG_COOKIE`/`LI_COOKIE` 환경변수 우선, 없으면 `.pw-profile` |
| `collect_playwright.py` | 코드 | 옛 수집기(LinkedIn 카드 / IG 썸네일 DOM 스크롤). 지금은 **최초 로그인 세션 만들기**와 `enrich_linkedin` 등 공용 함수 제공용 |
| `download_posts.py` | 코드 | 이미지 다운로드 + `posts.json`/`posts_index.csv`(`--merge`/`--append`) |
| `pipeline_ids.py` | 코드 | 증분용 안정 식별자(원장 키): LinkedIn urn / IG media_id |
| `run_incremental.py` | 코드 | Phase 1 한 번에(수집→다운→OCR, 증분 기본) |
| `run_incremental.bat` | 실행 | Phase 1 만 더블클릭(Windows) |
| `run_auto.bat` | 실행 | 완전자동: Phase 1 + `claude -p` 분류(구독제, API키 X) |
| `run_daily.bat` | 실행 | 매일 스케줄용: 증분 수집+분류+학습요약+**릴스50 전사**, `logs/daily.log` 기록 |
| `reel_transcribe.py` | 코드 | 릴스 음성 전사(페이싱·재개·재시도). muted+seek+9s dwell 로 오디오 확보(§7). 매칭만 미해결 |
| `ig_saved_api.py` | 코드 | 옛 IG 수집기(크롬을 띄워 저장 그리드 API 응답을 가로챔). 응답 파싱 함수(`scan`/`to_record`)를 `collect_cookie.py` 가 그대로 가져다 씀 → 두 방식의 레코드 모양이 같음 |
| `ig_enrich_posts.py` | 코드 | instagram_posts.json → posts.json 보강(shortcode 매칭): text(캡션)·hashtags·author·location·api_images 추가. 분류/OCR/전사 보존 |
| `reclass_prep.py` / `reclass_merge.py` | 코드 | Phase 2 재분류 배치: 컴팩트 입력 생성 → `claude -p` 배치 분류 → posts.json 병합(기존값 `*_prev` 보존) |
| `matjip_prep.py` / `notion_matjip.py` | 코드 | 맛집 Phase 4: 게시물→가게 추출 입력 생성 / 가게 1:N 행 생성(네이버·구글 지도링크 + 로컬이미지 업로드) |
| `reel_merge.py` | 코드 | reel_transcripts.json → posts.json `transcript` 병합(media_id) |
| `ocr_images.py` | 코드 | 로컬 PaddleOCR 로 이미지 텍스트 추출 → `ocr_text` |
| `backends.py` | 코드 | (구버전) Gemini/Claude 분류 함수 + 모델 폴백 체인 |
| `classify.py` | 코드 | (구버전) API 기반 일괄 분류 CLI |
| `evaluate.py` | 코드 | 정확도/F1/혼동행렬 |
| `filter_junk.py` | 코드 | 아이콘/로고 등 잡이미지 정리 |
| `labels.json` | 설정 | 카테고리 정의(구버전) |
| `notion_export.py` | 코드 | (구버전) `posts.json` → Notion import CSV/JSON |
| `notion_sync.py` | 코드 | **Phase 3 적재(REST, 증분 업서트)**. 카테고리별 빌더. `--dry-run`/`--limit` |
| `notion_map.json` | 설정 | 카테고리→Notion DB(database_id) 매핑. gitignore(data/) |
| `requirements.txt` | 설정 | 의존성 |
| `posts.json` | **데이터** | Phase 1+2 리치 저장소(추출+분류, 핵심). gitignore |
| `notion_import.csv/json` | 데이터 | Notion 적재/Import 용. gitignore |
| `linkedin_posts.json` | 데이터 | 수집 원본. gitignore |
| `posts_index.csv` | 데이터 | 이미지↔본문 매핑. gitignore |
| `images/` | 데이터 | 다운로드 이미지. gitignore |
| `.env` | 🔒 비밀 | API 키. gitignore |
| `.pw-profile/` | 🔒 비밀 | 크롬 로그인 세션. gitignore |

---

## 9. 분류 taxonomy (Phase 2 기준)

> 초안. 조정은 나중에.

**LinkedIn** — `category` 1개 + `tech_tags`(자유):
- `데이터엔지니어링` — 파이프라인/ETL/ELT, Airflow·Spark·dbt·Snowflake·Kafka·DuckDB, 웨어하우스/레이크, 스트리밍
- `AI·LLM·에이전트` — LLM/RAG/에이전트/MCP, LangChain, 프롬프트/컨텍스트 엔지니어링, AI 코딩도구(Codex 등)
- `채용·이직·커리어` — 이력서, 면접, 연봉협상, 프로필/브랜딩, 커리어 조언
- `제품·도구홍보` — 특정 앱/서비스/제품 소개·홍보
- `기타` — 위에 안 맞는 것
- `tech_tags` 예: `Airflow`, `Spark`, `dbt`, `Snowflake`, `Kafka`, `DuckDB`, `Pandas`, `LangChain`, `RAG`, `MCP`, `Agent`, `Databricks`, `Flink` …

**Instagram** — `category` 1개:
- `영어공부` / `중국어공부` / `맛집` / `AI` / `주식·투자` / `운동·스포츠` / `여행` / `밈·유머` / `영화·드라마·엔터` / `커리어·자기계발` / `건강·뷰티·패션` / `IT·개발` / `문화·시사` / `기타`
  - `운동·스포츠`: 농구·헬스·러닝 등 운동 기술/트레이닝/스포츠 콘텐츠
  - `여행`: 여행지·호텔·공항 꿀팁·해외 라이프스타일·레저(온천·눈썰매 등)
  - `밈·유머`: 스킷·공감 밈·연애 밈·직장 밈·언어유희 등 유머 콘텐츠
  - `영화·드라마·엔터`: 영화/드라마 클립·연예·음악·팟캐스트·게임·코미디
  - `커리어·자기계발`: 면접·이직·직장 브이로그·동기부여·성공 스토리·명상
  - `건강·뷰티·패션`: 영양제·건강수치·스킨케어·헤어·코디·의류/뷰티 제품
  - `IT·개발`: 시스템설계·클라우드·개발 지식(주로 LinkedIn)·디지털 기기
  - `문화·시사`: 이민·한국문화·뉴스/상식·부동산/청약·과학·외국인 대상 한국어 학습
  - `기타`: 위 어디에도 안 맞거나 내용 판별 불가 (2026-08-05 기타 177개를 위 7개로 세분화)

---

## 10. ToS / 보안 주의

- 자동 크롤링은 각 플랫폼 약관상 **회색지대 + 계정 위험**. **사람 속도·개인 규모**로만.
- 하지 말 것: 코드로 로그인/비밀번호 처리, Google-SSO 자동화, 봇탐지 우회.
- 커밋 금지: `.env`, `.pw-profile/`, 수집 데이터. (`.gitignore` 에 등록됨)
