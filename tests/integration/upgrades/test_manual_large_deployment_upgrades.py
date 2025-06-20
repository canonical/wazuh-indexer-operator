#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..ha.continuous_writes import ContinuousWrites
from ..ha.helpers import assert_continuous_writes_consistency
from ..helpers import APP_NAME, CONFIG_OPTS, IDLE_PERIOD, MODEL_CONFIG, run_action
from ..helpers_deployments import get_application_units, wait_until
from ..tls.test_tls import TLS_CERTIFICATES_APP_NAME, TLS_STABLE_CHANNEL

logger = logging.getLogger(__name__)


OPENSEARCH_ORIGINAL_CHARM_NAME = "wazuh-indexer"
OPENSEARCH_INITIAL_CHANNEL = "4.9/edge"
OPENSEARCH_MAIN_APP_NAME = "main"
OPENSEARCH_FAILOVER_APP_NAME = "failover"


charm = None


WORKLOAD = {
    APP_NAME: 3,
    OPENSEARCH_FAILOVER_APP_NAME: 2,
    OPENSEARCH_MAIN_APP_NAME: 1,
}


@pytest.mark.skip(reason="Fix with DPE-4528")
@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_large_deployment_deploy_original_charm(ops_test: OpsTest, series) -> None:
    """Build and deploy the charm for large deployment tests."""
    await ops_test.model.set_config(MODEL_CONFIG)
    # Deploy TLS Certificates operator.
    tls_config = {"ca-common-name": "CN_CA"}

    main_orchestrator_conf = {
        "cluster_name": "backup-test",
        "init_hold": False,
        "roles": "cluster_manager",
    }
    failover_orchestrator_conf = {
        "cluster_name": "backup-test",
        "init_hold": True,
        "roles": "cluster_manager",
    }
    data_hot_conf = {"cluster_name": "backup-test", "init_hold": True, "roles": "data.hot"}

    await asyncio.gather(
        ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=tls_config
        ),
        ops_test.model.deploy(
            OPENSEARCH_ORIGINAL_CHARM_NAME,
            application_name=OPENSEARCH_MAIN_APP_NAME,
            num_units=WORKLOAD[OPENSEARCH_MAIN_APP_NAME],
            series=series,
            channel=OPENSEARCH_INITIAL_CHANNEL,
            config=main_orchestrator_conf | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            OPENSEARCH_ORIGINAL_CHARM_NAME,
            application_name=OPENSEARCH_FAILOVER_APP_NAME,
            num_units=WORKLOAD[OPENSEARCH_FAILOVER_APP_NAME],
            series=series,
            channel=OPENSEARCH_INITIAL_CHANNEL,
            config=failover_orchestrator_conf | CONFIG_OPTS,
        ),
        ops_test.model.deploy(
            OPENSEARCH_ORIGINAL_CHARM_NAME,
            application_name=APP_NAME,
            num_units=WORKLOAD[APP_NAME],
            series=series,
            channel=OPENSEARCH_INITIAL_CHANNEL,
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
        apps=[
            TLS_CERTIFICATES_APP_NAME,
            OPENSEARCH_MAIN_APP_NAME,
            OPENSEARCH_FAILOVER_APP_NAME,
            APP_NAME,
        ],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units={
            TLS_CERTIFICATES_APP_NAME: 1,
            OPENSEARCH_MAIN_APP_NAME: WORKLOAD[OPENSEARCH_MAIN_APP_NAME],
            OPENSEARCH_FAILOVER_APP_NAME: WORKLOAD[OPENSEARCH_FAILOVER_APP_NAME],
            APP_NAME: WORKLOAD[APP_NAME],
        },
        idle_period=IDLE_PERIOD,
        timeout=3600,
    )


@pytest.mark.skip(reason="Fix with DPE-4528")
@pytest.mark.abort_on_fail
async def test_manually_upgrade_to_local(
    ops_test: OpsTest, c_writes: ContinuousWrites, c_writes_runner, charm
) -> None:
    """Test upgrade from usptream to currently locally built version."""
    units = await get_application_units(ops_test, OPENSEARCH_MAIN_APP_NAME)
    leader_id = [u.id for u in units if u.is_leader][0]

    action = await run_action(
        ops_test,
        leader_id,
        "pre-upgrade-check",
        app=OPENSEARCH_MAIN_APP_NAME,
    )
    assert action.status == "completed"

    logger.info("Build charm locally")

    async with ops_test.fast_forward():
        for app, unit_count in WORKLOAD.items():
            application = ops_test.model.applications[app]
            units = await get_application_units(ops_test, app)
            leader_id = [u.id for u in units if u.is_leader][0]

            logger.info(f"Refresh app {app}, leader {leader_id}")

            await application.refresh(path=charm)
            logger.info("Refresh is over, waiting for the charm to settle")

            if unit_count == 1:
                # Upgrade already happened for this unit, wait for idle and continue
                await wait_until(
                    ops_test,
                    apps=[app],
                    apps_statuses=["active"],
                    units_statuses=["active"],
                    idle_period=IDLE_PERIOD,
                    timeout=3600,
                )
                logger.info(f"Upgrade of app {app} finished")
                continue

            await wait_until(
                ops_test,
                apps=[app],
                apps_statuses=["blocked"],
                units_statuses=["active"],
                wait_for_exact_units={
                    app: unit_count,
                },
                idle_period=120,
                timeout=3600,
            )
            # Resume the upgrade
            action = await run_action(
                ops_test,
                leader_id,
                "resume-upgrade",
                app=app,
            )
            assert action.status == "completed"
            logger.info(f"resume-upgrade: {action}")

            await wait_until(
                ops_test,
                apps=[app],
                apps_statuses=["active"],
                units_statuses=["active"],
                idle_period=IDLE_PERIOD,
                timeout=3600,
            )
            logger.info(f"Upgrade of app {app} finished")

    # continuous writes checks
    await assert_continuous_writes_consistency(
        ops_test,
        c_writes,
        [APP_NAME, OPENSEARCH_MAIN_APP_NAME],
    )
