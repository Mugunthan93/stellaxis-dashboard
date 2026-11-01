"""Batch-extract speech + on-screen text for saved Instagram posts.

Usage: ~/Knowledge/reels/.tools/venv/bin/python reel.py queue.json
queue.json is the `items` array from fetch_saved.js. Writes $REELS_HOME/.tools/out/<code>.txt per item
(caption, Whisper transcript, de-duplicated OCR) and prints a one-line status each.
"""
import json, os, re, subprocess, sys, wave
import numpy as np

R = os.path.expanduser(os.environ.get("REELS_HOME", "~/Knowledge/reels"))  # the notes; code lives in the repo
STATE = os.path.join(R, ".tools")  # venv, Whisper model, scratch dirs
WORK, OUT = os.path.join(STATE, "work"), os.path.join(STATE, "out")
IMAGES = os.path.join(R, "images")  # cover images the notes embed
os.makedirs(WORK, exist_ok=True); os.makedirs(OUT, exist_ok=True); os.makedirs(IMAGES, exist_ok=True)
os.environ.setdefault("HF_HOME", os.path.join(STATE, "hf"))
_models = {}


def model(size):
    if size not in _models:
        from faster_whisper import WhisperModel
        _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _models[size]


def get(url, path):
    subprocess.run(["curl", "-sf", "-m", "120", "-o", path, url], check=True)


def ocr(paths):
    seen, lines = set(), []
    for p in paths:
        t = subprocess.run(["tesseract", p, "-", "--psm", "11"], capture_output=True, text=True).stdout
        for l in t.splitlines():
            l = re.sub(r"\s+", " ", l).strip()
            if len(l) >= 4 and sum(c.isalpha() for c in l) >= 3 and l.lower() not in seen:
                seen.add(l.lower()); lines.append(l)
    return " | ".join(lines)


def transcribe(audio_mp4, wav):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", audio_mp4, "-ar", "16000", "-ac", "1", wav], check=True)
    w = wave.open(wav)
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    segs, info = model("base").transcribe(x, vad_filter=True)
    if info.language != "en":  # base mangles Tamil/Hindi etc.: use a bigger model and translate to English
        segs, info = model("small").transcribe(x, vad_filter=True, task="translate")
        return " ".join(s.text.strip() for s in segs).strip(), f"{info.language}, translated to en"
    return " ".join(s.text.strip() for s in segs).strip(), info.language


def run(item):
    code = item["code"]; d = os.path.join(WORK, code); os.makedirs(d, exist_ok=True)
    if item.get("thumb"):
        get(item["thumb"], os.path.join(IMAGES, f"{code}.jpg"))
    speech, lang, frames = "", "", []
    if item.get("v"):
        audio = item.get("a") or item["v"]  # a single progressive URL carries both audio and video
        get(audio, f"{d}/a.mp4")
        speech, lang = transcribe(f"{d}/a.mp4", f"{d}/a.wav")
        if item["v"] != audio:
            get(item["v"], f"{d}/v.mp4")
        src = f"{d}/v.mp4" if os.path.exists(f"{d}/v.mp4") else f"{d}/a.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-vf", "fps=1", f"{d}/f%03d.png"], check=True)
        frames = sorted(os.path.join(d, p) for p in os.listdir(d) if p.startswith("f") and p.endswith(".png"))
    for i, u in enumerate(item.get("imgs") or []):
        p = f"{d}/img{i}.jpg"; get(u, p); frames.append(p)
    res = (f"# {code} by @{item.get('user')} ({item.get('dur') or 0}s) {item['url']}\n"
           f"## Caption\n{item.get('caption') or '(none)'}\n"
           f"## Speech{f' [{lang}]' if lang else ''}\n{speech or '(none)'}\n"
           f"## On-screen\n{ocr(frames) or '(none)'}\n")
    open(os.path.join(OUT, f"{code}.txt"), "w").write(res)
    subprocess.run(["rm", "-rf", d])
    return len(speech)


if __name__ == "__main__":
    if sys.argv[1] == "--covers-only":  # fetch just the cover images, e.g. for skipped or old items
        for it in json.load(open(sys.argv[2])):
            if it.get("thumb"):
                get(it["thumb"], os.path.join(IMAGES, f"{it['code']}.jpg")); print(f"ok   {it['code']} cover")
        sys.exit()
    items = json.load(open(sys.argv[1]))
    items = items["items"] if isinstance(items, dict) else items
    for it in items:
        if "code" not in it:
            continue
        try:
            n = run(it); print(f"ok   {it['code']} speech={n} chars")
        except Exception as e:  # keep going; the routine retries failures next run
            print(f"FAIL {it['code']} {type(e).__name__}: {e}")
