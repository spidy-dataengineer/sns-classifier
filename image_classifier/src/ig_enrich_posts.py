r"""instagram_posts.json(API 리치 수집) → posts.json 보강 병합.

shortcode(code ↔ post_url)로 매칭해 기존 IG 레코드에 다음을 추가/갱신:
  text(캡션) · hashtags · author · location · code · media_type · api_images(전 슬라이드 URL)
분류(category/tech_tags/summary) · ocr_text · transcript · local_files 등은 **보존**.

media_id(pk)는 posts.json 과 불일치하므로 shortcode 로만 매칭한다.
새 이미지(슬라이드) 다운로드/OCR/재분류는 후속 단계.

사용:  ..\.venv\Scripts\python.exe src\ig_enrich_posts.py [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "data" / "posts.json"
API = ROOT / "data" / "instagram_posts.json"
BAK = ROOT / "data" / "posts.enrichbak.json"


def shortcode(url):
    m = re.search(r"/(?:p|reel)/([^/?]+)", url or "")
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    api = json.loads(API.read_text(encoding="utf-8"))
    by_sc = {r["code"]: r for r in api if r.get("code")}

    enriched = got_caption = got_loc = 0
    matched_codes = set()
    for x in posts:
        if x.get("platform") != "instagram":
            continue
        sc = shortcode(x.get("post_url"))
        a = by_sc.get(sc)
        if not a:
            continue
        matched_codes.add(sc)
        enriched += 1
        if not args.dry_run:
            if (a.get("text") or "").strip():
                x["text"] = a["text"]
            x["hashtags"] = a.get("hashtags") or x.get("hashtags") or []
            if a.get("author"):
                x["author"] = a["author"]
            if a.get("location"):
                x["location"] = a["location"]
            x["code"] = sc
            x["media_type"] = a.get("media_type", x.get("media_type"))
            x["api_images"] = a.get("images") or []
        if (a.get("text") or "").strip():
            got_caption += 1
        if a.get("location"):
            got_loc += 1

    new_codes = [c for c in by_sc if c not in matched_codes]

    print(f"API 레코드 {len(api)} · posts.json IG 매칭 {enriched}")
    print(f"  캡션 채워질 것 {got_caption} · 장소 {got_loc}")
    print(f"  API엔 있으나 posts.json에 없는 신규 shortcode {len(new_codes)}건(이번 병합 제외, 커버리지 확장 시 처리)")
    if args.dry_run:
        print("DRY-RUN — 파일 변경 안 함")
        return

    BAK.write_text(POSTS.read_text(encoding="utf-8"), encoding="utf-8")  # 원본 백업(아직 미변경)
    POSTS.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ posts.json 갱신 완료. 원본 백업 → {BAK.name}")


if __name__ == "__main__":
    main()
