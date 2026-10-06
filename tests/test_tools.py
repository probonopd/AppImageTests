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
        launch = w["launch_ms_cold"] + w["cpu_s_startup_warm"] + w["mount_ms_warm"]
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

    def test_dynamic_ids_validated(self):
        self.assertEqual(common.get_variant("squashfs-zstd4-b512K")["block_bytes"], 512 * 1024)
        for bad in ("squashfs-zstd23-b128K", "squashfs-zstd7-b2K", "squashfs-zstd7-b2M", "squashfs-zstd7-b100K"):
            with self.assertRaises(KeyError):
                common.get_variant(bad)


if __name__ == "__main__":
    unittest.main()
