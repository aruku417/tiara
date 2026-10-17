## Pull requests: make the branch conflict-free first (tiara)

Before opening a pull request or pushing to an open one:

1. Commit your work and run `tiara check --prs`. Exit 0 means no conflict.
2. Otherwise run `tiara sync`. Exit 0 means it merged the base branch and resolved everything; push.
3. Exit 2 means a handoff was printed (also in `.git/tiara/handoff.md`). For each listed file, edit only the
   zdiff3 conflict regions at the listed lines, keeping both intents (the handoff shows each side's commits).
   Do not reformat or touch other files. If both sides rewrote the same lines with contradicting intents,
   stop and ask instead of choosing.
4. Run `tiara verify --commit --cmd "<test command>"`; fix and repeat while it exits 3. Then push.

If tiara itself fails (exit 4), run `git merge --abort` if a merge is in progress, merge the base branch the
usual way, resolve the conflicts yourself, and report what tiara said.

`git merge --abort` undoes everything. If `check --prs` warns that another open PR will conflict when it
merges first, report the PR number and files.
