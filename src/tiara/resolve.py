"""Deterministic resolution of one conflicted file. No AI is involved here.

Order (first that works wins):
  0. regenerate    files listed in `regenerate` (generated files): keep the base side, rebuild later
  1. line          a careful line merge (sometimes resolves what git's hunk boundaries did not)
  2. insert-both   both sides only inserted at the same spot: keep base's insertion, then the PR's
                   (stops when both define the same name: def/class/func/key...)
  3. structured    JSON / YAML merged by value; a structural conflict always wins over a clean line merge
Anything left goes to the agent (Claude Code, Codex, ...) as a small handoff.

Naming: `main` is the branch the PR targets, `child` is the PR.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .config import Config
from .merge3 import ConflictRegion, as_text, merge3, merge3_bytes
from .structured import CannotRewrite, MISSING, StructuredConflict, eq, merge_structured, parse


@dataclass(frozen=True)
class Resolution:
    data: bytes | None          # merged content, or None when the agent has to decide
    strategy: str               # how it was resolved, or why not
    where: str = ""             # structural location of the conflict (JSON path), if any
    regenerate: bool = False    # keep base side; the file must be rebuilt after the merge


def needs_regeneration(path: str, cfg: Config) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in cfg.regenerate)


# --- both sides inserted at the same spot ---------------------------------

_DEF_RE = re.compile(
    r"^\s*(?:export\s+|public\s+|private\s+|internal\s+|static\s+|async\s+|@\w+\s+)*"
    r"(?:def|class|func|function|struct|enum|protocol|interface|type|let|var|const|fn)\s+([A-Za-z_]\w*)")
_KEY_RE = re.compile(r"""^\s*["']?([A-Za-z_][\w.-]*)["']?\s*(?::|=(?!=))""")


def _names(lines: list[str]) -> set[str]:
    return {m.group(1) for ln in lines if (m := _DEF_RE.match(ln) or _KEY_RE.match(ln))}


class _NotInsertOnly(Exception):
    pass


def _contains(big: list[str], small: list[str]) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


def insert_both(base: bytes, main: bytes, child: bytes) -> bytes | None:
    texts = [as_text(x) for x in (base, main, child)]
    if any(t is None for t in texts):
        return None
    b, m, c = (t.splitlines(keepends=True) for t in texts)

    def resolve(region: ConflictRegion) -> list[str]:
        if region.base:
            raise _NotInsertOnly
        mine, theirs = region.main, region.child
        if _contains(mine, theirs):
            return mine
        if _contains(theirs, mine):
            return theirs
        if _names(mine) & _names(theirs):
            raise _NotInsertOnly  # the same thing was defined twice, differently
        if mine and not mine[-1].endswith(("\n", "\r")):
            mine = mine[:-1] + [mine[-1] + "\n"]
        return mine + theirs

    try:
        return "".join(merge3(b, m, c, resolve=resolve).lines).encode("utf-8")
    except _NotInsertOnly:
        return None


def both_rewrote(region: ConflictRegion) -> bool:
    """Both sides replaced the same base lines (neither kept them): usually the same behaviour changed twice."""
    return bool(region.base) and not _contains(region.main, region.base) and not _contains(region.child, region.base)




# --- the pipeline -----------------------------------------------------------

def resolve_file(path: str, base: bytes | None, main: bytes | None, child: bytes | None, cfg: Config) -> Resolution:
    if needs_regeneration(path, cfg):
        return Resolution(main, "regenerate", regenerate=True)
    if base is None or main is None or child is None:
        return Resolution(None, "added-twice" if base is None and main is not None and child is not None
                          else "deleted-on-one-side")
    if as_text(base) is None or as_text(main) is None or as_text(child) is None:
        return Resolution(None, "binary")

    line, how = merge3_bytes(base, main, child), "line"
    if line is None and cfg.insert_both:
        line = insert_both(base, main, child)
        how = "insert-both"
    try:
        structured = merge_structured(path, base, main, child, cfg.id_rules)
    except StructuredConflict as e:
        return Resolution(None, "structured-conflict", e.where)
    except CannotRewrite as e:
        if line is not None and eq(parse(path, line), e.merged):
            return Resolution(line, f"{how}(=structured)")
        return Resolution(None, "yaml-with-comments", "$")
    if structured is None:
        return Resolution(line, how) if line is not None else Resolution(None, "same-lines-changed")
    if line is not None and parse(path, line) is not MISSING and eq(parse(path, line), parse(path, structured)):
        return Resolution(line, f"{how}(=structured)")
    return Resolution(structured, "structured")
