#!/usr/bin/env python3
"""raw JSON -> markdown tables, plots, shortlist.json, noisy.json.

Timing metrics are normalised by the canary measured in the SAME job
(canary == current default, so ratios are relative to the baseline).
Size/zsync metrics are deterministic and relative to the baseline record of
the same app. Lower is better everywhere."""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from common import (load_variant_table, expand_variants, human, median, load_yaml, get_variant,
                    lever_value, parse_size)

TIMING = ("mount_ms_warm", "cpu_s_startup_warm", "launch_ms_cold", "build_wall_s",
          "fuse_rss_mb_warm", "workset_ms_warm", "seq_mb_s_warm")
# "launch (cold)" times the whole ./app.AppImage run, so it already contains the runtime mount;
# the standalone mount time is only a diagnostic (Table 4), not a headline metric.
HEAD = [("total size", "size_total"),
        ("startup CPU", "cpu_s_startup_warm"), ("app start: mount + launch (cold)", "launch_ms_cold"),
        ("update cost", "update_cost"), ("build time", "build_wall_s"),
        ("RAM (FUSE)", "fuse_rss_mb_warm")]


def merge(a, b):
    for k, v in b.items():
        if v in (None, [], {}):
            continue
        if isinstance(v, dict) and isinstance(a.get(k), dict) and k not in ("env",):
            merge(a[k], v)
        else:
            a[k] = v
    return a


def load(paths):
    recs, ref, codec = {}, [], []
    for p in sorted(paths):
        d = json.loads(Path(p).read_text())
        codec += [dict(r, app=d.get("app")) for r in d.get("codec_records", [])]
        ref += [dict(r, app=d["app"]) for r in d.get("reference", [])]
        for r in d.get("records", []):
            key = (r["app"], r["arch"], r["variant"])
            if key in recs and r.get("retry") is False and recs[key].get("retry"):
                continue
            if key in recs:
                if r.get("retry"):          # retry replaces noisy timings wholesale
                    recs[key]["startup"] = r.get("startup", {})
                    recs[key]["summary"] = r.get("summary", {})
                    recs[key]["canary"] = r.get("canary", {})
                    recs[key]["noisy"] = r.get("noisy", [])
                else:
                    merge(recs[key], r)
            else:
                recs[key] = r
    return list(recs.values()), ref, codec


def geomean(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else None


def update_cost(rec, pair="patch"):
    """Best total update cost (zsync file + download) over the tested block sizes."""
    rows = [z for z in rec.get("zsync", []) if z["pair"] == pair and z["ok"]]
    if not rows:
        return None, None
    best = min(rows, key=lambda z: z["update_cost"])
    return best["update_cost"], best["zsync_block"]


def metric(rec, m):
    if m == "size_total":
        return rec.get("size", {}).get("total")
    if m == "update_cost":
        return update_cost(rec)[0]
    return rec.get("summary", {}).get(m)


def rel(rec, m, base):
    """Relative to baseline: timing -> canary ratio of same job, else baseline record."""
    v = metric(rec, m)
    if v is None:
        return None
    if m in TIMING:
        c = rec.get("canary", {}).get(m)
        if m == "seq_mb_s_warm" and v and c:
            return c / v            # throughput: invert so lower is better
        return v / c if c else None
    b = base.get((rec["app"], rec["arch"]))
    bv = metric(b, m) if b else None
    return v / bv if bv else None


def score(cells, weights):
    """Weighted geometric mean over the metrics that are available."""
    num = den = 0.0
    for m, w in weights.items():
        v = cells.get(m)
        if v and v > 0:
            num += w * math.log(v)
            den += w
    return (math.exp(num / den), den) if den else (None, 0)


def _next_t(e, ext, direction):
    """Next integer axis position beyond edge `e` towards range extreme `ext`:
    halfway (rounded toward e); the extreme itself once adjacent."""
    if direction < 0:
        if e <= ext:
            return None
        return ext if e - ext <= 1 else math.ceil((e + ext) / 2)
    if e >= ext:
        return None
    return ext if ext - e <= 1 else math.floor((e + ext) / 2)


def _fmt_lever(spec, t):
    if spec.get("scale") == "log2":
        n = 2 ** int(round(t))
        return f"{n >> 20}M" if n % (1 << 20) == 0 else f"{n >> 10}K"
    return int(round(t))


def propose_extensions(score_of):
    """score_of: {variant id: weighted score (lower better)}.
    For every family lever whose best tested value sits on the low/high edge of the
    tested values, propose the next point beyond it. Returns {new id: reason}."""
    fams = {f["name"]: f for f in load_variant_table()["families"]}
    byfam = defaultdict(list)
    for vid, sc in score_of.items():
        if "+" in vid:
            continue
        try:
            v = get_variant(vid)
        except KeyError:
            continue
        byfam[v["family"]].append((sc, v))
    out = {}
    for fam, items in byfam.items():
        for lever, spec in (fams[fam].get("lever_ranges") or {}).items():
            best_at = {}                      # axis position -> (score, variant)
            for sc, v in items:
                t = lever_value(spec, v["params"][lever])
                if t not in best_at or sc < best_at[t][0]:
                    best_at[t] = (sc, v)
            if len(best_at) < 2:
                continue
            ts = sorted(best_at)
            win = min(ts, key=lambda t: best_at[t][0])
            lo, hi = lever_value(spec, spec["min"]), lever_value(spec, spec["max"])
            for edge, direction, ext in ((ts[0], -1, lo), (ts[-1], 1, hi)):
                if win != edge:
                    continue
                nt = _next_t(round(edge), ext, direction)
                if nt is None:
                    continue
                params = dict(best_at[win][1]["params"])
                params[lever] = _fmt_lever(spec, nt)
                try:
                    vid = fams[fam]["id"].format(**params)
                    get_variant(vid)
                except (KeyError, IndexError):
                    continue
                if vid not in score_of:
                    out[vid] = (f"{fam}: best {lever}={best_at[win][1]['params'][lever]} is the "
                                f"{'low' if direction < 0 else 'high'} edge of the tested values "
                                f"-> try {lever}={params[lever]}")
    return out


def fmt(x, nd=2):
    return "" if x is None else f"{x:.{nd}f}"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def pareto(points):
    """points: {id: tuple}; returns ids not dominated (lower is better)."""
    keep = []
    for i, p in points.items():
        if not any(all(q[k] <= p[k] for k in range(len(p))) and q != p
                   for j, q in points.items() if j != i):
            keep.append(i)
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", nargs="+", required=True, help="dirs with *.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    files = [p for d in a.raw for p in Path(d).rglob("*.json")
             if p.name not in ("shortlist.json", "noisy.json")]
    recs, ref, codec = load(files)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tbl = load_variant_table()
    baseline = tbl["default"]
    WT = load_yaml("variants/weights.yml")["weights"]
    W = {k: v["weight"] for k, v in WT.items()}
    compat = {v["id"]: v["compat"] for v in expand_variants()}
    base = {(r["app"], r["arch"]): r for r in recs if r["variant"] == baseline}
    md = ["# AppImage compression benchmark results\n",
          f"{len(recs)} (app, arch, variant) records from "
          f"{len({(r['app'], r['arch']) for r in recs})} app/arch pairs. Lower is better; "
          f"baseline = `{baseline}` = 1.00. Measured on GitHub-hosted runners "
          f"({', '.join(sorted({r['env'].get('cpu', '?') for r in recs if r.get('env')})) or '?'}); "
          "real hardware (HDD, slow ARM) shifts startup conclusions toward higher ratio at "
          "lower CPU cost.\n"]

    tot = sum(W.values())
    md.append("## Metric weights (variants/weights.yml)\n")
    md.append(table(["metric", "weight"], [[v["label"], f"{100 * v['weight'] / tot:.0f}%"]
                                          for v in WT.values()]))
    md.append("\nWeighted score = weighted geometric mean of metric/baseline (lower is better); "
              "metrics without data (e.g. startup/zsync in stage 1) are left out and the rest renormalised "
              "(see *weight coverage*).\n")

    # ---- Table 1/5: headline and per category
    variants = sorted({r["variant"] for r in recs})
    unsup = {r["variant"]: r["runtime_unsupported"] for r in recs if r.get("runtime_unsupported")}
    by_v = defaultdict(list)
    for r in recs:
        by_v[r["variant"]].append(r)

    def headline(rs_by_v, title):
        rows = []
        for v in variants:
            rs = rs_by_v.get(v, [])
            if not rs:
                continue
            vals = {m: geomean([rel(r, m, base) for r in rs]) for _, m in HEAD}
            sc, cov = score(vals, W)
            cells = [fmt(vals[m]) for _, m in HEAD]
            rows.append((v, cells, len(rs), sc, cov))
        rows.sort(key=lambda x: x[3] if x[3] else 99)
        md.append(f"## {title}\n")
        md.append(table(["variant", "weighted score"] + [h for h, _ in HEAD] + ["apps", "weight coverage"],
                        [[("**" + v + "**") if v == baseline else v, fmt(sc, 3)] + c + [n, f"{cov:.0f}%"]
                         for v, c, n, sc, cov in rows]))
        md.append("")
        return [r[:3] for r in rows]

    head_rows = headline(by_v, "Table 1 - Headline (geometric mean over corpus, relative to baseline)")

    cats = sorted({r.get("category") for r in recs if r.get("category")})
    for c in cats:
        sub = defaultdict(list)
        for r in recs:
            if r.get("category") == c:
                sub[r["variant"]].append(r)
        if len(cats) > 1:
            headline(sub, f"Table 5 - Category: {c}")

    # ---- Table 2: per app size
    md.append("## Table 2 - Total size per app (MB)\n")
    apps = sorted({(r["app"], r["arch"]) for r in recs})
    top = [x[0] for x in head_rows[:6]]
    if baseline not in top:
        top.append(baseline)
    rows = []
    for app, arch in apps:
        cell, best = {}, (None, None)
        for r in recs:
            if (r["app"], r["arch"]) == (app, arch) and r.get("size", {}).get("total"):
                cell[r["variant"]] = r["size"]["total"]
                if best[0] is None or r["size"]["total"] < best[0]:
                    best = (r["size"]["total"], r["variant"])
        unc = next((r["size"]["uncompressed"] for r in recs if (r["app"], r["arch"]) == (app, arch)), 0)
        rows.append([f"{app}/{arch}", f"{unc / 1e6:.1f}"] +
                    [f"{cell[v] / 1e6:.1f}" if v in cell else "" for v in top] + [best[1]])
    md.append(table(["app", "uncompressed"] + top + ["best"], rows))
    md.append("")

    # ---- Table 3: zsync per variant and block (patch pairs, median over apps)
    md.append("## Table 3 - zsync update cost (median over patch pairs)\n")
    rows = []
    for v in variants:
        per_b = defaultdict(list)
        for r in by_v[v]:
            for z in r.get("zsync", []):
                if z["pair"] == "patch" and z["ok"]:
                    per_b[z["zsync_block"]].append(z)
        for b, zs in sorted(per_b.items()):
            rows.append([v, b, human(median([z["zsync_file"] for z in zs])),
                         human(median([z["downloaded"] for z in zs])),
                         human(median([z["update_cost"] for z in zs])),
                         f"{100 * median([z['update_ratio'] for z in zs]):.1f}%",
                         int(median([z["requests"] for z in zs]))])
    md.append(table(["variant", "zsync -b", ".zsync", "downloaded", "total update",
                     "% of image", "requests"], rows) if rows else "_no zsync data (stage 1 only)_")
    md.append("")
    rb = [r for r in recs for z in r.get("zsync", []) if z["pair"] == "rebuild"]
    bad = [(r["variant"], r["app"]) for r in rb
           if any(z["pair"] == "rebuild" and z["downloaded"] > 0.01 * r["size"]["total"] for z in r["zsync"])]
    md.append(f"No-change rebuild: {len(rb)} variants tested; "
              f"{len(bad)} downloaded >1% (non-determinism / unstable layout): "
              f"{', '.join(sorted({b[0] for b in bad})) or 'none'}\n")
    if ref:
        md.append("Ideal references on raw AppDir tars: " +
                  ", ".join(f"{r['app']}/{r['pair']} xdelta3={human(r['xdelta3_bytes'])}"
                            for r in ref if r.get("xdelta3_bytes")) + "\n")

    # ---- Table 3b / 4: block-size grids, grouped by (container, codec, level)
    md.append("## Table 3b - Compression block x zsync block (update cost, % of image; best per row bold)\n")
    groups = defaultdict(list)
    for r in recs:
        if "+" not in r["variant"]:
            groups[(r["container"], r["codec"], r["level"], r["family_key"])].append(r)
    for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        blocks = sorted({r["block_bytes"] for r in rs if r.get("block_bytes")})
        if len(blocks) < 2:
            continue
        zb = sorted({z["zsync_block"] for r in rs for z in r.get("zsync", [])})
        if not zb:
            continue
        rows = []
        for b in blocks:
            vals = []
            for zbk in zb:
                xs = [z["update_ratio"] for r in rs if r["block_bytes"] == b
                      for z in r.get("zsync", []) if z["pair"] == "patch" and z["ok"]
                      and z["zsync_block"] == zbk]
                vals.append(median(xs))
            best = min((v for v in vals if v is not None), default=None)
            rows.append([human(b)] + [("**%.1f**" if v == best else "%.1f") % (100 * v) if v is not None else ""
                                      for v in vals])
        md.append(f"**{key[0]} {key[1]} level {key[2]}**\n")
        md.append(table(["compression block \\ zsync -b"] + [human(x) for x in zb], rows))
        md.append("")

    md.append("## Table 4 - Block-size sweep (mid-level compressor)\n")
    pick = next((k for k in groups if k[1] == "zstd" and k[2] == 12), None) or \
        next((k for k, rs in groups.items() if len({r['block_bytes'] for r in rs}) >= 3), None)
    if pick:
        rows = []
        for b in sorted({r["block_bytes"] for r in groups[pick] if r.get("block_bytes")}):
            rs = [r for r in groups[pick] if r["block_bytes"] == b]
            sm = lambda k: median([r.get("summary", {}).get(k) for r in rs])
            ratio = median([r["size"].get("ratio") for r in rs if r.get("size")])
            upd = median([update_cost(r)[0] / r["size"]["total"] * 100 for r in rs
                          if update_cost(r)[0] and r.get("size", {}).get("total")])
            rows.append([human(b), fmt(ratio, 3), fmt(sm("mount_ms_warm"), 0), fmt(sm("workset_ms_warm"), 0),
                         fmt(sm("seq_mb_s_warm"), 0), fmt(upd, 1)])
        md.append(f"{pick[0]} {pick[1]} level {pick[2]} (median over apps)\n")
        md.append(table(["block", "size ratio", "mount ms", "workset ms", "seq MB/s", "update %"], rows))
    md.append("")

    # ---- Table 6: compat + codec-only
    md.append("## Table 6 - Compatibility (recorded, not measured) and codec-only reference\n")
    seen, rows = set(), []
    for v in variants:
        c = compat.get(v.split("+")[0], {})
        fam = v.split("-b")[0] if v.startswith("squashfs") else v.split("-S")[0]
        if fam in seen:
            continue
        seen.add(fam)
        rows.append([fam, c.get("kernel", ""), c.get("fuse", ""), c.get("static_reader", "")])
    md.append(table(["family", "min kernel", "FUSE", "static reader"], rows))
    md.append("")
    if codec:
        agg = defaultdict(list)
        for c in codec:
            if "ratio" in c:
                agg[c["id"]].append(c["ratio"])
        md.append(table(["codec-only (tar stream)", "geomean ratio", "note"],
                        [[k, fmt(geomean(v), 3), "no random access, no startup metric"]
                         for k, v in sorted(agg.items(), key=lambda kv: geomean(kv[1]))]))
        md.append("")

    # ---- decision rule
    md.append("## Decision rule\n")
    scored = []
    for v in variants:
        vals = {m: geomean([rel(r, m, base) for r in by_v[v]]) for _, m in HEAD}
        sc, cov = score(vals, W)
        if sc and v not in unsup and v.split("+")[0] not in {x["id"] for x in expand_variants() if x["reference_only"]}:
            scored.append((sc, v, cov))
    scored.sort()
    if scored:
        md.append("Best by weighted score: " + ", ".join(f"`{v}` ({sc:.3f}, coverage {cov:.0f}%)"
                                                        for sc, v, cov in scored[:3]) + "\n")
    cand = {}
    for v, cells, n in head_rows:
        d = dict(zip([m for _, m in HEAD], cells))
        cand[v] = {k: float(x) if x else None for k, x in d.items()}
    ok = []
    # The +5% / +10% bounds are relative to the best of the top-scoring pool, not the global
    # best: the global bests come from different variants and no variant is within both.
    pool_ids = [v for _, v, _ in scored[:10]]
    pool = [cand[v] for v in pool_ids if v in cand and cand[v].get("launch_ms_cold") is not None
            and cand[v].get("update_cost") is not None]
    best_up = min((c["update_cost"] for c in pool if c["update_cost"]), default=None)
    best_cpu = min((c["cpu_s_startup_warm"] for c in pool if c["cpu_s_startup_warm"]), default=None)
    nondet = {r["variant"] for r in recs if r.get("build", {}).get("deterministic") is False}
    zfail = {r["variant"] for r in recs for z in r.get("zsync", []) if not z["ok"]}
    for v, c in cand.items():
        if v not in pool_ids or cand[v].get("launch_ms_cold") is None or cand[v].get("update_cost") is None:
            continue                       # need real startup + zsync data and a top-10 score
        if v in nondet or v in zfail or v in unsup or v.split("+")[0] in {x["id"] for x in expand_variants() if x["reference_only"]}:
            continue
        if best_up and c["update_cost"] and c["update_cost"] > 1.05 * best_up:
            continue
        if best_cpu and c["cpu_s_startup_warm"] and c["cpu_s_startup_warm"] > 1.10 * best_cpu:
            continue
        ok.append(v)
    md.append("1. among the 10 best weighted scores, keep variants within +5% of the best update cost and "
              "+10% of the best warm startup CPU (when those metrics exist);\n2. choose the smallest total size (sizes within 1.5% tie; the tie goes to the best weighted score);\n"
              "3. reject non-deterministic builds, zsync verification failures, reference-only variants.\n")
    if ok:
        smallest = min(cand[v]["size_total"] for v in ok)
        tied = [v for v in ok if cand[v]["size_total"] <= smallest * 1.015]   # sizes within 1.5% are a tie
        win = min(tied, key=lambda v: next(sc for sc, vv, _ in scored if vv == v))   # tie -> best weighted score
        md.append(f"**Winner: `{win}`**; candidates: {', '.join(ok[:8])}\n")
    else:
        md.append("_no candidate satisfies the rule yet (need stage 2 data)_\n")
    for label, m in (("smallest", "size_total"), ("fastest startup (CPU)", "cpu_s_startup_warm"),
                     ("cheapest update", "update_cost"), ("fastest build", "build_wall_s")):
        vals = [(c[m], v) for v, c in cand.items() if c.get(m)]
        vals.sort()
        if vals:
            md.append(f"- {label}: `{vals[0][1]}` ({vals[0][0]:.2f}), runner-up "
                      f"`{vals[1][1] if len(vals) > 1 else '-'}`")
    md.append("")
    if nondet:
        md.append(f"**Non-deterministic builds:** {', '.join(sorted(nondet))}\n")

    # ---- shortlist (Pareto) and noisy
    pts = {}
    for v, c in cand.items():
        if "+" in v or v in unsup:
            continue
        axes = [c.get("size_total"), c.get("cpu_s_startup_warm"), c.get("update_cost")]
        axes = [x for x in axes if x is not None]
        if len(axes) == 0:
            continue
        pts[v] = tuple(c.get(k) for k in ("size_total", "cpu_s_startup_warm", "update_cost")
                       if c.get(k) is not None) or (1.0,)
    lens = {len(p) for p in pts.values()}
    if len(lens) > 1:      # mixed availability: fall back to size and build time
        pts = {v: (cand[v]["size_total"], cand[v]["build_wall_s"] or 1) for v in pts
               if cand[v].get("size_total")}
    keep = pareto(pts)
    sc_of = {v: sc for sc, v, _ in scored}
    keep = sorted(set(keep) | {v for _, v, _ in scored[:3] if "+" not in v},
                  key=lambda v: sc_of.get(v, 99))      # best weighted score first
    short = keep[:11]
    if baseline not in short:
        short.append(baseline)
    (out / "shortlist.json").write_text(json.dumps({"variants": short}, indent=1))
    noisy = defaultdict(list)
    for r in recs:
        if r.get("noisy") and not r.get("retry"):
            noisy[(r["app"], r["arch"])].append(r["variant"])
    (out / "noisy.json").write_text(json.dumps(
        [{"app": a_, "arch": ar, "variants": vs} for (a_, ar), vs in noisy.items()], indent=1))
    if unsup:
        md.append("**Not mountable by the pinned runtime** (excluded from ranking; sizes still shown above): " +
                  ", ".join(f"`{v}`" for v in sorted(unsup)) + f" - runtime says: _{next(iter(unsup.values()))}_\n")
    pool = {v: sc for sc, v, cov in scored if cov >= 80} or {v: sc for sc, v, cov in scored}
    ext = propose_extensions(pool)
    (out / "extensions.json").write_text(json.dumps({"variants": sorted(ext), "reasons": ext}, indent=1))
    md.append("## Edge extension (levers whose best value is at the edge of the tested range)\n")
    md.append("\n".join(f"- `{v}`: {r}" for v, r in sorted(ext.items())) if ext else
              "_no lever has its best value at an edge: all optima are interior (or at the range extreme)_")
    md.append("")
    md.append(f"Shortlist for the next stage: {', '.join(f'`{v}`' for v in short)}\n")
    md.append(f"Noisy (CV>10%) variants queued for retry: {sum(len(v) for v in noisy.values())}\n")

    (out / "report.md").write_text("\n".join(md))
    (out / "records.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    plots(recs, cand, baseline, out, keep)
    print("\n".join(md))


def plots(recs, cand, baseline, out, keep):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    def scatter(xm, ym, name, xl, yl):
        pts = [(c[xm], c[ym], v) for v, c in cand.items() if c.get(xm) and c.get(ym)]
        if not pts:
            return
        fig, ax = plt.subplots(figsize=(8, 6))
        for x, y, v in pts:
            ax.scatter(x, y, c="tab:red" if v in keep else "tab:blue")
            ax.annotate(v, (x, y), fontsize=6)
        if baseline in cand and cand[baseline].get(xm):
            ax.scatter([cand[baseline][xm]], [cand[baseline][ym]], s=200, facecolors="none", edgecolors="k")
        ax.set_xlabel(xl); ax.set_ylabel(yl); ax.set_title(name + " (red = Pareto, circled = baseline)")
        fig.savefig(out / f"{name}.png", dpi=120, bbox_inches="tight")
        plt.close(fig)

    scatter("size_total", "cpu_s_startup_warm", "size-vs-startup-cpu", "total size (rel)", "startup CPU (rel)")
    scatter("size_total", "update_cost", "size-vs-update", "total size (rel)", "update cost (rel)")
    bt = sorted([(c["build_wall_s"], v) for v, c in cand.items() if c.get("build_wall_s")])
    if bt:
        fig, ax = plt.subplots(figsize=(8, max(4, len(bt) * 0.2)))
        ax.barh([v for _, v in bt], [x for x, _ in bt]); ax.set_xscale("log")
        ax.set_xlabel("build wall time (rel to baseline)")
        fig.savefig(out / "build-time.png", dpi=120, bbox_inches="tight"); plt.close(fig)
    apps = sorted({(r["app"], r["arch"]) for r in recs})
    vs = sorted({r["variant"] for r in recs})
    grid = [[next((r["size"]["ratio"] for r in recs if (r["app"], r["arch"]) == ap_ and r["variant"] == v
                   and r.get("size", {}).get("ratio")), float("nan")) for ap_ in apps] for v in vs]
    if grid and apps:
        fig, ax = plt.subplots(figsize=(max(4, len(apps) * 1.2), max(4, len(vs) * 0.2)))
        im = ax.imshow(grid, aspect="auto")
        ax.set_xticks(range(len(apps)), [a[0] for a in apps], rotation=45)
        ax.set_yticks(range(len(vs)), vs, fontsize=6); fig.colorbar(im)
        ax.set_title("size ratio (total / uncompressed)")
        fig.savefig(out / "size-heatmap.png", dpi=120, bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    main()
