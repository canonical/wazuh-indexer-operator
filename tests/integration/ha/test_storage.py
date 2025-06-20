#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import subprocess
import time

import pytest
from pytest_operator.plugin import OpsTest

from ..ha.helpers import (
    app_name,
    assert_continuous_writes_increasing,
    storage_id,
    storage_type,
)
from ..ha.test_horizontal_scaling import IDLE_PERIOD
from ..helpers import APP_NAME, CONFIG_OPTS, MODEL_CONFIG, get_application_unit_ids
from ..helpers_deployments import wait_until
from ..tls.test_tls import TLS_CERTIFICATES_APP_NAME, TLS_STABLE_CHANNEL
from .continuous_writes import ContinuousWrites

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm, series) -> None:
    """Build and deploy one unit of OpenSearch."""
    # it is possible for users to provide their own cluster for HA testing.
    # Hence, check if there is a pre-existing cluster.
    if await app_name(ops_test):
        return

    await ops_test.model.set_config(MODEL_CONFIG)
    # this assumes the test is run on a lxd cloud
    await ops_test.model.create_storage_pool("opensearch-pool", "lxd")
    storage = {"opensearch-data": {"pool": "opensearch-pool", "size": 2048}}
    # Deploy TLS Certificates operator.
    config = {"ca-common-name": "CN_CA"}
    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        ),
        ops_test.model.deploy(
            charm, num_units=1, series=series, storage=storage, config=CONFIG_OPTS
        ),
    )

    # Relate it to OpenSearch to set up TLS.
    await ops_test.model.integrate(APP_NAME, TLS_CERTIFICATES_APP_NAME)
    await wait_until(
        ops_test,
        apps=[TLS_CERTIFICATES_APP_NAME, APP_NAME],
        apps_statuses=["active"],
        units_statuses=["active"],
        timeout=1000,
        idle_period=IDLE_PERIOD,
        wait_for_exact_units={
            TLS_CERTIFICATES_APP_NAME: 1,
            "opensearch": 1,
        },
    )
    assert len(ops_test.model.applications[APP_NAME].units) == 1


@pytest.mark.abort_on_fail
async def test_storage_reuse_after_scale_down(
    ops_test: OpsTest, c_writes: ContinuousWrites, c_writes_runner
):
    """Check storage is reused and data accessible after scaling down and up."""
    app = (await app_name(ops_test)) or APP_NAME

    if storage_type(ops_test, app) == "rootfs":
        pytest.skip(
            "reuse of storage can only be used on deployments with persistent storage not on rootfs deployments"
        )

    # scale up to 2 units
    await ops_test.model.applications[app].add_unit(count=1)
    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active"],
        units_statuses=["active"],
        timeout=1000,
        idle_period=IDLE_PERIOD,
        wait_for_exact_units={
            app: 2,
        },
    )

    writes_result = await c_writes.stop()

    # get unit info
    unit_id = get_application_unit_ids(ops_test, app)[1]
    unit_storage_id = storage_id(ops_test, app, unit_id)

    # create a testfile on the newly added unit to check if data in storage is persistent
    testfile = "/var/snap/wazuh-indexer/common/testfile"
    create_testfile_cmd = f"juju ssh {app}/{unit_id} -q sudo touch {testfile}"
    subprocess.run(create_testfile_cmd, shell=True)

    # scale-down to 1
    # app status might be blocked because after scaling down not all shards are assigned
    await ops_test.model.applications[app].destroy_unit(f"{app}/{unit_id}")
    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active", "blocked"],
        units_statuses=["active"],
        timeout=1000,
        idle_period=IDLE_PERIOD,
        wait_for_exact_units={
            app: 1,
        },
    )

    # add unit with storage attached
    add_unit_cmd = (
        f"add-unit {app} --model={ops_test.model.info.name} --attach-storage={unit_storage_id}"
    )
    return_code, _, _ = await ops_test.juju(*add_unit_cmd.split())
    assert return_code == 0, "Failed to add unit with storage"

    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active"],
        units_statuses=["active"],
        timeout=1000,
        idle_period=IDLE_PERIOD,
        wait_for_exact_units={
            app: 2,
        },
    )

    # check the storage of the new unit
    new_unit_id = get_application_unit_ids(ops_test, app)[1]
    new_unit_storage_id = storage_id(ops_test, app, new_unit_id)
    assert unit_storage_id == new_unit_storage_id, "Storage IDs mismatch."

    # check if data is also imported
    assert writes_result.count == (await c_writes.count())
    assert writes_result.max_stored_id == (await c_writes.max_stored_id())

    # check if the testfile is still there or was overwritten on installation
    check_testfile_cmd = f"juju ssh {app}/{new_unit_id} -q sudo ls {testfile}"
    assert testfile == subprocess.getoutput(check_testfile_cmd)


@pytest.mark.abort_on_fail
async def test_storage_reuse_after_scale_to_zero(
    ops_test: OpsTest, c_writes: ContinuousWrites, c_writes_runner
):
    """Check storage is reused and data accessible after scaling down and up."""
    app = (await app_name(ops_test)) or APP_NAME

    if storage_type(ops_test, app) == "rootfs":
        pytest.skip(
            "reuse of storage can only be used on deployments with persistent storage not on rootfs deployments"
        )

    writes_result = await c_writes.stop()

    # scale down to zero units in reverse order
    unit_ids = get_application_unit_ids(ops_test, app)
    storage_ids = {}
    for unit_id in unit_ids[::-1]:
        storage_ids[unit_id] = storage_id(ops_test, app, unit_id)
        await ops_test.model.applications[app].destroy_unit(f"{app}/{unit_id}")
        # give some time for removing each unit
        time.sleep(60)

    # using wait_until doesn't really work well here with 0 units
    await ops_test.model.wait_for_idle(
        # app status will not be active because after scaling down not all shards are assigned
        apps=[app],
        timeout=1000,
        wait_for_exact_units=0,
    )

    # scale up again
    for unit_id in unit_ids:
        add_unit_cmd = f"add-unit {app} --model={ops_test.model.info.name} --attach-storage={storage_ids[unit_id]}"
        return_code, _, _ = await ops_test.juju(*add_unit_cmd.split())
        assert return_code == 0, f"Failed to add unit with storage {storage_ids[unit_id]}"
        await ops_test.model.wait_for_idle(apps=[app], timeout=1000)

    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active"],
        units_statuses=["active"],
        timeout=1000,
        idle_period=IDLE_PERIOD,
        wait_for_exact_units={
            app: len(unit_ids),
        },
    )

    # check if data is also imported
    assert writes_result.count == (await c_writes.count())
    assert writes_result.max_stored_id == (await c_writes.max_stored_id())

    # restart continuous writes and check if they can be written
    await c_writes.start()
    time.sleep(30)
    await assert_continuous_writes_increasing(c_writes)


@pytest.mark.abort_on_fail
async def test_storage_reuse_in_new_cluster_after_app_removal(
    ops_test: OpsTest, charm, c_writes: ContinuousWrites, c_balanced_writes_runner
):
    """Check storage is reused and data accessible after removing app and deploying new cluster."""
    app = (await app_name(ops_test)) or APP_NAME

    if storage_type(ops_test, app) == "rootfs":
        pytest.skip(
            "reuse of storage can only be used on deployments with persistent storage not on rootfs deployments"
        )

    # scale-up to 3 to make it a cluster
    unit_ids = get_application_unit_ids(ops_test, app)
    if len(unit_ids) < 3:
        await ops_test.model.applications[app].add_unit(count=3 - len(unit_ids))

        await wait_until(
            ops_test,
            apps=[app],
            apps_statuses=["active"],
            units_statuses=["active"],
            timeout=1000,
            idle_period=IDLE_PERIOD,
            wait_for_exact_units={
                app: 3,
            },
        )
    else:
        # wait for enough data to be written
        time.sleep(60)

    writes_result = await c_writes.stop()

    # Scale down carefully to be able to identify which storage needs to be deployed to
    # the leader when scaling up again. This is to avoid stale metadata when reusing the
    # storage on a different cluster.
    storage_ids = []
    unit_ids = get_application_unit_ids(ops_test, app)

    # remember the current storage disks
    for unit_id in unit_ids:
        storage_ids.append(storage_id(ops_test, app, unit_id))

    # remove all but the first unit
    # this will trigger the remaining unit to become the leader if it wasn't already
    for unit_id in unit_ids[1:]:
        await ops_test.model.applications[app].destroy_unit(f"{app}/{unit_id}")

    # app status might be blocked because after scaling down not all shards are assigned
    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active", "blocked"],
        units_statuses=["active"],
        timeout=1000,
        wait_for_exact_units={
            app: 1,
        },
    )

    # remove the remaining unit and the entire application
    await ops_test.model.remove_application(app, block_until_done=True)

    # deploy new cluster, attaching the storage from the previous leader to the new leader
    deploy_cluster_with_storage_cmd = (
        f"deploy {charm} --model={ops_test.model.info.name} --attach-storage={storage_ids[0]}"
        " --config profile=testing"
    )
    return_code, _, _ = await ops_test.juju(*deploy_cluster_with_storage_cmd.split())
    assert return_code == 0, f"Failed to deploy app with storage {storage_ids[0]}"
    await ops_test.model.integrate(app, TLS_CERTIFICATES_APP_NAME)

    # wait for cluster to be deployed
    # app status might be blocked because not all shards are assigned
    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active", "blocked"],
        units_statuses=["active"],
        wait_for_exact_units=1,
        timeout=2400,
    )

    # add unit with storage attached
    for unit_storage_id in storage_ids[1:]:
        add_unit_cmd = (
            f"add-unit {app} --model={ops_test.model.info.name} --attach-storage={unit_storage_id}"
        )
        return_code, _, _ = await ops_test.juju(*add_unit_cmd.split())
        assert return_code == 0, f"Failed to add unit with storage {unit_storage_id}"

    # wait for new cluster to settle down
    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units=len(storage_ids),
        idle_period=IDLE_PERIOD,
        timeout=2400,
    )
    assert len(ops_test.model.applications[app].units) == len(storage_ids)

    # check if previous volumes are attached to the units of the new cluster
    new_storage_ids = []
    for unit_id in get_application_unit_ids(ops_test, app):
        new_storage_ids.append(storage_id(ops_test, app, unit_id))

    assert sorted(storage_ids) == sorted(new_storage_ids), "Storage IDs mismatch."

    # check if data is also imported
    assert writes_result.count == (await c_writes.count())
    assert writes_result.max_stored_id == (await c_writes.max_stored_id())

    # restart continuous writes and check if they can be written
    await c_writes.start()
    time.sleep(60)
    assert (await c_writes.count()) > 0, "Continuous writes not increasing"
