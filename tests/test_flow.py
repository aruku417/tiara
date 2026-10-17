import json
import unittest

from tiara.check import check
from tiara.cli import main
from tiara.config import Config
from tiara.git import GitError
from tiara.sync import sync, verify

from helpers import Repo

CODE = "def a():\n    return 1\n"


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.cfg = Config(base="main")

    def tearDown(self):
        self.r.close()

    def github_clean(self) -> bool:
        return self.r.git.merge_tree("HEAD", "main").clean

    def test_no_conflict_means_nothing_to_do(self):
        self.r.setup({"a.py": CODE, "b.py": "x\n"}, {"b.py": "y\n"}, {"a.py": CODE + "# pr\n"})
        rep = check(self.r.git, self.cfg, fetch=False)
        self.assertEqual((rep.files, rep.exit_code), ([], 0))
        self.assertEqual(sync(self.r.git, self.cfg, fetch=False).status, "no-conflict")

    def test_both_appended_is_resolved_and_committed(self):
        self.r.setup({"a.py": CODE},
                     {"a.py": CODE + "\n\ndef b():\n    return 2\n"},
                     {"a.py": CODE + "\n\ndef c():\n    return 3\n"})
        rep = check(self.r.git, self.cfg, fetch=False)
        self.assertEqual([(f.path, f.status, f.strategy) for f in rep.files], [("a.py", "auto", "insert-both")])
        self.assertEqual(rep.exit_code, 1)
        res = sync(self.r.git, self.cfg, fetch=False)
        self.assertEqual((res.status, res.resolved), ("resolved", {"a.py": "insert-both"}))
        self.assertIn("def b", self.r.read("a.py"))
        self.assertIn("def c", self.r.read("a.py"))
        self.assertFalse(self.r.git.merging())
        self.assertTrue(self.r.git.is_ancestor("main", "HEAD"))
        self.assertTrue(self.github_clean())

    def test_same_line_goes_to_agent_then_verify(self):
        self.r.setup({"a.py": CODE, "n.json": '{"items": [{"id": 1}]}\n'},
                     {"a.py": CODE.replace("1", "2"), "n.json": '{"items": [{"id": 1}, {"id": 2}]}\n'},
                     {"a.py": CODE.replace("1", "3"), "n.json": '{"items": [{"id": 1}, {"id": 3}]}\n'})
        self.assertEqual({f.path: f.status for f in check(self.r.git, self.cfg, fetch=False).files},
                         {"a.py": "agent", "n.json": "auto"})
        res = sync(self.r.git, self.cfg, fetch=False)
        self.assertEqual(res.status, "needs-agent")
        self.assertEqual(res.resolved, {"n.json": "structured"})
        self.assertEqual(json.loads(self.r.read("n.json"))["items"], [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertIn("`a.py`", res.handoff)
        self.assertIn("both sides rewrote", res.handoff)
        self.assertIn("main change", res.handoff)          # what the base side did to the file
        self.assertFalse(verify(self.r.git).ok)            # markers still there
        self.r.write({"a.py": "def a():\n    return 2 + 3\n"})  # the agent's edit
        out = verify(self.r.git, commit=True)
        self.assertTrue(out.ok and out.committed, out.problems)
        self.assertTrue(self.github_clean())

    def test_verify_rejects_broken_json(self):
        self.r.setup({"c.json": '{"a": 1}\n'}, {"c.json": '{"a": 2}\n'}, {"c.json": '{"a": 3}\n'})
        self.assertEqual(sync(self.r.git, self.cfg, fetch=False).status, "needs-agent")
        self.r.write({"c.json": '{"a": 2,}\n'})
        self.assertIn("not valid JSON", verify(self.r.git).problems[0])
        self.r.write({"c.json": '{"a": 3}\n'})
        self.assertTrue(verify(self.r.git, cmd="git --version", commit=True).committed)

    def test_regenerate(self):
        cfg = Config(base="main", regenerate=["dist/*"])
        self.r.setup({"dist/app.js": "old\n"}, {"dist/app.js": "main\n"}, {"dist/app.js": "pr\n"})
        res = sync(self.r.git, cfg, fetch=False)
        self.assertEqual((res.status, res.regenerate), ("resolved", ["dist/app.js"]))
        self.assertEqual(self.r.read("dist/app.js"), "main\n")

    def test_refuses_dirty_tree(self):
        self.r.setup({"a.py": CODE}, {"a.py": CODE + "#m\n"}, {"a.py": CODE + "#p\n"})
        self.r.write({"a.py": "dirty\n"})
        with self.assertRaises(GitError):
            sync(self.r.git, self.cfg, fetch=False)

    def test_cli_exit_codes_and_config_file(self):
        self.r.setup({"a.py": CODE, ".tiara.json": '{"base": "main"}\n'},
                     {"a.py": CODE + "\n\ndef b():\n    pass\n"}, {"a.py": CODE + "\n\ndef c():\n    pass\n"})
        args = ["-C", str(self.r.root)]
        self.assertEqual(main(args + ["check", "--no-fetch"]), 1)
        self.assertEqual(main(args + ["sync", "--no-fetch"]), 0)
        self.assertEqual(main(args + ["check", "--no-fetch"]), 0)


if __name__ == "__main__":
    unittest.main()
