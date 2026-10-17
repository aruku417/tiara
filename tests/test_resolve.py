import json
import unittest

from tiara.config import Config
from tiara.resolve import insert_both, resolve_file

CFG = Config()
BASE = "def a():\n    return 1\n"


def res(path, base, main, child, cfg=CFG):
    r = resolve_file(path, base.encode(), main.encode(), child.encode(), cfg)
    return (r.data.decode() if r.data is not None else None), r.strategy


class InsertBothTest(unittest.TestCase):
    def test_both_appended_functions(self):
        out, how = res("m.py", BASE, BASE + "\n\ndef b():\n    return 2\n", BASE + "\n\ndef c():\n    return 3\n")
        self.assertEqual(how, "insert-both")
        self.assertEqual(out, BASE + "\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n")

    def test_same_name_defined_twice_stops(self):
        out, _ = res("m.py", BASE, BASE + "\ndef b():\n    return 2\n", BASE + "\ndef b():\n    return 20\n")
        self.assertIsNone(out)

    def test_same_key_stops(self):
        self.assertIsNone(insert_both(b"A=1\n", b"A=1\nB=2\n", b"A=1\nB=3\n"))
        self.assertEqual(insert_both(b"A=1\n", b"A=1\nB=2\n", b"A=1\nC=3\n"), b"A=1\nB=2\nC=3\n")

    def test_rewrite_is_not_insert(self):
        self.assertEqual(res("m.py", BASE, BASE.replace("1", "2"), BASE.replace("1", "3")),
                         (None, "same-lines-changed"))

    def test_one_side_contains_the_other_and_missing_newline(self):
        self.assertEqual(insert_both(b"x\n", b"x\ny\nz\n", b"x\ny\n"), b"x\ny\nz\n")
        self.assertEqual(insert_both(b"x\n", b"x\ny", b"x\nz\n"), b"x\ny\nz\n")

    def test_can_be_turned_off(self):
        cfg = Config(insert_both=False)
        self.assertIsNone(res("m.py", "x\n", "x\ny\n", "x\nz\n", cfg)[0])


class StructuredTest(unittest.TestCase):
    def test_list_by_id_keeps_both_and_formatting(self):
        base = '{\n  "items": [\n    {"id": 1, "v": "a"}\n  ]\n}\n'
        main = '{\n  "items": [\n    {"id": 1, "v": "a"},\n    {"id": 2, "v": "b"}\n  ]\n}\n'
        child = '{\n  "items": [\n    {"id": 1, "v": "A"},\n    {"id": 3, "v": "c"}\n  ]\n}\n'
        out, how = res("x.json", base, main, child)
        self.assertEqual(how, "structured")
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], [1, 2, 3])
        self.assertEqual(json.loads(out)["items"][0]["v"], "A")
        self.assertIn('{"id": 2, "v": "b"}', out)  # untouched objects keep their original text

    def test_same_value_changed_twice_is_a_conflict(self):
        self.assertEqual(res("x.json", '{"a": 1}', '{"a": 2}', '{"a": 3}'), (None, "structured-conflict"))

    def test_bool_is_not_int(self):
        out = json.loads(res("x.json", '{"a": 1}', '{"a": true}', '{"a": 1, "b": 2}')[0])
        self.assertIs(out["a"], True)
        self.assertEqual(out["b"], 2)




class OtherTest(unittest.TestCase):
    def test_binary_and_deleted(self):
        self.assertEqual(resolve_file("b.bin", b"\0a", b"\0b", b"\0c", CFG).strategy, "binary")
        self.assertEqual(resolve_file("a.txt", b"a", None, b"b", CFG).strategy, "deleted-on-one-side")

    def test_regenerate_glob(self):
        r = resolve_file("dist/app.js", b"a", b"b", b"c", Config(regenerate=["dist/*"]))
        self.assertEqual((r.data, r.regenerate), (b"b", True))


if __name__ == "__main__":
    unittest.main()
