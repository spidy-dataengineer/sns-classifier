"""릴스 전사 (페이스 배치, 사람처럼 천천히).

오디오 캡처: 재생에 의존하지 않고 **IG API 응답 본문(JSON)에서 progressive(오디오 포함)
             영상 URL(`/o1/v/t2/`)을 파싱** — 응답 객체를 모아두었다가 dwell 후 메인 흐름에서 .text() 로 읽음.
             네트워크로 잡힌 스트림도 후보로 합쳐 오디오 있는 것 나올 때까지 시도.
매칭 키: shortcode(permalink). 커버 media_id 는 best-effort.
재시도: 실패는 attempts 카운트로 저장 → 다음 실행 때 재시도(3회까지). 성공은 transcript 저장.

페이싱: 랜덤지연 · 매 10개 긴쉼 · 회당 --limit · 챌린지 감지 중단.

  python reel_transcribe.py --limit 50
  python reel_transcribe.py --limit 5     # 검증
"""
import sys, json, os, re, subprocess, time, random, argparse, imageio_ffmpeg
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from faster_whisper import WhisperModel
from ocr_images import make_ocr, image_text  # 영상 프레임 OCR용 (화면 텍스트/자막)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCRATCH = DATA / ".tmp"          # 중간 wav/프레임(삭제됨)
SCRATCH.mkdir(parents=True, exist_ok=True)
PROFILE = ROOT / ".pw-profile"
SAVED = "https://www.instagram.com/instpjh/saved/all-posts/"
OUT = DATA / "reel_transcripts.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
ff = imageio_ffmpeg.get_ffmpeg_exe()
MID_RE = re.compile(r"/[0-9]+_([0-9]{10,})_[0-9]+_n\.")
PROG_RE = re.compile(r"https://[^\"\\ ]+?/o1/v/t2/[^\"\\ ]+")
MAX_ATTEMPTS = 3

DOM_IMG_JS = r"""() => {
  const out = [];
  const scope = document.querySelector('div[role="dialog"]') || document;
  scope.querySelectorAll('img').forEach(i => {
    const s = i.currentSrc || i.src || '';
    if (/t51\.\d+-15\//.test(s)) out.push(s);
    (i.srcset || '').split(',').forEach(p => { const u = p.trim().split(' ')[0]; if (/t51\.\d+-15\//.test(u)) out.push(u); });
  });
  scope.querySelectorAll('video').forEach(v => { if (v.poster && /t51\.\d+-15\//.test(v.poster)) out.push(v.poster); });
  return out;
}"""

# 임베드 데이터 브리지: ArrowRight 프리로드는 네트워크에 안 잡힘 → script[type=application/json] 에서 code 주변 발췌
SCRIPT_BRIDGE_JS = r"""(code) => {
  const needle = '"code":"' + code + '"';
  for (const s of document.querySelectorAll('script[type="application/json"]')) {
    const t = s.textContent || '';
    const i = t.indexOf(needle);
    if (i >= 0) return t.slice(Math.max(0, i - 8000), i + 8000);
  }
  return '';
}"""


def our_media_ids():
    d = json.load(open(DATA / "posts.json", encoding="utf-8"))
    s = set()
    for r in d:
        if r.get("platform") == "instagram":
            m = MID_RE.search((r.get("images") or [""])[0])
            if m:
                s.add(m.group(1))
    return s


def load_done():
    if OUT.exists():
        try:
            return json.load(open(OUT, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def is_block(url):
    return any(k in url for k in ("/challenge", "/accounts/login", "/checkpoint", "/suspended"))


# 페이지 본문 차단 배너(일시 차단은 URL 안 바뀌고 다이얼로그로 뜸 — 2026-07-13 실증)
BLOCK_TEXTS = ("일시적으로 차단", "temporarily blocked", "try again later",
               "나중에 다시 시도", "we restrict certain activity", "action blocked")


def page_blocked(page):
    if is_block(page.url):
        return True
    try:
        t = (page.inner_text("body", timeout=3000) or "")[:4000].lower()
    except Exception:
        return False
    return any(k in t for k in BLOCK_TEXTS)


def mean_db(wav):
    r = subprocess.run([ff, "-i", wav, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else -999


def _lines(o, fp):
    try:
        res = o.predict(str(fp))
        return [x.strip() for x in res[0]["rec_texts"] if x and x.strip()]
    except Exception:
        return []


def frame_ocr(ocr_ko, ocr_ch, urls, tag):
    """영상 프레임 추출 → 각 프레임을 한국어+중국어 OCR 병행 → 화면 텍스트/자막(중복제거) + 한자 병음(성조)."""
    outdir = SCRATCH / f"frames_{tag}"
    outdir.mkdir(exist_ok=True)
    frames = []
    for u in urls[:6]:
        for f in outdir.glob("*.jpg"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            subprocess.run([ff, "-y", "-user_agent", UA, "-headers", "Referer: https://www.instagram.com/\r\n",
                            "-i", u, "-vf", "fps=1/2,mpdecimate", "-vsync", "vfr", "-frames:v", "30",
                            str(outdir / "f_%03d.jpg")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        except subprocess.TimeoutExpired:  # 스트림 정지 → 다음 후보 URL
            continue
        frames = sorted(outdir.glob("f_*.jpg"))
        if frames:
            break
    print(f"      [frame] {len(frames)} frames", flush=True)
    models = (ocr_ch, ocr_ko) if ocr_ch is not None else (ocr_ko,)  # 중국어 OCR은 필요할 때만
    texts = []
    for fp in frames:
        seen = []
        for o in models:
            for l in _lines(o, fp):
                if l not in seen:
                    seen.append(l)
        if seen:
            texts.append(" ".join(seen))
        try:
            fp.unlink()
        except Exception:
            pass
    uniq = []  # 오버레이가 유지/누적 반복되므로 포함관계 중복 제거
    for t in texts:
        if any(t in o for o in uniq):
            continue
        uniq = [o for o in uniq if o not in t]
        uniq.append(t)
    result = " / ".join(uniq)
    runs = list(dict.fromkeys(re.findall(r"[一-鿿]+", result)))  # 고유 한자 구절만(프레임 걸친 중복 제거) → 병음(성조)
    if runs:
        try:
            from pypinyin import pinyin as _pinyin, Style as _Style
            pin = " ".join("".join(x[0] for x in _pinyin(r, style=_Style.TONE)) for r in runs)
            result += "  [병음: " + pin + "]"
        except Exception:
            pass
    return result


def done_final(prev):
    """이 shortcode 를 더 안 건드려도 되는가 (전사됐거나·무음성이지만 자막은 얻었거나·사진이거나·재시도 소진)."""
    return bool(prev) and (bool(prev.get("transcript")) or bool(prev.get("frame_text"))
                           or prev.get("status") == "photo"
                           or prev.get("attempts", 0) >= MAX_ATTEMPTS)


def run_urls(page, urls, model, ocr, get_ch, vids, api_resps, hist, done, OUR, bridge, a):
    """직접 URL 방문 모드: 그리드 순회 대신 각 permalink 로 goto → 동일 캡처/전사.
    저장 피드 재순회 오버헤드가 없어 백로그 꼬리(뒤쪽에만 남은 소수) 회수에 효율적."""
    n_new = 0
    blocked = False
    for idx, url in enumerate(urls, 1):
        m = re.search(r"/(p|reel|reels)/([^/?]+)", url)
        code = m.group(2) if m else url
        vids.clear(); api_resps.clear(); hist.clear()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  [{idx}/{len(urls)}] goto 실패 {code}: {type(e).__name__}", flush=True)
            continue
        page.wait_for_timeout(4000)
        if page_blocked(page):
            print(f"BLOCK: 차단/체크포인트 감지 → 즉시 중단 (처리 전 {idx-1}건 완료)", flush=True)
            blocked = True
            break
        scm = re.search(r"/(p|reel)/([^/?]+)", page.url)
        sc = scm.group(0) if scm else f"/p/{code}"
        prev = done.get(sc)
        try:
            page.evaluate("document.querySelectorAll('video').forEach(v=>{v.muted=true; if(v.play)v.play(); try{v.currentTime=Math.min(3,(v.duration||8)*0.5);}catch(e){}})")
        except Exception:
            pass
        page.wait_for_timeout(a.dwell)
        prog = []
        for r in api_resps:
            try:
                b = r.text()
            except Exception:
                continue
            if "/o1/v/t2/" not in b:
                continue
            b2 = b.replace("\\/", "/").replace("\\u0026", "&")
            for mm in PROG_RE.finditer(b2):
                pu = mm.group(0).split("&bytestart")[0]
                if pu not in prog:
                    prog.append(pu)
            if prog:
                break
        candidates = prog + [v for v in vids if v not in prog]
        mid = None
        try:
            for u in page.evaluate(DOM_IMG_JS):
                mm = MID_RE.search(u)
                if mm and mm.group(1) in OUR:
                    mid = mm.group(1)
                    break
        except Exception:
            pass
        if mid:
            bridge[mid] = {"code": code, "media_type": (2 if vids else None)}
        print(f"  [{idx}/{len(urls)}] {sc} vids={len(vids)} prog={len(prog)} mid={mid}", flush=True)

        got = False
        for vurl in candidates[:6]:
            wav = str(SCRATCH / f"rt_u{idx}.wav")
            try:
                subprocess.run([ff, "-y", "-user_agent", UA, "-headers", "Referer: https://www.instagram.com/\r\n",
                                "-i", vurl, "-vn", "-ac", "1", "-ar", "16000", "-t", "300", wav],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            except subprocess.TimeoutExpired:
                continue
            ok = os.path.exists(wav) and os.path.getsize(wav) > 3000 and mean_db(wav) > -55
            if ok:
                segs, info = model.transcribe(wav, language="ko", vad_filter=False)
                txt = " ".join(s.text.strip() for s in segs).strip()
                need_ch = any("一" <= c <= "鿿" for c in txt)
                ft = frame_ocr(ocr, (get_ch() if need_ch else None), candidates, f"u{idx}")
                done[sc] = {"shortcode": sc, "mid": mid, "transcript": txt, "frame_text": ft}
                n_new += 1
                print(f"    OK[{n_new}] {sc} {round(info.duration,1)}s | 음성 {len(txt)}자 · 화면 {len(ft)}자", flush=True)
                json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                got = True
            try:
                os.remove(wav)
            except Exception:
                pass
            if got:
                break
        if not got:
            # 오디오 못 얻어도(무음 릴스) 화면 텍스트/자막은 프레임 OCR로 회수 — 학습 콘텐츠는 자막이 핵심
            ft = frame_ocr(ocr, None, candidates, f"u{idx}") if candidates else ""
            if ft:
                done[sc] = {"shortcode": sc, "mid": mid, "transcript": "", "frame_text": ft, "status": "no_speech"}
                print(f"    무음 → 화면 텍스트 {len(ft)}자 회수", flush=True)
            else:
                att = (prev.get("attempts", 0) if prev else 0) + 1
                done[sc] = {"shortcode": sc, "mid": mid, "transcript": "", "status": "no_audio", "attempts": att}
                print(f"    (오디오·화면 모두 실패, attempts={att}/{MAX_ATTEMPTS})", flush=True)
            json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(random.uniform(a.dmin, a.dmax))
        if n_new > 0 and n_new % 10 == 0:
            time.sleep(random.uniform(20, 40))
    return n_new, blocked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dmin", type=float, default=3.0)
    ap.add_argument("--dmax", type=float, default=8.0)
    ap.add_argument("--targets", default="", help="URL 리스트 파일(줄당 1개). 주면 그리드 순회 대신 직접 방문 모드")
    ap.add_argument("--dwell", type=int, default=9000, help="URL 모드에서 영상 시청(ms). 짧으면 추천 스트림 오염↓, 대상 로드 실패↑")
    a = ap.parse_args()

    OUR = our_media_ids()
    done = load_done()
    print(f"우리 IG media_id {len(OUR)} | 기존 기록 {len(done)} | 목표 {a.limit}", flush=True)
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    ocr = make_ocr()          # 한국어(한글+영어) 프레임 OCR
    _ch = {"o": None}
    def get_ch():             # 중국어 OCR(무거운 PP-OCRv6)은 한자 감지된 릴스에만 lazy 로드
        if _ch["o"] is None:
            print("      [중국어 OCR 로드]", flush=True)
            _ch["o"] = make_ocr("ch")
        return _ch["o"]

    from playwright.sync_api import sync_playwright
    vids, api_resps = [], []
    hist = []    # JSON 응답 롤링 버퍼(스텝별 clear 안 함) — 모달 응답이 ArrowRight 직후 도착해 api_resps.clear()에 지워지는 문제 보완
    n_new = 0
    bridge = {}  # 방문 중 발견한 media_id→code (saved_map 보강용; 첫화면 API 미캡처 보완)

    def on_resp(r):
        u = r.url
        try:
            ct = r.headers.get("content-type", "")
        except Exception:
            ct = ""
        if re.search(r"/o1/v/|\.mp4|video_dashinit", u) or ct.startswith("video"):
            v = u.split("&bytestart")[0]
            if v not in vids:
                vids.append(v)
        elif "json" in ct and ("graphql" in u or "/api/" in u):
            api_resps.append(r)
            hist.append(r)
            if len(hist) > 40:
                del hist[:len(hist) - 40]

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_resp)
        if a.targets:
            urls = [l.strip() for l in open(a.targets, encoding="utf-8") if l.strip()]
            print(f"직접 URL 모드: {len(urls)}건", flush=True)
            n_new, blocked = run_urls(page, urls, model, ocr, get_ch, vids, api_resps, hist, done, OUR, bridge, a)
            ctx.close()
            json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            ok = sum(1 for v in done.values() if v.get("transcript"))
            print(f"\n완료: 이번 신규 {n_new} | 전사 보유 {ok} | 기록 {len(done)} → {OUT.name}", flush=True)
            sys.exit(2 if blocked else 0)  # 차단 시 2 → 반복 러너가 즉시 중단
        page.goto(SAVED, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        if page_blocked(page):
            print("BLOCK: 로그인/체크포인트/차단배너 → 중단", flush=True)
            ctx.close()
            return

        opened = False
        for sel in ["main a[href*='/p/']", "main div[role='button']", "main img"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=6000)
                    opened = True
                    print("열기 셀렉터:", sel, flush=True)
                    break
            except Exception:
                pass
        if not opened:
            print("첫 게시물 열기 실패", flush=True)
            ctx.close()
            return
        page.wait_for_timeout(4000)

        steps = 0
        last_sc, same_sc = "", 0
        while n_new < a.limit and steps < a.limit * 6 + 900:
            steps += 1
            if is_block(page.url) or (steps % 10 == 1 and page_blocked(page)):  # URL은 매 스텝, 배너는 10스텝마다(inner_text 비용)
                print("BLOCK: 챌린지/차단 감지 → 즉시 중단", flush=True)
                break

            # 완료(전사됨/사진/재시도소진) 게시물은 9초 시청 없이 빠른 스킵 — 연속모드 걷기 비용 절감
            scm0 = re.search(r"/(p|reel)/([^/?]+)", page.url)
            sc0 = scm0.group(0) if scm0 else "?"
            same_sc = same_sc + 1 if sc0 == last_sc else 0
            last_sc = sc0
            if same_sc >= 8:
                print("피드 끝(같은 게시물 반복) → 종료", flush=True)
                break
            prev0 = done.get(sc0)
            if sc0 != "?" and done_final(prev0):
                print(f"  skip#{steps} sc={sc0}", flush=True)  # 하트비트: 긴 스킵 스크롤도 살아있음 신호
                if not prev0.get("mid"):  # 브리지만 저비용 보강(임베드 JSON)
                    code0 = sc0.rstrip("/").split("/")[-1]
                    try:
                        seg = page.evaluate(SCRIPT_BRIDGE_JS, code0)
                    except Exception:
                        seg = ""
                    if seg:
                        seg = seg.replace("\\/", "/")
                        for m in MID_RE.finditer(seg):
                            if m.group(1) in OUR:
                                prev0["mid"] = m.group(1)
                                bridge[m.group(1)] = {"code": code0, "media_type": None}
                                break
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(int(random.uniform(500, 1000)))  # 처리완료 릴스는 빠른 통과(resume) — 재시작 후 재스크롤 오버헤드 ↓ (신규 스크래핑 페이싱은 §하단 유지)
                continue

            vids.clear()
            api_resps.clear()
            try:
                page.evaluate("document.querySelectorAll('video').forEach(v=>{v.muted=true; if(v.play)v.play(); try{v.currentTime=Math.min(3,(v.duration||8)*0.5);}catch(e){}})")
            except Exception:
                pass
            page.wait_for_timeout(9000)  # 길게 시청 + seek → progressive(오디오포함) 스트림 로드 유도

            # 응답 본문에서 progressive(오디오포함) URL 추출 (메인 흐름에서 .text())
            prog = []
            for r in api_resps:
                try:
                    b = r.text()
                except Exception:
                    continue
                if "/o1/v/t2/" not in b:
                    continue
                b2 = b.replace("\\/", "/").replace("\\u0026", "&")
                for mm in PROG_RE.finditer(b2):
                    pu = mm.group(0).split("&bytestart")[0]
                    if pu not in prog:
                        prog.append(pu)
                if prog:
                    break
            candidates = prog + [v for v in vids if v not in prog]  # progressive 우선

            mid = None
            for u in page.evaluate(DOM_IMG_JS):
                m = MID_RE.search(u)
                if m and m.group(1) in OUR:
                    mid = m.group(1)
                    break
            scm = re.search(r"/(p|reel)/([^/?]+)", page.url)
            sc = scm.group(0) if scm else "?"
            code = sc.rstrip("/").split("/")[-1]
            if mid is None and sc != "?":
                # 응답본문 브리지: 모달 API JSON 에서 "code":"<sc>" 근처 이미지 URL 의 media_id 추출 (롤링 버퍼, 최신부터)
                for r in reversed(hist):
                    try:
                        b = r.text()
                    except Exception:
                        continue
                    i = b.find(f'"code":"{code}"')
                    if i < 0:
                        continue
                    b2 = b[max(0, i - 8000):i + 8000].replace("\\/", "/")
                    for m in MID_RE.finditer(b2):
                        if m.group(1) in OUR:
                            mid = m.group(1)
                            break
                    if mid:
                        break
            if mid is None and sc != "?":
                # 3차 폴백: 페이지 임베드 JSON(script 태그)에서 code 주변 media_id
                try:
                    seg = page.evaluate(SCRIPT_BRIDGE_JS, code)
                except Exception:
                    seg = ""
                if seg:
                    seg = seg.replace("\\/", "/")
                    for m in MID_RE.finditer(seg):
                        if m.group(1) in OUR:
                            mid = m.group(1)
                            break
            if mid and sc != "?":
                bridge[mid] = {"code": code, "media_type": (2 if vids else None)}
            prev = done.get(sc)
            if prev is not None and mid and not prev.get("mid"):
                prev["mid"] = mid  # 이미 전사된 건도 브리지 보강
            print(f"  post#{steps} sc={sc} vids={len(vids)} prog={len(prog)} mid={mid}", flush=True)

            if not candidates and sc != "?" and prev is None:
                try:
                    has_video = page.evaluate("!!document.querySelector('video')")
                except Exception:
                    has_video = True
                if not has_video:  # 영상 요소 자체가 없음 = 사진/캐러셀 → 이후 걷기에서 빠른 스킵
                    done[sc] = {"shortcode": sc, "status": "photo", "mid": mid}
                    json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

            if candidates and sc != "?" and not done_final(prev):
                got = False
                for vurl in candidates[:4]:  # 후보 상한(8→4): progressive 우선이라 앞쪽에서 대부분 성공
                    wav = str(SCRATCH / f"rt_{steps}.wav")
                    try:
                        subprocess.run([ff, "-y", "-user_agent", UA, "-headers", "Referer: https://www.instagram.com/\r\n",
                                        "-i", vurl, "-vn", "-ac", "1", "-ar", "16000", "-t", "300", wav],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)  # per-candidate 벽시계 상한(240→60): 정상 오디오는 수초, stall 은 60s 컷
                    except subprocess.TimeoutExpired:  # 스트림 정지 → 다음 후보
                        continue
                    ok = os.path.exists(wav) and os.path.getsize(wav) > 3000 and mean_db(wav) > -55
                    if ok:
                        segs, info = model.transcribe(wav, language="ko", vad_filter=False)
                        txt = " ".join(s.text.strip() for s in segs).strip()
                        need_ch = any("一" <= c <= "鿿" for c in txt)  # 음성에 한자 → 중국어 릴스
                        ft = frame_ocr(ocr, (get_ch() if need_ch else None), candidates, steps)  # 화면 텍스트/자막
                        done[sc] = {"shortcode": sc, "order": steps, "mid": mid, "transcript": txt, "frame_text": ft}
                        n_new += 1
                        print(f"    OK[{n_new}/{a.limit}] {sc} {round(info.duration,1)}s | 음성 {len(txt)}자 · 화면 {len(ft)}자", flush=True)
                        json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                        got = True
                    try:
                        os.remove(wav)
                    except Exception:
                        pass
                    if got:
                        break
                if not got:  # 실패 → attempts 증가(재시도 가능, 3회까지)
                    att = (prev.get("attempts", 0) if prev else 0) + 1
                    done[sc] = {"shortcode": sc, "order": steps, "mid": mid, "transcript": "", "status": "no_audio", "attempts": att}
                    json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    print(f"    (오디오 못 얻음, attempts={att}/{MAX_ATTEMPTS})", flush=True)

            time.sleep(random.uniform(a.dmin, a.dmax))
            if n_new > 0 and n_new % 10 == 0:
                time.sleep(random.uniform(20, 40))
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(int(random.uniform(1500, 3000)))
        ctx.close()

    json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)  # mid 보강분 반영
    if bridge:  # saved_map 에 신규 쌍만 유니온 (기존 항목의 media_type 보존)
        smap_path = DATA / "saved_map.json"
        try:
            smap = json.load(open(smap_path, encoding="utf-8"))
        except Exception:
            smap = {}
        added = 0
        for k, v in bridge.items():
            if k not in smap:
                smap[k] = v
                added += 1
        if added:
            json.dump(smap, open(smap_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"브리지: 발견 {len(bridge)} | saved_map 신규 {added}", flush=True)

    ok = sum(1 for v in done.values() if v.get("transcript"))
    print(f"\n완료: 이번 신규 {n_new} | 전사 보유 {ok} | 기록 {len(done)} → {OUT.name}", flush=True)


if __name__ == "__main__":
    main()
