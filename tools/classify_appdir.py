#!/usr/bin/env python3
"""Classify AppDir files: elf / lib / text / media (already compressed) / other."""
import json
import os
import sys
from collections import Counter

MEDIA = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ogg", ".mp3", ".flac",
         ".zip", ".gz", ".xz", ".bz2", ".zst", ".woff", ".woff2", ".mp4", ".webm",
         ".jar", ".pak", ".br"}
TEXT = {".txt", ".md", ".json", ".xml", ".html", ".css", ".js", ".py", ".sh", ".qml",
        ".desktop", ".svg", ".ts", ".po", ".yml", ".yaml", ".ini", ".conf", ".pl", ".lua", ".vim"}


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in MEDIA:
        return "media"
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return "other"
    if head == b"\x7fELF":
        return "lib" if (".so" in os.path.basename(path)) else "elf"
    if ext in TEXT or head[:2] == b"#!":
        return "text"
    return "other"


def scan(root):
    hist, size = Counter(), Counter()
    big = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            c, s = classify(p), os.path.getsize(p)
            hist[c] += 1
            size[c] += s
            big.append((s, os.path.relpath(p, root)))
    big.sort(reverse=True)
    return {"files": sum(hist.values()), "bytes": sum(size.values()),
            "count_by_class": dict(hist), "bytes_by_class": dict(size),
            "top20": [{"path": p, "bytes": s} for s, p in big[:20]]}


if __name__ == "__main__":
    print(json.dumps(scan(sys.argv[1]), indent=1))
