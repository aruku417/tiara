"""Line-based three-way merge.

Changes from both sides (relative to base) that overlap or touch are a conflict.
Adjacent-line edits conflict too (as cautious as git).
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable


@dataclass(frozen=True)
class Hunk:
    lo: int          # range in base [lo, hi)
    hi: int
    lines: list[str]  # replacement lines for that range
    side: str        # "main" or "child"


@dataclass(frozen=True)
class ConflictRegion:
    base: list[str]
    main: list[str]
    child: list[str]
    lo: int = 0   # position in base [lo, hi); used to show surrounding lines
    hi: int = 0


@dataclass(frozen=True)
class MergeResult:
    lines: list[str] | None          # None when there are conflicts
    conflicts: list[ConflictRegion]

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _hunks(base: list[str], other: list[str], side: str) -> list[Hunk]:
    sm = SequenceMatcher(None, base, other, autojunk=False)
    return [Hunk(i1, i2, other[j1:j2], side) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _side_text(base: list[str], lo: int, hi: int, hunks: list[Hunk]) -> list[str]:
    """[lo, hi) with only one side's changes applied."""
    out: list[str] = []
    pos = lo
    for h in hunks:
        out.extend(base[pos:h.lo])
        out.extend(h.lines)
        pos = h.hi
    out.extend(base[pos:hi])
    return out


def merge3(
    base: list[str],
    main: list[str],
    child: list[str],
    resolve: Callable[[ConflictRegion], list[str]] | None = None,
) -> MergeResult:
    """With `resolve`, conflict regions are filled with its return value (conflicts are still reported)."""
    hunks = sorted(_hunks(base, main, "main") + _hunks(base, child, "child"), key=lambda h: (h.lo, h.hi))
    groups: list[list[Hunk]] = []
    for h in hunks:
        if groups and h.lo <= max(g.hi for g in groups[-1]):
            groups[-1].append(h)
        else:
            groups.append([h])

    out: list[str] = []
    conflicts: list[ConflictRegion] = []
    pos = 0
    for group in groups:
        lo = min(h.lo for h in group)
        hi = max(h.hi for h in group)
        out.extend(base[pos:lo])
        pos = hi
        main_hunks = [h for h in group if h.side == "main"]
        child_hunks = [h for h in group if h.side == "child"]
        main_text = _side_text(base, lo, hi, main_hunks)
        child_text = _side_text(base, lo, hi, child_hunks)
        if not child_hunks:
            out.extend(main_text)
        elif not main_hunks or main_text == child_text:
            out.extend(child_text)
        else:
            region = ConflictRegion(base[lo:hi], main_text, child_text, lo, hi)
            conflicts.append(region)
            if resolve is not None:
                out.extend(resolve(region))
    out.extend(base[pos:])
    return MergeResult(None if conflicts and resolve is None else out, conflicts)


def as_text(data: bytes) -> str | None:
    """Decoded text, or None for binary / non-UTF-8 data."""
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def merge3_bytes(base: bytes, main: bytes, child: bytes) -> bytes | None:
    """Merged bytes if clean; None on conflict or binary."""
    texts = [as_text(x) for x in (base, main, child)]
    if any(t is None for t in texts):
        return None
    result = merge3(*(t.splitlines(keepends=True) for t in texts))
    return "".join(result.lines).encode("utf-8") if result.clean else None
