#!/usr/bin/env python3
"""AppDir + variant -> .AppImage (runtime + payload), with build-cost accounting
and a determinism check (build twice, compare sha256)."""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (PROCS, get_runtime, get_variant, load_variant_table, sha256_file,
                    timed)
from classify_appdir import classify

BCJ = {"x86_64": "x86", "aarch64": "arm64"}
ORDER_PRIO = {"elf": 100, "lib": 100, "text": 50, "other": 10, "media": 0}


def _sort_file(appdir, dest):
    with open(dest, "w") as f:
        for dp, _, fns in os.walk(appdir):
            for fn in fns:
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, appdir)
                f.write(f"{rel} {ORDER_PRIO[classify(p)]}\n")


def payload_cmd(v, appdir, payload, arch, epoch, workdir, hotness=None):
    p = v["params"]
    if v["kind"] == "squashfs":
        cmd = ["mksquashfs", str(appdir), str(payload), "-comp", p["codec"],
               "-b", str(v["block_bytes"]), "-all-root", "-all-time", str(epoch),
               "-mkfs-time", str(epoch), "-processors", str(PROCS), "-noappend",
               "-quiet", "-no-progress"]
        if p["codec"] in ("gzip", "zstd") and p.get("level"):
            cmd += ["-Xcompression-level", str(p["level"])]
        if p["codec"] == "xz" and p.get("bcj") == "auto":
            cmd += ["-Xbcj", BCJ[arch]]
        if p["codec"] == "lz4":
            cmd += ["-Xhc"]
        opts = load_variant_table().get("squashfs_options", {}).get(v.get("option"), {})
        cmd += opts.get("flags", [])
        if opts.get("sort") == "type":
            sf = Path(workdir) / "sortfile"
            _sort_file(appdir, sf)
            cmd += ["-sort", str(sf)]
        return cmd
    if v["kind"] == "dwarfs":
        preset = p.get("preset", p.get("level") if p["codec"] == "preset" else 5)
        cmd = ["mkdwarfs", "-i", str(appdir), "-o", str(payload)]
        if preset != "default":            # "default": leave mkdwarfs's own -l default
            cmd += ["-l", str(preset)]
        cmd += ["-S", str(p["bits"]), "-N", str(PROCS), "--set-owner", "0",
                "--set-group", "0", "--set-time", str(epoch), "--no-create-timestamp",
                "--progress=none", "--log-level=warn", "--no-history"]
        if p.get("lookback"):
            cmd += ["-B", str(p["lookback"])]
        if p.get("hotness") == "hot":
            if not hotness:
                raise ValueError("hotness variant needs a hotness list")
            cmd += ["--hotness-list", str(hotness)]
        c, lvl = p["codec"], p.get("level")
        if c == "zstd":
            cmd += ["-C", f"zstd:level={lvl}"]
        elif c == "lzma":
            cmd += ["-C", f"lzma:level={lvl}"]
        elif c == "brotli":
            cmd += ["-C", f"brotli:quality={lvl}"]
        if p.get("order"):
            cmd += ["--order", p["order"]]
        if p.get("categorize"):
            cmd += ["--categorize"]
        return cmd
    raise ValueError(v["kind"])


def build_once(v, appdir, out, arch="x86_64", epoch=0, timeout=2400, hotness=None):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = out.with_suffix(".payload")
    if payload.exists():
        payload.unlink()
    rt_kind = "dwarfs" if v["kind"] == "dwarfs" else "squashfs"
    rt_path, rt_name, rt_sha = get_runtime(rt_kind, arch)
    cmd = payload_cmd(v, appdir, payload, arch, epoch, out.parent, hotness)
    t = timed(cmd, timeout=timeout)
    res = {"build": {k: t[k] for k in ("wall_s", "cpu_s", "rss_mb", "timeout")},
           "runtime": f"{rt_name}@{rt_sha[:12]}"}
    if t["timeout"] or t["rc"] != 0:
        res["error"] = t["err"]
        return res
    with open(out, "wb") as o:
        for src in (rt_path, payload):
            with open(src, "rb") as f:
                shutil.copyfileobj(f, o, 1 << 20)
    os.chmod(out, 0o755)
    rt_bytes, pl_bytes = os.path.getsize(rt_path), os.path.getsize(payload)
    payload.unlink()
    res["size"] = {"payload": pl_bytes, "runtime": rt_bytes, "total": rt_bytes + pl_bytes}
    return res


def build(v, appdir, out, arch="x86_64", determinism=True, epoch=0, timeout=2400, hotness=None):
    res = build_once(v, appdir, out, arch, epoch, timeout, hotness)
    if "error" in res:
        return res
    sha = sha256_file(out)
    res["sha256"] = sha
    if determinism:
        second = out.parent / "again" / out.name   # same basename: paths end up in dwarfs metadata
        r2 = build_once(v, appdir, second, arch, epoch, timeout, hotness)
        res["build"]["deterministic"] = ("error" not in r2 and sha256_file(second) == sha)
        if not res["build"]["deterministic"] and second.exists():
            a, b = out.read_bytes(), second.read_bytes()
            diff = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
            print(f"!! nondeterminism {v['id']}: sizes {len(a)} vs {len(b)}, "
                  f"{len(diff)} differing bytes, first offsets {diff[:10]}, last {diff[-3:]}", flush=True)
        second.unlink(missing_ok=True)
    else:
        res["build"]["deterministic"] = None
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("variant")
    ap.add_argument("appdir")
    ap.add_argument("out")
    ap.add_argument("--arch", default="x86_64")
    ap.add_argument("--no-determinism", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(get_variant(a.variant), a.appdir, a.out, a.arch,
                           not a.no_determinism), indent=1))
