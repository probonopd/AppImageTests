#!/usr/bin/env python3
"""Codec-only reference: compress `tar --sort=name` of the AppDir with stand-alone
codecs (lower bound on size; no startup metric - no random access).
Continuous levers use low/mid/high (variants/candidates.yml)."""
import argparse
import itertools
import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import WORK, get_app, load_yaml, timed
from fetch_corpus import fetch, unpack


def expand(entry):
    keys = [k for k, v in entry.items() if isinstance(v, list)]
    for combo in itertools.product(*[entry[k] for k in keys]) if keys else [()]:
        p = dict(zip(keys, combo))
        yield entry["id"].format(**p), entry["cmd"].format(**p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--arch", default="x86_64")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    app = get_app(a.app, a.arch)
    fetch(a.app, a.arch)
    d = WORK / "codecref" / a.app
    shutil.rmtree(d, ignore_errors=True)
    appdir = unpack("new", app, d / "appdir")
    tar = d / "app.tar"
    subprocess.run(["tar", "--sort=name", "--mtime=@0", "--owner=0", "--group=0",
                    "--numeric-owner", "-cf", str(tar), "-C", str(appdir), "."], check=True)
    raw = tar.stat().st_size
    rows = []
    for entry in load_yaml("variants/candidates.yml")["codecs"]:
        for cid, cmd in expand(entry):
            exe = shlex.split(cmd)[0]
            if not shutil.which(exe):
                rows.append({"id": cid, "skipped": f"{exe} not installed"})
                continue
            outf = d / "out.bin"
            with open(outf, "wb") as o:
                t0 = time.time()
                r = subprocess.run(shlex.split(cmd) + [str(tar)], stdout=o,
                                   stderr=subprocess.DEVNULL)
                wall = time.time() - t0
            rows.append({"id": cid, "ok": r.returncode == 0, "bytes": outf.stat().st_size,
                         "ratio": outf.stat().st_size / raw, "wall_s": wall,
                         "note": "codec-only: no random access, no startup metric"})
            outf.unlink()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"codec_records": rows, "app": a.app, "arch": a.arch,
                                       "tar_bytes": raw}, indent=1))
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
