"""IG 캐러셀 2번째 이후 슬라이드 다운로드 (api_images → data/images/).

ig_saved_api.py 가 확보한 api_images(전 슬라이드 URL)는 다운로드가 안 돼 있어
대표 1장만 OCR된 상태 → 전 슬라이드를 받아 local_files 에 등록하고,
새 파일이 추가된 레코드는 ocr_text 를 제거해 ocr_images.py 가 다시 OCR하게 한다.

  python carousel_fill.py            # 다운로드만 (이어하기 지원)
  이후: python -u ocr_images.py      # 제거된 ocr_text 재생성(전 슬라이드 포함)
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IMAGES = DATA / "images"
STORE = DATA / "posts.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Referer": "https://www.instagram.com/",
           "Accept": "image/avif,image/webp,image/*,*/*"}


def shortcode(url):
    m = re.search(r"/(p|reel|reels)/([^/?]+)", url or "")
    return m.group(2) if m else None


def main() -> int:
    records = json.loads(STORE.read_text(encoding="utf-8"))
    targets = [r for r in records
               if r.get("platform") == "instagram" and r.get("media_type") == "carousel"
               and r.get("api_images") and shortcode(r.get("post_url"))]
    total = sum(len(r["api_images"]) for r in targets)
    print(f"캐러셀 {len(targets)}개 / 슬라이드 {total}장", flush=True)

    done = fail = 0
    for r in targets:
        sc = shortcode(r["post_url"])
        local = r.setdefault("local_files", [])
        added = False
        for i, url in enumerate(r["api_images"]):
            fn = f"ig_c_{sc}_s{i:02d}.jpg"
            path = IMAGES / fn
            if not path.exists():
                try:
                    req = urllib.request.Request(url, headers=HEADERS)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        path.write_bytes(resp.read())
                    time.sleep(0.15)
                except Exception as e:
                    fail += 1
                    print(f"  {fn} 실패: {type(e).__name__}", flush=True)
                    continue
            done += 1
            if fn not in local:
                local.append(fn)
                added = True
        if added and "ocr_text" in r:
            del r["ocr_text"]  # ocr_images.py 가 전 슬라이드로 재생성
        STORE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 다운로드 {done} | 실패 {fail} → local_files 등록, ocr_text 초기화 완료", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
