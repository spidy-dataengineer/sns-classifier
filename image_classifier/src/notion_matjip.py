r"""맛집 적재 (REST) — 게시물→가게 1:N.

입력:
  - data/posts.json (category=맛집)
  - data/matjip_extract.json : {post_url: [{"가게명","음식","지역"}, ...]}  ← Phase 4 추출(LLM/claude -p 산출)
동작:
  - 가게 1행 생성. 네이버·구글 지도 검색링크 자동 생성, 로컬 이미지 업로드(사진).
  - 멱등 업서트: (게시물링크, 가게명, 음식) 조합이 이미 있으면 스킵.

사용:
  python src\notion_matjip.py [--limit N] [--dry-run]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TOKEN = os.environ.get("NOTION_TOKEN")
if not TOKEN:
    sys.exit("NOTION_TOKEN 이 .env 에 없습니다.")

H = {"Authorization": f"Bearer {TOKEN}", "Notion-Version": "2022-06-28"}
HJ = {**H, "Content-Type": "application/json"}
API = "https://api.notion.com/v1"

POSTS = ROOT / "data" / "posts.json"
EXTRACT = ROOT / "data" / "matjip_extract.json"
MAP = ROOT / "data" / "notion_map.json"
IMG_DIR = ROOT / "data" / "images"
MAX = 2000


def _req(method, path, **kw):
    r = requests.request(method, f"{API}{path}", **kw)
    if r.status_code == 429:
        time.sleep(float(r.headers.get("Retry-After", 1)))
        return _req(method, path, **kw)
    if not r.ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json()


# --- 이미지 업로드 (파일당 3단계) ---
def upload_image(path: Path):
    up = _req("POST", "/file_uploads", headers=HJ,
              json={"filename": path.name, "content_type": "image/jpeg"})
    fid = up["id"]
    with open(path, "rb") as f:
        _req("POST", f"/file_uploads/{fid}/send", headers=H,
             files={"file": (path.name, f, "image/jpeg")})
    return fid


# --- 지도 검색링크 ---
def naver_link(q):
    return "https://map.naver.com/p/search/" + quote(q)


def google_link(q):
    return "https://www.google.com/maps/search/?api=1&query=" + quote(q)


def map_query(name, region):
    return " ".join(x for x in [region, name] if x).strip()


def rt(s):
    s = str(s or "")
    return [{"type": "text", "text": {"content": s[i:i + MAX]}} for i in range(0, len(s), MAX)] or \
           [{"type": "text", "text": {"content": ""}}]


def existing_keys(db_id):
    keys, cursor = set(), None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = _req("POST", f"/databases/{db_id}/query", headers=HJ, json=body)
        for pg in data["results"]:
            p = pg["properties"]
            url = p.get("게시물링크", {}).get("url") or ""
            name = "".join(t.get("plain_text", "") for t in p.get("가게명", {}).get("title", []))
            food = "".join(t.get("plain_text", "") for t in p.get("음식", {}).get("rich_text", []))
            keys.add((url, name, food))
        if not data.get("has_more"):
            return keys
        cursor = data["next_cursor"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_id = json.loads(MAP.read_text(encoding="utf-8"))["맛집"]["database_id"]
    posts = {x["post_url"]: x for x in json.loads(POSTS.read_text(encoding="utf-8"))
             if x.get("category") == "맛집" and x.get("post_url")}
    if not EXTRACT.exists():
        sys.exit(f"{EXTRACT} 없음. Phase 4 추출 결과(가게 목록)를 먼저 만드세요.")
    extract = json.loads(EXTRACT.read_text(encoding="utf-8"))

    # 생성할 행 목록 펼치기
    rows = []
    for url, places in extract.items():
        post = posts.get(url)
        if not post:
            continue
        summary = (post.get("summary") or "")[:MAX]
        lf = (post.get("local_files") or [None])[0]
        img = (IMG_DIR / lf) if lf else None
        for pl in places:
            name = (pl.get("가게명") or "").strip()
            food = (pl.get("음식") or "").strip()
            region = (pl.get("지역") or "").strip()
            rows.append({"url": url, "name": name, "food": food, "region": region,
                         "summary": summary, "img": img})

    print(f"[맛집] 게시물 {len(extract)}개 → 가게행 {len(rows)}개, DB={db_id}")

    if args.dry_run:
        for r in rows[:8]:
            q = map_query(r["name"] or r["food"], r["region"])
            print(f"  · {r['name'] or '(이름미상)'} | {r['food']} | {r['region']} | 검색='{q}' | img={bool(r['img'])}")
        return

    have = existing_keys(db_id)
    print(f"기존 행 {len(have)}개")
    todo = [r for r in rows if (r["url"], r["name"], r["food"]) not in have]
    if args.limit:
        todo = todo[:args.limit]
    print(f"신규 대상 {len(todo)}개")

    done = 0
    for r in todo:
        q = map_query(r["name"] or r["food"], r["region"])
        title = r["name"] or (f"{r['food']} · {r['region']}".strip(" ·") or "(이름미상)")
        props = {
            "가게명": {"title": rt(title)},
            "음식": {"rich_text": rt(r["food"])},
            "지역": {"rich_text": rt(r["region"])},
            "네이버지도": {"url": naver_link(q) if q else None},
            "구글맵": {"url": google_link(q) if q else None},
            "요약": {"rich_text": rt(r["summary"])},
            "게시물링크": {"url": r["url"]},
        }
        if r["img"] and r["img"].exists():
            fid = upload_image(r["img"])
            props["사진"] = {"files": [{"type": "file_upload", "name": r["img"].name,
                                       "file_upload": {"id": fid}}]}
        _req("POST", "/pages", headers=HJ,
             json={"parent": {"database_id": db_id}, "properties": props})
        done += 1
        if done % 10 == 0:
            print(f"  ...{done}/{len(todo)}")
        time.sleep(0.34)
    print(f"완료: {done}행 생성.")


if __name__ == "__main__":
    main()
