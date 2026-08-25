# Changelog

All notable changes to this project will be documented in this file. This will include only change that differ from the [upstream OpenSearch charm](https://github.com/canonical/opensearch-operator).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each revision is versioned by the date of the revision.

## 2026-08-25

### Changed

- Standardized on a single mechanism to mark spread integration tests as "not supported by
  Wazuh": `execute: | true # Not supported by Wazuh: <reason>`. Removed the undocumented
  `.no-large-deployment-for-wazuh` fake-system-suffix hack that silently prevented scheduling of
  large-deployment/cross-model spread tasks (`charmcraft test --list` never listed them). The
  underlying Python test modules are left untouched to minimize future upstream-sync conflicts.
- Disabled additional large-deployment/multi-cluster integration tests that are out of scope for
  Wazuh (single-cluster, microceph-only deployments): `test_backups.py-microceph-large`,
  `test_ca_rotation.py-large`, `test_ha_multi_clusters.py`, and all spread tasks previously hidden
  by the fake-system hack (`test_large_deployments_cluster_manager_only_nodes.py`,
  `test_large_deployments_inherit_cluster_name.py` (both variants),
  `test_large_deployments_relations.py`, `test_large_deployments_remove_orchestrators.py`,
  `test_manual_large_deployment_upgrades.py`, `test_plugins.py-large`,
  `test_failover_promotion_cross_model_relations.py`).
- Added a stub spread task for `test_large_deployments_validate_cm_count.py`, which previously had
  no spread wiring at all and never ran.
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
