#!/usr/bin/env python3
"""Download + verify + extract corpus AppImages into uncompressed AppDir tars
(cache-friendly), plus corpus-stats.json and an optional startup working set."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import WORK, corpus_dir, download, get_app, run
from classify_appdir import scan


def make_synthetic(root, version):
    """Deterministic fake app. version 2 changes a few files (zsync pair)."""
    import random
    rnd = random.Random(42)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/share/doc").mkdir(parents=True)
    run_sh = '#!/bin/sh\necho "synthetic %s"\n' % version
    (root / "AppRun").write_text(run_sh)
    os.chmod(root / "AppRun", 0o755)
    (root / "synthetic.desktop").write_text("[Desktop Entry]\nName=synthetic\nExec=AppRun\nType=Application\nIcon=synthetic\nCategories=Utility;\n")
    (root / "synthetic.png").write_bytes(bytes(rnd.randrange(256) for _ in range(2048)))
    words = [bytes(rnd.choices(b"abcdefghijklmnopqrstuvwxyz", k=rnd.randrange(3, 10))).decode() for _ in range(500)]
    for i in range(60):   # many small text files
        txt = " ".join(rnd.choice(words) for _ in range(2000))
        if version == 2 and i % 15 == 0:
            txt += " changed in version two"
        (root / f"usr/share/doc/doc{i}.txt").write_text(txt)
    for i in range(6):    # fewer big pseudo-binaries (semi-compressible)
        blob = b"".join(rnd.choice(words).encode() + bytes(rnd.randrange(256) for _ in range(8))
                        for _ in range(60000))
        if version == 2 and i == 3:
            blob = blob[:100000] + b"PATCH" * 50 + blob[100000:]
        (root / f"usr/lib/lib{i}.so").write_bytes(blob)
    (root / "usr/bin/tool").write_bytes(b"\x7fELF" + bytes(rnd.randrange(256) for _ in range(300000)))


def tar_dir(src, dest):
    with tarfile.open(dest, "w") as t:      # uncompressed
        for p in sorted(Path(src).rglob("*")):
            t.add(p, arcname=str(p.relative_to(src)), recursive=False)


def extract_appimage(img, dest):
    os.chmod(img, 0o755)
    with tempfile.TemporaryDirectory() as td:
        run([str(img), "--appimage-extract"], cwd=td)
        shutil.move(os.path.join(td, "squashfs-root"), dest)


def fetch_one(app, tag, url, sha, cdir, synthetic_version=None, workset=False):
    tarf = cdir / f"{tag}.tar"
    if tarf.exists():
        return tarf
    tmp = cdir / f"{tag}.appdir"
    if tmp.exists():
        shutil.rmtree(tmp)
    if synthetic_version:
        make_synthetic(tmp, synthetic_version)
    else:
        img = download(url, WORK / "dl" / f"{app['name']}-{tag}.AppImage", sha or None)
        extract_appimage(img, tmp)
        img.unlink()
    (cdir / f"{tag}.stats.json").write_text(json.dumps(scan(tmp), indent=1))
    tar_dir(tmp, tarf)
    if tag == "new" and workset:
        record_workset(app, cdir, tmp)
    shutil.rmtree(tmp)
    return tarf


def record_workset(app, cdir, appdir):
    wl = app.get("launch", {})
    out = cdir / "workset.json"
    if out.exists() or wl.get("mode") != "gui":
        return
    from record_workset import record
    try:
        record(appdir, wl, out)
    except Exception as e:   # best-effort, never fails the corpus job
        print(f"workset recording failed for {app['name']}: {e}", file=sys.stderr)


def fetch(name, arch="x86_64", workset=False):
    app = get_app(name, arch)
    cdir = corpus_dir(app)
    cdir.mkdir(parents=True, exist_ok=True)
    if app.get("synthetic"):
        fetch_one(app, "new", None, None, cdir, 2)
        fetch_one(app, "old-patch", None, None, cdir, 1)
        # a small fake launch working set so the hotness-list variants can be tested
        (cdir / "workset.json").write_text(json.dumps(
            {"files": ["AppRun", "usr/lib/lib0.so", "usr/share/doc/doc0.txt"]}))
    else:
        fetch_one(app, "new", app["url"], app.get("sha256"), cdir, workset=workset)
        for pr in app.get("pairs", []):
            fetch_one(app, f"old-{pr['kind']}", pr["old_url"], pr.get("old_sha256"), cdir)
    return cdir


def unpack(tag, app, dest):
    """Extract a cached tar into dest (fresh)."""
    cdir = corpus_dir(app)
    if Path(dest).exists():
        shutil.rmtree(dest)
    Path(dest).mkdir(parents=True)
    with tarfile.open(cdir / f"{tag}.tar") as t:
        t.extractall(dest, filter="tar")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--arch", default="x86_64")
    ap.add_argument("--workset", action="store_true")
    a = ap.parse_args()
    print(fetch(a.app, a.arch, a.workset))
