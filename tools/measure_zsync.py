#!/usr/bin/env python3
"""zsync update efficiency with a real range-capable server (nginx).
Python's http.server does not do ranges and is not used."""
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from common import run, sha256_file


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class RangeServer:
    def __init__(self, root):
        self.root = Path(root)
        self.prefix = Path(tempfile.mkdtemp(prefix="nginx-"))
        self.port = _free_port()
        conf = f"""
worker_processes 1;
pid {self.prefix}/nginx.pid;
events {{ worker_connections 64; }}
http {{
  log_format fmt '$request_uri $status $body_bytes_sent $http_range';
  access_log {self.prefix}/access.log fmt;
  client_body_temp_path {self.prefix}/cb; proxy_temp_path {self.prefix}/pt;
  fastcgi_temp_path {self.prefix}/ft; uwsgi_temp_path {self.prefix}/ut; scgi_temp_path {self.prefix}/st;
  server {{ listen 127.0.0.1:{self.port}; root {self.root}; sendfile on; }}
}}"""
        (self.prefix / "nginx.conf").write_text(conf)
        self.p = subprocess.Popen(
            ["nginx", "-p", str(self.prefix), "-c", str(self.prefix / "nginx.conf"),
             "-e", str(self.prefix / "error.log"), "-g", "daemon off;"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.1).close()
                break
            except OSError:
                time.sleep(0.05)

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"

    def clear_log(self):
        (self.prefix / "access.log").write_text("")

    def log(self):
        rows = []
        for line in (self.prefix / "access.log").read_text().splitlines():
            parts = line.split(" ", 3)
            rows.append((parts[0], int(parts[1]), int(parts[2])))
        return rows

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        shutil.rmtree(self.prefix, ignore_errors=True)


def zsyncmake_bin():
    return shutil.which("zsyncmake2") or shutil.which("zsyncmake") or "zsyncmake"


def measure(old, new, blocks, server_dir=None):
    """Return a list of {zsync_block, zsync_file, downloaded, requests, ok, ...}."""
    old, new = Path(old), Path(new)
    srv_dir = Path(server_dir or tempfile.mkdtemp(prefix="zs-"))
    name = "new.AppImage"
    link = srv_dir / name
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink(new, link)
    srv = RangeServer(srv_dir)
    res = []
    new_sha = sha256_file(new)
    try:
        for b in blocks:
            zs = srv_dir / (name + ".zsync")
            run([zsyncmake_bin(), "-b", str(b), "-u", srv.url(name), "-o", str(zs), str(new)])
            out = srv_dir / "out.AppImage"
            out.unlink(missing_ok=True)
            srv.clear_log()
            r = run(["zsync", "-q", "-i", str(old), "-o", str(out), srv.url(name + ".zsync")],
                    check=False, cwd=srv_dir)
            rows = srv.log()
            ctrl = sum(x[2] for x in rows if x[0].endswith(".zsync"))
            data = [x for x in rows if not x[0].endswith(".zsync")]
            downloaded = sum(x[2] for x in data)
            ok = out.exists() and sha256_file(out) == new_sha and r.returncode == 0
            if not ok:
                elog = srv.prefix / "error.log"
                print(f"!! zsync diag: rc={r.returncode} out_exists={out.exists()}\n"
                      f"stdout={r.stdout[-800:]}\nstderr={r.stderr[-800:]}\n"
                      f"nginx_alive={srv.p.poll() is None} port={srv.port} log_rows={rows[:5]}\n"
                      f"nginx error.log={elog.read_text()[-800:] if elog.exists() else 'none'}",
                      flush=True)
            res.append({"zsync_block": b, "zsync_file": os.path.getsize(zs),
                        "control_served": ctrl, "downloaded": downloaded,
                        "requests": len(data), "ok": ok,
                        "update_cost": os.path.getsize(zs) + downloaded,
                        "update_ratio": (os.path.getsize(zs) + downloaded) / os.path.getsize(new)})
            out.unlink(missing_ok=True)
    finally:
        srv.stop()
        link.unlink(missing_ok=True)
        (srv_dir / (name + ".zsync")).unlink(missing_ok=True)
    return res


def xdelta_reference(old_tar, new_tar):
    if not shutil.which("xdelta3"):
        return None
    d = tempfile.mktemp()
    r = run(["xdelta3", "-e", "-9", "-f", "-s", str(old_tar), str(new_tar), d], check=False)
    size = os.path.getsize(d) if r.returncode == 0 and os.path.exists(d) else None
    Path(d).unlink(missing_ok=True)
    return size
