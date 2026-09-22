"""무인 분류 (배치 [6]): 분류 안 된 게시물만 작은 파일로 뽑아 claude -p 에 주고 결과를 병합.

이전 방식(posts.json 통째로 claude 에 분류시킴)은 파일이 45,000줄로 커지자 claude 가
앞부분만 읽고 파일 끝의 새 게시물을 놓쳤다(미분류 누적). → 분류 대상만 추출해 주면
claude 가 볼 게 몇 건뿐이라 절대 안 놓친다. posts.json 구조는 그대로.

claude 는 Windows 에선 npm 전역(.cmd) → cmd /c 로 실행, Linux(GitHub Actions)에선 그대로 실행.
20분 타임아웃 후 프로세스 트리 강제 종료.

  python src/run_classify.py
"""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pipeline_ids import record_id  # IG media_id / LinkedIn urn / id 공용 식별자
POSTS = ROOT / "data" / "posts.json"
INP = ROOT / "data" / ".tmp" / "to_classify.json"
OUT = ROOT / "data" / ".tmp" / "classified.json"
PROMPT_FILE = ROOT / "data" / ".tmp" / "classify_prompt.txt"

# 상세 지시는 파일(UTF-8)에 두고, claude 인자로는 경로만 넘긴다.
# (긴 한글/특수문자를 cmd 인자로 넘기면 깨져서 claude 가 지시를 못 받음 — 2026-08-10 확인)
INSTRUCTIONS = """다음 파일을 분류하는 작업이다.
- 입력: {INP} — 분류 안 된 게시물 배열(각 항목: code, platform, media_type, caption, ocr, subtitle, hashtags).
- 각 게시물에 category(아래 목록 중 정확히 하나), tech_tags(배열, 없으면 []), summary(2-3문장 한국어 학습/정리 요약)를 부여하라.
- caption·ocr·subtitle·hashtags 를 종합 판단. 애매하면 기타.

Instagram category 목록: 영어공부 / 중국어공부 / 맛집 / AI / 주식·투자 / 운동·스포츠 / 여행 / 밈·유머 / 영화·드라마·엔터 / 커리어·자기계발 / 건강·뷰티·패션 / IT·개발 / 문화·시사 / 기타
LinkedIn category 목록: 데이터엔지니어링 / AI·LLM·에이전트 / 채용·이직·커리어 / 제품·도구홍보 / 기타

질문하지 말고 즉시 처리하라. 결과를 반드시 {OUT} 파일에 JSON 배열로 Write 하라.
각 원소는 code(입력 그대로), category, tech_tags, summary 4개 키를 가진다. 입력의 모든 code 를 빠짐없이 포함하라.
"""


def clip(s, n):
    return (s or "").strip().replace("\n", " ")[:n]


def key_of(x):
    # IG=code(shortcode), LinkedIn=urn:li:activity(record_id), 그 외 record_id 폴백
    return x.get("code") or record_id(x) or ""


def main():
    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    todo = [x for x in posts if not x.get("category")]
    if not todo:
        print("[classify] 분류할 새 게시물 없음 → 스킵", flush=True)
        return

    items = [{
        "code": key_of(x),
        "platform": x.get("platform"),
        "media_type": x.get("media_type"),
        "caption": clip(x.get("text"), 600),
        "ocr": clip(x.get("ocr_text"), 700),
        "subtitle": clip(x.get("transcript"), 400) or clip(x.get("frame_text"), 400),
        "hashtags": (x.get("hashtags") or [])[:6],
    } for x in todo]
    INP.parent.mkdir(parents=True, exist_ok=True)
    INP.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT.unlink(missing_ok=True)
    print(f"[classify] 분류 대상 {len(todo)}건 추출 → {INP.name}", flush=True)

    PROMPT_FILE.write_text(INSTRUCTIONS.format(INP=INP, OUT=OUT), encoding="utf-8")
    # claude 인자는 경로만(영숫자/슬래시) → cmd 를 거쳐도 안 깨짐. 상세 지시는 파일에서 읽힌다.
    prompt = (f"Read the file {PROMPT_FILE} for your task instructions, "
              f"then Read {INP} for the data, and follow the instructions exactly. "
              f"Do not ask questions; write the JSON result to {OUT}.")
    windows = os.name == "nt"
    cmd = ["claude", "-p", prompt,
           "--allowedTools", "Read Edit Write", "--permission-mode", "acceptEdits"]
    if windows:
        cmd = ["cmd", "/c", *cmd]
    # Linux 는 새 프로세스 그룹으로 띄워야 타임아웃 때 자식까지 한 번에 죽일 수 있다(taskkill /T 대응).
    p = subprocess.Popen(cmd, cwd=str(ROOT), start_new_session=not windows)
    try:
        p.wait(timeout=1200)
        print(f"[classify] claude -p 종료 (exit {p.returncode})", flush=True)
    except subprocess.TimeoutExpired:
        print("[classify] 20분 초과 → 강제 종료", flush=True)
        if windows:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)

    if not OUT.exists():
        print("[classify] ⚠️ 결과 파일 없음 — 분류 실패(다음 실행에서 재시도됨)", flush=True)
        return
    res = {r["code"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))
           if r.get("code") and r.get("category")}
    n = 0
    for x in posts:
        if x.get("category"):
            continue
        r = res.get(key_of(x))
        if r:
            x["category"] = r["category"]
            x["tech_tags"] = r.get("tech_tags", [])
            x["summary"] = r.get("summary", "")
            n += 1
    POSTS.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    left = sum(1 for x in posts if not x.get("category"))
    print(f"[classify] 병합 {n}/{len(todo)}건 | 남은 미분류 {left}", flush=True)


if __name__ == "__main__":
    main()
