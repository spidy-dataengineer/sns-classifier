r"""쿠키만으로 저장글 수집 — 브라우저·GUI 없이 HTTP 요청만 사용.

collect_playwright.py / ig_saved_api.py 를 대체한다. 크롬을 띄워 스크롤하는 대신
두 플랫폼의 내부 API 를 쿠키로 직접 호출한다. 출력 파일 모양은 기존과 동일해서
download_posts.py 이후 단계는 그대로 쓴다.

  Instagram : GET /api/v1/feed/saved/posts/   (max_id 로 페이지 넘김)
  LinkedIn  : GET /voyager/api/graphql SEARCH_MY_ITEMS_SAVED_POSTS
              (직전 응답의 paginationToken 을 같이 넘겨야 다음 페이지가 나옴)

Instagram 은 전체 훑기를 하지 않는다 — 항상 증분(fetch_instagram 주석 참고).

쿠키 출처: IG_COOKIE / LI_COOKIE 환경변수(쿠키 헤더 문자열)를 먼저 본다.
없으면 로컬 .pw-profile 에서 읽는다(이때만 playwright 필요).
쿠키 수명은 약 1년 — 만료되면 브라우저에서 한 번 로그인 후 다시 뽑는다.

실행:
  python src/collect_cookie.py                          # 두 플랫폼
  python src/collect_cookie.py --platform linkedin --incremental
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import collect_playwright as C  # DATA, PROFILE_DIR, enrich_linkedin, load_known
from ig_saved_api import load_known_codes, scan, to_record

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
IG_APP_ID = "936619743392459"   # 웹 인스타그램이 쓰는 고정값
LI_QUERY_ID = "voyagerSearchDashClusters.e438ab99259203e9c1cd3f358e217282"
PAGE_SLEEP = 1.2                # 사람 속도로 — 연속 호출 자제


def cookie_header(platform: str) -> str:
    """환경변수 우선, 없으면 .pw-profile 에서 추출."""
    env = os.environ.get("IG_COOKIE" if platform == "instagram" else "LI_COOKIE", "").strip()
    if env:
        return env
    from playwright.sync_api import sync_playwright
    domain = "instagram" if platform == "instagram" else "linkedin"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(C.PROFILE_DIR), channel="chrome", headless=True)
        pairs = [(c["name"], c["value"]) for c in ctx.cookies() if domain in c["domain"]]
        ctx.close()
    return "; ".join(f"{k}={v}" for k, v in pairs)


def cookie_dict(header: str) -> dict:
    out = {}
    for part in header.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_instagram() -> list:
    """항상 증분. Instagram 은 전체 훑기를 자동화로 감지한다.

    2026-09-21 에 953건 전체를 짧은 간격으로 두 번 받다가 계정에 자동화 경고
    (accounts/scraping_warning)가 걸려 API 가 checkpoint_required 로 막혔다.
    경고문에 영구 비활성화 가능성이 적혀 있어 전체 훑기 경로를 없앴다.
    원장(instagram_posts.json)에 이미 전량이 있으므로 다시 받을 일도 없다.
    """
    ck = cookie_dict(cookie_header("instagram"))
    if "sessionid" not in ck:
        print("[IG] sessionid 쿠키 없음 — 로그인 만료. 브라우저에서 로그인 후 재시도.")
        return []
    headers = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in ck.items()),
        "User-Agent": UA,
        "X-IG-App-ID": IG_APP_ID,
        "X-CSRFToken": ck.get("csrftoken", ""),
        "Referer": "https://www.instagram.com/",
    }
    known = load_known_codes()
    media, max_id = {}, None
    for page in range(60):
        url = "https://www.instagram.com/api/v1/feed/saved/posts/?count=50"
        if max_id:
            url += "&max_id=" + urllib.parse.quote(str(max_id))
        try:
            data = get_json(url, headers)
        except urllib.error.HTTPError as e:
            # 전체 훑기를 짧은 간격으로 반복하면 400 이 뜬다(2026-09-21 실측).
            # 받아둔 페이지는 살리고 멈춘다 — 다음 실행에서 이어서 받는다.
            print(f"  [IG] page {page + 1} HTTP {e.code} -> 중단 ({len(media)}건 유지)")
            break
        found = []
        scan(data, found)
        fresh = 0
        for m in found:
            code = m.get("code")
            if code and code not in media:
                media[code] = m
                if code not in known:
                    fresh += 1
        more = bool(data.get("more_available"))
        max_id = data.get("next_max_id")
        print(f"  [IG] page {page + 1}: {len(found)}건 (신규 {fresh}) -> 누적 {len(media)}")
        if fresh == 0:
            print("  [IG] 기존 구간 도달 -> 조기종료")
            break
        if not more or not max_id:
            break
        time.sleep(PAGE_SLEEP)
    return [to_record(m) for m in media.values()]


def li_images(entity: dict) -> list:
    """게시물에 붙은 미디어 URL. 작성자 프로필 사진(entity['image'])은 제외."""
    out = []
    img = (entity.get("entityEmbeddedObject") or {}).get("image") or {}
    for attr in img.get("attributes") or []:
        detail = attr.get("detailData") or {}
        vec = detail.get("vectorImage")
        if vec:
            arts = sorted(vec.get("artifacts") or [], key=lambda a: a.get("width") or 0)
            if arts:
                out.append((vec.get("rootUrl") or "") + arts[-1]["fileIdentifyingUrlPathSegment"])
        url = (detail.get("imageUrl") or {}).get("url")
        if url:
            out.append(url)
    return out


def li_text(entity: dict) -> str:
    """본문 + 공유된 링크·문서의 제목과 출처. DOM 수집기도 이 둘을 붙였다."""
    body = ((entity.get("summary") or {}).get("text") or "").strip()
    parts = [body]
    embedded = entity.get("entityEmbeddedObject") or {}
    for key in ("title", "primarySubtitle"):
        t = ((embedded.get(key) or {}).get("text") or "").strip()
        if t and t not in body:
            parts.append(t)
    return " ".join(p for p in parts if p)


def li_record(entity: dict) -> dict:
    return C.enrich_linkedin({
        "id": entity.get("trackingUrn") or "",
        "author": (entity.get("title") or {}).get("text") or "",
        "text": li_text(entity),
        "images": li_images(entity),
    })


def fetch_linkedin(incremental: bool) -> list:
    ck = cookie_dict(cookie_header("linkedin"))
    if "li_at" not in ck:
        print("[LI] li_at 쿠키 없음 — 로그인 만료. 브라우저에서 로그인 후 재시도.")
        return []
    headers = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in ck.items()),
        "csrf-token": ck.get("JSESSIONID", "").strip('"'),
        "User-Agent": UA,
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "Referer": "https://www.linkedin.com/my-items/saved-posts/",
    }
    known = C.load_known("linkedin") if incremental else set()
    records, seen, start, token = [], set(), 0, None
    for page in range(30):
        variables = (f"(start:{start}," + (f"paginationToken:{token}," if token else "")
                     + "query:(flagshipSearchIntent:SEARCH_MY_ITEMS_SAVED_POSTS))")
        url = ("https://www.linkedin.com/voyager/api/graphql?variables="
               + urllib.parse.quote(variables, safe="(),:") + "&queryId=" + LI_QUERY_ID)
        data = get_json(url, headers)
        ents = [x for x in (data.get("included") or [])
                if "EntityResultViewModel" in (x.get("$type") or "")]
        token = ((((data.get("data") or {}).get("data") or {})
                  .get("searchDashClustersByAll") or {}).get("metadata") or {}).get("paginationToken")
        fresh = 0
        for e in ents:
            urn = e.get("trackingUrn") or ""
            if not urn or urn in seen:
                continue
            seen.add(urn)
            if urn in known:
                continue
            records.append(li_record(e))
            fresh += 1
        print(f"  [LI] page {page + 1}: {len(ents)}건 (신규 {fresh}) -> 누적 {len(records)}")
        if not ents:
            break
        if incremental and fresh == 0:
            print("  [LI] 기존 구간 도달 -> 조기종료")
            break
        start += len(ents)
        if not token:
            break
        time.sleep(PAGE_SLEEP)
    return [r for r in records if r["images"] or r["text"]]


def save_instagram(records: list) -> None:
    """기존 instagram_posts.json 과 code 기준 병합(신규 우선) — ig_saved_api 와 동일."""
    out = C.DATA / "instagram_posts.json"
    by_code = {}
    if out.exists():
        for r in json.loads(out.read_text(encoding="utf-8")):
            if r.get("code"):
                by_code[r["code"]] = r
    prev = len(by_code)
    for r in records:
        by_code[r["code"]] = r
    merged = list(by_code.values())
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"IG 이번 {len(records)}개, 기존 {prev}개 -> 누적 {len(merged)}개 ({out.name})")


def save_linkedin(records: list) -> None:
    out = C.DATA / "linkedin_posts.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    mt = {}
    for r in records:
        mt[r["media_type"]] = mt.get(r["media_type"], 0) + 1
    print(f"LinkedIn {len(records)}개 -> {out.name} | 미디어타입: {mt}")


def main() -> int:
    ap = argparse.ArgumentParser(description="쿠키 기반 저장글 수집(브라우저 없음)")
    ap.add_argument("--platform", choices=["instagram", "linkedin", "both"], default="both")
    ap.add_argument("--incremental", action="store_true",
                    help="LinkedIn: 이미 있는 항목 구간에 닿으면 조기종료. "
                         "Instagram 은 이 옵션과 무관하게 항상 증분")
    ap.add_argument("--dry-run", action="store_true", help="파일 쓰지 않고 건수만 출력")
    args = ap.parse_args()

    if args.platform in ("instagram", "both"):
        recs = fetch_instagram()
        if args.dry_run:
            print(f"[dry-run] IG {len(recs)}건")
        elif recs:
            save_instagram(recs)
    if args.platform in ("linkedin", "both"):
        recs = fetch_linkedin(args.incremental)
        if args.dry_run:
            print(f"[dry-run] LinkedIn {len(recs)}건")
        else:
            save_linkedin(recs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
