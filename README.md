# AppImage compression benchmark

Answers AppImageSpec issue #44 with data: which container / compressor / block
size is the best default for AppImages, judged by size, startup time and zsync
delta-update efficiency. Design: [docs/AppImageCompressionTests.md](docs/AppImageCompressionTests.md).
Everything runs on GitHub Actions.

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
| App launch speed: cold launch 16% + startup CPU 8% + mount 4% | 28% |
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
_No results yet. Run `Pin corpus`, then `Benchmark` (stage 1)._
<!-- RESULTS:END -->
