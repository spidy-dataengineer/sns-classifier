r"""Phase 3 — Notion 적재 (REST API, 자동화용).

posts.json 의 분류 결과를 카테고리별 Notion DB 에 **업서트**한다.
- 인증: .env 의 NOTION_TOKEN (내부 integration 토큰). MCP 아님 → 무인 스케줄 가능.
- 멱등 업서트: Source URL 을 유니크 키로
    · 신규 URL       → 페이지 생성 (OCR/자막 본문 포함)
    · 기존 URL·변경  → 기본 속성 갱신 (제목/유형/한글설명/태그/자막있음)
    · 전사 새로 도착  → 본문에 '자막' 코드블록 append (자막있음 False→True 전이 시 1회)
    · 변경 없음       → 스킵 (불필요한 재기록 안 함)
- 학습 컬럼(표현/뜻(KO)/예문(EN)/예문(KO)/Style, Phase 4)은 **건드리지 않는다**.

파이프라인이 증분으로 데이터를 뽑을 때마다 재실행하면 델타만 반영된다.

사용:
  python src\notion_sync.py --category 영어공부 --dry-run   # 계획만(신규/갱신/스킵 건수)
  python src\notion_sync.py --category 영어공부 [--limit N]  # 실제 반영

카테고리→DB 매핑은 data/notion_map.json.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = os.environ.get("NOTION_TOKEN")
if not TOKEN:
    sys.exit("NOTION_TOKEN 이 .env 에 없습니다. RUNBOOK Phase 3 참고.")

BASE = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

POSTS = ROOT / "data" / "posts.json"
MAP = ROOT / "data" / "notion_map.json"

MAX = 2000  # Notion rich_text 텍스트 조각 최대 길이


# ---------------- REST helpers ----------------
def _req(method, path, **kw):
    r = requests.request(method, f"{BASE}{path}", headers=HEADERS, **kw)
    if r.status_code == 429:
        time.sleep(float(r.headers.get("Retry-After", 1)))
        return _req(method, path, **kw)
    if not r.ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json()


def get_database(db_id):
    return _req("GET", f"/databases/{db_id}")


def add_properties(db_id, props):
    return _req("PATCH", f"/databases/{db_id}", json={"properties": props})


def _plain(rt_list):
    return "".join(t.get("plain_text", "") for t in (rt_list or []))


def existing_pages(db_id):
    """{Source URL: {page_id, cur:{제목,유형,한글설명,태그,자막있음}}} — 업서트 diff 용."""
    out, cursor = {}, None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = _req("POST", f"/databases/{db_id}/query", json=body)
        for pg in data["results"]:
            p = pg["properties"]
            url = p.get("Source URL", {}).get("url")
            if not url:
                continue
            sel = p.get("유형", {}).get("select")
            out[url] = {
                "page_id": pg["id"],
                "cur": {
                    "제목": _plain(p.get("제목", {}).get("title")),
                    "유형": (sel or {}).get("name"),
                    "한글설명": _plain(p.get("한글설명", {}).get("rich_text")),
                    "태그": _plain(p.get("태그", {}).get("rich_text")),
                    "자막있음": bool(p.get("자막있음", {}).get("checkbox")),
                },
            }
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]


def create_page(db_id, properties, children):
    return _req("POST", "/pages", json={
        "parent": {"database_id": db_id}, "properties": properties, "children": children})


def update_props(page_id, properties):
    return _req("PATCH", f"/pages/{page_id}", json={"properties": properties})


def append_children(page_id, children):
    return _req("PATCH", f"/blocks/{page_id}/children", json={"children": children})


def archive_page(page_id):
    return _req("PATCH", f"/pages/{page_id}", json={"archived": True})


# ---------------- value builders ----------------
def _chunks(s, n=MAX):
    s = s or ""
    return [s[i:i + n] for i in range(0, len(s), n)] or [""]


def rt(s):
    return [{"type": "text", "text": {"content": c}} for c in _chunks(str(s or ""))]


def code_block(text):
    return {"object": "block", "type": "code",
            "code": {"language": "plain text",
                     "rich_text": [{"type": "text", "text": {"content": c}} for c in _chunks(text)]}}


def h3(text):
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": rt(text)}}


def link_para(url):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text",
                                         "text": {"content": "원문 링크", "link": {"url": url}}}]}}


def base_to_props(b):
    """diff용 base dict → Notion 속성 payload."""
    return {
        "제목": {"title": rt(b["제목"])},
        "유형": {"select": ({"name": b["유형"]} if b["유형"] else None)},
        "한글설명": {"rich_text": rt(b["한글설명"])},
        "태그": {"rich_text": rt(b["태그"])},
        "자막있음": {"checkbox": b["자막있음"]},
        "Source URL": {"url": (b["Source URL"] or None)},
    }


# ---------------- English builder ----------------
RULES = [
    ("문법", ["문법", "패턴", "시제", "전치사"]),
    ("단어·어휘", ["단어", "어휘"]),
    ("구동사·숙어", ["구동사", "phrasal", "숙어", "관용"]),
    ("슬랭·밈", ["슬랭", "밈", "유머", "구어체", "신조어"]),
    ("비즈니스·직장", ["비즈니스", "직장", "인터뷰", "이메일", "회사"]),
    ("상황·미디어", ["여행", "주문", "레스토랑", "연애", "미드", "영화", "인사"]),
    ("표현·뉘앙스", ["표현", "원어민", "감정", "뉘앙스", "번역"]),
    ("회화", ["회화", "일상", "생활", "실전", "실용", "실생활"]),
]
STYLE_OPTIONS = ["everyday", "business", "advanced-nuance", "situational", "idiom", "slang", "academic"]

ENGLISH_PROPS = {
    "유형": {"select": {"options": [{"name": n, "color": c} for n, c in [
        ("표현·뉘앙스", "blue"), ("구동사·숙어", "green"), ("비즈니스·직장", "orange"),
        ("슬랭·밈", "pink"), ("상황·미디어", "purple"), ("단어·어휘", "yellow"),
        ("회화", "red"), ("문법", "brown"), ("기타", "gray")]]}},
    "한글설명": {"rich_text": {}},
    "태그": {"rich_text": {}},
    "자막있음": {"checkbox": {}},
    "Source URL": {"url": {}},
    "표현": {"rich_text": {}},
    "뜻(KO)": {"rich_text": {}},
    "예문(EN)": {"rich_text": {}},
    "예문(KO)": {"rich_text": {}},
    "Style": {"select": {"options": [{"name": s} for s in STYLE_OPTIONS]}},
}


def pick_type(tags):
    joined = " ".join(tags)
    for name, kws in RULES:
        if any(k in joined for k in kws):
            return name
    return "기타"


def first_line(s):
    s = (s or "").strip()
    return re.split(r"[\r\n]", s)[0].strip() if s else ""


def first_sentence(s):
    s = (s or "").strip()
    return re.split(r"(?<=[.!?。！？])\s", s)[0].strip() if s else ""


def make_title(x):
    v = first_line(x.get("ocr_text"))
    if len(v) >= 4:
        return v[:90]
    v = first_sentence(x.get("summary"))
    return (v or (x.get("post_url") or "제목 없음")[-40:])[:90]


def build_english(x):
    """returns (base_dict, children_blocks)."""
    tags = [t for t in (x.get("tech_tags") or []) if str(t).strip()]
    base = {
        "제목": make_title(x),
        "유형": pick_type(tags),
        "한글설명": (x.get("summary") or "")[:MAX],
        "태그": ", ".join(tags)[:MAX],
        "자막있음": bool((x.get("transcript") or "").strip()),
        "Source URL": x.get("post_url"),
    }
    children = []
    ocr = (x.get("ocr_text") or "").strip()
    if ocr:
        children += [h3("OCR 텍스트"), code_block(ocr)]
    tr = (x.get("transcript") or "").strip()
    if tr:
        children += [h3("자막(전사)"), code_block(tr)]
    if x.get("post_url"):
        children.append(link_para(x["post_url"]))
    return base, children


def transcript_blocks(x):
    tr = (x.get("transcript") or "").strip()
    return [h3("자막(전사)"), code_block(tr)] if tr else []


# ---------------- Chinese builder ----------------
ZH_RULES = [
    ("여행·상황", ["여행", "식당", "카페", "주문", "체크인", "상하이", "중국여행", "현지식", "음식"]),
    ("발음·병음", ["병음", "발음", "성조"]),
    ("단어", ["단어", "어휘"]),
    ("표현", ["표현", "애정", "반응", "약속", "대답", "기초", "감정"]),
    ("회화", ["회화", "일상", "생활", "실전", "실용", "중드", "드라마"]),
]

CHINESE_PROPS = {
    "유형": {"select": {"options": [{"name": n, "color": c} for n, c in [
        ("회화", "red"), ("여행·상황", "purple"), ("표현", "blue"),
        ("발음·병음", "yellow"), ("단어", "green"), ("기타", "gray")]]}},
    "한자": {"rich_text": {}},
    "병음": {"rich_text": {}},
    "뜻(KO)": {"rich_text": {}},
    "예문": {"rich_text": {}},
    "한글설명": {"rich_text": {}},
    "태그": {"rich_text": {}},
    "자막있음": {"checkbox": {}},
    "Source URL": {"url": {}},
}


def pick_type_zh(tags):
    joined = " ".join(tags)
    for name, kws in ZH_RULES:
        if any(k in joined for k in kws):
            return name
    return "기타"


def build_chinese(x):
    """returns (base_dict, children). 학습 컬럼(한자/병음/뜻/예문)은 Phase 4 에서 채움."""
    tags = [t for t in (x.get("tech_tags") or []) if str(t).strip()]
    base = {
        "제목": make_title(x),
        "유형": pick_type_zh(tags),
        "한글설명": (x.get("summary") or "")[:MAX],
        "태그": ", ".join(tags)[:MAX],
        "자막있음": bool((x.get("transcript") or "").strip()),
        "Source URL": x.get("post_url"),
    }
    children = []
    ocr = (x.get("ocr_text") or "").strip()
    if ocr:
        children += [h3("OCR 텍스트"), code_block(ocr)]
    tr = (x.get("transcript") or "").strip()
    if tr:
        children += [h3("자막(전사)"), code_block(tr)]
    if x.get("post_url"):
        children.append(link_para(x["post_url"]))
    return base, children


# ---------------- 수집형 builder (주식·AI·운동 등: 갤러리형, 학습 컬럼 없음) ----------------
COLLECT_PROPS = {
    "유형": {"select": {"options": [{"name": n} for n in ["영상", "사진", "여러장", "기타"]]}},
    "한글설명": {"rich_text": {}},
    "태그": {"rich_text": {}},
    "자막있음": {"checkbox": {}},
    "Source URL": {"url": {}},
}
_MEDIA_KO = {"video": "영상", "carousel": "여러장", "image": "사진"}


def build_collect(x):
    """수집형: 제목/요약/태그/링크 + OCR·자막 본문. '유형'은 미디어 종류."""
    tags = [t for t in (x.get("tech_tags") or []) if str(t).strip()]
    base = {
        "제목": make_title(x),
        "유형": _MEDIA_KO.get(x.get("media_type"), "기타"),
        "한글설명": (x.get("summary") or "")[:MAX],
        "태그": ", ".join(tags)[:MAX],
        "자막있음": bool((x.get("transcript") or "").strip()),
        "Source URL": x.get("post_url"),
    }
    children = []
    ocr = (x.get("ocr_text") or "").strip()
    if ocr:
        children += [h3("OCR 텍스트"), code_block(ocr)]
    tr = (x.get("transcript") or "").strip()
    if tr:
        children += [h3("자막(전사)"), code_block(tr)]
    if x.get("post_url"):
        children.append(link_para(x["post_url"]))
    return base, children


BUILDERS = {
    "영어공부": (build_english, ENGLISH_PROPS),
    "중국어공부": (build_chinese, CHINESE_PROPS),
    "주식·투자": (build_collect, COLLECT_PROPS),
    "AI": (build_collect, COLLECT_PROPS),
    "운동·스포츠": (build_collect, COLLECT_PROPS),
    "기타": (build_collect, COLLECT_PROPS),
    "여행": (build_collect, COLLECT_PROPS),
    "밈·유머": (build_collect, COLLECT_PROPS),
    "영화·드라마·엔터": (build_collect, COLLECT_PROPS),
    "커리어·자기계발": (build_collect, COLLECT_PROPS),
    "건강·뷰티·패션": (build_collect, COLLECT_PROPS),
    "IT·개발": (build_collect, COLLECT_PROPS),
    "문화·시사": (build_collect, COLLECT_PROPS),
    "데이터엔지니어링": (build_collect, COLLECT_PROPS),
    "AI·LLM·에이전트": (build_collect, COLLECT_PROPS),
    "채용·이직·커리어": (build_collect, COLLECT_PROPS),
    "제품·도구홍보": (build_collect, COLLECT_PROPS),
}
DIFF_KEYS = ("제목", "유형", "한글설명", "태그", "자막있음")


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true", help="현재 카테고리에 없는 기존 행 아카이브")
    args = ap.parse_args()

    cat = args.category
    if cat not in BUILDERS:
        sys.exit(f"'{cat}' 빌더 미구현. 지원: {list(BUILDERS)}")
    db_id = json.loads(MAP.read_text(encoding="utf-8"))[cat]["database_id"]
    build, schema = BUILDERS[cat]

    posts = [x for x in json.loads(POSTS.read_text(encoding="utf-8")) if x.get("category") == cat]
    print(f"[{cat}] posts.json 대상 {len(posts)}건, DB={db_id}")

    if not args.dry_run:
        add_properties(db_id, schema)
    have = existing_pages(db_id)
    print(f"기존 DB 행 {len(have)}건")

    plan = {"new": [], "update": [], "add_transcript": [], "skip": 0}
    skipped_no_url = 0
    for x in posts:
        url = x.get("post_url")
        if not url:  # 주소 없으면 Source URL(유니크 키) 부재 → 매 실행마다 중복 생성됨. 스킵.
            skipped_no_url += 1
            continue
        base, _ = build(x)
        ex = have.get(url)
        if ex is None:
            plan["new"].append(x)
            continue
        changed = any(base[k] != ex["cur"][k] for k in DIFF_KEYS)
        newly_transcript = base["자막있음"] and not ex["cur"]["자막있음"]
        if changed:
            plan["update"].append((x, ex["page_id"], newly_transcript))
        elif newly_transcript:
            plan["add_transcript"].append((x, ex["page_id"]))
        else:
            plan["skip"] += 1

    current_urls = {x.get("post_url") for x in posts}
    stale = [(u, info["page_id"]) for u, info in have.items() if u not in current_urls]

    print(f"계획 → 신규 {len(plan['new'])} · 갱신 {len(plan['update'])} · "
          f"자막추가 {len(plan['add_transcript'])} · 변경없음 {plan['skip']} · "
          f"제거대상 {len(stale)}{' (--prune 시 아카이브)' if not args.prune else ''}"
          + (f" · 주소없어 스킵 {skipped_no_url}" if skipped_no_url else ""))

    if args.dry_run:
        for x in plan["new"][:3]:
            print("  [신규]", build(x)[0]["제목"][:50])
        for x, _, _ in plan["update"][:3]:
            print("  [갱신]", build(x)[0]["제목"][:50])
        return

    lim = args.limit or 10 ** 9
    done = 0
    # 신규 생성
    for x in plan["new"]:
        if done >= lim:
            break
        base, children = build(x)
        create_page(db_id, base_to_props(base), children)
        done += 1
        time.sleep(0.34)
    # 기존 갱신 (+ 필요 시 자막 append)
    for x, pid, newly in plan["update"]:
        if done >= lim:
            break
        base, _ = build(x)
        update_props(pid, base_to_props(base))
        if newly:
            blk = transcript_blocks(x)
            if blk:
                append_children(pid, blk)
        done += 1
        time.sleep(0.34)
    # 자막만 도착한 기존 행
    for x, pid in plan["add_transcript"]:
        if done >= lim:
            break
        blk = transcript_blocks(x)
        if blk:
            append_children(pid, blk)
        done += 1
        time.sleep(0.34)
    pruned = 0
    if args.prune:
        for _, pid in stale:
            archive_page(pid)
            pruned += 1
            time.sleep(0.34)
    print(f"반영 완료: {done}건 처리" + (f", {pruned}건 아카이브(prune)." if args.prune else "."))


if __name__ == "__main__":
    main()
