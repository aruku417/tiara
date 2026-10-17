"""Thin wrappers around the git CLI. Everything goes through `git` so behaviour matches GitHub."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_GIT = (2, 38)  # `git merge-tree --write-tree`


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, cwd: str | Path = "."):
        self.cwd = str(cwd)

    def run(self, *args: str, input: bytes | None = None, check: bool = True, env: dict | None = None
            ) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", *args], cwd=self.cwd, input=input, capture_output=True,
                              env={**os.environ, **env} if env else None)
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc

    def out(self, *args: str) -> str:
        return self.run(*args).stdout.decode("utf-8", "replace").strip()

    # --- basics ---------------------------------------------------------
    def version(self) -> tuple[int, ...]:
        m = re.search(r"(\d+)\.(\d+)", self.out("--version"))
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    def require_version(self) -> None:
        if self.version() < MIN_GIT:
            raise GitError(f"git {'.'.join(map(str, MIN_GIT))} or newer is required (merge-tree --write-tree)")

    def toplevel(self) -> Path:
        return Path(self.out("rev-parse", "--show-toplevel"))

    def git_dir(self) -> Path:
        return Path(self.out("rev-parse", "--absolute-git-dir"))

    def rev(self, spec: str) -> str:
        return self.out("rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}")

    def exists(self, spec: str) -> bool:
        return self.run("rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}", check=False).returncode == 0

    def merge_base(self, a: str, b: str) -> str | None:
        p = self.run("merge-base", a, b, check=False)
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.run("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def current_branch(self) -> str | None:
        p = self.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def blob(self, oid: str) -> bytes:
        return self.run("cat-file", "blob", oid).stdout

    def show(self, rev: str, path: str) -> bytes | None:
        p = self.run("cat-file", "blob", f"{rev}:{path}", check=False)
        return p.stdout if p.returncode == 0 else None

    def hash_blob(self, data: bytes) -> str:
        return self.run("hash-object", "-w", "--stdin", input=data).stdout.decode().strip()

    def dirty(self) -> list[str]:
        """Tracked files with uncommitted changes (untracked files are ignored)."""
        out = self.run("status", "--porcelain", "-z", "--untracked-files=no").stdout.decode("utf-8", "replace")
        return [e[3:] for e in out.split("\0") if e]

    def merging(self) -> bool:
        return (self.git_dir() / "MERGE_HEAD").exists()

    def log_subjects(self, rng: str, path: str, limit: int = 5) -> list[str]:
        out = self.out("log", f"-{limit}", "--format=%h %s", rng, "--", path)
        return [line for line in out.splitlines() if line]

    # --- merges ---------------------------------------------------------
    def merge_tree(self, ours: str, theirs: str) -> "TreeMerge":
        """Merge two commits without touching the work tree (same ort machinery GitHub uses)."""
        p = self.run("merge-tree", "--write-tree", "-z", "--no-messages", ours, theirs, check=False)
        if p.returncode not in (0, 1):
            raise GitError(f"git merge-tree failed: {p.stderr.decode('utf-8', 'replace').strip()}")
        parts = p.stdout.decode("utf-8", "surrogateescape").split("\0")
        tree, conflicts = parts[0], {}
        for entry in parts[1:]:
            if not entry:
                break
            meta, path = entry.split("\t", 1)
            mode, oid, stage = meta.split(" ")
            conflicts.setdefault(path, {})[int(stage)] = (mode, oid)
        return TreeMerge(tree, p.returncode == 0, {k: Stages.of(v) for k, v in conflicts.items()})

    def unmerged(self) -> dict[str, "Stages"]:
        """Unmerged index entries during a merge in the work tree."""
        out = self.run("ls-files", "-u", "-z").stdout.decode("utf-8", "surrogateescape")
        found: dict[str, dict] = {}
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            mode, oid, stage = meta.split(" ")
            found.setdefault(path, {})[int(stage)] = (mode, oid)
        return {k: Stages.of(v) for k, v in found.items()}

    def stage_blob(self, path: str, data: bytes, mode: str = "100644") -> None:
        """Resolve `path` in the index with `data` and write it to the work tree (honours eol/filters)."""
        oid = self.hash_blob(data)
        self.run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
        self.run("checkout-index", "-f", "--", path)

    def commit_tree(self, tree: str, parents: list[str], message: str) -> str:
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        env = {"GIT_AUTHOR_NAME": "tiara", "GIT_AUTHOR_EMAIL": "tiara@localhost",
               "GIT_COMMITTER_NAME": "tiara", "GIT_COMMITTER_EMAIL": "tiara@localhost"}
        return self.run(*args, "-m", message, env=env).stdout.decode().strip()


@dataclass(frozen=True)
class Stages:
    """Index stages of one conflicted path: 1 = common ancestor, 2 = ours, 3 = theirs (oid or None)."""
    base: str | None
    ours: str | None
    theirs: str | None
    mode: str

    @staticmethod
    def of(stages: dict[int, tuple[str, str]]) -> "Stages":
        mode = (stages.get(2) or stages.get(3) or stages.get(1))[0]
        return Stages(*(stages[i][1] if i in stages else None for i in (1, 2, 3)), mode)


@dataclass(frozen=True)
class TreeMerge:
    tree: str
    clean: bool
    conflicts: dict[str, Stages]
