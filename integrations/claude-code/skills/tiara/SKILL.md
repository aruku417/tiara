---
name: tiara
description: Make a branch conflict-free before opening or updating a pull request. Use before `gh pr create`, before pushing to an open PR, or when GitHub says "This branch has conflicts that must be resolved".
---

# Conflict-free PR branches with tiara

Before you open a pull request or push to one:

1. Commit your work, then run `tiara check --prs`.
   - Exit 0: no conflict. Go ahead.
   - Exit 1 or 2: run `tiara sync`.
2. `tiara sync` merges the base branch in and resolves mechanical conflicts by itself.
   - Exit 0: done (it committed the merge). Push.
   - Exit 2: it printed a handoff (also in `.git/tiara/handoff.md`). Continue below.
3. For each file in the handoff, edit **only** the zdiff3 conflict regions at the listed lines
   (`<<<<<<<` this branch, `|||||||` common ancestor, `=======`, `>>>>>>>` base).
   - Keep both intents. Use the commit subjects in the handoff to understand each side.
   - Do not reformat, and do not touch other files.
   - If the handoff says both sides rewrote the same lines and the intents contradict each other,
     stop and ask the user which behaviour they want. Do not pick one silently.
4. Run `tiara verify --commit --cmd "<the project's test command>"`.
   - Exit 3: fix what it lists and run it again.
5. Push.

To give up at any point: `git merge --abort`.

If `check --prs` says another open PR will conflict with yours when it merges first, tell the user which
PR and which files, so they can decide the merge order.
