"""Three-way merge of JSON and YAML by structure instead of by line.

A line merge can glue two list elements together at the `},{` boundary (still valid JSON, one element
silently gone). Here values are parsed; dicts merge per key, lists merge either
- per element "id" (when every element is a dict with the same unique id key, e.g. `id`, `name`), or
- by keeping both tails when both sides only appended to the end.
Anything else where both sides changed the same place differently is a StructuredConflict.

JSON keeps the original text of untouched objects/arrays so formatting does not churn.
YAML is handled only when PyYAML is installed; YAML with comments is never rewritten.
"""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

ID_KEYS = ("id", "name", "key", "slug")
_MISSING = object()


class StructuredConflict(Exception):
    def __init__(self, where: str):
        super().__init__(where)
        self.where = where


class CannotRewrite(Exception):
    """Mergeable by structure, but rewriting would lose something (YAML comments)."""

    def __init__(self, merged: Any):
        super().__init__("cannot rewrite")
        self.merged = merged


def eq(a: Any, b: Any) -> bool:
    """Deep equality that also compares types (True == 1 in Python)."""
    if a is b:
        return True
    if a is _MISSING or b is _MISSING or type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b))
    return a == b


def merge_values(base: Any, main: Any, child: Any, where: str = "$", id_rules: dict | None = None) -> Any:
    """`id_rules`: {glob over the list location, e.g. "$.jobs.*.steps": [id fields...]}."""
    if eq(main, child):
        return main
    if eq(base, main):
        return child
    if eq(base, child):
        return main
    if all(isinstance(v, dict) for v in (base, main, child)):
        out: dict = {}
        for k in list(main) + [k for k in child if k not in main]:
            v = _slot(base.get(k, _MISSING), main.get(k, _MISSING), child.get(k, _MISSING), f"{where}.{k}", id_rules)
            if v is not _MISSING:
                out[k] = v
        return out
    if all(isinstance(v, list) for v in (base, main, child)):
        return _merge_lists(base, main, child, where, id_rules)
    raise StructuredConflict(where)


def _slot(base: Any, main: Any, child: Any, where: str, id_rules: dict | None) -> Any:
    """A missing key/element is a value too (added / removed / changed)."""
    if eq(main, child):
        return main
    if eq(base, main):
        return child
    if eq(base, child):
        return main
    if _MISSING in (base, main, child):
        raise StructuredConflict(where)  # removed on one side and changed on the other, or added twice
    return merge_values(base, main, child, where, id_rules)


def _ident(item: Any, fields: list[str]):
    if not isinstance(item, dict):
        return None
    for f in fields:
        v = item.get(f)
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            return (f, v)
    return None


def _keyfunc(where: str, id_rules: dict | None, *lists: list):
    rule = next((fields for pat, fields in (id_rules or {}).items() if fnmatch.fnmatchcase(where, pat)), None)
    if rule is not None:
        ids = [[_ident(x, rule) for x in lst] for lst in lists]
        if any(i is None for lst in ids for i in lst) or any(len(set(lst)) != len(lst) for lst in ids):
            return None  # a rule that cannot label every element is not relaxed to the defaults
        return lambda x: _ident(x, rule)
    items = [x for lst in lists for x in lst]
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for key in ID_KEYS:
        if all(key in x and isinstance(x[key], (str, int)) and not isinstance(x[key], bool) for x in items) \
                and all(len({x[key] for x in lst}) == len(lst) for lst in lists):
            return lambda x, key=key: (key, x[key])
    return None


def _merge_lists(base: list, main: list, child: list, where: str, id_rules: dict | None) -> list:
    key = _keyfunc(where, id_rules, base, main, child)
    if key is not None:
        b, m, c = ({key(x): x for x in lst} for lst in (base, main, child))
        order = [key(x) for x in main] + [key(x) for x in child if key(x) not in m]
        out = []
        for ident in order:
            v = _slot(b.get(ident, _MISSING), m.get(ident, _MISSING), c.get(ident, _MISSING),
                      f"{where}[{ident[0]}={ident[1]!r}]", id_rules)
            if v is not _MISSING:
                out.append(v)
        return out
    n = len(base)
    if main[:n] == base and child[:n] == base:
        extra_m, extra_c = main[n:], child[n:]
        return base + extra_m + [x for x in extra_c if x not in extra_m]
    raise StructuredConflict(where)


# --- JSON with original formatting ----------------------------------------

_WS = re.compile(r"[ \t\n\r]*")
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?")


def parse_json_with_spans(text: str) -> tuple[Any, dict[int, str]]:
    """Parse JSON and also return {id(dict or list): original source text}."""
    from json.decoder import scanstring

    spans: dict[int, str] = {}

    def ws(i: int) -> int:
        return _WS.match(text, i).end()

    def value(i: int) -> tuple[Any, int]:
        i = ws(i)
        ch, start = text[i], i
        if ch in "{[":
            close = "}" if ch == "{" else "]"
            obj: Any = {} if ch == "{" else []
            i = ws(i + 1)
            if text[i] == close:
                spans[id(obj)] = text[start:i + 1]
                return obj, i + 1
            while True:
                if ch == "{":
                    if text[i] != '"':
                        raise ValueError(f"expected key at {i}")
                    key, i = scanstring(text, i + 1)
                    i = ws(i)
                    if text[i] != ":":
                        raise ValueError(f"expected ':' at {i}")
                    obj[key], i = value(i + 1)
                else:
                    item, i = value(i)
                    obj.append(item)
                i = ws(i)
                if text[i] == ",":
                    i = ws(i + 1)
                    continue
                if text[i] == close:
                    spans[id(obj)] = text[start:i + 1]
                    return obj, i + 1
                raise ValueError(f"expected ',' or '{close}' at {i}")
        if ch == '"':
            return scanstring(text, i + 1)
        for word, val in (("true", True), ("false", False), ("null", None)):
            if text.startswith(word, i):
                return val, i + len(word)
        m = _NUMBER.match(text, i)
        if not m or m.end() == i:
            raise ValueError(f"unexpected character at {i}")
        num = m.group(0)
        return (float(num) if m.group(1) or m.group(2) else int(num)), m.end()

    result, end = value(0)
    if ws(end) != len(text):
        raise ValueError("extra data")
    return result, spans


def _json_style(text: str) -> dict:
    m = re.search(r"\n([ \t]+)\S", text)
    unit = ("\t" if "\t" in m.group(1) else " " * len(m.group(1))) if m else "  "
    return {"unit": unit, "ensure_ascii": all(ord(ch) < 128 for ch in text), "newline": text.endswith("\n")}


def render_json(value: Any, sources: dict[int, str], style: dict, depth: int = 0) -> str:
    """Reuse the original text of untouched containers; only write out what changed."""
    if id(value) in sources and isinstance(value, (dict, list)):
        return sources[id(value)]
    pad, inner = style["unit"] * depth, style["unit"] * (depth + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{inner}{json.dumps(k, ensure_ascii=style['ensure_ascii'])}: "
                 f"{render_json(v, sources, style, depth + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[\n" + ",\n".join(inner + render_json(v, sources, style, depth + 1) for v in value) + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=style["ensure_ascii"])


def merge_json(base: bytes, main: bytes, child: bytes, id_rules: dict | None = None) -> bytes | None:
    try:
        texts = [x.decode("utf-8") for x in (base, main, child)]
        parsed = [parse_json_with_spans(t) for t in texts]
    except (UnicodeDecodeError, ValueError, IndexError):
        return None
    values = [v for v, _ in parsed]
    if not all(eq(v, json.loads(t)) for v, t in zip(values, texts)):
        return None  # our parser disagrees with json: do not merge by structure
    merged = merge_values(*values, id_rules=id_rules)
    style = _json_style(texts[1])
    sources = {**parsed[0][1], **parsed[2][1], **parsed[1][1]}
    text = render_json(merged, sources, style) + ("\n" if style["newline"] else "")
    if not eq(json.loads(text), merged):
        text = json.dumps(merged, indent=len(style["unit"]), ensure_ascii=style["ensure_ascii"]) + "\n"
    return text.encode("utf-8")


def merge_yaml(base: bytes, main: bytes, child: bytes, id_rules: dict | None = None) -> bytes | None:
    try:
        import yaml  # optional
    except ImportError:
        return None
    try:
        values = [yaml.safe_load(x.decode("utf-8")) for x in (base, main, child)]
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    merged = merge_values(*values, id_rules=id_rules)
    if any(b"#" in x for x in (main, child)):
        raise CannotRewrite(merged)  # rewriting would drop comments
    return yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, default_flow_style=False).encode("utf-8")


def kind(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    return None


def merge_structured(path: str, base: bytes, main: bytes, child: bytes, id_rules: dict | None = None) -> bytes | None:
    k = kind(path)
    if k == "json":
        return merge_json(base, main, child, id_rules)
    if k == "yaml":
        return merge_yaml(base, main, child, id_rules)
    return None


def parse(path: str, data: bytes) -> Any:
    """Parsed value, or the sentinel `MISSING` when it cannot be parsed."""
    try:
        if kind(path) == "json":
            return json.loads(data.decode("utf-8"))
        if kind(path) == "yaml":
            import yaml

            return yaml.safe_load(data.decode("utf-8"))
    except Exception:
        return _MISSING
    return _MISSING


MISSING = _MISSING
