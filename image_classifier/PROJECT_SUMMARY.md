# image_classifier — 프로젝트 총정리

내가 **인스타그램·링크드인에 저장한 게시물**을 자동으로 모아 → 모든 텍스트를 뽑고 → AI가 주제별로 분류·요약해 → **주제별로 찾아보고 학습할 수 있는 지식창고**로 만드는 도구.

> 상세 운영법·함정·taxonomy는 **`RUNBOOK.md`** 참고. 이 문서는 "무엇을 만들었고 어떻게 도는가"의 전체 그림.

---

## 1. 전체 파이프라인

```
[Phase 1] 추출 (로컬·무료)
   수집(Playwright, 전용 크롬 프로필) → 이미지 다운로드 → OCR(PaddleOCR) → (릴스) 음성 Whisper + 화면 프레임 OCR
        │  결과: posts.json (게시물별 메타 + 본문 + ocr_text + transcript + frame_text)
        ▼
[Phase 2] 분류·요약 (구독제 Claude, API 비용 0)
   각 게시물에 category + tech_tags + summary(학습요약) 부여
        │  결과: posts.json 갱신
        ▼
[Phase 3] 적재 (Notion)
   Notion DB에 카드로: 카테고리·요약·태그·원본이미지·원문링크
```

**설계 원칙**: 유료 LLM API 안 씀. 추출=로컬 무료 도구, 분류=구독제 Claude, Notion=무료.

---

## 2. 구현한 기능

| 영역 | 내용 |
|---|---|
| **수집** | LinkedIn 저장글(카드), Instagram 저장 그리드(썸네일). 전용 크롬 프로필로 1회 로그인 후 자동. 코드가 비번 안 다룸 |
| **증분(ID 원장)** | 재실행 시 `posts.json`에 이미 있는 것 건너뜀(비파괴, unsave 안 함). `pipeline_ids.py` |
| **이미지 OCR** | PaddleOCR 한국어(한글+영어). 릴스 화면 텍스트는 중국어 모델(한자)+병음(성조)까지 |
| **영상(릴스) 전사** | 저장 그리드에서 릴스 열기 → 스트림 캡처 → 오디오 Whisper + 프레임 OCR (음성 자막 + 화면 자막 둘 다) |
| **분류·요약** | 이미지 시각판단 + OCR + 캡션 종합. 구독제 Claude(워크플로우/`claude -p`). category+tech_tags+summary |
| **URL·매칭** | 저장 그리드 API 응답 캡처로 모든 게시물 permalink(post_url) + 릴스 transcript 매칭 |
| **Notion export** | posts.json → notion_import.csv/json |
| **자동화** | Windows 작업 스케줄러(매일 11시) → `run_daily.bat`: 증분→분류→릴스 자동 |

---

## 3. 실행 방법

| 목적 | 명령 |
|---|---|
| Phase 1만(추출) 증분 | `run_incremental.bat` 더블클릭 |
| **완전자동(추출+분류)** | `run_auto.bat` (구독제 claude -p, API키 X) |
| 매일 자동 | `SavedPostsDaily` 스케줄(11:00) → `run_daily.bat` (증분+분류+릴스) |
| Notion 적재 | Claude Code 재시작 후 MCP, 또는 `notion_import.csv` import |

> ⚠️ 스케줄 무인 실행은 **11시에 PC 켜짐+로그인(잠금해제)** 필요 (브라우저가 화면 필요).

---

## 4. 현재 데이터 상태

- **총 873 게시물**: LinkedIn 109 + Instagram 764, **전부 category+tech_tags+summary 완비**
- IG 분포: 영어공부 431 / 맛집 144 / 기타 132 / 운동·스포츠 25 / AI 12 / 주식·투자 11 / 중국어공부 9
- 릴스 전사: 진행형(스케줄이 하루 N개씩 축적). post_url: 556/764 채움(재캡처로 나머지 보충)

---

## 5. 알려진 제약·함정

- **`.bat`은 CRLF 줄바꿈 필수** — Write/Edit 툴은 LF로 저장 → cmd/스케줄러가 실행 못 함. 편집 후 `sed -i 's/$/\r/'` 로 변환.
- 자동 크롤링은 약관 회색지대 → **사람 속도·개인 규모**로만. 릴스 전사는 페이싱(랜덤지연·챌린지시 중단).
- 릴스 오디오: DASH 오디오트랙만 잡히면 무음 → 여러 스트림 시도 + 재시도.
- Notion MCP는 `claude mcp add` 후 **세션 재시작**해야 도구 로드됨.

---

## 6. 남은 일

- 릴스 전사 백로그 소진(스케줄), post_url 나머지 보충
- Notion 실제 적재(재시작 후)
- taxonomy 미세조정(기타 132 추가 세분화 여지)

---

## 7. 폴더 구조 (정리 후)

```
image_classifier/
├── PROJECT_SUMMARY.md · RUNBOOK.md · requirements.txt · .gitignore · .env(.example)
├── run_daily.bat · run_auto.bat · run_incremental.bat      # 실행 진입점(CRLF)
├── src/            # 파이프라인 코드 (collect/download/ocr/reel/merge/notion/run_incremental/pipeline_ids)
├── data/           # 산출물(gitignore): posts.json, *_posts.json, posts_index.csv, reel_transcripts.json, saved_map.json, notion_import.*, images/
├── logs/           # 실행 로그(gitignore)
├── legacy/         # 구버전(backends/classify/evaluate/filter_junk/collect_posts.js/labels.json/ground_truth.csv/old README)
└── .pw-profile/    # 로그인 세션(gitignore·비밀)
```
