# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Class for Setting configuration in opensearch config files."""

import logging
from collections import namedtuple
from typing import Any, Dict, List, Optional

from charms.opensearch.v0.constants_tls import CertType
from charms.opensearch.v0.helper_security import normalized_tls_subject
from charms.opensearch.v0.models import App, OpenSearchPerfProfile
from charms.opensearch.v0.opensearch_distro import OpenSearchDistribution

# The unique Charmhub library identifier, never change it
LIBID = "b02ab02d4fd644fdabe02c61e509093f"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

logger = logging.getLogger(__name__)


class OpenSearchConfig:
    """This class covers the configuration changes depending on certain actions."""

    CONFIG_YML = "opensearch.yml"
    SECURITY_CONFIG_YML = "opensearch-security/config.yml"
    JVM_OPTIONS = "jvm.options"

    def __init__(self, opensearch: OpenSearchDistribution):
        self._opensearch = opensearch

    def load_node(self):
        """Load the opensearch.yml config of the node."""
        return self._opensearch.config.load(self.CONFIG_YML)

    def set_client_auth(self):
        """Configure TLS and basic http for clients."""
        # The security plugin will accept TLS client certs if certs but doesn't require them
        # TODO this may be set to REQUIRED if we want to ensure certs provided by the client app
        self._opensearch.config.put(
            self.CONFIG_YML, "plugins.security.ssl.http.clientauth_mode", "OPTIONAL"
        )

        self._opensearch.config.put(
            self.SECURITY_CONFIG_YML,
            "config/dynamic/authc/basic_internal_auth_domain/http_enabled",
            True,
        )

        self._opensearch.config.put(
            self.SECURITY_CONFIG_YML,
            "config/dynamic/authc/clientcert_auth_domain/http_enabled",
            True,
        )

        self._opensearch.config.put(
            self.SECURITY_CONFIG_YML,
            "config/dynamic/authc/clientcert_auth_domain/transport_enabled",
            True,
        )

        self._opensearch.config.append(
            self.JVM_OPTIONS,
            "-Djdk.tls.client.protocols=TLSv1.2",
        )

    def add_oidc_auth(self, openid_connect_url: str):
        """Adds OIDC auth scheme on the security config."""
        oidc_config = {
            "http_enabled": True,
            "transport_enabled": True,
            # NOTE: Order value needs to be lower than basic_internal_auth_domain section, which
            # is set to 4 by default. Only available number is 1, if we want a different number,
            # all other numbers need to be reshuffled.
            "order": 1,
            "http_authenticator": {
                "type": "openid",
                "challenge": False,
                "config": {
                    "subject_key": "sub",
                    "openid_connect_url": openid_connect_url,
                    "openid_connect_idp": {
                        "enable_ssl": True,
                        "verify_hostnames": False,
                        # NOTE: this assumes Hydra and Opensearch are using the same certificates
                        # relation.
                        "pemtrustedcas_filepath": f"{self._opensearch.paths.certs}/chain.pem",
                    },
                },
            },
            "authentication_backend": {"type": "noop"},
        }
        self._opensearch.config.put(
            self.SECURITY_CONFIG_YML,
            "config/dynamic/authc/openid_auth_domain",
            oidc_config,
        )

    def remove_oidc_auth(self):
        """Removes the OIDC auth scheme from security config."""
        self._opensearch.config.delete(
            self.SECURITY_CONFIG_YML, "config/dynamic/authc/openid_auth_domain"
        )

    def apply_performance_profile(self, profile: OpenSearchPerfProfile):
        """Apply the performance profile to the opensearch config."""
        self._opensearch.config.replace(
            self.JVM_OPTIONS,
            "-Xms[0-9]+[kmgKMG]",
            f"-Xms{str(profile.heap_size_in_kb)}k",
            regex=True,
        )

        self._opensearch.config.replace(
            self.JVM_OPTIONS,
            "-Xmx[0-9]+[kmgKMG]",
            f"-Xmx{str(profile.heap_size_in_kb)}k",
            regex=True,
        )

        for key, val in profile.opensearch_yml.items():
            self._opensearch.config.put(self.CONFIG_YML, key, val)

    def set_admin_tls_conf(self, secrets: Dict[str, any]):
        """Configures the admin certificate."""
        self._opensearch.config.put(
            self.CONFIG_YML,
            "plugins.security.authcz.admin_dn/{}",
            f"{normalized_tls_subject(secrets['subject'])}",
        )

    def set_node_tls_conf(self, cert_type: CertType, truststore_pwd: str, keystore_pwd: str):
        """Configures TLS for nodes."""
        target_conf_layer = "http" if cert_type == CertType.UNIT_HTTP else "transport"

        for store_type, cert in [("keystore", target_conf_layer), ("truststore", "ca")]:
            self._opensearch.config.put(
                self.CONFIG_YML,
                f"plugins.security.ssl.{target_conf_layer}.{store_type}_type",
                "PKCS12",
            )

            self._opensearch.config.put(
                self.CONFIG_YML,
                f"plugins.security.ssl.{target_conf_layer}.{store_type}_filepath",
                f"{self._opensearch.paths.certs_relative}/{cert if cert == 'ca' else cert_type}.p12",
            )

        self._opensearch.config.put(
            self.CONFIG_YML,
            f"plugins.security.ssl.{target_conf_layer}.keystore_alias",
            cert_type.val,
        )
        self._opensearch.config.put(
            self.CONFIG_YML,
            f"plugins.security.ssl.{target_conf_layer}.keystore_keypassword",
            keystore_pwd,
        )

        for store_type, pwd in [
            ("keystore", keystore_pwd),
            ("truststore", truststore_pwd),
        ]:
            self._opensearch.config.put(
                self.CONFIG_YML,
                f"plugins.security.ssl.{target_conf_layer}.{store_type}_password",
                pwd,
            )

        self._opensearch.config.put(
            self.CONFIG_YML,
            f"plugins.security.ssl.{target_conf_layer}.enabled_protocols",
            "TLSv1.2",
        )

    def append_transport_node(self, ip_pattern_entries: List[str], append: bool = True):
        """Set the IP address of the new unit in nodes_dn."""
        if not append:
            self._opensearch.config.put(
                self.CONFIG_YML,
                "plugins.security.nodes_dn",
                ip_pattern_entries,
            )
            return

        for entry in ip_pattern_entries:
            self._opensearch.config.put(
                self.CONFIG_YML,
                "plugins.security.nodes_dn/{}",
                entry,
            )

    def set_node(
        self,
        app: App,
        cluster_name: str,
        unit_name: str,
        roles: List[str],
        cm_names: List[str],
        cm_ips: List[str],
        contribute_to_bootstrap: bool,
        node_temperature: Optional[str] = None,
    ) -> None:
        """Set base config for each node in the cluster."""
        self._opensearch.config.put(self.CONFIG_YML, "cluster.name", cluster_name)
        self._opensearch.config.put(self.CONFIG_YML, "node.name", unit_name)
        self._opensearch.config.put(
            self.CONFIG_YML, "network.host", ["_site_"] + self._opensearch.network_hosts
        )
        if self._opensearch.host:
            self._opensearch.config.put(
                self.CONFIG_YML, "network.publish_host", self._opensearch.host
            )
        self._opensearch.config.put(
            self.CONFIG_YML, "http.publish_host", self._opensearch.public_address
        )

        self._opensearch.config.put(
            self.CONFIG_YML, "node.roles", roles, inline_array=len(roles) == 0
        )
        if node_temperature:
            self._opensearch.config.put(self.CONFIG_YML, "node.attr.temp", node_temperature)
        else:
            self._opensearch.config.delete(self.CONFIG_YML, "node.attr.temp")

        # Set the current app full id
        self._opensearch.config.put(self.CONFIG_YML, "node.attr.app_id", app.id)

        # This allows the new CMs to be discovered automatically (hot reload of unicast_hosts.txt)
        self._opensearch.config.put(self.CONFIG_YML, "discovery.seed_providers", "file")
        self.add_seed_hosts(cm_ips)

        if "cluster_manager" in roles and contribute_to_bootstrap:  # cluster NOT bootstrapped yet
            self._opensearch.config.put(
                self.CONFIG_YML, "cluster.initial_cluster_manager_nodes", cm_names
            )

        self._opensearch.config.put(self.CONFIG_YML, "path.data", self._opensearch.paths.data)
        self._opensearch.config.put(self.CONFIG_YML, "path.logs", self._opensearch.paths.logs)

        self._opensearch.config.replace(
            self.JVM_OPTIONS, "=logs/", f"={self._opensearch.paths.logs}/"
        )

        self._opensearch.config.put(self.CONFIG_YML, "plugins.security.disabled", False)
        self._opensearch.config.put(self.CONFIG_YML, "plugins.security.ssl.http.enabled", True)
        self._opensearch.config.put(
            self.CONFIG_YML,
            "plugins.security.ssl.transport.enforce_hostname_verification",
            True,
        )

        # security plugin rest API access
        self._opensearch.config.put(
            self.CONFIG_YML,
            "plugins.security.restapi.roles_enabled",
            ["all_access", "security_rest_api_access"],
        )
        # to use the PUT and PATCH methods of the security rest API
        self._opensearch.config.put(
            self.CONFIG_YML,
            "plugins.security.unsupported.restapi.allow_securityconfig_modification",
            True,
        )

        # enable hot reload of TLS certs (without restarting the node)
        self._opensearch.config.put(
            self.CONFIG_YML,
            "plugins.security.ssl_cert_reload_enabled",
            True,
        )

    def remove_temporary_data_role(self):
        """Remove the data role that was added temporarily to the first dedicated CM node."""
        conf = self._opensearch.config.load(self.CONFIG_YML)
        stored_roles = conf.get("node.roles", [])

        if "data" in stored_roles:
            stored_roles.remove("data")

        self._opensearch.config.put(self.CONFIG_YML, "node.roles", stored_roles)

    def add_seed_hosts(self, cm_ips: List[str]):
        """Add CM nodes ips / host names to the seed host list of this unit."""
        cm_ips_set = set(cm_ips)

        # only update the file if there is data to update
        if cm_ips_set:
            with open(self._opensearch.paths.seed_hosts, "w+") as f:
                lines = "\n".join([entry for entry in cm_ips_set if entry.strip()])
                f.write(f"{lines}\n")

    def cleanup_bootstrap_conf(self):
        """Remove some conf entries when the cluster is bootstrapped."""
        self._opensearch.config.delete(self.CONFIG_YML, "cluster.initial_cluster_manager_nodes")

    def get_plugin(self, plugin_config: Dict[str, str] | List[str]) -> Dict[str, Any]:
        """Gets a list of configuration from opensearch.yml."""
        result = {}
        loaded_configs = self.load_node()
        key_list = plugin_config.keys() if isinstance(plugin_config, dict) else plugin_config
        for key in key_list:
            if key in loaded_configs:
                result[key] = loaded_configs[key]
        return result

    def update_plugin(self, plugin_config: Dict[str, Any]) -> None:
        """Adds or removes plugin configuration to opensearch.yml."""
        for key, val in plugin_config.items():
            if not val:
                self._opensearch.config.delete(self.CONFIG_YML, key)
            else:
                self._opensearch.config.put(self.CONFIG_YML, key, val)

    def update_host_if_needed(self) -> bool:
        """Update the opensearch config with the current network hosts, after having started.

        Returns: True if host updated, False otherwise.
        """
        NetworkHost = namedtuple("NetworkHost", ["entry", "old", "new"])

        node = self.load_node()
        result = False
        for host in [
            NetworkHost(
                "network.host",
                set(node.get("network.host", [])),
                set(["_site_"] + self._opensearch.network_hosts),
            ),
            NetworkHost(
                "network.publish_host",
                node.get("network.publish_host"),
                self._opensearch.host,
            ),
            NetworkHost(
                "http.publish_host",
                node.get("http.publish_host"),
                self._opensearch.public_address,
            ),
        ]:
            if not host.old:
                # Unit not configured yet
                continue

            if host.old != host.new:
                logger.info(f"Updating {host.entry} from: {host.old} - to: {host.new}")
                self._opensearch.config.put(self.CONFIG_YML, host.entry, host.new)
                result = True

        return result
