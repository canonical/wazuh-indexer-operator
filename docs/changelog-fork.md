# Changelog

All notable changes to this project will be documented in this file. This will include only change that differ from the [upstream OpenSearch charm](https://github.com/canonical/opensearch-operator).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each revision is versioned by the date of the revision.

## 2026-08-25

### Changed

- Removed the spread task directories (`tests/spread/<task>/task.yaml`) for all Azure/AWS/GCP and
  large-deployment/multi-cluster integration tests, which are out of scope for Wazuh (we only
  support single-cluster, microceph-only deployments): `test_backups.py-aws-large`,
  `test_backups.py-aws-small`, `test_backups.py-azure-large`, `test_backups.py-azure-small`,
  `test_backups.py-microceph-large`, `test_ca_rotation.py-large`, `test_ha_multi_clusters.py`,
  `test_large_deployments_cluster_manager_only_nodes.py`,
  `test_large_deployments_inherit_cluster_name.py` (both variants),
  `test_large_deployments_relations.py`, `test_large_deployments_remove_orchestrators.py`,
  `test_large_deployments_validate_cm_count.py`, `test_manual_large_deployment_upgrades.py`,
  `test_plugins.py-large`, `test_failover_promotion_cross_model_relations.py`. Since
  `charmcraft test --list` (and therefore the CI job matrix) is derived by enumerating
  `tests/spread/`, merely stubbing these tasks with `execute: | true` still scheduled a runner and
  consumed CI capacity for each one; deleting the task directories is the only way to stop them
  from ever being scheduled. Removed the now-unused `.no-large-deployment-for-wazuh`
  fake-system-suffix hack that some of these previously relied on. The underlying Python test
  modules are left untouched (unreachable, but kept to minimize future upstream-sync conflicts).
- Added a workaround in `.github/workflows/integration_test.yaml` for
  [juju/juju#18900](https://github.com/juju/juju/issues/18900) (unresolved upstream Juju bug: a
  race in the LXD provisioner when multiple VM machines are started concurrently in the same
  model, causing `Alias already exists: juju/ubuntu@22.04/amd64/vm` failures). The workaround
  pre-caches the jammy VM image under that alias before spread runs, so Juju's provisioner never
  needs to race on the download/alias step. Remove this step once the upstream bug is fixed.

## 2025-08-22

### Updated

- Removed old documentation workflow in favor of an updated workflow to inject a custom word list and check links.

## 2025-07-21

### Added

- Changelog added for tracking changes.
