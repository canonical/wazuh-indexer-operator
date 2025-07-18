# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit test for the opensearch_plugins library."""
import unittest
from collections import namedtuple
from unittest.mock import MagicMock, PropertyMock, patch

import charms
import pytest
import tenacity
from charms.opensearch.v0.constants_charm import (
    S3_RELATION,
    BackupDeferRelBrokenAsInProgress,
    BackupInDisabling,
    PeerRelationName,
    RestoreInProgress,
)
from charms.opensearch.v0.helper_cluster import IndexStateEnum
from charms.opensearch.v0.models import PerformanceType
from charms.opensearch.v0.opensearch_backups import (
    S3_REPOSITORY,
    BackupServiceState,
    OpenSearchRestoreCheckError,
    OpenSearchRestoreIndexClosingError,
)
from charms.opensearch.v0.opensearch_exceptions import (
    OpenSearchError,
    OpenSearchHttpError,
)
from charms.opensearch.v0.opensearch_health import HealthColors
from charms.opensearch.v0.opensearch_plugins import (
    OpenSearchPluginConfig,
    OpenSearchPluginError,
    OpenSearchS3Plugin,
    PluginState,
)
from ops.model import MaintenanceStatus, WaitingStatus
from ops.testing import Harness

from charm import OpenSearchOperatorCharm
from lib.charms.opensearch.v0.models import (
    App,
    DeploymentDescription,
    DeploymentState,
    DeploymentType,
    PeerClusterConfig,
    StartMode,
    State,
)
from tests.helpers import patch_wait_fixed

TEST_BUCKET_NAME = "s3://bucket-test"
TEST_BASE_PATH = "/test"


LIST_BACKUPS_TRIAL = """ backup-id           | backup-status
------------------------------------
2023-01-01T00:00:00Z | success
2023-01-01T00:10:00Z | snapshot failed for unknown reason
2023-01-01T00:20:00Z | snapshot in progress"""


deployment_desc = namedtuple("deployment_desc", ["typ"])


def create_deployment_desc(*args, **kwargs):
    return DeploymentDescription(
        config=PeerClusterConfig(
            cluster_name="logs",
            init_hold=False,
            roles=["cluster_manager", "data"],
            profile=PerformanceType.PRODUCTION,
        ),
        start=StartMode.WITH_PROVIDED_ROLES,
        pending_directives=[],
        app=App(model_uuid="model-uuid", name="wazuh-indexer"),
        typ=DeploymentType.MAIN_ORCHESTRATOR,
        state=DeploymentState(value=State.ACTIVE),
        promotion_time=None,
    )


@pytest.fixture(scope="module")
def active_relation(relation: str = S3_RELATION):
    with patch(
        "charms.opensearch.v0.opensearch_backups.OpenSearchBackupBase.active_relation",
        new_callable=PropertyMock,
        return_value=relation,
    ) as mock:
        yield mock


@pytest.fixture(scope="function")
def harness(active_relation):
    harness_obj = Harness(OpenSearchOperatorCharm)
    with patch(
        "charms.opensearch.v0.opensearch_base_charm.OpenSearchPeerClustersManager.deployment_desc",
        return_value=create_deployment_desc(),
    ):
        harness_obj.begin()
        charm = harness_obj.charm
        # Override the config to simulate the TestPlugin
        # As config.yml does not exist, the setup below simulates it
        charm.plugin_manager._charm_config = harness_obj.model._config
        # Override the ConfigExposedPlugins
        charms.opensearch.v0.opensearch_plugin_manager.ConfigExposedPlugins = {
            "repository-s3": {
                "class": OpenSearchS3Plugin,
                "config": None,
                "relation": "s3-credentials",
            },
        }
        charm.opensearch.is_started = MagicMock(return_value=True)
        charm.health.apply = MagicMock(return_value=HealthColors.GREEN)
        # Mock retrials to speed up tests
        charms.opensearch.v0.opensearch_backups.wait_fixed = MagicMock(
            return_value=tenacity.wait.wait_fixed(0.1)
        )

        # Replace some unused methods that will be called as part of set_leader with mock
        charm._put_admin_user = MagicMock()
        charm._put_kibanaserver_user = MagicMock()
        charm._put_or_update_internal_user_leader = MagicMock()

        harness_obj.add_relation(PeerRelationName, "wazuh-indexer")
        harness_obj.set_leader(is_leader=True)

        yield harness_obj


@pytest.fixture(scope="function")
def mock_request():
    with patch("charms.opensearch.v0.opensearch_distro.OpenSearchDistribution.request") as mock:
        yield mock


@pytest.mark.parametrize(
    "leader,request_value,result_value",
    [
        # Test leader + request_value that should return True
        (
            False,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
            },
            True,
        ),
        (
            True,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
            },
            True,
        ),
        # Test leader + request_value that should return False
        (
            False,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "IN_PROGRESS"}]},
            },
            False,
        ),
        (
            True,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "IN_PROGRESS"}]},
            },
            False,
        ),
        # Test leader + request_value that should return True
        (
            False,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index3": {"shards": [{"type": "NOT_SNAP", "stage": "DONE"}]},
            },
            True,
        ),
        (
            True,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index3": {"shards": [{"type": "NOT_SNAP", "stage": "DONE"}]},
            },
            True,
        ),
        # Test leader + request_value that should return False
        (
            False,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "IN_PROGRESS"}]},
                "index3": {"shards": [{"type": "NOT_SNAP", "stage": "DONE"}]},
            },
            False,
        ),
        (
            True,
            {
                "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
                "index2": {"shards": [{"type": "SNAPSHOT", "stage": "IN_PROGRESS"}]},
                "index3": {"shards": [{"type": "NOT_SNAP", "stage": "DONE"}]},
            },
            False,
        ),
    ],
)
def test_restore_finished_true(harness, mock_request, leader, request_value, result_value):
    harness.charm.backup.charm.unit.is_leader = MagicMock(return_value=leader)
    mock_request.return_value = request_value
    assert harness.charm.backup.backup_manager.is_restore_in_progress() != result_value


@pytest.mark.parametrize(
    "list_backup_response,cluster_state,req_response,exception_raised",
    [
        # Check if only indices in backup-id=1 are closed
        (
            {1: {"indices": ["index1", "index2"]}},
            {
                "index1": {"status": IndexStateEnum.OPEN},
                "index2": {"status": IndexStateEnum.OPEN},
                "index3": {"status": IndexStateEnum.OPEN},
            },
            {
                "acknowledged": True,
                "shards_acknowledged": True,
                "indices": {
                    "index1": {
                        "closed": True,
                    },
                    "index2": {
                        "closed": True,
                    },
                },  # represents the closed indices
            },
            False,
        ),
        # Check if only indices in backup-id=1 are closed
        (
            {
                1: {"indices": ["index1", "index2"]},
                2: {"indices": ["index3"]},
            },
            {
                "index1": {"status": IndexStateEnum.OPEN},
                "index2": {"status": IndexStateEnum.OPEN},
                "index3": {"status": IndexStateEnum.OPEN},
            },
            {
                "acknowledged": True,
                "shards_acknowledged": True,
                "indices": {
                    "index1": {
                        "closed": True,
                    },
                    "index2": {
                        "closed": True,
                    },
                },  # represents the closed indices
            },
            False,
        ),
        # Check if already closed indices are skipped
        (
            {
                1: {"indices": ["index1", "index2"]},
                2: {"indices": ["index3"]},
            },
            {
                "index1": {"status": IndexStateEnum.OPEN},
                "index2": {"status": IndexStateEnum.CLOSED},
                "index3": {"status": IndexStateEnum.OPEN},
            },
            {
                "acknowledged": True,
                "shards_acknowledged": True,
                "indices": {
                    "index1": {
                        "closed": True,
                    },
                },  # represents the closed indices
            },
            False,
        ),
        # Represents an error where index2 is not closed
        (
            {1: {"indices": ["index1", "index2"]}},
            {
                "index1": {"status": IndexStateEnum.OPEN},
                "index2": {"status": IndexStateEnum.OPEN},
                "index3": {"status": IndexStateEnum.OPEN},
            },
            {
                "acknowledged": True,
                "shards_acknowledged": True,
                "indices": {
                    "index1": {
                        "closed": True,
                    },
                    "index2": {
                        "closed": False,
                    },
                },  # represents the closed indices
            },
            True,
        ),
        # Represents an error where request failed
        (
            {1: {"indices": ["index1", "index2"]}},
            {
                "index1": {"status": IndexStateEnum.OPEN},
                "index2": {"status": IndexStateEnum.OPEN},
                "index3": {"status": IndexStateEnum.OPEN},
            },
            {"acknowledged": True, "shards_acknowledged": True, "indices": {}},
            True,
        ),
        # Represents an error where request failed
        (
            {1: {"indices": ["index1", "index2"]}},
            {
                "index1": {"status": IndexStateEnum.OPEN},
                "index2": {"status": IndexStateEnum.OPEN},
                "index3": {"status": IndexStateEnum.OPEN},
            },
            {
                "acknowledged": False,
            },
            True,
        ),
    ],
)
def test_close_indices_if_needed(
    harness, mock_request, list_backup_response, cluster_state, req_response, exception_raised
):
    harness.charm.backup.backup_manager.list_backups = MagicMock(return_value=list_backup_response)
    charms.opensearch.v0.opensearch_backups.ClusterState.indices = MagicMock(
        return_value=cluster_state
    )
    mock_request.return_value = req_response
    try:
        idx = harness.charm.backup.backup_manager.close_indices_if_needed(1)
    except OpenSearchError as e:
        assert isinstance(e, OpenSearchRestoreIndexClosingError) and exception_raised
    else:
        idx = {
            i
            for i in list_backup_response[1]["indices"]
            if (i in cluster_state.keys() and cluster_state[i]["status"] != IndexStateEnum.CLOSED)
        }
        mock_request.assert_called_with(
            "POST",
            f"{','.join(idx)}/_close",
            payload={
                "ignore_unavailable": "true",
            },
            retries=6,
            timeout=10,
        )


@pytest.mark.parametrize(
    "test_type,s3_units,snapshot_status,is_leader,apply_config_exc",
    [
        (
            "s3-still-units-present",
            ["some_unit"],  # This is a dummy value, so we trigger the .units check
            None,
            True,
            None,
        ),
        (
            "snapshot-in-progress",
            None,
            BackupServiceState.SNAPSHOT_IN_PROGRESS,
            True,
            None,
        ),
        (
            "apply-config-error",
            None,
            BackupServiceState.SUCCESS,
            True,
            OpenSearchPluginError("Error"),
        ),
        # Using this test case so we validate that a non-leader unit goes through
        # and eventually calls apply_config
        (
            "apply-config-error-not-leader",
            None,
            BackupServiceState.SUCCESS,
            True,
            OpenSearchPluginError("Error"),
        ),
        (
            "success",
            None,
            BackupServiceState.SUCCESS,
            True,
            None,
        ),
    ],
)
@patch("charms.opensearch.v0.opensearch_backups.BackupManager.check_snapshot_status")
def test_on_s3_broken_steps(
    check_snapshot_status,
    harness,
    test_type,
    s3_units,
    snapshot_status,
    is_leader,
    apply_config_exc,
):
    relation = MagicMock()
    relation.units = s3_units
    harness.charm.model.get_relation = MagicMock(return_value=relation)
    event = MagicMock()
    event.relation_name = "s3-credentials"
    harness.charm.backup._execute_s3_broken_calls = MagicMock()
    harness.charm.plugin_manager.apply_config = (
        MagicMock(side_effect=apply_config_exc) if apply_config_exc else MagicMock()
    )
    check_snapshot_status.return_value = snapshot_status
    harness.charm.unit.is_leader = MagicMock(return_value=is_leader)
    harness.charm.plugin_manager.get_plugin = MagicMock()
    harness.charm.plugin_manager.status = MagicMock(return_value=PluginState.ENABLED)
    harness.charm.status.set = MagicMock()
    harness.charm.backup.backup_manager.clean = MagicMock()

    # Call the method
    harness.charm.backup._on_backup_disable(event)

    if test_type == "s3-still-units-present":
        event.defer.assert_called()
        harness.charm.backup.backup_manager.clean.assert_not_called()
    elif test_type == "snapshot-in-progress":
        event.defer.assert_called()
        harness.charm.status.set.assert_any_call(MaintenanceStatus(BackupInDisabling))
        harness.charm.status.set.assert_any_call(WaitingStatus(BackupDeferRelBrokenAsInProgress))
        harness.charm.backup.backup_manager.clean.assert_not_called()
    elif test_type == "apply-config-error" or test_type == "apply-config-error-not-leader":
        event.defer.assert_called()
        harness.charm.status.set.assert_any_call(MaintenanceStatus(BackupInDisabling))
        harness.charm.backup.backup_manager.clean.assert_called_once()
    elif test_type == "success":
        event.defer.assert_not_called()
        harness.charm.status.set.assert_any_call(MaintenanceStatus(BackupInDisabling))
        harness.charm.backup.backup_manager.clean.assert_called_once()


@patch(
    "charms.opensearch.v0.opensearch_base_charm.OpenSearchPeerClustersManager.deployment_desc",
    return_value=create_deployment_desc(),
)
@patch_wait_fixed()
class TestBackups(unittest.TestCase):
    maxDiff = None

    @patch(
        "charms.opensearch.v0.opensearch_backups.OpenSearchBackupBase.active_relation",
        new_callable=PropertyMock,
        return_value=S3_RELATION,
    )
    def setUp(self, _) -> None:
        self.harness = Harness(OpenSearchOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        with patch(
            "charms.opensearch.v0.opensearch_base_charm.OpenSearchPeerClustersManager.deployment_desc",
            return_value=create_deployment_desc(),
        ):
            self.harness.begin()

            self.charm = self.harness.charm
            # Override the config to simulate the TestPlugin
            # As config.yml does not exist, the setup below simulates it
            self.charm.plugin_manager._charm_config = self.harness.model._config
            self.plugin_manager = self.charm.plugin_manager
            # Override the ConfigExposedPlugins
            charms.opensearch.v0.opensearch_plugin_manager.ConfigExposedPlugins = {
                "repository-s3": {
                    "class": OpenSearchS3Plugin,
                    "config": None,
                    "relation": "s3-credentials",
                },
            }
            self.charm.opensearch.is_started = MagicMock(return_value=True)
            self.charm.health.apply = MagicMock(return_value=HealthColors.GREEN)
            # Mock retrials to speed up tests
            charms.opensearch.v0.opensearch_backups.wait_fixed = MagicMock(
                return_value=tenacity.wait.wait_fixed(0.1)
            )
            self.charm.status = MagicMock()

            # Replace some unused methods that will be called as part of set_leader with mock
            self.charm._put_admin_user = MagicMock()
            self.charm._put_kibanaserver_user = MagicMock()
            self.charm._put_or_update_internal_user_leader = MagicMock()
            self.peer_id = self.harness.add_relation(PeerRelationName, "wazuh-indexer")
            self.harness.set_leader(is_leader=True)

        # Relate and run first check
        with patch(
            "charms.opensearch.v0.opensearch_plugin_manager.OpenSearchPluginManager.run"
        ) as mock_pm_run:
            self.s3_rel_id = self.harness.add_relation(S3_RELATION, "s3-integrator")
            self.harness.add_relation_unit(self.s3_rel_id, "s3-integrator/0")
            mock_pm_run.assert_not_called()

    def test_on_list_backups_action(self, _):
        event = MagicMock()
        event.params = {"output": "table"}
        self.charm.backup._list_backups = MagicMock(return_value={"backup1": {"state": "SUCCESS"}})
        self.charm.backup._generate_backup_list_output = MagicMock(
            return_value="backup1 | finished"
        )
        self.charm.backup._on_list_backups_action(event)
        event.set_results.assert_called_with({"backups": "backup1 | finished"})

    def test_on_list_backups_action_in_json_format(self, _):
        event = MagicMock()
        event.params = {"output": "json"}
        self.charm.backup.backup_manager.list_backups = MagicMock(
            return_value={"backup1": {"state": "SUCCESS"}}
        )
        self.charm.backup._generate_backup_list_output = MagicMock(
            return_value="backup1 | finished"
        )
        self.charm.backup._on_list_backups_action(event)
        event.set_results.assert_called_with({"backups": '{"backup1": {"state": "SUCCESS"}}'})

    @patch("charms.opensearch.v0.opensearch_distro.OpenSearchDistribution.request")
    def test_is_restore_complete(self, _, mock_request):
        rel = MagicMock()
        rel.data = {self.charm.app: {"restore_in_progress": "index1,index2"}}
        self.charm.model.get_relation = MagicMock(return_value=rel)
        mock_request.return_value = {
            "index1": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
            "index2": {"shards": [{"type": "SNAPSHOT", "stage": "DONE"}]},
            "index3": {"shards": [{"type": "PRIMARY", "stage": "DONE"}]},
        }
        result = self.charm.backup.backup_manager.is_restore_in_progress()
        self.assertFalse(result)

    @patch("charms.opensearch.v0.opensearch_backups.OpenSearchS3Backup.apply_api_config_if_needed")
    @patch("charms.opensearch.v0.opensearch_plugin_manager.OpenSearchPluginManager.apply_config")
    @patch("charms.opensearch.v0.opensearch_distro.OpenSearchDistribution.request")
    @patch("charms.opensearch.v0.opensearch_backups.BackupManager.clean")
    @patch("charms.opensearch.v0.opensearch_plugin_manager.OpenSearchPluginManager.status")
    def test_relation_broken(
        self,
        mock_status,
        mock_execute_s3_broken_calls,
        mock_request,
        mock_apply_config,
        _,
        __,
    ) -> None:
        """Tests broken relation unit."""
        mock_request.side_effects = [
            # list of returns for each call
            # 1st request: _check_snapshot_status
            # Return a response with SUCCESS in:
            {"SUCCESS"},
        ]
        mock_status.return_value = PluginState.ENABLED
        self.harness.remove_relation_unit(self.s3_rel_id, "s3-integrator/0")
        self.harness.remove_relation(self.s3_rel_id)
        mock_execute_s3_broken_calls.assert_called_once()
        assert (
            mock_apply_config.call_args[0][0].__dict__
            == OpenSearchPluginConfig(
                config_entries={},
                secret_entries={
                    "s3.client.default.access_key": None,
                    "s3.client.default.secret_key": None,
                },
            ).__dict__
        )

    def test_format_backup_list(self, _):
        """Tests the format of the backup list."""
        self.charm.opensearch.request = MagicMock(
            return_value={
                "snapshots": [
                    {"snapshot": "2023-01-01T00:00:00Z", "state": "SUCCESS", "indices": []},
                    {"snapshot": "2023-01-01T00:10:00Z", "state": "FAILED", "indices": []},
                    {"snapshot": "2023-01-01T00:20:00Z", "state": "IN_PROGRESS", "indices": []},
                ]
            }
        )
        backups = self.charm.backup.backup_manager.list_backups()
        self.assertEqual(
            self.charm.backup._generate_backup_list_output(backups), LIST_BACKUPS_TRIAL
        )

    @patch("charms.opensearch.v0.opensearch_backups.datetime")
    @patch("charms.opensearch.v0.opensearch_distro.OpenSearchDistribution.request")
    def test_on_create_backup_action_success(self, mock_request, mock_time, _):
        event = MagicMock()
        mock_time.now().strftime.return_value = "2023-01-01T00:00:00Z"
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=True)
        self.charm.backup.is_backup_in_progress = MagicMock(return_value=False)
        self.charm.backup.get_service_status = MagicMock(return_value="Backup completed.")
        self.charm.backup._on_create_backup_action(event)
        assert mock_request.call_args[0][0] == "PUT"
        assert (
            mock_request.call_args[0][1]
            == f"_snapshot/{S3_REPOSITORY}/2023-01-01t00:00:00z?wait_for_completion=false"
        )
        event.set_results.assert_called_with(
            {"backup-id": "2023-01-01T00:00:00Z", "status": "Backup is running."}
        )

    def test_on_create_backup_action_failure(self, _):
        event = MagicMock()
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=False)
        self.charm.backup._on_create_backup_action(event)
        event.fail.assert_called_with("Failed: backup service is not configured")

    @patch("charms.opensearch.v0.opensearch_distro.OpenSearchDistribution.request")
    def test_on_create_backup_action_exception(self, mock_request, _):
        event = MagicMock()
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=True)
        self.charm.backup.backup_manager.is_backup_in_progress = MagicMock(return_value=False)
        mock_request.side_effect = OpenSearchHttpError(
            response_text="Internal Server Error", response_code=500
        )
        self.charm.backup._on_create_backup_action(event)
        event.fail.assert_called_with(
            "Failed with exception: HTTP error self.response_code=500\nself.response_text='Internal Server Error'"
        )

    def test_on_restore_backup_action(self, _):
        """Runs the entire restore backup action successfully."""
        event = MagicMock()
        event.params = {"backup-id": "2023-01-01T00:00:00Z"}

        # Mocking helper methods
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=True)
        self.charm.backup.backup_manager.is_restore_in_progress = MagicMock(return_value=False)
        self.charm.backup.backup_manager.is_backup_available_for_restore = MagicMock(
            return_value=True
        )
        self.charm.backup.backup_manager.close_indices_if_needed = MagicMock(return_value=set())
        self.charm.backup.backup_manager.restore = MagicMock(
            return_value={"shards": {"successful": 1, "total": 1}}
        )
        self.charm.backup.get_service_status = MagicMock(return_value="success")
        self.charm.status = MagicMock()

        # Run the action
        self.charm.backup._on_restore_backup_action(event)

        event.fail.assert_not_called()
        event.set_results.assert_called_once_with(
            {
                "backup-id": "2023-01-01T00:00:00Z",
                "status": "Restore is complete",
                "closed-indices": "set()",
            }
        )
        self.charm.status.set.assert_called_once_with(MaintenanceStatus(RestoreInProgress))
        self.charm.status.clear.assert_called_once_with(RestoreInProgress)
        self.charm.backup.backup_manager.close_indices_if_needed.assert_called_once_with(
            "2023-01-01T00:00:00Z"
        )
        self.charm.backup.backup_manager.restore.assert_called_once_with("2023-01-01T00:00:00Z")

    def test_on_restore_backup_action_backup_service_not_configured(self, _):
        # Mocking helper method
        event = MagicMock()
        event.params = {"backup-id": "2023-01-01T00:00:00Z"}

        self.charm.status = MagicMock()
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=False)
        self.charm.backup._close_indices_if_needed = MagicMock(return_value=set())
        self.charm.backup._restore = MagicMock()
        # Run the action and first checks
        self.charm.backup._on_restore_backup_action(event)
        self.charm.backup._close_indices_if_needed.assert_not_called()
        self.charm.backup._restore.assert_not_called()
        # Status checks
        self.charm.status.set.assert_not_called()
        self.charm.status.clear.assert_not_called()
        # Eent checks
        event.fail.assert_called_once_with("Failed: backup service is not configured yet")
        event.set_results.assert_not_called()

    def test_on_restore_backup_action_previous_restore_in_progress(self, _):
        event = MagicMock()
        event.params = {"backup-id": "2023-01-01T00:00:00Z"}
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=True)
        self.charm.backup._close_indices_if_needed = MagicMock(return_value=set())
        self.charm.backup.backup_manager.is_idle = MagicMock(return_value=False)
        self.charm.backup._restore = MagicMock()
        self.charm.status = MagicMock()

        self.charm.backup._on_restore_backup_action(event)
        self.charm.status.set.assert_not_called()
        self.charm.status.clear.assert_not_called()
        self.charm.backup._close_indices_if_needed.assert_not_called()
        self.charm.backup._restore.assert_not_called()
        event.fail.assert_called_once_with("Failed: backup or restore is still in progress")
        event.set_results.assert_not_called()

    def test_on_restore_backup_action_backup_id_not_available(self, _):
        event = MagicMock()
        event.params = {"backup-id": "2023-01-01T00:00:00Z"}
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=True)
        self.charm.backup.backup_manager.close_indices_if_needed = MagicMock(return_value=set())
        self.charm.backup.backup_manager.is_restore_in_progress = MagicMock(return_value=False)
        self.charm.backup.backup_manager.is_backup_available_for_restore = MagicMock(
            return_value=False
        )
        self.charm.backup.backup_manager.restore = MagicMock()
        self.charm.status = MagicMock()

        self.charm.backup._on_restore_backup_action(event)
        self.charm.status.set.assert_not_called()
        self.charm.status.clear.assert_not_called()
        self.charm.backup.backup_manager.close_indices_if_needed.assert_not_called()
        self.charm.backup.backup_manager.restore.assert_not_called()
        event.fail.assert_called_once_with("Failed: no backup-id 2023-01-01T00:00:00Z")
        event.set_results.assert_not_called()

    def test_on_restore_backup_action_restore_failed(self, _):
        event = MagicMock()
        event.params = {"backup-id": "2023-01-01T00:00:00Z"}
        self.charm.backup.backup_manager.is_set = MagicMock(return_value=True)
        self.charm.backup.backup_manager.close_indices_if_needed = MagicMock(return_value=set())
        self.charm.backup.backup_manager.is_restore_in_progress = MagicMock(return_value=False)
        self.charm.backup.backup_manager.is_backup_available_for_restore = MagicMock(
            return_value=True
        )
        self.charm.backup.backup_manager.restore = MagicMock(
            side_effect=OpenSearchRestoreCheckError("_restore: unexpected response")
        )
        self.charm.status = MagicMock()

        self.charm.backup._on_restore_backup_action(event)
        event.fail.assert_called_once_with("Failed: _restore: unexpected response")
        self.charm.status.set.assert_called_once_with(MaintenanceStatus(RestoreInProgress))
        self.charm.status.clear.assert_called_once_with(RestoreInProgress)
        self.charm.backup.backup_manager.close_indices_if_needed.assert_called_once_with(
            "2023-01-01T00:00:00Z"
        )
        self.charm.backup.backup_manager.restore.assert_called_once_with("2023-01-01T00:00:00Z")
        event.set_results.assert_not_called()
