# AppImage Compression Benchmark - Test Design

Goal: answer AppImageSpec issue #44 with data. Find the best default
container/compression/block-size combination for AppImages, judged by
(1) size, (2) startup time, (3) zsync delta-update efficiency.

Everything runs unattended on GitHub Actions, fanned out as a matrix, with
results aggregated into summary tables.

---------------------------------------------------------------------------
## 1. Questions the benchmark must answer

1. Size: what is the smallest payload per format/compressor/block size?
2. Startup: how long until the app is usable (cold cache and warm cache)?
3. Update cost: how many bytes does `zsync`/AppImageUpdate download when going
   from release N-1 to N? (Compression often destroys delta efficiency.)
4. Cost to the producer: compress time and peak RAM (CI builders matter).
5. Cost to the consumer: decompress CPU, RAM, runtime/kernel requirements.
6. Is there a single default that is within X% of the best on all three axes?

---------------------------------------------------------------------------
## 2. Corpus ("typical real-world AppImages")

Pinned in `corpus/corpus.yml`. Every entry: name, category, arch, URL of
release asset, sha256, plus an "older" entry for zsync pairs. Never download
"latest" - pin the URL and hash so runs are reproducible.

Selection (aim ~14 apps, spanning toolkit and size):

| Class            | Candidates (examples)                                  | Size  |
|------------------|--------------------------------------------------------|-------|
| Tiny CLI         | btop, fastfetch, neovim, ripgrep-style static tools    | <10MB |
| Small GTK/Qt     | qBittorrent, KeePassXC, Audacity, Cura                 | 30-150MB |
| Electron         | Obsidian, Bitwarden, Joplin, Signal-like               | 100-250MB |
| Large Qt/KDE     | Krita, Kdenlive, OBS Studio                            | 200-500MB |
| Huge             | LibreOffice, Blender, Inkscape (with Python), Godot    | 300MB-1GB+ |
| Interpreted/data | App with big Python/Qt-QML tree, app with many assets  | varies |

Rules:
- Take only AppImages whose payload is squashfs (the current format) so the
  AppDir can be recovered with `--appimage-extract`.
- Include at least one app with many small files, one with few huge
  binaries, one with pre-compressed assets (PNG/JPG/ogg/zip; expected to
  compress badly), one with debug symbols.
- Per app, define a `launch` block (see section 6) and an optional `pairs`
  list for zsync: patch release (N-1 -> N), minor release, and major release
  where available. Without a version pair no zsync test is run for that app.
- Both `x86_64` and `aarch64` (GitHub public repos have free
  `ubuntu-24.04-arm` runners). Run aarch64 on a reduced app subset.

Corpus job:
1. Download, verify sha256, run `--appimage-extract` into an AppDir.
2. Store the AppDir as an uncompressed tar in the Actions cache keyed by
   sha256 (so the matrix jobs do not re-download and re-extract).
3. Emit `corpus-stats.json`: file count, size, file-type histogram (ELF, shared
   libs, images, text, already-compressed), top-20 largest files.

---------------------------------------------------------------------------
## 3. Variants under test (`variants.yml`)

A variant = container + compressor + level + block size + options. All
variants are rebuilt from the same extracted AppDir with the same runtime
type so only the payload differs.

Reproducibility for all builders: sorted file list, `-all-root`, all mtimes
clamped to a fixed epoch, fixed uid/gid, fixed processor count. Verify by
building twice and comparing sha256 (a "determinism" check in the job).

### 3.1 SquashFS (current baseline; `mksquashfs` 4.6+)

| Compressor | Levels                 | Block sizes                 | Extra                 |
|------------|------------------------|-----------------------------|-----------------------|
| gzip       | 6, 9 (default 9)       | 128K (current default)      |                       |
| gzip       | 9                      | 4K, 16K, 64K, 256K, 1M      |                       |
| xz         | default                | 128K, 256K, 512K, 1M        | `-Xbcj x86` on/off    |
| lz4        | hc                     | 128K                        |                       |
| lzo        | default                | 128K                        | reference only        |
| zstd       | 1, 3, 10, 15, 19, 22   | 16K, 64K, 128K, 256K, 512K, 1M | `-Xcompression-level` |

Option axes to test on the best 2-3 compressors only (to limit the matrix):
- `-no-fragments` and `-no-tailends` (fragments pack file tails together and
  hurt zsync locality)
- `-no-duplicates` on/off
- file ordering: default vs `-sort` (group by type / by path)

### 3.2 DwarFS (`mkdwarfs` current release)

| Axis            | Values                                                        |
|-----------------|---------------------------------------------------------------|
| preset `-l`     | 1, 3, 5, 7, 9 (maps to codec + ordering presets)              |
| block bits `-S` | 16 (64K), 18 (256K), 20 (1M), 22 (4M), 24 (16M, default), 26 (64M) |
| codec `-C`      | zstd:level=3, zstd:level=19, lzma:level=9, brotli:quality=11  |
| ordering        | `--order=none`, `path`, `similarity`, `nilsimsa` (default at high -l) |
| categorizers    | default off vs `--categorize` (per-category codecs, e.g. ricepp/flac) |

Reader options (via the runtime, test defaults and one tuned set):
`cachesize`, `workers`, `readahead`, `preload_category`.

Important: DwarFS dedups segments across files and orders by similarity,
which gives great ratios but is expected to be hostile to zsync unless
ordering is stable. Test `--order=path|none` specifically for zsync.

### 3.3 EROFS (optional, `mkfs.erofs` 1.8+)

lz4hc, lzma, zstd (if built in), with `-C` cluster sizes 16K/64K/1M and
`-E fragments,dedupe`. Kernel mount needs root; user-space `erofsfuse` is
what an AppImage runtime could use. Mark as "informational" unless a
runtime exists that supports it.

### 3.4 Codec-only reference (not an AppImage format)

On `tar --sort=name --mtime=@0` of the AppDir, compress with: zstd (levels
3/19/22, `--long=27`, `--rsyncable`), xz -9e, brotli -q 11, bzip3, and
OpenZL (or whichever "successor" codec is being evaluated). Gives a lower
bound on size and shows whether a codec is worth wrapping in a container at
all. These rows have no startup metric (no random access) and are labelled
as such in tables.

### 3.4b OpenZL and other "successor" candidates

OpenZL (Meta, BSD, format-aware: you describe the data, it builds a
specialised compressor graph; payloads from release-tagged versions stay
decodable). Open questions this benchmark must settle, because I could not
confirm them from the project page: does it have a useful generic mode for
mixed data like an AppDir, are there ready profiles for ELF/text/images,
and does it support independent blocks (random access)? Plan assumes
"unknown" and tests in four steps, stopping early if a step shows no gain:

1. Codec-only, whole tar stream (section 3.4): OpenZL generic vs zstd-19 vs
   xz. If it does not beat zstd by a clear margin here, stop.
2. Per file class: split the AppDir into classes (ELF/.so, text/scripts/
   QML/JSON, already-compressed media, other). Compress each class
   separately with zstd-19, xz, and OpenZL (generic, plus any shipped
   profile that fits). Shows where OpenZL could help (e.g. ELF sections,
   structured tables) and what share of the AppDir that class is. Expected
   upper bound on the whole-image gain = sum over classes.
3. Train-and-test without leakage: train an OpenZL compressor (if training
   is supported) on the OLD release's AppDirs and evaluate on the NEW
   release of the same app, and also train on app set A, test on app set B.
   An app-specific trained compressor is only useful if it generalises or
   if the AppImage could ship the profile in the runtime.
4. Block-independent mode (needed for a filesystem, since AppImages use
   random access): compress the AppDir in independent chunks of 64K, 256K,
   1M with OpenZL and measure (a) ratio loss versus whole-stream,
   (b) decode time of the recorded startup working set (4.3 B) from a
   simple chunk-index container built by `tools/blockstore.py` (a minimal
   harness: index + independent frames, read via a Python/C reader, no
   FUSE). This gives a startup number and a ratio for OpenZL without a
   filesystem integration. Same harness runs zstd (with and without a
   trained dictionary) so the comparison is fair.

Verdict categories for OpenZL in the report: "no gain", "gain only on
class X (n% of AppDir)", "gain, needs shipped profile", "gain, ready for a
filesystem prototype". Do not conclude more than the data supports; if
OpenZL has no random-access mode today, say that the result is a ceiling
estimate, not a deployable AppImage format.

Other candidates for the same harness (cheap to add as blockstore/codec
entries or squashfs/dwarfs options):
- zstd with a trained dictionary per app (helps small blocks a lot),
  `--long` / LDM window sizes, and zstd advanced params (`--ultra -22`,
  strategy btultra2, `--zstd=wlog=..`)
- ELF preprocessing filters: BCJ x86 / ARM64 / RISCV before zstd or lzma
  (squashfs xz has `-Xbcj`; for zstd, test filter + zstd in the blockstore
  harness); possibly a split "text vs code" stream
- brotli quality 11 with large window (`--large_window`)
- lzma/xz with `lc/lp/pb` tuned for x86 code (e.g. lc=0 lp=2 pb=2 for
  64-bit ELF) via mkdwarfs `-C lzma:...`
- lz4 / lz4hc as the speed-first floor (fastest startup, worst ratio)
- bzip3 / zpaq-class as ratio ceiling, codec-only, no startup claim
- DwarFS categorizers (ricepp for images, flac for audio) for media-heavy
  apps
- EROFS with zstd and lzma (section 3.3), as a different container with
  similar codecs

All of these are entries in `variants/candidates.yml` with fields
`{id, kind: squashfs|dwarfs|erofs|blockstore|codec-only, params}` so a new
codec costs one YAML entry plus, for blockstore, a small codec adapter.

### 3.5 Runtimes

- SquashFS variants: AppImage `type2-runtime` (pinned release).
- DwarFS variants: a DwarFS-capable runtime (e.g. the `uruntime`
  project that bundles dwarfs; pin commit). If unavailable, build the
  runtime in the corpus job from source and record its sha.
- Report `runtime_bytes` separately, and both `payload_bytes` and
  `total_bytes`. Runtime size is constant per runtime and matters for tiny
  apps (a 2MB DwarFS runtime vs a 0.5MB squashfs runtime).

Matrix size estimate: ~14 apps x ~60 variants x 2 arch is too big to run
fully; use staged pruning (section 8) so the full grid runs on 4 reference
apps and the pruned set on the rest.

---------------------------------------------------------------------------
## 4. Metrics and how each is measured

### 4.1 Size
- `payload_bytes` (fs image), `runtime_bytes`, `total_bytes`
- `ratio = total / uncompressed AppDir bytes`
- also size of the `.zsync` control file (counts toward update cost)

### 4.2 Build cost
- wall time, user+sys CPU time, peak RSS (`/usr/bin/time -v`), fixed
  `-processors`/`-N` so runs are comparable.
- hard timeout per build (e.g. 40 min); a timeout is recorded as a result,
  not a failure.

### 4.3 Startup (the hard one on shared CI runners)
Three layers, from most stable to most realistic:

A. **Mount time**: `./app.AppImage --appimage-mount` until the mountpoint
   answers `stat`. Isolates runtime + FS metadata overhead.
B. **Read workloads on the mounted tree** (no app logic, so no GPU/DBus
   flakiness):
   - sequential read of all files (throughput, MB/s of uncompressed data)
   - read of the "startup working set": the list of files and offsets the
     real app touches during launch, recorded once per app (see section 6)
     and replayed in recorded order
   - random 4K reads of N sampled files (latency)
C. **Real launch**: run the app under `xvfb-run` (software GL, `QT_QPA_PLATFORM`
   etc. pinned) and measure time to "ready": window mapped (poll
   `xdotool search --onlyvisible`), or a stdout marker, or for CLI apps the
   exit of `--version`. Apps that cannot launch headless are marked
   `launch: cli-only` and only get A and B.

Each of A/B/C is measured in two cache states:
- **warm**: page cache populated (run once, discard, then measure)
- **cold**: `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches` before each
  run (works on GitHub-hosted runners with sudo). Note GitHub disks are
  network-backed so cold numbers include I/O; therefore also run
  **tmpfs mode**: copy the AppImage into `/dev/shm` first, which removes disk
  noise and leaves pure decompression CPU. Report all three: cold-disk,
  warm, cold-tmpfs.

Also record decompression CPU (user+sys of the runtime/FUSE process via
`/proc/<pid>/stat` deltas) and peak RSS of the FUSE process. CPU time is far
less noisy than wall time on shared runners and is the primary startup
metric for ranking; wall time is the user-facing one.

Constrain parallelism to what a modest user machine has: run reads under
`taskset -c 0,1` (2 cores) as the headline, with an unconstrained run as
secondary.

### 4.4 zsync efficiency
Per (app, pair, variant):
1. Build `new.AppImage` and `old.AppImage` with the SAME variant from the two
   AppDirs.
2. `zsyncmake2 -b <blocksize> -u <url> new.AppImage` for blocksize in
   {512, 1024, 2048 (default), 4096, 8192, 16384, 32768, 65536}.
   No zsync block size is assumed best: the optimum depends on the
   compression block size (a zsync block larger than the compressed block
   can never match when one compressed block changes; a much smaller one
   inflates the .zsync file). So the sweep is a CROSS PRODUCT:
   compression block size x zsync block size, per variant family
   (see 4.4.1). The .zsync control file grows roughly linearly with
   file_size / zsync_block, so its cost is part of the result, not
   ignored.
3. Serve `new.AppImage` and `.zsync` with a range-capable static server
   (nginx or caddy on localhost, access log with bytes sent). Python's
   `http.server` does NOT do ranges; do not use it.
4. Run `zsync -i old.AppImage -o out.AppImage new.zsync`. Record
   - bytes downloaded (nginx log sum, cross-checked with zsync's own
     report),
   - `.zsync` size,
   - request count (each range request has latency cost),
   - verify `sha256(out) == sha256(new)`; mismatch = test failure.
5. `update_cost = zsync_file_bytes + downloaded_bytes`;
   `update_ratio = update_cost / new.total_bytes`.
6. Idealised references on the uncompressed AppDirs: `xdelta3`/`bsdiff` size
   of the tar diff, and a `casync`/`desync` chunk-store estimate. This shows
   the best any scheme could do, so we can tell whether zsync loss comes
   from the container or from the delta tool.

#### 4.4.1 Block-size grid (compression block x zsync block)
Run for the shortlisted compressors (e.g. squashfs zstd, squashfs xz,
squashfs gzip, dwarfs zstd/lzma):

| Compression block \ zsync -b | 512 | 1K | 2K | 4K | 8K | 16K | 32K | 64K |
|------------------------------|-----|----|----|----|----|-----|-----|-----|
| 4K / 16K / 64K / 128K / 256K / 512K / 1M (squashfs) | | | | | | | | |
| 64K / 256K / 1M / 4M / 16M / 64M (dwarfs -S 16..26)  | | | | | | | | |

Cell value = total update cost (zsync file + downloaded bytes) as % of the
new image, median over the pairs. Cheap to compute: the images are built
once per compression block size and the zsync sweep only re-runs
`zsyncmake2` + `zsync` (seconds to minutes), so the whole grid fits in the
same job. Output: heat map per compressor plus the argmin per row, and a
rule-of-thumb check (is best zsync -b ~ compression block / k?). Finer
non-power-of-2 values are not allowed by zsyncmake (must be a power of 2),
so the grid is complete at this resolution; if the argmin lands on the grid
edge (512 or 64K), extend the sweep one step further in that direction in
stage 3.

Also test the "no change" case (rebuild identical content, different build
timestamp) - good variants should give ~0 download, bad ones expose
non-determinism.

### 4.5 Compatibility flags (not measured, recorded)
Minimum kernel, FUSE version, needs `fusermount`, kernel module required,
static-linked reader, availability on aarch64. Presented as a table next to
the performance ones, because a faster default that does not run on an
older distro is not a valid default.

---------------------------------------------------------------------------
## 5. Statistics and noise control

- Interleave variants: round-robin each repetition (v1,v2,...,vN, v1,v2,...)
  rather than all runs of one variant back to back, so drift hits everyone.
- Warm: 7 runs, discard the first, report median and MAD. Cold: 5 runs.
- Include a canary variant (squashfs gzip-9 128K) in EVERY job. Report each
  metric also as a ratio to the canary measured in the same job. This
  normalises machine-to-machine differences between runners and is the
  number used in cross-job aggregation.
- Flag results with CV > 10% and automatically rerun the affected variant
  once on a fresh job (workflow `retry-noisy`).
- Record runner facts in every result: CPU model (`lscpu`), cores, RAM,
  kernel, image version, `RUNNER_NAME`.
- Size and zsync metrics are deterministic and need one run; timing metrics
  need the repetition.

---------------------------------------------------------------------------
## 6. Per-app launch spec (`corpus/<app>.yml`)

```yaml
name: krita
category: large-qt
arch: x86_64
url: https://.../krita-5.2.9-x86_64.appimage
sha256: ...
pairs:
  - kind: patch
    old_url: ...
    old_sha256: ...
launch:
  mode: gui            # gui | cli-only
  cmd: ["--nosplash"]  # args after the AppImage
  env: { QT_QPA_PLATFORM: xcb, LIBGL_ALWAYS_SOFTWARE: "1" }
  ready:
    type: window       # window | stdout | exit
    timeout_s: 60
  workset_seconds: 15  # how long to trace for the working set
```

Working-set recording (once per app, cached as an artifact): mount the
squashfs variant, run the app under `xvfb-run` with `fatrace`/`strace -f -e
trace=openat,read,execve` or fanotify, write ordered `(path, offset, len)`
list. Replay tool `replay-workset` (small C or Python program) reads exactly
that sequence from the mounted variant under test.

---------------------------------------------------------------------------
## 7. Repository layout

```
.
  README.md                 results summary (auto-updated), how to add things
  corpus/
    corpus.yml              list of apps
    <app>.yml               per-app spec (section 6)
  variants/
    variants.yml            all variants (container, params)
    candidates.yml          experimental codecs ("successor" slot)
  tools/
    fetch_corpus.py         download + verify + extract + cache
    build_variant.py        AppDir + variant -> .AppImage (+ determinism check)
    measure_size.py
    measure_startup.py      mount / read / launch, cold|warm|tmpfs
    record_workset.py
    replay_workset.c
    measure_zsync.py        nginx range server + zsync + byte accounting
    codec_reference.py      tar-stream codec runs (section 3.4)
    classify_appdir.py      split AppDir into ELF/text/media/other (3.4b)
    blockstore.py           chunk-index container + reader for codecs
                            without a filesystem (OpenZL, zstd+dict) (3.4b)
    train_openzl.py         old-release train / new-release test (3.4b)
    collect_env.sh          runner facts
    aggregate.py            JSON -> tables/plots/markdown
    plan_matrix.py          emits the dynamic job matrix
  results/                  (in a separate `results` branch, not main)
    raw/<run-id>/<app>/<arch>/<group>.json
  .github/workflows/
    prepare.yml             corpus + matrix planning
    bench.yml               matrix of measurement jobs
    aggregate.yml           tables, plots, publish
    retry-noisy.yml
    manual.yml              workflow_dispatch: one app, one variant filter
```

Result record (one JSON object per measurement, JSON Lines also fine):

```json
{
  "run_id": "...", "app": "krita", "arch": "x86_64",
  "variant": "squashfs-zstd19-b256K", "container": "squashfs",
  "codec": "zstd", "level": 19, "block_bytes": 262144,
  "runtime": "type2-runtime@<sha>",
  "size": {"payload": 0, "runtime": 0, "total": 0, "uncompressed": 0},
  "build": {"wall_s": 0, "cpu_s": 0, "rss_mb": 0, "deterministic": true},
  "startup": {"mount_ms": {"warm": [], "cold": [], "tmpfs": []},
              "workset_ms": {...}, "launch_ms": {...}, "seq_mb_s": {...},
              "cpu_s": {...}, "fuse_rss_mb": 0},
  "zsync": [{"pair": "patch", "zsync_block": 2048, "zsync_file": 0,
             "downloaded": 0, "requests": 0, "ok": true}],
  "env": {"cpu": "...", "cores": 4, "kernel": "...", "runner": "..."},
  "canary": {"mount_ms": 0, "build_wall_s": 0}
}
```

---------------------------------------------------------------------------
## 8. GitHub Actions design

### 8.1 Workflows

`prepare.yml` (runs on push, schedule, dispatch)
1. Checkout, `python tools/plan_matrix.py` produces JSON:
   `{include: [{app, arch, group, runner}, ...]}`.
2. `fetch_corpus` job per app (matrix) populates `actions/cache`
   (key = sha256 of AppImage + tool version) and uploads
   `corpus-stats.json`.

`bench.yml` (needs prepare)
```yaml
strategy:
  fail-fast: false          # one bad variant must not cancel the grid
  max-parallel: 20          # free plan concurrency cap for public repos
  matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}
runs-on: ${{ matrix.runner }}      # ubuntu-24.04 | ubuntu-24.04-arm
timeout-minutes: 350               # job limit is 360
```
Steps: install tools from pinned versions (squashfs-tools, mkdwarfs
release binary, erofs-utils, zsync, nginx, xvfb, xdotool, fatrace; cache
them), restore corpus cache, run `build_variant` + all measurers for the
variants in `matrix.group`, upload raw JSON as artifact
`raw-<app>-<arch>-<group>`.

`aggregate.yml` (needs bench; `if: always()`)
Download all `raw-*` artifacts, run `aggregate.py`, write tables to
`$GITHUB_STEP_SUMMARY`, update `README.md` between marker comments, push
plots (PNG/SVG) and the merged `results.jsonl` to the `results` branch or
GitHub Pages.

### 8.2 Splitting for parallelism
- One job = one (app, arch, group). A group is ~6-10 variants, always
  including the canary. Variants of one app are spread over groups by cost so
  groups finish at similar times (greedy bin packing on estimated time,
  `plan_matrix.py`).
- All variants that are being COMPARED for timing run in the same job on the
  same machine (so noise cancels). Cross-job comparison goes through the
  canary ratio only.
- Heavy variants (zstd -22 b1M, dwarfs -l9 -S26 lzma on 1GB) get their own
  group with a longer timeout.
- Matrix cap is 256 jobs per workflow; plan ~14 apps x ~5 groups x
  (x86_64 + a reduced arm64 set) ~ 100-130 jobs. With 20 concurrent and
  ~45 min average that is a ~5-6 hour wall-clock run; fine for a nightly or
  on-demand benchmark.
- Disk: runners have ~14 GB free on `/`; use `/mnt` (about 70 GB) for build
  output and delete finished variant images after measuring. For the
  huge apps, `jlumbroso/free-disk-space` or manual `rm` of preinstalled SDKs.

### 8.3 Staged pruning (keep cost sane)
Stage 1 (screen): 4 reference apps (one tiny, one Electron, one Qt, one huge)
x ALL variants, size + build only. Cheap.
Stage 2 (shortlist): keep Pareto-optimal variants plus the current default
(top ~12). Run full startup + zsync on the whole corpus.
Stage 3 (tune): zsync block-size sweep and DwarFS/squashfs option axes on the
top 3 shortlisted variants only.
Implement as three workflow invocations chained by `workflow_run` or by
one `plan_matrix.py --stage N` reading the prior stage's results.

### 8.4 Reproducing locally
`make bench APP=krita VARIANT=squashfs-zstd19-b256K` runs the same scripts
in a container (pinned Docker image identical to the one the workflows use,
so people can reproduce a single data point).

---------------------------------------------------------------------------
## 9. Result presentation (generated by `aggregate.py`)

All tables are Markdown, generated into README and the job summary. Each
table also shows the canary row.

Table 1 - Headline (geometric mean over corpus, relative to current default
squashfs gzip-9 128K = 1.00; lower is better):

| Variant | Total size | Mount (warm) | Startup CPU | Launch (cold) | Update cost (patch) | Build time | RAM (FUSE) |
|---------|-----------:|-------------:|------------:|--------------:|--------------------:|-----------:|-----------:|
| squashfs gzip-9 128K (baseline) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| squashfs zstd-19 256K | | | | | | | |
| squashfs xz 1M | | | | | | | |
| dwarfs -l7 -S22 | | | | | | | |
| dwarfs -l9 -S24 order=nilsimsa | | | | | | | |
| ... | | | | | | | |

Table 2 - Per app (one table per metric, apps as rows, top variants as
columns), e.g. total size in MB:

| App | Uncompressed | gzip-9 | zstd-19 | xz | dwarfs-l7 | Best |
|-----|-------------:|-------:|--------:|---:|----------:|------|

Table 3 - zsync: per variant and zsync block size, median across pairs:

| Variant | zsync -b | .zsync size | Downloaded | Total update | % of new image | Requests |
|---------|---------:|------------:|-----------:|-------------:|---------------:|---------:|

Table 3b - Block-size grid heat map (section 4.4.1): one per compressor,
rows = compression block size, columns = zsync block size, cell = update
cost % of image, best cell per row marked.

Table 4 - Block-size sweep for one compressor (shows the trade-off curve):

| Block | Size ratio | Mount ms | Workset ms | Seq MB/s | Update % |
|-------|-----------:|---------:|-----------:|---------:|---------:|
| 16K | | | | | |
| 64K | | | | | |
| 128K | | | | | |
| 256K | | | | | |
| 1M | | | | | |

Table 5 - Per-category geometric means (tiny CLI / Electron / Qt / huge) so
a default that wins only on big apps is visible.

Table 6 - Compatibility matrix (section 4.5) and the codec-only reference.

Plots (matplotlib, auto-generated PNG/SVG):
- Scatter: total size (x) vs startup CPU (y), one point per variant,
  Pareto frontier highlighted, current default circled.
- Scatter: total size vs update cost (zsync), same.
- Bar: build time per variant (log scale).
- Heat map: variant x app for size ratio.

Pareto and decision rule (automatic, printed in README):
1. Keep variants within +5% of the best update cost AND within +10% of the
   best warm startup CPU.
2. Among those, choose the smallest total size.
3. Reject any variant with deterministic=false, any zsync verification
   failure, or compatibility score below the agreed floor (e.g. must work
   with kernel >= 5.4 and FUSE 2.9 and have an aarch64 reader).
4. Report the winner AND the runner-up per axis, so the issue discussion
   can argue priorities (small download vs fast start vs cheap updates).

---------------------------------------------------------------------------
## 10. Known pitfalls to design around

- Wall-clock on shared runners is noisy: rely on interleaving, canary
  normalisation, CPU time and repetition (section 5).
- Cold cache numbers on CI are dominated by network disk; hence tmpfs mode.
- `drop_caches` needs root; use sudo, and do not run two timing jobs on one
  runner concurrently.
- zsync can only reuse blocks it finds at ANY offset in the old file, but
  compressed blocks only match if the compressed bytes are identical; a
  change early in a solid stream or in a dedup/similarity-ordered image
  shifts everything after it. This is the expected main finding for large
  blocks and for DwarFS similarity ordering; measure, do not assume.
- Squashfs fragment blocks and dedup make unrelated file changes ripple
  into unrelated blocks; covered by the `-no-fragments` axis.
- Non-reproducible builds poison zsync results: always enforce fixed mtimes,
  sorted inputs, fixed thread count (some compressors give different output
  with different thread counts; verify by double build).
- Apps that fail headless: use `cli-only` mode, do not drop the app.
- Range requests: use nginx/caddy, not `python -m http.server`.
- Pin everything (tool versions, runtime, corpus hashes) or results from
  different nights are not comparable. Record versions in each result.
- Licensing/redistribution: store only URLs and hashes, never the
  AppImages themselves in the repo; artifacts are short-lived.
- Do not over-claim: say "on GitHub-hosted runners (model, cores)"; real
  user hardware (HDD, slow ARM) shifts the startup conclusions toward
  higher ratio vs lower CPU cost. Offer a self-hosted-runner mode
  (`runs-on: [self-hosted, bench]`) for people to contribute data from
  real machines, with the same JSON schema.

---------------------------------------------------------------------------
## 11. Implementation order

1. `corpus.yml` + `fetch_corpus.py` + cache (3 apps).
2. `build_variant.py` for squashfs and dwarfs + determinism check.
3. `measure_size.py` + `aggregate.py` size table (first useful output).
4. `measure_zsync.py` with nginx range server and verification.
5. `measure_startup.py` (mount, read workloads, replay), then launch mode.
6. `plan_matrix.py` + `bench.yml` matrix; run stage 1 on 4 apps.
7. Pruning stages, full corpus, arm64 subset, plots, README automation.
8. Add the "successor" codec(s) as variants/candidates.yml entries.
9. Publish results and decision-rule output as a comment on
   AppImageSpec issue #44.
