r"""IG 저장 게시물 리치 수집 (API 응답 캡처) — Phase 1 보강.

저장 그리드를 스크롤하며 /api/v1/feed/saved/posts/ · /graphql/query 응답을 캡처,
media 객체에서 caption·해시태그·전 캐러셀 슬라이드·작성자·장소·shortcode 를 파싱해
instagram_posts.json 을 만든다. (기존 썸네일-only 수집을 대체)

- shortcode(code)+media_id(pk) 를 함께 확보 → 매칭 문제 해소.
- 캐러셀은 전 슬라이드 이미지 URL 을 images 에 담음.
- 이후 흐름은 동일: download_posts.py → ocr_images.py → (재)분류.

주의: .pw-profile 을 다른 Playwright 가 쓰면 충돌. 로그인 세션 필요.
실행:  ..\.venv\Scripts\python.exe -u src\ig_saved_api.py [--max-steps 400]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import collect_playwright as C  # PROFILE_DIR, DATA, PLATFORMS, LOGIN_WAIT_SEC, _is_login_page

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SAVED_URL = C.PLATFORMS["instagram"]["saved_url"]
OUT = C.DATA / "instagram_posts.json"
INTERESTING = ("/api/v1/feed/saved/posts", "/graphql/query")
MT = {1: "image", 2: "video", 8: "carousel"}


def looks_media(n):
    return isinstance(n, dict) and ("code" in n) and \
        any(k in n for k in ("caption", "carousel_media", "image_versions2"))


def scan(o, out):
    if isinstance(o, dict):
        if looks_media(o):
            out.append(o)
        for v in o.values():
            scan(v, out)
    elif isinstance(o, list):
        for v in o:
            scan(v, out)


def best_img(node):
    iv = (node.get("image_versions2") or {}).get("candidates") or []
    return iv[0]["url"] if iv else None


def collect_images(m):
    """캐러셀이면 전 슬라이드, 아니면 단일. 이미지 URL 리스트."""
    urls = []
    car = m.get("carousel_media")
    if car:
        for s in car:
            u = best_img(s)
            if u:
                urls.append(u)
    else:
        u = best_img(m)
        if u:
            urls.append(u)
    # 중복 제거(순서 보존)
    seen, out = set(), []
    for u in urls:
        k = u.split("?")[0]
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


def caption_text(m):
    c = m.get("caption")
    if isinstance(c, dict):
        return c.get("text", "") or ""
    return c if isinstance(c, str) else ""


def to_record(m):
    code = m.get("code") or ""
    cap = caption_text(m)
    loc = m.get("location")
    return {
        "platform": "instagram",
        "id": str(m.get("pk") or m.get("id") or code),
        "code": code,
        "post_url": f"https://www.instagram.com/p/{code}/" if code else "",
        "author": (m.get("user") or {}).get("username", ""),
        "media_type": MT.get(m.get("media_type"), "image"),
        "hashtags": re.findall(r"#[\w가-힣]+", cap),
        "text": cap,
        "location": (loc or {}).get("name", "") if isinstance(loc, dict) else "",
        "images": collect_images(m),
    }


# 일시 차단 배너(URL 안 바뀌고 다이얼로그로 뜸 — 2026-07-13 실증). 감지 시 즉시 스크롤 중단.
BLOCK_TEXTS = ("일시적으로 차단", "temporarily blocked", "try again later",
               "나중에 다시 시도", "we restrict certain activity", "action blocked")


def page_blocked(page):
    try:
        t = (page.inner_text("body", timeout=3000) or "")[:4000].lower()
    except Exception:
        return False
    return any(k in t for k in BLOCK_TEXTS)


def load_known_codes():
    """posts.json 의 기존 IG shortcode 집합 (증분 early-stop 용)."""
    p = C.DATA / "posts.json"
    if not p.exists():
        return set()
    out = set()
    for x in json.loads(p.read_text(encoding="utf-8")):
        if x.get("platform") != "instagram":
            continue
        c = x.get("code") or (x.get("post_url") or "").rstrip("/").rsplit("/", 1)[-1]
        if c:
            out.add(c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--incremental", action="store_true",
                    help="새 shortcode 만; 아는 구간 도달 시 조기종료")
    args = ap.parse_args()
    known = load_known_codes() if args.incremental else set()

    from playwright.sync_api import sync_playwright
    C.PROFILE_DIR.mkdir(exist_ok=True)
    pending = []  # (url, Response) 미처리 응답

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(C.PROFILE_DIR), channel="chrome", headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(r):
            if any(k in r.url for k in INTERESTING) and "json" in (r.headers or {}).get("content-type", ""):
                pending.append(r)

        page.on("response", on_response)
        page.goto(SAVED_URL, wait_until="domcontentloaded", timeout=60000)

        deadline = time.monotonic() + C.LOGIN_WAIT_SEC
        ready = False
        while time.monotonic() < deadline:
            if C._is_login_page(page.url):
                print("→ 브라우저에서 로그인해 주세요... (대기 중)")
                page.wait_for_timeout(3000)
                continue
            if "saved" in page.url.lower():
                ready = True
                break
            page.goto(SAVED_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
        if not ready:
            print("[오류] 로그인 확인 실패.")
            ctx.close()
            return 1

        media, no_growth, more_available, retrig, known_streak = {}, 0, True, 0, 0
        MAX_RETRIG = 12
        print("스크롤하며 API 캡처 중..." + (" [증분]" if args.incremental else ""))
        for step in range(args.max_steps):
            if step % 5 == 0 and page_blocked(page):
                print(f"  BLOCK: 차단 배너 감지 → 즉시 중단 (step {step}, {len(media)}개 수집)")
                break
            page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.9))")
            page.wait_for_timeout(1100)
            # 이번까지 쌓인 응답 처리(본문 읽기)
            batch, pending[:] = pending[:], []
            prev_codes = set(media)
            for r in batch:
                try:
                    data = json.loads(r.text())
                except Exception:
                    continue
                if isinstance(data, dict) and "more_available" in data:
                    more_available = bool(data["more_available"])
                found = []
                scan(data, found)
                for m in found:
                    if m.get("code"):
                        media[m["code"]] = m
            grew = len(media) - len(prev_codes)
            new_unknown = sum(1 for c in media if c not in prev_codes and c not in known)
            no_growth = 0 if grew else no_growth + 1
            if grew:
                retrig = 0
                print(f"  step {step+1}: +{grew} → {len(media)}개 (more={more_available})")
            # 증분: 새 shortcode 가 안 나오는 구간 도달 → 조기종료
            if args.incremental:
                known_streak = known_streak + 1 if new_unknown == 0 else 0
                if known_streak >= 4:
                    print(f"  [증분] 기존 구간 도달 → 조기종료 ({len(media)}개 스캔)")
                    break
            # 진짜 소진
            if not more_available and no_growth >= 3:
                print("  more_available=False → 수집 완료")
                break
            # more_available 인데 스톨 → 리트리거(위로 올렸다 맨아래로)
            if no_growth >= 4:
                if retrig >= MAX_RETRIG:
                    print(f"  스톨 재시도 {MAX_RETRIG}회 소진 → 종료 ({len(media)}개)")
                    break
                retrig += 1
                print(f"  스톨 재트리거 {retrig}/{MAX_RETRIG} (현재 {len(media)}, more={more_available})")
                page.evaluate("window.scrollBy(0, -Math.round(window.innerHeight * 2.5))")
                page.wait_for_timeout(1500)
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_timeout(2800)
                no_growth = 0

        # 남은 응답 마저 처리
        for r in pending:
            try:
                data = json.loads(r.text())
            except Exception:
                continue
            found = []
            scan(data, found)
            for m in found:
                if m.get("code"):
                    media[m["code"]] = m

        new_records = [to_record(m) for m in media.values()]
        # 누적: 기존 instagram_posts.json 과 code 기준 병합(신규 우선)
        by_code = {}
        if OUT.exists():
            try:
                for r in json.loads(OUT.read_text(encoding="utf-8")):
                    if r.get("code"):
                        by_code[r["code"]] = r
            except Exception:
                pass
        prev = len(by_code)
        for r in new_records:
            by_code[r["code"]] = r
        records = list(by_code.values())
        OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  (이번 {len(new_records)}개, 기존 {prev}개 → 누적 {len(records)}개)")
        ctx.close()

    # 요약
    car = sum(1 for r in records if r["media_type"] == "carousel")
    vid = sum(1 for r in records if r["media_type"] == "video")
    cap = sum(1 for r in records if r["text"].strip())
    imgs = sum(len(r["images"]) for r in records)
    locs = sum(1 for r in records if r["location"])
    print(f"\n✅ {len(records)}개 → {OUT.name}")
    print(f"   캡션 있음 {cap} · 캐러셀 {car} · 영상 {vid} · 장소 {locs} · 총 이미지 {imgs}")
    print("다음: python download_posts.py instagram_posts.json --referer https://www.instagram.com/ --prefix ig --merge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
