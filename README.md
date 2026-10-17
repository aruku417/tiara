# tiara

**Get your PR branch conflict-free before GitHub sees it.**
Deterministic merges first; a small, precise handoff to your coding agent (Claude Code, Codex, ...) for the rest.

[日本語](README.ja.md)

When several AI agents work on one repository in parallel, most of the time lost on pull requests is not
spent writing code. It goes into the loop *open PR → "This branch has conflicts" → pull main → let the agent
read whole files and fix markers → push → re-run CI*. Many of those conflicts are not real disagreements:
two agents appended a function at the end of the same file, or added different entries to the same JSON list.

tiara predicts those conflicts with the same merge GitHub uses, resolves the mechanical ones without any AI,
and hands only what is left to the agent - with line ranges, both sides' commit messages, and a warning when
both sides rewrote the same lines.

```text
$ tiara check --prs
conflicts with origin/main in 3 file(s):
  auto    src/routes.ts  (insert-both)
  auto    config/features.json  (structured)
  AGENT   src/auth/session.ts  (same-lines-changed)
run `tiara sync` - the files marked AGENT will be handed to you
if #212 (Add rate limiting) merges first, you will also conflict in:
    auto       docs/api.md  (insert-both)
```

## Install

Python 3.10+ and git 2.38+. No dependencies (PyYAML is optional, for YAML merges).

```bash
pipx install git+https://github.com/aruku417/tiara
# or: pip install "tiara[yaml] @ git+https://github.com/aruku417/tiara"
```

`tiara check --prs` also needs the [GitHub CLI](https://cli.github.com/) (`gh`), logged in.

## Use

```bash
tiara check            # predict: will GitHub report a conflict with the base? (touches nothing)
tiara check --prs      # ...and with each other open PR, if it merges first
tiara sync             # merge the base in; resolve what can be resolved; commit, or hand the rest over
tiara verify --commit  # after the agent edits: no markers left, JSON/YAML still valid, then commit
tiara verify --cmd "pytest -q" --commit   # run your tests before committing
```

Exit codes make it easy to script: `check` returns 0 (no conflict), 1 (tiara resolves everything) or
2 (the agent is needed); `sync` returns 0 or 2; `verify` returns 0 or 3; errors return 4.

`sync` only acts when there is a conflict; a branch GitHub can already merge is left alone. It refuses to
run with uncommitted changes, and `git merge --abort` always takes you back.

## What tiara resolves by itself

Only conflicts git reports are touched, and every rule stops rather than guesses.

| Rule | When | Result |
|---|---|---|
| `line` | a careful line merge succeeds where git's hunks touched | merged |
| `insert-both` | both sides only **inserted** at the same spot (new functions, tests, list items) | base's insertion, then yours. Stops if both define the same name (`def`/`class`/`func`/key...) |
| `structured` | JSON / YAML: different keys, or list elements matched by `id`/`name`/`key`/`slug` | merged by value; untouched JSON keeps its exact formatting. YAML with comments is never rewritten |
| `regenerate` | opt-in: files matching `regenerate` globs (generated files) | keeps the base side and tells you to regenerate |

Everything else - the same lines changed differently, delete vs. modify, binaries - goes to the agent.

## The handoff

When something is left, `sync` leaves zdiff3 conflict markers in those files only, and prints
(and saves to `.git/tiara/handoff.md`):

```markdown
## `src/auth/session.ts` (lines 120-141)
- why: both sides changed the same or adjacent lines
- `origin/main` changed it in: 3fa9c1e Shorten session lifetime to 1h
- this branch changed it in: 81d02b7 Refresh sessions on activity
- caution: in 1 place(s) both sides rewrote the same lines. If the two intents cannot both hold,
  do not pick one silently: stop and ask a human.
```

The agent edits only those regions and runs `tiara verify --commit`.

## With coding agents

- **Claude Code**: copy [`integrations/claude-code/skills/tiara`](integrations/claude-code/skills/tiara) to
  `.claude/skills/tiara/` in your repository (or `~/.claude/skills/tiara/`). The agent then runs `tiara check`
  before opening or updating a PR and follows the handoff.
- **Codex** (and other agents that read `AGENTS.md`): append
  [`integrations/codex/AGENTS.md`](integrations/codex/AGENTS.md) to your `AGENTS.md`.

tiara itself never calls an AI model, needs no API key, and sends nothing anywhere.

## Configuration

Optional `.tiara.json` at the repository root (every key is optional):

```json
{
  "base": "origin/main",
  "regenerate": ["dist/*", "*.generated.ts"],
  "id_rules": {"$.jobs.*.steps": ["id", "name"]},
  "insert_both": true
}
```

- `regenerate`: generated files that must never be merged by hand; tiara keeps the base side and reminds you to rebuild them.
- `id_rules`: how to match list elements in JSON/YAML, per location (default: `id`, `name`, `key` or `slug`).
- `insert_both`: set to `false` if the order of inserted blocks matters in your code base.

## How well does it work?

Replayed on 47 past pull requests from two app repositories that had hit a conflict with `main` and were fixed
by hand (149 conflicted files), with the default settings, comparing tiara with the human's final merge:

- tiara resolved **14 files** by itself: 8 identical to the human's result, 3 identical except the order of two
  inserted blocks, 3 where the human also wrote new lines during the merge (so not comparable).
- **No file was merged wrongly.** The other 135 files went to the agent, each with a short handoff.

How much tiara saves depends on how your agents collide: parallel agents that add functions, tests or list
entries next to each other benefit the most.

## Background

tiara started as an experiment after Anthropic described replacing Git with Scion for AI-heavy development
([Redesigning source control at Anthropic](https://www.anthropic.com/engineering/redesigning-source-control)).
This repository is the part that works on top of plain Git and GitHub today: the server-side rebase and
structured merging, applied before the PR instead of inside a new version-control system.

## License

MIT
