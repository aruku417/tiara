import unittest
from unittest import mock

from tiara import check as check_mod
from tiara.config import Config

from helpers import Repo


class OpenPrsTest(unittest.TestCase):
    """`check --prs`: if another open PR merges first, which of my files will conflict?"""

    def test_overlap_with_another_pr(self):
        r = Repo()
        try:
            r.commit({"a.py": "x = 1\n", "b.py": "y = 1\n"}, "base")
            r.branch("other")
            other = r.commit({"a.py": "x = 2\n"}, "other pr")
            r.run("update-ref", "refs/tiara/pr/7", other)           # what `git fetch pull/7/head` would give
            r.checkout("main")
            r.branch("mine")
            r.commit({"a.py": "x = 3\n", "b.py": "y = 2\n"}, "my pr")
            prs = [{"number": 7, "title": "other", "headRefName": "other", "headRefOid": other},
                   {"number": 8, "title": "me", "headRefName": "mine", "headRefOid": ""}]
            with mock.patch.object(check_mod, "_gh_open_prs", return_value=prs):
                rep = check_mod.check(r.git, Config(base="main"), fetch=False, prs=True)
            self.assertEqual(rep.files, [])                         # fine against main today
            self.assertEqual([(p["number"], [f["path"] for f in p["files"]]) for p in rep.prs], [(7, ["a.py"])])
            self.assertEqual(rep.exit_code, 2)
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
