@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
echo. >> logs\daily.log
echo ============================================================ >> logs\daily.log
echo RUN  %date% %time% >> logs\daily.log

echo [1] IG 증분 수집 (쿠키 API: code/caption/캐러셀, 브라우저 없음) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\collect_cookie.py --platform instagram --incremental >> logs\daily.log 2>&1

echo [2] IG 신규 게시물 추출 (posts.json 에 없는 것만) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\extract_new_ig.py >> logs\daily.log 2>&1

echo [3] IG 다운로드 + posts.json 추가 (이미지 신선할 때 즉시) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\download_posts.py data\.tmp\new_ig.json --append --prefix ig --referer https://www.instagram.com/ >> logs\daily.log 2>&1

echo [3b] LinkedIn 증분 수집 (쿠키 voyager API, 브라우저 없음) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\collect_cookie.py --platform linkedin --incremental >> logs\daily.log 2>&1

echo [3c] LinkedIn 다운로드 + posts.json 추가 >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\download_posts.py data\linkedin_posts.json --append --prefix li --referer https://www.linkedin.com/ >> logs\daily.log 2>&1

echo [4] OCR (다운스케일 빠른 방식, 새 이미지만) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\ocr_images.py >> logs\daily.log 2>&1

echo [5] IG 캡션/캐러셀 URL 보강 (shortcode) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\ig_enrich_posts.py >> logs\daily.log 2>&1

echo [6] 분류 + 학습요약 (python 래퍼: 미분류만 추출→claude→병합, 20분 타임아웃) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\run_classify.py >> logs\daily.log 2>&1

echo [7] Notion 반영 (빌더 있는 카테고리 전체; 맛집 제외) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\notion_sync_all.py >> logs\daily.log 2>&1

echo DONE %date% %time% >> logs\daily.log

echo [8] 실행 결과 요약 (logs\daily_summary.txt) >> logs\daily.log
"..\.venv\Scripts\python.exe" -u src\check_daily.py >> logs\daily.log 2>&1

echo. >> logs\daily.log
echo [수동 잔여] 맛집 가게추출(notion_matjip) / 영상 자막(오염 위험, 보류) 은 사람이 별도 처리 >> logs\daily.log
