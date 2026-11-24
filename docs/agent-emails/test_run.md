# Test prompt run

- Prompt: T1 from `golden/after/01_scout-daily_2026-11-23.txt` (hidden push errors).
- Where: local branch `sandbox/silent-steps` from `origin/main` (git worktree, not pushed, deleted after the run).
- Runner: fresh Claude Code subagent, given only the T1 text and the sandbox path.
- Ground truth (grep before the run): `.github/workflows/scout-daily.yml:51` and `:52`, both `|| true`. No `continue-on-error`.
- Runner result: listed `scout-daily.yml:51` and `scout-daily.yml:52` with file:line, the failure each hides, and the alert path. No `continue-on-error` found. `git status` clean.
- **Result: PASS** (2/2 commands found, 0 missed, 0 files changed).
- Extra find, outside the T1 rule: `.github/workflows/daily-email.yml:37` `git add email/last_sent.txt 2>/dev/null || exit 0` also exits green with no alert.
