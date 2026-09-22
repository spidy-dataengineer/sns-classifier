"""logs/daily.log 의 '가장 최근 RUN 블록'을 파싱해 성공/실패를 한국어로 요약.

run_daily.bat 맨 끝에서 호출 → logs/daily_summary.txt 에 기록(사람이 아침에 한눈에 확인).
단계별 성공/에러, 로그인 만료, 새 게시물 수집/분류/노션 반영 건수를 뽑는다.

  python src/check_daily.py
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "daily.log"
OUT = ROOT / "logs" / "daily_summary.txt"

# 진짜 위험 신호만 (claude -p 의 무해한 안내문 'blocked by permission'/'실패 0' 등 오탐 제외)
ERR_PATTERNS = ["Traceback", "일시적으로 차단", "/accounts/login", "/challenge",
                "checkpoint", "ExpiredToken", "Error loading SSO", "로그인해 주세요",
                "[중단]", "최대 재시도 도달"]


def main():
    if not LOG.exists():
        OUT.write_text("daily.log 없음 — 아직 실행된 적 없음.", encoding="utf-8")
        print("daily.log 없음")
        return
    text = LOG.read_text(encoding="utf-8", errors="replace")
    # 마지막 RUN ~ (DONE 또는 끝) 블록
    runs = list(re.finditer(r"RUN\s+.*", text))
    if not runs:
        OUT.write_text("RUN 기록 없음.", encoding="utf-8")
        print("RUN 기록 없음")
        return
    block = text[runs[-1].start():]
    run_time = runs[-1].group(0).replace("RUN", "").strip()
    done = "DONE" in block

    steps = re.findall(r"\[(\d[a-z]?)\][^\n]*", block)
    errs = []
    for ln in block.splitlines():
        for p in ERR_PATTERNS:
            if p.lower() in ln.lower():
                errs.append(ln.strip()[:120])
                break

    def find(pat):
        m = re.search(pat, block)
        return m.group(1) if m else "?"

    new_ig = find(r"신규\s+(\d+)건")
    cats = re.findall(r"반영 완료:\s*(\d+)건", block)
    notion_total = sum(int(c) for c in cats) if cats else 0

    lines = []
    lines.append(f"■ 최근 실행: {run_time}")
    lines.append(f"■ 완료 표시(DONE): {'있음 ✅' if done else '없음 ⚠️ (중간에 멈췄을 수 있음)'}")
    lines.append(f"■ 실행된 단계: {len(steps)}개  {steps}")
    lines.append(f"■ 새 게시물 수집: {new_ig}건")
    lines.append(f"■ Notion 반영(카테고리 합): {notion_total}건")
    if errs:
        lines.append(f"■ ⚠️ 문제 신호 {len(errs)}건:")
        seen = set()
        for e in errs:
            if e not in seen:
                lines.append(f"    - {e}")
                seen.add(e)
    else:
        lines.append("■ 문제 신호: 없음 ✅")
    summary = "\n".join(lines)
    OUT.write_text(summary, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
