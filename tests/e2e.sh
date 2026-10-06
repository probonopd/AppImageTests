#!/bin/bash
# End-to-end pipeline test on the synthetic app with one variant per container.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/bin:$PATH"
mkdir -p e2e-out
python3 tools/fetch_corpus.py --app synthetic
V="squashfs-zstd12-b128K,squashfs-gzip3-b32K"
command -v mkdwarfs >/dev/null && V="$V,dwarfs-l5-S20"
python3 tools/run_group.py --app synthetic --variants "$V" --stage 2 \
  --reps-warm 2 --reps-cold 2 --reps-launch 1 --out e2e-out/synthetic.json
python3 tools/codec_reference.py --app synthetic --out e2e-out/codec-synthetic.json
python3 tools/aggregate.py --raw e2e-out --out e2e-out/report > /dev/null
python3 - <<'PY'
import json
d = json.load(open("e2e-out/synthetic.json"))
for r in d["records"]:
    assert r["size"]["total"] > 0, r["variant"]
    assert r["build"]["deterministic"] is True, f"{r['variant']} not deterministic"
    assert r["startup"]["mount"]["warm"] and "error" not in r["startup"]["mount"]["warm"][0], r["variant"]
    ok = [z for z in r["zsync"] if z["pair"] == "patch"]
    assert ok and all(z["ok"] for z in r["zsync"]), f"zsync failed for {r['variant']}"
    rb = [z for z in r["zsync"] if z["pair"] == "rebuild"]
    print(r["variant"], "total", r["size"]["total"], "patch update", min(z["update_cost"] for z in ok))
print("e2e ok")
PY
cat e2e-out/report/report.md | head -40
