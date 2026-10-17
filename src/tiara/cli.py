"""tiara: get your PR branch conflict-free before GitHub sees it.

  tiara check [--prs]        predict conflicts with the base (and other open PRs) without touching anything
  tiara sync                 merge the base in, resolve what can be resolved, hand the rest to the agent
  tiara verify [--commit]    check the agent's resolution (markers, JSON/YAML, tests) and finish the merge
  tiara handoff              print the current handoff again

Exit codes: 0 = nothing to do / done, 1 = conflicts tiara resolves alone, 2 = the agent is needed,
3 = verify found problems, 4 = error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import __version__
from .check import check
from .config import Config
from .git import Git, GitError
from .sync import _state_dir, sync, verify


def _cfg(git: Git) -> Config:
    return Config.load(git.toplevel())


def cmd_check(args, git: Git) -> int:
    r = check(git, _cfg(git), args.base, not args.no_fetch, args.prs)
    if args.json:
        print(json.dumps(r.as_dict(), ensure_ascii=False, indent=1))
        return r.exit_code
    if r.up_to_date:
        print(f"up to date with {r.base}: GitHub will not report a conflict with it")
    elif not r.files:
        print(f"no conflict with {r.base}")
    else:
        print(f"conflicts with {r.base} in {len(r.files)} file(s):")
        for f in r.files:
            mark = {"auto": "auto  ", "regenerate": "regen ", "agent": "AGENT "}[f.status]
            print(f"  {mark} {f.path}  ({f.strategy}{' at ' + f.where if f.where else ''})")
        print("run `tiara sync`" + (" - the files marked AGENT will be handed to you" if r.needs_agent else
                                    " - it resolves all of them"))
    for p in r.prs:
        if "skipped" in p:
            continue
        print(f"if #{p['number']} ({p['title']}) merges first, you will also conflict in:")
        for f in p["files"]:
            print(f"    {f['status']:<10} {f['path']}  ({f['strategy']})")
    return r.exit_code


def cmd_sync(args, git: Git) -> int:
    r = sync(git, _cfg(git), args.base, not args.no_fetch, not args.no_commit, args.always)
    if args.json:
        print(json.dumps({**asdict(r), "exit_code": r.exit_code}, ensure_ascii=False, indent=1))
        return r.exit_code
    msg = {"up-to-date": f"already contains {r.base}; nothing to do",
           "no-conflict": f"no conflict with {r.base}; nothing to do (use --always to merge anyway)",
           "merged": f"merged {r.base}" + ("" if not args.no_commit else " (not committed)"),
           "resolved": f"merged {r.base}; tiara resolved {len(r.resolved)} file(s)"
                       + ("" if not args.no_commit else " (not committed)")}
    if r.status in msg:
        print(msg[r.status])
        for p, s in r.resolved.items():
            print(f"  {p}: {s}")
        for p in r.regenerate:
            print(f"  regenerate after merge: {p}")
    else:
        print(r.handoff)
    return r.exit_code


def cmd_verify(args, git: Git) -> int:
    r = verify(git, args.cmd, args.commit)
    if args.json:
        print(json.dumps(asdict(r), ensure_ascii=False, indent=1))
    elif r.ok:
        print("ok" + (" - merge committed" if r.committed else " - run with --commit to finish the merge"))
    else:
        print("not yet:")
        for p in r.problems:
            print(f"  {p}")
        if r.test and r.test["returncode"]:
            print(r.test["output_tail"])
    return 0 if r.ok else 3


def cmd_handoff(args, git: Git) -> int:
    path = _state_dir(git) / "handoff.md"
    if not path.exists() or not git.merging():
        print("no handoff (no tiara merge in progress)")
        return 0
    print(path.read_text(encoding="utf-8"))
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tiara", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"tiara {__version__}")
    p.add_argument("-C", dest="cwd", default=".", help="run in this repository")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("check", help="predict conflicts without touching anything")
    s.add_argument("--base", help="target branch (default: .tiara.json `base`, else origin/main)")
    s.add_argument("--prs", action="store_true", help="also check against other open PRs (needs `gh`)")
    s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("sync", help="merge the base in and resolve what can be resolved")
    s.add_argument("--base")
    s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--no-commit", action="store_true", help="leave the merge staged instead of committing")
    s.add_argument("--always", action="store_true", help="merge even when there is no conflict")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("verify", help="check the agent's resolution and finish the merge")
    s.add_argument("--cmd", help="test command to run before committing")
    s.add_argument("--commit", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("handoff", help="print the current handoff again")
    s.set_defaults(func=cmd_handoff)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    git = Git(args.cwd)
    try:
        return args.func(args, git)
    except (GitError, ValueError) as e:
        print(f"tiara: {e}", file=sys.stderr)
        return 4
