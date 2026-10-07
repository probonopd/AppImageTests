# AppImage compression benchmark

Answers AppImageSpec issue #44 with data: which container / compressor / block
size is the best default for AppImages, judged by size, startup time and zsync
delta-update efficiency. Design: [docs/AppImageCompressionTests.md](docs/AppImageCompressionTests.md).
Everything runs on GitHub Actions.

## Recommendation

**Use SquashFS with zstd level 7 and a 256K block size**
(`mksquashfs ... -comp zstd -Xcompression-level 7 -b 256K`) as the default AppImage
container, with the existing type2 runtime. Block 128K is a near-equal alternative that uses
about a quarter less FUSE RAM.

Compared with today's default (gzip level 9, 128K blocks). Figures are geometric means over
a 6-app corpus; 1.00 is today's default, lower is better:

| Metric | zstd 7 / 256K | What it means |
|---|---|---|
| Download size | 0.96 | 4% smaller |
| App start: mount + launch, cold cache | 0.54 | about 45% faster first start (one measurement: `./app.AppImage` to first window) |
| Startup CPU (mount + working set) | 0.33 | one third of the CPU |
| zsync update cost | 0.91 | 9% less to download per update |
| Build time | 0.18 | 5x faster to create |
| FUSE process RAM | 1.38 | the one cost; 128K blocks: 1.02 |

Every one of these metrics is equal or better than today except memory at 256K blocks. The
automatic decision rule ([full report](https://github.com/probonopd/AppImageTests/blob/results/latest/report.md))
selects this variant. All measurements go through the runtime's own FUSE mount (squashfuse in
the type2 runtime, `--appimage-mount`), exactly as users run AppImages, so no kernel squashfs
support is involved: the only requirement is FUSE, as for every AppImage today. zstd needs a
runtime built with zstd support (the pinned type2 runtime has it).

### Why we are confident

1. **It is the only family that is never bad.** Whatever mix of priorities is used, the
   winner is SquashFS + zstd (see the sensitivity table). Level and block size move the score by
   a few percent; leaving the family costs 20-80%.
2. **It improves the metrics people actually feel** (first-start time, CPU, bytes
   downloaded per update) while keeping the proven SquashFS format and the
   current runtime. Nothing else we tested improves startup without a large penalty elsewhere.
3. **The result is measured on real AppImages, not synthetic data**: neovim, KeePassXC,
   Obsidian, Krita, Kdenlive and LibreOffice, with the update cost computed from real older
   releases using real zsync over an HTTP range server.
4. **The measurement is controlled.** Variants being compared run interleaved in the same
   job on the same runner, every job also contains the current default as a canary to
   normalise runner differences, and every image was built twice and verified bit-identical.
5. **Level 7 is not a guess**: the grid around it (levels 5/7/9 x blocks 64K-512K, plus
   automatic edge extension up to the range limits) was searched and the optimum is
   interior; 512K blocks and levels 5 and 9 are worse.

### Robust to different priorities

The weights are a judgement call, so we re-ranked all 70 fully measured variants under other
priorities (best three each; score vs today's default):

| If you only care about... | Winner | Is zstd7 / 256K good enough? |
|---|---|---|
| Our weights (size 35, app start 20, startup CPU 8, updates 24, RAM 10, build 3) | squashfs-zstd12-b32K 0.73; zstd7-b256K 0.77 | yes: 5th of 70, 0.04 behind (128K is 3rd) |
| Speed (app start incl. mount, CPU) | squashfs-zstd12-b32K 0.41 | yes: 4th of 70 (0.42 vs 0.41) |
| Bandwidth (size + updates) | squashfs-zstd17-b256K 0.87 | 0.94 (10th of 70), but builds about 10x faster |
| All six metrics equally | squashfs-zstd3-b32K 0.49 | 0.58 (13th of 70); 128K: 0.56 (6th) |
| Download size alone | DwarFS (mkdwarfs zstd22 -S26 -B6, hotness list) 0.74 | no: 0.96 (see below) |
| Cold launch alone | DwarFS zstd7 -S20 0.29 | no: 0.54 (see below) |
| Update cost alone | DwarFS -l2 -S24 0.72 | no: 0.91 (see below) |
| Memory alone | squashfs-gzip7-b16K 0.49 | no: 1.38 (use 128K or 64K) |
| Build time alone | squashfs-zstd3-b32K 0.07 | no: 0.18 |

Only one-dimensional preferences point somewhere else, and each of those gives up a lot
elsewhere (below). Any balanced weighting lands on SquashFS + zstd.

### "But I prefer ..." answers

**"DwarFS compresses better and starts faster."** True on two axes, and we say so: the best
DwarFS configuration is 20-26% smaller than today's default (ours is 4% smaller), and cold
launch is up to 2x faster than ours. It pays for that: mount is about 4-5x slower, the FUSE
process uses 12-40x more RAM, startup CPU is up to 2.6x of today's for the small-size settings (ours is 0.33), builds are up to
4x slower than today's, and zsync updates cost 1.4-2.0x of today's (ours 0.91). The one DwarFS setting with
cheap updates (low level, large blocks) is 11-16% larger than today's default. DwarFS also
needs a different runtime (uruntime) and FUSE3 instead of the current type2 runtime. We measured the
project's own mkdwarfs setup (`zstd:level=22 -S26 -B6 --order=path`, with and without
`--hotness-list`): size 0.74-0.77, but mount 5x, RAM 12-16x and updates 1.5x; the hotness list
made images slightly smaller but did not speed up cold launch (0.52 vs 0.42), which may mean
the recorded working set does not match what the launch measures. If DwarFS's memory and mount
costs can be fixed, it deserves a re-test.

**"xz / lzma compresses smallest."** xz squashfs is 7-19% smaller than today's default, but
the type2 runtime cannot mount xz at all (it only supports zlib and zstd), builds are 2-5x
slower, and zstd 17 gets within 5-8% of its size while being mountable. DwarFS-lzma costs 7x
startup CPU and 35-40x RAM.

**"gzip is the safe, compatible choice."** gzip is the most widely supported codec, but
the runtime we ship already reads zstd, and the squashfs reader is the runtime's own FUSE
code, not the user's kernel, so there is no kernel-version compatibility argument for gzip.
It scores 0.92-1.05 against zstd's 0.75-0.85 and is never ahead of zstd on launch, CPU, size or
updates. Level 3 builds faster, but it is 5-9% larger than the default. The remaining risk is
old AppImages' runtimes without zstd: images using zstd cannot be opened by an old runtime, so
the runtime embedded in the image must be one that supports it (it is part of the AppImage).

**"Use a higher zstd level; size matters most."** Level 17 is 12% smaller, but builds
about 10x slower than level 7 and is not faster to launch; it is a reasonable choice if build
time is free and only the download matters (score 0.83-0.86, third in the bandwidth ranking).
Level 12 is in between and wins on neither.

**"Use a lower level / smaller block for speed and RAM."** zstd 3-5 builds 3-5x faster, but is
4-8% larger and slower to launch than level 7; 16K blocks are worse in every way (score 0.84).
Smaller blocks (64K) do reduce FUSE RAM a lot (0.74), and 64K scored best in one run; the
difference to 128K/256K is within noise, which is why 128K is listed as an equal alternative.

**"lz4 / lzo are fastest."** They are 9-17% larger than today's default, and we could not measure
their startup (no startup data for them), so they offer a size penalty with no demonstrated benefit.

**"Fancy squashfs flags (-no-fragments, -sort, ...)."** All within about 0.01-0.03 of the
same variant without a flag, inside the noise. `-no-fragments` was the most promising and may
be worth adding after repeated runs; it is not part of the recommendation.

**"Your weights are wrong."** Change them: they are one file, `variants/weights.yml`, and
the report is recomputed from the stored raw data by `tools/aggregate.py`. The sensitivity
table above shows what happens for the extreme choices.

### What would change this recommendation

- A DwarFS configuration with low mount time and RAM, or a runtime that makes the cost vanish.
- Real-disk or slow-ARM measurements that favour a different level or block size (not done yet).
- Repeated runs showing 64K or 128K blocks beat 256K by more than the noise (the weighted
  scores of the three are within about 0.03 today).
- Apps with very different content (not tested: games, scientific, interpreted apps).

### Caveats we know about

- SquashFS results are for squashfuse as built into the pinned type2 runtime (its sha256 is in
  `corpus/runtimes.yml`; each record names the runtime build). A different squashfuse version
  (caching, threading) could shift mount time, CPU and RAM. The RAM trend with block size
  (64K: 0.74, 128K: 1.02, 256K: 1.38, 512K: 3.0) fits a per-block cache in the FUSE process.
- Measured on GitHub-hosted runners with fast virtual disks; real hard disks or slow ARM CPUs
  shift startup results toward higher compression at lower CPU cost. A throttled-disk test and
  aarch64 apps are open items.
- Differences of about 0.03 in the weighted score are within noise (many variants were flagged
  for retry), especially between block sizes.
- The corpus is 6 apps (neovim, KeePassXC, Obsidian, Krita, Kdenlive, LibreOffice); update cost
  uses 1-2 older releases per app and zsync blocks of 1K-4K.
- Cold launch is a real GUI start under Xvfb with the page cache dropped; it measures
  decompression and I/O, not rendering.

## Test plan: three points per continuous lever

Every continuous lever is tested at **low / mid / high** only (see below), so the grid
stays small and can be refined around the winner later:

| Lever (range, mid) | low | mid | high |
|---|---|---|---|
| squashfs gzip level (1-9, 5) | 3 | 5 | 7 |
| squashfs zstd level (1-22, 12) | 7 | 12 | 17 |
| squashfs block, all codecs (4K-1M, 128K) | 32K | 128K | 256K |
| DwarFS preset `-l` (0-9, 5) | 3 | 5 | 7 |
| DwarFS block `-S` (12-28, 20) | 16 (64K) | 20 (1M) | 24 (16M) |
| DwarFS zstd level (1-22, 12) | 7 | 12 | 17 |
| DwarFS lzma level (0-9, 5) | 3 | 5 | 7 |
| DwarFS brotli quality (0-11, 6) | 3 | 6 | 8 |
| zsync `-b` (512-64K) | 1K | 2K | 4K (window moved down: 2K beat 4K/16K in the first measurement) |
| codec-only zstd level / `--long` / xz / brotli | 7 / 22 / 3 / 3 | 12 / 24 / 6 / 6 | 17 / 25 / 7 / 8 |

Low/high are **halfway between mid and the lever's min/max**, not the
extremes (powers of two: halfway on the log2 scale, rounded toward mid). Go to
the extremes later only if results suggest it.

Level x block are crossed (3x3) for gzip, zstd and DwarFS presets, and every
compression block size is crossed with every zsync block size (3x3 grid).
Categorical options (xz BCJ on/off, lz4, lzo, DwarFS ordering, categorize,
squashfs `-no-fragments/-no-tailends/-no-duplicates/-sort`) are listed
explicitly in [`variants/variants.yml`](variants/variants.yml). The unit tests
enforce the "max 3 points per lever" rule.

## Automatic edge extension

Low/high are not the extremes, but the optimum may lie beyond them. After every
stage `aggregate.py` finds, per family and lever, the best tested value (by the
weighted score). If it sits on the **low or high edge** of the tested values, the
next point beyond it is proposed: halfway to the lever's hard range
(`lever_ranges` in `variants/variants.yml`), or the range extreme itself once
adjacent. The proposals are written to `extensions.json`, listed in the report
("Edge extension") and measured by stage `ext` (full startup + zsync). Other
levers are held at the best variant's values, so each proposal is one new
variant, not a cross product. The loop stops when every optimum is interior
or at the range extreme (or after 3 rounds with `chain`). New ids such as
`squashfs-zstd4-b512K` resolve dynamically if all levers are inside their range.

## Metric weights

Final ranking uses a weighted geometric mean of each metric relative to the
baseline (`variants/weights.yml`; download size most important, launch speed
second, build time least):

| Metric | Weight |
|---|---|
| Download size (total AppImage bytes) | 35% |
| App start speed: mount + launch in one run (cold) 20% + startup CPU 8% | 28% |
| zsync update cost (best tested `-b`) | 24% |
| RAM of the FUSE/runtime process | 10% |
| AppImage generation time | 3% |

## Workflows (all in `.github/workflows`)

| Workflow | Purpose |
|---|---|
| `pin.yml` | Run **first**: downloads each URL in `corpus/*.yml`, commits the sha256 values. Unpinned apps are skipped by the planner. |
| `selftest.yml` | Unit tests + end-to-end pipeline on a synthetic app (build, determinism, mount, nginx+zsync, aggregate). Runs on every push. |
| `prepare.yml` ("Benchmark") | Entry point. `plan` -> `fetch` (corpus cache) -> `bench` -> `codec` -> `aggregate`, optional `chain` to the next stage. |
| `bench.yml` | Reusable matrix: one job per (app, arch, small group of variants + canary), `max-parallel: 20`. |
| `aggregate.yml` | Reusable: tables -> job summary, plots, `results` branch, README. |
| `retry-noisy.yml` | Re-runs variants with timing CV > 10% once on fresh runners. |
| `manual.yml` | One app + variant list, to reproduce a data point. |

Stages (`prepare.yml` input `stage`; `chain: true` runs 1 -> 2 -> 3):

1. **screen**: 4 reference apps x all variants, size + build + determinism.
2. **shortlist**: Pareto set from stage 1 x whole corpus, mount/read/launch (warm, cold-disk, cold-tmpfs) and zsync at the 3 block sizes.
3. **options**: squashfs flag axes and DwarFS ordering on the top 3.
4. **ext**: edge extension of levers (see below), repeated until no optimum is at an edge.
`full` runs everything (heavy).

Variants of one app that are compared run in the same job and interleaved;
the canary (`squashfs-gzip9-b128K`, the current default) is in every job and
timing metrics are reported as ratios to it.

## Layout

`corpus/` pinned apps + runtimes, `variants/` variant table, `tools/` the
harness (`plan_matrix`, `fetch_corpus`, `build_variant`, `measure_startup`,
`measure_zsync`, `record_workset`, `codec_reference`, `classify_appdir`,
`aggregate`, `pin`), `tests/` unit + e2e tests. Raw results and generated
reports live on the `results` branch (`raw/<run-id>/`, `latest/`).

## Not implemented yet

EROFS variants, OpenZL / blockstore experiments (`variants/candidates.yml`
`todo:` entries), casync/desync reference, aarch64 corpus entries, a `Makefile`
local-container wrapper. Add apps by adding entries
to `corpus/corpus.yml` (each needs at least one older release for the delta tests).

## Results

<!-- RESULTS:START -->
# AppImage compression benchmark results

459 (app, arch, variant) records from 6 app/arch pairs. Lower is better; baseline = `squashfs-gzip9-b128K` = 1.00. Measured on GitHub-hosted runners (AMD EPYC 7763 64-Core Processor, AMD EPYC 9V45 96-Core Processor, AMD EPYC 9V74 80-Core Processor, INTEL(R) XEON(R) PLATINUM 8573C, Intel(R) Xeon(R) 6973P-C, Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz); real hardware (HDD, slow ARM) shifts startup conclusions toward higher ratio at lower CPU cost.

## Metric weights (variants/weights.yml)

| metric | weight |
|---|---|
| Download size (total AppImage bytes) | 35% |
| App start time: mount + launch in one run (./app.AppImage to first window/exit), cold cache | 20% |
| Startup decompression CPU (mount + working set) | 8% |
| zsync update cost (best of tested -b; .zsync file + downloaded bytes) | 24% |
| RAM of the FUSE/runtime process | 10% |
| AppImage generation (build) time | 3% |

Weighted score = weighted geometric mean of metric/baseline (lower is better); metrics without data (e.g. startup/zsync in stage 1) are left out and the rest renormalised (see *weight coverage*).

## Table 1 - Headline (geometric mean over corpus, relative to baseline)

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd7-b64K | 0.738 | 1.00 | 0.32 | 0.54 | 0.95 | 0.19 | 0.74 | 6 | 100% |
| squashfs-zstd7-b128K | 0.764 | 0.98 | 0.34 | 0.57 | 0.94 | 0.19 | 1.01 | 6 | 100% |
| squashfs-zstd5-b64K | 0.779 | 1.02 | 0.36 | 0.68 | 0.97 | 0.13 | 0.73 | 6 | 100% |
| squashfs-zstd9-b64K | 0.780 | 1.00 | 0.39 | 0.66 | 0.94 | 0.24 | 0.73 | 6 | 100% |
| squashfs-zstd9-b256K | 0.784 | 0.95 | 0.33 | 0.59 | 0.91 | 0.24 | 1.40 | 6 | 100% |
| squashfs-zstd7-b32K | 0.795 | 1.03 | 0.42 | 0.72 | 0.97 | 0.19 | 0.61 | 6 | 100% |
| squashfs-zstd7-b128K+sorttype | 0.796 | 0.98 | 0.37 | 0.67 | 0.92 | 0.21 | 1.03 | 6 | 100% |
| squashfs-zstd7-b128K+nofrag | 0.797 | 1.00 | 0.39 | 0.74 | 0.92 | 0.19 | 0.79 | 6 | 100% |
| squashfs-zstd7-b128K+nodup | 0.797 | 0.99 | 0.39 | 0.65 | 0.94 | 0.20 | 1.01 | 6 | 100% |
| squashfs-zstd9-b128K | 0.798 | 0.98 | 0.36 | 0.67 | 0.93 | 0.24 | 1.02 | 6 | 100% |
| squashfs-zstd3-b32K | 0.803 | 1.08 | 0.39 | 0.78 | 1.03 | 0.08 | 0.59 | 6 | 100% |
| squashfs-zstd12-b128K | 0.804 | 0.97 | 0.37 | 0.63 | 0.91 | 0.61 | 1.02 | 5 | 100% |
| squashfs-zstd7-b32K+nofrag | 0.805 | 1.04 | 0.40 | 0.81 | 0.97 | 0.20 | 0.55 | 6 | 100% |
| squashfs-zstd7-b256K+nodup | 0.806 | 0.96 | 0.34 | 0.68 | 0.92 | 0.19 | 1.38 | 6 | 100% |
| squashfs-zstd5-b128K | 0.807 | 1.00 | 0.42 | 0.68 | 0.96 | 0.13 | 1.02 | 6 | 100% |
| squashfs-zstd5-b256K | 0.808 | 0.98 | 0.38 | 0.63 | 0.95 | 0.14 | 1.38 | 6 | 100% |
| squashfs-zstd7-b32K+sorttype | 0.809 | 1.03 | 0.41 | 0.79 | 0.96 | 0.22 | 0.61 | 6 | 100% |
| squashfs-zstd7-b256K+nofrag | 0.810 | 0.98 | 0.38 | 0.77 | 0.89 | 0.18 | 1.03 | 6 | 100% |
| squashfs-zstd4-b32K | 0.810 | 1.07 | 0.47 | 0.78 | 1.02 | 0.08 | 0.60 | 6 | 100% |
| squashfs-zstd7-b128K+notail | 0.813 | 0.98 | 0.39 | 0.74 | 0.94 | 0.20 | 1.01 | 6 | 100% |
| squashfs-zstd7-b256K | 0.813 | 0.96 | 0.35 | 0.72 | 0.91 | 0.18 | 1.37 | 6 | 100% |
| squashfs-zstd7-b256K+notail | 0.814 | 0.96 | 0.40 | 0.69 | 0.91 | 0.18 | 1.37 | 6 | 100% |
| squashfs-zstd7-b32K+notail | 0.818 | 1.03 | 0.45 | 0.80 | 0.97 | 0.20 | 0.62 | 6 | 100% |
| squashfs-zstd7-b32K+nodup | 0.820 | 1.04 | 0.46 | 0.79 | 0.98 | 0.20 | 0.61 | 6 | 100% |
| squashfs-zstd7-b16K | 0.827 | 1.06 | 0.50 | 0.78 | 1.01 | 0.23 | 0.52 | 6 | 100% |
| squashfs-zstd12-b32K | 0.828 | 1.02 | 0.40 | 0.83 | 0.95 | 0.47 | 0.58 | 5 | 100% |
| squashfs-zstd7-b256K+sorttype | 0.828 | 0.96 | 0.36 | 0.77 | 0.89 | 0.20 | 1.42 | 6 | 100% |
| squashfs-zstd7-b256K-fullrt | 0.837 | 0.96 | 0.38 | 0.79 | 0.92 | 0.18 | 1.37 | 6 | 100% |
| squashfs-zstd12-b256K | 0.838 | 0.94 | 0.38 | 0.73 | 0.86 | 0.74 | 1.37 | 5 | 100% |
| squashfs-zstd17-b32K | 0.843 | 0.97 | 0.49 | 0.77 | 0.92 | 1.73 | 0.60 | 6 | 100% |
| squashfs-lz4hc-b128K | 0.845 | 1.17 | 0.16 | 0.72 | 0.93 | 1.21 | 0.98 | 6 | 100% |
| squashfs-zstd7-b512K | 0.855 | 0.94 | 0.34 | 0.64 | 0.92 | 0.17 | 3.08 | 6 | 100% |
| squashfs-zstd17-b256K | 0.856 | 0.88 | 0.43 | 0.73 | 0.86 | 2.26 | 1.35 | 6 | 100% |
| squashfs-zstd17-b128K | 0.862 | 0.91 | 0.47 | 0.78 | 0.87 | 2.15 | 1.03 | 6 | 100% |
| squashfs-zstd9-b512K | 0.864 | 0.94 | 0.33 | 0.68 | 0.91 | 0.20 | 3.00 | 6 | 100% |
| squashfs-gzip7-b32K | 0.889 | 1.04 | 0.95 | 0.80 | 1.01 | 0.39 | 0.57 | 5 | 100% |
| squashfs-zstd5-b512K | 0.898 | 0.96 | 0.37 | 0.78 | 0.94 | 0.13 | 3.03 | 6 | 100% |
| squashfs-gzip5-b32K | 0.910 | 1.04 | 1.01 | 0.91 | 1.02 | 0.26 | 0.57 | 5 | 100% |
| squashfs-gzip7-b16K | 0.913 | 1.07 | 1.09 | 0.86 | 1.05 | 0.33 | 0.49 | 6 | 100% |
| squashfs-gzip8-b32K | 0.922 | 1.04 | 0.99 | 0.89 | 1.02 | 0.56 | 0.57 | 6 | 100% |
| squashfs-lzo-b128K | 0.923 | 1.10 | 0.53 | 0.86 | 0.84 | 1.39 | 1.00 | 6 | 100% |
| squashfs-gzip3-b32K | 0.934 | 1.09 | 1.04 | 0.95 | 1.08 | 0.18 | 0.57 | 6 | 100% |
| squashfs-gzip3-b128K | 0.951 | 1.06 | 0.99 | 0.84 | 1.06 | 0.20 | 0.97 | 5 | 100% |
| squashfs-gzip5-b128K | 0.971 | 1.01 | 1.00 | 1.01 | 1.01 | 0.28 | 0.99 | 5 | 100% |
| squashfs-gzip7-b128K | 0.978 | 1.00 | 1.00 | 1.00 | 1.00 | 0.48 | 0.99 | 5 | 100% |
| squashfs-gzip5-b256K | 0.989 | 1.01 | 0.97 | 0.96 | 1.04 | 0.28 | 1.32 | 5 | 100% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 6 | 100% |
| squashfs-gzip7-b256K | 1.012 | 1.00 | 0.96 | 1.02 | 1.02 | 0.50 | 1.32 | 5 | 100% |
| squashfs-gzip3-b256K | 1.032 | 1.05 | 1.03 | 1.07 | 1.08 | 0.20 | 1.32 | 5 | 100% |
| dwarfs-l2-S24 | 1.076 | 1.18 | 0.57 | 0.63 | 0.72 | 0.41 | 13.05 | 6 | 100% |
| dwarfs-l3-S20 | 1.139 | 1.18 | 0.45 | 0.64 | 0.96 | 0.46 | 13.13 | 6 | 100% |
| dwarfs-l3-S24 | 1.151 | 1.16 | 0.74 | 0.70 | 0.82 | 0.49 | 12.53 | 6 | 100% |
| squashfs-xz-bcjnone-b32K | 1.153 | 0.92 | 3.20 | 1.88 | 0.94 | 2.13 | 0.59 | 6 | 100% |
| squashfs-xz-bcjauto-b32K | 1.156 | 0.89 | 3.10 | 1.85 | 0.91 | 4.21 | 0.61 | 6 | 100% |
| dwarfs-l3-S26 | 1.166 | 1.16 | 0.74 | 0.73 | 0.82 | 0.63 | 12.22 | 6 | 100% |
| squashfs-xz-bcjnone-b128K | 1.185 | 0.84 | 2.89 | 2.09 | 0.88 | 2.29 | 1.03 | 6 | 100% |
| dwarfs-l1-S24 | 1.191 | 1.43 | 0.56 | 0.69 | 0.96 | 0.08 | 13.13 | 6 | 100% |
| squashfs-xz-bcjauto-b128K | 1.212 | 0.82 | 2.98 | 2.30 | 0.85 | 4.53 | 1.01 | 6 | 100% |
| squashfs-xz-bcjauto-b256K | 1.223 | 0.79 | 2.94 | 2.25 | 0.83 | 4.69 | 1.38 | 6 | 100% |
| squashfs-xz-bcjnone-b256K | 1.240 | 0.82 | 2.89 | 2.44 | 0.86 | 2.39 | 1.39 | 6 | 100% |
| dwarfs-zstd7-S20 | 1.253 | 0.95 | 1.13 | 0.55 | 1.65 | 0.30 | 14.80 | 5 | 100% |
| dwarfs-user-S26-B6-hot | 1.270 | 0.74 | 1.74 | 0.52 | 1.49 | 5.64 | 16.32 | 6 | 100% |
| dwarfs-zstd12-S20 | 1.286 | 0.95 | 0.99 | 0.64 | 1.63 | 0.45 | 14.72 | 5 | 100% |
| dwarfs-zstd17-S20 | 1.305 | 0.90 | 1.17 | 0.60 | 1.68 | 1.48 | 12.94 | 6 | 100% |
| dwarfs-zstd4-S20 | 1.342 | 1.00 | 1.08 | 0.67 | 1.92 | 0.24 | 12.82 | 6 | 100% |
| dwarfs-l5-S20 | 1.346 | 0.87 | 1.31 | 0.67 | 1.65 | 2.61 | 12.90 | 6 | 100% |
| dwarfs-l7-S24 | 1.377 | 0.80 | 1.75 | 0.69 | 1.75 | 4.47 | 12.11 | 6 | 100% |
| dwarfs-user-S26-B6-plain | 1.386 | 0.77 | 2.07 | 0.82 | 1.51 | 6.00 | 11.82 | 6 | 100% |
| dwarfs-l7-S20 | 1.410 | 0.86 | 1.26 | 0.73 | 1.87 | 3.37 | 12.91 | 6 | 100% |
| dwarfs-l5-S16 | 1.423 | 0.98 | 1.20 | 0.59 | 1.86 | 3.43 | 14.33 | 6 | 100% |
| dwarfs-l3-S16 | 1.426 | 1.26 | 0.53 | 0.63 | 2.04 | 0.41 | 15.24 | 6 | 100% |
| dwarfs-brotli6-S20 | 1.468 | 0.92 | 2.53 | 0.78 | 1.83 | 0.55 | 13.83 | 6 | 100% |
| dwarfs-brotli8-S20 | 1.477 | 0.91 | 2.45 | 0.79 | 1.82 | 0.73 | 13.95 | 6 | 100% |
| dwarfs-l5-S24 | 1.483 | 0.81 | 2.78 | 0.89 | 1.56 | 3.59 | 13.98 | 5 | 100% |
| dwarfs-brotli3-S20 | 1.524 | 1.01 | 2.41 | 0.80 | 2.01 | 0.27 | 13.69 | 6 | 100% |
| dwarfs-l7-S16 | 1.535 | 0.98 | 1.18 | 0.69 | 2.19 | 4.24 | 14.24 | 6 | 100% |
| dwarfs-lzma7-S20 | 1.677 | 0.81 | 6.73 | 0.75 | 1.57 | 2.95 | 34.43 | 6 | 100% |
| dwarfs-lzma5-S20 | 1.706 | 0.82 | 7.25 | 0.79 | 1.49 | 2.64 | 39.51 | 5 | 100% |
| dwarfs-lzma3-S20 | 1.754 | 0.86 | 6.72 | 0.85 | 1.69 | 1.04 | 39.28 | 6 | 100% |

## Table 5 - Category: electron

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| dwarfs-l2-S24 | 0.727 | 1.13 |  | 0.61 | 0.13 | 0.45 | 15.23 | 1 | 92% |
| squashfs-zstd7-b128K+sorttype | 0.742 | 0.97 |  | 0.36 | 0.92 | 0.24 | 1.01 | 1 | 92% |
| squashfs-zstd7-b256K+notail | 0.750 | 0.94 |  | 0.40 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd12-b128K | 0.754 | 0.96 |  | 0.35 | 0.88 | 0.78 | 1.01 | 1 | 92% |
| dwarfs-l1-S24 | 0.756 | 1.40 |  | 0.42 | 0.19 | 0.08 | 15.49 | 1 | 92% |
| dwarfs-l3-S24 | 0.787 | 1.11 |  | 0.43 | 0.24 | 0.60 | 14.90 | 1 | 92% |
| squashfs-zstd17-b256K | 0.796 | 0.87 |  | 0.46 | 0.78 | 2.47 | 1.26 | 1 | 92% |
| squashfs-lz4hc-b128K | 0.803 | 1.15 |  | 0.58 | 0.55 | 1.06 | 1.00 | 1 | 92% |
| squashfs-zstd7-b128K+notail | 0.812 | 0.97 |  | 0.55 | 0.92 | 0.23 | 1.01 | 1 | 92% |
| squashfs-zstd7-b128K+nofrag | 0.818 | 0.97 |  | 0.57 | 0.92 | 0.23 | 1.01 | 1 | 92% |
| squashfs-zstd7-b64K | 0.823 | 1.00 |  | 0.58 | 0.94 | 0.27 | 0.83 | 1 | 92% |
| dwarfs-l3-S26 | 0.823 | 1.11 |  | 0.52 | 0.24 | 0.70 | 14.57 | 1 | 92% |
| squashfs-lzo-b128K | 0.825 | 1.08 |  | 0.81 | 0.47 | 1.80 | 1.00 | 1 | 92% |
| squashfs-zstd7-b128K+nodup | 0.828 | 0.97 |  | 0.60 | 0.92 | 0.24 | 1.01 | 1 | 92% |
| squashfs-zstd7-b256K+nofrag | 0.832 | 0.94 |  | 0.66 | 0.83 | 0.22 | 1.26 | 1 | 92% |
| squashfs-zstd7-b256K+sorttype | 0.833 | 0.94 |  | 0.65 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd5-b64K | 0.834 | 1.02 |  | 0.62 | 0.97 | 0.18 | 0.83 | 1 | 92% |
| squashfs-zstd4-b32K | 0.837 | 1.08 |  | 0.66 | 1.03 | 0.09 | 0.66 | 1 | 92% |
| squashfs-zstd7-b256K+nodup | 0.838 | 0.94 |  | 0.66 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-gzip7-b32K | 0.842 | 1.05 |  | 0.55 | 1.04 | 0.49 | 0.66 | 1 | 92% |
| squashfs-zstd7-b256K-fullrt | 0.846 | 0.94 |  | 0.69 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| dwarfs-l3-S20 | 0.847 | 1.12 |  | 0.38 | 0.33 | 0.57 | 17.06 | 1 | 92% |
| squashfs-zstd9-b64K | 0.851 | 1.00 |  | 0.68 | 0.94 | 0.30 | 0.83 | 1 | 92% |
| squashfs-zstd7-b32K | 0.851 | 1.04 |  | 0.69 | 0.99 | 0.24 | 0.66 | 1 | 92% |
| squashfs-zstd7-b256K | 0.854 | 0.94 |  | 0.71 | 0.83 | 0.24 | 1.35 | 1 | 92% |
| squashfs-zstd9-b128K | 0.857 | 0.97 |  | 0.70 | 0.91 | 0.29 | 1.01 | 1 | 92% |
| squashfs-zstd17-b32K | 0.857 | 0.98 |  | 0.59 | 0.97 | 1.86 | 0.66 | 1 | 92% |
| squashfs-zstd9-b256K | 0.857 | 0.94 |  | 0.72 | 0.82 | 0.31 | 1.35 | 1 | 92% |
| squashfs-zstd7-b128K | 0.858 | 0.97 |  | 0.70 | 0.92 | 0.26 | 1.01 | 1 | 92% |
| squashfs-zstd5-b256K | 0.860 | 0.97 |  | 0.71 | 0.85 | 0.18 | 1.35 | 1 | 92% |
| squashfs-zstd3-b32K | 0.860 | 1.10 |  | 0.71 | 1.04 | 0.10 | 0.66 | 1 | 92% |
| squashfs-zstd7-b32K+sorttype | 0.862 | 1.04 |  | 0.72 | 0.98 | 0.27 | 0.66 | 1 | 92% |
| squashfs-zstd7-b16K | 0.868 | 1.08 |  | 0.64 | 1.04 | 0.29 | 0.66 | 1 | 92% |
| squashfs-zstd7-b32K+notail | 0.873 | 1.04 |  | 0.72 | 0.99 | 0.27 | 0.75 | 1 | 92% |
| squashfs-zstd7-b32K+nodup | 0.874 | 1.04 |  | 0.72 | 0.99 | 0.27 | 0.74 | 1 | 92% |
| squashfs-zstd17-b128K | 0.874 | 0.91 |  | 0.65 | 0.87 | 2.89 | 1.01 | 1 | 92% |
| squashfs-gzip7-b16K | 0.876 | 1.09 |  | 0.61 | 1.08 | 0.41 | 0.66 | 1 | 92% |
| squashfs-zstd5-b128K | 0.877 | 1.00 |  | 0.77 | 0.94 | 0.17 | 1.01 | 1 | 92% |
| squashfs-zstd7-b32K+nofrag | 0.880 | 1.04 |  | 0.81 | 0.98 | 0.24 | 0.66 | 1 | 92% |
| squashfs-zstd12-b256K | 0.881 | 0.93 |  | 0.72 | 0.80 | 0.96 | 1.35 | 1 | 92% |
| squashfs-gzip5-b32K | 0.908 | 1.05 |  | 0.81 | 1.05 | 0.33 | 0.65 | 1 | 92% |
| squashfs-gzip8-b32K | 0.914 | 1.04 |  | 0.77 | 1.04 | 0.64 | 0.66 | 1 | 92% |
| squashfs-zstd12-b32K | 0.917 | 1.03 |  | 0.83 | 0.98 | 0.54 | 0.75 | 1 | 92% |
| squashfs-gzip3-b128K | 0.917 | 1.06 |  | 0.67 | 1.09 | 0.25 | 1.00 | 1 | 92% |
| squashfs-zstd7-b512K | 0.932 | 0.93 |  | 0.77 | 0.77 | 0.23 | 3.24 | 1 | 92% |
| squashfs-zstd9-b512K | 0.933 | 0.93 |  | 0.75 | 0.76 | 0.24 | 3.51 | 1 | 92% |
| squashfs-gzip5-b256K | 0.945 | 1.01 |  | 0.78 | 1.01 | 0.37 | 1.26 | 1 | 92% |
| squashfs-zstd5-b512K | 0.954 | 0.95 |  | 0.83 | 0.80 | 0.17 | 3.31 | 1 | 92% |
| squashfs-gzip3-b32K | 0.963 | 1.09 |  | 0.96 | 1.11 | 0.25 | 0.66 | 1 | 92% |
| squashfs-gzip5-b128K | 0.975 | 1.01 |  | 1.01 | 1.01 | 0.37 | 1.00 | 1 | 92% |
| dwarfs-user-S26-B6-plain | 0.981 | 0.76 |  | 0.48 | 0.67 | 6.49 | 14.13 | 1 | 92% |
| squashfs-gzip7-b128K | 0.986 | 1.00 |  | 1.00 | 1.00 | 0.63 | 1.00 | 1 | 92% |
| squashfs-gzip7-b256K | 0.988 | 0.99 |  | 0.92 | 0.99 | 0.65 | 1.26 | 1 | 92% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 |  | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 92% |
| dwarfs-user-S26-B6-hot | 1.012 | 0.76 |  | 0.56 | 0.67 | 5.80 | 14.06 | 1 | 92% |
| squashfs-gzip3-b256K | 1.016 | 1.05 |  | 0.97 | 1.09 | 0.26 | 1.26 | 1 | 92% |
| dwarfs-zstd12-S20 | 1.051 | 0.91 |  | 0.38 | 1.02 | 0.56 | 17.03 | 1 | 92% |
| squashfs-xz-bcjauto-b32K | 1.056 | 0.91 |  | 1.58 | 0.94 | 4.89 | 0.66 | 1 | 92% |
| dwarfs-zstd7-S20 | 1.070 | 0.92 |  | 0.42 | 1.05 | 0.37 | 16.89 | 1 | 92% |
| squashfs-xz-bcjnone-b128K | 1.074 | 0.85 |  | 1.81 | 0.87 | 2.81 | 1.09 | 1 | 92% |
| dwarfs-zstd4-S20 | 1.082 | 0.97 |  | 0.37 | 1.17 | 0.31 | 16.95 | 1 | 92% |
| dwarfs-zstd17-S20 | 1.093 | 0.87 |  | 0.44 | 0.96 | 2.08 | 16.91 | 1 | 92% |
| squashfs-xz-bcjnone-b32K | 1.096 | 0.93 |  | 1.98 | 0.94 | 2.63 | 0.65 | 1 | 92% |
| squashfs-xz-bcjauto-b128K | 1.099 | 0.83 |  | 1.97 | 0.87 | 5.44 | 1.00 | 1 | 92% |
| dwarfs-brotli6-S20 | 1.109 | 0.89 |  | 0.43 | 1.12 | 0.70 | 17.65 | 1 | 92% |
| squashfs-xz-bcjauto-b256K | 1.140 | 0.79 |  | 2.41 | 0.79 | 5.75 | 1.35 | 1 | 92% |
| dwarfs-brotli8-S20 | 1.149 | 0.88 |  | 0.49 | 1.12 | 0.96 | 17.85 | 1 | 92% |
| squashfs-xz-bcjnone-b256K | 1.159 | 0.81 |  | 2.76 | 0.79 | 2.90 | 1.35 | 1 | 92% |
| dwarfs-l5-S20 | 1.163 | 0.84 |  | 0.60 | 0.94 | 3.30 | 17.12 | 1 | 92% |
| dwarfs-brotli3-S20 | 1.185 | 0.99 |  | 0.49 | 1.23 | 0.35 | 17.52 | 1 | 92% |
| dwarfs-l5-S16 | 1.207 | 0.96 |  | 0.37 | 1.28 | 3.64 | 17.85 | 1 | 92% |
| dwarfs-l5-S24 | 1.240 | 0.79 |  | 0.54 | 1.47 | 4.22 | 14.90 | 1 | 92% |
| dwarfs-lzma7-S20 | 1.243 | 0.79 |  | 0.57 | 0.94 | 3.93 | 41.03 | 1 | 92% |
| dwarfs-lzma5-S20 | 1.351 | 0.79 |  | 0.84 | 0.95 | 3.51 | 40.82 | 1 | 92% |
| dwarfs-lzma3-S20 | 1.381 | 0.84 |  | 0.79 | 1.03 | 1.46 | 47.70 | 1 | 92% |
| dwarfs-l7-S20 | 1.390 | 0.84 |  | 0.51 | 2.11 | 3.60 | 17.04 | 1 | 92% |
| dwarfs-l3-S16 | 1.395 | 1.22 |  | 0.60 | 1.31 | 0.50 | 19.02 | 1 | 92% |
| dwarfs-l7-S24 | 1.443 | 0.78 |  | 0.68 | 2.15 | 5.08 | 14.80 | 1 | 92% |
| dwarfs-l7-S16 | 1.579 | 0.96 |  | 0.50 | 2.77 | 4.20 | 17.78 | 1 | 92% |

## Table 5 - Category: huge

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd7-b64K | 0.734 | 1.00 | 0.35 | 0.64 | 0.81 | 0.19 | 0.70 | 1 | 100% |
| squashfs-zstd7-b128K | 0.752 | 0.98 | 0.32 | 0.64 | 0.81 | 0.19 | 1.01 | 1 | 100% |
| squashfs-zstd5-b64K | 0.755 | 1.02 | 0.43 | 0.67 | 0.82 | 0.14 | 0.71 | 1 | 100% |
| squashfs-zstd7-b32K+nofrag | 0.756 | 1.03 | 0.43 | 0.70 | 0.83 | 0.21 | 0.56 | 1 | 100% |
| squashfs-zstd7-b256K | 0.759 | 0.96 | 0.32 | 0.62 | 0.79 | 0.17 | 1.38 | 1 | 100% |
| squashfs-zstd7-b32K+sorttype | 0.760 | 1.02 | 0.46 | 0.71 | 0.82 | 0.21 | 0.56 | 1 | 100% |
| squashfs-zstd3-b32K | 0.760 | 1.06 | 0.43 | 0.70 | 0.90 | 0.08 | 0.56 | 1 | 100% |
| squashfs-zstd9-b64K | 0.761 | 0.99 | 0.45 | 0.67 | 0.81 | 0.25 | 0.71 | 1 | 100% |
| squashfs-zstd7-b128K+nofrag | 0.762 | 1.00 | 0.42 | 0.71 | 0.80 | 0.20 | 0.74 | 1 | 100% |
| squashfs-zstd7-b32K | 0.765 | 1.02 | 0.45 | 0.73 | 0.83 | 0.21 | 0.57 | 1 | 100% |
| squashfs-zstd7-b32K+nodup | 0.769 | 1.03 | 0.48 | 0.72 | 0.83 | 0.21 | 0.57 | 1 | 100% |
| squashfs-zstd4-b32K | 0.770 | 1.05 | 0.51 | 0.73 | 0.88 | 0.08 | 0.57 | 1 | 100% |
| squashfs-zstd7-b32K+notail | 0.770 | 1.02 | 0.49 | 0.73 | 0.83 | 0.21 | 0.57 | 1 | 100% |
| squashfs-zstd12-b32K | 0.772 | 1.01 | 0.45 | 0.70 | 0.81 | 0.46 | 0.57 | 1 | 100% |
| squashfs-zstd5-b128K | 0.775 | 1.00 | 0.42 | 0.67 | 0.82 | 0.14 | 1.00 | 1 | 100% |
| squashfs-zstd7-b256K+nofrag | 0.776 | 0.98 | 0.42 | 0.70 | 0.79 | 0.20 | 1.00 | 1 | 100% |
| squashfs-zstd7-b128K+nodup | 0.782 | 0.99 | 0.42 | 0.70 | 0.80 | 0.21 | 0.98 | 1 | 100% |
| squashfs-zstd9-b256K | 0.784 | 0.96 | 0.33 | 0.70 | 0.78 | 0.25 | 1.37 | 1 | 100% |
| squashfs-zstd7-b128K+notail | 0.784 | 0.98 | 0.43 | 0.70 | 0.81 | 0.21 | 0.99 | 1 | 100% |
| squashfs-zstd7-b16K | 0.784 | 1.04 | 0.51 | 0.76 | 0.86 | 0.24 | 0.49 | 1 | 100% |
| squashfs-zstd7-b128K+sorttype | 0.784 | 0.98 | 0.42 | 0.73 | 0.80 | 0.21 | 0.98 | 1 | 100% |
| squashfs-zstd9-b128K | 0.787 | 0.98 | 0.43 | 0.70 | 0.80 | 0.25 | 0.99 | 1 | 100% |
| squashfs-zstd7-b256K+nodup | 0.795 | 0.97 | 0.36 | 0.73 | 0.79 | 0.20 | 1.36 | 1 | 100% |
| squashfs-zstd7-b256K-fullrt | 0.795 | 0.96 | 0.42 | 0.69 | 0.79 | 0.19 | 1.37 | 1 | 100% |
| squashfs-zstd12-b128K | 0.796 | 0.97 | 0.42 | 0.70 | 0.78 | 0.61 | 0.97 | 1 | 100% |
| squashfs-zstd7-b256K+sorttype | 0.800 | 0.96 | 0.40 | 0.73 | 0.79 | 0.20 | 1.38 | 1 | 100% |
| squashfs-zstd17-b32K | 0.801 | 0.97 | 0.55 | 0.73 | 0.77 | 1.92 | 0.56 | 1 | 100% |
| squashfs-zstd7-b256K+notail | 0.803 | 0.96 | 0.42 | 0.73 | 0.79 | 0.20 | 1.36 | 1 | 100% |
| squashfs-lz4hc-b128K | 0.805 | 1.15 | 0.19 | 0.60 | 0.86 | 1.14 | 1.00 | 1 | 100% |
| squashfs-zstd5-b256K | 0.815 | 0.99 | 0.43 | 0.72 | 0.83 | 0.15 | 1.40 | 1 | 100% |
| squashfs-zstd12-b256K | 0.827 | 0.95 | 0.42 | 0.73 | 0.77 | 0.73 | 1.38 | 1 | 100% |
| squashfs-zstd17-b128K | 0.833 | 0.92 | 0.49 | 0.76 | 0.75 | 2.39 | 1.00 | 1 | 100% |
| squashfs-zstd7-b512K | 0.834 | 0.96 | 0.32 | 0.65 | 0.80 | 0.16 | 3.28 | 1 | 100% |
| squashfs-zstd9-b512K | 0.845 | 0.95 | 0.31 | 0.70 | 0.80 | 0.20 | 3.19 | 1 | 100% |
| squashfs-zstd17-b256K | 0.861 | 0.90 | 0.51 | 0.76 | 0.76 | 2.68 | 1.38 | 1 | 100% |
| squashfs-lzo-b128K | 0.876 | 1.08 | 0.48 | 0.77 | 0.79 | 1.40 | 0.99 | 1 | 100% |
| squashfs-zstd5-b512K | 0.881 | 0.97 | 0.39 | 0.76 | 0.83 | 0.14 | 3.31 | 1 | 100% |
| squashfs-gzip5-b32K | 0.898 | 1.03 | 0.98 | 0.94 | 0.98 | 0.26 | 0.55 | 1 | 100% |
| squashfs-gzip7-b32K | 0.908 | 1.03 | 1.00 | 0.94 | 0.98 | 0.40 | 0.55 | 1 | 100% |
| squashfs-gzip7-b16K | 0.917 | 1.05 | 1.09 | 0.97 | 1.02 | 0.34 | 0.46 | 1 | 100% |
| squashfs-gzip8-b32K | 0.918 | 1.03 | 0.98 | 0.94 | 0.98 | 0.58 | 0.55 | 1 | 100% |
| squashfs-gzip3-b32K | 0.918 | 1.07 | 1.12 | 0.94 | 1.02 | 0.19 | 0.55 | 1 | 100% |
| squashfs-gzip5-b128K | 0.973 | 1.01 | 1.00 | 1.03 | 1.00 | 0.29 | 1.00 | 1 | 100% |
| squashfs-gzip7-b128K | 0.979 | 1.00 | 1.02 | 1.00 | 1.00 | 0.49 | 0.99 | 1 | 100% |
| squashfs-gzip3-b128K | 0.990 | 1.05 | 1.02 | 1.06 | 1.04 | 0.21 | 0.98 | 1 | 100% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 100% |
| squashfs-gzip5-b256K | 1.032 | 1.01 | 1.04 | 1.12 | 1.05 | 0.29 | 1.34 | 1 | 100% |
| squashfs-gzip7-b256K | 1.050 | 1.00 | 1.02 | 1.15 | 1.05 | 0.51 | 1.35 | 1 | 100% |
| squashfs-gzip3-b256K | 1.055 | 1.04 | 1.04 | 1.18 | 1.08 | 0.21 | 1.36 | 1 | 100% |
| dwarfs-l2-S24 | 1.081 | 1.14 | 0.75 | 0.38 | 0.84 | 0.42 | 23.56 | 1 | 100% |
| squashfs-xz-bcjnone-b32K | 1.189 | 0.92 | 3.20 | 1.72 | 1.15 | 2.03 | 0.58 | 1 | 100% |
| dwarfs-l3-S20 | 1.242 | 1.13 | 0.49 | 0.64 | 1.10 | 0.48 | 24.06 | 1 | 100% |
| squashfs-xz-bcjnone-b128K | 1.266 | 0.87 | 2.98 | 2.05 | 1.14 | 2.11 | 1.01 | 1 | 100% |
| dwarfs-l3-S24 | 1.270 | 1.11 | 0.89 | 0.73 | 0.93 | 0.48 | 22.98 | 1 | 100% |
| squashfs-xz-bcjauto-b32K | 1.272 | 0.90 | 3.25 | 2.33 | 1.11 | 4.35 | 0.57 | 1 | 100% |
| dwarfs-l3-S26 | 1.278 | 1.11 | 0.94 | 0.73 | 0.91 | 0.53 | 23.37 | 1 | 100% |
| dwarfs-zstd7-S20 | 1.279 | 0.94 | 1.14 | 0.39 | 1.99 | 0.30 | 24.30 | 1 | 100% |
| squashfs-xz-bcjauto-b128K | 1.289 | 0.84 | 3.14 | 2.14 | 1.10 | 4.69 | 1.02 | 1 | 100% |
| dwarfs-zstd17-S20 | 1.308 | 0.89 | 1.11 | 0.38 | 1.96 | 1.43 | 24.44 | 1 | 100% |
| dwarfs-user-S26-B6-hot | 1.311 | 0.76 | 2.16 | 0.41 | 1.59 | 6.05 | 23.38 | 1 | 100% |
| squashfs-xz-bcjnone-b256K | 1.340 | 0.85 | 3.16 | 2.33 | 1.12 | 2.52 | 1.40 | 1 | 100% |
| squashfs-xz-bcjauto-b256K | 1.355 | 0.82 | 3.00 | 2.56 | 1.08 | 4.68 | 1.37 | 1 | 100% |
| dwarfs-l5-S20 | 1.360 | 0.87 | 1.35 | 0.40 | 1.99 | 2.89 | 24.26 | 1 | 100% |
| dwarfs-l7-S24 | 1.361 | 0.78 | 2.25 | 0.41 | 1.84 | 4.94 | 22.85 | 1 | 100% |
| dwarfs-l1-S24 | 1.395 | 1.33 | 0.85 | 0.76 | 1.28 | 0.08 | 23.41 | 1 | 100% |
| dwarfs-l3-S16 | 1.421 | 1.20 | 0.52 | 0.38 | 2.49 | 0.40 | 30.73 | 1 | 100% |
| dwarfs-zstd12-S20 | 1.431 | 0.93 | 1.06 | 0.68 | 1.97 | 0.46 | 23.53 | 1 | 100% |
| dwarfs-l5-S16 | 1.454 | 0.95 | 1.28 | 0.38 | 2.19 | 3.63 | 28.66 | 1 | 100% |
| dwarfs-zstd4-S20 | 1.478 | 0.98 | 1.11 | 0.71 | 2.18 | 0.24 | 23.21 | 1 | 100% |
| dwarfs-user-S26-B6-plain | 1.517 | 0.76 | 2.68 | 0.80 | 1.59 | 5.70 | 23.18 | 1 | 100% |
| dwarfs-l7-S20 | 1.535 | 0.86 | 1.42 | 0.71 | 2.00 | 4.40 | 22.98 | 1 | 100% |
| dwarfs-l5-S24 | 1.590 | 0.80 | 2.42 | 0.92 | 1.79 | 3.61 | 22.66 | 1 | 100% |
| dwarfs-l7-S16 | 1.624 | 0.95 | 1.25 | 0.65 | 2.18 | 5.44 | 27.92 | 1 | 100% |
| dwarfs-brotli6-S20 | 1.699 | 0.91 | 2.66 | 0.96 | 2.22 | 0.61 | 23.92 | 1 | 100% |
| dwarfs-brotli8-S20 | 1.710 | 0.90 | 2.72 | 0.95 | 2.21 | 0.82 | 24.03 | 1 | 100% |
| dwarfs-lzma5-S20 | 1.725 | 0.82 | 8.06 | 0.42 | 1.96 | 2.73 | 74.90 | 1 | 100% |
| dwarfs-brotli3-S20 | 1.732 | 0.99 | 2.66 | 0.95 | 2.37 | 0.27 | 23.83 | 1 | 100% |
| dwarfs-lzma7-S20 | 1.734 | 0.82 | 8.53 | 0.42 | 1.95 | 2.95 | 75.07 | 1 | 100% |
| dwarfs-lzma3-S20 | 1.750 | 0.86 | 8.69 | 0.40 | 2.06 | 1.09 | 86.41 | 1 | 100% |

## Table 5 - Category: large-qt

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd5-b64K | 0.794 | 1.02 | 0.34 |  | 1.00 | 0.11 | 0.68 | 2 | 80% |
| squashfs-zstd7-b32K+nofrag | 0.796 | 1.04 | 0.40 |  | 1.00 | 0.18 | 0.49 | 2 | 80% |
| squashfs-zstd3-b32K | 0.798 | 1.08 | 0.38 |  | 1.06 | 0.06 | 0.55 | 2 | 80% |
| squashfs-zstd7-b64K | 0.798 | 1.00 | 0.36 |  | 0.98 | 0.17 | 0.68 | 2 | 80% |
| squashfs-zstd7-b128K+nofrag | 0.811 | 1.00 | 0.41 |  | 0.96 | 0.17 | 0.73 | 2 | 80% |
| squashfs-zstd4-b32K | 0.814 | 1.07 | 0.49 |  | 1.04 | 0.07 | 0.55 | 2 | 80% |
| squashfs-zstd7-b32K | 0.814 | 1.03 | 0.43 |  | 1.00 | 0.17 | 0.58 | 2 | 80% |
| squashfs-zstd9-b64K | 0.815 | 1.00 | 0.42 |  | 0.97 | 0.21 | 0.68 | 2 | 80% |
| squashfs-zstd7-b256K+nofrag | 0.818 | 0.98 | 0.40 |  | 0.94 | 0.16 | 0.91 | 2 | 80% |
| squashfs-zstd7-b32K+nodup | 0.819 | 1.04 | 0.47 |  | 1.00 | 0.17 | 0.55 | 2 | 80% |
| squashfs-zstd7-b32K+notail | 0.821 | 1.03 | 0.47 |  | 1.00 | 0.17 | 0.57 | 2 | 80% |
| squashfs-zstd12-b32K | 0.822 | 1.02 | 0.38 |  | 1.00 | 0.42 | 0.55 | 2 | 80% |
| squashfs-zstd7-b128K+notail | 0.832 | 0.98 | 0.40 |  | 0.96 | 0.17 | 0.96 | 2 | 80% |
| squashfs-zstd7-b128K | 0.833 | 0.98 | 0.40 |  | 0.96 | 0.17 | 0.96 | 2 | 80% |
| squashfs-zstd7-b16K | 0.837 | 1.06 | 0.53 |  | 1.03 | 0.20 | 0.48 | 2 | 80% |
| squashfs-zstd7-b128K+nodup | 0.837 | 0.98 | 0.41 |  | 0.97 | 0.17 | 0.96 | 2 | 80% |
| squashfs-zstd7-b256K+nodup | 0.841 | 0.96 | 0.34 |  | 0.95 | 0.16 | 1.35 | 2 | 80% |
| squashfs-zstd9-b128K | 0.843 | 0.97 | 0.42 |  | 0.96 | 0.21 | 1.00 | 2 | 80% |
| squashfs-zstd7-b256K-fullrt | 0.850 | 0.96 | 0.39 |  | 0.95 | 0.15 | 1.35 | 2 | 80% |
| squashfs-zstd17-b32K | 0.852 | 0.97 | 0.49 |  | 0.94 | 1.54 | 0.56 | 2 | 80% |
| squashfs-zstd7-b256K | 0.853 | 0.95 | 0.40 |  | 0.95 | 0.16 | 1.34 | 2 | 80% |
| squashfs-zstd5-b128K | 0.855 | 1.00 | 0.50 |  | 0.99 | 0.11 | 0.99 | 2 | 80% |
| squashfs-zstd7-b256K+notail | 0.859 | 0.95 | 0.42 |  | 0.95 | 0.16 | 1.36 | 2 | 80% |
| squashfs-zstd12-b128K | 0.860 | 0.97 | 0.36 |  | 0.96 | 0.53 | 1.03 | 2 | 80% |
| squashfs-zstd9-b256K | 0.862 | 0.95 | 0.40 |  | 0.94 | 0.22 | 1.39 | 2 | 80% |
| squashfs-zstd5-b256K | 0.862 | 0.98 | 0.40 |  | 0.98 | 0.12 | 1.36 | 2 | 80% |
| squashfs-zstd17-b128K | 0.878 | 0.90 | 0.48 |  | 0.89 | 1.82 | 1.00 | 2 | 80% |
| squashfs-zstd17-b256K | 0.884 | 0.88 | 0.41 |  | 0.88 | 2.01 | 1.33 | 2 | 80% |
| squashfs-zstd12-b256K | 0.885 | 0.94 | 0.36 |  | 0.94 | 0.65 | 1.38 | 2 | 80% |
| squashfs-gzip7-b32K | 0.899 | 1.04 | 0.92 |  | 1.02 | 0.34 | 0.53 | 2 | 80% |
| squashfs-gzip5-b32K | 0.900 | 1.05 | 1.03 |  | 1.03 | 0.22 | 0.53 | 2 | 80% |
| squashfs-gzip3-b32K | 0.908 | 1.09 | 1.00 |  | 1.07 | 0.16 | 0.51 | 2 | 80% |
| squashfs-gzip7-b16K | 0.916 | 1.07 | 1.08 |  | 1.05 | 0.30 | 0.47 | 2 | 80% |
| squashfs-gzip8-b32K | 0.917 | 1.04 | 0.97 |  | 1.02 | 0.52 | 0.52 | 2 | 80% |
| squashfs-lz4hc-b128K | 0.921 | 1.18 | 0.16 |  | 1.08 | 1.27 | 1.00 | 2 | 80% |
| squashfs-zstd7-b512K | 0.937 | 0.94 | 0.37 |  | 0.96 | 0.15 | 3.17 | 2 | 80% |
| squashfs-zstd5-b512K | 0.942 | 0.96 | 0.40 |  | 0.98 | 0.11 | 3.01 | 2 | 80% |
| squashfs-zstd9-b512K | 0.943 | 0.94 | 0.41 |  | 0.95 | 0.18 | 2.98 | 2 | 80% |
| squashfs-gzip5-b128K | 0.952 | 1.01 | 1.00 |  | 1.01 | 0.24 | 0.96 | 2 | 80% |
| squashfs-gzip7-b128K | 0.963 | 1.00 | 0.99 |  | 1.00 | 0.41 | 0.96 | 2 | 80% |
| squashfs-gzip3-b128K | 0.970 | 1.06 | 0.98 |  | 1.06 | 0.18 | 0.96 | 2 | 80% |
| squashfs-xz-bcjnone-b32K | 0.982 | 0.90 | 3.15 |  | 0.87 | 2.01 | 0.55 | 2 | 80% |
| squashfs-lzo-b128K | 0.983 | 1.09 | 0.57 |  | 0.99 | 1.25 | 0.96 | 2 | 80% |
| squashfs-xz-bcjnone-b128K | 0.984 | 0.83 | 2.75 |  | 0.81 | 2.15 | 0.97 | 2 | 80% |
| squashfs-xz-bcjauto-b128K | 0.986 | 0.81 | 2.75 |  | 0.79 | 4.20 | 0.97 | 2 | 80% |
| squashfs-gzip5-b256K | 0.988 | 1.01 | 0.94 |  | 1.02 | 0.24 | 1.34 | 2 | 80% |
| squashfs-xz-bcjauto-b32K | 0.988 | 0.88 | 2.98 |  | 0.85 | 4.03 | 0.58 | 2 | 80% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 |  | 1.00 | 1.00 | 1.00 | 2 | 80% |
| squashfs-gzip7-b256K | 1.002 | 1.00 | 0.94 |  | 1.01 | 0.43 | 1.36 | 2 | 80% |
| squashfs-xz-bcjnone-b256K | 1.008 | 0.81 | 2.70 |  | 0.80 | 2.19 | 1.36 | 2 | 80% |
| squashfs-xz-bcjauto-b256K | 1.014 | 0.79 | 2.78 |  | 0.77 | 4.35 | 1.37 | 2 | 80% |
| squashfs-gzip3-b256K | 1.015 | 1.05 | 1.02 |  | 1.07 | 0.18 | 1.32 | 2 | 80% |
| dwarfs-l2-S24 | 1.502 | 1.15 | 0.65 |  | 1.03 | 0.35 | 27.98 | 2 | 80% |
| dwarfs-l3-S20 | 1.526 | 1.14 | 0.49 |  | 1.23 | 0.41 | 26.13 | 2 | 80% |
| dwarfs-l3-S26 | 1.544 | 1.11 | 1.05 |  | 1.06 | 0.45 | 24.27 | 2 | 80% |
| dwarfs-l3-S24 | 1.550 | 1.11 | 1.00 |  | 1.07 | 0.39 | 25.84 | 2 | 80% |
| dwarfs-l1-S24 | 1.611 | 1.39 | 0.60 |  | 1.28 | 0.06 | 27.98 | 2 | 80% |
| dwarfs-user-S26-B6-hot | 1.630 | 0.71 | 2.44 |  | 1.38 | 4.23 | 24.04 | 2 | 80% |
| dwarfs-zstd12-S20 | 1.631 | 0.91 | 0.95 |  | 1.75 | 0.39 | 25.30 | 2 | 80% |
| dwarfs-l7-S24 | 1.634 | 0.75 | 2.15 |  | 1.37 | 3.82 | 24.30 | 2 | 80% |
| dwarfs-zstd7-S20 | 1.639 | 0.92 | 1.12 |  | 1.77 | 0.24 | 25.22 | 2 | 80% |
| dwarfs-user-S26-B6-plain | 1.652 | 0.71 | 2.78 |  | 1.38 | 4.23 | 24.06 | 2 | 80% |
| dwarfs-l7-S20 | 1.661 | 0.81 | 1.35 |  | 1.51 | 2.98 | 25.02 | 2 | 80% |
| dwarfs-zstd17-S20 | 1.706 | 0.86 | 1.38 |  | 1.67 | 1.33 | 25.02 | 2 | 80% |
| dwarfs-zstd4-S20 | 1.711 | 0.96 | 1.22 |  | 1.89 | 0.21 | 25.09 | 2 | 80% |
| dwarfs-l5-S20 | 1.715 | 0.83 | 1.49 |  | 1.66 | 2.21 | 24.66 | 2 | 80% |
| dwarfs-l5-S24 | 1.732 | 0.77 | 2.98 |  | 1.49 | 2.82 | 24.18 | 2 | 80% |
| dwarfs-brotli8-S20 | 1.826 | 0.86 | 2.74 |  | 1.78 | 0.63 | 26.44 | 2 | 80% |
| dwarfs-brotli6-S20 | 1.826 | 0.87 | 2.92 |  | 1.79 | 0.48 | 26.30 | 2 | 80% |
| dwarfs-l5-S16 | 1.844 | 0.94 | 1.21 |  | 1.64 | 3.32 | 30.27 | 2 | 80% |
| dwarfs-l7-S16 | 1.878 | 0.93 | 1.18 |  | 1.75 | 3.88 | 30.13 | 2 | 80% |
| dwarfs-l3-S16 | 1.884 | 1.23 | 0.57 |  | 1.96 | 0.38 | 32.22 | 2 | 80% |
| dwarfs-brotli3-S20 | 1.897 | 0.97 | 2.66 |  | 1.99 | 0.23 | 25.62 | 2 | 80% |
| dwarfs-lzma5-S20 | 2.178 | 0.77 | 6.88 |  | 1.54 | 2.29 | 74.93 | 2 | 80% |
| dwarfs-lzma7-S20 | 2.203 | 0.77 | 7.31 |  | 1.54 | 2.51 | 78.17 | 2 | 80% |
| dwarfs-lzma3-S20 | 2.233 | 0.81 | 6.95 |  | 1.64 | 0.93 | 83.75 | 2 | 80% |
| squashfs-zstd7-b128K+sorttype |  |  |  |  |  |  |  | 2 | 0% |
| squashfs-zstd7-b256K+sorttype |  |  |  |  |  |  |  | 2 | 0% |
| squashfs-zstd7-b32K+sorttype |  |  |  |  |  |  |  | 2 | 0% |

## Table 5 - Category: small-qt

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd5-b256K | 0.788 | 1.00 | 0.30 | 0.56 | 0.98 | 0.12 | 1.54 | 1 | 100% |
| squashfs-zstd7-b64K | 0.791 | 1.01 | 0.24 | 0.78 | 0.99 | 0.17 | 0.85 | 1 | 100% |
| squashfs-zstd9-b64K | 0.798 | 1.01 | 0.29 | 0.78 | 0.98 | 0.21 | 0.77 | 1 | 100% |
| squashfs-zstd7-b128K+nofrag | 0.802 | 1.00 | 0.33 | 0.76 | 0.97 | 0.17 | 0.85 | 1 | 100% |
| squashfs-zstd7-b128K | 0.803 | 1.00 | 0.24 | 0.78 | 0.97 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd7-b32K+nofrag | 0.805 | 1.04 | 0.37 | 0.76 | 1.03 | 0.17 | 0.61 | 1 | 100% |
| squashfs-zstd9-b128K | 0.806 | 0.99 | 0.24 | 0.78 | 0.96 | 0.21 | 1.08 | 1 | 100% |
| squashfs-zstd5-b128K | 0.807 | 1.02 | 0.30 | 0.71 | 1.00 | 0.11 | 1.08 | 1 | 100% |
| squashfs-zstd7-b32K+sorttype | 0.809 | 1.04 | 0.37 | 0.76 | 1.03 | 0.18 | 0.63 | 1 | 100% |
| squashfs-zstd7-b256K+nofrag | 0.812 | 0.97 | 0.33 | 0.76 | 0.93 | 0.16 | 1.16 | 1 | 100% |
| squashfs-zstd4-b32K | 0.815 | 1.07 | 0.41 | 0.76 | 1.08 | 0.06 | 0.69 | 1 | 100% |
| squashfs-zstd3-b32K | 0.815 | 1.09 | 0.37 | 0.76 | 1.09 | 0.06 | 0.69 | 1 | 100% |
| squashfs-zstd7-b32K | 0.815 | 1.04 | 0.37 | 0.76 | 1.03 | 0.17 | 0.69 | 1 | 100% |
| squashfs-zstd7-b32K+notail | 0.816 | 1.04 | 0.37 | 0.76 | 1.03 | 0.17 | 0.69 | 1 | 100% |
| squashfs-zstd7-b128K+sorttype | 0.822 | 1.00 | 0.33 | 0.76 | 0.97 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd7-b16K | 0.822 | 1.06 | 0.44 | 0.76 | 1.06 | 0.20 | 0.53 | 1 | 100% |
| squashfs-zstd7-b128K+notail | 0.822 | 1.00 | 0.33 | 0.76 | 0.97 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd9-b256K | 0.825 | 0.96 | 0.24 | 0.78 | 0.94 | 0.21 | 1.60 | 1 | 100% |
| squashfs-zstd5-b64K | 0.832 | 1.03 | 0.35 | 0.90 | 1.02 | 0.11 | 0.75 | 1 | 100% |
| squashfs-zstd7-b32K+nodup | 0.832 | 1.07 | 0.41 | 0.76 | 1.04 | 0.18 | 0.69 | 1 | 100% |
| squashfs-zstd7-b128K+nodup | 0.833 | 1.02 | 0.33 | 0.76 | 0.98 | 0.17 | 1.09 | 1 | 100% |
| squashfs-zstd7-b256K+notail | 0.835 | 0.97 | 0.33 | 0.76 | 0.94 | 0.16 | 1.51 | 1 | 100% |
| squashfs-zstd7-b256K+sorttype | 0.842 | 0.97 | 0.33 | 0.76 | 0.95 | 0.16 | 1.60 | 1 | 100% |
| squashfs-zstd7-b256K+nodup | 0.852 | 1.00 | 0.33 | 0.76 | 0.96 | 0.17 | 1.61 | 1 | 100% |
| squashfs-zstd7-b256K-fullrt | 0.853 | 0.97 | 0.33 | 0.83 | 0.94 | 0.18 | 1.52 | 1 | 100% |
| squashfs-zstd7-b512K | 0.857 | 0.95 | 0.30 | 0.60 | 0.94 | 0.16 | 3.65 | 1 | 100% |
| squashfs-zstd17-b128K | 0.861 | 0.91 | 0.44 | 0.76 | 0.90 | 1.87 | 1.09 | 1 | 100% |
| squashfs-zstd17-b32K | 0.872 | 0.97 | 0.44 | 0.83 | 0.99 | 1.62 | 0.69 | 1 | 100% |
| squashfs-lz4hc-b128K | 0.884 | 1.19 | 0.15 | 0.77 | 1.11 | 1.11 | 0.92 | 1 | 100% |
| squashfs-zstd7-b256K | 0.884 | 0.97 | 0.30 | 1.05 | 0.94 | 0.16 | 1.52 | 1 | 100% |
| squashfs-zstd9-b512K | 0.884 | 0.95 | 0.24 | 0.78 | 0.93 | 0.17 | 3.66 | 1 | 100% |
| squashfs-zstd17-b256K | 0.885 | 0.89 | 0.41 | 0.82 | 0.88 | 1.92 | 1.53 | 1 | 100% |
| squashfs-gzip7-b16K | 0.930 | 1.06 | 1.11 | 0.94 | 1.09 | 0.31 | 0.47 | 1 | 100% |
| squashfs-gzip8-b32K | 0.943 | 1.03 | 1.04 | 0.86 | 1.05 | 0.56 | 0.68 | 1 | 100% |
| squashfs-gzip3-b32K | 0.947 | 1.08 | 1.07 | 0.94 | 1.12 | 0.16 | 0.63 | 1 | 100% |
| squashfs-zstd5-b512K | 0.955 | 0.97 | 0.30 | 1.02 | 0.97 | 0.11 | 3.61 | 1 | 100% |
| squashfs-lzo-b128K | 0.980 | 1.10 | 0.52 | 0.89 | 1.05 | 1.35 | 1.00 | 1 | 100% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 100% |
| squashfs-xz-bcjauto-b32K | 1.077 | 0.86 | 3.23 | 1.40 | 0.86 | 3.88 | 0.69 | 1 | 100% |
| squashfs-xz-bcjnone-b32K | 1.080 | 0.89 | 3.29 | 1.34 | 0.92 | 2.02 | 0.69 | 1 | 100% |
| dwarfs-l2-S24 | 1.086 | 1.21 | 0.33 | 0.69 | 1.11 | 0.38 | 6.19 | 1 | 100% |
| squashfs-xz-bcjauto-b128K | 1.108 | 0.79 | 3.33 | 1.63 | 0.77 | 4.25 | 1.09 | 1 | 100% |
| squashfs-xz-bcjnone-b128K | 1.115 | 0.83 | 3.10 | 1.63 | 0.83 | 2.21 | 1.08 | 1 | 100% |
| dwarfs-l3-S24 | 1.119 | 1.18 | 0.33 | 0.76 | 1.18 | 0.47 | 6.08 | 1 | 100% |
| dwarfs-l3-S26 | 1.128 | 1.18 | 0.30 | 0.76 | 1.18 | 0.69 | 6.35 | 1 | 100% |
| squashfs-xz-bcjauto-b256K | 1.144 | 0.77 | 3.22 | 1.80 | 0.75 | 4.34 | 1.53 | 1 | 100% |
| dwarfs-l3-S20 | 1.150 | 1.21 | 0.34 | 0.70 | 1.34 | 0.41 | 6.59 | 1 | 100% |
| squashfs-xz-bcjnone-b256K | 1.155 | 0.80 | 3.03 | 1.80 | 0.81 | 2.20 | 1.54 | 1 | 100% |
| dwarfs-l1-S24 | 1.178 | 1.44 | 0.33 | 0.70 | 1.49 | 0.06 | 6.24 | 1 | 100% |
| dwarfs-l7-S24 | 1.358 | 0.80 | 0.89 | 0.83 | 2.42 | 4.08 | 5.93 | 1 | 100% |
| dwarfs-zstd17-S20 | 1.363 | 0.91 | 0.89 | 0.76 | 2.39 | 1.30 | 6.68 | 1 | 100% |
| dwarfs-zstd4-S20 | 1.363 | 1.01 | 0.81 | 0.76 | 2.73 | 0.19 | 6.61 | 1 | 100% |
| dwarfs-l5-S20 | 1.388 | 0.87 | 1.00 | 0.82 | 2.31 | 2.43 | 6.56 | 1 | 100% |
| dwarfs-l7-S20 | 1.409 | 0.87 | 0.96 | 0.77 | 2.55 | 2.96 | 6.72 | 1 | 100% |
| dwarfs-user-S26-B6-hot | 1.411 | 0.77 | 0.71 | 0.62 | 3.66 | 9.10 | 6.09 | 1 | 100% |
| dwarfs-l3-S16 | 1.466 | 1.28 | 0.45 | 0.70 | 3.04 | 0.35 | 7.18 | 1 | 100% |
| dwarfs-brotli8-S20 | 1.470 | 0.92 | 1.78 | 0.83 | 2.52 | 0.66 | 7.26 | 1 | 100% |
| dwarfs-brotli6-S20 | 1.483 | 0.92 | 1.81 | 0.89 | 2.54 | 0.48 | 7.14 | 1 | 100% |
| dwarfs-user-S26-B6-plain | 1.500 | 0.77 | 0.89 | 0.82 | 3.66 | 6.84 | 6.01 | 1 | 100% |
| dwarfs-l5-S16 | 1.541 | 1.00 | 1.11 | 0.82 | 2.71 | 3.07 | 6.75 | 1 | 100% |
| dwarfs-l7-S16 | 1.542 | 1.00 | 1.11 | 0.71 | 3.00 | 3.77 | 6.76 | 1 | 100% |
| dwarfs-brotli3-S20 | 1.542 | 1.02 | 1.78 | 0.90 | 2.79 | 0.24 | 7.16 | 1 | 100% |
| dwarfs-lzma7-S20 | 1.683 | 0.80 | 4.50 | 0.93 | 2.14 | 3.11 | 15.89 | 1 | 100% |
| dwarfs-lzma3-S20 | 1.798 | 0.85 | 4.86 | 1.12 | 2.31 | 0.90 | 19.39 | 1 | 100% |

## Table 5 - Category: tiny-cli

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd7-b64K | 0.710 | 1.01 |  | 0.30 | 1.00 | 0.21 | 0.72 | 1 | 92% |
| squashfs-zstd7-b128K | 0.737 | 0.98 |  | 0.30 | 1.02 | 0.22 | 1.06 | 1 | 92% |
| squashfs-zstd9-b256K | 0.753 | 0.95 |  | 0.31 | 1.04 | 0.26 | 1.31 | 1 | 92% |
| squashfs-zstd9-b64K | 0.806 | 1.00 |  | 0.54 | 1.00 | 0.25 | 0.72 | 1 | 92% |
| squashfs-zstd5-b64K | 0.819 | 1.03 |  | 0.57 | 1.04 | 0.16 | 0.71 | 1 | 92% |
| squashfs-zstd9-b128K | 0.835 | 0.97 |  | 0.54 | 1.01 | 0.26 | 1.06 | 1 | 92% |
| squashfs-zstd7-b128K+nodup | 0.847 | 0.98 |  | 0.57 | 1.02 | 0.24 | 1.05 | 1 | 92% |
| squashfs-zstd5-b128K | 0.851 | 1.01 |  | 0.57 | 1.05 | 0.16 | 1.06 | 1 | 92% |
| squashfs-zstd7-b256K | 0.860 | 0.96 |  | 0.57 | 1.05 | 0.21 | 1.30 | 1 | 92% |
| squashfs-zstd7-b256K+nodup | 0.862 | 0.96 |  | 0.57 | 1.04 | 0.22 | 1.30 | 1 | 92% |
| squashfs-zstd5-b256K | 0.872 | 0.98 |  | 0.57 | 1.09 | 0.17 | 1.30 | 1 | 92% |
| squashfs-zstd9-b512K | 0.895 | 0.93 |  | 0.54 | 1.11 | 0.22 | 1.99 | 1 | 92% |
| squashfs-zstd7-b32K+nofrag | 0.904 | 1.06 |  | 1.00 | 1.00 | 0.21 | 0.52 | 1 | 92% |
| squashfs-zstd3-b32K | 0.909 | 1.10 |  | 1.00 | 1.07 | 0.10 | 0.52 | 1 | 92% |
| squashfs-zstd7-b32K | 0.911 | 1.03 |  |  |  | 0.21 |  | 1 | 38% |
| squashfs-zstd7-b32K+sorttype | 0.912 | 1.03 |  | 1.00 | 1.00 | 0.23 | 0.59 | 1 | 92% |
| squashfs-zstd4-b32K | 0.914 | 1.08 |  | 1.00 | 1.05 | 0.10 | 0.59 | 1 | 92% |
| squashfs-zstd7-b32K+notail | 0.914 | 1.03 |  | 1.00 | 1.01 | 0.23 | 0.59 | 1 | 92% |
| squashfs-zstd7-b32K+nodup | 0.914 | 1.04 |  | 1.00 | 1.01 | 0.23 | 0.58 | 1 | 92% |
| squashfs-zstd7-b512K | 0.916 | 0.94 |  | 0.57 | 1.11 | 0.20 | 2.20 | 1 | 92% |
| squashfs-gzip7-b16K | 0.917 | 1.08 |  | 1.00 | 1.03 | 0.35 | 0.45 | 1 | 92% |
| squashfs-zstd7-b128K+nofrag | 0.917 | 1.03 |  | 1.00 | 0.96 | 0.21 | 0.72 | 1 | 92% |
| squashfs-zstd7-b16K | 0.920 | 1.07 |  | 1.00 | 1.02 | 0.26 | 0.52 | 1 | 92% |
| squashfs-zstd12-b32K | 0.920 | 1.03 |  | 1.00 | 1.00 | 0.51 | 0.52 | 1 | 92% |
| squashfs-zstd5-b512K | 0.923 | 0.96 |  | 0.57 | 1.14 | 0.16 | 2.16 | 1 | 92% |
| squashfs-gzip5-b32K | 0.924 | 1.05 |  | 1.00 | 1.02 | 0.27 | 0.58 | 1 | 92% |
| squashfs-zstd12-b256K | 0.927 | 0.94 |  |  |  | 0.78 |  | 1 | 38% |
| squashfs-gzip8-b32K | 0.928 | 1.04 |  | 1.00 | 1.01 | 0.55 | 0.51 | 1 | 92% |
| squashfs-gzip7-b32K | 0.931 | 1.04 |  | 1.00 | 1.01 | 0.39 | 0.58 | 1 | 92% |
| squashfs-gzip3-b256K | 0.936 | 1.07 |  |  |  | 0.20 |  | 1 | 38% |
| squashfs-gzip3-b128K | 0.940 | 1.07 |  |  |  | 0.20 |  | 1 | 38% |
| squashfs-zstd7-b256K+nofrag | 0.941 | 1.02 |  | 1.00 | 0.94 | 0.21 | 0.99 | 1 | 92% |
| squashfs-zstd17-b32K | 0.947 | 0.98 |  | 1.00 | 0.95 | 1.98 | 0.59 | 1 | 92% |
| squashfs-zstd7-b128K+notail | 0.957 | 0.98 |  | 1.00 | 1.02 | 0.23 | 1.06 | 1 | 92% |
| squashfs-gzip3-b32K | 0.958 | 1.10 |  |  |  | 0.19 |  | 1 | 38% |
| squashfs-zstd7-b128K+sorttype | 0.958 | 0.98 |  | 1.00 | 1.02 | 0.24 | 1.06 | 1 | 92% |
| squashfs-zstd7-b256K+sorttype | 0.971 | 0.96 |  | 1.00 | 1.01 | 0.22 | 1.36 | 1 | 92% |
| squashfs-zstd7-b256K+notail | 0.972 | 0.96 |  | 1.00 | 1.05 | 0.21 | 1.29 | 1 | 92% |
| squashfs-gzip5-b128K | 0.977 | 1.01 |  | 1.00 | 1.03 | 0.29 | 1.05 | 1 | 92% |
| squashfs-zstd7-b256K-fullrt | 0.979 | 0.97 |  | 1.00 | 1.05 | 0.23 | 1.30 | 1 | 92% |
| squashfs-zstd12-b128K | 0.981 | 0.97 |  | 1.00 | 1.00 | 0.64 | 1.06 | 1 | 92% |
| squashfs-gzip7-b128K | 0.985 | 1.00 |  | 1.00 | 1.01 | 0.48 | 1.05 | 1 | 92% |
| squashfs-zstd17-b128K | 0.990 | 0.91 |  | 1.00 | 0.96 | 2.32 | 1.06 | 1 | 92% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 |  | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 92% |
| squashfs-zstd17-b256K | 1.005 | 0.88 |  | 1.00 | 0.97 | 2.57 | 1.30 | 1 | 92% |
| squashfs-gzip5-b256K | 1.009 | 1.01 |  | 1.00 | 1.08 | 0.28 | 1.29 | 1 | 92% |
| squashfs-gzip7-b256K | 1.018 | 1.00 |  | 1.00 | 1.05 | 0.51 | 1.30 | 1 | 92% |
| squashfs-lzo-b128K | 1.039 | 1.11 |  | 1.00 | 0.93 | 1.35 | 1.06 | 1 | 92% |
| squashfs-lz4hc-b128K | 1.099 | 1.20 |  | 1.00 | 1.05 | 1.48 | 0.99 | 1 | 92% |
| squashfs-xz-bcjauto-b32K | 1.109 | 0.93 |  | 2.30 | 0.87 | 4.13 | 0.59 | 1 | 92% |
| squashfs-xz-bcjnone-b32K | 1.130 | 0.94 |  | 2.73 | 0.89 | 2.18 | 0.53 | 1 | 92% |
| squashfs-xz-bcjauto-b256K | 1.149 | 0.82 |  | 2.30 | 0.84 | 4.80 | 1.30 | 1 | 92% |
| squashfs-xz-bcjnone-b128K | 1.203 | 0.86 |  | 3.16 | 0.85 | 2.36 | 1.06 | 1 | 92% |
| squashfs-xz-bcjnone-b256K | 1.217 | 0.83 |  | 3.07 | 0.87 | 2.44 | 1.31 | 1 | 92% |
| dwarfs-l2-S24 | 1.235 | 1.33 |  | 1.00 | 1.04 | 0.55 | 2.83 | 1 | 92% |
| dwarfs-l3-S24 | 1.248 | 1.33 |  | 1.00 | 1.06 | 0.67 | 2.79 | 1 | 92% |
| dwarfs-l3-S20 | 1.253 | 1.34 |  | 1.00 | 1.10 | 0.52 | 2.78 | 1 | 92% |
| dwarfs-l3-S26 | 1.261 | 1.33 |  | 1.00 | 1.06 | 1.16 | 2.61 | 1 | 92% |
| squashfs-xz-bcjauto-b128K | 1.276 | 0.85 |  | 4.03 | 0.82 | 4.53 | 1.06 | 1 | 92% |
| dwarfs-l7-S24 | 1.298 | 0.94 |  | 1.00 | 1.59 | 5.30 | 2.67 | 1 | 92% |
| dwarfs-zstd7-S20 | 1.313 | 1.09 |  | 1.00 | 1.88 | 0.36 | 2.72 | 1 | 92% |
| dwarfs-zstd12-S20 | 1.315 | 1.08 |  | 1.00 | 1.85 | 0.48 | 2.69 | 1 | 92% |
| dwarfs-zstd17-S20 | 1.329 | 1.03 |  | 1.00 | 1.78 | 1.52 | 2.72 | 1 | 92% |
| dwarfs-l5-S20 | 1.333 | 1.00 |  | 1.00 | 1.72 | 2.77 | 2.78 | 1 | 92% |
| dwarfs-l7-S20 | 1.338 | 0.99 |  | 1.00 | 1.71 | 3.55 | 2.82 | 1 | 92% |
| dwarfs-brotli6-S20 | 1.346 | 1.06 |  | 1.00 | 1.86 | 0.58 | 3.36 | 1 | 92% |
| dwarfs-brotli8-S20 | 1.353 | 1.05 |  | 1.00 | 1.84 | 0.75 | 3.39 | 1 | 92% |
| dwarfs-zstd4-S20 | 1.358 | 1.15 |  | 1.00 | 2.04 | 0.29 | 2.71 | 1 | 92% |
| dwarfs-l1-S24 | 1.366 | 1.63 |  | 1.00 | 1.33 | 0.13 | 2.90 | 1 | 92% |
| dwarfs-brotli3-S20 | 1.402 | 1.16 |  | 1.00 | 2.06 | 0.33 | 3.36 | 1 | 92% |
| dwarfs-l5-S24 | 1.404 | 0.95 |  | 1.44 | 1.59 | 4.89 | 2.71 | 1 | 92% |
| dwarfs-user-S26-B6-plain | 1.410 | 0.94 |  | 1.43 | 1.58 | 10.34 | 2.39 | 1 | 92% |
| dwarfs-l5-S16 | 1.452 | 1.12 |  | 1.00 | 1.98 | 3.64 | 2.73 | 1 | 92% |
| dwarfs-l7-S16 | 1.458 | 1.11 |  | 1.00 | 1.98 | 4.51 | 2.73 | 1 | 92% |
| dwarfs-l3-S16 | 1.479 | 1.43 |  | 1.00 | 1.89 | 0.45 | 2.87 | 1 | 92% |
| dwarfs-lzma7-S20 | 1.509 | 0.96 |  | 1.44 | 1.62 | 2.91 | 5.57 | 1 | 92% |
| dwarfs-lzma5-S20 | 1.510 | 0.97 |  | 1.44 | 1.63 | 2.55 | 5.61 | 1 | 92% |
| dwarfs-lzma3-S20 | 1.549 | 1.02 |  | 1.44 | 1.75 | 1.00 | 6.55 | 1 | 92% |
| dwarfs-user-S26-B6-hot |  |  |  |  |  |  |  | 1 | 0% |

## Table 2 - Total size per app (MB)

| app | uncompressed | squashfs-zstd7-b64K | squashfs-zstd7-b128K | squashfs-zstd5-b64K | squashfs-zstd9-b64K | squashfs-zstd9-b256K | squashfs-zstd7-b32K | squashfs-gzip9-b128K | best |
|---|---|---|---|---|---|---|---|---|---|
| kdenlive/x86_64 | 658.6 | 219.8 | 214.9 | 224.4 | 218.7 | 207.8 | 226.5 | 218.4 | dwarfs-user-S26-B6-plain |
| keepassxc/x86_64 | 110.3 | 42.0 | 41.3 | 42.7 | 41.8 | 39.9 | 42.9 | 41.4 | squashfs-xz-bcjauto-b256K |
| krita/x86_64 | 1003.6 | 366.7 | 358.9 | 373.3 | 365.2 | 348.7 | 376.1 | 368.4 | dwarfs-user-S26-B6-plain |
| libreoffice/x86_64 | 732.8 | 299.2 | 294.4 | 304.8 | 298.2 | 287.3 | 305.3 | 300.0 | dwarfs-user-S26-B6-plain |
| neovim/x86_64 | 38.0 | 13.0 | 12.6 | 13.3 | 12.9 | 12.2 | 13.3 | 12.9 | squashfs-xz-bcjauto-b256K |
| obsidian/x86_64 | 291.3 | 114.2 | 111.0 | 116.2 | 113.5 | 106.6 | 118.5 | 113.9 | dwarfs-user-S26-B6-plain |

## Table 3 - zsync update cost (median over patch pairs)

| variant | zsync -b | .zsync | downloaded | total update | % of image | requests |
|---|---|---|---|---|---|---|
| dwarfs-brotli3-S20 | 1024 | 1.1M | 108.7M | 109.5M | 80.4% | 2 |
| dwarfs-brotli3-S20 | 2048 | 538.8K | 108.8M | 109.2M | 80.2% | 1 |
| dwarfs-brotli3-S20 | 4096 | 269.5K | 108.8M | 109.0M | 80.4% | 1 |
| dwarfs-brotli6-S20 | 1024 | 967.2K | 97.4M | 98.1M | 79.8% | 2 |
| dwarfs-brotli6-S20 | 2048 | 483.7K | 97.4M | 97.8M | 79.5% | 1 |
| dwarfs-brotli6-S20 | 4096 | 242.0K | 97.5M | 97.6M | 79.4% | 1 |
| dwarfs-brotli8-S20 | 1024 | 959.1K | 96.5M | 97.2M | 79.7% | 1 |
| dwarfs-brotli8-S20 | 2048 | 479.7K | 96.5M | 96.9M | 79.4% | 1 |
| dwarfs-brotli8-S20 | 4096 | 240.0K | 96.6M | 96.8M | 79.3% | 1 |
| dwarfs-l1-S24 | 1024 | 1.5M | 62.3M | 64.1M | 30.4% | 39 |
| dwarfs-l1-S24 | 2048 | 786.9K | 63.4M | 64.3M | 30.9% | 21 |
| dwarfs-l1-S24 | 4096 | 393.6K | 64.6M | 65.1M | 31.5% | 13 |
| dwarfs-l2-S24 | 1024 | 1.2M | 50.3M | 51.7M | 26.5% | 31 |
| dwarfs-l2-S24 | 2048 | 638.8K | 51.3M | 52.1M | 26.9% | 18 |
| dwarfs-l2-S24 | 4096 | 319.5K | 52.6M | 53.1M | 27.5% | 12 |
| dwarfs-l3-S16 | 1024 | 1.3M | 124.0M | 125.6M | 56.1% | 96 |
| dwarfs-l3-S16 | 2048 | 684.1K | 129.8M | 130.3M | 60.2% | 48 |
| dwarfs-l3-S16 | 4096 | 342.2K | 132.9M | 133.2M | 65.0% | 23 |
| dwarfs-l3-S20 | 1024 | 1.2M | 65.6M | 67.1M | 33.7% | 51 |
| dwarfs-l3-S20 | 2048 | 629.6K | 68.6M | 69.3M | 34.9% | 33 |
| dwarfs-l3-S20 | 4096 | 314.9K | 72.4M | 72.8M | 36.4% | 22 |
| dwarfs-l3-S24 | 1024 | 1.2M | 51.8M | 53.3M | 29.0% | 37 |
| dwarfs-l3-S24 | 2048 | 617.3K | 53.0M | 53.7M | 29.6% | 22 |
| dwarfs-l3-S24 | 4096 | 308.8K | 54.3M | 54.7M | 30.5% | 15 |
| dwarfs-l3-S26 | 1024 | 1.2M | 50.5M | 52.0M | 28.6% | 38 |
| dwarfs-l3-S26 | 2048 | 614.8K | 51.6M | 52.4M | 29.1% | 22 |
| dwarfs-l3-S26 | 4096 | 307.5K | 52.9M | 53.3M | 29.9% | 15 |
| dwarfs-l5-S16 | 1024 | 1.0M | 104.2M | 105.0M | 68.9% | 13 |
| dwarfs-l5-S16 | 2048 | 523.6K | 104.6M | 105.0M | 69.6% | 6 |
| dwarfs-l5-S16 | 4096 | 261.9K | 105.0M | 105.2M | 71.9% | 3 |
| dwarfs-l5-S20 | 1024 | 907.0K | 90.2M | 90.9M | 77.1% | 8 |
| dwarfs-l5-S20 | 2048 | 453.6K | 90.5M | 90.8M | 77.4% | 3 |
| dwarfs-l5-S20 | 4096 | 226.9K | 90.6M | 90.8M | 77.7% | 2 |
| dwarfs-l5-S24 | 1024 | 1.1M | 149.4M | 150.5M | 74.3% | 19 |
| dwarfs-l5-S24 | 2048 | 540.8K | 150.1M | 150.7M | 75.0% | 5 |
| dwarfs-l5-S24 | 4096 | 270.5K | 150.5M | 150.8M | 76.3% | 2 |
| dwarfs-l7-S16 | 1024 | 1.0M | 105.1M | 106.1M | 73.7% | 18 |
| dwarfs-l7-S16 | 2048 | 520.1K | 105.7M | 106.2M | 74.2% | 8 |
| dwarfs-l7-S16 | 4096 | 260.2K | 106.1M | 106.4M | 74.9% | 5 |
| dwarfs-l7-S20 | 1024 | 894.2K | 89.2M | 89.8M | 71.7% | 12 |
| dwarfs-l7-S20 | 2048 | 447.2K | 89.5M | 89.9M | 72.0% | 5 |
| dwarfs-l7-S20 | 4096 | 223.7K | 89.8M | 89.9M | 72.3% | 3 |
| dwarfs-l7-S24 | 1024 | 819.9K | 81.1M | 81.8M | 70.1% | 12 |
| dwarfs-l7-S24 | 2048 | 410.1K | 81.4M | 81.8M | 70.3% | 5 |
| dwarfs-l7-S24 | 4096 | 205.2K | 81.7M | 81.9M | 70.8% | 3 |
| dwarfs-lzma3-S20 | 1024 | 897.3K | 89.3M | 90.0M | 77.4% | 1 |
| dwarfs-lzma3-S20 | 2048 | 448.8K | 89.3M | 89.7M | 77.3% | 1 |
| dwarfs-lzma3-S20 | 4096 | 224.5K | 89.4M | 89.5M | 77.6% | 1 |
| dwarfs-lzma5-S20 | 1024 | 1.1M | 150.6M | 151.6M | 78.1% | 2 |
| dwarfs-lzma5-S20 | 2048 | 540.3K | 150.6M | 151.1M | 78.2% | 2 |
| dwarfs-lzma5-S20 | 4096 | 270.3K | 150.7M | 151.0M | 79.0% | 2 |
| dwarfs-lzma7-S20 | 1024 | 836.9K | 82.8M | 83.4M | 76.5% | 1 |
| dwarfs-lzma7-S20 | 2048 | 418.6K | 82.8M | 83.1M | 76.4% | 1 |
| dwarfs-lzma7-S20 | 4096 | 209.4K | 82.9M | 83.0M | 76.7% | 1 |
| dwarfs-user-S26-B6-hot | 1024 | 1010.3K | 139.2M | 140.1M | 82.9% | 19 |
| dwarfs-user-S26-B6-hot | 2048 | 505.3K | 139.9M | 140.4M | 87.1% | 6 |
| dwarfs-user-S26-B6-hot | 4096 | 252.8K | 140.3M | 140.5M | 88.7% | 2 |
| dwarfs-user-S26-B6-plain | 1024 | 796.1K | 82.9M | 83.5M | 78.8% | 12 |
| dwarfs-user-S26-B6-plain | 2048 | 398.2K | 83.3M | 83.6M | 80.8% | 4 |
| dwarfs-user-S26-B6-plain | 4096 | 199.2K | 83.6M | 83.7M | 81.6% | 2 |
| dwarfs-zstd12-S20 | 1024 | 1.3M | 179.8M | 181.1M | 75.9% | 18 |
| dwarfs-zstd12-S20 | 2048 | 654.8K | 180.4M | 181.0M | 75.9% | 11 |
| dwarfs-zstd12-S20 | 4096 | 327.5K | 181.0M | 181.3M | 76.0% | 6 |
| dwarfs-zstd17-S20 | 1024 | 947.6K | 93.8M | 94.5M | 74.9% | 16 |
| dwarfs-zstd17-S20 | 2048 | 473.9K | 94.3M | 94.7M | 75.1% | 8 |
| dwarfs-zstd17-S20 | 4096 | 237.1K | 94.8M | 95.0M | 75.6% | 3 |
| dwarfs-zstd4-S20 | 1024 | 1.0M | 106.2M | 107.0M | 76.8% | 22 |
| dwarfs-zstd4-S20 | 2048 | 532.1K | 106.9M | 107.3M | 77.4% | 12 |
| dwarfs-zstd4-S20 | 4096 | 266.1K | 107.9M | 108.1M | 79.0% | 1 |
| dwarfs-zstd7-S20 | 1024 | 1.3M | 181.6M | 182.9M | 76.2% | 18 |
| dwarfs-zstd7-S20 | 2048 | 660.5K | 182.1M | 182.7M | 76.1% | 11 |
| dwarfs-zstd7-S20 | 4096 | 330.4K | 182.8M | 183.1M | 76.3% | 6 |
| squashfs-gzip3-b128K | 1024 | 1.8M | 102.1M | 104.4M | 32.0% | 12 |
| squashfs-gzip3-b128K | 2048 | 912.0K | 102.7M | 103.9M | 31.9% | 11 |
| squashfs-gzip3-b128K | 4096 | 456.1K | 104.0M | 104.6M | 32.1% | 9 |
| squashfs-gzip3-b256K | 1024 | 1.8M | 104.8M | 107.1M | 33.0% | 13 |
| squashfs-gzip3-b256K | 2048 | 908.4K | 105.4M | 106.5M | 32.8% | 11 |
| squashfs-gzip3-b256K | 4096 | 454.3K | 106.5M | 107.0M | 33.0% | 9 |
| squashfs-gzip3-b32K | 1024 | 1.6M | 88.4M | 90.9M | 24.0% | 13 |
| squashfs-gzip3-b32K | 2048 | 797.5K | 88.7M | 90.0M | 23.8% | 12 |
| squashfs-gzip3-b32K | 4096 | 398.9K | 89.3M | 89.9M | 23.8% | 10 |
| squashfs-gzip5-b128K | 1024 | 1.4M | 83.4M | 85.9M | 39.4% | 11 |
| squashfs-gzip5-b128K | 2048 | 740.0K | 83.7M | 84.9M | 39.4% | 10 |
| squashfs-gzip5-b128K | 4096 | 370.1K | 84.1M | 84.7M | 39.9% | 8 |
| squashfs-gzip5-b256K | 1024 | 1.4M | 84.3M | 86.7M | 41.4% | 12 |
| squashfs-gzip5-b256K | 2048 | 735.6K | 84.6M | 85.8M | 41.4% | 11 |
| squashfs-gzip5-b256K | 4096 | 367.9K | 85.1M | 85.7M | 41.8% | 9 |
| squashfs-gzip5-b32K | 1024 | 1.5M | 85.0M | 87.5M | 37.7% | 13 |
| squashfs-gzip5-b32K | 2048 | 765.6K | 85.3M | 86.5M | 38.2% | 12 |
| squashfs-gzip5-b32K | 4096 | 382.9K | 85.8M | 86.5M | 39.5% | 10 |
| squashfs-gzip7-b128K | 1024 | 1.4M | 82.4M | 84.9M | 39.7% | 12 |
| squashfs-gzip7-b128K | 2048 | 731.5K | 82.7M | 83.9M | 39.7% | 10 |
| squashfs-gzip7-b128K | 4096 | 365.9K | 83.2M | 83.8M | 40.3% | 9 |
| squashfs-gzip7-b16K | 1024 | 1.2M | 47.8M | 49.5M | 31.3% | 7 |
| squashfs-gzip7-b16K | 2048 | 599.7K | 48.0M | 48.8M | 31.6% | 6 |
| squashfs-gzip7-b16K | 4096 | 300.0K | 48.3M | 48.8M | 32.8% | 6 |
| squashfs-gzip7-b256K | 1024 | 1.4M | 83.3M | 85.7M | 41.8% | 12 |
| squashfs-gzip7-b256K | 2048 | 726.7K | 83.6M | 84.8M | 41.8% | 11 |
| squashfs-gzip7-b256K | 4096 | 363.5K | 84.1M | 84.7M | 42.2% | 9 |
| squashfs-gzip7-b32K | 1024 | 1.5M | 84.4M | 86.9M | 37.8% | 13 |
| squashfs-gzip7-b32K | 2048 | 759.5K | 84.7M | 85.9M | 38.2% | 12 |
| squashfs-gzip7-b32K | 4096 | 379.9K | 85.2M | 85.8M | 39.6% | 10 |
| squashfs-gzip8-b32K | 1024 | 1.1M | 46.3M | 47.9M | 30.9% | 7 |
| squashfs-gzip8-b32K | 2048 | 577.8K | 46.4M | 47.2M | 31.0% | 7 |
| squashfs-gzip8-b32K | 4096 | 289.0K | 46.8M | 47.2M | 31.6% | 6 |
| squashfs-gzip9-b128K | 1024 | 1.1M | 45.2M | 46.7M | 31.9% | 7 |
| squashfs-gzip9-b128K | 2048 | 554.9K | 45.3M | 46.1M | 31.8% | 6 |
| squashfs-gzip9-b128K | 4096 | 277.6K | 45.6M | 46.0M | 32.0% | 6 |
| squashfs-lz4hc-b128K | 1024 | 1.3M | 50.8M | 52.3M | 26.6% | 34 |
| squashfs-lz4hc-b128K | 2048 | 652.9K | 51.5M | 52.3M | 26.9% | 19 |
| squashfs-lz4hc-b128K | 4096 | 326.6K | 52.4M | 52.8M | 27.6% | 13 |
| squashfs-lzo-b128K | 1024 | 1.2M | 46.4M | 47.8M | 25.9% | 31 |
| squashfs-lzo-b128K | 2048 | 607.3K | 47.1M | 47.8M | 26.3% | 19 |
| squashfs-lzo-b128K | 4096 | 303.8K | 48.0M | 48.4M | 27.1% | 12 |
| squashfs-xz-bcjauto-b128K | 1024 | 888.4K | 35.7M | 37.0M | 32.5% | 7 |
| squashfs-xz-bcjauto-b128K | 2048 | 444.3K | 35.9M | 36.5M | 32.4% | 6 |
| squashfs-xz-bcjauto-b128K | 4096 | 222.3K | 36.2M | 36.5M | 32.6% | 5 |
| squashfs-xz-bcjauto-b256K | 1024 | 857.3K | 34.9M | 36.2M | 34.0% | 6 |
| squashfs-xz-bcjauto-b256K | 2048 | 428.8K | 35.1M | 35.7M | 33.8% | 6 |
| squashfs-xz-bcjauto-b256K | 4096 | 214.5K | 35.3M | 35.6M | 34.0% | 5 |
| squashfs-xz-bcjauto-b32K | 1024 | 976.7K | 38.7M | 40.1M | 31.8% | 8 |
| squashfs-xz-bcjauto-b32K | 2048 | 488.5K | 38.9M | 39.6M | 31.7% | 7 |
| squashfs-xz-bcjauto-b32K | 4096 | 244.3K | 39.2M | 39.6M | 32.1% | 5 |
| squashfs-xz-bcjnone-b128K | 1024 | 916.2K | 36.7M | 38.1M | 32.9% | 7 |
| squashfs-xz-bcjnone-b128K | 2048 | 458.2K | 36.9M | 37.5M | 32.8% | 6 |
| squashfs-xz-bcjnone-b128K | 4096 | 229.2K | 37.2M | 37.5M | 33.1% | 5 |
| squashfs-xz-bcjnone-b256K | 1024 | 885.3K | 36.0M | 37.3M | 34.4% | 6 |
| squashfs-xz-bcjnone-b256K | 2048 | 442.8K | 36.1M | 36.7M | 34.2% | 6 |
| squashfs-xz-bcjnone-b256K | 4096 | 221.5K | 36.3M | 36.6M | 34.4% | 5 |
| squashfs-xz-bcjnone-b32K | 1024 | 1003.3K | 39.6M | 41.1M | 32.1% | 8 |
| squashfs-xz-bcjnone-b32K | 2048 | 501.7K | 39.8M | 40.6M | 32.1% | 7 |
| squashfs-xz-bcjnone-b32K | 4096 | 251.0K | 40.2M | 40.5M | 32.4% | 5 |
| squashfs-zstd12-b128K | 1024 | 1.4M | 77.8M | 80.1M | 31.8% | 13 |
| squashfs-zstd12-b128K | 2048 | 709.7K | 78.1M | 79.2M | 32.8% | 11 |
| squashfs-zstd12-b128K | 4096 | 355.0K | 78.6M | 79.2M | 34.5% | 8 |
| squashfs-zstd12-b256K | 1024 | 1.6M | 80.9M | 82.9M | 27.9% | 17 |
| squashfs-zstd12-b256K | 2048 | 822.3K | 82.9M | 83.9M | 28.2% | 13 |
| squashfs-zstd12-b256K | 4096 | 411.3K | 85.6M | 86.2M | 29.1% | 10 |
| squashfs-zstd12-b32K | 1024 | 1.5M | 81.4M | 83.8M | 31.9% | 17 |
| squashfs-zstd12-b32K | 2048 | 751.4K | 81.8M | 83.1M | 33.4% | 13 |
| squashfs-zstd12-b32K | 4096 | 375.8K | 82.5M | 83.1M | 36.1% | 10 |
| squashfs-zstd17-b128K | 1024 | 998.5K | 39.5M | 41.0M | 27.8% | 8 |
| squashfs-zstd17-b128K | 2048 | 499.4K | 39.8M | 40.5M | 28.2% | 6 |
| squashfs-zstd17-b128K | 4096 | 249.8K | 40.1M | 40.4M | 29.2% | 5 |
| squashfs-zstd17-b256K | 1024 | 967.0K | 38.7M | 40.0M | 28.5% | 8 |
| squashfs-zstd17-b256K | 2048 | 483.6K | 38.9M | 39.5M | 29.1% | 6 |
| squashfs-zstd17-b256K | 4096 | 241.9K | 39.2M | 39.5M | 30.2% | 4 |
| squashfs-zstd17-b32K | 1024 | 1.1M | 42.1M | 43.7M | 27.4% | 8 |
| squashfs-zstd17-b32K | 2048 | 540.7K | 42.3M | 43.1M | 28.0% | 7 |
| squashfs-zstd17-b32K | 4096 | 270.5K | 42.7M | 43.1M | 29.4% | 6 |
| squashfs-zstd3-b32K | 1024 | 1.2M | 47.3M | 49.0M | 28.6% | 9 |
| squashfs-zstd3-b32K | 2048 | 608.3K | 47.6M | 48.4M | 29.4% | 7 |
| squashfs-zstd3-b32K | 4096 | 304.3K | 47.9M | 48.3M | 30.8% | 6 |
| squashfs-zstd4-b32K | 1024 | 1.2M | 46.7M | 48.4M | 28.4% | 9 |
| squashfs-zstd4-b32K | 2048 | 598.1K | 47.0M | 47.8M | 29.2% | 7 |
| squashfs-zstd4-b32K | 4096 | 299.2K | 47.3M | 47.7M | 30.6% | 6 |
| squashfs-zstd5-b128K | 1024 | 1.1M | 44.0M | 45.5M | 28.1% | 7 |
| squashfs-zstd5-b128K | 2048 | 556.7K | 44.1M | 44.9M | 28.5% | 6 |
| squashfs-zstd5-b128K | 4096 | 278.4K | 44.4M | 44.8M | 29.4% | 5 |
| squashfs-zstd5-b256K | 1024 | 1.1M | 43.3M | 44.6M | 28.6% | 7 |
| squashfs-zstd5-b256K | 2048 | 546.0K | 43.4M | 44.1M | 29.0% | 6 |
| squashfs-zstd5-b256K | 4096 | 273.1K | 43.7M | 44.0M | 29.9% | 4 |
| squashfs-zstd5-b512K | 1024 | 1.0M | 43.3M | 44.6M | 29.1% | 7 |
| squashfs-zstd5-b512K | 2048 | 534.6K | 43.5M | 44.1M | 29.6% | 6 |
| squashfs-zstd5-b512K | 4096 | 267.4K | 43.8M | 44.1M | 30.6% | 4 |
| squashfs-zstd5-b64K | 1024 | 1.1M | 44.6M | 46.2M | 27.8% | 8 |
| squashfs-zstd5-b64K | 2048 | 568.6K | 44.8M | 45.5M | 28.2% | 7 |
| squashfs-zstd5-b64K | 4096 | 284.4K | 45.1M | 45.5M | 29.3% | 5 |
| squashfs-zstd7-b128K | 1024 | 1.1M | 42.9M | 44.4M | 28.1% | 7 |
| squashfs-zstd7-b128K | 2048 | 544.1K | 43.1M | 43.8M | 28.6% | 6 |
| squashfs-zstd7-b128K | 4096 | 272.2K | 43.4M | 43.8M | 29.5% | 5 |
| squashfs-zstd7-b128K+nodup | 1024 | 1.1M | 43.0M | 44.5M | 27.8% | 7 |
| squashfs-zstd7-b128K+nodup | 2048 | 544.2K | 43.1M | 43.9M | 28.3% | 6 |
| squashfs-zstd7-b128K+nodup | 4096 | 272.2K | 43.4M | 43.8M | 29.2% | 5 |
| squashfs-zstd7-b128K+nofrag | 1024 | 1.1M | 42.7M | 44.3M | 27.4% | 8 |
| squashfs-zstd7-b128K+nofrag | 2048 | 551.8K | 42.9M | 43.7M | 27.9% | 7 |
| squashfs-zstd7-b128K+nofrag | 4096 | 276.0K | 43.3M | 43.6M | 29.1% | 5 |
| squashfs-zstd7-b128K+notail | 1024 | 1.1M | 42.9M | 44.4M | 28.1% | 7 |
| squashfs-zstd7-b128K+notail | 2048 | 544.2K | 43.1M | 43.8M | 28.6% | 6 |
| squashfs-zstd7-b128K+notail | 4096 | 272.2K | 43.4M | 43.8M | 29.5% | 5 |
| squashfs-zstd7-b128K+sorttype | 1024 | 508.5K | 7.2M | 7.7M | 25.4% | 2 |
| squashfs-zstd7-b128K+sorttype | 2048 | 254.4K | 7.2M | 7.4M | 25.8% | 2 |
| squashfs-zstd7-b128K+sorttype | 4096 | 127.3K | 7.2M | 7.4M | 26.7% | 1 |
| squashfs-zstd7-b16K | 1024 | 1.2M | 46.5M | 48.1M | 28.2% | 11 |
| squashfs-zstd7-b16K | 2048 | 597.1K | 46.7M | 47.6M | 29.3% | 8 |
| squashfs-zstd7-b16K | 4096 | 298.7K | 47.2M | 47.6M | 31.5% | 6 |
| squashfs-zstd7-b256K | 1024 | 1.0M | 42.0M | 43.4M | 28.2% | 7 |
| squashfs-zstd7-b256K | 2048 | 528.6K | 42.2M | 42.9M | 28.6% | 6 |
| squashfs-zstd7-b256K | 4096 | 264.4K | 42.5M | 42.8M | 29.6% | 5 |
| squashfs-zstd7-b256K+nodup | 1024 | 1.0M | 42.1M | 43.5M | 28.0% | 7 |
| squashfs-zstd7-b256K+nodup | 2048 | 528.7K | 42.3M | 42.9M | 28.4% | 6 |
| squashfs-zstd7-b256K+nodup | 4096 | 264.5K | 42.6M | 42.9M | 29.3% | 5 |
| squashfs-zstd7-b256K+nofrag | 1024 | 1.1M | 41.6M | 43.1M | 27.3% | 8 |
| squashfs-zstd7-b256K+nofrag | 2048 | 538.3K | 41.8M | 42.5M | 27.7% | 7 |
| squashfs-zstd7-b256K+nofrag | 4096 | 269.3K | 42.1M | 42.5M | 28.8% | 5 |
| squashfs-zstd7-b256K+notail | 1024 | 1.0M | 42.0M | 43.4M | 28.2% | 7 |
| squashfs-zstd7-b256K+notail | 2048 | 528.6K | 42.2M | 42.9M | 28.6% | 6 |
| squashfs-zstd7-b256K+notail | 4096 | 264.4K | 42.5M | 42.8M | 29.6% | 5 |
| squashfs-zstd7-b256K+sorttype | 1024 | 492.8K | 6.7M | 7.2M | 25.6% | 2 |
| squashfs-zstd7-b256K+sorttype | 2048 | 246.5K | 6.8M | 7.0M | 26.0% | 2 |
| squashfs-zstd7-b256K+sorttype | 4096 | 123.4K | 6.8M | 6.9M | 26.9% | 2 |
| squashfs-zstd7-b256K-fullrt | 1024 | 1.0M | 42.0M | 43.4M | 28.2% | 7 |
| squashfs-zstd7-b256K-fullrt | 2048 | 529.1K | 42.2M | 42.9M | 28.6% | 6 |
| squashfs-zstd7-b256K-fullrt | 4096 | 264.7K | 42.5M | 42.8M | 29.5% | 5 |
| squashfs-zstd7-b32K | 1024 | 1.5M | 81.9M | 84.3M | 23.5% | 16 |
| squashfs-zstd7-b32K | 2048 | 756.1K | 82.3M | 83.5M | 23.3% | 13 |
| squashfs-zstd7-b32K | 4096 | 378.2K | 82.9M | 83.6M | 23.3% | 10 |
| squashfs-zstd7-b32K+nodup | 1024 | 1.1M | 44.9M | 46.5M | 27.7% | 9 |
| squashfs-zstd7-b32K+nodup | 2048 | 576.2K | 45.1M | 45.9M | 28.4% | 7 |
| squashfs-zstd7-b32K+nodup | 4096 | 288.2K | 45.5M | 45.9M | 29.8% | 6 |
| squashfs-zstd7-b32K+nofrag | 1024 | 1.1M | 44.8M | 46.5M | 27.6% | 9 |
| squashfs-zstd7-b32K+nofrag | 2048 | 579.9K | 45.1M | 45.9M | 28.3% | 7 |
| squashfs-zstd7-b32K+nofrag | 4096 | 290.0K | 45.5M | 45.9M | 29.9% | 6 |
| squashfs-zstd7-b32K+notail | 1024 | 1.1M | 44.9M | 46.5M | 28.0% | 9 |
| squashfs-zstd7-b32K+notail | 2048 | 575.9K | 45.1M | 45.9M | 28.7% | 7 |
| squashfs-zstd7-b32K+notail | 4096 | 288.1K | 45.5M | 45.9M | 30.1% | 6 |
| squashfs-zstd7-b32K+sorttype | 1024 | 538.9K | 7.6M | 8.2M | 25.4% | 2 |
| squashfs-zstd7-b32K+sorttype | 2048 | 269.6K | 7.7M | 7.9M | 26.1% | 2 |
| squashfs-zstd7-b32K+sorttype | 4096 | 134.9K | 7.7M | 7.9M | 27.5% | 1 |
| squashfs-zstd7-b512K | 1024 | 1.0M | 42.3M | 43.6M | 28.7% | 7 |
| squashfs-zstd7-b512K | 2048 | 523.6K | 42.5M | 43.1M | 29.2% | 6 |
| squashfs-zstd7-b512K | 4096 | 261.9K | 42.7M | 43.1M | 30.2% | 4 |
| squashfs-zstd7-b64K | 1024 | 1.1M | 43.6M | 45.2M | 27.8% | 8 |
| squashfs-zstd7-b64K | 2048 | 557.7K | 43.8M | 44.6M | 28.4% | 7 |
| squashfs-zstd7-b64K | 4096 | 278.9K | 44.2M | 44.6M | 29.4% | 5 |
| squashfs-zstd9-b128K | 1024 | 1.1M | 42.7M | 44.2M | 28.1% | 7 |
| squashfs-zstd9-b128K | 2048 | 540.7K | 42.8M | 43.6M | 28.7% | 6 |
| squashfs-zstd9-b128K | 4096 | 270.5K | 43.1M | 43.5M | 29.7% | 5 |
| squashfs-zstd9-b256K | 1024 | 1.0M | 41.7M | 43.1M | 28.1% | 8 |
| squashfs-zstd9-b256K | 2048 | 524.9K | 41.9M | 42.5M | 28.6% | 6 |
| squashfs-zstd9-b256K | 4096 | 262.6K | 42.2M | 42.5M | 29.5% | 5 |
| squashfs-zstd9-b512K | 1024 | 1.0M | 42.1M | 43.4M | 28.8% | 7 |
| squashfs-zstd9-b512K | 2048 | 521.8K | 42.3M | 42.9M | 29.3% | 6 |
| squashfs-zstd9-b512K | 4096 | 261.0K | 42.6M | 42.9M | 30.3% | 4 |
| squashfs-zstd9-b64K | 1024 | 1.1M | 43.4M | 45.0M | 27.9% | 8 |
| squashfs-zstd9-b64K | 2048 | 554.8K | 43.6M | 44.4M | 28.4% | 7 |
| squashfs-zstd9-b64K | 4096 | 277.5K | 43.9M | 44.3M | 29.4% | 5 |

No-change rebuild: 447 variants tested; 0 downloaded >1% (non-determinism / unstable layout): none

Ideal references on raw AppDir tars: kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K

## Table 3b - Compression block x zsync block (update cost, % of image; best per row bold)

**dwarfs preset level 3**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **56.1** | 60.2 | 65.0 |
| 1.0M | **33.7** | 34.9 | 36.4 |
| 16.0M | **29.0** | 29.6 | 30.5 |

**dwarfs preset level 5**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **68.9** | 69.6 | 71.9 |
| 1.0M | **77.1** | 77.4 | 77.7 |
| 16.0M | **74.3** | 75.0 | 76.3 |

**dwarfs preset level 7**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **73.7** | 74.2 | 74.9 |
| 1.0M | **71.7** | 72.0 | 72.3 |
| 16.0M | **70.1** | 70.3 | 70.8 |

**squashfs gzip level 3**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | 24.0 | 23.8 | **23.8** |
| 128.0K | 32.0 | **31.9** | 32.1 |
| 256.0K | 33.0 | **32.8** | 33.0 |

**squashfs gzip level 5**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | **37.7** | 38.2 | 39.5 |
| 128.0K | **39.4** | 39.4 | 39.9 |
| 256.0K | 41.4 | **41.4** | 41.8 |

**squashfs gzip level 7**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | **37.8** | 38.2 | 39.6 |
| 128.0K | **39.7** | 39.7 | 40.3 |
| 256.0K | 41.8 | **41.8** | 42.2 |

**squashfs xz level None**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | 31.8 | **31.7** | 32.1 |
| 128.0K | 32.5 | **32.4** | 32.6 |
| 256.0K | 34.0 | **33.8** | 34.0 |

**squashfs xz level None**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | 32.1 | **32.1** | 32.4 |
| 128.0K | 32.9 | **32.8** | 33.1 |
| 256.0K | 34.4 | **34.2** | 34.4 |

**squashfs zstd level 12**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | **31.9** | 33.4 | 36.1 |
| 128.0K | **31.8** | 32.8 | 34.5 |
| 256.0K | **27.9** | 28.2 | 29.1 |

**squashfs zstd level 17**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | **27.4** | 28.0 | 29.4 |
| 128.0K | **27.8** | 28.2 | 29.2 |
| 256.0K | **28.5** | 29.1 | 30.2 |

**squashfs zstd level 5**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **27.8** | 28.2 | 29.3 |
| 128.0K | **28.1** | 28.5 | 29.4 |
| 256.0K | **28.6** | 29.0 | 29.9 |
| 512.0K | **29.1** | 29.6 | 30.6 |

**squashfs zstd level 7**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 32.0K | 23.5 | **23.3** | 23.3 |
| 128.0K | **28.1** | 28.6 | 29.5 |
| 256.0K | **28.2** | 28.6 | 29.6 |

**squashfs zstd level 7**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 16.0K | **28.2** | 29.3 | 31.5 |
| 64.0K | **27.8** | 28.4 | 29.4 |
| 512.0K | **28.7** | 29.2 | 30.2 |

**squashfs zstd level 9**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **27.9** | 28.4 | 29.4 |
| 128.0K | **28.1** | 28.7 | 29.7 |
| 256.0K | **28.1** | 28.6 | 29.5 |
| 512.0K | **28.8** | 29.3 | 30.3 |

## Table 4 - Block-size sweep (mid-level compressor)

dwarfs zstd level 12 (median over apps)

| block | size ratio | mount ms | workset ms | seq MB/s | update % |
|---|---|---|---|---|---|
| 1.0M | 0.356 | 30 | 255 | 323 | 75.9 |

## Table 6 - Compatibility (recorded, not measured) and codec-only reference

| family | min kernel | FUSE | static reader |
|---|---|---|---|
| dwarfs-brotli3 | n/a (FUSE3) | 3.x | True |
| dwarfs-brotli6 | n/a (FUSE3) | 3.x | True |
| dwarfs-brotli8 | n/a (FUSE3) | 3.x | True |
| dwarfs-l1 |  |  |  |
| dwarfs-l2 |  |  |  |
| dwarfs-l3 | n/a (FUSE3) | 3.x | True |
| dwarfs-l5 | n/a (FUSE3) | 3.x | True |
| dwarfs-l7 | n/a (FUSE3) | 3.x | True |
| dwarfs-lzma3 | n/a (FUSE3) | 3.x | True |
| dwarfs-lzma5 | n/a (FUSE3) | 3.x | True |
| dwarfs-lzma7 | n/a (FUSE3) | 3.x | True |
| dwarfs-user | n/a (FUSE3) | 3.x | True |
| dwarfs-zstd12 | n/a (FUSE3) | 3.x | True |
| dwarfs-zstd17 | n/a (FUSE3) | 3.x | True |
| dwarfs-zstd4 |  |  |  |
| dwarfs-zstd7 | n/a (FUSE3) | 3.x | True |
| squashfs-gzip3 | n/a (FUSE; in-kernel mount would need 2.6.29) | 2.9 | True |
| squashfs-gzip5 | n/a (FUSE; in-kernel mount would need 2.6.29) | 2.9 | True |
| squashfs-gzip7 | n/a (FUSE; in-kernel mount would need 2.6.29) | 2.9 | True |
| squashfs-gzip8 |  |  |  |
| squashfs-gzip9 | n/a (FUSE; in-kernel mount would need 2.6.29) | 2.9 | True |
| squashfs-lz4hc | n/a (FUSE; in-kernel mount would need 3.19) | 2.9 | True |
| squashfs-lzo | n/a (FUSE; in-kernel mount would need 2.6.36) | 2.9 | True |
| squashfs-xz | n/a (FUSE; in-kernel mount would need 2.6.38) | 2.9 | True |
| squashfs-zstd12 | n/a (FUSE; in-kernel mount would need 4.14) | 2.9 | True |
| squashfs-zstd17 | n/a (FUSE; in-kernel mount would need 4.14) | 2.9 | True |
| squashfs-zstd3 |  |  |  |
| squashfs-zstd4 |  |  |  |
| squashfs-zstd5 |  |  |  |
| squashfs-zstd7 | n/a (FUSE; in-kernel mount would need 4.14) | 2.9 | True |
| squashfs-zstd9 |  |  |  |

| codec-only (tar stream) | geomean ratio | note |
|---|---|---|
| xz-l7 | 0.258 | no random access, no startup metric |
| xz-l6 | 0.263 | no random access, no startup metric |
| zstd19-long25 | 0.271 | no random access, no startup metric |
| zstd19-long24 | 0.272 | no random access, no startup metric |
| zstd19-rsyncable | 0.276 | no random access, no startup metric |
| zstd19-long22 | 0.280 | no random access, no startup metric |
| zstd-l17 | 0.286 | no random access, no startup metric |
| xz-l3 | 0.287 | no random access, no startup metric |
| bzip3 | 0.292 | no random access, no startup metric |
| brotli-q8 | 0.293 | no random access, no startup metric |
| brotli-q6 | 0.302 | no random access, no startup metric |
| zstd-l12 | 0.307 | no random access, no startup metric |
| zstd-l7 | 0.318 | no random access, no startup metric |
| brotli-q3 | 0.345 | no random access, no startup metric |

## Decision rule

Best by weighted score: `squashfs-zstd7-b64K` (0.738, coverage 100%), `squashfs-zstd7-b128K` (0.764, coverage 100%), `squashfs-zstd5-b64K` (0.779, coverage 100%)

1. among the 10 best weighted scores, keep variants within +5% of the best update cost and +10% of the best warm startup CPU (when those metrics exist);
2. choose the smallest total size (sizes within 1.5% tie; the tie goes to the best weighted score);
3. reject non-deterministic builds, zsync verification failures, reference-only variants.

**Winner: `squashfs-zstd9-b256K`**; candidates: squashfs-zstd7-b64K, squashfs-zstd7-b128K, squashfs-zstd9-b256K

- smallest: `dwarfs-user-S26-B6-hot` (0.74), runner-up `dwarfs-user-S26-B6-plain`
- fastest startup (CPU): `squashfs-lz4hc-b128K` (0.16), runner-up `squashfs-zstd7-b64K`
- cheapest update: `dwarfs-l2-S24` (0.72), runner-up `dwarfs-l3-S24`
- fastest build: `dwarfs-l1-S24` (0.08), runner-up `squashfs-zstd3-b32K`

## Edge extension (levers whose best value is at the edge of the tested range)

- `dwarfs-lzma8-S20`: dw-lzma: best level=7 is the high edge of the tested values -> try level=8
- `squashfs-xz-bcjnone-b16K`: sq-xz: best block=32K is the low edge of the tested values -> try block=16K

Shortlist for the next stage: `squashfs-zstd7-b64K`, `squashfs-zstd7-b128K`, `squashfs-zstd5-b64K`, `squashfs-zstd12-b256K`, `squashfs-lz4hc-b128K`, `squashfs-zstd17-b256K`, `squashfs-zstd9-b512K`, `squashfs-lzo-b128K`, `dwarfs-l2-S24`, `dwarfs-l3-S24`, `dwarfs-l3-S26`, `squashfs-gzip9-b128K`

Noisy (CV>10%) variants queued for retry: 314

<!-- RESULTS:END -->
