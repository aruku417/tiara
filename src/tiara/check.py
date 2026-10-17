"""Predict, before you open or update a PR, whether GitHub will report a merge conflict.

Uses `git merge-tree --write-tree` (the same ort merge GitHub runs), without touching your work tree.
Every conflicted file is then run through tiara's deterministic resolvers to tell you which ones
`tiara sync` will fix by itself and which ones need the agent.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field

from .config import Config
from .git import Git, GitError, Stages
from .resolve import resolve_file


@dataclass
class FileReport:
    path: str
    status: str          # "auto" | "regenerate" | "agent"
    strategy: str
    where: str = ""


@dataclass
class Report:
    base: str
    head: str
    up_to_date: bool = False
    files: list[FileReport] = field(default_factory=list)
    prs: list[dict] = field(default_factory=list)

    @property
    def needs_agent(self) -> list[FileReport]:
        return [f for f in self.files if f.status == "agent"]

    @property
    def exit_code(self) -> int:
        """0 = no conflict, 1 = conflicts that `tiara sync` resolves alone, 2 = the agent is needed."""
        pr_agent = any(f["status"] == "agent" for p in self.prs for f in p.get("files", []))
        if self.needs_agent:
            return 2
        if self.files:
            return 1
        return 2 if pr_agent else 0

    def as_dict(self) -> dict:
        return {**asdict(self), "exit_code": self.exit_code}


def fetch_base(git: Git, base: str) -> None:
    """`origin/main` -> `git fetch origin main`. Local refs are left alone."""
    remote, _, branch = base.partition("/")
    if branch and remote in git.out("remote").split():
        git.run("fetch", "--quiet", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")


def classify(git: Git, cfg: Config, conflicts: dict[str, Stages]) -> list[FileReport]:
    out = []
    for path, st in sorted(conflicts.items()):
        blob = lambda oid: git.blob(oid) if oid else None  # noqa: E731
        # merging `main` into the PR: stage 2 = the PR (child), stage 3 = main
        r = resolve_file(path, blob(st.base), blob(st.theirs), blob(st.ours), cfg)
        status = "regenerate" if r.regenerate and r.data is not None else "auto" if r.data is not None else "agent"
        out.append(FileReport(path, status, r.strategy, r.where))
    return out


def check(git: Git, cfg: Config, base: str | None = None, fetch: bool = True, prs: bool = False) -> Report:
    git.require_version()
    base = base or cfg.base
    if fetch:
        fetch_base(git, base)
    if not git.exists(base):
        raise GitError(f"base {base!r} not found (set it with --base or in .tiara.json)")
    head = git.rev("HEAD")
    report = Report(base, head)
    if git.is_ancestor(base, "HEAD"):
        report.up_to_date = True
    else:
        tm = git.merge_tree("HEAD", base)
        report.files = classify(git, cfg, tm.conflicts)
    if prs:
        report.prs = check_open_prs(git, cfg, base, fetch, report)
    return report


# --- other open pull requests ---------------------------------------------

def _gh_open_prs(git: Git, base_branch: str) -> list[dict]:
    proc = subprocess.run(["gh", "pr", "list", "--state", "open", "--base", base_branch, "--limit", "100",
                           "--json", "number,title,headRefName,headRefOid"],
                          cwd=git.cwd, capture_output=True)
    if proc.returncode != 0:
        raise GitError("gh pr list failed (is the GitHub CLI installed and logged in?): "
                       + proc.stderr.decode("utf-8", "replace").strip())
    return json.loads(proc.stdout.decode("utf-8"))


def check_open_prs(git: Git, cfg: Config, base: str, fetch: bool, report: Report) -> list[dict]:
    """For each other open PR: if it merges first, which of your files will conflict?"""
    remote, _, base_branch = base.partition("/")
    if not base_branch:
        remote, base_branch = "origin", base
    me = git.current_branch()
    already = {f.path for f in report.files}
    out = []
    for pr in _gh_open_prs(git, base_branch):
        if pr["headRefName"] == me:
            continue
        ref = f"refs/tiara/pr/{pr['number']}"
        if fetch:
            git.run("fetch", "--quiet", remote, f"+refs/pull/{pr['number']}/head:{ref}", check=False)
        if not git.exists(ref):
            out.append({"number": pr["number"], "title": pr["title"], "skipped": "could not fetch"})
            continue
        first = git.merge_tree(base, ref)
        if not first.clean:
            out.append({"number": pr["number"], "title": pr["title"], "skipped": "conflicts with base itself"})
            continue
        after = git.commit_tree(first.tree, [git.rev(base), git.rev(ref)], f"tiara: simulate #{pr['number']}")
        tm = git.merge_tree("HEAD", after)
        new = {p: s for p, s in tm.conflicts.items() if p not in already}
        if new:
            files = [asdict(f) for f in classify(git, cfg, new)]
            out.append({"number": pr["number"], "title": pr["title"], "files": files})
    return out
