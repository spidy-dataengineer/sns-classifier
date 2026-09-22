r"""맛집 Phase 4 추출용 컴팩트 입력 배치 생성.

posts.json 의 category=맛집 레코드를 배치로 뽑는다. claude -p 가 읽고
게시물별 가게 목록(가게명/음식/지역/주소)을 out 파일로 쓴다.

출력: data/.tmp/matjip/in_000.json ...  (각 [{post_url,text,ocr_text,transcript,location}])
사용:  ..\.venv\Scripts\python.exe src\matjip_prep.py [--batch 75]
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "data" / "posts.json"
OUTDIR = ROOT / "data" / ".tmp" / "matjip"


def clip(s, n):
    return (s or "").strip()[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=75)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    for f in OUTDIR.glob("*.json"):
        f.unlink()

    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    m = [x for x in posts if x.get("category") == "맛집" and x.get("post_url")]
    items = [{
        "post_url": x["post_url"],
        "text": clip(x.get("text"), 1800),
        "ocr_text": clip(x.get("ocr_text"), 700),
        "transcript": clip(x.get("transcript"), 700),
        "location": clip(x.get("location"), 100),
    } for x in m]

    n = args.batch
    batches = [items[i:i + n] for i in range(0, len(items), n)]
    for bi, b in enumerate(batches):
        (OUTDIR / f"in_{bi:03d}.json").write_text(
            json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"맛집 {len(items)}건 → {len(batches)}배치(배치당 {n}) → {OUTDIR}")


if __name__ == "__main__":
    main()
