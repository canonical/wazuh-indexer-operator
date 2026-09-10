# Copilot instructions for wazuh-indexer-operator

## What this is

A [Juju](https://juju.is/docs/sdk) machine charm (Ops framework) that packages and operates
**Wazuh Indexer** (a fork of OpenSearch) on VMs/machine clusters. Deployed via `charmcraft` from
a `wazuh-indexer` snap.

**This repo is a fork of [`canonical/opensearch-operator`](https://github.com/canonical/opensearch-operator).**
Almost all core logic lives in the shared, upstream-derived library at `lib/charms/opensearch/v0/`
(charm base classes, TLS, backups, plugins, peer-cluster relations, security, etc.) — this is
*not* charm-specific code and changes there should stay compatible with upstream where possible.
Fork-specific behavior (Wazuh naming, snap packaging, config) lives in `src/`. See `FORK.md` for
the upstream-sync workflow (rebase/merge/cherry-pick against `upstream/main`, `git rerere` usage)
and `CHANGELOG-FORK.md` for the log of fork-only changes — update the latter when making
Wazuh-specific (non-upstream) changes.

## Architecture

- `src/charm.py` — `OpenSearchOperatorCharm`, the entry point. Subclasses `OpenSearchCharm` →
  `OpenSearchBaseCharm` (from `lib/charms/opensearch/v0/opensearch_base_charm.py`), which wires up
  almost all event observers (install, relations, TLS, backups, peer-clusters, upgrades...).
  `OpenSearchCharmConfig` overrides `OpenSearchConfig.set_node()` to inject Wazuh-specific audit
  config.
- `src/opensearch.py` — `OpenSearchSnap`, the `OpenSearchDistribution` implementation that manages
  the `wazuh-indexer` snap (install/start/stop, paths, users) instead of upstream's tarball/deb
  distribution.
- `src/lifecycle.py`, `src/machine_upgrade.py`, `src/upgrade.py` — machine-charm specific rolling
  upgrade orchestration (peer-relation-based upgrade protocol "DA056"), health checks per unit.
- `lib/charms/opensearch/v0/` — the shared OpenSearch charm library (peer-cluster orchestration,
  large-deployments, TLS certs, security/users, backups to S3/Azure, plugin manager, node
  exclusions, etc.). Read the relevant module here first when a change touches cluster behavior,
  TLS, backups, or relations — `src/` mostly configures/extends these.
- Other vendored libs under `lib/charms/*` (`data_platform_libs`, `tls_certificates_interface`,
  `operator_libs_linux`, `grafana_agent`, `hydra`) are external charm libraries pulled in via
  `charmcraft fetch-lib`; don't hand-edit them beyond what upstream sync requires.
- Relations defined in `metadata.yaml`: `opensearch-client` (client interface), `peer-cluster` /
  `peer-cluster-orchestrator` (large deployments / multi-cluster), `certificates` (TLS, required),
  `s3-credentials` / `azure-credentials` (backups), `cos-agent` (observability), `oauth`.

## Build / test / lint

Dependency management is via **Poetry** with dependency groups (`charm-libs`, `format`, `lint`,
`unit`, `integration`); tasks are run through **tox**, which installs only the group each env needs.

```shell
poetry install                 # full dev environment

tox                             # runs format, lint, and unit envs
tox -e format                   # isort + black (auto-fixes)
tox -e lint                     # poetry check, codespell, pflake8, isort/black --check, shellcheck
tox -e unit                     # pytest tests/unit + coverage report/xml
tox -e integration -- <args>    # pytest tests/integration (needs a real Juju/LXD cloud)
```

Run a single unit test (pass pytest args after `--` via tox's `{posargs}`):
```shell
tox -e unit -- tests/unit/test_charm.py::TestCharm::test_some_case
tox -e unit -- tests/unit/lib/test_opensearch_base_charm.py -k some_keyword
```

Build the charm (required before integration tests):
```shell
charmcraftcache pack           # preferred over `charmcraft pack` (faster local caching)
```

Integration/system tests also live under `tests/spread/` (spread framework, see `spread.yaml`) —
these run scenarios like backups, HA, large deployments, upgrades against real Juju models; they
are heavier and mostly CI-only.

Linting excludes the `lib/charms/data_platform_libs/` copy inside
`tests/integration/relations/opensearch_provider/application-charm/` — don't add lint fixes there.

## Conventions

- Unit tests mix plain `pytest` + `unittest.mock` and `ops-scenario`/`ops.testing` (Harness-style
  and `Scenario` context based tests) — check the sibling test file for the module you're touching
  to see which style it uses before adding new tests.
- `tests/unit/lib/` mirrors `lib/charms/opensearch/v0/` module-for-module; put new library tests
  there, not under the top-level `tests/unit/`.
- Config/formatting is enforced by `black` + `isort` (`tox -e format` before committing); `pflake8`
  reads config from `pyproject.toml`.
- Charm version bump: `charm_version` file is auto-suffixed with the git hash at pack time
  (`charmcraft.yaml` `files` part) — don't hand-edit that suffix.
- Vale (`.vale.ini`) lints prose docs; `<!-- vale ... -->` comment directives toggle rules inline
  (e.g. in `README.md` for heading case) — preserve these when editing docs.
