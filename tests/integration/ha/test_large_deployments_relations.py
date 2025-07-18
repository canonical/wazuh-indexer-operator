#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time

import pytest
from charms.opensearch.v0.constants_charm import PClusterNoRelation, TLSRelationMissing
from pytest_operator.plugin import OpsTest

from ..helpers import CONFIG_OPTS, MODEL_CONFIG, get_leader_unit_ip
from ..helpers_deployments import wait_until
from ..tls.test_tls import TLS_CERTIFICATES_APP_NAME, TLS_STABLE_CHANNEL
from .continuous_writes import ContinuousWrites
from .helpers import all_nodes
from .test_horizontal_scaling import IDLE_PERIOD

logger = logging.getLogger(__name__)

REL_ORCHESTRATOR = "peer-cluster-orchestrator"
REL_PEER = "peer-cluster"

MAIN_APP = "opensearch-main"
FAILOVER_APP = "opensearch-failover"
DATA_APP = "opensearch-data"
INVALID_APP = "opensearch-invalid"

CLUSTER_NAME = "log-app"
INVALID_CLUSTER_NAME = "timeseries"

APP_UNITS = {MAIN_APP: 3, FAILOVER_APP: 3, DATA_APP: 2, INVALID_APP: 1}


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy(ops_test: OpsTest, charm, series) -> None:
    """Build and deploy one unit of OpenSearch."""
    # it is possible for users to provide their own cluster for HA testing.
    # Hence, check if there is a pre-existing cluster.
    await ops_test.model.set_config(MODEL_CONFIG)

    # Deploy TLS Certificates operator.
    config = {"ca-common-name": "CN_CA"}
    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        ),
        ops_test.model.deploy(
            charm,
            application_name=MAIN_APP,
            num_units=3,
            series=series,
            config={"cluster_name": CLUSTER_NAME} | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            charm,
            application_name=FAILOVER_APP,
            num_units=3,
            series=series,
            config={"cluster_name": CLUSTER_NAME, "init_hold": True} | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            charm,
            application_name=DATA_APP,
            num_units=2,
            series=series,
            config={"cluster_name": CLUSTER_NAME, "init_hold": True, "roles": "data.hot,ml"}
            | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            charm,
            application_name=INVALID_APP,
            num_units=1,
            series=series,
            config={"cluster_name": INVALID_CLUSTER_NAME, "init_hold": True, "roles": "data.cold"}
            | CONFIG_OPTS,
        ),
    )

    # wait until the TLS operator is ready
    await wait_until(
        ops_test,
        apps=[TLS_CERTIFICATES_APP_NAME],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units={TLS_CERTIFICATES_APP_NAME: 1},
        idle_period=IDLE_PERIOD,
    )

    # confirm all apps are blocked because NO TLS relation established
    await wait_until(
        ops_test,
        apps=list(APP_UNITS.keys()),
        apps_full_statuses={
            MAIN_APP: {"blocked": [TLSRelationMissing]},
            FAILOVER_APP: {"blocked": [PClusterNoRelation]},
            DATA_APP: {"blocked": [PClusterNoRelation]},
            INVALID_APP: {"blocked": [PClusterNoRelation]},
        },
        units_full_statuses={
            MAIN_APP: {"units": {"blocked": [TLSRelationMissing]}},
            FAILOVER_APP: {"units": {"active": []}},
            DATA_APP: {"units": {"active": []}},
            INVALID_APP: {"units": {"active": []}},
        },
        wait_for_exact_units={app: units for app, units in APP_UNITS.items()},
        idle_period=IDLE_PERIOD,
        timeout=1800,
    )


@pytest.mark.abort_on_fail
async def test_invalid_conditions(ops_test: OpsTest) -> None:
    """Check invalid conditions under different states."""
    # integrate an app with the main-orchestrator when TLS is not related to the provider
    await ops_test.model.integrate(f"{FAILOVER_APP}:{REL_PEER}", f"{MAIN_APP}:{REL_ORCHESTRATOR}")
    await wait_until(
        ops_test,
        apps=[MAIN_APP, FAILOVER_APP],
        apps_full_statuses={
            MAIN_APP: {"blocked": [TLSRelationMissing]},
            FAILOVER_APP: {
                "waiting": ["TLS not fully configured in related 'main-orchestrator'."]
            },
        },
        units_full_statuses={
            MAIN_APP: {"units": {"blocked": [TLSRelationMissing]}},
            FAILOVER_APP: {"units": {"blocked": [TLSRelationMissing]}},
        },
        wait_for_exact_units={
            MAIN_APP: APP_UNITS[MAIN_APP],
            FAILOVER_APP: APP_UNITS[FAILOVER_APP],
        },
        idle_period=IDLE_PERIOD,
        timeout=1800,
    )

    # integrate TLS to all applications
    for app in [MAIN_APP, FAILOVER_APP, DATA_APP, INVALID_APP]:
        await ops_test.model.integrate(app, TLS_CERTIFICATES_APP_NAME)

    await wait_until(
        ops_test,
        apps=[MAIN_APP, FAILOVER_APP, DATA_APP, INVALID_APP],
        apps_full_statuses={
            MAIN_APP: {"active": []},
            FAILOVER_APP: {"active": []},
            DATA_APP: {"blocked": [PClusterNoRelation]},
            INVALID_APP: {"blocked": [PClusterNoRelation]},
        },
        units_statuses=["active"],
        wait_for_exact_units={app: units for app, units in APP_UNITS.items()},
        idle_period=IDLE_PERIOD,
        timeout=1800,
    )

    c_writes = ContinuousWrites(ops_test, app=MAIN_APP)
    await c_writes.start()
    time.sleep(120)
    await c_writes.stop()

    # fetch nodes, we should have 6 nodes (main + failover)-orchestrators
    leader_unit_ip = await get_leader_unit_ip(ops_test, app=MAIN_APP)
    nodes = await all_nodes(ops_test, leader_unit_ip, app=MAIN_APP)
    assert len(nodes) == 6, f"Wrong node count. Expecting 6 online nodes, found: {len(nodes)}."

    # integrate cluster with different name
    await ops_test.model.integrate(f"{INVALID_APP}:{REL_PEER}", f"{MAIN_APP}:{REL_ORCHESTRATOR}")
    await wait_until(
        ops_test,
        apps=[MAIN_APP, INVALID_APP],
        apps_full_statuses={
            MAIN_APP: {"active": []},
            INVALID_APP: {
                "blocked": ["Cannot relate 2 clusters with different 'cluster_name' values."]
            },
        },
        units_statuses=["active"],
        wait_for_exact_units={MAIN_APP: APP_UNITS[MAIN_APP], INVALID_APP: APP_UNITS[INVALID_APP]},
        idle_period=IDLE_PERIOD,
        timeout=1800,
    )

    # delete the invalid app name
    await ops_test.model.remove_application(
        INVALID_APP, block_until_done=True, force=True, destroy_storage=True, no_wait=True
    )


@pytest.mark.abort_on_fail
async def test_large_deployment_fully_formed(
    ops_test: OpsTest, c_writes: ContinuousWrites, c_writes_runner
) -> None:
    """Test that under optimal conditions all the nodes form the same big cluster."""
    await ops_test.model.integrate(f"{DATA_APP}:{REL_PEER}", f"{MAIN_APP}:{REL_ORCHESTRATOR}")
    await ops_test.model.integrate(f"{DATA_APP}:{REL_PEER}", f"{FAILOVER_APP}:{REL_ORCHESTRATOR}")

    await wait_until(
        ops_test,
        apps=[MAIN_APP, FAILOVER_APP, DATA_APP],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units={
            app: units for app, units in APP_UNITS.items() if app != INVALID_APP
        },
        idle_period=IDLE_PERIOD,
        timeout=1800,
    )

    # fetch nodes, we should have 8 nodes (main + failover)-orchestrators + 2 data nodes
    leader_unit_ip = await get_leader_unit_ip(ops_test, app=MAIN_APP)
    nodes = await all_nodes(ops_test, leader_unit_ip, app=MAIN_APP)
    assert len(nodes) == 8, f"Wrong node count. Expecting 8 online nodes, found: {len(nodes)}."

    # check the roles
    auto_gen_roles = ["cluster_manager", "data", "ingest", "ml"]
    data_roles = ["data", "ml"]
    for app, node_count in [(MAIN_APP, 3), (FAILOVER_APP, 3), (DATA_APP, 2)]:
        current_app_nodes = [
            node for node in nodes if node.app.id == f"{ops_test.model.uuid}/{app}"
        ]
        assert (
            len(current_app_nodes) == node_count
        ), f"Wrong count for {app}:{len(current_app_nodes)} - expected:{node_count}"

        roles = current_app_nodes[0].roles
        temperature = current_app_nodes[0].temperature
        if app in [MAIN_APP, FAILOVER_APP]:
            assert sorted(roles) == sorted(
                auto_gen_roles
            ), f"Wrong roles for {app}:{roles} - expected:{auto_gen_roles}"
            assert temperature is None, f"Wrong temperature for {app}:{roles} - expected:None"
        else:
            assert sorted(roles) == sorted(
                data_roles
            ), f"Wrong roles for {app}:{roles} - expected:{data_roles}"
            assert (
                temperature == "hot"
            ), f"Wrong temperature for {app}:{temperature} - expected:hot"
