#!/usr/bin/env python3
"""Startup measurements on a built AppImage.

mount_run: mount via --appimage-mount, then on the mounted tree
  1. working-set replay (recorded launch order)
  2. tree walk + random 4K reads of sampled files
  3. sequential read of everything
and report wall times, FUSE-process CPU (psutil tree), peak RSS.
launch_run: real launch under Xvfb until 'ready'.
States: warm | cold | tmpfs (see docs section 4.3)."""
import os
import random
import shutil
import signal
import tempfile
import subprocess
import time
from pathlib import Path

import psutil


def drop_caches():
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
                   check=False)


def _procs_for(pid, image=None):
    """The runtime process tree plus detached FUSE daemons (found by the image path in
    their command line: squashfuse forks/daemonizes and is no longer our child)."""
    procs = {}
    try:
        root = psutil.Process(pid)
        for p in [root] + root.children(recursive=True):
            procs[p.pid] = p
    except psutil.Error:
        pass
    if image:
        needle = str(image)
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if p.pid != os.getpid() and any(needle in c for c in (p.info["cmdline"] or [])):
                    procs[p.pid] = p
            except psutil.Error:
                pass
    return list(procs.values())


def _tree_cpu(pid, image=None):
    cpu = rss = 0.0
    for p in _procs_for(pid, image):
        try:
            t = p.cpu_times()
            cpu += t.user + t.system
            with open(f"/proc/{p.pid}/status") as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        rss += int(line.split()[1]) / 1024
        except (psutil.Error, OSError):
            pass
    return cpu, rss


def _read_file(path, limit=None, chunk=1 << 20):
    n = 0
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk if limit is None else min(chunk, limit - n))
                if not b:
                    break
                n += len(b)
                if limit is not None and n >= limit:
                    break
    except OSError:
        pass
    return n


def mount_run(image, workset=None, cpus="0,1", sample=200, seed=1, timeout=120):
    cmd = (["taskset", "-c", cpus] if cpus else []) + [str(image), "--appimage-mount"]
    t0 = time.perf_counter()
    errf = tempfile.TemporaryFile("w+")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, stderr=errf)
    mp = p.stdout.readline().strip()
    deadline = time.time() + timeout
    while True:
        try:
            if os.listdir(mp):
                break
        except OSError:
            pass
        if time.time() > deadline or p.poll() is not None:
            p.kill()
            errf.seek(0)
            msg = errf.read()[-600:]
            print(f"!! mount failed for {image}: rc={p.poll()} stderr={msg!r}", flush=True)
            return {"error": "mount failed", "stderr": msg}
        time.sleep(0.005)
    r = {"mount_ms": (time.perf_counter() - t0) * 1000}
    cpu_m, _ = _tree_cpu(p.pid, image)
    try:
        t = time.perf_counter()
        wbytes = 0
        for rel in (workset or []):
            wbytes += _read_file(os.path.join(mp, rel), limit=16 << 20)
        r["workset_ms"] = (time.perf_counter() - t) * 1000 if workset else None
        cpu_w, _ = _tree_cpu(p.pid, image)
        r["cpu_s_startup"] = cpu_w  # mount + working set

        t = time.perf_counter()
        files = []
        for dp, _, fns in os.walk(mp):
            for fn in fns:
                fp = os.path.join(dp, fn)
                if os.path.isfile(fp) and not os.path.islink(fp):
                    files.append(fp)
        r["walk_ms"] = (time.perf_counter() - t) * 1000
        files.sort()
        rnd = random.Random(seed)
        picks = rnd.sample(files, min(sample, len(files)))
        t = time.perf_counter()
        for fp in picks:
            try:
                size = os.path.getsize(fp)
                with open(fp, "rb") as f:
                    f.seek(rnd.randrange(max(size - 4096, 1)))
                    f.read(4096)
            except OSError:
                pass
        r["rand4k_ms"] = (time.perf_counter() - t) * 1000 / max(len(picks), 1)

        t = time.perf_counter()
        total = sum(_read_file(fp) for fp in files)
        dt = time.perf_counter() - t
        r["seq_mb_s"] = total / (1 << 20) / dt if dt > 0 else None
        r["uncompressed_bytes"] = total
        cpu_t, rss = _tree_cpu(p.pid, image)
        r["cpu_s_total"] = cpu_t
        r["fuse_rss_mb"] = rss
    finally:
        kids = [k for k in _procs_for(p.pid, image) if k.pid != p.pid]
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
        subprocess.run(["fusermount3", "-u", "-z", mp], capture_output=True)
        for k in kids:                      # no orphaned FUSE helpers between runs
            try:
                k.kill()
            except psutil.Error:
                pass
    return r


def launch_run(image, launch, display=":99", cpus=None):
    env = dict(os.environ, DISPLAY=display, **{k: str(v) for k, v in launch.get("env", {}).items()})
    ready = launch.get("ready", {"type": "exit", "timeout_s": 30})
    cmd = (["taskset", "-c", cpus] if cpus else []) + [str(image)] + launch.get("cmd", [])
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    ok = False
    try:
        deadline = time.time() + ready.get("timeout_s", 60)
        if ready["type"] == "exit":
            try:
                p.wait(ready.get("timeout_s", 60))
                ok = p.returncode == 0
            except subprocess.TimeoutExpired:
                pass
        else:
            while time.time() < deadline and p.poll() is None:
                r = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "."],
                                   env=env, capture_output=True, text=True)
                if r.returncode == 0 and r.stdout.strip():
                    ok = True
                    break
                time.sleep(0.05)
        ms = (time.perf_counter() - t0) * 1000
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p.pid, sig)
            except ProcessLookupError:
                break
            time.sleep(0.5)
    out = {"launch_ms": ms if ok else None, "ok": ok}
    if not ok:
        try:
            out["output"] = p.stdout.read()[-400:] if p.stdout else ""
        except Exception:
            pass
        print(f"!! launch failed for {image}: {out.get('output')!r}", flush=True)
    return out


def prepare_state(image, state, scratch="/dev/shm"):
    """Return (path_to_use, cleanup). Applies the cache state before a run."""
    if state == "tmpfs":
        dst = Path(scratch) / ("bench-" + Path(image).name)
        if not dst.exists():
            shutil.copyfile(image, dst)
            os.chmod(dst, 0o755)
        drop_caches()
        return dst, lambda: dst.unlink(missing_ok=True)
    if state == "cold":
        drop_caches()
    return Path(image), lambda: None
