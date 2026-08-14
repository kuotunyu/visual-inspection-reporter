# Contributing

This is a solo-maintained portfolio project, but `main` is fully protected — including for the
repository admin (`enforce_admins` is on). That has one practical consequence worth knowing
before you next try to push:

## Why you can't `git push origin main` directly anymore

`main` requires the `quality` status check (ruff + pytest, see `.github/workflows/ci.yml`) to
have already succeeded before a push is accepted. A brand-new commit that has never existed on
GitHub can't have a check result yet, so a direct push is rejected outright — there is no
"pending, will pass eventually" state for a push, only "no result recorded" and "successful."

With admin enforcement off, the repo owner could bypass this. With it on (current state), the
owner is held to the same rule as anyone else.

## The workflow that does work

```bash
git checkout -b <short-description>
# ... make changes ...
git push origin <short-description>
gh pr create --fill
# wait for the "quality" check to go green on the PR
gh pr merge --squash --delete-branch
```

- Squash or rebase merge only — a regular merge commit would violate the repo's
  `required_linear_history` setting and GitHub will refuse it.
- `delete_branch_on_merge` is enabled repo-wide, so `--delete-branch` is largely a formality, but
  passing it explicitly avoids relying on that setting from the CLI's perspective.
- The local pre-push hook (`.git/hooks/pre-push`, not tracked in this repo) still runs on every
  push regardless of target branch — it checks the committer identity, scans for the project's
  retired test-account identity, and runs `ruff` + `pytest` via WSL before letting anything out.

## Temporary escape hatch

If you genuinely need one direct push to `main` (e.g. recovering from a broken protected-branch
state), an admin can flip `enforce_admins` off, push, then turn it back on:

```bash
gh api -X PUT repos/kuotunyu/visual-inspection-reporter/branches/main/protection \
  --input <(gh api repos/kuotunyu/visual-inspection-reporter/branches/main/protection \
    --jq '{required_status_checks:{strict:true,contexts:.required_status_checks.contexts},enforce_admins:false,required_pull_request_reviews:null,restrictions:null,allow_force_pushes:.allow_force_pushes.enabled,allow_deletions:.allow_deletions.enabled,required_conversation_resolution:.required_conversation_resolution.enabled,required_linear_history:.required_linear_history.enabled}')
```

Re-run the same call with `enforce_admins:true` afterward. Prefer the PR flow above for anything
that isn't an emergency.
