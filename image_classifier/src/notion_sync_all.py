"""빌더가 있는 모든 카테고리를 Notion 에 업서트 (무인 스케줄용).

맛집은 가게 추출(Phase 4, LLM 판단)이 선행이라 무인 자동에서 제외 → notion_matjip.py 로 별도.
각 카테고리는 notion_sync.py 를 재사용(멱등 업서트, 주소없는 것 스킵).

  python src/notion_sync_all.py [--dry-run]
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CATEGORIES = ["영어공부", "중국어공부", "주식·투자", "AI", "운동·스포츠", "기타",
              "여행", "밈·유머", "영화·드라마·엔터", "커리어·자기계발",
              "건강·뷰티·패션", "IT·개발", "문화·시사",
              "데이터엔지니어링", "AI·LLM·에이전트", "채용·이직·커리어", "제품·도구홍보"]


def main():
    extra = ["--dry-run"] if "--dry-run" in sys.argv else []
    for c in CATEGORIES:
        print(f"\n===== {c} =====", flush=True)
        subprocess.run([PY, "-u", "src/notion_sync.py", "--category", c, *extra], cwd=str(ROOT))
    print("\n전체 카테고리 Notion 반영 완료 (맛집은 notion_matjip.py 로 별도).", flush=True)


if __name__ == "__main__":
    main()
