"""
============================================================
 ocr_images.py — 로컬 PaddleOCR로 이미지 텍스트 추출 (Phase 1)
------------------------------------------------------------
 posts.json 의 각 이미지에서 글자를 뽑아 레코드에 ocr_text 로 추가.
   - 로컬·무료(API 0). lang='korean' 모델이 한국어+영어 둘 다 인식.
   - enable_mkldnn=False: paddle 3.x oneDNN 런타임 버그 우회(필수).
   - 매 게시물마다 저장(체크포인트) + 이미 한 건 건너뜀(이어하기).

   python ocr_images.py
============================================================
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("FLAGS_use_mkldnn", "0")  # paddle oneDNN 크래시 우회

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IMAGES = DATA / "images"
STORE = DATA / "posts.json"


def make_ocr(lang="korean"):
    from paddleocr import PaddleOCR
    # 문서 보정용 서브모델은 SNS 이미지엔 불필요 → 끄면 빠름 (파라미터 없으면 폴백)
    # lang="korean"(한국어+영어) 기본, lang="ch"(중국어 한자+병음) 등 지정 가능
    try:
        return PaddleOCR(lang=lang, enable_mkldnn=False,
                         use_doc_orientation_classify=False, use_doc_unwarping=False,
                         use_textline_orientation=False)
    except TypeError:
        return PaddleOCR(lang=lang, enable_mkldnn=False)


def image_text(ocr, path: Path, max_side: int = 1600) -> str:
    # 고해상도 이미지는 server 검출 모델에서 극도로 느림(장당 30초) → 긴 변 max_side 로 다운스케일.
    # SNS 슬라이드 텍스트는 1600px 면 충분히 읽힘.
    from PIL import Image
    import numpy as np
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    res = ocr.predict(np.array(img))
    if not res:
        return ""
    texts = res[0]["rec_texts"]
    return " ".join(texts)


def main() -> int:
    records = json.loads(STORE.read_text(encoding="utf-8"))
    todo = [r for r in records if "ocr_text" not in r]
    if not todo:
        print("모든 레코드에 ocr_text 가 이미 있습니다. (--재추출 원하면 posts.json 의 ocr_text 제거)")
        return 0

    ocr = make_ocr()
    total = sum(len(r.get("local_files", [])) for r in todo)
    done = 0
    for r in records:
        if "ocr_text" in r:  # 이어하기: 이미 처리됨
            continue
        parts = []
        for fn in r.get("local_files", []):
            p = IMAGES / fn
            if not p.exists():
                continue
            done += 1
            try:
                t = image_text(ocr, p)
            except Exception as e:
                t = ""
                print(f"  [{done}/{total}] {fn} OCR 실패: {type(e).__name__}")
            else:
                print(f"  [{done}/{total}] {fn} → {len(t)}자")
            if t:
                parts.append(t)
        r["ocr_text"] = "\n".join(parts)
        STORE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")  # 체크포인트

    n = sum(1 for r in records if r.get("ocr_text"))
    print(f"\n✅ OCR 완료: {n}개 게시물에 ocr_text 채움 → {STORE.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
