# Reel Brain

Turns saved Instagram reels into AI knowledge notes. Code only: the notes, images, venv and Whisper
model live in `~/Knowledge/reels` (override with `REELS_HOME`) and never go in this repo.

| File | What it does |
|---|---|
| `ROUTINE.md` | Step-by-step routine the `instagram-reels-knowledge` scheduled task follows |
| `fetch_saved.js` | Runs in the logged-in browser pane; lists saved posts and their media URLs (read-only GETs) |
| `reel.py` | Whisper transcript + OCR per post into `$REELS_HOME/.tools/out/<code>.txt` |
| `build_dashboard.py` | Builds `$REELS_HOME/dashboard.html` and the phone artifact page (stdlib only) |
| `run_local.py` | The first batch pipeline (261 reels in `~/Downloads/reels`); kept for re-runs |

`~/Knowledge/reels/.tools/` holds symlinks to these files, so the scheduled task's approved commands
(`cd ~/Knowledge/reels/.tools && ./venv/bin/python reel.py queue.json`, `python3 ~/Knowledge/reels/.tools/build_dashboard.py`)
run the repo copies unchanged.

Needs `ffmpeg`, `tesseract`, and a venv with `faster-whisper` and `numpy` at `~/Knowledge/reels/.tools/venv`.
