"""Local reel processor. Usage: python3 run_local.py <reels_dir> [workers]
Reads <reels_dir>/urls.json {code: mp4_url}, writes <reels_dir>/out/<code>.json
and <reels_dir>/progress.json. Re-runnable: skips codes already done."""
import sys, os, json, re, subprocess, wave, time, shutil, traceback
from concurrent.futures import ProcessPoolExecutor

BASE = sys.argv[1]
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
BUDGET = float(sys.argv[3]) if len(sys.argv) > 3 else 1e9
OUT = os.path.join(BASE, "out")
WORK = os.path.expanduser("~/reelwork")
os.makedirs(OUT, exist_ok=True); os.makedirs(WORK, exist_ok=True)

_model = None
def model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=2)
    return _model

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def process(code, url):
    dst = os.path.join(OUT, code + ".json")
    if os.path.exists(dst):
        return code, "skip"
    rec = {"code": code, "status": "ok", "speech": "", "lang": "", "screen": [], "duration": 0}
    if not url.startswith("http"):
        rec["status"] = url
        json.dump(rec, open(dst, "w"), ensure_ascii=False); return code, url
    d = os.path.join(WORK, code); os.makedirs(d, exist_ok=True)
    try:
        v = os.path.join(d, "v.mp4")
        r = run(["curl", "-s", "-m", "120", "-o", v, "-w", "%{http_code}", url])
        if r.stdout.strip() != "200":
            rec["status"] = "http_" + r.stdout.strip()
        else:
            wav = os.path.join(d, "a.wav")
            run(["ffmpeg", "-loglevel", "error", "-y", "-i", v, "-vn", "-ar", "16000", "-ac", "1", wav])
            if os.path.exists(wav) and os.path.getsize(wav) > 2000:
                import numpy as np
                w = wave.open(wav)
                x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
                segs, info = model().transcribe(x, vad_filter=True)
                rec["speech"] = " ".join(s.text.strip() for s in segs).strip()
                rec["lang"] = info.language; rec["duration"] = round(info.duration)
            run(["ffmpeg", "-loglevel", "error", "-y", "-i", v, "-vf", "fps=0.34,scale=600:-1", os.path.join(d, "f%03d.png")])
            seen = set()
            for f in sorted(p for p in os.listdir(d) if p.startswith("f")):
                t = run(["tesseract", os.path.join(d, f), "-", "--psm", "11"]).stdout
                for l in t.splitlines():
                    l = re.sub(r"\s+", " ", l).strip()
                    if len(l) >= 4 and sum(c.isalpha() for c in l) >= 3 and l.lower() not in seen:
                        seen.add(l.lower()); rec["screen"].append(l)
    except Exception as e:
        rec["status"] = "error: " + str(e)[:200]
    finally:
        shutil.rmtree(d, ignore_errors=True)
    json.dump(rec, open(dst, "w"), ensure_ascii=False)
    return code, rec["status"]

def progress(urls, started):
    done = [c for c in urls if os.path.exists(os.path.join(OUT, c + ".json"))]
    bad = 0
    for c in done:
        try:
            if json.load(open(os.path.join(OUT, c + ".json")))["status"] != "ok": bad += 1
        except Exception: pass
    json.dump({"total": len(urls), "done": len(done), "failed": bad, "started": started,
               "updated": time.time()}, open(os.path.join(BASE, "progress.json"), "w"))

if __name__ == "__main__":
    urls = json.load(open(os.path.join(BASE, "urls.json")))
    started = time.time(); progress(urls, started)
    from concurrent.futures import wait, FIRST_COMPLETED
    t0 = time.time()
    todo = [c for c in urls if not os.path.exists(os.path.join(OUT, c + ".json"))]
    with ProcessPoolExecutor(WORKERS) as ex:
        running = set()
        while todo or running:
            while todo and len(running) < WORKERS and time.time() - t0 < BUDGET:
                c = todo.pop(0); running.add(ex.submit(process, c, urls[c]))
            if not running: break
            done_, running = wait(running, return_when=FIRST_COMPLETED)
            progress(urls, started)
    progress(urls, started)
    left = [c for c in urls if not os.path.exists(os.path.join(OUT, c + ".json"))]
    print("LEFT", len(left))
    if not left: open(os.path.join(BASE, "DONE"), "w").write("ok")
