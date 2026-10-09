"""Shared helpers: paths, YAML loading, variant expansion, timed subprocesses."""
import hashlib
import itertools
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("BENCH_WORK", "/mnt/bench"))
PROCS = int(os.environ.get("BENCH_PROCS", "4"))


def load_yaml(rel):
    with open(ROOT / rel) as f:
        return yaml.safe_load(f)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_size(s):
    s = str(s).upper()
    m = re.fullmatch(r"(\d+)([KM]?)", s)
    n = int(m.group(1))
    return n * {"": 1, "K": 1024, "M": 1 << 20}[m.group(2)]


def human(n):
    for u in ("B", "K", "M", "G"):
        if n < 1024 or u == "G":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024


# ---------------------------------------------------------------- variants
def _expand_family(fam):
    params = fam["params"]
    keys = list(params)
    lists = [v if isinstance(v, list) else [v] for v in params.values()]
    levers = fam.get("levers", [])
    for combo in itertools.product(*lists):
        p = dict(zip(keys, combo))
        vid = fam["id"].format(**p)
        # family key: family name + categorical (non-lever, multi-valued) params
        cat = [f"{k}={p[k]}" for k in keys
               if isinstance(params[k], list) and k not in levers]
        v = {
            "id": vid, "kind": fam["kind"], "family": fam["name"],
            "family_key": "/".join([fam["name"]] + cat),
            "params": p, "codec": p.get("codec"), "level": p.get("level"),
            "block_bytes": parse_size(p["block"]) if "block" in p
            else (1 << int(p["bits"]) if "bits" in p else None),
            "stage3": bool(fam.get("stage3")),
            "reference_only": bool(fam.get("reference_only")),
            "compat": fam.get("compat", {}),
        }
        yield v


def load_variant_table():
    return load_yaml("variants/variants.yml")


def expand_variants(include_stage3=True):
    tbl = load_variant_table()
    out = []
    for fam in tbl["families"]:
        for v in _expand_family(fam):
            if v["stage3"] and not include_stage3:
                continue
            out.append(v)
    return out


def derive_option_variants(base):
    """Option axes for a squashfs base variant: ids '<base>+<opt>'."""
    tbl = load_variant_table()
    res = []
    for name in tbl.get("squashfs_options", {}):
        v = dict(base)
        v["id"] = f"{base['id']}+{name}"
        v["option"] = name
        v["stage3"] = True
        res.append(v)
    return res


def _template_regex(tpl):
    parts = re.split(r"\{(\w+)\}", tpl)
    return "".join(re.escape(x) if i % 2 == 0 else f"(?P<{x}>[^-]+)" for i, x in enumerate(parts))


def lever_value(spec, raw):
    """raw lever value (int or '512K') -> position on the lever axis (log2 or linear)."""
    x = parse_size(raw) if spec.get("scale") == "log2" else float(raw)
    return math.log2(x) if spec.get("scale") == "log2" else x


def _resolve_dynamic(base_id):
    """Resolve an id that is not in the table but fits a family template with every
    lever inside its lever_ranges (used for edge-extension points)."""
    for fam in load_variant_table()["families"]:
        ranges = fam.get("lever_ranges")
        if not ranges:
            continue
        m = re.fullmatch(_template_regex(fam["id"]), base_id)
        if not m:
            continue
        params = {}
        try:
            for k, ref in fam["params"].items():
                ref0 = ref[0] if isinstance(ref, list) else ref
                val = m.group(k) if k in m.groupdict() else ref0
                params[k] = int(val) if isinstance(ref0, int) and not isinstance(ref0, bool) and k in m.groupdict() else val
        except ValueError:          # e.g. 'dwarfs-lzma2-S20' also fits 'dwarfs-l{level}-S{bits}'
            continue
        for lever, spec in ranges.items():
            try:
                t = lever_value(spec, params[lever])
                lo, hi = lever_value(spec, spec["min"]), lever_value(spec, spec["max"])
            except (ValueError, TypeError, KeyError):
                raise KeyError(base_id)
            if not lo - 1e-9 <= t <= hi + 1e-9 or (spec.get("scale") == "log2" and abs(t - round(t)) > 1e-9):
                raise KeyError(base_id)
        single = dict(fam, params={k: [v] for k, v in params.items()})
        cand = list(_expand_family(single))
        if cand and cand[0]["id"] == base_id:
            return cand[0]
    raise KeyError(base_id)


def get_variant(vid):
    base_id, _, opt = vid.partition("+")
    for v in expand_variants():
        if v["id"] == base_id:
            if opt:
                for d in derive_option_variants(v):
                    if d["id"] == vid:
                        return d
                raise KeyError(vid)
            return v
    v = _resolve_dynamic(base_id)
    if opt:
        for d in derive_option_variants(v):
            if d["id"] == vid:
                return d
        raise KeyError(vid)
    return v


# ---------------------------------------------------------------- corpus
def load_corpus():
    return load_yaml("corpus/corpus.yml")["apps"]


def get_app(name, arch="x86_64"):
    for a in load_corpus():
        if a["name"] == name and a.get("arch", "x86_64") == arch:
            return a
    raise KeyError(f"{name}/{arch}")


def is_pinned(app):
    if app.get("synthetic"):
        return True
    if not app.get("sha256"):
        return False
    return all(p.get("old_sha256") for p in app.get("pairs", [])) or True


def corpus_dir(app):
    return WORK / "corpus" / f"{app['name']}-{app.get('arch', 'x86_64')}"


def corpus_cache_key(app):
    h = hashlib.sha256(json.dumps(
        {k: app.get(k) for k in ("url", "sha256", "pairs", "synthetic")},
        sort_keys=True).encode()).hexdigest()[:12]
    return f"corpus-v1-{app['name']}-{app.get('arch', 'x86_64')}-{h}"


def download(url, dest, expect_sha=None):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not expect_sha or sha256_file(dest) == expect_sha):
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "appimage-bench"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    got = sha256_file(tmp)
    if expect_sha and got != expect_sha:
        tmp.unlink()
        raise RuntimeError(f"sha256 mismatch for {url}: {got} != {expect_sha}")
    if not expect_sha and os.environ.get("ALLOW_UNPINNED") != "1":
        tmp.unlink()
        raise RuntimeError(f"{url} is not pinned (sha256 empty); run the pin workflow "
                           "or set ALLOW_UNPINNED=1")
    tmp.rename(dest)
    return dest


def get_runtime(kind, arch="x86_64"):
    rt = load_yaml("corpus/runtimes.yml")[kind]
    sha = rt["sha256"].get(arch) or None
    p = download(rt["urls"][arch], WORK / "runtimes" / f"{rt['name']}-{arch}", sha)
    os.chmod(p, 0o755)
    return p, rt["name"], sha256_file(p)


# ---------------------------------------------------------------- processes
def run(cmd, check=True, **kw):
    kw.setdefault("text", True)
    kw.setdefault("capture_output", True)
    r = subprocess.run(cmd, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


def timed(cmd, timeout=None, **kw):
    """Run under /usr/bin/time -v. Returns dict(wall_s, cpu_s, rss_mb, rc, timeout)."""
    with tempfile.NamedTemporaryFile("r", suffix=".time") as tf:
        wrapped = ["/usr/bin/time", "-v", "-o", tf.name] + cmd
        try:
            r = subprocess.run(wrapped, capture_output=True, text=True,
                               timeout=timeout, **kw)
        except subprocess.TimeoutExpired:
            return {"wall_s": timeout, "cpu_s": None, "rss_mb": None, "rc": -1,
                    "timeout": True, "err": "timeout"}
        txt = tf.read()
    def grab(pat):
        m = re.search(pat, txt)
        return float(m.group(1)) if m else 0.0
    el = re.search(r"Elapsed \(wall clock\) time.*: ([\d:.]+)", txt)
    parts = [float(x) for x in el.group(1).split(":")] if el else [0.0]
    wall = 0.0
    for x in parts:
        wall = wall * 60 + x
    return {"wall_s": wall,
            "cpu_s": grab(r"User time \(seconds\): ([\d.]+)") + grab(r"System time \(seconds\): ([\d.]+)"),
            "rss_mb": grab(r"Maximum resident set size \(kbytes\): (\d+)") / 1024,
            "rc": r.returncode, "timeout": False, "err": r.stderr[-2000:]}


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def mad(xs):
    m = median(xs)
    return median([abs(x - m) for x in xs]) if m is not None else None


def cv(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3 or statistics.mean(xs) == 0:
        return 0.0
    return statistics.pstdev(xs) / statistics.mean(xs)


def uruntime_auto_cache_mb():
    """The DwarFS block cache the uruntime picks on this host (VHSgunzo/uruntime get_dwfs_cachesize:
    largest tier strictly below MemAvailable/1.3, else 32M)."""
    try:
        with open("/proc/meminfo") as f:
            kb = {l.split(":")[0]: int(l.split()[1]) for l in f}
        avail_mb = kb.get("MemAvailable", kb.get("MemFree", 0)) / 1024 / 1.3
    except (OSError, ValueError):
        return None
    return next((t for t in (1536, 1024, 896, 768, 640, 512, 384, 256, 128, 64) if avail_mb > t), 32)


def env_facts():
    def sh(c):
        r = subprocess.run(c, shell=True, capture_output=True, text=True)
        return r.stdout.strip()
    return {
        "cpu": sh("lscpu | sed -n 's/^Model name: *//p'"),
        "cores": os.cpu_count(),
        "ram_gb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1),
        "uruntime_auto_cache_mb": uruntime_auto_cache_mb(),
        "kernel": platform.release(),
        "image": os.environ.get("ImageVersion", ""),
        "runner": os.environ.get("RUNNER_NAME", platform.node()),
        "tools": {
            "mksquashfs": sh("mksquashfs -version | head -1"),
            "mkdwarfs": sh("mkdwarfs --help 2>&1 | head -1"),
            "zsync": sh("zsync -V 2>&1 | head -1"),
        },
    }
