# Fork maintenance

This repository is a fork of [`canonical/opensearch-operator`](https://github.com/canonical/opensearch-operator),
adapted to package and operate [Wazuh Indexer](https://wazuh.com/) instead of OpenSearch. This
document describes how the fork is kept in sync with upstream and how to carry that work out
efficiently.

Fork-specific (non-upstream) changes are tracked in [`CHANGELOG-FORK.md`](CHANGELOG-FORK.md).

## Remotes

The repository is expected to have two remotes configured:

```shell
git remote add origin   git@github.com:canonical/wazuh-indexer-operator.git
git remote add upstream git@github.com:canonical/opensearch-operator.git
```

`origin` is this fork; `upstream` is the OpenSearch charm this project is based on.

## Syncing with upstream

To pull in upstream improvements and bug fixes:

```shell
git fetch upstream
git rebase upstream/main   # or: git merge upstream/main
```

Prefer rebasing on top of `upstream/main` when the fork-specific changes are still small and
localized (e.g. workflow tweaks, renamed charm/snap, string replacements), since it keeps history
linear and makes it easy to see which commits are fork-only. Switch to merging once the divergence
grows large enough that repeated rebasing becomes error-prone or history rewriting is undesirable
(e.g. after the fork has been branched/released independently).

Conflicts are expected in files that are heavily customized for Wazuh Indexer, notably:

- `charmcraft.yaml`, `metadata.yaml` / `charm's identity & packaging`
- `.github/workflows/*.yaml` (CI, especially anything referencing snaps, packages, or tool
  versions)
- Any file where an "opensearch" reference was renamed to "wazuh-indexer"

## Cherry-picking individual upstream fixes

When only a specific upstream fix or CI patch is needed (rather than a full rebase/merge), find
the commit on `upstream/main` and cherry-pick it directly:

```shell
git fetch upstream
git log upstream/main --oneline -- <path>   # find the relevant commit
git cherry-pick <sha>
```

Resolve any conflicts, `git add` the resolved files, then `git cherry-pick --continue`.

## Using `git rerere` to reduce repeat conflict resolution

Because the fork repeatedly rebases/merges/cherry-picks against upstream, the same conflicts
(e.g. package name renames, workflow customizations) tend to reappear. [`git rerere`](https://git-scm.com/docs/git-rerere)
("reuse recorded resolution") records how a conflict was resolved and automatically re-applies
that resolution the next time an identical conflict shows up.

Enable it once per clone (repo-local, not global, to avoid affecting unrelated repositories):

```shell
git config rerere.enabled true
git config rerere.autoupdate true   # automatically `git add` resolutions rerere can reapply
```

Notes:

- `rerere` only helps with conflicts it has *already seen and you resolved manually at least
  once*. It records nothing retroactively for conflicts resolved before it was enabled.
- Recorded resolutions live in `.git/rr-cache/`, which is local to the clone and not committed to
  the repository. If you set up a fresh clone, re-enable `rerere` there as well; resolutions do
  not transfer automatically.
- Run `git rerere status` / `git rerere diff` while resolving a conflict to see what rerere has
  staged, and `git rerere forget <path>` if a recorded resolution becomes stale (e.g. upstream
  changed the surrounding context) and needs to be re-recorded.
- `rerere` is especially useful before a `git rebase upstream/main`, since a rebase can replay the
  same conflicting hunk across many commits; once resolved once, later occurrences in the same
  rebase are handled automatically.

## Known recurring conflict points

- `go install github.com/snapcore/spread/cmd/spread@latest` vs.
  `github.com/canonical/spread/cmd/spread@latest` in `.github/workflows/integration_test.yaml`
  (upstream `spread` module path changed from `snapcore` to `canonical`).
- Charm/snap naming (`opensearch` → `wazuh-indexer`) across workflows, `charmcraft.yaml`, and
  Python source.
