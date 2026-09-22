"""
============================================================
 download_posts.py
------------------------------------------------------------
 collect_posts.js 가 만든 [{text, images:[url,...]}] JSON 을 받아
   - 이미지를 images/ 로 다운로드
   - 게시물 텍스트를 함께 인덱스로 저장
 (CDN URL 은 세션 만료 전 빨리 받아야 함 — 수집 직후 같은 세션에서 실행 권장)

   python download_posts.py linkedin_posts.json
   python download_posts.py instagram_posts.json --referer https://www.instagram.com/ --prefix ig --merge

 출력:
   images/post_0001_1.jpg ...          게시물별 이미지
   posts_index.csv  (image_file, post_text)   ← 분류 결과(classifications.csv)와 조인
   posts.json       원본 레코드 + 로컬 파일명
============================================================
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from pipeline_ids import record_id, safe_id

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IMAGES = DATA / "images"

EXT_BY_CT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _safe(s: str) -> str:
    """깨진(짝 없는) 서로게이트 문자 제거 — UTF-8 저장 시 에러 방지."""
    return (s or "").encode("utf-8", "ignore").decode("utf-8")


def guess_ext(content_type: str, url: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in EXT_BY_CT:
        return EXT_BY_CT[ct]
    low = url.lower()
    for e in (".png", ".webp", ".gif", ".jpeg", ".jpg"):
        if e in low:
            return ".jpg" if e == ".jpeg" else e
    return ".jpg"


def fetch(url: str, headers: dict, timeout: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="게시물 JSON → images/ 다운로드 + 텍스트 인덱스")
    ap.add_argument("posts_file", help="collect_posts.js 출력 JSON")
    ap.add_argument("--referer", default="https://www.linkedin.com/", help="Referer 헤더(403 회피)")
    ap.add_argument("--out", default=str(IMAGES), help="이미지 저장 폴더")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--prefix", default="post", help="이미지 파일명 접두어(플랫폼 구분: ig 등)")
    ap.add_argument("--merge", action="store_true", help="기존 posts.json 유지 + 같은 platform 만 교체 후 병합")
    ap.add_argument("--append", action="store_true", help="증분: 기존 posts.json 전체 유지 + id 기준 새 항목만 추가")
    args = ap.parse_args()

    records = json.loads(Path(args.posts_file).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        print("[오류] JSON 최상위가 리스트가 아닙니다 (collect_posts.js 출력 확인).")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": UA, "Referer": args.referer, "Accept": "image/avif,image/webp,image/*,*/*"}

    seen: set[str] = set()
    index_rows: list[dict] = []
    saved = skipped = failed = 0

    for i, rec in enumerate(records, 1):
        text = _safe((rec.get("text") or "").replace("\r", " ").replace("\n", " ").strip())
        rec["text"] = text  # posts.json 저장 시에도 깨진 문자 없도록
        rid = record_id(rec)
        base = f"{args.prefix}_{safe_id(rid)}" if rid else f"{args.prefix}_{i:04d}"  # ID기반 파일명(증분 재실행 충돌 방지)
        local: list[str] = []
        for j, url in enumerate(rec.get("images", []), 1):
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            try:
                data, ct = fetch(url, headers, args.timeout)
                h = hashlib.sha256(data).hexdigest()
                if h in seen:
                    skipped += 1
                    continue
                seen.add(h)
                fn = out / f"{base}_{j}{guess_ext(ct, url)}"
                fn.write_bytes(data)
                saved += 1
                local.append(fn.name)
                index_rows.append({"image_file": fn.name, "post_text": text})
                print(f"  게시물 {i} 이미지 {j} → {fn.name} ({len(data) // 1024}KB)")
            except urllib.error.HTTPError as e:
                failed += 1
                print(f"  게시물 {i} 이미지 {j} 실패 HTTP {e.code} (세션 만료/403 가능) {url[:60]}")
            except Exception as e:
                failed += 1
                print(f"  게시물 {i} 이미지 {j} 실패 {type(e).__name__} {url[:60]}")
        rec["local_files"] = local

    posts_path = DATA / "posts.json"
    if args.append and posts_path.exists():  # 증분: 전체 유지 + id 신규만 추가
        try:
            existing = json.loads(posts_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
        have = {record_id(r) for r in existing}
        to_add = [r for r in records if record_id(r) not in have]
        merged = existing + to_add
        index_rows = [{"image_file": fn, "post_text": _safe(r.get("text", ""))}
                      for r in merged for fn in r.get("local_files", [])]
        print(f"[증분] 기존 {len(existing)} + 신규 {len(to_add)} = {len(merged)}")
    elif args.merge and posts_path.exists():
        try:
            existing = json.loads(posts_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
        inc_platform = next((r.get("platform") for r in records if r.get("platform")), None)
        kept = [r for r in existing if r.get("platform") != inc_platform]  # 다른 플랫폼은 보존
        merged = kept + records
        index_rows = [{"image_file": fn, "post_text": _safe(r.get("text", ""))}
                      for r in merged for fn in r.get("local_files", [])]  # 인덱스는 병합 전체로 재구성
    else:
        merged = records

    with (DATA / "posts_index.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["image_file", "post_text"])
        w.writeheader()
        w.writerows(index_rows)
    posts_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n저장 {saved} / 중복스킵 {skipped} / 실패 {failed}  → {out}")
    print(f"[저장] {DATA / 'posts_index.csv'}  (이미지 ↔ 게시물 텍스트)")
    print(f"[저장] {DATA / 'posts.json'}")
    if failed:
        print("실패가 많으면 같은 세션에서 collect_posts.js 를 다시 실행해 새 URL 을 받으세요 (CDN 만료).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
