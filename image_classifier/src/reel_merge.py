"""reel_transcripts.json(shortcode 키 -> {mid, transcript, frame_text, ...}) 를 posts.json 에 병합.
키 형식 혼재('/p/CODE'·'CODE') 를 정규화하고, IG 레코드의 post_url shortcode(보조: media_id) 로 매칭해
transcript / frame_text 필드를 추가한다. 비어있지 않은 값만 쓰므로 기존 값을 지우지 않는다(멱등)."""
import sys, json, os, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pipeline_ids import record_id

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
done = json.load(open(os.path.join(DATA, "reel_transcripts.json"), encoding="utf-8"))
d = json.load(open(os.path.join(DATA, "posts.json"), encoding="utf-8"))
ig = [r for r in d if r.get("platform") == "instagram"]

def sc_of(url):
    m = re.search(r"/(p|reel|reels)/([^/?]+)", url or "")
    return m.group(2) if m else None

by_sc = {sc_of(r.get("post_url")): r for r in ig if sc_of(r.get("post_url"))}
by_mid = {record_id(r): r for r in ig}

applied, unmatched = 0, []
for key, info in done.items():
    if not isinstance(info, dict):
        continue
    sc = re.sub(r"^/(p|reel|reels)/", "", key).strip("/")
    r = by_sc.get(sc) or by_mid.get(str(info.get("mid") or ""))
    if r is None:
        if info.get("transcript") or info.get("frame_text"):
            unmatched.append(key)
        continue
    changed = False
    for f in ("transcript", "frame_text"):
        v = info.get(f)
        if v and v != r.get(f):
            r[f] = v
            changed = True
    if changed:
        applied += 1

with open(os.path.join(DATA, "posts.json"), "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

total_tr = sum(1 for r in ig if r.get("transcript"))
total_ft = sum(1 for r in ig if r.get("frame_text"))
print(f"병합 {applied} | 매칭실패 {len(unmatched)} | IG transcript 보유 {total_tr} | frame_text 보유 {total_ft}")
if unmatched:
    print("  매칭실패 예:", unmatched[:5])
