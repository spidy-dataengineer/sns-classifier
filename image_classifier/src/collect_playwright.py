"""
============================================================
 collect_playwright.py  (Phase 1: 추출·적재)
------------------------------------------------------------
 전용 자동화 크롬 프로필로 저장 게시물을 수집해 "리치 저장소"로 적재.
   - 첫 1회만 브라우저에서 직접 로그인 → 이후 세션 유지(전자동)
   - 코드가 비밀번호를 다루지 않음. 메인 크롬과 분리(.pw-profile).
   - 게시물별로 메타데이터 + 본문 text + 이미지(URL) 추출

 LinkedIn: 셀렉터 확정됨(data-chameleon-result-urn) → 카드별 메타+본문+이미지.
 Instagram: 저장 그리드 썸네일(background-image) 수집. 그리드엔 permalink/캡션이
            없어(모달 방식) 썸네일+OCR 위주. 캡션/URL은 후속(모달 클릭) 과제.

 사용법:
   pip install playwright
   python collect_playwright.py                       # LinkedIn 저장글
   python collect_playwright.py --platform instagram --url "<IG 저장 URL>" --dump-dom

 출력: {platform}_posts.json  (→ download_posts.py → 분류)
 ⚠️ 자동 크롤링은 약관 회색·계정위험. 사람 속도·개인 규모로만.
============================================================
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from pipeline_ids import ig_media_id, record_id

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROFILE_DIR = ROOT / ".pw-profile"
LOGIN_WAIT_SEC = 420

PLATFORMS = {
    "linkedin": {
        "saved_url": "https://www.linkedin.com/my-items/saved-posts/",
        "ready_selector": "[data-chameleon-result-urn]",
    },
    "instagram": {
        # 로그인 유저(instpjh)의 저장 컬렉션. 다른 계정/컬렉션이면 --url 로 덮어씀.
        "saved_url": "https://www.instagram.com/instpjh/saved/all-posts/",
        "ready_selector": None,
    },
}

# LinkedIn 카드별 {id, author, text(정제), images} 추출
LINKEDIN_EXTRACT_JS = r"""
() => {
  const KEEP = (u) => /media\.licdn\.com\/dms\/image\//.test(u) &&
    !/(profile-display|profile-framedphoto|company-logo|ghost|EntityPhoto|static\.licdn|aero-v1|sprite)/i.test(u);
  const imgsIn = (root) => {
    const out = new Set();
    root.querySelectorAll("img").forEach((img) => {
      let b = img.currentSrc || img.src;
      if (img.srcset) {
        const c = img.srcset.split(",").map((s) => { const p = s.trim().split(/\s+/); return { u: p[0], w: parseInt(p[1]) || 0 }; }).sort((a, b) => b.w - a.w);
        if (c[0] && c[0].u) b = c[0].u;
      }
      if (b && b.startsWith("http") && KEEP(b)) out.add(b);
    });
    root.querySelectorAll('[style*="background-image"]').forEach((el) => {
      const m = getComputedStyle(el).backgroundImage.match(/url\(["']?(https?:[^"')]+)["']?\)/);
      if (m && KEEP(m[1])) out.add(m[1]);
    });
    return [...out];
  };
  const body = (raw) => {
    let s = raw;
    const m = s.match(/Visible to (everyone|anyone|your network|connections)\s+/i);
    if (m && m.index < 400) s = s.slice(m.index + m[0].length);
    s = s.replace(/(…|\.\.\.)\s*see more\b.*$/i, "").trim();
    return Array.from(s).slice(0, 4000).join("");
  };
  const authorOf = (raw) => {
    const m = raw.match(/^(?:Status is \w+\s+)?(.+?)\s+View\s/);
    return m ? m[1].trim() : "";
  };
  const out = [];
  document.querySelectorAll("[data-chameleon-result-urn]").forEach((card) => {
    const raw = (card.innerText || "").replace(/\s+/g, " ").trim();
    out.push({
      id: card.getAttribute("data-chameleon-result-urn"),
      author: authorOf(raw),
      text: body(raw),
      images: imgsIn(card),
    });
  });
  return out;
}
"""

# Instagram 저장 그리드: 썸네일은 background-image(및 일부 img). 프로필사진/아이콘 제외.
INSTAGRAM_EXTRACT_JS = r"""
() => {
  const KEEP = (u) => /scontent[^"']*cdninstagram/i.test(u)
    && /t51\.\d+-15\//.test(u);   // 피드(게시물) 이미지만; 프로필사진(-19)·아이콘 제외
  const out = new Set();
  document.querySelectorAll('[style*="background-image"]').forEach((el) => {
    const bg = el.style.backgroundImage || getComputedStyle(el).backgroundImage || "";
    const m = bg.match(/url\(["']?(https?:[^"')]+)["']?\)/);
    if (m && KEEP(m[1])) out.add(m[1]);
  });
  document.querySelectorAll("img").forEach((img) => {
    const u = img.currentSrc || img.src || "";
    if (KEEP(u)) out.add(u);
  });
  return [...out];
}
"""


def linkedin_media_type(images: list[str]) -> str:
    joined = " ".join(images)
    if "videocover" in joined:
        return "video"
    if "articleshare" in joined:
        return "article"
    if "document-cover" in joined:
        return "document"
    if images:
        return "image"
    return "text"


def hashtags(text: str) -> list[str]:
    return re.findall(r"#[\w가-힣]+", text or "")


def enrich_linkedin(rec: dict) -> dict:
    pid = rec.get("id", "")
    return {
        "platform": "linkedin",
        "id": pid,
        "post_url": f"https://www.linkedin.com/feed/update/{pid}/" if pid else "",
        "author": rec.get("author", ""),
        "media_type": linkedin_media_type(rec.get("images", [])),
        "hashtags": hashtags(rec.get("text", "")),
        "text": rec.get("text", ""),
        "images": rec.get("images", []),
    }


def enrich_instagram(url: str) -> dict:
    # 그리드엔 permalink/캡션이 없어 이미지 URL 만 확보. 나머지는 후속(모달) 과제.
    return {
        "platform": "instagram",
        "id": ig_media_id(url) or "",
        "post_url": "",
        "author": "",
        "media_type": "image",
        "hashtags": [],
        "text": "",
        "images": [url],
    }


def _is_login_page(url: str) -> bool:
    return any(k in url for k in ("/login", "/checkpoint", "/authwall", "/uas/login", "/accounts/login", "/signup"))


def _merge(store: dict, recs: list) -> None:
    for rec in recs:
        prev = store.get(rec["id"])
        if not prev or len(rec["images"]) > len(prev["images"]) or len(rec["text"]) > len(prev["text"]):
            store[rec["id"]] = rec


def load_known(platform: str) -> set:
    """posts.json(원장)에서 해당 플랫폼의 이미 처리된 ID 집합 → 증분 시 skip 용."""
    p = DATA / "posts.json"
    if not p.exists():
        return set()
    try:
        recs = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {rid for r in recs if r.get("platform") == platform
            for rid in [record_id(r)] if rid}


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 추출: 저장글 → 리치 저장소")
    ap.add_argument("--platform", choices=list(PLATFORMS), default="linkedin")
    ap.add_argument("--url", default=None, help="저장 페이지 URL 직접 지정(IG 등)")
    ap.add_argument("--dump-dom", action="store_true", help="셀렉터 확인용으로 페이지 HTML 덤프")
    ap.add_argument("--incremental", action="store_true", help="posts.json 에 이미 있는 항목은 건너뛰고 새 항목만(조기종료)")
    args = ap.parse_args()

    cfg = PLATFORMS[args.platform]
    saved_url = args.url or cfg["saved_url"]
    out_path = DATA / f"{args.platform}_posts.json"

    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), channel="chrome", headless=False,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(saved_url, wait_until="domcontentloaded", timeout=60000)

        # 첫 1회 로그인 대기
        ready_sel = cfg["ready_selector"]
        ready = False
        deadline = time.monotonic() + LOGIN_WAIT_SEC
        while time.monotonic() < deadline:
            url = page.url
            if _is_login_page(url):
                print("→ 브라우저 창에서 로그인해 주세요... (대기 중)")
                page.wait_for_timeout(3000)
                continue
            if ready_sel is None:
                # IG: 저장 컬렉션(URL에 saved) 도달하면 준비 완료. 아니면 saved_url 로 이동 시도.
                if "saved" in url.lower():
                    ready = True
                    break
                page.goto(saved_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                continue
            if page.locator(ready_sel).count() > 0:
                ready = True
                break
            if "/my-items/saved-posts" not in url:  # 로그인은 됐는데 저장페이지가 아니면 이동
                page.goto(saved_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

        if not ready:
            print("[오류] 로그인이 확인되지 않았습니다(제한시간 초과). 창에서 로그인 후 다시 실행하세요.")
            ctx.close()
            return 1

        if args.dump_dom:
            for _ in range(5):  # 저장 항목이 로드되도록 몇 번 스크롤
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(1500)
            dom_path = DATA / f"{args.platform}_dom.html"
            dom_path.write_text(page.content(), encoding="utf-8")
            shot = DATA / f"{args.platform}_dom.png"
            page.screenshot(path=str(shot), full_page=False)
            # 후보 셀렉터 카운트(진단)
            cands = ["[data-chameleon-result-urn]", "article", "main article", "div[role=button] img",
                     "a[href*='/p/']", "a[href*='/reel/']", "a[href*='/p/'] img", "main ul li", "img"]
            counts = {s: page.locator(s).count() for s in cands}
            print(f"[덤프] {dom_path.name}, {shot.name}")
            print("SELECTOR_COUNTS:", json.dumps(counts))
            print("→ 이 두 파일/카운트를 공유해주시면 셀렉터를 확정해 추출을 완성합니다.")
            ctx.close()
            return 0

        if args.platform == "instagram":
            print("추출 시작 (IG 썸네일 수집)" + (" [증분]" if args.incremental else ""))
            known = load_known("instagram") if args.incremental else set()
            seen: dict[str, str] = {}
            before, last_h, stable, steps, known_streak = 0, -1, 0, 0, 0
            while stable < 6 and steps < 400:
                batch_new = 0
                for u in page.evaluate(INSTAGRAM_EXTRACT_JS):
                    k = ig_media_id(u) or u.split("?")[0].rsplit("/", 1)[-1]  # 안정적 media_id
                    if k in known:  # 증분: 이미 처리됨
                        continue
                    if k not in seen:
                        batch_new += 1
                    if k not in seen or ("e35" in u and "e35" not in seen[k]):  # 고해상도(e35) 우선
                        seen[k] = u
                if args.incremental and batch_new == 0:
                    known_streak += 1
                    if known_streak >= 4:
                        print("  [증분] 기존 항목 구간 도달 → 조기 종료")
                        break
                else:
                    known_streak = 0
                page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.9))")
                page.wait_for_timeout(1000)
                h = page.evaluate("document.documentElement.scrollHeight")
                at_bottom = page.evaluate("(window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 80)")
                stable = stable + 1 if (abs(h - last_h) < 5 and at_bottom) else 0
                last_h = h
                steps += 1
                if len(seen) != before:
                    print(f"  ...{len(seen)} thumbs")
                    before = len(seen)
            records = [enrich_instagram(u) for u in seen.values()]
            out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n✅ IG 썸네일 {len(records)}개 → {out_path.name}")
            print("다음: python download_posts.py " + out_path.name)
            ctx.close()
            return 0

        # 스크롤하며 추출·누적 (가상화 대응)
        print("추출 시작 (스크롤 수집)" + (" [증분]" if args.incremental else ""))
        known = load_known("linkedin") if args.incremental else set()
        store: dict[str, dict] = {}
        last_h, stable, steps, before, known_streak = -1, 0, 0, 0, 0
        while stable < 6 and steps < 300:
            recs = page.evaluate(LINKEDIN_EXTRACT_JS)
            fresh = [r for r in recs if r["id"] not in known]
            _merge(store, fresh)
            if args.incremental and recs and not fresh:  # 화면이 전부 기존 항목
                known_streak += 1
                if known_streak >= 3:
                    print("  [증분] 기존 항목 구간 도달 → 조기 종료")
                    break
            else:
                known_streak = 0
            page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.9))")
            page.wait_for_timeout(900)
            h = page.evaluate("document.documentElement.scrollHeight")
            at_bottom = page.evaluate("(window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 80)")
            stable = stable + 1 if (abs(h - last_h) < 5 and at_bottom) else 0
            last_h = h
            steps += 1
            if len(store) != before:
                print(f"  ...{len(store)} posts")
                before = len(store)
        _merge(store, [r for r in page.evaluate(LINKEDIN_EXTRACT_JS) if r["id"] not in known])

        records = [enrich_linkedin(r) for r in store.values() if r["images"] or r["text"]]
        out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        nimg = sum(len(r["images"]) for r in records)
        mt = {}
        for r in records:
            mt[r["media_type"]] = mt.get(r["media_type"], 0) + 1
        print(f"\n✅ 게시물 {len(records)}개, 이미지 {nimg}개 → {out_path.name}")
        print(f"   미디어타입: {mt}")
        print("다음: python download_posts.py " + out_path.name)
        ctx.close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
