#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..ha.helpers import get_elected_cm_unit_id
from ..helpers import (
    APP_NAME,
    CONFIG_OPTS,
    MODEL_CONFIG,
    cluster_health,
    cluster_voting_config_exclusions,
    execute_update_status_manually,
    get_leader_unit_ip,
    set_watermark,
)
from ..helpers_deployments import wait_until
from ..tls.test_tls import TLS_CERTIFICATES_APP_NAME, TLS_STABLE_CHANNEL
from .continuous_writes import ContinuousWrites
from .helpers import (
    app_name,
    assert_continuous_writes_consistency,
    assert_continuous_writes_increasing,
)
from .test_horizontal_scaling import IDLE_PERIOD

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy(ops_test: OpsTest, charm, series) -> None:
    """Build and deploy one unit of OpenSearch."""
    # it is possible for users to provide their own cluster for HA testing.
    # Hence, check if there is a pre-existing cluster.
    if await app_name(ops_test):
        return

    # This test will manually issue update-status hooks, as we want to see the change in behavior
    # when applying `settle_voting` during start/stop and during update-status.
    MODEL_CONFIG["update-status-hook-interval"] = "360m"

    await ops_test.model.set_config(MODEL_CONFIG)

    # Deploy TLS Certificates operator.
    config = {"ca-common-name": "CN_CA"}
    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        ),
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
    assert len(ops_test.model.applications[APP_NAME].units) == 3

    # Set watermark
    await set_watermark(ops_test, app=APP_NAME)


@pytest.mark.abort_on_fail
async def test_scale_down(
    ops_test: OpsTest, c_writes: ContinuousWrites, c_0_repl_writes_runner
) -> None:
    """Tests the shutdown of a node, and see the voting exclusions to be applied.

    This test will remove the elected cluster manager.
    """
    app = (await app_name(ops_test)) or APP_NAME

    leader_unit_ip = await get_leader_unit_ip(ops_test, app=app)
    voting_exclusions = await cluster_voting_config_exclusions(ops_test, unit_ip=leader_unit_ip)
    assert len(voting_exclusions) == 0

    init_count = len(ops_test.model.applications[app].units)
    while init_count > 1:
        # find unit currently elected cluster_manager
        elected_cm_unit_id = await get_elected_cm_unit_id(ops_test, leader_unit_ip)

        # remove the service in the chosen unit
        await ops_test.model.applications[app].destroy_unit(f"{app}/{elected_cm_unit_id}")

        await wait_until(
            ops_test,
            apps=[app],
            units_statuses=["active"],
            wait_for_exact_units=init_count - 1,
            idle_period=IDLE_PERIOD,
        )

        # Check voting exclusions
        leader_unit_ip = await get_leader_unit_ip(ops_test, app=app)
        voting_exclusions = await cluster_voting_config_exclusions(
            ops_test, unit_ip=leader_unit_ip
        )
        assert len(voting_exclusions) == 0
        # Test the cleanup() method
        await execute_update_status_manually(ops_test, app=app)
        voting_exclusions = await cluster_voting_config_exclusions(
            ops_test, unit_ip=leader_unit_ip
        )
        assert len(voting_exclusions) == 0

        # get initial cluster health - expected to be all good: green
        leader_unit_ip = await get_leader_unit_ip(ops_test, app=app)
        cluster_health_resp = await cluster_health(
            ops_test, leader_unit_ip, wait_for_green_first=True
        )
        assert cluster_health_resp["status"] == "green"
        assert cluster_health_resp["unassigned_shards"] == 0

        # Make sure we continue to be writable
        await assert_continuous_writes_increasing(c_writes)

        init_count = len(ops_test.model.applications[app].units)

    # continuous writes checks
    await assert_continuous_writes_consistency(ops_test, c_writes, [app])


@pytest.mark.abort_on_fail
async def test_scale_back_up(
    ops_test: OpsTest, c_writes: ContinuousWrites, c_0_repl_writes_runner
) -> None:
    """Tests the scaling back to 3x node-cluster and see the voting exclusions to be applied."""
    app = (await app_name(ops_test)) or APP_NAME

    init_count = len(ops_test.model.applications[app].units)
    while init_count < 3:
        # find unit currently elected cluster_manager
        leader_unit_ip = await get_leader_unit_ip(ops_test, app=app)

        # remove the service in the chosen unit
        await ops_test.model.applications[app].add_unit(count=1)
        await wait_until(
            ops_test,
            apps=[app],
            units_statuses=["active"],
            wait_for_exact_units=init_count + 1,
            idle_period=IDLE_PERIOD,
        )

        # get initial cluster health - expected to be all good: green
        leader_unit_ip = await get_leader_unit_ip(ops_test, app=app)
        cluster_health_resp = await cluster_health(
            ops_test, leader_unit_ip, wait_for_green_first=True
        )
        assert cluster_health_resp["status"] == "green"
        assert cluster_health_resp["unassigned_shards"] == 0

        # Adding new units should not trigger a new voting exclusion
        voting_exclusions = await cluster_voting_config_exclusions(
            ops_test, unit_ip=leader_unit_ip
        )
        assert len(voting_exclusions) == 0

        # Make sure we continue to be writable
        await assert_continuous_writes_increasing(c_writes)

        init_count = len(ops_test.model.applications[app].units)

    # Make sure update status is executed and fixes the voting exclusions
    await execute_update_status_manually(ops_test, app=app)
    voting_exclusions = await cluster_voting_config_exclusions(ops_test, unit_ip=leader_unit_ip)
    assert len(voting_exclusions) == 0

    # continuous writes checks
    await assert_continuous_writes_consistency(ops_test, c_writes, [app])


@pytest.mark.abort_on_fail
async def test_gracefully_cluster_remove(ops_test: OpsTest) -> None:
    """Tests removing the entire application at once."""
    app = (await app_name(ops_test)) or APP_NAME

    # This removal must not leave units in error.
    # We will block until it is finished.
    await asyncio.gather(
        ops_test.model.remove_application(app, block_until_done=True),
    )
