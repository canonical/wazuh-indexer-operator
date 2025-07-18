# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base class for the OpenSearch Operators."""
import abc
import logging
import random
import typing
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from charms.opensearch.v0.constants_charm import (
    PERFORMANCE_PROFILE,
    AdminUser,
    AdminUserInitProgress,
    AdminUserNotConfigured,
    CertsExpirationError,
    ClientRelationName,
    ClusterHealthRed,
    ClusterHealthUnknown,
    COSPort,
    COSRelationName,
    COSUser,
    OpenSearchSystemUsers,
    OpenSearchUsers,
    PClusterNoDataNode,
    PeerClusterRelationName,
    PeerRelationName,
    PluginConfigChangeError,
    PluginConfigCheck,
    RequestUnitServiceOps,
    SecurityIndexInitProgress,
    ServiceIsStopping,
    ServiceStartError,
    ServiceStopped,
    TLSCaRotation,
    TLSNewCertsRequested,
    TLSNotFullyConfigured,
    TLSRelationBrokenError,
    TLSRelationMissing,
    WaitingToStart,
)
from charms.opensearch.v0.constants_tls import CertType
from charms.opensearch.v0.helper_charm import Status, all_units, format_unit_name
from charms.opensearch.v0.helper_cluster import ClusterTopology, Node
from charms.opensearch.v0.helper_networking import get_host_ip, units_ips
from charms.opensearch.v0.helper_security import (
    cert_expiration_remaining_hours,
    generate_hashed_password,
    generate_password,
)
from charms.opensearch.v0.models import (
    DeploymentDescription,
    DeploymentType,
    PerformanceType,
)
from charms.opensearch.v0.opensearch_backups import backup
from charms.opensearch.v0.opensearch_config import OpenSearchConfig
from charms.opensearch.v0.opensearch_distro import OpenSearchDistribution
from charms.opensearch.v0.opensearch_exceptions import (
    OpenSearchCmdError,
    OpenSearchError,
    OpenSearchHAError,
    OpenSearchHttpError,
    OpenSearchMissingError,
    OpenSearchNotFullyReadyError,
    OpenSearchStartError,
    OpenSearchStartTimeoutError,
    OpenSearchStopError,
)
from charms.opensearch.v0.opensearch_fixes import OpenSearchFixes
from charms.opensearch.v0.opensearch_health import HealthColors, OpenSearchHealth
from charms.opensearch.v0.opensearch_internal_data import RelationDataStore, Scope
from charms.opensearch.v0.opensearch_keystore import OpenSearchKeystoreNotReadyError
from charms.opensearch.v0.opensearch_locking import OpenSearchNodeLock
from charms.opensearch.v0.opensearch_nodes_exclusions import OpenSearchExclusions
from charms.opensearch.v0.opensearch_oauth import OAuthHandler
from charms.opensearch.v0.opensearch_peer_clusters import (
    OpenSearchPeerClustersManager,
    StartMode,
)
from charms.opensearch.v0.opensearch_performance_profile import OpenSearchPerformance
from charms.opensearch.v0.opensearch_plugin_manager import OpenSearchPluginManager
from charms.opensearch.v0.opensearch_plugins import OpenSearchPluginError
from charms.opensearch.v0.opensearch_relation_peer_cluster import (
    OpenSearchPeerClusterProvider,
    OpenSearchPeerClusterRequirer,
)
from charms.opensearch.v0.opensearch_relation_provider import OpenSearchProvider
from charms.opensearch.v0.opensearch_secrets import OpenSearchSecrets
from charms.opensearch.v0.opensearch_tls import OLD_CA_ALIAS, OpenSearchTLS
from charms.opensearch.v0.opensearch_users import (
    OpenSearchUserManager,
    OpenSearchUserMgmtError,
)
from charms.tls_certificates_interface.v3.tls_certificates import (
    CertificateAvailableEvent,
)
from ops.charm import (
    ActionEvent,
    CharmBase,
    ConfigChangedEvent,
    LeaderElectedEvent,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
    StartEvent,
    StorageDetachingEvent,
    UpdateStatusEvent,
)
from ops.framework import EventBase, EventSource
from ops.model import BlockedStatus, MaintenanceStatus, WaitingStatus

import lifecycle
import upgrade

# The unique Charmhub library identifier, never change it
LIBID = "cba015bae34642baa1b6bb27bb35a2f7"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2


SERVICE_MANAGER = "service"
STORAGE_NAME = "opensearch-data"


logger = logging.getLogger(__name__)


class _StartOpenSearch(EventBase):
    """Attempt to acquire lock & start OpenSearch.

    This event will be deferred until OpenSearch starts.
    """

    def __init__(self, handle, *, ignore_lock=False, after_upgrade=False):
        super().__init__(handle)
        # Only used for force upgrade
        self.ignore_lock = ignore_lock
        self.after_upgrade = after_upgrade

    def snapshot(self) -> Dict[str, Any]:
        return {"ignore_lock": self.ignore_lock, "after_upgrade": self.after_upgrade}

    def restore(self, snapshot: Dict[str, Any]):
        self.ignore_lock = snapshot["ignore_lock"]
        self.after_upgrade = snapshot["after_upgrade"]


class _RestartOpenSearch(EventBase):
    """Attempt to acquire lock & restart OpenSearch.

    This event will be deferred until OpenSearch stops. Then, `_StartOpenSearch` will be emitted.
    """


class _UpgradeOpenSearch(_StartOpenSearch):
    """Attempt to acquire lock & upgrade OpenSearch.

    This event will be deferred until OpenSearch stops. Then, the snap will be upgraded and
    `_StartOpenSearch` will be emitted.
    """

    def __init__(self, handle, *, ignore_lock=False):
        super().__init__(handle, ignore_lock=ignore_lock)


class OpenSearchBaseCharm(CharmBase, abc.ABC):
    """Base class for OpenSearch charms."""

    _start_opensearch_event = EventSource(_StartOpenSearch)
    _restart_opensearch_event = EventSource(_RestartOpenSearch)
    _upgrade_opensearch_event = EventSource(_UpgradeOpenSearch)

    def __init__(self, *args, distro: Type[OpenSearchDistribution] = None):
        super().__init__(*args)
        # Instantiate before registering other event observers
        self._unit_lifecycle = lifecycle.Unit(self, subordinated_relation_endpoint_names=None)

        if distro is None:
            raise ValueError("The type of the opensearch distro must be specified.")

        self.opensearch = distro(self, PeerRelationName)
        self.opensearch_peer_cm = OpenSearchPeerClustersManager(self)
        self.opensearch_config = OpenSearchConfig(self.opensearch)
        self.opensearch_exclusions = OpenSearchExclusions(self)
        self.opensearch_fixes = OpenSearchFixes(self)

        self.peers_data = RelationDataStore(self, PeerRelationName)
        self.secrets = OpenSearchSecrets(self, PeerRelationName)
        self.tls = OpenSearchTLS(
            self, PeerRelationName, self.opensearch.paths.jdk, self.opensearch.paths.certs
        )
        self.oauth = OAuthHandler(self)
        self.status = Status(self)
        self.health = OpenSearchHealth(self)
        self.node_lock = OpenSearchNodeLock(self)

        self.plugin_manager = OpenSearchPluginManager(self)

        self.backup = backup(self)

        self.user_manager = OpenSearchUserManager(self)
        self.opensearch_provider = OpenSearchProvider(self)
        self.peer_cluster_provider = OpenSearchPeerClusterProvider(self)
        self.peer_cluster_requirer = OpenSearchPeerClusterRequirer(self)

        self.framework.observe(self._start_opensearch_event, self._start_opensearch)
        self.framework.observe(self._restart_opensearch_event, self._restart_opensearch)
        self.framework.observe(self._upgrade_opensearch_event, self._upgrade_opensearch)

        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

        self.framework.observe(
            self.on[PeerRelationName].relation_created, self._on_peer_relation_created
        )
        self.framework.observe(
            self.on[PeerRelationName].relation_joined, self._on_peer_relation_joined
        )
        self.framework.observe(
            self.on[PeerRelationName].relation_changed, self._on_peer_relation_changed
        )
        self.framework.observe(
            self.on[PeerRelationName].relation_departed, self._on_peer_relation_departed
        )
        self.framework.observe(
            self.on[STORAGE_NAME].storage_detaching, self._on_opensearch_data_storage_detaching
        )

        self.framework.observe(self.on.set_password_action, self._on_set_password_action)
        self.framework.observe(self.on.get_password_action, self._on_get_password_action)

        self.cos_integration = COSAgentProvider(
            self,
            relation_name=COSRelationName,
            metrics_endpoints=[],
            scrape_configs=self._scrape_config,
            refresh_events=[
                self.on.config_changed,
                self.on.set_password_action,
                self.on.secret_changed,
                self.on[PeerRelationName].relation_changed,
                self.on[PeerClusterRelationName].relation_changed,
            ],
            metrics_rules_dir="./src/alert_rules/prometheus",
            log_slots=["opensearch:logs"],
        )

        self.performance_profile = OpenSearchPerformance(self)
        # Ensure that only one instance of the `_on_peer_relation_changed` handler exists
        # in the deferred event queue
        self._is_peer_rel_changed_deferred = False

    @property
    @abc.abstractmethod
    def _upgrade(self) -> typing.Optional[upgrade.Upgrade]:
        pass

    @property
    def upgrade_in_progress(self):
        """Whether upgrade is in progress"""
        if not self._upgrade:
            return False
        return self._upgrade.in_progress

    @abc.abstractmethod
    def _reconcile_upgrade(self, _=None):
        pass

    def _on_leader_elected(self, event: LeaderElectedEvent):
        """Handle leader election event."""
        # We check if the current unit is the leader, in case where the leader elected event
        # was deferred, then juju proceeded with a new leader election, and this now deferred-event
        # was emitted in a non-juju leader unit (previous leader)
        if not self.unit.is_leader():
            return

        if self.peers_data.get(Scope.APP, "security_index_initialised", False):
            # Leader election event happening after a previous leader got killed
            if not self.opensearch.is_node_up():
                event.defer()
                return

            if self.health.apply(unit=False) in [
                HealthColors.UNKNOWN,
                HealthColors.YELLOW_TEMP,
            ]:
                event.defer()

            self._compute_and_broadcast_updated_topology(self._get_nodes(True))
            return

        # TODO: check if cluster can start independently

        # User config is currently in a default state, which contains multiple insecure default
        # users. Purge the user list before initialising the users the charm requires.
        self._purge_users()

        if not (deployment_desc := self.opensearch_peer_cm.deployment_desc()):
            event.defer()
            return

        if deployment_desc.typ != DeploymentType.MAIN_ORCHESTRATOR:
            return

        if not self.peers_data.get(Scope.APP, "admin_user_initialized"):
            self.status.set(MaintenanceStatus(AdminUserInitProgress))

        # Restore purged system users in local `internal_users.yml`
        # with corresponding credentials
        for user in OpenSearchSystemUsers:
            self._put_or_update_internal_user_leader(user, update=False)

        self.status.clear(AdminUserInitProgress)

    def _on_start(self, event: StartEvent):  # noqa C901
        """Triggered when on start. Set the right node role."""

        def cleanup():
            if self.peers_data.get(Scope.APP, "security_index_initialised"):
                # in the case where it was on WaitingToStart status, event got deferred
                # and the service started in between, put status back to active
                self.status.clear(WaitingToStart)
                self.status.clear(PClusterNoDataNode)

            # cleanup bootstrap conf in the node if existing
            if self.peers_data.get(Scope.UNIT, "bootstrap_contributor"):
                self._cleanup_bootstrap_conf_if_applies()

        if self.opensearch.is_node_up():
            cleanup()
            return

        elif (
            self.peers_data.get(Scope.UNIT, "started")
            and "cluster_manager" in self.opensearch.roles
            and not self.opensearch.is_service_started()
        ):
            # This logic will only be triggered if the service has started (i.e. "started")
            # if we had a "start" hook (i.e. the actual machine has rebooted)
            # and we are a cluster_manager with the service down
            # After these conditions are met, then we can simply restart the service.
            logger.debug(
                "Start hook: snap already installed and service should be up, but it is not. Restarting it..."
            )

            # We had a reboot in this node.
            # We execute the same logic as above:
            cleanup()

            # Now, reissue a restart: we should not have stopped in the first place
            # as "started" flag is still set to True.
            # We do not wait for the 200 return, as maybe more than one unit is coming back
            try:
                self.opensearch.start_service_only()
                # We're done here, we can return
                return
            except OpenSearchStartError as e:
                logger.warning(f"Machine restart detected but error at service start with: {e}")
                # Defer and retry later
                event.defer()
                return
            except OpenSearchMissingError:
                # This is unlike to happen, unless the snap has been manually removed
                logger.error("Service previously started but now misses the snap.")
                return

        # apply the directives computed and emitted by the peer cluster manager
        if not self._apply_peer_cm_directives_and_check_if_can_start():
            event.defer()
            return

        if not self.is_admin_user_configured() or not self.tls.is_fully_configured():
            if not self.model.get_relation("certificates"):
                status = BlockedStatus(TLSRelationMissing)
            else:
                status = MaintenanceStatus(
                    TLSNotFullyConfigured
                    if self.is_admin_user_configured()
                    else AdminUserNotConfigured
                )
            self.status.set(status)
            event.defer()
            return

        self.status.clear(AdminUserNotConfigured)
        self.status.clear(TLSNotFullyConfigured)
        self.status.clear(TLSRelationMissing)

        # Since system users are initialized, we should take them to local internal_users.yml
        # Leader should be done already
        if not self.unit.is_leader():
            self._purge_users()
            for user in OpenSearchSystemUsers:
                self._put_or_update_internal_user_unit(user)

        # configure clients auth
        self.opensearch_config.set_client_auth()

        deployment_desc = self.opensearch_peer_cm.deployment_desc()
        # only start the main orchestrator if a data node is available
        # this allows for "cluster-manager-only" nodes in large deployments
        # workflow documentation:
        # no "data" role in deployment desc -> start gets deferred
        # when "data" node joins -> start cluster-manager via _on_peer_cluster_relation_changed
        # cluster-manager notifies "data" node via refresh of peer cluster relation data
        # "data" node starts and initializes security index
        if (
            deployment_desc.typ == DeploymentType.MAIN_ORCHESTRATOR
            and not deployment_desc.start == StartMode.WITH_GENERATED_ROLES
            and "data" not in deployment_desc.config.roles
        ):
            self.status.set(BlockedStatus(PClusterNoDataNode))
            event.defer()
            return

        # request the start of OpenSearch
        self.status.set(WaitingStatus(RequestUnitServiceOps.format("start")))

        # if this is the first data node to join, start without getting the lock
        ignore_lock = (
            "data" in deployment_desc.config.roles
            and self.unit.is_leader()
            and deployment_desc.typ == DeploymentType.OTHER
            and (
                not self.peers_data.get(Scope.APP, "security_index_initialised", False)
                or (
                    # in case all data-nodes are powered down after being previously started
                    # ignore the lock to get a data-node started, as it holds security index
                    self.peers_data.get(Scope.UNIT, "started")
                    and not self.opensearch.is_service_started()
                )
            )
        )
        self._start_opensearch_event.emit(ignore_lock=ignore_lock)

    def _apply_peer_cm_directives_and_check_if_can_start(self) -> bool:
        """Apply the directives computed by the opensearch peer cluster manager."""
        if not (deployment_desc := self.opensearch_peer_cm.deployment_desc()):
            # the deployment description hasn't finished being computed by the leader
            return False

        # check possibility to start
        if self.opensearch_peer_cm.can_start(deployment_desc):
            try:
                self._get_nodes(False)
            except OpenSearchHttpError:
                return False

            return True

        if self.unit.is_leader():
            self.opensearch_peer_cm.apply_status_if_needed(
                deployment_desc, show_status_only_once=False
            )

        return False

    def _on_peer_relation_created(self, event: RelationCreatedEvent):
        """Event received by the new node joining the cluster."""
        if self.upgrade_in_progress:
            logger.warning(
                "Adding units during an upgrade is not supported. The charm may be in a broken, unrecoverable state"
            )

    def _on_peer_relation_joined(self, event: RelationJoinedEvent):
        """Event received by all units when a new node joins the cluster."""
        if self.upgrade_in_progress:
            logger.warning(
                "Adding units during an upgrade is not supported. The charm may be in a broken, unrecoverable state"
            )

    def _on_peer_relation_changed(self, event: RelationChangedEvent):  # noqa C901
        """Handle peer relation changes."""
        if self.opensearch.is_node_up():
            health = self.health.apply(app=self.unit.is_leader())
            if self._is_peer_rel_changed_deferred:
                # We already deferred this event during this Juju event. Retry on the next
                # Juju event.
                return

            if health in [HealthColors.UNKNOWN, HealthColors.YELLOW_TEMP]:
                # we defer because we want the temporary status to be updated
                logger.debug("Cluster health temp yellow or unknown. Deferring event.")
                event.defer()
                # If the handler is called again within this Juju hook, we will abandon the event
                self._is_peer_rel_changed_deferred = True

        # we want to have the most up-to-date info broadcasted to related sub-clusters
        if self.opensearch_peer_cm.is_provider():
            self.peer_cluster_provider.refresh_relation_data(event, can_defer=False)

        # update any orchestrators about planned units
        if self.opensearch_peer_cm.is_consumer():
            self.peer_cluster_requirer.refresh_requirer_relation_data()

        for relation in self.model.relations.get(ClientRelationName, []):
            self.opensearch_provider.update_endpoints(relation)

        # register new cm addresses on every node
        self._add_cm_addresses_to_conf()

        if self.unit.is_leader():
            # Recompute the node roles in case self-healing didn't trigger leader related event
            self._recompute_roles_if_needed(event)
            if self.peers_data.get(Scope.APP, "is_expecting_cm_unit"):
                # indicates we previously scaled down to <3 CM-eligible units in the cluster
                self.opensearch_peer_cm.validate_recommended_cm_unit_count()
            if self.model.relations[PeerClusterRelationName]:
                self.peer_cluster_requirer.apply_orchestrator_status()
        elif event.relation.data.get(event.app):
            # if app_data + app_data["nodes_config"]: Reconfigure + restart node on the unit
            self._reconfigure_and_restart_unit_if_needed()

        if not (unit_data := event.relation.data.get(event.unit)):
            return

        self.opensearch_exclusions.cleanup()

        if self.unit.is_leader() and unit_data.get("bootstrap_contributor"):
            contributor_count = self.peers_data.get(Scope.APP, "bootstrap_contributors_count", 0)
            self.peers_data.put(Scope.APP, "bootstrap_contributors_count", contributor_count + 1)

    def _on_peer_relation_departed(self, event: RelationDepartedEvent):
        """Relation departed event."""
        if self.upgrade_in_progress:
            logger.warning(
                "Removing units during an upgrade is not supported. The charm may be in a broken, unrecoverable state"
            )
        if not (self.unit.is_leader() and self.opensearch.is_node_up()):
            return

        # Now, we register in the leader application the presence of departing unit's name
        # We need to save them as we have a count limit
        if (
            not (deployment_desc := self.opensearch_peer_cm.deployment_desc())
            or not event.departing_unit
        ):
            # No deployment description present
            # that happens in the very last stages of the application removal
            return

        current_app = self.opensearch_peer_cm.deployment_desc().app
        remaining_nodes = [
            node
            for node in self._get_nodes(True)
            if node.name != format_unit_name(event.departing_unit, app=current_app)
        ]

        self.health.apply(wait_for_green_first=True, unit=False)

        n_units = sum(1 for node in remaining_nodes if node.app.id == current_app.id)
        if n_units == self.app.planned_units():
            self._compute_and_broadcast_updated_topology(remaining_nodes)
        else:
            logger.debug(
                f"Waiting for units to leave: expecting {self.app.planned_units()}, currently {n_units}. Deferring event."
            )
            event.defer()

        if not self.unit.is_leader():
            return

        self.opensearch_peer_cm.validate_recommended_cm_unit_count(remaining_nodes)

        self.opensearch_exclusions.add_to_cleanup_list(
            unit_name=format_unit_name(event.departing_unit.name, deployment_desc.app)
        )

    def _on_opensearch_data_storage_detaching(self, event: StorageDetachingEvent):  # noqa: C901
        """Triggered when removing unit, Prior to the storage being detached."""
        if self.upgrade_in_progress:
            logger.warning(
                "Removing units during an upgrade is not supported. The charm may be in a broken, unrecoverable state"
            )
        # acquire lock to ensure only 1 unit removed at a time
        # Closes canonical/opensearch-operator#378
        if self.app.planned_units() > 1 and not self.node_lock.acquired:
            # Raise uncaught exception to prevent Juju from removing unit
            raise Exception("Unable to acquire lock: Another unit is starting or stopping.")

        # if the leader is departing, and this hook fails "leader elected" won"t trigger,
        # so we want to re-balance the node roles from here
        if self.unit.is_leader():
            if self.app.planned_units() >= 1 and (self.opensearch.is_node_up() or self.alt_hosts):
                remaining_nodes = [
                    node
                    for node in self._get_nodes(self.opensearch.is_node_up())
                    if node.name != self.unit_name
                ]
                self._compute_and_broadcast_updated_topology(remaining_nodes)
                self.opensearch_peer_cm.validate_recommended_cm_unit_count(remaining_nodes)
            elif self.app.planned_units() == 0:
                if self.model.get_relation(PeerRelationName):
                    self.peers_data.delete(Scope.APP, "bootstrap_contributors_count")
                    self.peers_data.delete(Scope.APP, "nodes_config")
                    # we delete the security index initialised and bootstrapped flags
                    # if there are no data units left in all cluster
                    data_apps_in_fleet = [
                        app
                        for app in self.opensearch_peer_cm.apps_in_fleet()
                        if "data" in app.roles
                    ]
                    if not data_apps_in_fleet or all(
                        app.planned_units == 0 for app in data_apps_in_fleet
                    ):
                        self.peers_data.delete(Scope.APP, "security_index_initialised")
                        self.peers_data.delete(Scope.APP, "bootstrapped")
                if self.opensearch_peer_cm.is_provider():
                    self.peer_cluster_provider.refresh_relation_data(event, can_defer=False)
                if self.opensearch_peer_cm.is_consumer():
                    self.peer_cluster_requirer.refresh_requirer_relation_data()

        # we attempt to flush the translog to disk
        if self.opensearch.is_node_up():
            try:
                self.opensearch.request("POST", "/_flush")
            except OpenSearchHttpError:
                # if it's a failed attempt we move on
                pass
        try:
            self._stop_opensearch()
            if self.alt_hosts:
                # There is enough peers available for us to try removing the unit
                self.opensearch_exclusions.delete_current()

            # safeguards in case planned_units > 0
            if self.app.planned_units() > 0:
                # check cluster status
                if self.alt_hosts:
                    health_color = self.health.apply(
                        wait_for_green_first=True, use_localhost=False, unit=False
                    )
                    if health_color == HealthColors.RED:
                        raise OpenSearchHAError(ClusterHealthRed)
                else:
                    raise OpenSearchHAError(ClusterHealthUnknown)
        finally:
            if self.app.planned_units() > 1 and (self.opensearch.is_node_up() or self.alt_hosts):
                # release lock
                self.node_lock.release()

    def _on_update_status(self, event: UpdateStatusEvent):  # noqa: C901
        """On update status event.

        We want to periodically check for the following:
        1- Do we have users that need to be deleted, and if so we need to delete them.
        2- The system requirements are still met
        3- every 6 hours check if certs are expiring soon (in 7 days),
            as a safeguard in case relation broken. As there will be data loss
            without the user noticing in case the cert of the unit transport layer expires.
            So we want to stop opensearch in that case, since it cannot be recovered from.
        """
        # if there are missing system requirements defer
        if len(missing_sys_reqs := self.opensearch.missing_sys_requirements()) > 0:
            self.status.set(BlockedStatus(" - ".join(missing_sys_reqs)))
            return

        # if node already shutdown - leave
        if not self.opensearch.is_node_up():
            return

        # review available CMs
        self._add_cm_addresses_to_conf()

        # if there are exclusions to be removed
        # each unit should check its own exclusions' list
        self.opensearch_exclusions.cleanup()
        if (
            health := self.health.apply(wait_for_green_first=True, app=self.unit.is_leader())
        ) not in [
            HealthColors.GREEN,
            HealthColors.IGNORE,
        ]:
            logger.warning(f"Update status: exclusions updated and cluster health is {health}.")

            if health == HealthColors.UNKNOWN:
                return

        for relation in self.model.relations.get(ClientRelationName, []):
            self.opensearch_provider.update_endpoints(relation)

        deployment_desc = self.opensearch_peer_cm.deployment_desc()
        if self.upgrade_in_progress:
            logger.debug(
                "Skipping `remove_lingering_users_and_roles()` because upgrade is in-progress"
            )
        elif (
            self.unit.is_leader()
            and deployment_desc
            and deployment_desc.typ == DeploymentType.MAIN_ORCHESTRATOR
        ):
            self.opensearch_provider.remove_lingering_relation_users_and_roles()

        # If the unit reloads its certs but the other units are not ready yet
        # we need to wait for them all to be ready before deleting the old CA
        if (
            self.tls.read_stored_ca(OLD_CA_ALIAS)
            and self.tls.ca_and_certs_rotation_complete_in_cluster()
        ):
            logger.debug("update_status: Detected CA rotation complete in cluster")
            self.tls.on_ca_certs_rotation_complete()
        # If relation not broken - leave
        if self.model.get_relation("certificates") is not None:
            return

        # handle when/if certificates are expired
        self._check_certs_expiration(event)

    def trigger_restart(self):
        """Trigger a restart of the service."""
        self._restart_opensearch_event.emit()

    def _on_config_changed(self, event: ConfigChangedEvent):  # noqa C901
        """On config changed event. Useful for IP changes or for user provided config changes."""
        if not self.performance_profile.current:
            # We are running (1) install or (2) an upgrade on instance that pre-dates profile
            # First, we set this unit's effective profile -> 1G heap and no index templates.
            # Our goal is to make sure this value exists once the refresh is finished
            # and it represents the accurate value for this unit.
            self.performance_profile.current = PerformanceType.TESTING

        if self.opensearch_config.update_host_if_needed():
            self.status.set(MaintenanceStatus(TLSNewCertsRequested))
            self.tls.delete_stored_tls_resources()
            self.tls.request_new_unit_certificates()

            # since when an IP change happens, "_on_peer_relation_joined" won't be called,
            # we need to alert the leader that it must recompute the node roles for any unit whose
            # roles were changed while the current unit was cut-off from the rest of the network
            self._on_peer_relation_joined(
                RelationJoinedEvent(event.handle, PeerRelationName, self.app, self.unit)
            )

        previous_deployment_desc = self.opensearch_peer_cm.deployment_desc()
        if self.unit.is_leader():
            # run peer cluster manager processing
            # todo add check here if the diff can be known from now on already
            self.opensearch_peer_cm.run()

            # handle cluster change to main-orchestrator (i.e: init_hold: true -> false)
            self._handle_change_to_main_orchestrator_if_needed(event, previous_deployment_desc)

        if self.upgrade_in_progress:
            logger.warning(
                "Changing config during an upgrade is not supported. The charm may be in a broken, unrecoverable state"
            )
            event.defer()
            return

        if not self.opensearch.is_node_up():
            logger.debug("Node not up yet, deferring plugin check")
            # possible enhancement:
            # currently we wait for Opensearch to be started before applying any plugin
            # this could be improved as some plugins don't require the service to run
            event.defer()
            return

        perf_profile_needs_restart = False
        plugin_needs_restart = False

        try:
            original_status = None
            if self.unit.status.message not in [
                PluginConfigChangeError,
                PluginConfigCheck,
            ]:
                logger.debug(f"Plugin manager: storing status {self.unit.status.message}")
                original_status = self.unit.status
                self.status.set(MaintenanceStatus(PluginConfigCheck))

            plugin_needs_restart = self.plugin_manager.run()
        except (OpenSearchNotFullyReadyError, OpenSearchPluginError) as e:
            if isinstance(e, OpenSearchNotFullyReadyError):
                logger.warning("Plugin management: cluster not ready yet at config changed")
            else:
                logger.warning(f"{PluginConfigChangeError}: {str(e)}")
                self.status.set(BlockedStatus(PluginConfigChangeError))
            event.defer()
            self.status.clear(PluginConfigCheck)
        except OpenSearchKeystoreNotReadyError:
            logger.warning("Keystore not ready yet")
            # defer, and let it finish the status clearing down below
            event.defer()
        else:
            self.status.clear(PluginConfigChangeError)
            self.status.clear(PluginConfigCheck)
            if original_status:
                self.status.set(original_status)

        perf_profile_needs_restart = self.performance_profile.apply(
            self.config.get(PERFORMANCE_PROFILE)
        )

        if not self.opensearch_provider.update_relations_roles_mapping():
            event.defer()

        if self.opensearch.is_service_started() and (
            plugin_needs_restart or perf_profile_needs_restart
        ):
            self._restart_opensearch_event.emit()

    def _on_set_password_action(self, event: ActionEvent):
        """Set new admin password from user input or generate if not passed."""
        if not self.opensearch_peer_cm.deployment_desc():
            event.fail("The action can only be run once the deployment is complete.")
            return
        if self.opensearch_peer_cm.deployment_desc().typ != DeploymentType.MAIN_ORCHESTRATOR:
            event.fail("The action can only be run on the main orchestrator cluster.")
            return
        if not self.unit.is_leader():
            event.fail("The action can only be run on leader unit.")
            return

        if self.upgrade_in_progress:
            event.fail("Setting password not supported while upgrade in-progress")
            return

        user_name = event.params.get("username")
        if user_name not in OpenSearchUsers:
            event.fail(f"Only the {OpenSearchUsers} usernames are allowed for this action.")
            return

        password = event.params.get("password") or generate_password()
        try:
            self._put_or_update_internal_user_leader(user_name, password)
            label = self.secrets.password_key(user_name)
            event.set_results({label: password})
            # We know we are already running for MAIN_ORCH. and its leader unit
            self.peer_cluster_provider.refresh_relation_data(event)
        except OpenSearchError as e:
            event.fail(f"Failed changing the password: {e}")
        except RuntimeError as e:
            # From:
            # https://github.com/canonical/operator/blob/ \
            #     eb52cef1fba4df2f999f88902fb39555fb6de52f/ops/charm.py
            if str(e) == "cannot defer action events":
                event.fail("Cluster is not ready to update this password. Try again later.")
            else:
                event.fail(f"Failed with unknown error: {e}")

    def _on_get_password_action(self, event: ActionEvent):
        """Return the password and cert chain for the admin user of the cluster."""
        if not self.opensearch_peer_cm.deployment_desc():
            event.fail("The action can only be run once the deployment is complete.")
            return

        user_name = event.params.get("username")
        if user_name not in OpenSearchUsers:
            event.fail(f"Only the {OpenSearchUsers} username is allowed for this action.")
            return

        if not self.is_admin_user_configured():
            event.fail(f"{user_name} user not configured yet.")
            return

        if not self.tls.is_fully_configured():
            event.fail("TLS certificates not configured yet.")
            return

        password = self.secrets.get(Scope.APP, self.secrets.password_key(user_name))
        cert = self.secrets.get_object(
            Scope.APP, CertType.APP_ADMIN.val, peek=True
        )  # replace later with new user certs

        event.set_results(
            {
                "username": user_name,
                "password": password,
                "ca-chain": cert["chain"],
            }
        )

    def on_tls_ca_rotation(self):
        """Called when adding new CA to the trust store."""
        self.status.set(MaintenanceStatus(TLSCaRotation))
        self._restart_opensearch_event.emit()

    def on_tls_conf_set(
        self, event: CertificateAvailableEvent, scope: Scope, cert_type: CertType, renewal: bool
    ):
        """Called after certificate ready and stored on the corresponding scope databag.

        - Store the cert on the file system, on all nodes for APP certificates
        - Update the corresponding yaml conf files
        - Run the security admin script
        """
        if scope == Scope.UNIT:
            admin_secrets = (
                self.secrets.get_object(Scope.APP, CertType.APP_ADMIN.val, peek=True) or {}
            )
            if not (truststore_pwd := admin_secrets.get("truststore-password")):
                event.defer()
                return

            keystore_pwd = self.secrets.get_object(scope, cert_type.val, peek=True)[
                "keystore-password"
            ]

            # node http or transport cert
            self.opensearch_config.set_node_tls_conf(
                cert_type,
                truststore_pwd=truststore_pwd,
                keystore_pwd=keystore_pwd,
            )

            # write the admin cert conf on all units, in case there is a leader loss + cert renewal
            if not admin_secrets.get("subject"):
                return
            self.opensearch_config.set_admin_tls_conf(admin_secrets)

        self.tls.store_admin_tls_secrets_if_applies()

        # In case of renewal of the unit transport layer cert - restart opensearch
        if renewal and self.is_admin_user_configured():
            if self.tls.is_fully_configured():
                try:
                    self.tls.reload_tls_certificates()
                except OpenSearchHttpError:
                    logger.error("Could not reload TLS certificates via API, will restart.")
                    self._restart_opensearch_event.emit()
                else:
                    self.status.clear(TLSNotFullyConfigured)
                    self.tls.reset_ca_rotation_state()
                    # if all certs are stored and CA rotation is complete in the cluster
                    # we delete the old ca and update the chain to only include the new one
                    if (
                        self.tls.read_stored_ca(OLD_CA_ALIAS)
                        and self.tls.ca_and_certs_rotation_complete_in_cluster()
                    ):
                        logger.info("on_tls_conf_set: Detected CA rotation complete in cluster")
                        self.tls.on_ca_certs_rotation_complete()
            else:
                event.defer()
                return

    def on_tls_relation_broken(self, _: RelationBrokenEvent):
        """As long as all certificates are produced, we don't do anything."""
        if self.tls.all_tls_resources_stored():
            return

        # Otherwise, we block.
        self.status.set(BlockedStatus(TLSRelationBrokenError))

    def is_every_unit_marked_as_started(self) -> bool:
        """Check if every unit in the cluster is marked as started."""
        rel = self.model.get_relation(PeerRelationName)
        all_started = True
        for unit in all_units(self):
            if rel.data[unit].get("started") != "True":
                all_started = False
                break

        if all_started:
            return True

        try:
            current_app_nodes = [
                node
                for node in self._get_nodes(self.opensearch.is_node_up())
                if node.app.id == self.opensearch_peer_cm.deployment_desc().app.id
            ]
            return len(current_app_nodes) == self.app.planned_units()
        except OpenSearchHttpError:
            return False

    def is_tls_full_configured_in_cluster(self) -> bool:
        """Check if TLS is configured in all the units of the current cluster."""
        rel = self.model.get_relation(PeerRelationName)
        for unit in all_units(self):
            if (
                rel.data[unit].get("tls_configured") != "True"
                or "tls_ca_renewing" in rel.data[unit]
                or "tls_ca_renewed" in rel.data[unit]
            ):
                return False
        return True

    def is_admin_user_configured(self) -> bool:
        """Check if admin user configured."""
        # In case the initialisation of the admin user is not finished yet
        return self.peers_data.get(Scope.APP, "admin_user_initialized", False)

    def _handle_change_to_main_orchestrator_if_needed(
        self, event: ConfigChangedEvent, previous_deployment_desc: Optional[DeploymentDescription]
    ) -> None:
        """Handle when the user changes the roles or init_hold config from True to False."""
        # if the current cluster wasn't already a "main-Orchestrator" and we're now updating
        # the roles for it to become one. We need to: create the admin user if missing, and
        # generate the admin certificate if missing and the TLS relation is established.
        cluster_changed_to_main_cm = (
            previous_deployment_desc is not None
            and previous_deployment_desc.typ != DeploymentType.MAIN_ORCHESTRATOR
            and self.opensearch_peer_cm.deployment_desc().typ == DeploymentType.MAIN_ORCHESTRATOR
        )
        if not cluster_changed_to_main_cm:
            return
        if self.upgrade_in_progress:
            logger.warning(
                "Changing config during an upgrade is not supported. The charm may be in a broken, unrecoverable state"
            )
            event.defer()
            return

        # we check if we need to create the admin user
        if not self.is_admin_user_configured():
            self._put_or_update_internal_user_leader(AdminUser)

        # we check if we need to generate the admin certificate if missing
        if not self.tls.all_tls_resources_stored():
            if not self.model.get_relation("certificates"):
                event.defer()
                return

            self.tls.request_new_admin_certificate()

    def _start_opensearch(self, event: _StartOpenSearch) -> None:  # noqa: C901
        """Start OpenSearch, with a generated or passed conf, if all resources configured."""
        if not self.opensearch_peer_cm.deployment_desc() and self.app.planned_units() == 0:
            # canonical/opensearch-operator#444
            # https://bugs.launchpad.net/juju/+bug/2076599
            # This condition is a corner case where we have:
            #   1) a single-node cluster
            #   2) an unfinished (re)start: yet to run _post_start_init() method
            #   3) LP#2076599: remove-application was called in-between and peer databag is empty
            # TODO: remove this IF condition once LP#2076599 is fixed in Juju.
            return

        if self.opensearch.is_started():
            try:
                self._post_start_init(event)
            except (
                OpenSearchHttpError,
                OpenSearchNotFullyReadyError,
            ):
                event.defer()
            except OpenSearchUserMgmtError as e:
                # Either generic start failure or cluster is not read to create the internal users
                logger.warning(e)
                self.node_lock.release()
                self.status.set(BlockedStatus(ServiceStartError))
                event.defer()
            return

        self.peers_data.delete(Scope.UNIT, "started")

        if event.ignore_lock:
            # Only used for force upgrades
            logger.debug("Starting without lock")
        elif not self.node_lock.acquired:
            logger.debug("Lock to start opensearch not acquired. Will retry next event")
            event.defer()
            return

        if not self._can_service_start():
            self.node_lock.release()
            logger.info("Could not start opensearch service. Will retry next event.")
            event.defer()
            return

        if self.opensearch.is_failed():
            self.node_lock.release()
            self.status.set(BlockedStatus(ServiceStartError))
            event.defer()
            return

        self.unit.status = WaitingStatus(WaitingToStart)

        try:
            # Retrieve the nodes of the cluster, needed to configure this node
            nodes = self._get_nodes(False)

            # Set the configuration of the node
            self._set_node_conf(nodes)
        except OpenSearchHttpError as e:
            logger.debug(f"error getting the nodes: {e}")
            self.node_lock.release()
            event.defer()
            return

        try:
            self.opensearch.start(
                wait_until_http_200=(
                    not self.unit.is_leader()
                    or self.peers_data.get(Scope.APP, "security_index_initialised", False)
                )
            )
            self._post_start_init(event)
        except (
            OpenSearchHttpError,
            OpenSearchStartTimeoutError,
            OpenSearchNotFullyReadyError,
        ) as e:
            self.node_lock.release()
            # In large deployments with cluster-manager-only-nodes, the startup might fail
            # for the cluster-manager if a joining data node did not yet initialize the
            # security index. We still want to update and broadcast the latest relation data.
            if self.opensearch_peer_cm.is_provider(typ="main"):
                self.peer_cluster_provider.refresh_relation_data(event, can_defer=False)
            event.defer()
            logger.warning(e)
        except (OpenSearchStartError, OpenSearchUserMgmtError) as e:
            logger.warning(e)
            self.node_lock.release()
            self.status.set(BlockedStatus(ServiceStartError))
            event.defer()

    def _post_start_init(self, event: _StartOpenSearch):  # noqa: C901
        """Initialization post OpenSearch start."""
        # initialize the security index if needed (and certs written on disk etc.)
        # this happens only on the first data node to join the cluster
        if (
            self.unit.is_leader()
            and not self.peers_data.get(Scope.APP, "security_index_initialised")
            and (
                "data" in self.opensearch_peer_cm.deployment_desc().config.roles
                or self.opensearch_peer_cm.deployment_desc().start
                == StartMode.WITH_GENERATED_ROLES
            )
        ):
            admin_secrets = self.secrets.get_object(Scope.APP, CertType.APP_ADMIN.val, peek=True)
            try:
                self._initialize_security_index(admin_secrets)
                self.put_security_index_initialized(event)

            except OpenSearchCmdError as e:
                logger.debug(f"Error when initializing the security index: {e.out}")
                event.defer()
                return

        # it sometimes takes a few seconds before the node is fully "up" otherwise a 503 error
        # may be thrown when calling a node - we want to ensure this node is perfectly ready
        # before marking it as ready
        if not self.opensearch.is_node_up():
            raise OpenSearchNotFullyReadyError("Node started but not full ready yet.")

        try:
            nodes = self._get_nodes(use_localhost=self.opensearch.is_node_up())
        except OpenSearchHttpError:
            logger.info("Failed to get online nodes")
            event.defer()
            return

        for node in nodes:
            if node.name == self.unit_name:
                break
        else:
            raise OpenSearchNotFullyReadyError("Node online but not in cluster.")

        # cleanup bootstrap conf in the node
        if self.peers_data.get(Scope.UNIT, "bootstrap_contributor"):
            self._cleanup_bootstrap_conf_if_applies()

        # Remove the exclusions that could not be removed when no units were online
        self.opensearch_exclusions.delete_current()

        self.node_lock.release()

        if event.after_upgrade:
            try:
                self.opensearch.request(
                    "PUT",
                    "/_cluster/settings",
                    # Reset to default value
                    payload={"persistent": {"cluster.routing.allocation.enable": None}},
                )
            except OpenSearchHttpError:
                logger.exception("Failed to re-enable allocation after upgrade")
                event.defer()
                return

        self.peers_data.put(Scope.UNIT, "started", True)

        # apply post_start fixes to resolve start related upstream bugs
        self.opensearch_fixes.apply_on_start()

        # apply cluster health
        self.health.apply(
            wait_for_green_first=True,
            app=self.unit.is_leader(),
        )

        if (
            self.unit.is_leader()
            and self.opensearch_peer_cm.deployment_desc().typ == DeploymentType.MAIN_ORCHESTRATOR
        ):
            # Creating the monitoring user
            self._put_or_update_internal_user_leader(COSUser, update=False)

        self.unit.open_port("tcp", 9200)

        # clear waiting to start status
        self.status.clear(RequestUnitServiceOps.format("start"))
        self.status.clear(WaitingToStart)
        self.status.clear(ServiceStartError)
        self.status.clear(PClusterNoDataNode)

        if event.after_upgrade:
            health = self.health.get(local_app_only=False, wait_for_green_first=True)
            self.health.apply_for_unit_during_upgrade(health)

            # Cluster is considered healthy if green or yellow
            # TODO future improvement: try to narrow scope to just green or green + yellow in
            # specific cases
            # https://github.com/canonical/opensearch-operator/issues/268
            # See https://chat.canonical.com/canonical/pl/s5j64ekxwi8epq53kzhd8fhrco and
            # https://chat.canonical.com/canonical/pl/zaizx3bu3j8ftfcw67qozw9dbo
            # For now, we need to allow yellow because
            # "During a rolling upgrade, primary shards assigned to a node running the new
            # version cannot have their replicas assigned to a node with the old version. The new
            # version might have a different data format that is not understood by the old
            # version.
            #
            # "If it is not possible to assign the replica shards to another node (there is only
            # one upgraded node in the cluster), the replica shards remain unassigned and status
            # stays `yellow`.
            #
            # "In this case, you can proceed once there are no initializing or relocating shards
            # (check the `init` and `relo` columns).
            #
            # "As soon as another node is upgraded, the replicas can be assigned and the status
            # will change to `green`."
            #
            # from
            # https://www.elastic.co/guide/en/elastic-stack/8.13/upgrading-elasticsearch.html#upgrading-elasticsearch
            #
            # If `health_ == HealthColors.YELLOW`, no shards are initializing or relocating
            # (otherwise `health_` would be `HealthColors.YELLOW_TEMP`)
            if health not in (HealthColors.GREEN, HealthColors.YELLOW):
                logger.error(
                    "Cluster is not healthy after upgrade. Manual intervention required. To rollback, "
                    "`juju refresh` to the previous revision"
                )
                event.defer()
                return
            elif health == HealthColors.YELLOW:
                # TODO future improvement:
                # https://github.com/canonical/opensearch-operator/issues/268
                logger.warning(
                    "Cluster is yellow. Upgrade may cause data loss if cluster is yellow for reason "
                    "other than primary shards on upgraded unit & not enough upgraded units available "
                    "for replica shards"
                )

        self._upgrade.unit_state = upgrade.UnitState.HEALTHY
        logger.debug("Set upgrade unit state to healthy")
        self._reconcile_upgrade()

        # update the peer cluster rel data with new IP in case of main cluster manager
        if self.opensearch_peer_cm.is_provider():
            self.peer_cluster_provider.refresh_relation_data(event, can_defer=False)

        # update the peer relation data for TLS CA rotation routine
        self.tls.reset_ca_rotation_state()
        if self.is_tls_full_configured_in_cluster():
            self.status.clear(TLSCaRotation)
            self.status.clear(TLSNotFullyConfigured)

        # request new certificates after rotating the CA
        if self.peers_data.get(Scope.UNIT, "tls_ca_renewing", False) and self.peers_data.get(
            Scope.UNIT, "tls_ca_renewed", False
        ):
            self.status.set(MaintenanceStatus(TLSNotFullyConfigured))
            self.tls.request_new_unit_certificates()
            if self.unit.is_leader():
                self.tls.request_new_admin_certificate()
            else:
                self.tls.store_admin_tls_secrets_if_applies()
        # If the reload through API failed, we restart the service
        # We remove the old CA and update the chain to only include the new one
        # if all certs are stored and CA rotation is complete in the cluster
        if (
            self.tls.read_stored_ca(OLD_CA_ALIAS)
            and self.tls.ca_and_certs_rotation_complete_in_cluster()
        ):
            logger.info("post_start_init: Detected CA rotation complete in cluster")
            self.tls.on_ca_certs_rotation_complete()

    def _stop_opensearch(self, *, restart: bool = False) -> None:
        """Stop OpenSearch if possible."""
        self.status.set(WaitingStatus(ServiceIsStopping))

        if self.opensearch.is_node_up():
            try:
                nodes = self._get_nodes(True)
                # do not add exclusions if it's the last unit to stop
                # otherwise cluster manager election will be blocked when starting up again
                # and reusing storage
                if len(nodes) > 1:
                    # 1. Add current node to the voting + alloc exclusions
                    self.opensearch_exclusions.add_current(voting=True, allocation=not restart)
            except OpenSearchHttpError:
                logger.debug("Failed to get online nodes, voting and alloc exclusions not added")

        # block until all primary shards are moved away from the unit that is stopping
        self.health.wait_for_shards_relocation()

        # 2. stop the service
        self.opensearch.stop()
        self.peers_data.delete(Scope.UNIT, "started")
        self.status.set(WaitingStatus(ServiceStopped))

    def _restart_opensearch(self, event: _RestartOpenSearch) -> None:
        """Restart OpenSearch if possible."""
        if not self.node_lock.acquired:
            logger.debug("Lock to restart opensearch not acquired. Will retry next event")
            event.defer()
            return

        try:
            self._stop_opensearch(restart=True)
            logger.info("Restarting OpenSearch.")
        except OpenSearchStopError as e:
            logger.info(f"Error while Restarting Opensearch: {e}")
            logger.exception(e)
            self.node_lock.release()
            event.defer()
            self.status.set(WaitingStatus(ServiceIsStopping))
            return

        self._start_opensearch_event.emit()

    def _upgrade_opensearch(self, event: _UpgradeOpenSearch) -> None:  # noqa: C901
        """Upgrade OpenSearch."""
        logger.debug("Attempting to acquire lock for upgrade")
        if not self.node_lock.acquired:
            # (Attempt to acquire lock even if `event.ignore_lock`)
            if event.ignore_lock:
                logger.debug("Upgrading without lock")
            else:
                logger.debug("Lock to upgrade opensearch not acquired. Will retry next event")
                event.defer()
                return
        logger.debug("Acquired lock for upgrade")

        # https://www.elastic.co/guide/en/elastic-stack/8.13/upgrading-elasticsearch.html
        try:
            self.opensearch.request(
                "PUT",
                "/_cluster/settings",
                payload={"persistent": {"cluster.routing.allocation.enable": "primaries"}},
            )
        except OpenSearchHttpError:
            logger.exception("Failed to disable shard allocation before upgrade")
            self.node_lock.release()
            event.defer()
            return
        try:
            self.opensearch.request("POST", "/_flush", retries=3)
        except OpenSearchHttpError as e:
            logger.debug("Failed to flush before upgrade", exc_info=e)

        logger.debug("Stopping OpenSearch before upgrade")
        try:
            self._stop_opensearch(restart=True)
        except OpenSearchStopError as e:
            logger.exception(e)
            self.node_lock.release()
            event.defer()
            self.status.set(WaitingStatus(ServiceIsStopping))
            return
        logger.debug("Stopped OpenSearch before upgrade")

        self._upgrade.upgrade_unit(snap=self.opensearch)

        logger.debug("Starting OpenSearch after upgrade")
        self._start_opensearch_event.emit(ignore_lock=event.ignore_lock, after_upgrade=True)

    def _can_service_start(self) -> bool:
        """Return if the opensearch service can start."""
        # if there are any missing system requirements leave
        if missing_sys_reqs := self.opensearch.missing_sys_requirements():
            self.status.set(BlockedStatus(" - ".join(missing_sys_reqs)))
            return False

        if not (deployment_desc := self.opensearch_peer_cm.deployment_desc()):
            return False

        if not self.opensearch_peer_cm.can_start(deployment_desc):
            return False

        if not self.is_admin_user_configured():
            return False

        # Case of the first "main" cluster to get started.
        if (
            not self.peers_data.get(Scope.APP, "security_index_initialised", False)
            or not self.alt_hosts
        ):
            return self.unit.is_leader() and (
                deployment_desc.start == StartMode.WITH_GENERATED_ROLES
                or deployment_desc.typ == DeploymentType.MAIN_ORCHESTRATOR
                or "data" in deployment_desc.config.roles
            )

        # When a new unit joins, replica shards are automatically added to it. In order to prevent
        # overloading the cluster, units must be started one at a time. So we defer starting
        # opensearch until all shards in other units are in a "started" or "unassigned" state.
        try:
            if (
                self.health.apply(wait_for_green_first=True, use_localhost=False, app=False)
                == HealthColors.YELLOW_TEMP
            ):
                return False
        except OpenSearchHttpError:
            # this means that the leader unit is not reachable (not started yet),
            # meaning it's a new cluster, so we can safely start the OpenSearch service
            pass

        return True

    def _purge_users(self):
        """Removes all users from internal_users yaml config.

        This is to be used when starting up the charm, to remove unnecessary default users.
        """
        try:
            internal_users = self.opensearch.config.load(
                "opensearch-security/internal_users.yml"
            ).keys()
        except FileNotFoundError:
            # internal_users.yml hasn't been initialised yet, so skip purging for now.
            return

        for user in internal_users:
            if user != "_meta":
                self.opensearch.config.delete("opensearch-security/internal_users.yml", user)

    def _put_or_update_internal_user_leader(
        self,
        user: str,
        pwd: Optional[str] = None,
        update: bool = True,
    ) -> None:
        """Create system user or update it with a new password."""
        # Leader is to set new password and hash, others populate existing hash locally
        if not self.unit.is_leader():
            logger.error("Credential change can be only performed by the leader unit.")
            return

        secret = self.secrets.get(Scope.APP, self.secrets.password_key(user))
        if secret and not update:
            self._put_or_update_internal_user_unit(user)
            return

        hashed_pwd, pwd = generate_hashed_password(pwd)

        # Updating security index
        # We need to do this for all credential changes
        if secret and update:
            self.user_manager.update_user_password(user, hashed_pwd)

        # In case it's a new user, OR it's a system user (that has an entry in internal_users.yml)
        # we either need to initialize or update (local) credentials as well
        if not secret or user in OpenSearchSystemUsers:
            self.user_manager.put_internal_user(user, hashed_pwd)

        # Secrets need to be maintained
        # For System Users we also save the hash key
        # so all units can fetch it for local users (internal_users.yml) updates.
        self.secrets.put(Scope.APP, self.secrets.password_key(user), pwd)

        if user in OpenSearchSystemUsers:
            self.secrets.put(Scope.APP, self.secrets.hash_key(user), hashed_pwd)

        if user == AdminUser:
            self.peers_data.put(Scope.APP, "admin_user_initialized", True)

    def _put_or_update_internal_user_unit(self, user: str) -> None:
        """Create system user or update it with a new password."""
        # Leader is to set new password and hash, others populate existing hash locally
        hashed_pwd = self.secrets.get(Scope.APP, self.secrets.hash_key(user))

        # System users have to be saved locally in internal_users.yml
        if user in OpenSearchSystemUsers:
            self.user_manager.put_internal_user(user, hashed_pwd)

    def _initialize_security_index(self, admin_secrets: Dict[str, any]) -> None:
        """Run the security_admin script, it creates and initializes the opendistro_security index.

        IMPORTANT: must only run once per cluster, otherwise the index gets overrode
        """
        args = [
            f"-cd {self.opensearch.paths.conf}/opensearch-security/",
            f"-cn {self.opensearch_peer_cm.deployment_desc().config.cluster_name}",
            f"-h {self.unit_ip}",
            f"-ts {self.opensearch.paths.certs}/ca.p12",
            f"-tspass {self.secrets.get_object(Scope.APP, CertType.APP_ADMIN.val, peek=True)['truststore-password']}",
            "-tsalias ca",
            "-tst PKCS12",
            f"-ks {self.opensearch.paths.certs}/{CertType.APP_ADMIN}.p12",
            f"-kspass {self.secrets.get_object(Scope.APP, CertType.APP_ADMIN.val, peek=True)['keystore-password']}",
            f"-ksalias {CertType.APP_ADMIN}",
            "-kst PKCS12",
        ]

        admin_key_pwd = admin_secrets.get("key-password", None)
        if admin_key_pwd is not None:
            args.append(f"-keypass {admin_key_pwd}")

        self.status.set(MaintenanceStatus(SecurityIndexInitProgress))
        self.opensearch.run_script(
            "plugins/opensearch-security/tools/securityadmin.sh", " ".join(args)
        )
        self.status.clear(SecurityIndexInitProgress)

    def update_security_config(self, admin_secrets: Dict[str, any], file: str) -> None:
        """Run the security_admin script for specified config file, avoiding changes to others."""
        if not file.startswith("opensearch-security"):
            raise ValueError("security config is expected")

        args = [
            f"-f {self.opensearch.paths.conf}/{file}",
            f"-cn {self.opensearch_peer_cm.deployment_desc().config.cluster_name}",
            f"-h {self.unit_ip}",
            f"-ts {self.opensearch.paths.certs}/ca.p12",
            f"-tspass {admin_secrets['truststore-password']}",
            "-tst PKCS12",
            f"-ks {self.opensearch.paths.certs}/{CertType.APP_ADMIN}.p12",
            f"-kspass {admin_secrets['keystore-password']}",
            "-kst PKCS12",
        ]

        admin_key_pwd = admin_secrets.get("key-password", None)
        if admin_key_pwd is not None:
            args.append(f"-keypass {admin_key_pwd}")

        self.opensearch.run_script(
            "plugins/opensearch-security/tools/securityadmin.sh", " ".join(args)
        )

    def _get_nodes(self, use_localhost: bool) -> List[Node]:
        """Fetch the list of nodes of the cluster, depending on the requester."""
        if self.app.planned_units() == 0 and not self.opensearch_peer_cm.deployment_desc():
            # This app is going away and the -broken event already happened
            return []

        # This means it's the first unit on the cluster.
        if self.opensearch_peer_cm.deployment_desc().start == StartMode.WITH_PROVIDED_ROLES:
            computed_roles = self.opensearch_peer_cm.deployment_desc().config.roles
        else:
            computed_roles = ClusterTopology.generated_roles()

        if (
            self.unit.is_leader()
            and "data" in computed_roles
            and not self.peers_data.get(Scope.APP, "security_index_initialised", False)
        ):
            return []

        return ClusterTopology.nodes(self.opensearch, use_localhost, self.alt_hosts)

    def _set_node_conf(self, nodes: List[Node]) -> None:
        """Set the configuration of the current node / unit."""
        # set user provided roles if any, else generate base roles
        if (
            deployment_desc := self.opensearch_peer_cm.deployment_desc()
        ).start == StartMode.WITH_PROVIDED_ROLES:
            computed_roles = deployment_desc.config.roles
        else:
            computed_roles = ClusterTopology.generated_roles()

        cm_names = ClusterTopology.get_cluster_managers_names(nodes)
        cm_ips = ClusterTopology.get_cluster_managers_ips(nodes)

        contribute_to_bootstrap = False
        if computed_roles == ["coordinating"]:
            computed_roles = []  # to mark a node as dedicated coordinating only, we clear the list
        elif "cluster_manager" in computed_roles:
            cm_names.append(self.unit_name)
            cm_ips.append(self.unit_ip)

            if (
                self.opensearch_peer_cm.deployment_desc().typ == DeploymentType.MAIN_ORCHESTRATOR
                and not self.peers_data.get(Scope.APP, "bootstrapped", False)
            ):
                cms_in_bootstrap = self.peers_data.get(
                    Scope.APP, "bootstrap_contributors_count", 0
                )
                if cms_in_bootstrap < self.app.planned_units():
                    contribute_to_bootstrap = True

                    if self.unit.is_leader():
                        self.peers_data.put(
                            Scope.APP, "bootstrap_contributors_count", cms_in_bootstrap + 1
                        )

                    # indicates that this unit is part of the "initial cm nodes"
                    self.peers_data.put(Scope.UNIT, "bootstrap_contributor", True)

        deployment_desc = self.opensearch_peer_cm.deployment_desc()
        self.opensearch_config.set_node(
            app=deployment_desc.app,
            cluster_name=deployment_desc.config.cluster_name,
            unit_name=self.unit_name,
            roles=computed_roles,
            cm_names=list(set(cm_names)),
            cm_ips=list(set(cm_ips)),
            contribute_to_bootstrap=contribute_to_bootstrap,
            node_temperature=deployment_desc.config.data_temperature,
        )

    def _cleanup_bootstrap_conf_if_applies(self) -> None:
        """Remove some conf props in the CM nodes that contributed to the cluster bootstrapping."""
        if self.unit.is_leader():
            self.peers_data.put(Scope.APP, "bootstrapped", True)
        self.peers_data.delete(Scope.UNIT, "bootstrap_contributor")
        self.opensearch_config.cleanup_bootstrap_conf()

    def _add_cm_addresses_to_conf(self):
        """Add the new IP addresses of the current CM units."""
        try:
            # fetch nodes
            nodes = ClusterTopology.nodes(
                self.opensearch, use_localhost=self.opensearch.is_node_up(), hosts=self.alt_hosts
            )
            # update (append) CM IPs
            self.opensearch_config.add_seed_hosts(
                [node.ip for node in nodes if node.is_cm_eligible()]
            )
        except OpenSearchHttpError:
            return

    def _reconfigure_and_restart_unit_if_needed(self):
        """Reconfigure the current unit if a new config was computed for it, then restart."""
        if not (nodes_config := self.peers_data.get_object(Scope.APP, "nodes_config")):
            return

        nodes_config = {name: Node.from_dict(node) for name, node in nodes_config.items()}

        # update (append) CM IPs
        self.opensearch_config.add_seed_hosts(
            [node.ip for node in list(nodes_config.values()) if node.is_cm_eligible()]
        )

        if not (new_node_conf := nodes_config.get(self.unit_name)):
            # the conf could not be computed / broadcast, because this node is
            # "starting" and is not online "yet" - either barely being configured (i.e. TLS)
            # or waiting to start.
            return

        current_conf = self.opensearch_config.load_node()
        stored_roles = current_conf["node.roles"] or ["coordinating"]
        new_conf_roles = new_node_conf.roles or ["coordinating"]
        if (
            sorted(stored_roles) == sorted(new_conf_roles)
            and current_conf.get("node.attr.temp") == new_node_conf.temperature
        ):
            # no conf change (roles for now)
            return

        self.status.set(WaitingStatus(WaitingToStart))
        self._restart_opensearch_event.emit()

    def _recompute_roles_if_needed(self, event: RelationChangedEvent):
        """Recompute node roles:self-healing that didn't trigger leader related event occurred."""
        try:
            if not (nodes := self._get_nodes(self.opensearch.is_node_up())):
                return

            if len(nodes) < self.app.planned_units():
                return

            self._compute_and_broadcast_updated_topology(nodes)
        except OpenSearchHttpError:
            pass

    def _compute_and_broadcast_updated_topology(self, current_nodes: List[Node]) -> None:
        """Compute cluster topology and broadcast node configs (roles for now) to change if any."""
        if not current_nodes:
            return

        current_reported_nodes = {
            name: Node.from_dict(node)
            for name, node in (self.peers_data.get_object(Scope.APP, "nodes_config") or {}).items()
        }

        if (
            deployment_desc := self.opensearch_peer_cm.deployment_desc()
        ).start == StartMode.WITH_GENERATED_ROLES:
            updated_nodes = ClusterTopology.recompute_nodes_conf(
                app_id=deployment_desc.app.id, nodes=current_nodes
            )
        else:
            updated_nodes = {}
            for node in current_nodes:
                roles = node.roles
                temperature = node.temperature

                # only change the roles of the nodes of the current cluster
                if node.app.id == deployment_desc.app.id:
                    roles = deployment_desc.config.roles
                    temperature = deployment_desc.config.data_temperature

                updated_nodes[node.name] = Node(
                    name=node.name,
                    roles=roles,
                    ip=node.ip,
                    app=node.app,
                    unit_number=self.unit_id,
                    temperature=temperature,
                )

        if current_reported_nodes == updated_nodes:
            return

        self.peers_data.put_object(Scope.APP, "nodes_config", updated_nodes)

        # all units will get a peer_rel_changed event, for leader we do as follows
        self._reconfigure_and_restart_unit_if_needed()

    def _check_certs_expiration(self, event: UpdateStatusEvent) -> None:
        """Checks the certificates' expiration."""
        date_format = "%Y-%m-%d %H:%M:%S"
        last_cert_check = datetime.strptime(
            self.peers_data.get(Scope.UNIT, "certs_exp_checked_at", "1970-01-01 00:00:00"),
            date_format,
        )

        # See if the last check was made less than 6h ago, if yes - leave
        if (datetime.now() - last_cert_check).seconds < 6 * 3600:
            return

        certs = self.tls.get_unit_certificates()

        # keep certificates that are expiring in less than 24h
        for cert_type in list(certs.keys()):
            hours = cert_expiration_remaining_hours(certs[cert_type])
            if hours > 24 * 7:
                del certs[cert_type]

        if certs:
            missing = [cert.val for cert in certs.keys()]
            self.status.set(BlockedStatus(CertsExpirationError.format(", ".join(missing))))

            # stop opensearch in case the Node-transport certificate expires.
            if certs.get(CertType.UNIT_TRANSPORT) is not None:
                try:
                    self._stop_opensearch()
                except OpenSearchStopError:
                    event.defer()
                    return

        self.peers_data.put(
            Scope.UNIT, "certs_exp_checked_at", datetime.now().strftime(date_format)
        )

    def _get_prometheus_labels(self) -> Optional[Dict[str, str]]:
        """Return the labels for the prometheus scrape."""
        try:
            if not self.opensearch.roles:
                return None
            taggable_roles = ClusterTopology.generated_roles() + ["voting"]
            roles = set(
                role if role in taggable_roles else "other" for role in self.opensearch.roles
            )
            roles = sorted(roles)
            return {"roles": ",".join(roles)}
        except KeyError:
            # At very early stages of the deployment, "node.roles" may not be yet present
            # in the opensearch.yml, nor APIs is responding. Therefore, we need to catch
            # the KeyError here and report the appropriate response.
            return None

    def _scrape_config(self) -> List[Dict]:
        """Generates the scrape config as needed."""
        if (
            not (
                app_secrets := self.secrets.get_object(
                    Scope.APP, CertType.APP_ADMIN.val, peek=True
                )
            )
            or not (ca := app_secrets.get("ca-cert"))
            or not (pwd := self.secrets.get(Scope.APP, self.secrets.password_key(COSUser)))
            or not self._get_prometheus_labels()
        ):
            # Not yet ready, waiting for certain values to be set
            return []
        return [
            {
                "metrics_path": "/_prometheus/metrics",
                "static_configs": [
                    {
                        "targets": [f"{self.unit_ip}:{COSPort}"],
                        "labels": self._get_prometheus_labels(),
                    }
                ],
                "tls_config": {"ca": ca},
                "scheme": "https" if self.tls.all_tls_resources_stored() else "http",
                "basic_auth": {"username": f"{COSUser}", "password": f"{pwd}"},
            }
        ]

    def handle_joining_data_node(self) -> None:
        """Start Opensearch on a cluster-manager node when a data-node is joining"""
        if self.peers_data.get(Scope.UNIT, "started", False):
            self.status.clear(PClusterNoDataNode)
        else:
            self._start_opensearch_event.emit(ignore_lock=True)

    def put_security_index_initialized(self, event: EventBase) -> None:
        """Set the security index initialized flag."""
        self.peers_data.put(Scope.APP, "security_index_initialised", True)
        if self.opensearch_peer_cm.deployment_desc().typ == DeploymentType.MAIN_ORCHESTRATOR:
            self.peer_cluster_provider.refresh_relation_data(event)
        else:
            # notify the main orchestrator that the security index is initialized
            self.peer_cluster_requirer.set_security_index_initialised()

    @property
    def unit_ip(self) -> str:
        """IP address of the current unit."""
        return get_host_ip(self, PeerRelationName)

    @property
    def unit_name(self) -> str:
        """Name of the current unit."""
        return format_unit_name(self.unit, app=self.opensearch_peer_cm.deployment_desc().app)

    @property
    def unit_id(self) -> int:
        """ID of the current unit."""
        return int(self.unit.name.split("/")[-1])

    @property
    def alt_hosts(self) -> Optional[List[str]]:
        """Return an alternative host (of another node) in case the current is offline."""
        all_units_ips = units_ips(self, PeerRelationName)
        all_hosts = list(all_units_ips.values())

        if nodes_conf := self.peers_data.get_object(Scope.APP, "nodes_config"):
            all_hosts.extend([Node.from_dict(node).ip for node in nodes_conf.values()])

        if peer_cm_rel_data := self.opensearch_peer_cm.rel_data():
            all_hosts.extend([node.ip for node in peer_cm_rel_data.cm_nodes])

        random.shuffle(all_hosts)

        if not all_hosts:
            return None

        return [
            host for host in all_hosts if host != self.unit_ip and self.opensearch.is_node_up(host)
        ]
