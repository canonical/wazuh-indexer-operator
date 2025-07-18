#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for the OpenSearch charm with backups and restores.

This test suite will test backup and restore functionality of the OpenSearch charm
against every cloud provider currently supported. Tests are separated into groups
that falls in 2x categories:
* Per cloud provider tests: backup, restore, remove-readd relation and disaster recovery
* All cloud providers tests: build, deploy, test expected API errors and switch configs
                             between the clouds to ensure config changes are working as expected

The latter test group is called "all". The former is a set of groups, each corresponding to a
different cloud.
"""

import asyncio
import logging
import os
import random
import string
import subprocess
import time
import uuid
from datetime import datetime
from typing import Dict

import boto3
import pytest
from azure.storage.blob import BlobServiceClient
from charms.opensearch.v0.constants_charm import (
    OPENSEARCH_BACKUP_ID_FORMAT,
    BackupRelShouldNotExist,
    BackupSetupFailed,
)
from charms.opensearch.v0.opensearch_backups import S3_REPOSITORY
from pytest_operator.plugin import OpsTest

from ..ha.continuous_writes import ContinuousWrites
from ..ha.test_horizontal_scaling import IDLE_PERIOD
from ..helpers import (
    APP_NAME,
    CONFIG_OPTS,
    MODEL_CONFIG,
    get_leader_unit_id,
    get_leader_unit_ip,
    http_request,
    run_action,
)
from ..helpers_deployments import get_application_units, wait_until
from ..tls.test_tls import TLS_CERTIFICATES_APP_NAME, TLS_STABLE_CHANNEL
from .helpers import (
    add_juju_secret,
    app_name,
    assert_continuous_writes_consistency,
    assert_continuous_writes_increasing,
    assert_restore_indices_and_compare_consistency,
    assert_start_and_check_continuous_writes,
    create_backup,
    list_backups,
    restore,
)
from .helpers_data import index_docs_count

logger = logging.getLogger(__name__)


ALL_GROUPS = {
    (cloud_name, deploy_type): pytest.param(
        cloud_name,
        deploy_type,
        id=f"{cloud_name}-{deploy_type}",
        marks=[
            pytest.mark.group(id=f"{cloud_name}-{deploy_type}"),
        ],
    )
    for cloud_name in ["microceph"]  # Wazuh is only supported on microcepth
    for deploy_type in ["large", "small"]
}

ALL_DEPLOYMENTS_ALL_CLOUDS = list(ALL_GROUPS.values())
SMALL_DEPLOYMENTS_ALL_CLOUDS = [
    ALL_GROUPS[(cloud, "small")] for cloud in ["microceph"]  # Wazuh is only supported on microceph
]
LARGE_DEPLOYMENTS_ALL_CLOUDS = [
    ALL_GROUPS[(cloud, "large")] for cloud in ["microceph"]  # Wazuh is only supported on microceph
]


S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "1/stable"
S3_RELATION = "s3-credentials"
AZURE_INTEGRATOR = "azure-storage-integrator"
AZURE_INTEGRATOR_CHANNEL = "latest/edge"
AZURE_RELATION = "azure-credentials"

TIMEOUT = 20 * 60
BackupsPath = f"wazuh-indexer/{uuid.uuid4()}"


# We use this global variable to track the current relation of:
#    backup-id <-> continuous-writes index document count
# We use this global variable then to restore each backup on full DR scenario.
cwrites_backup_doc_count = {}


# Keeps track of the current continuous_writes object that we are using.
# This is relevant for the case where we have a test failure and we need to clean
# the cluster
global_cwrites = None


@pytest.fixture(scope="function")
async def force_clear_cwrites_index():
    """Force clear the global cwrites_backup_doc_count."""
    global global_cwrites
    try:
        if global_cwrites:
            await global_cwrites.clear()
    except Exception:
        pass


@pytest.fixture(scope="session")
def cloud_configs(microceph: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    # Figure out the address of the LXD host itself, where tests are executed
    # this is where microceph will be installed.
    ip = subprocess.check_output(["hostname", "-I"]).decode().split()[0]
    results = {
        "microceph": {
            "endpoint": f"http://{ip}",
            "bucket": microceph.bucket,
            "path": BackupsPath,
            "region": "default",
        },
    }
    if os.environ["AWS_ACCESS_KEY"]:
        results["aws"] = {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": BackupsPath,
            "region": "us-east-1",
        }
    if os.environ["AZURE_SECRET_KEY"]:
        results["azure"] = {
            "connection-protocol": "abfss",
            "container": "data-charms-testing",
            "path": BackupsPath,
        }
    return results


@pytest.fixture(scope="session")
def cloud_credentials(microceph: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    """Read cloud credentials."""
    results = {
        "microceph": {
            "access-key": microceph.access_key_id,
            "secret-key": microceph.secret_access_key,
        },
    }
    if os.environ["AWS_ACCESS_KEY"]:
        results["aws"] = {
            "access-key": os.environ["AWS_ACCESS_KEY"],
            "secret-key": os.environ["AWS_SECRET_KEY"],
        }
    if os.environ["AZURE_SECRET_KEY"]:
        results["azure"] = {
            "secret-key": os.environ["AZURE_SECRET_KEY"],
            "storage-account": os.environ["AZURE_STORAGE_ACCOUNT"],
        }
    return results


@pytest.fixture(scope="session", autouse=True)
def remove_backups(  # noqa C901
    # ops_test: OpsTest,
    cloud_configs: Dict[str, Dict[str, str]],
    cloud_credentials: Dict[str, Dict[str, str]],
):
    """Remove previously created backups from the cloud-corresponding bucket."""
    yield

    logger.info("Cleaning backups from cloud buckets")
    for cloud_name, config in cloud_configs.items():
        if cloud_name not in cloud_credentials:
            continue

        if cloud_name == "aws" or cloud_name == "microceph":
            if (
                "access-key" not in cloud_credentials[cloud_name]
                or "secret-key" not in cloud_credentials[cloud_name]
            ):
                # This cloud has not been used in this test run
                continue

            session = boto3.session.Session(
                aws_access_key_id=cloud_credentials[cloud_name]["access-key"],
                aws_secret_access_key=cloud_credentials[cloud_name]["secret-key"],
                region_name=config["region"],
            )
            s3 = session.resource("s3", endpoint_url=config["endpoint"])
            bucket = s3.Bucket(config["bucket"])

            # Some of our runs target only a single cloud, therefore, they will
            # raise errors on the other cloud's bucket. We catch and log them.
            try:
                bucket.objects.filter(Prefix=f"{BackupsPath}/").delete()
            except Exception as e:
                logger.warning(f"Failed to clean up backups: {e}")

        if cloud_name == "azure":
            if (
                "secret-key" not in cloud_credentials[cloud_name]
                or "storage-account" not in cloud_credentials[cloud_name]
            ):
                # This cloud has not been used in this test run
                continue

            storage_account = cloud_credentials[cloud_name]["storage-account"]
            secret_key = cloud_credentials[cloud_name]["secret-key"]
            connection_string = f"DefaultEndpointsProtocol=https;AccountName={storage_account};AccountKey={secret_key};EndpointSuffix=core.windows.net"
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            container_client = blob_service_client.get_container_client(config["container"])

            # List and delete blobs with the specified prefix
            blobs_to_delete = container_client.list_blobs(name_starts_with=BackupsPath)

            try:
                for blob in blobs_to_delete:
                    container_client.delete_blob(blob.name)
            except Exception as e:
                logger.warning(f"Failed to clean up backups: {e}")


async def _configure_s3(
    ops_test: OpsTest, config: Dict[str, str], credentials: Dict[str, str], app_name: str = None
) -> None:
    await ops_test.model.applications[S3_INTEGRATOR].set_config(config)
    s3_integrator_id = (await get_application_units(ops_test, S3_INTEGRATOR))[
        0
    ].id  # We redeploy s3-integrator once, so we may have anything >=0 as id
    await run_action(
        ops_test,
        s3_integrator_id,
        "sync-s3-credentials",
        params=credentials,
        app=S3_INTEGRATOR,
    )

    apps = [S3_INTEGRATOR] if app_name is None else [S3_INTEGRATOR, app_name]
    await ops_test.model.wait_for_idle(
        apps=apps,
        status="active",
        timeout=TIMEOUT,
    )


async def _configure_azure(
    ops_test: OpsTest,
    config: Dict[str, str],
    credentials: Dict[str, str],
    app_name: str = None,
) -> None:
    await ops_test.model.applications[AZURE_INTEGRATOR].set_config(config)
    logger.info("Adding Juju secret for secret-key config option for azure-storage-integrator")

    # Creates a new secret for each test
    local_label = "".join(random.choice(string.ascii_letters) for _ in range(10))
    credentials_secret_uri = await add_juju_secret(
        ops_test,
        AZURE_INTEGRATOR,
        local_label,
        {"secret-key": credentials["secret-key"]},
    )
    logger.info(
        f"Juju secret for secret-key config option for azure-storage-integrator added. Secret URI: {credentials_secret_uri}"
    )

    configuration_parameters = {
        "storage-account": credentials["storage-account"],
        "credentials": credentials_secret_uri,
    }
    # apply new configuration options
    logger.info("Setting up configuration for azure-storage-integrator charm...")
    await ops_test.model.applications[AZURE_INTEGRATOR].set_config(configuration_parameters)

    apps = [AZURE_INTEGRATOR] if app_name is None else [AZURE_INTEGRATOR, app_name]
    await ops_test.model.wait_for_idle(
        apps=apps,
        status="active",
        timeout=TIMEOUT,
    )


@pytest.mark.parametrize("cloud_name,deploy_type", SMALL_DEPLOYMENTS_ALL_CLOUDS)
@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_small_deployment_build_and_deploy(
    ops_test: OpsTest, charm, series, cloud_name: str, deploy_type: str
) -> None:
    """Build and deploy an HA cluster of OpenSearch and corresponding S3 integration."""
    if await app_name(ops_test):
        return

    await ops_test.model.set_config(MODEL_CONFIG)
    # Deploy TLS Certificates operator.
    config = {"ca-common-name": "CN_CA"}

    backup_integrator = AZURE_INTEGRATOR if cloud_name == "azure" else S3_INTEGRATOR
    backup_integrator_channel = (
        AZURE_INTEGRATOR_CHANNEL if cloud_name == "azure" else S3_INTEGRATOR_CHANNEL
    )

    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        ),
        ops_test.model.deploy(backup_integrator, channel=backup_integrator_channel),
        ops_test.model.deploy(charm, num_units=3, series=series, config=CONFIG_OPTS),
    )

    # Relate it to OpenSearch to set up TLS.
    await ops_test.model.integrate(APP_NAME, TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[TLS_CERTIFICATES_APP_NAME, APP_NAME],
        status="active",
        timeout=1400,
        idle_period=IDLE_PERIOD,
    )
    # Credentials not set yet, this will move the opensearch to blocked state
    # Credentials are set per test scenario
    await ops_test.model.integrate(APP_NAME, backup_integrator)


@pytest.mark.parametrize("cloud_name,deploy_type", LARGE_DEPLOYMENTS_ALL_CLOUDS)
@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_large_deployment_build_and_deploy(
    ops_test: OpsTest, charm, series, cloud_name: str, deploy_type: str
) -> None:
    """Build and deploy a large deployment for OpenSearch.

    The following apps will be deployed:
    * main: the main orchestrator
    * failover: the failover orchestrator
    * opensearch (or APP_NAME): the data.hot node

    The data node is selected to adopt the "APP_NAME" value because it is the node which
    ContinuousWrites will later target its writes to.
    """
    if await app_name(ops_test):
        return

    await ops_test.model.set_config(MODEL_CONFIG)
    # Deploy TLS Certificates operator.
    tls_config = {"ca-common-name": "CN_CA"}

    main_orchestrator_conf = {
        "cluster_name": "backup-test",
        "init_hold": False,
        "roles": "cluster_manager,data",
    }
    failover_orchestrator_conf = {
        "cluster_name": "backup-test",
        "init_hold": True,
        "roles": "cluster_manager",
    }
    data_hot_conf = {"cluster_name": "backup-test", "init_hold": True, "roles": "data.hot"}

    backup_integrator = AZURE_INTEGRATOR if cloud_name == "azure" else S3_INTEGRATOR
    backup_integrator_channel = (
        AZURE_INTEGRATOR_CHANNEL if cloud_name == "azure" else S3_INTEGRATOR_CHANNEL
    )

    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=tls_config
        ),
        ops_test.model.deploy(backup_integrator, channel=backup_integrator_channel),
        ops_test.model.deploy(
            charm,
            application_name="main",
            num_units=1,
            series=series,
            config=main_orchestrator_conf | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            charm,
            application_name="failover",
            num_units=2,
            series=series,
            config=failover_orchestrator_conf | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME,
            num_units=1,
            series=series,
            config=data_hot_conf | CONFIG_OPTS,
        ),
    )

    # Large deployment setup
    await ops_test.model.integrate("main:peer-cluster-orchestrator", "failover:peer-cluster")
    await ops_test.model.integrate("main:peer-cluster-orchestrator", f"{APP_NAME}:peer-cluster")
    await ops_test.model.integrate(
        "failover:peer-cluster-orchestrator", f"{APP_NAME}:peer-cluster"
    )

    # TLS setup
    await ops_test.model.integrate("main", TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.integrate("failover", TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.integrate(APP_NAME, TLS_CERTIFICATES_APP_NAME)

    # Charms except s3-integrator should be active
    await wait_until(
        ops_test,
        apps=[TLS_CERTIFICATES_APP_NAME, "main", "failover", APP_NAME],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units={
            TLS_CERTIFICATES_APP_NAME: 1,
            "main": 1,
            "failover": 2,
            APP_NAME: 1,
        },
        idle_period=IDLE_PERIOD,
        timeout=3600,
    )

    # Credentials not set yet, this will move the opensearch to blocked state
    # Credentials are set per test scenario
    await ops_test.model.integrate("main", backup_integrator)


@pytest.mark.parametrize("cloud_name,deploy_type", LARGE_DEPLOYMENTS_ALL_CLOUDS)
@pytest.mark.abort_on_fail
async def test_large_setups_relations_with_misconfiguration(
    ops_test: OpsTest,
    cloud_name: str,
    deploy_type: str,
) -> None:
    """Tests the different blocked messages expected in large deployments."""
    if cloud_name == "azure":
        config = {
            "connection-protocol": "abfss",
            "container": "error",
            "path": "/",
        }
        credentials = {
            "storage-account": "error",
            "secret-key": "error",
        }
        await _configure_azure(ops_test=ops_test, config=config, credentials=credentials)
    else:
        config = {
            "endpoint": "http://localhost",
            "bucket": "error",
            "path": "/",
            "region": "default",
        }
        credentials = {
            "access-key": "error",
            "secret-key": "error",
        }
        await _configure_s3(ops_test=ops_test, config=config, credentials=credentials)

    await wait_until(
        ops_test,
        apps=["main"],
        apps_statuses=["blocked"],
        apps_full_statuses={"main": {"blocked": [BackupSetupFailed]}},
        idle_period=IDLE_PERIOD,
    )

    backup_integrator = AZURE_INTEGRATOR if cloud_name == "azure" else S3_INTEGRATOR
    backup_relation = AZURE_RELATION if cloud_name == "azure" else S3_RELATION

    # Now, relate failover cluster to backup-integrator and review the status
    await ops_test.model.integrate(f"failover:{backup_relation}", backup_integrator)
    await ops_test.model.integrate(f"{APP_NAME}:{backup_relation}", backup_integrator)
    await wait_until(
        ops_test,
        apps=["failover", APP_NAME],
        apps_statuses=["blocked"],
        apps_full_statuses={
            "failover": {"blocked": [BackupRelShouldNotExist]},
            APP_NAME: {"blocked": [BackupRelShouldNotExist]},
        },
        idle_period=IDLE_PERIOD,
    )

    # Reverting should return it to normal
    await ops_test.model.applications[APP_NAME].destroy_relation(
        f"{APP_NAME}:{backup_relation}", backup_integrator
    )
    await ops_test.model.applications["failover"].destroy_relation(
        f"failover:{backup_relation}", backup_integrator
    )

    await wait_until(
        ops_test,
        apps=["main"],
        apps_statuses=["blocked"],
        apps_full_statuses={"main": {"blocked": [BackupSetupFailed]}},
        idle_period=IDLE_PERIOD,
    )
    await wait_until(
        ops_test,
        apps=["failover", APP_NAME],
        apps_statuses=["active"],
        idle_period=IDLE_PERIOD,
    )


@pytest.mark.parametrize("cloud_name,deploy_type", ALL_DEPLOYMENTS_ALL_CLOUDS)
@pytest.mark.abort_on_fail
async def test_create_backup_and_restore(
    ops_test: OpsTest,
    c_writes: ContinuousWrites,
    c_writes_runner,
    cloud_configs: Dict[str, Dict[str, str]],
    cloud_credentials: Dict[str, Dict[str, str]],
    cloud_name: str,
    deploy_type: str,
) -> None:
    """Runs the backup process whilst writing to the cluster into 'noisy-index'."""
    app = (await app_name(ops_test) or APP_NAME) if deploy_type == "small" else "main"
    apps = [app] if deploy_type == "small" else [app, APP_NAME]
    leader_id = await get_leader_unit_id(ops_test, app=app)
    unit_ip = await get_leader_unit_ip(ops_test, app=app)
    config = cloud_configs[cloud_name]

    logger.info(f"Syncing credentials for {cloud_name}")
    if cloud_name == "azure":
        await _configure_azure(ops_test, config, cloud_credentials[cloud_name], app)
    else:
        await _configure_s3(ops_test, config, cloud_credentials[cloud_name], app)

    date_before_backup = datetime.utcnow()

    # Wait, we want to make sure the timestamps are different
    await asyncio.sleep(5)

    assert (
        datetime.strptime(
            backup_id := await create_backup(
                ops_test,
                leader_id,
                unit_ip=unit_ip,
                app=app,
            ),
            OPENSEARCH_BACKUP_ID_FORMAT,
        )
        > date_before_backup
    )
    # continuous writes checks
    await assert_continuous_writes_increasing(c_writes)
    await assert_continuous_writes_consistency(ops_test, c_writes, apps)
    await assert_restore_indices_and_compare_consistency(
        ops_test, app, leader_id, unit_ip, backup_id
    )
    global cwrites_backup_doc_count
    cwrites_backup_doc_count[backup_id] = await index_docs_count(
        ops_test,
        app,
        unit_ip,
        ContinuousWrites.INDEX_NAME,
    )


@pytest.mark.parametrize("cloud_name,deploy_type", ALL_DEPLOYMENTS_ALL_CLOUDS)
@pytest.mark.abort_on_fail
async def test_remove_and_readd_backup_relation(
    ops_test: OpsTest,
    c_writes: ContinuousWrites,
    c_writes_runner,
    cloud_configs: Dict[str, Dict[str, str]],
    cloud_credentials: Dict[str, Dict[str, str]],
    cloud_name: str,
    deploy_type: str,
) -> None:
    """Removes and re-adds the backup relation to test backup and restore."""
    app = (await app_name(ops_test) or APP_NAME) if deploy_type == "small" else "main"
    apps = [app] if deploy_type == "small" else [app, APP_NAME]

    leader_id: int = await get_leader_unit_id(ops_test, app=app)
    unit_ip: str = await get_leader_unit_ip(ops_test, app=app)
    config: Dict[str, str] = cloud_configs[cloud_name]

    backup_integrator = AZURE_INTEGRATOR if cloud_name == "azure" else S3_INTEGRATOR
    backup_relation = AZURE_RELATION if cloud_name == "azure" else S3_RELATION

    logger.info("Remove backup relation")
    # Remove relation
    await ops_test.model.applications[app].destroy_relation(
        backup_relation, f"{backup_integrator}:{backup_relation}"
    )
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        timeout=1400,
        idle_period=IDLE_PERIOD,
    )

    logger.info("Re-add backup credentials relation")
    await ops_test.model.integrate(app, backup_integrator)
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        timeout=1400,
        idle_period=IDLE_PERIOD,
    )

    logger.info(f"Syncing credentials for {cloud_name}")
    if cloud_name == "azure":
        await _configure_azure(ops_test, config, cloud_credentials[cloud_name], app)
    else:
        await _configure_s3(ops_test, config, cloud_credentials[cloud_name], app)

    date_before_backup = datetime.utcnow()

    # Wait, we want to make sure the timestamps are different
    await asyncio.sleep(5)

    assert (
        datetime.strptime(
            backup_id := await create_backup(
                ops_test,
                leader_id,
                unit_ip=unit_ip,
                app=app,
            ),
            OPENSEARCH_BACKUP_ID_FORMAT,
        )
        > date_before_backup
    )

    # continuous writes checks
    await assert_continuous_writes_increasing(c_writes)
    await assert_continuous_writes_consistency(ops_test, c_writes, apps)
    await assert_restore_indices_and_compare_consistency(
        ops_test, app, leader_id, unit_ip, backup_id
    )
    global cwrites_backup_doc_count
    cwrites_backup_doc_count[backup_id] = await index_docs_count(
        ops_test,
        app,
        unit_ip,
        ContinuousWrites.INDEX_NAME,
    )


@pytest.mark.parametrize("cloud_name,deploy_type", SMALL_DEPLOYMENTS_ALL_CLOUDS)
@pytest.mark.abort_on_fail
async def test_restore_to_new_cluster(
    ops_test: OpsTest,
    charm,
    series,
    cloud_configs: Dict[str, Dict[str, str]],
    cloud_credentials: Dict[str, Dict[str, str]],
    cloud_name: str,
    deploy_type: str,
    force_clear_cwrites_index,
) -> None:
    """Deletes the entire OpenSearch cluster and redeploys from scratch.

    Restores each of the previous backups we created and compare with their doc count.
    The cluster is considered healthy if:
    1) At each backup restored, check our track of doc count vs. current index count
    2) Try to write to that new index.
    """
    app = (await app_name(ops_test) or APP_NAME) if deploy_type == "small" else "main"
    backup_integrator = AZURE_INTEGRATOR if cloud_name == "azure" else S3_INTEGRATOR
    backup_integrator_channel = (
        AZURE_INTEGRATOR_CHANNEL if cloud_name == "azure" else S3_INTEGRATOR_CHANNEL
    )

    logging.info("Destroying the application")
    await asyncio.gather(
        ops_test.model.remove_application(backup_integrator, block_until_done=True),
        ops_test.model.remove_application(app, block_until_done=True),
        ops_test.model.remove_application(TLS_CERTIFICATES_APP_NAME, block_until_done=True),
    )

    logging.info("Deploying a new cluster")
    await ops_test.model.set_config(MODEL_CONFIG)
    # Deploy TLS Certificates operator.
    config = {"ca-common-name": "CN_CA"}

    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        ),
        ops_test.model.deploy(backup_integrator, channel=backup_integrator_channel),
        ops_test.model.deploy(charm, num_units=3, series=series, config=CONFIG_OPTS),
    )

    # Relate it to OpenSearch to set up TLS.
    await ops_test.model.integrate(app, TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[TLS_CERTIFICATES_APP_NAME, app],
        status="active",
        timeout=1400,
        idle_period=IDLE_PERIOD,
    )
    # Credentials not set yet, this will move the opensearch to blocked state
    # Credentials are set per test scenario
    await ops_test.model.integrate(app, backup_integrator)

    leader_id = await get_leader_unit_id(ops_test, app=app)
    unit_ip = await get_leader_unit_ip(ops_test, app=app)
    config: Dict[str, str] = cloud_configs[cloud_name]

    logger.info(f"Syncing credentials for {cloud_name}")
    if cloud_name == "azure":
        await _configure_azure(ops_test, config, cloud_credentials[cloud_name], app)
    else:
        await _configure_s3(ops_test, config, cloud_credentials[cloud_name], app)
    backups = await list_backups(ops_test, leader_id, app=app)

    global cwrites_backup_doc_count
    # We are expecting 2x backups available
    assert len(backups) == 2
    assert len(cwrites_backup_doc_count) == 2
    count = 0
    for backup_id in backups.keys():
        assert await restore(ops_test, backup_id, unit_ip, leader_id, app=app)
        count = await index_docs_count(ops_test, app, unit_ip, ContinuousWrites.INDEX_NAME)

        # Ensure we have the same doc count as we had on the original cluster
        assert count == cwrites_backup_doc_count[backup_id]

        # restart the continuous writes and check the cluster is still accessible post restore
        await assert_start_and_check_continuous_writes(ops_test, unit_ip, app)

    # Now, try a backup & restore with continuous writes
    logger.info("Final stage of DR test: try a backup & restore with continuous writes")
    writer: ContinuousWrites = ContinuousWrites(ops_test, app)

    # store the global cwrites object
    global global_cwrites
    global_cwrites = writer

    await writer.start()
    time.sleep(10)
    date_before_backup = datetime.utcnow()

    # Wait, we want to make sure the timestamps are different
    await asyncio.sleep(5)

    assert (
        datetime.strptime(
            backup_id := await create_backup(
                ops_test,
                leader_id,
                unit_ip=unit_ip,
                app=app,
            ),
            OPENSEARCH_BACKUP_ID_FORMAT,
        )
        > date_before_backup
    )

    # continuous writes checks
    await assert_continuous_writes_increasing(writer)
    await assert_continuous_writes_consistency(ops_test, writer, [app])
    # This assert assures we have taken a new backup, after the last restore from the original
    # cluster. That means the index is writable.
    await assert_restore_indices_and_compare_consistency(
        ops_test, app, leader_id, unit_ip, backup_id
    )
    # Clear the writer manually, as we are not using the conftest c_writes_runner to do so
    await writer.clear()


# -------------------------------------------------------------------------------------------
# Tests for the "allgroup" group
#
# This group will iterate over each cloud, update its credentials via config and rerun
# the backup and restore tests.
# -------------------------------------------------------------------------------------------


@pytest.mark.group(id="all")
@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_deploy_and_test_status(ops_test: OpsTest, charm, series) -> None:
    """Build, deploy and test status of an HA cluster of OpenSearch and corresponding backups.

    This test group will iterate over each cloud, update its credentials via config and rerun
    the backup and restore tests.
    """
    if await app_name(ops_test):
        return

    await ops_test.model.set_config(MODEL_CONFIG)
    # Deploy TLS Certificates operator.
    config = {"ca-common-name": "CN_CA"}
    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        ),
        ops_test.model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        ops_test.model.deploy(charm, num_units=3, series=series, config=CONFIG_OPTS),
    )

    # Relate it to OpenSearch to set up TLS.
    await ops_test.model.integrate(APP_NAME, TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[TLS_CERTIFICATES_APP_NAME, APP_NAME],
        status="active",
        timeout=1400,
        idle_period=IDLE_PERIOD,
    )
    # Credentials not set yet, this will move the opensearch to blocked state
    # Credentials are set per test scenario
    await ops_test.model.integrate(APP_NAME, S3_INTEGRATOR)


@pytest.mark.group(id="all")
@pytest.mark.abort_on_fail
async def test_repo_missing_message(ops_test: OpsTest) -> None:
    """Check the repo is missing error returned by OpenSearch.

    We use the message format to monitor the cluster status. We need to know if this
    message pattern changed between releases of OpenSearch.
    """
    app: str = (await app_name(ops_test)) or APP_NAME
    unit_ip = await get_leader_unit_ip(ops_test, app=app)
    resp = await http_request(
        ops_test, "GET", f"https://{unit_ip}:9200/_snapshot/{S3_REPOSITORY}", json_resp=True
    )
    logger.debug(f"Response: {resp}")
    assert resp["status"] == 404
    assert "repository_missing_exception" in resp["error"]["type"]


@pytest.mark.group(id="all")
@pytest.mark.abort_on_fail
async def test_wrong_s3_credentials(ops_test: OpsTest) -> None:
    """Check the repo is misconfigured."""
    app = (await app_name(ops_test)) or APP_NAME
    unit_ip = await get_leader_unit_ip(ops_test, app=app)

    config = {
        "endpoint": "http://localhost",
        "bucket": "error",
        "path": "/",
        "region": "default",
    }
    credentials = {
        "access-key": "error",
        "secret-key": "error",
    }

    # Not using _configure_s3 as this method will cause opensearch to block
    await ops_test.model.applications[S3_INTEGRATOR].set_config(config)
    await run_action(
        ops_test,
        0,
        "sync-s3-credentials",
        params=credentials,
        app=S3_INTEGRATOR,
    )
    await ops_test.model.wait_for_idle(
        apps=[S3_INTEGRATOR],
        status="active",
        timeout=TIMEOUT,
    )
    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["blocked"],
        units_statuses=["active", "blocked"],
        wait_for_exact_units=3,
        idle_period=30,
    )

    resp = await http_request(
        ops_test, "GET", f"https://{unit_ip}:9200/_snapshot/{S3_REPOSITORY}/_all", json_resp=True
    )
    logger.debug(f"Response: {resp}")
    assert resp["status"] == 500
    assert "repository_exception" in resp["error"]["type"]
    assert "Could not determine repository generation from root blobs" in resp["error"]["reason"]


@pytest.mark.group(id="all")
@pytest.mark.abort_on_fail
async def test_change_config_and_backup_restore(
    ops_test: OpsTest,
    cloud_configs: Dict[str, Dict[str, str]],
    cloud_credentials: Dict[str, Dict[str, str]],
    force_clear_cwrites_index,
) -> None:
    """Run for each cloud and update the cluster config."""
    app: str = (await app_name(ops_test)) or APP_NAME
    unit_ip: str = await get_leader_unit_ip(ops_test, app=app)
    leader_id: int = await get_leader_unit_id(ops_test, app=app)

    initial_count: int = 0
    for cloud_name in cloud_configs.keys():
        # Azure has no different config setups at this point
        if cloud_name == "azure":
            continue
        logger.debug(
            f"Index {ContinuousWrites.INDEX_NAME} has {initial_count} documents, starting there"
        )
        # Start the ContinuousWrites here instead of bringing as a fixture because we want to do
        # it for every cloud config we have and we have to stop it before restore, right down.
        writer: ContinuousWrites = ContinuousWrites(ops_test, app, initial_count=initial_count)

        # store the global cwrites object
        global global_cwrites
        global_cwrites = writer

        await writer.start()
        time.sleep(10)

        logger.info(f"Syncing credentials for {cloud_name}")
        config: Dict[str, str] = cloud_configs[cloud_name]
        await _configure_s3(ops_test, config, cloud_credentials[cloud_name], app)

        date_before_backup = datetime.utcnow()

        # Wait, we want to make sure the timestamps are different
        await asyncio.sleep(5)

        assert (
            datetime.strptime(
                backup_id := await create_backup(
                    ops_test,
                    leader_id,
                    unit_ip=unit_ip,
                ),
                OPENSEARCH_BACKUP_ID_FORMAT,
            )
            > date_before_backup
        )

        # continuous writes checks
        await assert_continuous_writes_increasing(writer)
        await assert_continuous_writes_consistency(ops_test, writer, [app])
        await assert_restore_indices_and_compare_consistency(
            ops_test, app, leader_id, unit_ip, backup_id
        )
        # Clear the writer manually, as we are not using the conftest c_writes_runner to do so
        await writer.clear()
