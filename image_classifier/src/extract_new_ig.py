"""instagram_posts.json(ig_saved_api 수집) 중 posts.json 에 아직 없는 신규만 추출.

download_posts.py --append 에 넘길 컴팩트 입력(data/.tmp/new_ig.json)을 만든다.
전체를 넘기면 download_posts 가 기존 것까지 재다운로드하므로 신규만 골라낸다.
(신방식 수집 = code/post_url 항상 존재 → 구버전 collect_playwright 의 '주소 없는 게시물' 문제 없음)

  python src/extract_new_ig.py
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def sc(u):
    m = re.search(r"/(p|reel)/([^/?]+)", u or "")
    return m.group(2) if m else None


def main():
    api = json.loads((DATA / "instagram_posts.json").read_text(encoding="utf-8"))
    posts = json.loads((DATA / "posts.json").read_text(encoding="utf-8"))
    have = {x.get("code") for x in posts if x.get("code")} | \
           {sc(x.get("post_url")) for x in posts if sc(x.get("post_url"))}
    new = [r for r in api if r.get("code") and r["code"] not in have]
    out = DATA / ".tmp" / "new_ig.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"신규 {len(new)}건 → {out} | media_type: {dict(Counter(r.get('media_type') for r in new))}")


if __name__ == "__main__":
    main()
