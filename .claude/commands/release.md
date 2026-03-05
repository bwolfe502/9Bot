Create a release: bump version, merge to master, tag, build zip, publish GitHub release.

## Pre-flight checks

1. Confirm on `dev` branch with clean working tree (no uncommitted changes). Abort if not.
2. Check for unmerged feature branches: `git branch --no-merged dev`. If any exist, list them
   and ask the user which (if any) to merge into `dev` before proceeding. Merge the selected
   branches, then continue. If none selected, continue without merging.
3. Run `py -m pytest -x` (or `python -m pytest -x` on macOS). Abort if tests fail.
4. Read current version from `version.txt`.

## Release review

Before proceeding, review everything that will ship. This is your chance to flag problems.

1. Run `git log --oneline <last_tag>..HEAD` to get all commits since the last release.
2. Read the diffs for any commits that look risky or significant (`git diff <last_tag>..HEAD`).
3. Check `.claude/analysis/` for any known open issues or recent findings that might affect stability.
4. Present a summary to the user:
   - **Commits included**: list each with a one-line description
   - **Risk assessment**: flag anything that concerns you — incomplete features, large refactors,
     changes to critical paths (vision, navigation, rally joining), untested areas, or anything
     that looks like WIP that shouldn't ship yet
   - **Recommendation**: explicitly say whether you think this is a **good** or **bad** release,
     and why. Be honest — it's better to delay a release than ship a broken update to users.
5. Wait for the user to confirm before continuing. If you flagged concerns, make sure they
   acknowledge each one.

## Version bump

Ask the user: patch, minor, or major? Show what each would produce (e.g. 2.0.6 → 2.0.7 / 2.1.0 / 3.0.0).

Bump `version.txt` with the new version. Commit on `dev`:
```
chore: bump version to X.Y.Z
```

## Merge and tag

```bash
git checkout master
git merge dev --no-edit
git tag vX.Y.Z
git checkout dev
```

Push everything:
```bash
git push origin dev master vX.Y.Z
```

## Build release zip

Create the zip from the tagged commit using `git archive`:
```bash
git archive --format=zip --prefix=9bot-X.Y.Z/ -o 9bot-X.Y.Z.zip vX.Y.Z
```

This includes all tracked files at that commit. The updater handles it correctly.

## Publish GitHub release

Ask the user for release notes (or offer to auto-generate from commits since the previous tag).

```bash
gh release create vX.Y.Z 9bot-X.Y.Z.zip --title "vX.Y.Z" --notes "NOTES_HERE"
```

Clean up the local zip file after upload.

## Done

Show the user:
- Old version → new version
- GitHub release URL
- Remind them that users will auto-update on next bot restart
