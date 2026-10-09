import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).parent))
import common, plan_matrix, aggregate  # noqa
from make_fake_results import make


class Variants(unittest.TestCase):
    def test_unique_ids(self):
        ids = [v["id"] for v in common.expand_variants()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_levers_have_at_most_three_points(self):
        """Continuous levers: low/mid/high only."""
        tbl = common.load_variant_table()
        for fam in tbl["families"]:
            for lever in fam.get("levers", []):
                vals = fam["params"][lever]
                self.assertLessEqual(len(vals), 3, f"{fam['name']}.{lever}")
        self.assertEqual(len(tbl["zsync_blocks"]), 3)

    def test_canary_exists(self):
        common.get_variant(common.load_variant_table()["canary"])

    def test_option_variant(self):
        v = common.get_variant("squashfs-zstd12-b128K+nofrag")
        self.assertEqual(v["option"], "nofrag")

    def test_build_command_flags(self):
        import build_variant as bv
        v = common.get_variant("squashfs-zstd12-b128K")
        cmd = bv.payload_cmd(v, "a", "p", "x86_64", 0, "/tmp")
        self.assertIn("-Xcompression-level", cmd)
        self.assertEqual(cmd[cmd.index("-b") + 1], str(128 * 1024))
        d = bv.payload_cmd(common.get_variant("dwarfs-l7-S24"), "a", "p", "x86_64", 0, "/tmp")
        self.assertEqual(d[d.index("-S") + 1], "24")


class DwarfsUser(unittest.TestCase):
    def test_owner_configuration(self):
        import build_variant as bv
        plain = bv.payload_cmd(common.get_variant("dwarfs-user-S26-B6-plain"), "a", "p", "x86_64", 0, "/tmp")
        for flag in ("--no-history", "--no-create-timestamp"):
            self.assertIn(flag, plain)
        self.assertEqual(plain[plain.index("-S") + 1], "26")
        self.assertEqual(plain[plain.index("-B") + 1], "6")
        self.assertEqual(plain[plain.index("--order") + 1], "path")
        self.assertEqual(plain[plain.index("-C") + 1], "zstd:level=22")
        self.assertEqual(plain[plain.index("--set-owner") + 1], "0")
        self.assertEqual(plain[plain.index("--set-group") + 1], "0")
        self.assertNotIn("-l", plain)                 # mkdwarfs default preset
        self.assertNotIn("--hotness-list", plain)
        hot = bv.payload_cmd(common.get_variant("dwarfs-user-S26-B6-hot"), "a", "p", "x86_64", 0, "/tmp", "hot.txt")
        self.assertEqual(hot[hot.index("--hotness-list") + 1], "hot.txt")
        with self.assertRaises(ValueError):
            bv.payload_cmd(common.get_variant("dwarfs-user-S26-B6-hot"), "a", "p", "x86_64", 0, "/tmp")


class DwarfsRuntimeAndCache(unittest.TestCase):
    """Issues #1 and #2: lite runtime by default, explicit cache sizes, native mount reference."""

    def test_runtime_choice(self):
        rts = common.load_yaml("corpus/runtimes.yml")
        self.assertIn("lite", rts["dwarfs"]["urls"]["x86_64"])          # default DwarFS runtime is the lite one
        self.assertNotIn("lite", rts["dwarfs_full"]["urls"]["x86_64"])
        self.assertEqual(common.get_variant("dwarfs-user-S26-B6-hot-fullrt")["params"]["runtime"], "full")
        self.assertIsNone(common.get_variant("dwarfs-user-S26-B6-hot")["params"].get("runtime"))

    def test_cache_variants(self):
        for h in ("plain", "hot"):
            for c in ("256M", "64M"):
                v = common.get_variant(f"dwarfs-user-S26-B6-{h}-c{c}")
                self.assertEqual(v["params"]["cache"], c)
        import build_variant as bv
        cmd = bv.payload_cmd(common.get_variant("dwarfs-user-S26-B6-plain-c64M"), "a", "p", "x86_64", 0, "/tmp")
        self.assertNotIn("cache", " ".join(cmd))        # the cache is a mount-time setting, not a build option

    def test_small_block_cache_variants(self):
        for c in ("256M", "128M", "64M"):
            v = common.get_variant(f"dwarfs-l5-S20-c{c}")
            self.assertEqual((v["params"]["bits"], v["params"]["cache"]), (20, c))

    def test_auto_cache_tiers(self):
        c = common.uruntime_auto_cache_mb()
        self.assertIn(c, (1536, 1024, 896, 768, 640, 512, 384, 256, 128, 64, 32))


class AggregateOrder(unittest.TestCase):
    def test_newer_run_wins_regardless_of_path_order(self):
        import json, tempfile, aggregate
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        def rec(run, rt, mount):
            return {"app": "a", "arch": "x86_64", "variant": "v", "run_id": str(run), "retry": False,
                    "size": {"runtime": rt, "total": rt + 1}, "summary": {"mount_ms_warm": mount}}
        (d / "aaa.json").write_text(json.dumps({"app": "a", "records": [rec(200, 1000, 5.0)]}))   # sorts first, newer
        (d / "zzz.json").write_text(json.dumps({"app": "a", "records": [rec(100, 3000, 30.0)]}))  # sorts last, older
        recs, _, _ = aggregate.load([d / "aaa.json", d / "zzz.json"])
        self.assertEqual(recs[0]["size"]["runtime"], 1000)
        self.assertEqual(recs[0]["summary"]["mount_ms_warm"], 5.0)
        self.assertEqual(recs[0]["run_id"], "200")


class Corpus(unittest.TestCase):
    def test_every_app_has_an_older_version(self):
        for a in common.load_corpus():
            if a.get("synthetic"):
                continue
            self.assertTrue(a.get("pairs"), f"{a['name']} has no older release for delta tests")
            for p in a["pairs"]:
                self.assertNotEqual(p["old_url"], a["url"])


class Weights(unittest.TestCase):
    def test_sum_and_order(self):
        w = {k: v["weight"] for k, v in common.load_yaml("variants/weights.yml")["weights"].items()}
        self.assertEqual(sum(w.values()), 100)
        launch = w["launch_ms_cold"] + w["cpu_s_startup_warm"]
        self.assertEqual(max(w, key=w.get), "size_total")                 # size most important
        self.assertGreater(launch, w["update_cost"])                      # launch second
        self.assertEqual(min(w, key=w.get), "build_wall_s")               # build time least


class Plan(unittest.TestCase):
    def test_groups_small_and_cover_all(self):
        vs = plan_matrix.select_variants("1", None, "")
        groups, _ = plan_matrix.pack(vs, 450, {"no_startup": True, "no_zsync": True}, "squashfs-gzip9-b128K")
        self.assertTrue(all(len(g) <= plan_matrix.MAX_PER_GROUP for g in groups))
        self.assertEqual(sorted(v["id"] for g in groups for v in g), sorted(v["id"] for v in vs))
        self.assertGreater(len(groups), 5)       # parallelism


class Aggregate(unittest.TestCase):
    def test_report(self):
        with tempfile.TemporaryDirectory() as d:
            make(d + "/raw")
            sys.argv = ["aggregate", "--raw", d + "/raw", "--out", d + "/out"]
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                aggregate.main()
            self.assertTrue((Path(d) / "out/report.md").exists())
            sl = json.loads((Path(d) / "out/shortlist.json").read_text())
            self.assertIn("squashfs-gzip9-b128K", sl["variants"])


class EdgeExtension(unittest.TestCase):
    def test_extends_at_edge(self):
        # zstd level best at the low edge (7), block best at the high edge (256K)
        scores = {}
        for lvl, lscore in ((7, 0.80), (12, 0.90), (17, 0.95)):
            for blk, bscore in (("32K", 0.10), ("128K", 0.02), ("256K", 0.0)):
                scores[f"squashfs-zstd{lvl}-b{blk}"] = lscore + bscore
        ext = aggregate.propose_extensions(scores)
        self.assertIn("squashfs-zstd4-b256K", ext)       # halfway between 7 and the min 1
        self.assertIn("squashfs-zstd7-b512K", ext)       # halfway (log2) between 256K and 1M
        self.assertEqual(len(ext), 2)

    def test_interior_winner_stops(self):
        scores = {f"squashfs-zstd{l}-b128K": sc for l, sc in ((7, 0.9), (12, 0.8), (17, 0.9))}
        scores.update({f"squashfs-zstd12-b{b}": sc for b, sc in (("32K", 0.9), ("256K", 0.95))})
        self.assertEqual(aggregate.propose_extensions(scores), {})

    def test_reaches_extreme_when_adjacent(self):
        self.assertEqual(aggregate._next_t(2, 1, -1), 1)
        self.assertEqual(aggregate._next_t(7, 1, -1), 4)
        self.assertEqual(aggregate._next_t(1, 1, -1), None)
        self.assertEqual(aggregate._next_t(19, 20, 1), 20)

    def test_dynamic_id_matching_other_family_template(self):
        # 'dwarfs-lzma2-S20' also fits the pattern of dwarfs-l{level}-S{bits}; must resolve
        # to the lzma family (level 2) instead of crashing
        v = common.get_variant("dwarfs-lzma2-S20")
        self.assertEqual(v["params"]["codec"], "lzma")
        self.assertEqual(v["params"]["level"], 2)
        with self.assertRaises(KeyError):
            common.get_variant("dwarfs-zma2-S20")

    def test_dynamic_ids_validated(self):
        self.assertEqual(common.get_variant("squashfs-zstd4-b512K")["block_bytes"], 512 * 1024)
        for bad in ("squashfs-zstd23-b128K", "squashfs-zstd7-b2K", "squashfs-zstd7-b2M", "squashfs-zstd7-b100K"):
            with self.assertRaises(KeyError):
                common.get_variant(bad)


if __name__ == "__main__":
    unittest.main()
