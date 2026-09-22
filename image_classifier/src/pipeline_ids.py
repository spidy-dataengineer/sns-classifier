"""게시물 안정 식별자 — 증분 처리(ID 원장)용 공용 키.

posts.json 이 원장 역할을 하며, 각 레코드를 아래 규칙으로 식별한다:
  - LinkedIn: urn:li:activity:{숫자}  (record["id"] 또는 post_url 에서 추출)
  - Instagram: media_id (record["id"] 또는 이미지 CDN 파일명 중간 숫자)
collect(수집)·download(적재) 양쪽이 같은 키를 써야 증분이 어긋나지 않으므로 여기 한 곳에 둔다.
"""
from __future__ import annotations

import re

_IG_MID = re.compile(r"/[0-9]+_([0-9]{10,})_[0-9]+_n\.")
_LI_URN = re.compile(r"urn:li:activity:\d+")


def record_id(rec: dict) -> str | None:
    """레코드의 안정적 식별자. 없으면 post_url/이미지에서 추출."""
    if rec.get("id"):
        return str(rec["id"])
    m = _LI_URN.search(rec.get("post_url", "") or "")
    if m:
        return m.group(0)
    for u in rec.get("images", []) or []:
        m = _IG_MID.search(u)
        if m:
            return m.group(1)
    return None


def ig_media_id(url: str) -> str | None:
    m = _IG_MID.search(url)
    return m.group(1) if m else None


def safe_id(rid: str | None) -> str:
    """파일명용: 'urn:li:activity:123' → '123', media_id 는 그대로."""
    if not rid:
        return ""
    m = re.search(r"(\d{6,})", rid)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9]+", "", rid)
