#!/usr/bin/env python3
"""Turn R/.tools/inbox.jsonl into a reel.py queue: drop what is already handled and captions with no AI angle.
Blank captions stay in, because the speech decides. Prints what it dropped so it can be logged as skipped."""
import json, os, re, sys

R = os.path.expanduser(os.environ.get("REELS_HOME", "~/Knowledge/reels"))
AI = re.compile(r"\b(ai|a\.i\.|agent|agents|claude|gpt|chatgpt|gemini|cursor|n8n|veo|prompt|prompts|mcp|notebook|notebooklm|llm|"
                r"github|repo|automat\w+|openclaw|clawd|arcads|figma|lovable|vibe|skill|skills|workflow|stack)\b", re.I)

CODE = re.compile(r"[\w-]{5,20}")


def main(out):
    with open(os.path.join(R, ".processed.json")) as f:
        items, seen = {}, set(json.load(f))
    with open(os.path.join(R, ".tools", "inbox.jsonl"), encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if CODE.fullmatch(str(o.get("code"))):  # the receiver takes anything; reel.py builds paths from this
                items[o["code"]] = o
    queue, dropped = [], []
    for c, o in items.items():
        if c in seen or o.get("err") or os.path.exists(os.path.join(R, ".tools", "out", c + ".txt")):
            continue
        cap = o.get("caption", "")
        (queue if not cap.strip() or AI.search(cap) else dropped).append(o)
    with open(os.path.join(R, ".tools", out), "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False)
    with open(os.path.join(R, ".tools", "dropped.json"), "w") as f:
        json.dump([{"code": o["code"], "user": o.get("user")} for o in dropped], f)
    print(len(queue), "queued,", len(dropped), "dropped")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "queue.json")
