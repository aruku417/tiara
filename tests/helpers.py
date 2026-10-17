import os
import subprocess
import tempfile
from pathlib import Path

from tiara.git import Git


class Repo:
    """A throwaway repository with a `main` branch and a feature branch."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git = Git(self.root)
        for args in (["init", "-q", "-b", "main"], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
                     ["config", "core.autocrlf", "false"], ["config", "commit.gpgsign", "false"]):
            self.run(*args)

    def run(self, *args: str) -> str:
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True, env=env
                              ).stdout.decode("utf-8", "replace")

    def write(self, files: dict[str, str | None]) -> None:
        for path, text in files.items():
            p = self.root / path
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))

    def commit(self, files: dict[str, str | None], message: str = "change") -> str:
        self.write(files)
        self.run("add", "-A")
        self.run("commit", "-q", "-m", message)
        return self.run("rev-parse", "HEAD").strip()

    def read(self, path: str) -> str:
        return (self.root / path).read_bytes().decode("utf-8")

    def branch(self, name: str, start: str = "main") -> None:
        self.run("checkout", "-q", "-b", name, start)

    def checkout(self, name: str) -> None:
        self.run("checkout", "-q", name)

    def setup(self, base: dict, main: dict, pr: dict) -> None:
        """base commit -> `main` moves on with `main` changes; `pr` branch from base with `pr` changes."""
        self.commit(base, "base")
        self.branch("pr")
        self.commit(pr, "pr change")
        self.checkout("main")
        self.commit(main, "main change")
        self.checkout("pr")

    def close(self) -> None:
        self._tmp.cleanup()
