"""노션 페이지 본문의 오염된 '자막(전사)' 블록 삭제 + '자막있음' False 로 정정.

2026-07-21 그리드 모드 오염(엉뚱한 영상 자막이 붙음)으로 posts.json 은 정화했으나,
과거 sync 로 노션 페이지 본문에 이미 들어간 오염 자막 code-block 은 남아있음.
OCR 텍스트 블록과 속성(제목/요약/분류)은 건드리지 않고, '자막(전사)' 헤딩+코드블록만 제거.

입력: data/.tmp/notion_contam.json  ([{url, cat}])
  python src/notion_fix_contam.py
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
H = {"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
     "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
MAP = json.loads((ROOT / "data" / "notion_map.json").read_text(encoding="utf-8"))
TARGETS = json.loads((ROOT / "data" / ".tmp" / "notion_contam.json").read_text(encoding="utf-8"))


def _plain(rt):
    return "".join(x.get("plain_text", "") for x in (rt or []))


def main():
    fixed = missing = 0
    for t in TARGETS:
        db = MAP[t["cat"]]["database_id"]
        q = requests.post(f"https://api.notion.com/v1/databases/{db}/query", headers=H,
                          json={"filter": {"property": "Source URL", "url": {"equals": t["url"]}}}).json()
        if not q.get("results"):
            missing += 1
            continue
        pid = q["results"][0]["id"]
        blocks = requests.get(f"https://api.notion.com/v1/blocks/{pid}/children", headers=H).json().get("results", [])
        # '자막(전사)' 헤딩과 그 바로 다음 code 블록을 삭제 대상으로
        to_del = []
        for i, b in enumerate(blocks):
            if b.get("type") == "heading_3" and _plain(b["heading_3"]["rich_text"]).strip() == "자막(전사)":
                to_del.append(b["id"])
                if i + 1 < len(blocks) and blocks[i + 1].get("type") == "code":
                    to_del.append(blocks[i + 1]["id"])
        for bid in to_del:
            requests.delete(f"https://api.notion.com/v1/blocks/{bid}", headers=H)
            time.sleep(0.34)
        # 자막있음 False
        requests.patch(f"https://api.notion.com/v1/pages/{pid}", headers=H,
                       json={"properties": {"자막있음": {"checkbox": False}}})
        time.sleep(0.34)
        fixed += 1
        if fixed % 10 == 0:
            print(f"  ...{fixed}/{len(TARGETS)}", flush=True)
    print(f"완료: {fixed}개 페이지 오염 자막 블록 삭제 + 자막있음 False | 노션에 없던 것 {missing}", flush=True)


if __name__ == "__main__":
    main()
