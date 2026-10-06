"""Synthetic raw result files for testing aggregate.py without any tools."""
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from common import expand_variants

def make(outdir, apps=("synthetic", "neovim"), startup=True):
    rnd = random.Random(1)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    for app in apps:
        recs = []
        for v in expand_variants(include_stage3=False)[:20] + [x for x in expand_variants() if x["id"] == "squashfs-gzip9-b128K"]:
            unc = 10_000_000
            tot = int(unc * rnd.uniform(0.3, 0.7)) + 500_000
            r = {"run_id": "t", "app": app, "arch": "x86_64", "category": "tiny-cli", "variant": v["id"],
                 "container": v["kind"], "codec": v["codec"], "level": v["level"],
                 "block_bytes": v["block_bytes"], "family_key": v["family_key"], "stage": "2",
                 "retry": False, "size": {"payload": tot - 500_000, "runtime": 500_000, "total": tot,
                                          "uncompressed": unc, "ratio": tot / unc},
                 "build": {"wall_s": rnd.uniform(1, 9), "cpu_s": 3, "rss_mb": 100, "deterministic": True},
                 "env": {"cpu": "test"}, "startup": {}, "zsync": []}
            s = {"build_wall_s": r["build"]["wall_s"], "mount_ms_warm": rnd.uniform(10, 50),
                 "cpu_s_startup_warm": rnd.uniform(.1, 1), "launch_ms_cold": rnd.uniform(100, 900),
                 "fuse_rss_mb_warm": 20, "workset_ms_warm": 5, "seq_mb_s_warm": 300}
            if startup:
                r["summary"] = s
            for b in (512, 4096, 65536):
                dl = int(tot * rnd.uniform(.05, .6))
                zf = tot // b * 20
                r["zsync"].append({"pair": "patch", "zsync_block": b, "zsync_file": zf, "downloaded": dl,
                                   "update_cost": zf + dl, "update_ratio": (zf + dl) / tot,
                                   "requests": 10, "ok": True})
            recs.append(r)
        canary = next(r for r in recs if r["variant"] == "squashfs-gzip9-b128K")
        for r in recs:
            r["canary"] = canary.get("summary", {})
        (Path(outdir) / f"{app}.json").write_text(json.dumps({"records": recs, "app": app, "arch": "x86_64",
                                                             "reference": []}))
if __name__ == "__main__":
    make(sys.argv[1])
