# AppImage compression benchmark

Answers AppImageSpec issue #44 with data: which container / compressor / block
size is the best default for AppImages, judged by size, startup time and zsync
delta-update efficiency. Design: [docs/AppImageCompressionTests.md](docs/AppImageCompressionTests.md).
Everything runs on GitHub Actions.

## Recommendation

**Use SquashFS with zstd level 7 and a 128K block size**
(`mksquashfs ... -comp zstd -Xcompression-level 7 -b 128K`) as the default AppImage
container, with the existing type2 runtime.

Compared with today's default (gzip level 9, 128K blocks). Figures are geometric means over
a 6-app corpus; 1.00 is today's default, lower is better:

| Metric | zstd 7 / 128K | What it means |
|---|---|---|
| Download size | 0.98 | 2% smaller |
| App start: mount + launch, cold cache | 0.57 | about 40% faster first start |
| Startup CPU (mount + working set) | 0.34 | one third of the CPU |
| zsync update cost | 0.94 | 6% less to download per update |
| Build time | 0.19 | 5x faster to create |
| FUSE process RAM | 1.01 | unchanged |

Every metric is equal or better than today, none is worse. All measurements go through the
runtime's own FUSE mount (squashfuse in the type2 runtime, `--appimage-mount`), exactly as
users run AppImages, so no kernel squashfs support is involved: the only requirement is FUSE,
as for every AppImage today. zstd needs a runtime built with zstd support (the pinned type2
runtime has it).

### Why we are confident

1. **It ranks near the top under every weighting that cares about both size and speed**
   (sensitivity table below): 2nd of 79 with our weights, 5th with all metrics weighted
   equally, 3rd for speed. Among the eight finalists we compared across the balanced weightings,
   it has the best mean rank.
2. **It improves what people feel** (first-start time, CPU, bytes per update) with no
   penalty elsewhere, while keeping the proven SquashFS format and the current runtime.
3. **Measured on real AppImages**: neovim, KeePassXC, Obsidian, Krita, Kdenlive and LibreOffice,
   with update cost computed from real older releases using real zsync over an HTTP range server.
4. **Controlled measurement.** Variants being compared run interleaved in the same job on the
   same runner, every job also contains the current default as a canary to normalise runner
   differences, and every image was built twice and verified bit-identical.
5. **Level and block size were searched around**: levels 5/7/9 x blocks 64K-512K plus automatic
   edge extension up to the range limits. The optimum is interior; 512K blocks and levels 5 and
   9 are worse overall, and 64K and 128K are within noise of each other (128K keeps FUSE RAM at
   today's level; 64K uses about a quarter less).

### Robust to different priorities

The weights are a judgement call, so we re-ranked all 79 fully measured variants under other
priorities (best variant for each; score vs today's default):

| If you weigh ... | Winner | zstd 7 / 128K |
|---|---|---|
| Our weights (size 35, app start 20, startup CPU 8, updates 24, RAM 10, build 3) | squashfs zstd7 / 64K 0.74 | 0.76 (2nd) |
| All six metrics equally | squashfs zstd3 / 32K 0.50 | 0.57 (5th) |
| Speed (app start incl. mount, CPU) | squashfs lz4hc 0.34 | 0.44 (3rd) |
| Download size and updates only | squashfs xz 0.81 | 0.96 (26th) |
| Download size alone | DwarFS (mkdwarfs zstd22 -S26 -B6, hotness list) 0.74 | 0.98 |
| App start alone | DwarFS (same, hotness list) 0.52 | 0.57 (4th) |
| Update cost alone | DwarFS -l2 -S24 0.72 | 0.94 |
| Memory alone | squashfs gzip7 / 16K 0.49 | 1.01 |
| Build time alone | squashfs zstd3 / 32K 0.08 | 0.19 |

Only weightings that ignore one side entirely point elsewhere: size and updates only picks xz,
speed only picks lz4, and each single-metric winner pays heavily on the other metrics (below).
Every weighting that counts both size and speed lands on SquashFS + zstd.

### "But I prefer ..." answers

**"xz compresses best."** It does: squashfs xz is 8-21% smaller than today's default and its
updates are 6-17% cheaper. But xz decompression is slow: app start is 1.9-2.4x slower than
today's, startup CPU 2.9-3.2x higher, and builds take 2-5x longer. That is an unattractive trade
for a 10-20% size gain over zstd 7, and the stock type2 runtime cannot even mount xz (we built
a full-codec runtime only for this measurement).

**"lz4 / lzo are fastest."** lz4 has the lowest startup CPU of anything we tested (0.16), but
images are 17% larger, app start is 0.72 (zstd 7: 0.57), builds are 6x slower than zstd 7, and
the score is 0.85 vs 0.76. lzo is 10% larger, starts slower (0.86) and uses more CPU (0.53), though its updates are cheaper (0.84). Fine for CPU-starved
machines; not a good default for downloads.

**"DwarFS compresses better and starts as fast."** It can: with the project's mkdwarfs setup
(`zstd:level=22 -S26 -B6 --order=path`) images are 23-26% smaller, and the `--hotness-list`
variant starts as fast as our recommendation (cold app start 0.52, the same setup without the
list 0.82; for LibreOffice 1.8 s drops to 0.55 s). **That is the part users notice**, and it
is a real advantage of DwarFS with a hotness list. Our earlier DwarFS "mount time" and "RAM"
comparisons were unfair and have been withdrawn (issues #1 and #2): the mount number mostly
measured the full uruntime unpacking a bundled `mkdwarfs` helper on every launch (the `-lite`
runtime, which is what DwarFS AppImages should use, avoids that), and the RAM number measured
a block cache that the uruntime deliberately sizes from free host memory (1536M on a 16 GiB
runner). They are being re-measured with the lite runtime, a native `dwarfs` mount as
reference, and explicit cache sizes; see Table 7 of the report. What remains true is that
DwarFS builds take about 6x longer than today's, that zsync updates cost 1.5x of today's
(ours 0.94), and that the format needs a different runtime (uruntime) and FUSE3 instead of
the current type2 runtime.

**"gzip is the safe, compatible choice."** gzip is the most widely supported codec, but the
runtime we ship already reads zstd, and the squashfs reader is the runtime's own FUSE code,
not the user's kernel, so there is no kernel-version compatibility argument for gzip. It
scores 0.92-1.05 against zstd's 0.74-0.85 and is never ahead of zstd on launch, CPU, size or
updates. Level 3 builds faster, but it is 5-9% larger than the default. The remaining risk is
an image using zstd being opened by a runtime without zstd, but the runtime is embedded in
each image, so that cannot happen.

**"Use a higher zstd level; size matters most."** Level 17 is 10-12% smaller, but builds
about 12x slower than level 7 and is slower to start (0.73); it is the best pure-zstd choice
if build time is free and only the download matters (score 0.86). Level 9 builds 25% slower
than level 7 for 1-3% size.

**"Use a lower level or smaller block."** zstd 3-5 builds 1.5-2.7x faster but is 2-8% larger and
slower to start; 64K blocks use less RAM and score about the same as 128K (within noise);
16K blocks are worse in every way (score 0.84) and 512K blocks triple FUSE RAM (score 0.87+).
256K blocks are 2% smaller than 128K but use 1.37x the RAM, and in the latest re-measurement
start slower (0.72 vs 0.57).

**"Fancy squashfs flags (-no-fragments, -sort, ...)."** None beat the same variant without a
flag on the weighted score (0.80-0.83 vs 0.76); `-no-fragments` reduces RAM (0.79) at the cost
of a slower start.

**"Your weights are wrong."** Change them: they are one file, `variants/weights.yml`, and the
report is recomputed from the stored raw data by `tools/aggregate.py`. The sensitivity table
above shows what happens for the extreme choices.

### What would change this recommendation

- A DwarFS configuration with low RAM and build cost, or a runtime that makes the cost vanish.
- Real-disk or slow-ARM measurements that favour a different level or block size (not done yet).
- Repeated runs separating 64K, 128K and 256K by more than the noise (see below).
- Apps with very different content (not tested: games, scientific, interpreted apps).

### Caveats we know about

- **Timing noise is large.** Re-measuring variants flagged as noisy moved some results a lot:
  app start for zstd 7 / 256K went from 0.54 to 0.72, and for DwarFS zstd 7 from 0.29 to 0.55.
  We therefore treat differences below about 0.1 in a timing ratio, or 0.03 in the weighted
  score, as ties. The family (SquashFS + zstd, level 7-9, block 64K-128K) is robust; the exact
  block size inside it is not, which is why the recommendation uses the mean rank over several
  weightings rather than one run. The automated decision rule's single "winner" flips
  between near-equal candidates for the same reason.
- Measured on GitHub-hosted runners with fast virtual disks; real hard disks or slow ARM CPUs
  shift startup results toward higher compression at lower CPU cost. A throttled-disk test and
  aarch64 apps are open items.
- The corpus is 6 apps (neovim, KeePassXC, Obsidian, Krita, Kdenlive, LibreOffice); update cost
  uses 1-2 older releases per app and zsync blocks of 1K-4K.
- SquashFS results are for squashfuse as built into the pinned type2 runtime (its sha256 is in
  `corpus/runtimes.yml`). xz, lz4 and lzo were measured with a runtime we rebuilt with those
  codecs added; the same zstd 7 image measured with it (control `-fullrt`) was within about
  10% on every timing metric (app start 0.79 vs 0.72), the same size as the stock runtime's.
  That is within the noise above, so the xz and lz4 results are comparable, but not exact.
  A different squashfuse version could shift mount time, CPU and RAM. The RAM trend with block
  size (64K: 0.74, 128K: 1.01, 256K: 1.37, 512K: 3.0) fits a per-block cache in the FUSE
  process.
- Cold app start is a real GUI start under Xvfb with the page cache dropped; it measures
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

490 (app, arch, variant) records from 6 app/arch pairs. Lower is better; baseline = `squashfs-gzip9-b128K` = 1.00. Measured on GitHub-hosted runners (AMD EPYC 7763 64-Core Processor, AMD EPYC 9V45 96-Core Processor, AMD EPYC 9V74 80-Core Processor, INTEL(R) XEON(R) PLATINUM 8573C, Intel(R) Xeon(R) 6973P-C, Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz); real hardware (HDD, slow ARM) shifts startup conclusions toward higher ratio at lower CPU cost.

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
| dwarfs-l3-S24 | 0.755 | 1.15 | 0.07 | 0.64 | 0.82 | 0.49 | 1.49 | 6 | 100% |
| squashfs-zstd9-b64K | 0.758 | 1.00 | 0.35 | 0.60 | 0.94 | 0.23 | 0.73 | 6 | 100% |
| squashfs-zstd7-b32K+nofrag | 0.773 | 1.04 | 0.40 | 0.67 | 0.97 | 0.19 | 0.55 | 6 | 100% |
| squashfs-zstd7-b256K+nofrag | 0.776 | 0.98 | 0.34 | 0.66 | 0.89 | 0.17 | 1.03 | 6 | 100% |
| squashfs-zstd7-b128K+nofrag | 0.782 | 1.00 | 0.37 | 0.69 | 0.92 | 0.19 | 0.79 | 6 | 100% |
| squashfs-zstd5-b64K | 0.783 | 1.02 | 0.35 | 0.71 | 0.97 | 0.13 | 0.72 | 6 | 100% |
| squashfs-zstd7-b64K | 0.784 | 1.00 | 0.31 | 0.75 | 0.95 | 0.19 | 0.74 | 6 | 100% |
| squashfs-zstd7-b32K+sorttype | 0.785 | 1.03 | 0.41 | 0.69 | 0.96 | 0.21 | 0.60 | 6 | 100% |
| squashfs-zstd7-b128K+sorttype | 0.785 | 0.98 | 0.34 | 0.65 | 0.92 | 0.20 | 1.03 | 6 | 100% |
| squashfs-zstd7-b32K+nodup | 0.786 | 1.04 | 0.40 | 0.69 | 0.98 | 0.19 | 0.60 | 6 | 100% |
| squashfs-zstd3-b32K | 0.790 | 1.08 | 0.35 | 0.76 | 1.03 | 0.07 | 0.59 | 6 | 100% |
| squashfs-zstd9-b128K | 0.791 | 0.98 | 0.35 | 0.66 | 0.93 | 0.23 | 1.02 | 6 | 100% |
| squashfs-zstd7-b16K | 0.795 | 1.06 | 0.44 | 0.68 | 1.01 | 0.23 | 0.52 | 6 | 100% |
| squashfs-zstd4-b32K | 0.796 | 1.07 | 0.43 | 0.74 | 1.02 | 0.08 | 0.60 | 6 | 100% |
| squashfs-zstd7-b128K+notail | 0.798 | 0.98 | 0.37 | 0.69 | 0.94 | 0.19 | 1.01 | 6 | 100% |
| squashfs-zstd7-b32K+notail | 0.799 | 1.03 | 0.42 | 0.73 | 0.97 | 0.19 | 0.62 | 6 | 100% |
| squashfs-zstd5-b256K | 0.799 | 0.98 | 0.30 | 0.67 | 0.95 | 0.13 | 1.38 | 6 | 100% |
| squashfs-zstd5-b128K | 0.799 | 1.00 | 0.37 | 0.68 | 0.96 | 0.12 | 1.02 | 6 | 100% |
| squashfs-zstd7-b256K-fullrt | 0.803 | 0.96 | 0.33 | 0.68 | 0.92 | 0.18 | 1.38 | 6 | 100% |
| squashfs-zstd7-b128K+nodup | 0.803 | 0.99 | 0.35 | 0.71 | 0.94 | 0.19 | 1.01 | 6 | 100% |
| squashfs-zstd7-b32K | 0.803 | 1.03 | 0.39 | 0.79 | 0.97 | 0.19 | 0.61 | 6 | 100% |
| squashfs-zstd7-b128K | 0.804 | 0.98 | 0.35 | 0.73 | 0.94 | 0.19 | 1.01 | 6 | 100% |
| squashfs-zstd7-b256K | 0.804 | 0.96 | 0.35 | 0.68 | 0.91 | 0.17 | 1.38 | 6 | 100% |
| squashfs-zstd12-b32K | 0.805 | 1.02 | 0.36 | 0.76 | 0.95 | 0.45 | 0.58 | 5 | 100% |
| squashfs-zstd7-b256K+notail | 0.805 | 0.96 | 0.35 | 0.68 | 0.91 | 0.18 | 1.38 | 6 | 100% |
| squashfs-zstd9-b256K | 0.806 | 0.95 | 0.34 | 0.67 | 0.91 | 0.24 | 1.41 | 6 | 100% |
| squashfs-zstd7-b256K+nodup | 0.811 | 0.96 | 0.35 | 0.69 | 0.92 | 0.18 | 1.38 | 6 | 100% |
| squashfs-zstd12-b256K | 0.815 | 0.94 | 0.33 | 0.67 | 0.86 | 0.71 | 1.38 | 5 | 100% |
| squashfs-zstd7-b256K+sorttype | 0.820 | 0.96 | 0.35 | 0.75 | 0.89 | 0.19 | 1.42 | 6 | 100% |
| squashfs-zstd12-b128K | 0.820 | 0.97 | 0.33 | 0.74 | 0.91 | 0.59 | 1.02 | 5 | 100% |
| squashfs-zstd17-b256K | 0.830 | 0.88 | 0.38 | 0.68 | 0.86 | 1.91 | 1.35 | 6 | 100% |
| squashfs-zstd17-b32K | 0.838 | 0.97 | 0.46 | 0.77 | 0.92 | 1.60 | 0.60 | 6 | 100% |
| squashfs-lz4hc-b128K | 0.838 | 1.18 | 0.14 | 0.74 | 0.93 | 1.16 | 0.98 | 6 | 100% |
| squashfs-zstd17-b128K | 0.851 | 0.91 | 0.43 | 0.78 | 0.87 | 1.97 | 1.03 | 6 | 100% |
| squashfs-zstd7-b512K | 0.871 | 0.94 | 0.34 | 0.70 | 0.92 | 0.17 | 3.09 | 6 | 100% |
| squashfs-zstd9-b512K | 0.872 | 0.94 | 0.34 | 0.71 | 0.91 | 0.20 | 2.96 | 6 | 100% |
| squashfs-zstd5-b512K | 0.885 | 0.96 | 0.37 | 0.72 | 0.94 | 0.13 | 3.05 | 6 | 100% |
| dwarfs-l3-S20 | 0.900 | 1.17 | 0.12 | 0.67 | 0.96 | 0.45 | 3.31 | 6 | 100% |
| squashfs-gzip5-b32K | 0.915 | 1.04 | 1.06 | 0.92 | 1.02 | 0.25 | 0.57 | 5 | 100% |
| squashfs-gzip7-b32K | 0.917 | 1.04 | 1.04 | 0.90 | 1.01 | 0.39 | 0.57 | 5 | 100% |
| squashfs-lzo-b128K | 0.924 | 1.10 | 0.57 | 0.85 | 0.84 | 1.38 | 0.99 | 6 | 100% |
| squashfs-gzip8-b32K | 0.925 | 1.04 | 1.02 | 0.88 | 1.02 | 0.56 | 0.58 | 6 | 100% |
| squashfs-gzip7-b16K | 0.934 | 1.07 | 1.08 | 0.97 | 1.05 | 0.33 | 0.49 | 6 | 100% |
| squashfs-gzip3-b32K | 0.941 | 1.09 | 1.09 | 0.96 | 1.08 | 0.19 | 0.57 | 6 | 100% |
| squashfs-gzip5-b128K | 0.967 | 1.01 | 1.04 | 0.98 | 1.01 | 0.28 | 0.99 | 5 | 100% |
| squashfs-gzip7-b128K | 0.989 | 1.00 | 1.03 | 1.04 | 1.00 | 0.48 | 0.99 | 5 | 100% |
| dwarfs-brotli6-S20 | 0.989 | 0.89 | 0.18 | 0.94 | 1.83 | 0.53 | 1.69 | 6 | 100% |
| dwarfs-brotli3-S20 | 0.989 | 1.00 | 0.11 | 0.85 | 2.01 | 0.27 | 2.07 | 6 | 100% |
| squashfs-gzip3-b128K | 0.992 | 1.06 | 1.06 | 1.01 | 1.06 | 0.20 | 0.98 | 5 | 100% |
| dwarfs-brotli8-S20 | 0.995 | 0.89 | 0.21 | 0.89 | 1.82 | 0.73 | 1.67 | 6 | 100% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 6 | 100% |
| squashfs-gzip5-b256K | 1.022 | 1.01 | 1.07 | 1.09 | 1.04 | 0.28 | 1.31 | 5 | 100% |
| squashfs-gzip7-b256K | 1.023 | 1.00 | 1.01 | 1.05 | 1.02 | 0.50 | 1.32 | 5 | 100% |
| squashfs-gzip3-b256K | 1.054 | 1.05 | 1.07 | 1.16 | 1.08 | 0.20 | 1.32 | 5 | 100% |
| dwarfs-l2-S24 | 1.070 | 1.18 | 0.57 | 0.62 | 0.72 | 0.41 | 13.06 | 6 | 100% |
| squashfs-xz-bcjnone-b32K | 1.121 | 0.92 | 3.20 | 1.61 | 0.94 | 2.09 | 0.60 | 6 | 100% |
| squashfs-xz-bcjauto-b32K | 1.129 | 0.90 | 3.20 | 1.64 | 0.91 | 3.87 | 0.61 | 6 | 100% |
| squashfs-xz-bcjnone-b128K | 1.160 | 0.85 | 3.03 | 1.85 | 0.88 | 2.17 | 1.03 | 6 | 100% |
| dwarfs-l3-S16 | 1.161 | 1.25 | 0.37 | 0.60 | 2.04 | 0.41 | 2.93 | 6 | 100% |
| squashfs-xz-bcjauto-b128K | 1.162 | 0.82 | 3.03 | 1.85 | 0.85 | 4.35 | 1.02 | 6 | 100% |
| dwarfs-l3-S26 | 1.189 | 1.16 | 0.76 | 0.80 | 0.82 | 0.62 | 12.31 | 6 | 100% |
| squashfs-xz-bcjnone-b256K | 1.205 | 0.82 | 2.97 | 2.08 | 0.86 | 2.30 | 1.39 | 6 | 100% |
| dwarfs-zstd7-S20 | 1.213 | 0.93 | 0.94 | 0.52 | 1.77 | 0.28 | 12.77 | 6 | 100% |
| squashfs-xz-bcjauto-b256K | 1.223 | 0.80 | 3.05 | 2.22 | 0.83 | 4.46 | 1.38 | 6 | 100% |
| dwarfs-user-S26-B6-hot-fullrt | 1.226 | 0.74 | 2.75 | 0.44 | 1.19 | 4.76 | 20.88 | 5 | 100% |
| dwarfs-user-S26-B6-plain | 1.242 | 0.74 | 2.05 | 0.51 | 1.50 | 6.10 | 11.80 | 6 | 100% |
| dwarfs-l1-S24 | 1.242 | 1.43 | 0.60 | 0.83 | 0.96 | 0.08 | 13.09 | 6 | 100% |
| dwarfs-zstd12-S20 | 1.271 | 0.94 | 1.14 | 0.58 | 1.63 | 0.43 | 14.81 | 5 | 100% |
| dwarfs-zstd17-S20 | 1.278 | 0.88 | 1.07 | 0.59 | 1.68 | 1.54 | 12.81 | 6 | 100% |
| dwarfs-l5-S20 | 1.282 | 0.84 | 1.25 | 0.57 | 1.65 | 2.67 | 12.68 | 6 | 100% |
| dwarfs-l5-S24 | 1.289 | 0.78 | 1.89 | 0.57 | 1.63 | 3.59 | 12.14 | 6 | 100% |
| dwarfs-user-S26-B6-hot | 1.295 | 0.73 | 2.12 | 0.56 | 1.49 | 5.37 | 16.17 | 6 | 100% |
| dwarfs-l7-S20 | 1.309 | 0.84 | 1.22 | 0.53 | 1.87 | 3.29 | 12.78 | 6 | 100% |
| dwarfs-l7-S24 | 1.314 | 0.77 | 1.80 | 0.57 | 1.75 | 4.51 | 11.95 | 6 | 100% |
| dwarfs-l5-S16 | 1.361 | 0.95 | 1.03 | 0.53 | 1.86 | 3.23 | 14.14 | 6 | 100% |
| dwarfs-zstd4-S20 | 1.365 | 1.00 | 1.05 | 0.73 | 1.92 | 0.24 | 12.93 | 6 | 100% |
| dwarfs-l7-S16 | 1.450 | 0.95 | 1.05 | 0.58 | 2.19 | 4.21 | 14.12 | 6 | 100% |
| dwarfs-user-S26-B6-plain-c256M | 1.566 | 0.75 | 5.19 | 1.40 | 1.26 | 5.51 | 11.72 | 5 | 100% |
| dwarfs-lzma7-S20 | 1.620 | 0.79 | 6.94 | 0.65 | 1.57 | 3.14 | 33.98 | 6 | 100% |
| dwarfs-lzma5-S20 | 1.643 | 0.79 | 7.11 | 0.72 | 1.58 | 2.53 | 33.72 | 6 | 100% |
| dwarfs-user-S26-B6-hot-c256M | 1.849 | 0.73 | 3.10 | 3.11 | 1.49 | 5.34 | 13.62 | 6 | 100% |
| dwarfs-lzma3-S20 | 1.894 | 0.86 | 6.98 | 1.23 | 1.69 | 1.06 | 39.66 | 6 | 100% |
| dwarfs-user-S26-B6-plain-c64M | 2.354 | 0.74 | 11.02 | 7.30 | 1.50 | 5.81 | 8.97 | 6 | 100% |
| dwarfs-user-S26-B6-hot-c64M | 2.869 | 0.73 | 12.22 | 17.44 | 1.49 | 5.34 | 11.68 | 6 | 100% |

## Table 5 - Category: electron

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| dwarfs-l2-S24 | 0.684 | 1.13 |  | 0.45 | 0.13 | 0.45 | 15.35 | 1 | 92% |
| squashfs-lz4hc-b128K | 0.777 | 1.15 |  | 0.50 | 0.55 | 1.05 | 1.00 | 1 | 92% |
| dwarfs-l1-S24 | 0.795 | 1.40 |  | 0.54 | 0.19 | 0.08 | 15.41 | 1 | 92% |
| dwarfs-l3-S24 | 0.796 | 1.11 |  | 0.45 | 0.24 | 0.62 | 14.90 | 1 | 92% |
| squashfs-zstd9-b64K | 0.813 | 1.00 |  | 0.55 | 0.94 | 0.29 | 0.84 | 1 | 92% |
| squashfs-zstd7-b256K+nofrag | 0.817 | 0.94 |  | 0.61 | 0.83 | 0.22 | 1.26 | 1 | 92% |
| squashfs-lzo-b128K | 0.818 | 1.09 |  | 0.79 | 0.47 | 1.67 | 1.00 | 1 | 92% |
| squashfs-zstd7-b64K | 0.823 | 1.00 |  | 0.58 | 0.94 | 0.27 | 0.83 | 1 | 92% |
| dwarfs-l3-S26 | 0.835 | 1.11 |  | 0.56 | 0.24 | 0.70 | 14.65 | 1 | 92% |
| squashfs-zstd12-b128K | 0.836 | 0.96 |  | 0.57 | 0.88 | 0.77 | 1.00 | 1 | 92% |
| squashfs-zstd7-b32K | 0.836 | 1.04 |  | 0.63 | 0.99 | 0.24 | 0.66 | 1 | 92% |
| squashfs-zstd7-b32K+nofrag | 0.836 | 1.04 |  | 0.64 | 0.98 | 0.24 | 0.66 | 1 | 92% |
| squashfs-zstd4-b32K | 0.837 | 1.08 |  | 0.66 | 1.03 | 0.09 | 0.66 | 1 | 92% |
| squashfs-zstd7-b128K+nofrag | 0.842 | 0.97 |  | 0.65 | 0.92 | 0.23 | 1.00 | 1 | 92% |
| squashfs-zstd5-b64K | 0.842 | 1.02 |  | 0.65 | 0.97 | 0.16 | 0.83 | 1 | 92% |
| squashfs-zstd7-b128K+sorttype | 0.844 | 0.97 |  | 0.66 | 0.92 | 0.24 | 1.01 | 1 | 92% |
| squashfs-zstd7-b128K | 0.848 | 0.97 |  | 0.67 | 0.92 | 0.23 | 1.00 | 1 | 92% |
| squashfs-zstd7-b128K+notail | 0.849 | 0.97 |  | 0.68 | 0.92 | 0.23 | 1.00 | 1 | 92% |
| squashfs-zstd7-b256K | 0.849 | 0.94 |  | 0.71 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd9-b256K | 0.849 | 0.94 |  | 0.69 | 0.82 | 0.31 | 1.35 | 1 | 92% |
| squashfs-zstd9-b128K | 0.850 | 0.97 |  | 0.67 | 0.91 | 0.29 | 1.01 | 1 | 92% |
| squashfs-zstd7-b256K+nodup | 0.851 | 0.94 |  | 0.71 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd7-b256K+notail | 0.851 | 0.94 |  | 0.72 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd7-b256K-fullrt | 0.852 | 0.94 |  | 0.71 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd5-b128K | 0.854 | 1.00 |  | 0.69 | 0.94 | 0.15 | 1.01 | 1 | 92% |
| squashfs-zstd5-b256K | 0.854 | 0.97 |  | 0.69 | 0.85 | 0.17 | 1.35 | 1 | 92% |
| squashfs-zstd7-b128K+nodup | 0.854 | 0.97 |  | 0.69 | 0.92 | 0.23 | 1.01 | 1 | 92% |
| squashfs-zstd7-b256K+sorttype | 0.855 | 0.94 |  | 0.73 | 0.83 | 0.22 | 1.35 | 1 | 92% |
| squashfs-zstd17-b256K | 0.855 | 0.87 |  | 0.65 | 0.78 | 2.43 | 1.26 | 1 | 92% |
| squashfs-zstd17-b32K | 0.857 | 0.98 |  | 0.59 | 0.97 | 1.86 | 0.66 | 1 | 92% |
| squashfs-zstd3-b32K | 0.860 | 1.10 |  | 0.71 | 1.04 | 0.10 | 0.66 | 1 | 92% |
| squashfs-zstd7-b32K+sorttype | 0.862 | 1.04 |  | 0.72 | 0.98 | 0.27 | 0.66 | 1 | 92% |
| squashfs-zstd12-b32K | 0.866 | 1.03 |  | 0.64 | 0.98 | 0.55 | 0.75 | 1 | 92% |
| squashfs-zstd7-b32K+notail | 0.873 | 1.04 |  | 0.72 | 0.99 | 0.27 | 0.75 | 1 | 92% |
| squashfs-zstd7-b32K+nodup | 0.874 | 1.04 |  | 0.72 | 0.99 | 0.27 | 0.74 | 1 | 92% |
| squashfs-zstd17-b128K | 0.874 | 0.91 |  | 0.65 | 0.87 | 2.89 | 1.01 | 1 | 92% |
| squashfs-zstd12-b256K | 0.875 | 0.93 |  | 0.71 | 0.80 | 0.92 | 1.35 | 1 | 92% |
| squashfs-zstd7-b16K | 0.877 | 1.08 |  | 0.67 | 1.04 | 0.29 | 0.66 | 1 | 92% |
| dwarfs-l3-S20 | 0.878 | 1.12 |  | 0.46 | 0.33 | 0.57 | 17.00 | 1 | 92% |
| squashfs-gzip8-b32K | 0.911 | 1.04 |  | 0.76 | 1.04 | 0.65 | 0.66 | 1 | 92% |
| squashfs-gzip5-b32K | 0.916 | 1.05 |  | 0.84 | 1.05 | 0.33 | 0.66 | 1 | 92% |
| squashfs-gzip7-b32K | 0.925 | 1.05 |  | 0.85 | 1.04 | 0.49 | 0.66 | 1 | 92% |
| squashfs-zstd9-b512K | 0.925 | 0.93 |  | 0.75 | 0.76 | 0.24 | 3.26 | 1 | 92% |
| dwarfs-brotli8-S20 | 0.926 | 0.87 |  | 0.66 | 1.12 | 0.90 | 1.44 | 1 | 92% |
| squashfs-zstd7-b512K | 0.928 | 0.93 |  | 0.77 | 0.77 | 0.20 | 3.25 | 1 | 92% |
| dwarfs-brotli6-S20 | 0.929 | 0.88 |  | 0.69 | 1.12 | 0.71 | 1.43 | 1 | 92% |
| squashfs-gzip7-b16K | 0.943 | 1.09 |  | 0.85 | 1.08 | 0.41 | 0.66 | 1 | 92% |
| squashfs-zstd5-b512K | 0.954 | 0.95 |  | 0.83 | 0.80 | 0.17 | 3.31 | 1 | 92% |
| squashfs-gzip3-b32K | 0.963 | 1.09 |  | 0.96 | 1.11 | 0.25 | 0.66 | 1 | 92% |
| dwarfs-user-S26-B6-plain | 0.969 | 0.75 |  | 0.45 | 0.67 | 7.87 | 13.98 | 1 | 92% |
| squashfs-gzip5-b128K | 0.975 | 1.01 |  | 1.01 | 1.01 | 0.37 | 1.00 | 1 | 92% |
| squashfs-gzip7-b128K | 0.986 | 1.00 |  | 1.00 | 1.00 | 0.63 | 1.00 | 1 | 92% |
| dwarfs-user-S26-B6-hot-fullrt | 0.991 | 0.76 |  | 0.51 | 0.67 | 6.02 | 14.04 | 1 | 92% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 |  | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 92% |
| squashfs-gzip3-b128K | 1.008 | 1.06 |  | 1.04 | 1.09 | 0.25 | 1.00 | 1 | 92% |
| squashfs-gzip5-b256K | 1.028 | 1.01 |  | 1.15 | 1.01 | 0.37 | 1.26 | 1 | 92% |
| squashfs-xz-bcjnone-b32K | 1.034 | 0.93 |  | 1.51 | 0.94 | 2.60 | 0.65 | 1 | 92% |
| squashfs-gzip7-b256K | 1.035 | 0.99 |  | 1.14 | 0.99 | 0.65 | 1.26 | 1 | 92% |
| dwarfs-user-S26-B6-hot | 1.041 | 0.75 |  | 0.66 | 0.67 | 6.32 | 13.93 | 1 | 92% |
| squashfs-xz-bcjauto-b32K | 1.056 | 0.91 |  | 1.57 | 0.94 | 4.88 | 0.66 | 1 | 92% |
| squashfs-gzip3-b256K | 1.064 | 1.05 |  | 1.20 | 1.09 | 0.26 | 1.26 | 1 | 92% |
| squashfs-xz-bcjnone-b128K | 1.095 | 0.85 |  | 1.96 | 0.87 | 2.79 | 1.09 | 1 | 92% |
| dwarfs-l3-S16 | 1.095 | 1.20 |  | 0.53 | 1.31 | 0.50 | 2.82 | 1 | 92% |
| dwarfs-zstd7-S20 | 1.101 | 0.92 |  | 0.48 | 1.05 | 0.37 | 16.94 | 1 | 92% |
| squashfs-xz-bcjauto-b128K | 1.108 | 0.83 |  | 2.05 | 0.87 | 5.41 | 1.00 | 1 | 92% |
| dwarfs-zstd12-S20 | 1.110 | 0.91 |  | 0.49 | 1.02 | 0.55 | 16.99 | 1 | 92% |
| squashfs-xz-bcjnone-b256K | 1.132 | 0.82 |  | 2.47 | 0.79 | 2.89 | 1.35 | 1 | 92% |
| dwarfs-user-S26-B6-plain-c256M | 1.138 | 0.75 |  | 0.99 | 0.67 | 6.00 | 14.08 | 1 | 92% |
| dwarfs-user-S26-B6-hot-c256M | 1.153 | 0.75 |  | 1.02 | 0.67 | 7.37 | 13.91 | 1 | 92% |
| squashfs-xz-bcjauto-b256K | 1.154 | 0.79 |  | 2.55 | 0.79 | 5.65 | 1.35 | 1 | 92% |
| dwarfs-zstd4-S20 | 1.156 | 0.97 |  | 0.49 | 1.17 | 0.31 | 17.03 | 1 | 92% |
| dwarfs-l5-S20 | 1.169 | 0.82 |  | 0.64 | 0.94 | 3.34 | 16.67 | 1 | 92% |
| dwarfs-zstd17-S20 | 1.186 | 0.85 |  | 0.67 | 0.96 | 2.04 | 16.89 | 1 | 92% |
| dwarfs-brotli3-S20 | 1.239 | 0.99 |  | 0.62 | 1.23 | 0.35 | 16.76 | 1 | 92% |
| dwarfs-l5-S24 | 1.246 | 0.77 |  | 0.55 | 1.47 | 5.47 | 14.63 | 1 | 92% |
| dwarfs-l5-S16 | 1.298 | 0.96 |  | 0.52 | 1.28 | 3.64 | 17.74 | 1 | 92% |
| dwarfs-l7-S20 | 1.380 | 0.84 |  | 0.49 | 2.11 | 3.45 | 17.15 | 1 | 92% |
| dwarfs-lzma7-S20 | 1.385 | 0.79 |  | 0.94 | 0.94 | 3.79 | 40.51 | 1 | 92% |
| dwarfs-lzma5-S20 | 1.397 | 0.79 |  | 0.99 | 0.95 | 3.37 | 40.33 | 1 | 92% |
| dwarfs-l7-S24 | 1.441 | 0.77 |  | 0.68 | 2.15 | 5.97 | 14.57 | 1 | 92% |
| dwarfs-lzma3-S20 | 1.489 | 0.82 |  | 1.14 | 1.03 | 1.77 | 46.23 | 1 | 92% |
| dwarfs-l7-S16 | 1.663 | 0.96 |  | 0.64 | 2.77 | 4.19 | 17.54 | 1 | 92% |
| dwarfs-user-S26-B6-plain-c64M | 2.244 | 0.75 |  | 23.15 | 0.67 | 6.29 | 13.07 | 1 | 92% |
| dwarfs-user-S26-B6-hot-c64M | 2.248 | 0.75 |  | 23.41 | 0.67 | 6.14 | 13.06 | 1 | 92% |

## Table 5 - Category: huge

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| dwarfs-l3-S24 | 0.722 | 1.11 | 0.06 | 0.73 | 0.93 | 0.45 | 0.68 | 1 | 100% |
| squashfs-zstd9-b64K | 0.727 | 0.99 | 0.41 | 0.57 | 0.81 | 0.23 | 0.69 | 1 | 100% |
| squashfs-zstd4-b32K | 0.733 | 1.05 | 0.45 | 0.60 | 0.88 | 0.08 | 0.57 | 1 | 100% |
| squashfs-zstd3-b32K | 0.735 | 1.06 | 0.41 | 0.61 | 0.90 | 0.08 | 0.57 | 1 | 100% |
| squashfs-zstd7-b128K+sorttype | 0.755 | 0.98 | 0.39 | 0.63 | 0.80 | 0.19 | 0.98 | 1 | 100% |
| squashfs-zstd7-b32K+sorttype | 0.760 | 1.02 | 0.46 | 0.71 | 0.82 | 0.21 | 0.56 | 1 | 100% |
| squashfs-zstd7-b32K+nofrag | 0.760 | 1.03 | 0.47 | 0.70 | 0.83 | 0.20 | 0.56 | 1 | 100% |
| squashfs-zstd7-b16K | 0.765 | 1.04 | 0.48 | 0.70 | 0.86 | 0.23 | 0.49 | 1 | 100% |
| squashfs-zstd12-b32K | 0.765 | 1.01 | 0.43 | 0.70 | 0.81 | 0.41 | 0.56 | 1 | 100% |
| squashfs-zstd9-b128K | 0.767 | 0.98 | 0.41 | 0.64 | 0.80 | 0.24 | 1.00 | 1 | 100% |
| squashfs-zstd7-b32K+nodup | 0.769 | 1.03 | 0.48 | 0.72 | 0.83 | 0.21 | 0.57 | 1 | 100% |
| squashfs-zstd7-b256K+nofrag | 0.769 | 0.98 | 0.39 | 0.70 | 0.79 | 0.18 | 1.00 | 1 | 100% |
| squashfs-zstd7-b128K+nofrag | 0.770 | 1.00 | 0.36 | 0.80 | 0.80 | 0.18 | 0.75 | 1 | 100% |
| dwarfs-l3-S20 | 0.775 | 1.13 | 0.06 | 0.63 | 1.10 | 0.44 | 1.17 | 1 | 100% |
| squashfs-zstd5-b256K | 0.782 | 0.99 | 0.34 | 0.66 | 0.83 | 0.14 | 1.38 | 1 | 100% |
| squashfs-zstd5-b64K | 0.784 | 1.02 | 0.36 | 0.89 | 0.82 | 0.13 | 0.70 | 1 | 100% |
| squashfs-zstd5-b128K | 0.785 | 1.00 | 0.41 | 0.73 | 0.82 | 0.13 | 1.01 | 1 | 100% |
| squashfs-zstd12-b128K | 0.785 | 0.97 | 0.36 | 0.70 | 0.78 | 0.53 | 0.99 | 1 | 100% |
| squashfs-zstd9-b256K | 0.786 | 0.96 | 0.39 | 0.67 | 0.78 | 0.25 | 1.37 | 1 | 100% |
| squashfs-zstd7-b128K+notail | 0.788 | 0.98 | 0.39 | 0.76 | 0.81 | 0.19 | 1.01 | 1 | 100% |
| squashfs-zstd7-b256K+notail | 0.789 | 0.96 | 0.39 | 0.70 | 0.79 | 0.18 | 1.38 | 1 | 100% |
| squashfs-zstd7-b256K | 0.790 | 0.96 | 0.39 | 0.70 | 0.79 | 0.18 | 1.38 | 1 | 100% |
| squashfs-zstd7-b32K+notail | 0.793 | 1.02 | 0.39 | 0.94 | 0.83 | 0.19 | 0.57 | 1 | 100% |
| squashfs-zstd12-b256K | 0.795 | 0.95 | 0.36 | 0.64 | 0.77 | 0.66 | 1.39 | 1 | 100% |
| squashfs-zstd7-b256K-fullrt | 0.795 | 0.96 | 0.42 | 0.69 | 0.79 | 0.19 | 1.37 | 1 | 100% |
| squashfs-zstd17-b32K | 0.798 | 0.97 | 0.51 | 0.74 | 0.77 | 1.89 | 0.56 | 1 | 100% |
| squashfs-zstd7-b256K+nodup | 0.801 | 0.97 | 0.39 | 0.75 | 0.79 | 0.18 | 1.35 | 1 | 100% |
| squashfs-zstd7-b128K+nodup | 0.808 | 0.99 | 0.39 | 0.86 | 0.80 | 0.19 | 1.00 | 1 | 100% |
| squashfs-zstd7-b32K | 0.814 | 1.02 | 0.43 | 1.02 | 0.83 | 0.19 | 0.57 | 1 | 100% |
| squashfs-zstd17-b128K | 0.827 | 0.92 | 0.51 | 0.73 | 0.75 | 2.20 | 1.01 | 1 | 100% |
| squashfs-zstd17-b256K | 0.829 | 0.90 | 0.43 | 0.70 | 0.76 | 2.10 | 1.38 | 1 | 100% |
| squashfs-zstd7-b128K | 0.831 | 0.98 | 0.39 | 0.99 | 0.81 | 0.19 | 1.01 | 1 | 100% |
| squashfs-zstd7-b64K | 0.840 | 1.00 | 0.36 | 1.26 | 0.81 | 0.19 | 0.69 | 1 | 100% |
| squashfs-zstd7-b256K+sorttype | 0.845 | 0.96 | 0.36 | 1.00 | 0.79 | 0.18 | 1.38 | 1 | 100% |
| squashfs-lz4hc-b128K | 0.845 | 1.15 | 0.16 | 0.83 | 0.86 | 1.06 | 1.01 | 1 | 100% |
| squashfs-zstd7-b512K | 0.869 | 0.96 | 0.39 | 0.73 | 0.80 | 0.18 | 3.28 | 1 | 100% |
| squashfs-lzo-b128K | 0.875 | 1.08 | 0.52 | 0.74 | 0.79 | 1.40 | 0.99 | 1 | 100% |
| squashfs-zstd9-b512K | 0.878 | 0.95 | 0.36 | 0.80 | 0.80 | 0.21 | 3.14 | 1 | 100% |
| squashfs-zstd5-b512K | 0.881 | 0.97 | 0.39 | 0.76 | 0.83 | 0.14 | 3.31 | 1 | 100% |
| squashfs-gzip7-b32K | 0.896 | 1.03 | 1.05 | 0.86 | 0.98 | 0.40 | 0.55 | 1 | 100% |
| squashfs-gzip5-b32K | 0.898 | 1.03 | 1.02 | 0.93 | 0.98 | 0.26 | 0.55 | 1 | 100% |
| squashfs-gzip8-b32K | 0.917 | 1.03 | 1.04 | 0.91 | 0.98 | 0.57 | 0.56 | 1 | 100% |
| squashfs-gzip3-b32K | 0.918 | 1.07 | 1.12 | 0.94 | 1.02 | 0.19 | 0.55 | 1 | 100% |
| squashfs-gzip5-b128K | 0.954 | 1.01 | 1.02 | 0.93 | 1.00 | 0.28 | 1.00 | 1 | 100% |
| squashfs-gzip7-b16K | 0.955 | 1.05 | 1.07 | 1.19 | 1.02 | 0.34 | 0.46 | 1 | 100% |
| dwarfs-brotli8-S20 | 0.977 | 0.90 | 0.10 | 1.05 | 2.21 | 0.67 | 1.16 | 1 | 100% |
| squashfs-gzip3-b128K | 0.981 | 1.05 | 1.05 | 0.99 | 1.04 | 0.21 | 0.99 | 1 | 100% |
| dwarfs-brotli3-S20 | 0.992 | 0.98 | 0.12 | 0.93 | 2.37 | 0.28 | 1.15 | 1 | 100% |
| dwarfs-l3-S16 | 0.995 | 1.19 | 0.31 | 0.36 | 2.49 | 0.42 | 1.47 | 1 | 100% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 100% |
| squashfs-gzip7-b128K | 1.003 | 1.00 | 1.00 | 1.13 | 1.00 | 0.50 | 0.99 | 1 | 100% |
| dwarfs-brotli6-S20 | 1.004 | 0.90 | 0.08 | 1.28 | 2.22 | 0.55 | 1.20 | 1 | 100% |
| squashfs-gzip7-b256K | 1.028 | 1.00 | 1.02 | 1.03 | 1.05 | 0.51 | 1.35 | 1 | 100% |
| squashfs-gzip5-b256K | 1.035 | 1.01 | 1.08 | 1.12 | 1.05 | 0.29 | 1.34 | 1 | 100% |
| squashfs-gzip3-b256K | 1.049 | 1.04 | 1.07 | 1.13 | 1.08 | 0.21 | 1.36 | 1 | 100% |
| dwarfs-l2-S24 | 1.081 | 1.14 | 0.75 | 0.38 | 0.84 | 0.42 | 23.56 | 1 | 100% |
| squashfs-xz-bcjauto-b32K | 1.190 | 0.90 | 3.30 | 1.68 | 1.11 | 3.95 | 0.58 | 1 | 100% |
| squashfs-xz-bcjnone-b32K | 1.192 | 0.92 | 3.20 | 1.72 | 1.15 | 2.19 | 0.58 | 1 | 100% |
| squashfs-xz-bcjnone-b128K | 1.249 | 0.87 | 3.11 | 1.88 | 1.14 | 2.12 | 1.02 | 1 | 100% |
| squashfs-xz-bcjauto-b128K | 1.251 | 0.84 | 3.11 | 1.88 | 1.10 | 4.27 | 1.02 | 1 | 100% |
| dwarfs-zstd7-S20 | 1.255 | 0.93 | 0.95 | 0.38 | 1.99 | 0.28 | 24.44 | 1 | 100% |
| dwarfs-zstd12-S20 | 1.276 | 0.93 | 1.06 | 0.39 | 1.97 | 0.41 | 24.44 | 1 | 100% |
| dwarfs-user-S26-B6-hot-fullrt | 1.303 | 0.76 | 2.53 | 0.38 | 1.59 | 5.19 | 23.39 | 1 | 100% |
| dwarfs-zstd17-S20 | 1.304 | 0.89 | 1.09 | 0.38 | 1.96 | 1.38 | 24.63 | 1 | 100% |
| dwarfs-l7-S24 | 1.307 | 0.77 | 2.27 | 0.35 | 1.84 | 4.60 | 22.78 | 1 | 100% |
| dwarfs-user-S26-B6-plain | 1.313 | 0.76 | 2.67 | 0.39 | 1.59 | 5.62 | 23.28 | 1 | 100% |
| dwarfs-l5-S20 | 1.314 | 0.86 | 1.21 | 0.36 | 1.99 | 2.45 | 24.63 | 1 | 100% |
| dwarfs-l5-S24 | 1.315 | 0.79 | 2.10 | 0.38 | 1.79 | 3.08 | 23.71 | 1 | 100% |
| dwarfs-l3-S26 | 1.315 | 1.11 | 1.05 | 0.83 | 0.91 | 0.49 | 23.10 | 1 | 100% |
| squashfs-xz-bcjnone-b256K | 1.324 | 0.85 | 3.09 | 2.26 | 1.12 | 2.19 | 1.40 | 1 | 100% |
| dwarfs-user-S26-B6-hot | 1.328 | 0.76 | 2.76 | 0.41 | 1.59 | 5.58 | 23.28 | 1 | 100% |
| squashfs-xz-bcjauto-b256K | 1.328 | 0.82 | 3.11 | 2.30 | 1.08 | 4.40 | 1.38 | 1 | 100% |
| dwarfs-l7-S20 | 1.330 | 0.85 | 1.20 | 0.38 | 2.00 | 3.17 | 24.52 | 1 | 100% |
| dwarfs-l5-S16 | 1.424 | 0.95 | 1.02 | 0.40 | 2.19 | 2.96 | 28.67 | 1 | 100% |
| dwarfs-l1-S24 | 1.460 | 1.33 | 0.93 | 0.93 | 1.28 | 0.08 | 23.42 | 1 | 100% |
| dwarfs-l7-S16 | 1.475 | 0.94 | 1.27 | 0.40 | 2.19 | 5.40 | 28.56 | 1 | 100% |
| dwarfs-zstd4-S20 | 1.476 | 0.98 | 1.20 | 0.68 | 2.18 | 0.25 | 23.16 | 1 | 100% |
| dwarfs-lzma7-S20 | 1.682 | 0.81 | 8.00 | 0.36 | 1.95 | 3.23 | 75.03 | 1 | 100% |
| dwarfs-lzma5-S20 | 1.725 | 0.82 | 8.51 | 0.41 | 1.96 | 2.61 | 74.64 | 1 | 100% |
| dwarfs-user-S26-B6-hot-c256M | 2.094 | 0.76 | 5.67 | 3.34 | 1.59 | 5.16 | 19.25 | 1 | 100% |
| dwarfs-lzma3-S20 | 2.188 | 0.86 | 8.41 | 1.25 | 2.06 | 1.13 | 86.38 | 1 | 100% |
| dwarfs-user-S26-B6-plain-c256M | 2.264 | 0.76 | 5.72 | 4.87 | 1.59 | 5.45 | 19.24 | 1 | 100% |
| dwarfs-user-S26-B6-plain-c64M | 3.310 | 0.76 | 15.62 | 24.78 | 1.59 | 5.21 | 15.03 | 1 | 100% |
| dwarfs-user-S26-B6-hot-c64M | 3.314 | 0.76 | 15.72 | 24.71 | 1.59 | 5.42 | 15.06 | 1 | 100% |

## Table 5 - Category: large-qt

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| dwarfs-l3-S24 | 0.757 | 1.11 | 0.07 |  | 1.07 | 0.39 | 0.69 | 2 | 80% |
| squashfs-zstd7-b64K | 0.776 | 1.00 | 0.28 |  | 0.98 | 0.16 | 0.68 | 2 | 80% |
| squashfs-zstd3-b32K | 0.786 | 1.08 | 0.33 |  | 1.06 | 0.06 | 0.55 | 2 | 80% |
| squashfs-zstd7-b32K+nofrag | 0.789 | 1.04 | 0.38 |  | 1.00 | 0.16 | 0.49 | 2 | 80% |
| squashfs-zstd5-b64K | 0.798 | 1.02 | 0.36 |  | 1.00 | 0.11 | 0.68 | 2 | 80% |
| squashfs-zstd7-b32K+nodup | 0.799 | 1.04 | 0.38 |  | 1.00 | 0.16 | 0.55 | 2 | 80% |
| squashfs-zstd7-b32K | 0.801 | 1.03 | 0.38 |  | 1.00 | 0.16 | 0.58 | 2 | 80% |
| squashfs-zstd9-b64K | 0.802 | 1.00 | 0.36 |  | 0.97 | 0.20 | 0.68 | 2 | 80% |
| squashfs-zstd7-b256K+nofrag | 0.805 | 0.98 | 0.34 |  | 0.94 | 0.15 | 0.91 | 2 | 80% |
| squashfs-zstd4-b32K | 0.806 | 1.07 | 0.46 |  | 1.04 | 0.07 | 0.55 | 2 | 80% |
| squashfs-zstd12-b32K | 0.810 | 1.02 | 0.33 |  | 1.00 | 0.41 | 0.55 | 2 | 80% |
| squashfs-zstd7-b128K+nofrag | 0.814 | 1.00 | 0.42 |  | 0.96 | 0.17 | 0.73 | 2 | 80% |
| squashfs-zstd7-b128K | 0.820 | 0.98 | 0.35 |  | 0.96 | 0.16 | 0.97 | 2 | 80% |
| squashfs-zstd7-b32K+notail | 0.824 | 1.03 | 0.49 |  | 1.00 | 0.17 | 0.57 | 2 | 80% |
| squashfs-zstd7-b16K | 0.824 | 1.06 | 0.46 |  | 1.03 | 0.20 | 0.49 | 2 | 80% |
| squashfs-zstd7-b128K+nodup | 0.824 | 0.98 | 0.35 |  | 0.97 | 0.16 | 0.97 | 2 | 80% |
| squashfs-zstd5-b128K | 0.829 | 1.00 | 0.37 |  | 0.99 | 0.10 | 0.99 | 2 | 80% |
| squashfs-zstd9-b128K | 0.830 | 0.97 | 0.36 |  | 0.96 | 0.20 | 1.00 | 2 | 80% |
| squashfs-zstd5-b256K | 0.833 | 0.98 | 0.29 |  | 0.98 | 0.11 | 1.36 | 2 | 80% |
| squashfs-zstd7-b256K-fullrt | 0.834 | 0.96 | 0.31 |  | 0.95 | 0.15 | 1.37 | 2 | 80% |
| squashfs-zstd7-b128K+notail | 0.835 | 0.98 | 0.41 |  | 0.96 | 0.17 | 0.96 | 2 | 80% |
| squashfs-zstd7-b256K | 0.840 | 0.95 | 0.34 |  | 0.95 | 0.15 | 1.37 | 2 | 80% |
| squashfs-zstd17-b32K | 0.841 | 0.97 | 0.45 |  | 0.94 | 1.33 | 0.56 | 2 | 80% |
| squashfs-zstd7-b256K+nodup | 0.845 | 0.96 | 0.36 |  | 0.95 | 0.15 | 1.36 | 2 | 80% |
| squashfs-zstd7-b256K+notail | 0.846 | 0.95 | 0.36 |  | 0.95 | 0.15 | 1.37 | 2 | 80% |
| squashfs-zstd12-b128K | 0.847 | 0.97 | 0.31 |  | 0.96 | 0.52 | 1.03 | 2 | 80% |
| squashfs-zstd9-b256K | 0.848 | 0.95 | 0.34 |  | 0.94 | 0.21 | 1.39 | 2 | 80% |
| squashfs-zstd17-b128K | 0.861 | 0.90 | 0.42 |  | 0.89 | 1.57 | 1.00 | 2 | 80% |
| squashfs-zstd17-b256K | 0.867 | 0.88 | 0.35 |  | 0.88 | 1.72 | 1.34 | 2 | 80% |
| squashfs-zstd12-b256K | 0.871 | 0.94 | 0.31 |  | 0.94 | 0.62 | 1.38 | 2 | 80% |
| dwarfs-brotli6-S20 | 0.898 | 0.86 | 0.08 |  | 1.79 | 0.45 | 1.63 | 2 | 80% |
| squashfs-gzip5-b32K | 0.904 | 1.05 | 1.08 |  | 1.03 | 0.22 | 0.53 | 2 | 80% |
| squashfs-lz4hc-b128K | 0.906 | 1.18 | 0.14 |  | 1.08 | 1.20 | 1.00 | 2 | 80% |
| squashfs-gzip7-b32K | 0.910 | 1.04 | 1.04 |  | 1.02 | 0.34 | 0.53 | 2 | 80% |
| squashfs-gzip7-b16K | 0.916 | 1.07 | 1.08 |  | 1.05 | 0.30 | 0.47 | 2 | 80% |
| squashfs-gzip3-b32K | 0.916 | 1.09 | 1.08 |  | 1.07 | 0.16 | 0.51 | 2 | 80% |
| squashfs-gzip8-b32K | 0.920 | 1.04 | 1.00 |  | 1.02 | 0.52 | 0.52 | 2 | 80% |
| squashfs-zstd9-b512K | 0.930 | 0.94 | 0.35 |  | 0.95 | 0.17 | 3.02 | 2 | 80% |
| dwarfs-brotli8-S20 | 0.932 | 0.86 | 0.11 |  | 1.78 | 0.66 | 1.61 | 2 | 80% |
| squashfs-zstd7-b512K | 0.933 | 0.94 | 0.35 |  | 0.96 | 0.15 | 3.24 | 2 | 80% |
| dwarfs-l3-S20 | 0.936 | 1.13 | 0.10 |  | 1.23 | 0.40 | 1.93 | 2 | 80% |
| squashfs-zstd5-b512K | 0.945 | 0.96 | 0.41 |  | 0.98 | 0.11 | 3.06 | 2 | 80% |
| dwarfs-brotli3-S20 | 0.947 | 0.96 | 0.12 |  | 1.99 | 0.23 | 1.21 | 2 | 80% |
| squashfs-gzip5-b128K | 0.956 | 1.01 | 1.04 |  | 1.01 | 0.24 | 0.96 | 2 | 80% |
| squashfs-gzip7-b128K | 0.968 | 1.00 | 1.04 |  | 1.00 | 0.41 | 0.96 | 2 | 80% |
| squashfs-gzip3-b128K | 0.979 | 1.06 | 1.07 |  | 1.06 | 0.18 | 0.97 | 2 | 80% |
| squashfs-xz-bcjnone-b32K | 0.979 | 0.90 | 3.08 |  | 0.87 | 1.93 | 0.55 | 2 | 80% |
| squashfs-xz-bcjauto-b32K | 0.987 | 0.88 | 3.04 |  | 0.85 | 3.65 | 0.58 | 2 | 80% |
| squashfs-xz-bcjnone-b128K | 0.987 | 0.83 | 2.90 |  | 0.81 | 2.03 | 0.97 | 2 | 80% |
| squashfs-xz-bcjauto-b128K | 0.990 | 0.81 | 2.85 |  | 0.79 | 4.08 | 0.97 | 2 | 80% |
| squashfs-lzo-b128K | 0.991 | 1.09 | 0.62 |  | 0.99 | 1.30 | 0.96 | 2 | 80% |
| squashfs-gzip5-b256K | 0.998 | 1.01 | 1.06 |  | 1.02 | 0.24 | 1.33 | 2 | 80% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 |  | 1.00 | 1.00 | 1.00 | 2 | 80% |
| squashfs-gzip7-b256K | 1.009 | 1.00 | 1.01 |  | 1.01 | 0.43 | 1.36 | 2 | 80% |
| squashfs-xz-bcjnone-b256K | 1.016 | 0.81 | 2.89 |  | 0.80 | 2.20 | 1.38 | 2 | 80% |
| squashfs-xz-bcjauto-b256K | 1.018 | 0.79 | 2.94 |  | 0.77 | 4.04 | 1.38 | 2 | 80% |
| squashfs-gzip3-b256K | 1.022 | 1.05 | 1.07 |  | 1.07 | 0.18 | 1.34 | 2 | 80% |
| dwarfs-l3-S16 | 1.319 | 1.22 | 0.36 |  | 1.96 | 0.38 | 2.74 | 2 | 80% |
| dwarfs-l2-S24 | 1.502 | 1.15 | 0.65 |  | 1.03 | 0.35 | 27.98 | 2 | 80% |
| dwarfs-l3-S26 | 1.539 | 1.11 | 1.03 |  | 1.06 | 0.43 | 24.33 | 2 | 80% |
| dwarfs-l1-S24 | 1.618 | 1.39 | 0.62 |  | 1.28 | 0.06 | 28.09 | 2 | 80% |
| dwarfs-zstd7-S20 | 1.624 | 0.91 | 1.06 |  | 1.77 | 0.24 | 25.21 | 2 | 80% |
| dwarfs-l7-S24 | 1.628 | 0.74 | 2.29 |  | 1.37 | 3.46 | 23.86 | 2 | 80% |
| dwarfs-user-S26-B6-plain | 1.642 | 0.71 | 2.79 |  | 1.38 | 3.99 | 23.99 | 2 | 80% |
| dwarfs-user-S26-B6-hot-fullrt | 1.654 | 0.71 | 2.86 |  | 1.38 | 4.05 | 24.06 | 2 | 80% |
| dwarfs-user-S26-B6-hot | 1.655 | 0.71 | 2.94 |  | 1.38 | 4.28 | 23.98 | 2 | 80% |
| dwarfs-zstd12-S20 | 1.656 | 0.90 | 1.18 |  | 1.75 | 0.37 | 25.06 | 2 | 80% |
| dwarfs-l7-S20 | 1.661 | 0.81 | 1.39 |  | 1.51 | 3.28 | 24.31 | 2 | 80% |
| dwarfs-user-S26-B6-plain-c256M | 1.678 | 0.71 | 4.95 |  | 1.38 | 3.84 | 18.20 | 2 | 80% |
| dwarfs-zstd17-S20 | 1.688 | 0.86 | 1.28 |  | 1.67 | 1.36 | 24.81 | 2 | 80% |
| dwarfs-user-S26-B6-hot-c256M | 1.688 | 0.71 | 5.19 |  | 1.38 | 4.01 | 18.16 | 2 | 80% |
| dwarfs-l5-S20 | 1.715 | 0.82 | 1.47 |  | 1.66 | 2.54 | 24.59 | 2 | 80% |
| dwarfs-zstd4-S20 | 1.717 | 0.96 | 1.24 |  | 1.89 | 0.22 | 25.42 | 2 | 80% |
| dwarfs-l5-S24 | 1.726 | 0.76 | 2.87 |  | 1.49 | 2.73 | 25.09 | 2 | 80% |
| dwarfs-user-S26-B6-plain-c64M | 1.726 | 0.71 | 9.37 |  | 1.38 | 3.99 | 13.57 | 2 | 80% |
| dwarfs-user-S26-B6-hot-c64M | 1.759 | 0.71 | 11.06 |  | 1.38 | 4.16 | 13.63 | 2 | 80% |
| dwarfs-l5-S16 | 1.805 | 0.93 | 1.04 |  | 1.64 | 3.07 | 30.09 | 2 | 80% |
| dwarfs-l7-S16 | 1.832 | 0.93 | 0.95 |  | 1.75 | 3.79 | 30.24 | 2 | 80% |
| dwarfs-lzma5-S20 | 2.200 | 0.76 | 7.78 |  | 1.54 | 2.21 | 76.18 | 2 | 80% |
| dwarfs-lzma7-S20 | 2.222 | 0.76 | 8.02 |  | 1.54 | 2.92 | 76.21 | 2 | 80% |
| dwarfs-lzma3-S20 | 2.256 | 0.81 | 7.62 |  | 1.64 | 0.90 | 87.49 | 2 | 80% |
| squashfs-zstd7-b128K+sorttype |  |  |  |  |  |  |  | 2 | 0% |
| squashfs-zstd7-b256K+sorttype |  |  |  |  |  |  |  | 2 | 0% |
| squashfs-zstd7-b32K+sorttype |  |  |  |  |  |  |  | 2 | 0% |

## Table 5 - Category: small-qt

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd9-b64K | 0.788 | 1.01 | 0.30 | 0.71 | 0.98 | 0.21 | 0.76 | 1 | 100% |
| squashfs-zstd7-b128K+nofrag | 0.796 | 1.00 | 0.30 | 0.77 | 0.97 | 0.17 | 0.85 | 1 | 100% |
| squashfs-zstd5-b64K | 0.803 | 1.03 | 0.33 | 0.77 | 1.02 | 0.11 | 0.74 | 1 | 100% |
| squashfs-zstd7-b32K+nofrag | 0.805 | 1.04 | 0.37 | 0.77 | 1.03 | 0.17 | 0.61 | 1 | 100% |
| squashfs-zstd7-b32K+notail | 0.806 | 1.04 | 0.32 | 0.76 | 1.03 | 0.17 | 0.69 | 1 | 100% |
| squashfs-zstd7-b256K+nofrag | 0.806 | 0.97 | 0.30 | 0.77 | 0.93 | 0.16 | 1.16 | 1 | 100% |
| squashfs-zstd7-b32K+sorttype | 0.808 | 1.04 | 0.37 | 0.77 | 1.03 | 0.17 | 0.62 | 1 | 100% |
| squashfs-zstd3-b32K | 0.808 | 1.09 | 0.33 | 0.77 | 1.09 | 0.06 | 0.69 | 1 | 100% |
| squashfs-zstd4-b32K | 0.810 | 1.07 | 0.37 | 0.77 | 1.08 | 0.06 | 0.69 | 1 | 100% |
| squashfs-zstd7-b64K | 0.812 | 1.01 | 0.33 | 0.77 | 0.99 | 0.17 | 0.85 | 1 | 100% |
| squashfs-zstd7-b128K+notail | 0.814 | 1.00 | 0.29 | 0.76 | 0.97 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd7-b32K | 0.816 | 1.04 | 0.37 | 0.77 | 1.03 | 0.17 | 0.69 | 1 | 100% |
| squashfs-zstd7-b128K+sorttype | 0.816 | 1.00 | 0.30 | 0.77 | 0.97 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd7-b128K | 0.817 | 1.00 | 0.30 | 0.77 | 0.97 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd7-b32K+nodup | 0.818 | 1.07 | 0.37 | 0.77 | 1.04 | 0.17 | 0.62 | 1 | 100% |
| squashfs-zstd9-b128K | 0.819 | 0.99 | 0.30 | 0.77 | 0.96 | 0.21 | 1.08 | 1 | 100% |
| squashfs-zstd7-b16K | 0.821 | 1.06 | 0.35 | 0.82 | 1.06 | 0.21 | 0.53 | 1 | 100% |
| squashfs-zstd5-b128K | 0.825 | 1.02 | 0.33 | 0.77 | 1.00 | 0.11 | 1.08 | 1 | 100% |
| squashfs-zstd7-b128K+nodup | 0.827 | 1.02 | 0.30 | 0.77 | 0.98 | 0.17 | 1.08 | 1 | 100% |
| squashfs-zstd7-b256K+notail | 0.830 | 0.97 | 0.30 | 0.77 | 0.94 | 0.16 | 1.51 | 1 | 100% |
| dwarfs-l3-S24 | 0.830 | 1.15 | 0.07 | 0.89 | 1.17 | 0.48 | 0.83 | 1 | 100% |
| squashfs-zstd7-b256K-fullrt | 0.831 | 0.97 | 0.30 | 0.77 | 0.94 | 0.16 | 1.51 | 1 | 100% |
| squashfs-zstd7-b256K | 0.837 | 0.97 | 0.33 | 0.77 | 0.94 | 0.16 | 1.52 | 1 | 100% |
| squashfs-zstd9-b256K | 0.839 | 0.96 | 0.30 | 0.77 | 0.94 | 0.22 | 1.60 | 1 | 100% |
| squashfs-zstd5-b256K | 0.840 | 1.00 | 0.30 | 0.77 | 0.98 | 0.12 | 1.53 | 1 | 100% |
| squashfs-zstd7-b256K+sorttype | 0.843 | 0.97 | 0.33 | 0.77 | 0.95 | 0.16 | 1.60 | 1 | 100% |
| squashfs-zstd7-b256K+nodup | 0.845 | 1.00 | 0.30 | 0.77 | 0.96 | 0.16 | 1.60 | 1 | 100% |
| squashfs-zstd17-b128K | 0.850 | 0.91 | 0.38 | 0.76 | 0.90 | 1.82 | 1.09 | 1 | 100% |
| squashfs-lz4hc-b128K | 0.857 | 1.19 | 0.13 | 0.71 | 1.11 | 1.00 | 0.90 | 1 | 100% |
| squashfs-zstd17-b32K | 0.872 | 0.97 | 0.44 | 0.83 | 0.99 | 1.62 | 0.69 | 1 | 100% |
| squashfs-zstd17-b256K | 0.880 | 0.89 | 0.40 | 0.83 | 0.88 | 1.61 | 1.52 | 1 | 100% |
| squashfs-zstd7-b512K | 0.896 | 0.95 | 0.30 | 0.77 | 0.94 | 0.15 | 3.56 | 1 | 100% |
| squashfs-zstd9-b512K | 0.899 | 0.95 | 0.30 | 0.77 | 0.93 | 0.18 | 3.64 | 1 | 100% |
| squashfs-zstd5-b512K | 0.900 | 0.97 | 0.30 | 0.77 | 0.97 | 0.11 | 3.59 | 1 | 100% |
| squashfs-gzip7-b16K | 0.918 | 1.06 | 1.10 | 0.88 | 1.09 | 0.32 | 0.47 | 1 | 100% |
| squashfs-gzip8-b32K | 0.943 | 1.03 | 1.04 | 0.86 | 1.05 | 0.56 | 0.68 | 1 | 100% |
| dwarfs-brotli3-S20 | 0.951 | 0.99 | 0.07 | 0.89 | 2.80 | 0.23 | 0.84 | 1 | 100% |
| squashfs-gzip3-b32K | 0.961 | 1.08 | 1.09 | 1.00 | 1.12 | 0.17 | 0.63 | 1 | 100% |
| squashfs-lzo-b128K | 0.980 | 1.10 | 0.52 | 0.89 | 1.05 | 1.35 | 1.00 | 1 | 100% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 100% |
| squashfs-xz-bcjauto-b32K | 1.088 | 0.86 | 3.43 | 1.45 | 0.86 | 3.48 | 0.69 | 1 | 100% |
| squashfs-xz-bcjnone-b32K | 1.089 | 0.89 | 3.43 | 1.40 | 0.92 | 1.88 | 0.69 | 1 | 100% |
| squashfs-xz-bcjauto-b128K | 1.108 | 0.79 | 3.33 | 1.63 | 0.77 | 4.25 | 1.09 | 1 | 100% |
| squashfs-xz-bcjnone-b128K | 1.122 | 0.83 | 3.23 | 1.68 | 0.83 | 1.97 | 1.08 | 1 | 100% |
| dwarfs-l2-S24 | 1.123 | 1.21 | 0.32 | 0.83 | 1.11 | 0.39 | 6.19 | 1 | 100% |
| squashfs-xz-bcjauto-b256K | 1.144 | 0.77 | 3.22 | 1.80 | 0.75 | 4.34 | 1.53 | 1 | 100% |
| dwarfs-l3-S20 | 1.150 | 1.21 | 0.34 | 0.70 | 1.34 | 0.41 | 6.59 | 1 | 100% |
| squashfs-xz-bcjnone-b256K | 1.155 | 0.80 | 3.03 | 1.80 | 0.81 | 2.20 | 1.54 | 1 | 100% |
| dwarfs-l3-S26 | 1.167 | 1.18 | 0.29 | 0.89 | 1.18 | 0.75 | 6.34 | 1 | 100% |
| dwarfs-l5-S24 | 1.260 | 0.75 | 0.74 | 0.88 | 2.03 | 3.48 | 5.79 | 1 | 100% |
| dwarfs-l1-S24 | 1.266 | 1.44 | 0.35 | 0.95 | 1.49 | 0.08 | 6.23 | 1 | 100% |
| dwarfs-zstd7-S20 | 1.287 | 0.92 | 0.74 | 0.73 | 2.54 | 0.25 | 6.46 | 1 | 100% |
| dwarfs-l5-S20 | 1.356 | 0.83 | 0.93 | 0.82 | 2.31 | 2.45 | 6.40 | 1 | 100% |
| dwarfs-l7-S24 | 1.358 | 0.80 | 0.89 | 0.83 | 2.42 | 4.08 | 5.93 | 1 | 100% |
| dwarfs-zstd4-S20 | 1.368 | 1.01 | 0.68 | 0.83 | 2.73 | 0.19 | 6.61 | 1 | 100% |
| dwarfs-zstd17-S20 | 1.371 | 0.91 | 0.74 | 0.83 | 2.39 | 1.53 | 6.60 | 1 | 100% |
| dwarfs-user-S26-B6-plain | 1.402 | 0.73 | 0.85 | 0.66 | 3.65 | 6.89 | 5.86 | 1 | 100% |
| dwarfs-user-S26-B6-hot | 1.403 | 0.73 | 0.85 | 0.65 | 3.65 | 6.93 | 5.94 | 1 | 100% |
| dwarfs-l7-S20 | 1.409 | 0.87 | 0.96 | 0.77 | 2.55 | 2.96 | 6.72 | 1 | 100% |
| dwarfs-l5-S16 | 1.458 | 0.96 | 1.04 | 0.69 | 2.71 | 3.08 | 6.63 | 1 | 100% |
| dwarfs-l3-S16 | 1.466 | 1.28 | 0.45 | 0.70 | 3.04 | 0.35 | 7.18 | 1 | 100% |
| dwarfs-brotli6-S20 | 1.483 | 0.92 | 1.81 | 0.89 | 2.54 | 0.48 | 7.14 | 1 | 100% |
| dwarfs-brotli8-S20 | 1.486 | 0.92 | 1.65 | 0.90 | 2.52 | 0.71 | 7.17 | 1 | 100% |
| dwarfs-l7-S16 | 1.540 | 0.96 | 1.07 | 0.78 | 3.00 | 3.71 | 6.64 | 1 | 100% |
| dwarfs-lzma7-S20 | 1.683 | 0.80 | 4.50 | 0.93 | 2.14 | 3.11 | 15.89 | 1 | 100% |
| dwarfs-lzma5-S20 | 1.726 | 0.76 | 4.96 | 1.15 | 2.15 | 2.39 | 15.59 | 1 | 100% |
| dwarfs-lzma3-S20 | 1.798 | 0.85 | 4.86 | 1.12 | 2.31 | 0.90 | 19.39 | 1 | 100% |
| dwarfs-user-S26-B6-hot-c256M | 2.272 | 0.73 | 0.61 | 8.77 | 3.65 | 7.11 | 5.31 | 1 | 100% |
| dwarfs-user-S26-B6-plain-c64M | 2.891 | 0.73 | 10.75 | 8.82 | 3.65 | 7.11 | 5.86 | 1 | 100% |
| dwarfs-user-S26-B6-hot-c64M | 2.941 | 0.73 | 11.59 | 9.17 | 3.65 | 7.54 | 5.95 | 1 | 100% |

## Table 5 - Category: tiny-cli

| variant | weighted score | total size | startup CPU | app start: mount + launch (cold) | update cost | build time | RAM (FUSE) | apps | weight coverage |
|---|---|---|---|---|---|---|---|---|---|
| squashfs-zstd7-b32K+nofrag | 0.802 | 1.06 |  | 0.58 | 1.00 | 0.20 | 0.52 | 1 | 92% |
| squashfs-zstd7-b32K+sorttype | 0.802 | 1.03 |  | 0.56 | 1.00 | 0.21 | 0.58 | 1 | 92% |
| squashfs-zstd7-b32K+nodup | 0.804 | 1.04 |  | 0.56 | 1.01 | 0.21 | 0.58 | 1 | 92% |
| squashfs-zstd7-b32K+notail | 0.804 | 1.03 |  | 0.56 | 1.01 | 0.21 | 0.59 | 1 | 92% |
| squashfs-zstd7-b128K+nofrag | 0.808 | 1.03 |  | 0.56 | 0.96 | 0.20 | 0.72 | 1 | 92% |
| squashfs-zstd7-b16K | 0.811 | 1.07 |  | 0.56 | 1.02 | 0.25 | 0.52 | 1 | 92% |
| squashfs-zstd7-b64K | 0.812 | 1.01 |  | 0.56 | 1.00 | 0.21 | 0.72 | 1 | 92% |
| squashfs-zstd9-b64K | 0.816 | 1.00 |  | 0.56 | 1.00 | 0.26 | 0.72 | 1 | 92% |
| squashfs-zstd5-b64K | 0.819 | 1.03 |  | 0.57 | 1.04 | 0.16 | 0.71 | 1 | 92% |
| squashfs-zstd7-b256K+nofrag | 0.828 | 1.02 |  | 0.56 | 0.94 | 0.19 | 0.99 | 1 | 92% |
| squashfs-zstd7-b128K | 0.842 | 0.98 |  | 0.56 | 1.02 | 0.21 | 1.06 | 1 | 92% |
| squashfs-zstd7-b128K+notail | 0.842 | 0.98 |  | 0.56 | 1.02 | 0.21 | 1.06 | 1 | 92% |
| squashfs-zstd7-b128K+sorttype | 0.843 | 0.98 |  | 0.56 | 1.02 | 0.22 | 1.06 | 1 | 92% |
| squashfs-zstd9-b128K | 0.844 | 0.97 |  | 0.56 | 1.01 | 0.26 | 1.06 | 1 | 92% |
| squashfs-zstd7-b128K+nodup | 0.847 | 0.98 |  | 0.57 | 1.02 | 0.24 | 1.05 | 1 | 92% |
| squashfs-zstd5-b128K | 0.848 | 1.01 |  | 0.56 | 1.05 | 0.15 | 1.06 | 1 | 92% |
| squashfs-zstd7-b256K | 0.857 | 0.96 |  | 0.56 | 1.05 | 0.20 | 1.30 | 1 | 92% |
| squashfs-zstd7-b256K+notail | 0.858 | 0.96 |  | 0.56 | 1.05 | 0.21 | 1.29 | 1 | 92% |
| squashfs-zstd7-b256K+sorttype | 0.858 | 0.96 |  | 0.58 | 1.01 | 0.20 | 1.36 | 1 | 92% |
| squashfs-zstd7-b256K-fullrt | 0.861 | 0.97 |  | 0.56 | 1.05 | 0.20 | 1.29 | 1 | 92% |
| squashfs-zstd7-b256K+nodup | 0.862 | 0.96 |  | 0.57 | 1.04 | 0.22 | 1.30 | 1 | 92% |
| squashfs-zstd9-b256K | 0.865 | 0.95 |  | 0.56 | 1.04 | 0.28 | 1.35 | 1 | 92% |
| squashfs-zstd5-b256K | 0.868 | 0.98 |  | 0.56 | 1.09 | 0.16 | 1.30 | 1 | 92% |
| squashfs-zstd17-b256K | 0.879 | 0.88 |  | 0.56 | 0.97 | 1.99 | 1.30 | 1 | 92% |
| squashfs-zstd9-b512K | 0.905 | 0.93 |  | 0.56 | 1.11 | 0.22 | 1.99 | 1 | 92% |
| squashfs-zstd3-b32K | 0.909 | 1.10 |  | 1.00 | 1.07 | 0.10 | 0.52 | 1 | 92% |
| squashfs-zstd7-b32K | 0.911 | 1.03 |  |  |  | 0.21 |  | 1 | 38% |
| squashfs-zstd7-b512K | 0.912 | 0.94 |  | 0.56 | 1.11 | 0.19 | 2.21 | 1 | 92% |
| squashfs-gzip7-b16K | 0.913 | 1.08 |  | 1.00 | 1.03 | 0.31 | 0.44 | 1 | 92% |
| squashfs-zstd4-b32K | 0.914 | 1.08 |  | 1.00 | 1.05 | 0.10 | 0.59 | 1 | 92% |
| squashfs-zstd5-b512K | 0.918 | 0.96 |  | 0.56 | 1.14 | 0.14 | 2.15 | 1 | 92% |
| squashfs-zstd12-b32K | 0.921 | 1.03 |  | 1.00 | 1.00 | 0.51 | 0.52 | 1 | 92% |
| squashfs-gzip5-b32K | 0.924 | 1.05 |  | 1.00 | 1.02 | 0.27 | 0.58 | 1 | 92% |
| squashfs-zstd12-b256K | 0.927 | 0.94 |  |  |  | 0.78 |  | 1 | 38% |
| squashfs-gzip7-b32K | 0.931 | 1.04 |  | 1.00 | 1.01 | 0.39 | 0.58 | 1 | 92% |
| squashfs-gzip3-b256K | 0.936 | 1.07 |  |  |  | 0.20 |  | 1 | 38% |
| squashfs-gzip3-b128K | 0.940 | 1.07 |  |  |  | 0.20 |  | 1 | 38% |
| squashfs-gzip8-b32K | 0.941 | 1.04 |  | 1.00 | 1.01 | 0.55 | 0.58 | 1 | 92% |
| squashfs-zstd17-b32K | 0.942 | 0.98 |  | 1.00 | 0.95 | 1.66 | 0.59 | 1 | 92% |
| squashfs-gzip3-b32K | 0.958 | 1.10 |  |  |  | 0.19 |  | 1 | 38% |
| squashfs-gzip5-b128K | 0.976 | 1.01 |  | 1.00 | 1.03 | 0.28 | 1.05 | 1 | 92% |
| squashfs-zstd12-b128K | 0.981 | 0.97 |  | 1.00 | 1.00 | 0.64 | 1.06 | 1 | 92% |
| squashfs-gzip7-b128K | 0.985 | 1.00 |  | 1.00 | 1.01 | 0.46 | 1.06 | 1 | 92% |
| squashfs-zstd17-b128K | 0.985 | 0.91 |  | 1.00 | 0.96 | 2.05 | 1.06 | 1 | 92% |
| **squashfs-gzip9-b128K** | 1.000 | 1.00 |  | 1.00 | 1.00 | 1.00 | 1.00 | 1 | 92% |
| squashfs-gzip5-b256K | 1.010 | 1.01 |  | 1.00 | 1.08 | 0.29 | 1.30 | 1 | 92% |
| squashfs-gzip7-b256K | 1.017 | 1.00 |  | 1.00 | 1.05 | 0.49 | 1.30 | 1 | 92% |
| squashfs-lzo-b128K | 1.042 | 1.13 |  | 1.00 | 0.93 | 1.32 | 1.06 | 1 | 92% |
| squashfs-xz-bcjnone-b32K | 1.055 | 0.95 |  | 1.87 | 0.89 | 2.06 | 0.58 | 1 | 92% |
| squashfs-xz-bcjauto-b32K | 1.063 | 0.94 |  | 1.87 | 0.87 | 3.78 | 0.58 | 1 | 92% |
| squashfs-xz-bcjnone-b128K | 1.076 | 0.87 |  | 1.87 | 0.85 | 2.20 | 1.06 | 1 | 92% |
| dwarfs-l5-S24 | 1.077 | 0.83 |  | 0.56 | 1.59 | 4.93 | 2.53 | 1 | 92% |
| squashfs-xz-bcjauto-b128K | 1.083 | 0.86 |  | 1.87 | 0.82 | 4.16 | 1.06 | 1 | 92% |
| dwarfs-brotli6-S20 | 1.086 | 0.94 |  | 1.00 | 1.86 | 0.60 | 0.71 | 1 | 92% |
| dwarfs-l7-S24 | 1.087 | 0.82 |  | 0.56 | 1.60 | 6.29 | 2.60 | 1 | 92% |
| dwarfs-brotli8-S20 | 1.092 | 0.93 |  | 1.00 | 1.84 | 0.81 | 0.71 | 1 | 92% |
| dwarfs-user-S26-B6-plain | 1.093 | 0.81 |  | 0.56 | 1.58 | 10.68 | 2.46 | 1 | 92% |
| dwarfs-user-S26-B6-plain-c64M | 1.094 | 0.81 |  | 0.56 | 1.58 | 10.33 | 2.46 | 1 | 92% |
| dwarfs-zstd7-S20 | 1.095 | 0.97 |  | 0.56 | 1.88 | 0.34 | 2.55 | 1 | 92% |
| dwarfs-user-S26-B6-plain-c256M | 1.095 | 0.81 |  | 0.56 | 1.58 | 10.52 | 2.46 | 1 | 92% |
| squashfs-xz-bcjnone-b256K | 1.095 | 0.84 |  | 1.87 | 0.87 | 2.22 | 1.31 | 1 | 92% |
| dwarfs-l3-S24 | 1.100 | 1.33 |  | 0.56 | 1.06 | 0.68 | 2.79 | 1 | 92% |
| squashfs-lz4hc-b128K | 1.103 | 1.21 |  | 1.00 | 1.05 | 1.49 | 0.99 | 1 | 92% |
| dwarfs-l5-S20 | 1.111 | 0.88 |  | 0.56 | 1.72 | 2.83 | 2.62 | 1 | 92% |
| dwarfs-l7-S20 | 1.113 | 0.87 |  | 0.56 | 1.71 | 3.63 | 2.62 | 1 | 92% |
| dwarfs-zstd17-S20 | 1.114 | 0.91 |  | 0.56 | 1.78 | 1.66 | 2.61 | 1 | 92% |
| squashfs-xz-bcjauto-b256K | 1.154 | 0.83 |  | 2.31 | 0.84 | 4.48 | 1.30 | 1 | 92% |
| dwarfs-lzma5-S20 | 1.161 | 0.84 |  | 0.56 | 1.63 | 2.55 | 5.40 | 1 | 92% |
| dwarfs-lzma7-S20 | 1.165 | 0.84 |  | 0.56 | 1.62 | 2.94 | 5.49 | 1 | 92% |
| dwarfs-l5-S16 | 1.218 | 1.00 |  | 0.56 | 1.98 | 3.65 | 2.62 | 1 | 92% |
| dwarfs-l7-S16 | 1.223 | 0.99 |  | 0.56 | 1.98 | 4.64 | 2.61 | 1 | 92% |
| dwarfs-l2-S24 | 1.235 | 1.33 |  | 1.00 | 1.04 | 0.53 | 2.84 | 1 | 92% |
| dwarfs-l3-S20 | 1.247 | 1.34 |  | 1.00 | 1.10 | 0.47 | 2.73 | 1 | 92% |
| dwarfs-l3-S26 | 1.268 | 1.33 |  | 1.00 | 1.06 | 1.16 | 2.74 | 1 | 92% |
| dwarfs-zstd12-S20 | 1.317 | 1.08 |  | 1.00 | 1.85 | 0.47 | 2.73 | 1 | 92% |
| dwarfs-l1-S24 | 1.362 | 1.63 |  | 1.00 | 1.33 | 0.13 | 2.83 | 1 | 92% |
| dwarfs-zstd4-S20 | 1.365 | 1.15 |  | 1.00 | 2.04 | 0.31 | 2.77 | 1 | 92% |
| dwarfs-brotli3-S20 | 1.398 | 1.16 |  | 1.00 | 2.06 | 0.31 | 3.28 | 1 | 92% |
| dwarfs-l3-S16 | 1.473 | 1.43 |  | 1.00 | 1.89 | 0.42 | 2.83 | 1 | 92% |
| dwarfs-lzma3-S20 | 1.546 | 1.02 |  | 1.44 | 1.75 | 0.98 | 6.56 | 1 | 92% |
| dwarfs-user-S26-B6-hot |  |  |  |  |  |  |  | 1 | 0% |
| dwarfs-user-S26-B6-hot-c256M |  |  |  |  |  |  |  | 1 | 0% |
| dwarfs-user-S26-B6-hot-c64M |  |  |  |  |  |  |  | 1 | 0% |
| dwarfs-user-S26-B6-hot-fullrt |  |  |  |  |  |  |  | 1 | 0% |

## Table 2 - Total size per app (MB)

| app | uncompressed | dwarfs-l3-S24 | squashfs-zstd9-b64K | squashfs-zstd7-b32K+nofrag | squashfs-zstd7-b256K+nofrag | squashfs-zstd7-b128K+nofrag | squashfs-zstd5-b64K | squashfs-gzip9-b128K | best |
|---|---|---|---|---|---|---|---|---|---|
| kdenlive/x86_64 | 658.6 | 241.4 | 218.7 | 228.8 | 215.0 | 219.4 | 224.4 | 218.4 | dwarfs-user-S26-B6-plain |
| keepassxc/x86_64 | 110.3 | 47.5 | 41.8 | 42.9 | 40.3 | 41.3 | 42.7 | 41.4 | dwarfs-user-S26-B6-plain |
| krita/x86_64 | 1003.6 | 407.7 | 365.2 | 379.6 | 362.2 | 367.4 | 373.3 | 368.4 | dwarfs-user-S26-B6-plain |
| libreoffice/x86_64 | 732.8 | 332.9 | 298.2 | 308.2 | 295.0 | 299.0 | 304.8 | 300.0 | dwarfs-user-S26-B6-plain |
| neovim/x86_64 | 38.0 | 17.1 | 12.9 | 13.7 | 13.1 | 13.2 | 13.3 | 12.9 | dwarfs-user-S26-B6-plain |
| obsidian/x86_64 | 291.3 | 126.8 | 113.5 | 118.5 | 107.4 | 111.0 | 116.2 | 113.9 | dwarfs-user-S26-B6-plain |

## Table 3 - zsync update cost (median over patch pairs)

| variant | zsync -b | .zsync | downloaded | total update | % of image | requests |
|---|---|---|---|---|---|---|
| dwarfs-brotli3-S20 | 1024 | 1.0M | 108.7M | 109.5M | 80.6% | 2 |
| dwarfs-brotli3-S20 | 2048 | 536.1K | 108.8M | 109.2M | 80.4% | 1 |
| dwarfs-brotli3-S20 | 4096 | 268.2K | 108.8M | 109.0M | 80.6% | 1 |
| dwarfs-brotli6-S20 | 1024 | 956.6K | 97.4M | 98.1M | 85.1% | 2 |
| dwarfs-brotli6-S20 | 2048 | 478.4K | 97.4M | 97.8M | 84.8% | 1 |
| dwarfs-brotli6-S20 | 4096 | 239.3K | 97.5M | 97.6M | 84.7% | 1 |
| dwarfs-brotli8-S20 | 1024 | 948.6K | 96.5M | 97.2M | 84.9% | 1 |
| dwarfs-brotli8-S20 | 2048 | 474.4K | 96.5M | 96.9M | 84.7% | 1 |
| dwarfs-brotli8-S20 | 4096 | 237.3K | 96.6M | 96.8M | 84.6% | 1 |
| dwarfs-l1-S24 | 1024 | 1.5M | 62.3M | 64.1M | 30.4% | 39 |
| dwarfs-l1-S24 | 2048 | 786.9K | 63.4M | 64.3M | 30.9% | 21 |
| dwarfs-l1-S24 | 4096 | 393.6K | 64.6M | 65.1M | 31.5% | 13 |
| dwarfs-l2-S24 | 1024 | 1.2M | 50.3M | 51.7M | 26.5% | 31 |
| dwarfs-l2-S24 | 2048 | 638.8K | 51.3M | 52.1M | 26.9% | 18 |
| dwarfs-l2-S24 | 4096 | 319.5K | 52.6M | 53.1M | 27.5% | 12 |
| dwarfs-l3-S16 | 1024 | 1.3M | 124.0M | 125.6M | 56.2% | 96 |
| dwarfs-l3-S16 | 2048 | 678.8K | 129.8M | 130.3M | 60.3% | 48 |
| dwarfs-l3-S16 | 4096 | 339.5K | 132.9M | 133.1M | 65.1% | 23 |
| dwarfs-l3-S20 | 1024 | 1.2M | 65.6M | 67.1M | 33.8% | 51 |
| dwarfs-l3-S20 | 2048 | 627.0K | 68.6M | 69.4M | 35.0% | 33 |
| dwarfs-l3-S20 | 4096 | 313.6K | 72.4M | 72.9M | 36.5% | 22 |
| dwarfs-l3-S24 | 1024 | 1.2M | 51.7M | 53.2M | 29.2% | 38 |
| dwarfs-l3-S24 | 2048 | 614.7K | 53.0M | 53.7M | 29.8% | 22 |
| dwarfs-l3-S24 | 4096 | 307.4K | 54.4M | 54.8M | 30.6% | 15 |
| dwarfs-l3-S26 | 1024 | 1.2M | 50.5M | 52.0M | 28.6% | 38 |
| dwarfs-l3-S26 | 2048 | 614.8K | 51.6M | 52.4M | 29.1% | 22 |
| dwarfs-l3-S26 | 4096 | 307.5K | 52.9M | 53.3M | 29.9% | 15 |
| dwarfs-l5-S16 | 1024 | 1.0M | 104.2M | 105.0M | 73.8% | 13 |
| dwarfs-l5-S16 | 2048 | 521.0K | 104.6M | 105.0M | 74.5% | 6 |
| dwarfs-l5-S16 | 4096 | 260.6K | 105.0M | 105.2M | 76.8% | 3 |
| dwarfs-l5-S20 | 1024 | 896.5K | 90.2M | 90.9M | 82.6% | 8 |
| dwarfs-l5-S20 | 2048 | 448.3K | 90.5M | 90.8M | 82.8% | 2 |
| dwarfs-l5-S20 | 4096 | 224.3K | 90.6M | 90.8M | 83.1% | 2 |
| dwarfs-l5-S24 | 1024 | 829.7K | 82.1M | 82.7M | 79.8% | 12 |
| dwarfs-l5-S24 | 2048 | 415.0K | 82.5M | 82.8M | 80.2% | 4 |
| dwarfs-l5-S24 | 4096 | 207.6K | 82.7M | 82.8M | 80.7% | 2 |
| dwarfs-l7-S16 | 1024 | 1.0M | 105.1M | 106.2M | 78.7% | 17 |
| dwarfs-l7-S16 | 2048 | 517.5K | 105.7M | 106.2M | 79.2% | 8 |
| dwarfs-l7-S16 | 4096 | 258.8K | 106.1M | 106.4M | 80.0% | 6 |
| dwarfs-l7-S20 | 1024 | 888.9K | 89.2M | 89.8M | 77.2% | 11 |
| dwarfs-l7-S20 | 2048 | 444.6K | 89.5M | 89.9M | 77.4% | 5 |
| dwarfs-l7-S20 | 4096 | 222.4K | 89.8M | 89.9M | 77.9% | 3 |
| dwarfs-l7-S24 | 1024 | 809.4K | 81.1M | 81.7M | 75.8% | 13 |
| dwarfs-l7-S24 | 2048 | 404.8K | 81.4M | 81.8M | 76.1% | 5 |
| dwarfs-l7-S24 | 4096 | 202.5K | 81.7M | 81.9M | 76.6% | 3 |
| dwarfs-lzma3-S20 | 1024 | 886.8K | 89.3M | 90.0M | 77.6% | 1 |
| dwarfs-lzma3-S20 | 2048 | 443.5K | 89.3M | 89.7M | 77.5% | 1 |
| dwarfs-lzma3-S20 | 4096 | 221.9K | 89.4M | 89.5M | 77.8% | 1 |
| dwarfs-lzma5-S20 | 1024 | 834.8K | 83.1M | 83.8M | 82.2% | 1 |
| dwarfs-lzma5-S20 | 2048 | 417.5K | 83.2M | 83.5M | 82.1% | 1 |
| dwarfs-lzma5-S20 | 4096 | 208.9K | 83.2M | 83.4M | 82.4% | 1 |
| dwarfs-lzma7-S20 | 1024 | 831.6K | 82.8M | 83.4M | 82.1% | 1 |
| dwarfs-lzma7-S20 | 2048 | 415.9K | 82.8M | 83.1M | 82.0% | 1 |
| dwarfs-lzma7-S20 | 4096 | 208.1K | 82.9M | 83.0M | 82.4% | 1 |
| dwarfs-user-S26-B6-hot | 1024 | 999.8K | 139.2M | 140.1M | 83.4% | 19 |
| dwarfs-user-S26-B6-hot | 2048 | 500.0K | 139.9M | 140.4M | 87.7% | 6 |
| dwarfs-user-S26-B6-hot | 4096 | 250.1K | 140.3M | 140.5M | 91.2% | 2 |
| dwarfs-user-S26-B6-hot-c256M | 1024 | 999.8K | 139.2M | 140.1M | 83.4% | 19 |
| dwarfs-user-S26-B6-hot-c256M | 2048 | 500.0K | 139.9M | 140.4M | 87.7% | 6 |
| dwarfs-user-S26-B6-hot-c256M | 4096 | 250.1K | 140.3M | 140.5M | 91.2% | 2 |
| dwarfs-user-S26-B6-hot-c64M | 1024 | 999.8K | 139.2M | 140.1M | 83.4% | 19 |
| dwarfs-user-S26-B6-hot-c64M | 2048 | 500.0K | 139.9M | 140.4M | 87.7% | 6 |
| dwarfs-user-S26-B6-hot-c64M | 4096 | 250.1K | 140.3M | 140.5M | 91.2% | 2 |
| dwarfs-user-S26-B6-hot-fullrt | 1024 | 1.2M | 159.3M | 160.5M | 77.7% | 46 |
| dwarfs-user-S26-B6-hot-fullrt | 2048 | 634.9K | 163.8M | 164.5M | 80.1% | 24 |
| dwarfs-user-S26-B6-hot-fullrt | 4096 | 317.6K | 165.1M | 165.5M | 82.2% | 16 |
| dwarfs-user-S26-B6-plain | 1024 | 785.5K | 82.9M | 83.5M | 84.7% | 12 |
| dwarfs-user-S26-B6-plain | 2048 | 392.9K | 83.3M | 83.6M | 86.7% | 4 |
| dwarfs-user-S26-B6-plain | 4096 | 196.6K | 83.6M | 83.7M | 88.4% | 2 |
| dwarfs-user-S26-B6-plain-c256M | 1024 | 999.8K | 139.2M | 140.1M | 83.4% | 19 |
| dwarfs-user-S26-B6-plain-c256M | 2048 | 500.0K | 139.9M | 140.4M | 85.7% | 6 |
| dwarfs-user-S26-B6-plain-c256M | 4096 | 250.1K | 140.3M | 140.5M | 85.6% | 2 |
| dwarfs-user-S26-B6-plain-c64M | 1024 | 785.5K | 82.9M | 83.5M | 84.7% | 12 |
| dwarfs-user-S26-B6-plain-c64M | 2048 | 392.9K | 83.3M | 83.6M | 86.7% | 4 |
| dwarfs-user-S26-B6-plain-c64M | 4096 | 196.6K | 83.6M | 83.7M | 88.4% | 2 |
| dwarfs-zstd12-S20 | 1024 | 1.3M | 179.8M | 181.1M | 75.9% | 18 |
| dwarfs-zstd12-S20 | 2048 | 649.5K | 180.4M | 181.0M | 75.9% | 11 |
| dwarfs-zstd12-S20 | 4096 | 324.9K | 181.0M | 181.4M | 76.0% | 6 |
| dwarfs-zstd17-S20 | 1024 | 937.0K | 93.7M | 94.5M | 80.2% | 16 |
| dwarfs-zstd17-S20 | 2048 | 468.6K | 94.3M | 94.6M | 80.4% | 8 |
| dwarfs-zstd17-S20 | 4096 | 234.4K | 94.8M | 95.0M | 80.9% | 3 |
| dwarfs-zstd4-S20 | 1024 | 1.0M | 106.2M | 107.0M | 76.8% | 22 |
| dwarfs-zstd4-S20 | 2048 | 532.1K | 106.9M | 107.3M | 77.4% | 12 |
| dwarfs-zstd4-S20 | 4096 | 266.1K | 107.9M | 108.1M | 79.0% | 1 |
| dwarfs-zstd7-S20 | 1024 | 1005.5K | 100.0M | 100.8M | 79.0% | 10 |
| dwarfs-zstd7-S20 | 2048 | 502.8K | 100.3M | 100.7M | 79.6% | 6 |
| dwarfs-zstd7-S20 | 4096 | 251.5K | 100.6M | 100.8M | 79.9% | 4 |
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

No-change rebuild: 475 variants tested; 0 downloaded >1% (non-determinism / unstable layout): none

Ideal references on raw AppDir tars: kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, kdenlive/patch xdelta3=92.3M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, keepassxc/patch xdelta3=2.1M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, krita/patch xdelta3=38.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, libreoffice/patch xdelta3=33.1M, libreoffice/major xdelta3=93.2M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, neovim/patch xdelta3=1.5M, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K, obsidian/patch xdelta3=69.5K

## Table 3b - Compression block x zsync block (update cost, % of image; best per row bold)

**dwarfs preset level 3**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **56.2** | 60.3 | 65.1 |
| 1.0M | **33.8** | 35.0 | 36.5 |
| 16.0M | **29.2** | 29.8 | 30.6 |

**dwarfs preset level 5**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **73.8** | 74.5 | 76.8 |
| 1.0M | **82.6** | 82.8 | 83.1 |
| 16.0M | **79.8** | 80.2 | 80.7 |

**dwarfs preset level 7**

| compression block \ zsync -b | 1.0K | 2.0K | 4.0K |
|---|---|---|---|
| 64.0K | **78.7** | 79.2 | 80.0 |
| 1.0M | **77.2** | 77.4 | 77.9 |
| 16.0M | **75.8** | 76.1 | 76.6 |

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
| 1.0M | 0.356 | 28 | 262 | 346 | 75.9 |

## Table 7 - DwarFS: runtime vs native mount, and cache size

`mount (runtime)` is `--appimage-mount`, which for DwarFS includes whatever the runtime does before mounting (the full uruntime unpacks a bundled mkdwarfs; the lite one does not). `mount (native)` is the `dwarfs` binary on the same image with no runtime. The uruntime sizes the DwarFS block cache from free host memory (`auto`); the `-cNNN` variants fix it with `DWARFS_CACHESIZE`, so RAM is comparable. Medians over apps; app start is relative to the baseline.

| variant | runtime size | cache | mount (runtime) ms | mount (native) ms | FUSE RSS MB (runtime) | FUSE RSS MB (native) | app start (cold) | apps |
|---|---|---|---|---|---|---|---|---|
| dwarfs-l3-S24 | 1.4M | auto (1536M) | 23.5 | 47.0 | 22 | 609 | 0.64 | 6 |
| dwarfs-l7-S24 | 1.4M | auto (1536M) | 24.1 | 57.2 | 495 | 595 | 0.57 | 6 |
| dwarfs-user-S26-B6-hot | 1.4M | auto (1536M) | 26.7 | 56.9 | 624 | 615 | 0.56 | 6 |
| dwarfs-user-S26-B6-hot-c256M | 1.4M | 256M | 22.6 | 47.5 | 540 | 407 | 3.11 | 6 |
| dwarfs-user-S26-B6-hot-c64M | 1.4M | 64M | 20.3 | 48.8 | 337 | 284 | 17.44 | 6 |
| dwarfs-user-S26-B6-hot-fullrt | 2.9M | auto (1536M) | 40.4 | 56.0 | 681 | 637 | 0.44 | 5 |
| dwarfs-user-S26-B6-plain | 1.4M | auto (1536M) | 18.7 | 48.4 | 476 | 471 | 0.51 | 6 |
| dwarfs-user-S26-B6-plain-c256M | 1.4M | 256M | 24.4 | 58.2 | 542 | 407 | 1.40 | 5 |
| dwarfs-user-S26-B6-plain-c64M | 1.4M | 64M | 18.7 | 46.6 | 319 | 246 | 7.30 | 6 |
| dwarfs-zstd7-S20 | 1.4M | auto (1536M) | 22.9 | 45.6 | 525 | 609 | 0.52 | 6 |
| squashfs-gzip9-b128K | 922.5K |  | 7.2 |  | 31 |  | 1.00 | 6 |
| squashfs-zstd7-b128K | 922.5K |  | 8.5 |  | 31 |  | 0.73 | 6 |

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

Best by weighted score: `dwarfs-l3-S24` (0.755, coverage 100%), `squashfs-zstd9-b64K` (0.758, coverage 100%), `squashfs-zstd7-b32K+nofrag` (0.773, coverage 100%)

1. among the 10 best weighted scores, keep variants within +5% of the best update cost and +10% of the best warm startup CPU (when those metrics exist);
2. choose the smallest total size (sizes within 1.5% tie; the tie goes to the best weighted score);
3. reject non-deterministic builds, zsync verification failures, reference-only variants.

**Winner: `dwarfs-l3-S24`**; candidates: dwarfs-l3-S24

- smallest: `dwarfs-user-S26-B6-hot` (0.73), runner-up `dwarfs-user-S26-B6-hot-c256M`
- fastest startup (CPU): `dwarfs-l3-S24` (0.07), runner-up `dwarfs-brotli3-S20`
- cheapest update: `dwarfs-l2-S24` (0.72), runner-up `dwarfs-l3-S24`
- fastest build: `squashfs-zstd3-b32K` (0.07), runner-up `dwarfs-l1-S24`

## Edge extension (levers whose best value is at the edge of the tested range)

- `dwarfs-lzma8-S20`: dw-lzma: best level=7 is the high edge of the tested values -> try level=8
- `squashfs-xz-bcjnone-b16K`: sq-xz: best block=32K is the low edge of the tested values -> try block=16K

Shortlist for the next stage: `dwarfs-l3-S24`, `squashfs-zstd9-b64K`, `squashfs-zstd5-b256K`, `squashfs-zstd12-b256K`, `squashfs-zstd17-b256K`, `squashfs-lzo-b128K`, `dwarfs-brotli6-S20`, `dwarfs-brotli3-S20`, `dwarfs-brotli8-S20`, `dwarfs-l2-S24`, `squashfs-xz-bcjauto-b128K`, `squashfs-gzip9-b128K`

Noisy (CV>10%) variants queued for retry: 334

<!-- RESULTS:END -->
