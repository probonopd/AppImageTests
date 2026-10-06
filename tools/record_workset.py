#!/usr/bin/env python3
"""Record the ordered list of files an app opens during launch (strace openat),
relative to the AppDir. Replayed by measure_startup against a mounted image."""
import json
import os
import re
import signal
import subprocess
import sys
import time


def record(appdir, launch, out, display=":98"):
    appdir = os.path.abspath(appdir)
    xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x800x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    log = str(out) + ".strace"
    env = dict(os.environ, DISPLAY=display, **launch.get("env", {}))
    cmd = ["strace", "-f", "-e", "trace=openat,execve", "-o", log,
           os.path.join(appdir, "AppRun")] + launch.get("cmd", [])
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(launch.get("workset_seconds", 15))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            break
        time.sleep(1)
    xvfb.terminate()
    seen, order = set(), []
    pat = re.compile(r'(?:openat\(AT_FDCWD, |execve\()"([^"]+)"')
    with open(log, errors="replace") as f:
        for line in f:
            m = pat.search(line)
            if not m or "= -1" in line:
                continue
            path = m.group(1)
            if path.startswith(appdir + "/"):
                rel = os.path.relpath(path, appdir)
                if rel not in seen and os.path.isfile(path):
                    seen.add(rel)
                    order.append(rel)
    os.unlink(log)
    with open(out, "w") as f:
        json.dump({"files": order}, f)
    return order
