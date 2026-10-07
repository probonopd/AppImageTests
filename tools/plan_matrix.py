#!/usr/bin/env python3
"""Emit the dynamic job matrices (fetch / bench / codec) for a stage.

stage 1   screen: reference apps x ALL variants, size + build only
stage 2   shortlist (Pareto set from stage 1) x whole corpus, startup + zsync
stage 3   option axes (squashfs flags, dwarfs order/categorize) on the top 3
full      everything x everything (heavy)
Variants are packed into groups by estimated cost (greedy bin packing) so
that many small jobs run in parallel and finish at similar times."""
import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (corpus_cache_key, derive_option_variants, expand_variants, get_variant,
                    load_corpus, load_variant_table)

RUNNER = {"x86_64": "ubuntu-24.04", "aarch64": "ubuntu-24.04-arm"}
GROUP_TARGET_S = 1500       # ~25 min of estimated work per job -> lots of parallelism
HEAVY_S = 3000
MAX_PER_GROUP = 6          # keep groups small: more parallel jobs, faster results
MAX_JOBS = 250
MAX_JOB_MINUTES = 350        # hosted-runner job limit is 360 min


def app_mb(app):
    return {"synthetic": 1, "tiny-cli": 15, "small-qt": 120, "electron": 250,
            "large-qt": 450, "huge": 700}.get(app.get("category"), 200)


def est_build_s(v, mb):
    p = v["params"]
    c, l = p.get("codec"), int(p.get("level") or 0)
    if v["kind"] == "squashfs":
        per = {"gzip": 0.03 + 0.01 * l, "xz": 0.25, "lz4": 0.05, "lzo": 0.03,
               "zstd": 0.02 + 0.005 * l + 0.04 * max(0, l - 15)}[c]
    else:
        if c == "preset":
            per = 0.05 + 0.08 * l
        elif c == "lzma":
            per = 0.15 + 0.05 * l
        elif c == "brotli":
            per = 0.1 + 0.06 * l
        else:
            per = 0.1 + 0.02 * l + 0.05 * max(0, l - 15)
    return per * mb


def est_cost_s(v, mb, flags):
    t = est_build_s(v, mb) * 2                      # build + determinism rebuild
    if not flags["no_startup"]:
        t += 18 * (mb / 150 + 3) + 9 * 8            # mount/read reps + launches
    if not flags["no_zsync"]:
        t += est_build_s(v, mb) * 1.2 + 40           # old build + zsync rounds
    return t


def pack(vs, mb, flags, canary):
    cost_c = est_cost_s(get_variant(canary), mb, flags)
    heavy = [v for v in vs if est_cost_s(v, mb, flags) > HEAVY_S]
    rest = sorted([v for v in vs if v not in heavy], key=lambda v: -est_cost_s(v, mb, flags))
    groups = [[v] for v in heavy]
    loads = [est_cost_s(v, mb, flags) for v in heavy]
    cap = GROUP_TARGET_S
    bins, bl = [], []
    for v in rest:
        c = est_cost_s(v, mb, flags)
        for i, l in enumerate(bl):
            if l + c <= cap and len(bins[i]) < MAX_PER_GROUP:
                bins[i].append(v); bl[i] += c
                break
        else:
            bins.append([v]); bl.append(c + cost_c)
    return groups + bins, loads + bl


WIDE = False
FINE_LEVELS = (5, 7, 9)
FINE_BLOCKS = ("64K", "128K", "256K", "512K")


def select_variants(stage, results, only):
    allv = [v for v in expand_variants(include_stage3=False)]
    shortlist = None
    sp = Path(results) / "shortlist.json" if results else None
    if sp and sp.exists():
        shortlist = json.loads(sp.read_text())["variants"]
    if stage in ("1", "full"):
        vs = allv
        if stage == "full":
            vs = allv + [v for v in expand_variants() if v["stage3"]]
    elif stage == "ext":
        ep = Path(results) / "extensions.json" if results else None
        ids = json.loads(ep.read_text())["variants"] if ep and ep.exists() else []
        vs = [get_variant(i) for i in ids]
    elif stage == "fine":
        # Fine grid around the stage 2/3 optimum, all in the same jobs (and next to the canary)
        # so the points can be compared directly. Not a 3-point lever scan: it refines it.
        vs = [get_variant(f"squashfs-zstd{lv}-b{bk}") for lv in FINE_LEVELS for bk in FINE_BLOCKS]
    elif stage == "2" and WIDE:
        bad = set(load_variant_table().get("runtime_unsupported_codecs", []))
        vs = [v for v in allv if v["codec"] not in bad and not v["reference_only"] and v["codec"] not in ("lz4", "lzo")]
    elif stage == "2":
        if shortlist is None:
            sys.exit("stage 2 needs results/shortlist.json from a stage 1 run")
        vs = [get_variant(i) for i in shortlist]
    elif stage == "3":
        if shortlist is None:
            sys.exit("stage 3 needs results/shortlist.json")
        top = [get_variant(i) for i in shortlist[:3] if "+" not in i]
        vs = []
        for v in top:
            if v["kind"] == "squashfs":
                vs += derive_option_variants(v)
        if any(v["kind"] == "dwarfs" for v in top):
            vs += [v for v in expand_variants() if v["stage3"] and v["kind"] == "dwarfs"]
        if not vs:
            sys.exit("nothing to do for stage 3")
    else:
        sys.exit("bad stage")
    if only:
        rx = re.compile(only)
        vs = [v for v in vs if rx.search(v["id"])]
    return vs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="1")
    ap.add_argument("--apps", default="", help="comma list (default: stage rules)")
    ap.add_argument("--variants-filter", default="")
    ap.add_argument("--results", default="results-branch")
    ap.add_argument("--include-synthetic", action="store_true")
    ap.add_argument("--retry", help="noisy.json from a previous run")
    ap.add_argument("--wide", action="store_true", help="stage 2: all runtime-mountable variants, not just the shortlist")
    a = ap.parse_args()
    global WIDE
    WIDE = a.wide
    tbl = load_variant_table()
    canary = tbl["canary"]
    corpus = [x for x in load_corpus()
              if (x.get("synthetic") is None or a.include_synthetic)]
    pinned = [x for x in corpus if x.get("synthetic") or x.get("sha256")]
    for x in corpus:
        if x not in pinned:
            print(f"warning: {x['name']} not pinned yet, skipped", file=sys.stderr)
    if a.apps:
        names = a.apps.split(",")
        pinned = [x for x in pinned if x["name"] in names]
    elif a.stage == "1":
        ref = [x for x in pinned if x.get("reference")]
        pinned = ref or pinned

    flags = {"no_startup": a.stage == "1", "no_zsync": a.stage == "1"}
    bench = []
    if a.stage == "ext" and not select_variants("ext", a.results, a.variants_filter):
        print("edge extension: nothing to extend, optima are interior", file=sys.stderr)
        go = os.environ.get("GITHUB_OUTPUT")
        empty = {"fetch": '{"include": []}', "bench": '{"include": []}', "codec": '{"include": []}',
                 "stage": "ext", "has_bench": "false", "has_codec": "false", "has_fetch": "false"}
        if go:
            with open(go, "a") as f:
                f.writelines(f"{k}={v}\n" for k, v in empty.items())
        else:
            print(json.dumps(empty))
        return
    if a.retry:
        noisy = json.loads(Path(a.retry).read_text())
        for n in noisy:
            app = next(x for x in pinned_all(corpus) if x["name"] == n["app"] and x.get("arch", "x86_64") == n["arch"])
            bench.append(entry(app, n["variants"], 1, flags_all(), 200, n["app"]))
    else:
        vs = select_variants(a.stage, a.results, a.variants_filter)
        # reference-only variants are only measured for size/build
        for app in pinned:
            mb = app_mb(app)
            groups, loads = pack(vs, mb, flags, canary)
            for i, (g, load) in enumerate(zip(groups, loads), 1):
                bench.append(entry(app, [v["id"] for v in g], i, flags,
                                   load, app["name"]))
    if len(bench) > MAX_JOBS:
        sys.exit(f"{len(bench)} jobs exceeds the matrix limit of 256; narrow --apps / --variants-filter")
    fetch = [{"app": x["name"], "arch": x.get("arch", "x86_64"),
              "runner": RUNNER[x.get("arch", "x86_64")], "cache_key": corpus_cache_key(x),
              "workset": x.get("launch", {}).get("mode") == "gui"}
             for x in pinned]
    codec = [{"app": x["name"], "arch": "x86_64", "runner": RUNNER["x86_64"],
              "cache_key": corpus_cache_key(x)} for x in pinned
             if a.stage in ("1", "full") and x.get("arch", "x86_64") == "x86_64"]
    out = {"fetch": {"include": fetch}, "bench": {"include": bench},
           "codec": {"include": codec}, "stage": a.stage,
           "has_bench": bool(bench), "has_codec": bool(codec), "has_fetch": bool(fetch)}
    print(f"stage {a.stage}: {len(fetch)} corpus jobs, {len(bench)} bench jobs, {len(codec)} codec jobs",
          file=sys.stderr)
    go = os.environ.get("GITHUB_OUTPUT")
    lines = {k: json.dumps(v) if isinstance(v, dict) else str(v).lower() if isinstance(v, bool) else str(v)
             for k, v in out.items()}
    if go:
        with open(go, "a") as f:
            for k, v in lines.items():
                f.write(f"{k}={v}\n")
    else:
        print(json.dumps(out, indent=1))


def pinned_all(corpus):
    return [x for x in corpus if x.get("synthetic") or x.get("sha256")]


def flags_all():
    return {"no_startup": False, "no_zsync": False}


def entry(app, variants, group, flags, load, name):
    minutes = MAX_JOB_MINUTES        # GitHub-hosted jobs may run up to 360 min; use the maximum
    return {"app": name, "arch": app.get("arch", "x86_64"), "group": group,
            "runner": RUNNER[app.get("arch", "x86_64")], "variants": ",".join(variants),
            "cache_key": corpus_cache_key(app), "timeout": minutes,
            "no_startup": flags["no_startup"], "no_zsync": flags["no_zsync"]}


if __name__ == "__main__":
    main()
