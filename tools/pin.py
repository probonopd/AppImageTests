#!/usr/bin/env python3
"""Fill empty sha256 fields in corpus/corpus.yml and corpus/runtimes.yml by
downloading each URL. Reports URLs that fail. Edits the YAML textually so
comments survive."""
import re
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, WORK, load_yaml, sha256_file


def fetch_sha(url):
    try:
        return sha256_file(_dl(url))
    except (urllib.error.URLError, OSError) as e:
        print(f"FAILED {url}: {e}", file=sys.stderr)
        return None


def _dl(url):
    import shutil, urllib.request
    dest = WORK / "pin" / re.sub(r"\W", "_", url)[-120:]
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "appimage-bench"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    return dest


def pin_corpus():
    path = ROOT / "corpus/corpus.yml"
    text = path.read_text()
    for app in load_yaml("corpus/corpus.yml")["apps"]:
        items = []
        if app.get("url") and not app.get("sha256"):
            items.append((app["url"], "sha256"))
        for pr in app.get("pairs", []):
            if not pr.get("old_sha256"):
                items.append((pr["old_url"], "old_sha256"))
        for url, key in items:
            sha = fetch_sha(url)
            if sha:
                u = re.escape(url)
                if key == "sha256":
                    pat = re.compile(r'(\burl: %s\n\s+sha256: )""' % u)
                else:
                    pat = re.compile(r'(old_url: "%s", old_sha256: )""' % u)
                text, n = pat.subn(r'\g<1>"%s"' % sha, text, count=1)
                print(f"pinned {url} -> {sha[:12]} ({n})")
    path.write_text(text)


def pin_runtimes():
    path = ROOT / "corpus/runtimes.yml"
    text = path.read_text()
    rts = load_yaml("corpus/runtimes.yml")
    for kind, rt in rts.items():
        for arch, url in rt["urls"].items():
            if rt["sha256"].get(arch):
                continue
            sha = fetch_sha(url)
            if sha:
                text = _set_runtime(text, kind, arch, sha)
                print(f"pinned runtime {kind}/{arch} -> {sha[:12]}")
    path.write_text(text)


def _set_runtime(text, kind, arch, sha):
    start = text.index(f"{kind}:")
    m = re.search(r'%s: ""' % arch, text[start:])
    # first empty slot for this arch after the kind header
    i = start + m.start()
    return text[:i] + f'{arch}: "{sha}"' + text[i + len(m.group(0)):]


if __name__ == "__main__":
    pin_runtimes()
    pin_corpus()
