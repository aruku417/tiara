"""Bring the base branch into your PR branch so GitHub sees no conflict.

`sync` starts a normal `git merge` of the base (with zdiff3 markers), resolves every file it can
deterministically, and either commits the merge or leaves the few remaining files to the agent with a
short handoff (.git/tiara/handoff.md). `verify` checks the agent's edits and finishes the merge.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .check import fetch_base
from .config import Config
from .git import Git, GitError
from .merge3 import as_text, merge3
from .resolve import both_rewrote, resolve_file
from .structured import MISSING, kind, parse

MARKER = re.compile(r"^(<<<<<<<|\|\|\|\|\|\|\||=======|>>>>>>>)( |$)", re.M)


@dataclass
class SyncResult:
    status: str                      # "up-to-date" | "no-conflict" | "merged" | "resolved" | "needs-agent"
    base: str
    resolved: dict[str, str] = field(default_factory=dict)      # path -> strategy
    regenerate: list[str] = field(default_factory=list)
    needs_agent: list[dict] = field(default_factory=list)
    handoff: str = ""

    @property
    def exit_code(self) -> int:
        return 2 if self.status == "needs-agent" else 0


def _state_dir(git: Git) -> Path:
    d = git.git_dir() / "tiara"
    d.mkdir(exist_ok=True)
    return d


def sync(git: Git, cfg: Config, base: str | None = None, fetch: bool = True, commit: bool = True,
         always: bool = False) -> SyncResult:
    git.require_version()
    base = base or cfg.base
    if git.merging():
        raise GitError("a merge is already in progress: finish it (`tiara verify --commit`) or `git merge --abort`")
    if git.dirty():
        raise GitError("commit or stash your changes first (tracked files are modified)")
    if fetch:
        fetch_base(git, base)
    if not git.exists(base):
        raise GitError(f"base {base!r} not found")
    if git.is_ancestor(base, "HEAD"):
        return SyncResult("up-to-date", base)
    if git.merge_tree("HEAD", base).clean and not always:
        return SyncResult("no-conflict", base)  # GitHub can merge as is; nothing to prepare

    branch = git.current_branch() or "HEAD"
    proc = git.run("-c", "merge.conflictStyle=zdiff3", "merge", "--no-ff", "--no-commit", "--no-edit", base,
                   check=False)
    if proc.returncode not in (0, 1) or (proc.returncode == 1 and not git.unmerged()):
        raise GitError("git merge failed: " + (proc.stderr or proc.stdout).decode("utf-8", "replace").strip())

    result = SyncResult("merged", base)
    for path, st in sorted(git.unmerged().items()):
        blob = lambda oid: git.blob(oid) if oid else None  # noqa: E731
        b, child, main = blob(st.base), blob(st.ours), blob(st.theirs)
        r = resolve_file(path, b, main, child, cfg)
        if r.data is not None:
            git.stage_blob(path, r.data, st.mode)
            result.resolved[path] = r.strategy
            if r.regenerate:
                result.regenerate.append(path)
        else:
            result.needs_agent.append(_describe(git, base, path, b, main, child, r.strategy, r.where))

    if result.needs_agent:
        result.status = "needs-agent"
        result.handoff = _write_handoff(git, base, branch, result)
        return result
    result.status = "resolved" if result.resolved else "merged"
    if commit:
        git.run("commit", "--quiet", "-m", _message(base, branch, result))
    return result


def _describe(git: Git, base: str, path: str, b, main, child, strategy: str, where: str) -> dict:
    root = git.toplevel()
    text = (root / path).read_text(encoding="utf-8", errors="replace") if (root / path).exists() else ""
    lines = text.splitlines()
    ranges, start = [], None
    for i, ln in enumerate(lines, 1):
        if ln.startswith("<<<<<<< "):
            start = i
        elif ln.startswith(">>>>>>> ") and start is not None:
            ranges.append([start, i])
            start = None
    rewrites = 0
    texts = [as_text(x) if x is not None else None for x in (b, main, child)]
    if all(t is not None for t in texts):
        regions = merge3(*(t.splitlines(keepends=True) for t in texts)).conflicts
        rewrites = sum(both_rewrote(r) for r in regions)
    mb = git.merge_base("HEAD", base) or ""
    return {"path": path, "why": strategy, "where": where, "marker_lines": ranges, "both_rewrote": rewrites,
            "base_commits": git.log_subjects(f"{mb}..{base}", path) if mb else [],
            "branch_commits": git.log_subjects(f"{mb}..HEAD", path) if mb else []}


WHY = {
    "same-lines-changed": "both sides changed the same or adjacent lines",
    "structured-conflict": "both sides set the same JSON/YAML value differently",
    "yaml-with-comments": "YAML merge would drop comments, and the line merge failed",
    "deleted-on-one-side": "deleted on one side, changed on the other",
    "added-twice": "added on both sides with different content",
    "binary": "binary file changed on both sides",
}


def _write_handoff(git: Git, base: str, branch: str, result: SyncResult) -> str:
    d = _state_dir(git)
    (d / "state.json").write_text(json.dumps({"base": base, "branch": branch, **asdict(result)},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# tiara handoff: merging `{base}` into `{branch}`", ""]
    if result.resolved:
        md.append(f"tiara already resolved {len(result.resolved)} file(s): "
                  + ", ".join(f"`{p}` ({s})" for p, s in result.resolved.items()) + ".")
    if result.regenerate:
        md.append("Kept the base side of " + ", ".join(f"`{p}`" for p in result.regenerate)
                  + " - regenerate them after the merge (they are generated files).")
    md += ["", f"{len(result.needs_agent)} file(s) need you:", ""]
    for f in result.needs_agent:
        rng = ", ".join(f"{a}-{b}" for a, b in f["marker_lines"]) or "whole file"
        md.append(f"## `{f['path']}` (lines {rng})")
        md.append(f"- why: {WHY.get(f['why'], f['why'])}" + (f" at `{f['where']}`" if f["where"] else ""))
        if f["base_commits"]:
            md.append(f"- `{base}` changed it in: " + "; ".join(f["base_commits"]))
        if f["branch_commits"]:
            md.append("- this branch changed it in: " + "; ".join(f["branch_commits"]))
        if f["both_rewrote"]:
            md.append(f"- caution: in {f['both_rewrote']} place(s) both sides rewrote the same lines. If the two "
                      "intents cannot both hold, do not pick one silently: stop and ask a human.")
        md.append("")
    md += ["## How to finish",
           "- Markers are zdiff3: `<<<<<<<` this branch, `|||||||` common ancestor, `=======`, `>>>>>>>` base.",
           "- Edit only those regions. Keep both intents; do not reformat or touch other files.",
           "- Then run `tiara verify --commit` (add `--cmd \"<your tests>\"` to run tests first).",
           "- To give up: `git merge --abort`.", ""]
    text = "\n".join(md)
    (d / "handoff.md").write_text(text, encoding="utf-8")
    return text


def _message(base: str, branch: str, result: SyncResult) -> str:
    lines = [f"Merge {base} into {branch}"]
    if result.resolved:
        lines += ["", "Conflicts resolved by tiara:"] + [f"- {p}: {s}" for p, s in result.resolved.items()]
    if result.regenerate:
        lines += ["", "Regenerate after merge: " + ", ".join(result.regenerate)]
    return "\n".join(lines)


# --- verify -----------------------------------------------------------------

@dataclass
class VerifyResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    committed: bool = False
    test: dict | None = None


def verify(git: Git, cmd: str | None = None, commit: bool = False) -> VerifyResult:
    if not git.merging():
        raise GitError("no merge in progress (run `tiara sync` first)")
    state_path = _state_dir(git) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    paths = [f["path"] for f in state.get("needs_agent", [])] or list(git.unmerged())
    root = git.toplevel()
    res = VerifyResult(True)
    for path in paths:
        f = root / path
        if not f.exists():
            continue  # resolved by deleting; `git add -A` below records it
        data = f.read_bytes()
        text = as_text(data)
        if text is not None and MARKER.search(text):
            res.problems.append(f"{path}: conflict markers are still there")
        elif kind(path) and parse(path, data) is MISSING:
            res.problems.append(f"{path}: not valid {kind(path).upper()} any more")
    if res.problems:
        res.ok = False
        return res
    git.run("add", "-A", "--", *paths)
    leftover = set(git.unmerged())
    if leftover:
        res.ok = False
        res.problems += [f"{p}: still unmerged" for p in sorted(leftover)]
        return res
    if cmd:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True)
        tail = (p.stdout + p.stderr).decode("utf-8", "replace")[-2000:]
        res.test = {"cmd": cmd, "returncode": p.returncode, "output_tail": tail}
        if p.returncode != 0:
            res.ok = False
            res.problems.append(f"tests failed: {cmd}")
            return res
    if commit:
        msg = _message(state.get("base", "base"), state.get("branch", "HEAD"),
                       SyncResult("resolved", state.get("base", ""), state.get("resolved", {}),
                                  state.get("regenerate", [])))
        msg += "\n\nResolved by the agent: " + ", ".join(paths)
        git.run("commit", "--quiet", "-m", msg)
        res.committed = True
        state_path.unlink(missing_ok=True)
    return res
