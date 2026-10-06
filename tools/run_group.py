#!/usr/bin/env python3
"""One benchmark job: (app, arch, group of variants + canary).
Builds all variants, measures size/build/startup/zsync, writes one JSON file.
All variants being compared run on the same machine, timing runs interleaved
round-robin; the canary is included in every job for cross-job normalisation."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import measure_startup as ms
import measure_zsync as mz
from build_variant import build
from common import (WORK, corpus_dir, cv, env_facts, get_app, get_variant, load_variant_table,
                    mad, median, sha256_file)
from fetch_corpus import fetch, unpack

STATES = ("warm", "cold", "tmpfs")
TIMING_KEYS = ("mount_ms", "workset_ms", "rand4k_ms", "cpu_s_startup", "seq_mb_s")


def start_xvfb(display=":99"):
    if not shutil.which("Xvfb"):
        return None
    p = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x800x24"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    return p


def summarise(rec):
    s = {}
    b = rec.get("build", {})
    for k in ("wall_s", "cpu_s", "rss_mb"):
        s[f"build_{k}"] = b.get(k)
    st = rec.get("startup", {})
    for state, runs in st.get("mount", {}).items():
        for k in TIMING_KEYS + ("fuse_rss_mb", "cpu_s_total", "walk_ms"):
            vals = [r.get(k) for r in runs if r.get(k) is not None]
            s[f"{k}_{state}"] = median(vals)
    for state, runs in st.get("launch", {}).items():
        s[f"launch_ms_{state}"] = median([r["launch_ms"] for r in runs if r.get("launch_ms")])
    return s


def noisy_metrics(rec, thr=0.10):
    out = []
    for state, runs in rec.get("startup", {}).get("mount", {}).items():
        for k in ("mount_ms", "cpu_s_startup"):
            if cv([r.get(k) for r in runs]) > thr:
                out.append(f"{k}_{state}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--arch", default="x86_64")
    ap.add_argument("--variants", required=True, help="comma separated ids")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stage", default="1")
    ap.add_argument("--no-startup", action="store_true")
    ap.add_argument("--no-zsync", action="store_true")
    ap.add_argument("--reps-warm", type=int, default=7)
    ap.add_argument("--reps-cold", type=int, default=5)
    ap.add_argument("--reps-launch", type=int, default=3)
    ap.add_argument("--zsync-blocks", default="")
    ap.add_argument("--cpus", default="0,1")
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--retry", action="store_true")
    a = ap.parse_args()

    tbl = load_variant_table()
    ids = [tbl["canary"]] + [v for v in a.variants.split(",") if v and v != tbl["canary"]]
    blocks = [int(x) for x in a.zsync_blocks.split(",") if x] or tbl["zsync_blocks"]
    app = get_app(a.app, a.arch)
    cdir = fetch(a.app, a.arch)
    run_dir = WORK / "run" / f"{a.app}-{a.arch}"
    shutil.rmtree(run_dir, ignore_errors=True)
    (run_dir / "img").mkdir(parents=True)
    new_dir = unpack("new", app, run_dir / "new")
    uncompressed = sum(f.stat().st_size for f in new_dir.rglob("*") if f.is_file() and not f.is_symlink())
    workset = None
    if (cdir / "workset.json").exists():
        workset = json.loads((cdir / "workset.json").read_text())["files"]
    env = env_facts()
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    failures = 0

    recs = {}
    hot_of = {}
    for vid in ids:
        v = get_variant(vid)
        img = run_dir / "img" / f"{vid}.AppImage"
        print(f"== build {vid}", flush=True)
        hot = None
        if v["params"].get("hotness") == "hot":
            if workset:
                hot = run_dir / "hotness.txt"
                hot.write_text("\n".join(workset) + "\n")
        if v["params"].get("hotness") == "hot" and hot is None:
            r = {"error": "no recorded working set for a hotness list (cli-only app)"}
        else:
            r = build(v, new_dir, img, a.arch, determinism=uncompressed < 400 << 20,
                      timeout=a.timeout, hotness=hot)
        rec = {"run_id": run_id, "app": a.app, "arch": a.arch, "category": app.get("category"),
               "variant": vid, "container": v["kind"], "codec": v["codec"], "level": v["level"],
               "block_bytes": v["block_bytes"], "family_key": v["family_key"],
               "stage": a.stage, "retry": a.retry, "runtime": r.get("runtime"),
               "size": dict(r.get("size", {}), uncompressed=uncompressed),
               "build": r.get("build", {}), "env": env, "startup": {}, "zsync": []}
        if "size" in r:
            rec["size"]["ratio"] = r["size"]["total"] / uncompressed
        if "error" in r:
            rec["error"] = r["error"]
        recs[vid] = (rec, img)
        if hot is not None:
            hot_of[vid] = hot
        if r.get("build", {}).get("deterministic") is False:
            print(f"!! {vid} is NOT deterministic", flush=True)

    live = [vid for vid in ids if "error" not in recs[vid][0] and recs[vid][0]["build"].get("wall_s") is not None]

    # ---------------- startup (interleaved round-robin)
    if not a.no_startup:
        xvfb = start_xvfb() if app.get("launch", {}).get("mode") == "gui" else None
        unsupported = {}      # vid -> runtime message: codec the runtime cannot mount
        for state, reps in (("warm", a.reps_warm), ("cold", a.reps_cold), ("tmpfs", a.reps_cold)):
            for rec, _ in recs.values():
                rec["startup"].setdefault("mount", {})[state] = []
            passes = reps + (1 if state == "warm" else 0)
            for i in range(passes):
                for vid in live:
                    if vid in unsupported:
                        continue
                    rec, img = recs[vid]
                    path, cleanup = ms.prepare_state(img, state)
                    try:
                        res = ms.mount_run(path, workset, a.cpus)
                        if "supports only" in res.get("stderr", ""):
                            unsupported[vid] = res["stderr"].splitlines()[0]
                            rec["runtime_unsupported"] = unsupported[vid]
                            rec["startup"]["mount"] = {}
                            print(f"-- {vid}: not mountable by the runtime ({unsupported[vid]})", flush=True)
                            continue
                        if state == "warm" and i == 0:      # populate cache, discard
                            continue
                        if "error" in res:
                            failures += 1
                        rec["startup"]["mount"][state].append(res)
                    finally:
                        cleanup()
        launch = app.get("launch")
        if launch:
            for state in STATES:
                for rec, _ in recs.values():
                    rec["startup"].setdefault("launch", {})[state] = []
                for i in range(a.reps_launch):
                    for vid in live:
                        if vid in unsupported:
                            continue
                        rec, img = recs[vid]
                        path, cleanup = ms.prepare_state(img, state)
                        try:
                            rec["startup"]["launch"][state].append(ms.launch_run(path, launch))
                        finally:
                            cleanup()
        if xvfb:
            xvfb.terminate()

    # ---------------- zsync
    if not a.no_zsync:
        for pr in [{"kind": "patch"}] if app.get("synthetic") else app.get("pairs", []):
            old_dir = unpack(f"old-{pr['kind']}", app, run_dir / "old")
            for vid in live:
                rec, img = recs[vid]
                v = get_variant(vid)
                old_img = run_dir / "img" / f"{vid}.old.AppImage"
                ro = build(v, old_dir, old_img, a.arch, determinism=False, timeout=a.timeout, hotness=hot_of.get(vid))
                if "error" in ro:
                    continue
                for z in mz.measure(old_img, img, blocks):
                    z["pair"] = pr["kind"]
                    rec["zsync"].append(z)
                    if not z["ok"]:
                        failures += 1
                        print(f"!! zsync verification failed {vid} {pr['kind']} b={z['zsync_block']}")
                old_img.unlink(missing_ok=True)
        # "no change" case: same content, different build timestamp
        for vid in live:
            rec, img = recs[vid]
            v = get_variant(vid)
            alt = run_dir / "img" / f"{vid}.alt.AppImage"
            ra = build(v, new_dir, alt, a.arch, determinism=False, epoch=86400, timeout=a.timeout,
                       hotness=hot_of.get(vid))
            if "error" not in ra:
                for z in mz.measure(alt, img, [blocks[1]]):
                    z["pair"] = "rebuild"
                    rec["zsync"].append(z)
            alt.unlink(missing_ok=True)
        # idealised reference on the raw AppDir tars
        ref = []
        for pr in app.get("pairs", []):
            size = mz.xdelta_reference(cdir / f"old-{pr['kind']}.tar", cdir / "new.tar")
            ref.append({"pair": pr["kind"], "xdelta3_bytes": size})
    else:
        ref = []

    out_recs = []
    canary = summarise(recs[ids[0]][0])
    for vid in ids:
        rec, img = recs[vid]
        rec["summary"] = summarise(rec)
        rec["canary"] = canary
        rec["noisy"] = noisy_metrics(rec)
        out_recs.append(rec)
        img.unlink(missing_ok=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"records": out_recs, "reference": ref, "app": a.app,
                                       "arch": a.arch}, indent=2))
    shutil.rmtree(run_dir, ignore_errors=True)
    print(f"wrote {a.out}; failures={failures}")
    sys.exit(3 if failures else 0)


if __name__ == "__main__":
    main()
