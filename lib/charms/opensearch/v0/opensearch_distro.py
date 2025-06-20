# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base class for Opensearch distributions."""
import json
import logging
import os
import pathlib
import random
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime
from functools import cached_property
from os.path import exists
from typing import Dict, List, Optional, Set, Tuple, Union

import requests
import urllib3.exceptions
from charms.opensearch.v0.constants_charm import GeneratedRoles
from charms.opensearch.v0.helper_charm import (
    format_unit_name,
    mask_sensitive_information,
)
from charms.opensearch.v0.helper_cluster import Node
from charms.opensearch.v0.helper_conf_setter import YamlConfigSetter
from charms.opensearch.v0.helper_http import error_http_retry_log
from charms.opensearch.v0.helper_networking import (
    get_host_ip,
    get_host_public_ip,
    is_reachable,
)
from charms.opensearch.v0.models import App, StartMode
from charms.opensearch.v0.opensearch_exceptions import (
    OpenSearchCmdError,
    OpenSearchError,
    OpenSearchHttpError,
    OpenSearchStartTimeoutError,
)
from charms.opensearch.v0.opensearch_internal_data import Scope
from pydantic.error_wrappers import ValidationError
from tenacity import (
    Retrying,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

# The unique Charmhub library identifier, never change it
LIBID = "7145c219467d43beb9c566ab4a72c454"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2


logger = logging.getLogger(__name__)


class Paths:
    """This class represents the group of Paths that need to be exposed."""

    def __init__(self, home: str, conf: str, data: str, logs: str, jdk: str, tmp: str, bin: str):
        """Constructor of Paths.

        Args:
            home: Home path of Opensearch, equivalent to the env variable ${OPENSEARCH_HOME}
            conf: Path to the config folder of opensearch
            data: Path to the data folder of opensearch
            logs: Path to the logs folder of opensearch
            jdk: Path of the jdk that comes bundled with the opensearch distro
            tmp: JNA temporary directory
            bin: optional, Path to the bin/ folder
        """
        self.home = home
        self.conf = conf
        self.plugins = f"{home}/plugins"
        self.data = data
        self.logs = logs
        self.jdk = jdk
        self.tmp = tmp
        self.bin = bin
        self.certs = f"{conf}/certificates"  # must be under config
        self.certs_relative = "certificates"
        self.seed_hosts = f"{conf}/unicast_hosts.txt"


class OpenSearchDistribution(ABC):
    """This class represents an interface for a Distributed Opensearch (snap, tarball, oci img)."""

    SERVICE_NAME = "daemon"

    def __init__(self, charm, peer_relation_name: str):
        self.paths = self._build_paths()
        self._set_env_variables()

        self.config = YamlConfigSetter(base_path=self.paths.conf)
        self._charm = charm
        self._peer_relation_name = peer_relation_name

    def install(self):
        """Install the package."""
        pass

    def start(self, wait_until_http_200: bool = True):
        """Start the opensearch service."""

        def _is_connected():
            return self.is_node_up() if wait_until_http_200 else self.is_started()

        if self.is_started():
            return

        # start the opensearch service
        self._start_service()

        start = datetime.now()
        while not _is_connected() and (datetime.now() - start).seconds < 180:
            time.sleep(3)
        else:
            raise OpenSearchStartTimeoutError()

    def restart(self):
        """Restart the opensearch service."""
        if self.is_started():
            self.stop()

        self.start()

    def stop(self):
        """Stop OpenSearch."""
        # stop the opensearch service
        self._stop_service()

        start = datetime.now()
        while self.is_started() and (datetime.now() - start).seconds < 60:
            time.sleep(3)

    @abstractmethod
    def _start_service(self):
        """Start the opensearch service."""
        pass

    @abstractmethod
    def _stop_service(self):
        """Stop the opensearch service."""
        pass

    @abstractmethod
    def is_service_started(self, paused: Optional[bool] = False) -> bool:
        """Check if the snap service and JVM process are running.

        Set paused=True if the process was intentionally paused.
        """
        pass

    @abstractmethod
    def start_service_only(self):
        """Start the actual service only (snap / pebble)."""
        pass

    def is_started(self) -> bool:
        """Check if OpenSearch is started."""
        reachable = is_reachable(self.host, self.port)
        if not reachable:
            logger.debug("Cannot connect to the OpenSearch server...")

        return reachable

    @abstractmethod
    def is_failed(self) -> bool:
        """Check if OpenSearch daemon has failed."""
        pass

    def is_node_up(self, host: Optional[str] = None) -> bool:
        """Get status of node. This assumes OpenSearch is Running.

        Defaults to this unit
        """
        host = host or self.host
        if not is_reachable(host, self.port):
            return False

        try:
            resp_code = self.request(
                "GET",
                "/",
                host=host,
                check_hosts_reach=False,
                resp_status_code=True,
                timeout=1,
            )
            return resp_code < 400
        except (OpenSearchHttpError, Exception) as e:
            logger.debug(f"Error when checking if host {host} is up: {e}")
            return False

    def run_bin(self, bin_script_name: str, args: str = None, stdin: str = None) -> str:
        """Run opensearch provided bin command, through the snap.

        Args:
            bin_script_name: opensearch script located in OPENSEARCH_BIN to be executed
            args: arguments passed to the script
            stdin: string input to be passed on the standard input of the subprocess.
        """
        opensearch_command = f"wazuh-indexer.{bin_script_name}"
        return self._run_cmd(opensearch_command, args, stdin=stdin)

    def run_script(self, script_name: str, args: str = None):
        """Run script provided by Opensearch in another directory, relative to OPENSEARCH_HOME."""
        script_path = f"{self.paths.home}/{script_name}"
        if not os.access(script_path, os.X_OK):
            self._run_cmd(f"chmod a+x {script_path}")

        self._run_cmd(f"snap run --shell wazuh-indexer.daemon -- {script_path}", args)

    def request(  # noqa
        self,
        method: str,
        endpoint: str,
        payload: Optional[Union[str, Dict[str, any], List[Dict[str, any]]]] = None,
        host: Optional[str] = None,
        alt_hosts: Optional[List[str]] = None,
        check_hosts_reach: bool = True,
        resp_status_code: bool = False,
        retries: int = 0,
        ignore_retry_on: Optional[List] = None,
        timeout: int = 5,
        cert_files: Optional[Tuple[str]] = None,
    ) -> Union[Dict[str, any], List[any], int]:
        """Make an HTTP request.

        Args:
            method: matching the known http methods.
            endpoint: relative to the base uri.
            payload: str, JSON obj or array body payload.
            host: host of the node we wish to make a request on, by default current host.
            alt_hosts: in case the default host is unreachable, fallback/alternative hosts.
            check_hosts_reach: if true, performs a ping for each host
            resp_status_code: whether to only return the HTTP code from the response.
            retries: number of retries
            ignore_retry_on: don't retry for specific error codes
            timeout: number of seconds before a timeout happens
            cert_files: tuple of cert and key files to use for authentication

        Raises:
            ValueError if method or endpoint are missing
            OpenSearchHttpError if hosts are unreachable
        """

        def call(urls: List[str]) -> requests.Response:
            """Performs an HTTP request."""
            random.shuffle(urls)

            for attempt in Retrying(
                retry=retry_if_exception_type(requests.RequestException)
                | retry_if_exception_type(urllib3.exceptions.HTTPError),
                stop=stop_after_attempt(retries),
                wait=wait_fixed(1),
                before_sleep=error_http_retry_log(logger, retries, method, urls, payload),
                reraise=True,
            ):
                with attempt, requests.Session() as s:
                    url = urls[(attempt.retry_state.attempt_number - 1) % len(urls)]
                    admin_field = self._charm.secrets.password_key("admin")
                    if cert_files:
                        s.cert = cert_files
                    else:
                        s.auth = ("admin", self._charm.secrets.get(Scope.APP, admin_field))

                    request_kwargs = {
                        "method": method.upper(),
                        "url": url,
                        "verify": f"{self.paths.certs}/chain.pem",
                        "headers": {
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                        "timeout": (timeout, timeout),
                    }
                    if payload:
                        request_kwargs["data"] = (
                            json.dumps(payload) if not isinstance(payload, str) else payload
                        )

                    response = s.request(**request_kwargs)
                    try:
                        response.raise_for_status()
                    except requests.RequestException as ex:
                        if ex.response.status_code in (ignore_retry_on or []):
                            raise OpenSearchHttpError(
                                response_text=ex.response.text,
                                response_code=ex.response.status_code,
                            )
                        raise

                    return response

        if None in [endpoint, method]:
            raise ValueError("endpoint or method missing")

        if endpoint.startswith("/"):
            endpoint = endpoint[1:]

        urls = []
        for host_candidate in (host or self.host, *(alt_hosts or [])):
            if check_hosts_reach and not self.is_node_up(host_candidate):
                continue
            urls.append(f"https://{host_candidate}:{self.port}/{endpoint}")
        if not urls:
            raise OpenSearchHttpError(
                f"Host {host or self.host}:{self.port} and alternative_hosts: {alt_hosts or []} not reachable."
            )

        resp = None
        try:
            resp = call(urls)
            if resp_status_code:
                return resp.status_code

            return resp.json()
        except OpenSearchHttpError as e:
            if resp_status_code:
                return e.response_code
            raise
        except (requests.RequestException, urllib3.exceptions.HTTPError) as e:
            if not isinstance(e, requests.RequestException) or e.response is None:
                raise OpenSearchHttpError(response_text=str(e))

            if resp_status_code:
                return e.response.status_code

            raise OpenSearchHttpError(
                response_text=e.response.text, response_code=e.response.status_code
            )
        except requests.JSONDecodeError:
            raise OpenSearchHttpError(response_text=resp.text)
        except Exception as e:
            raise OpenSearchHttpError(response_text=str(e))

    def write_file(self, path: str, data: str, override: bool = True):
        """Persists data into file. Useful for files generated on the fly, such as certs etc."""
        if not override and exists(path):
            return

        parent_dir_path = "/".join(path.split("/")[:-1])
        if parent_dir_path:
            pathlib.Path(parent_dir_path).mkdir(parents=True, exist_ok=True)

        with open(path, mode="w") as f:
            f.write(data)

    @staticmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5), reraise=True)
    def _run_cmd(command: str, args: str = None, stdin: str = None) -> str:
        """Run command.

        Arg:
            command: can contain arguments
            args: command line arguments
            stdin: string input to be passed on the standard input of the subprocess

        Returns the stdout
        """
        command_with_args = command
        if args is not None:
            command_with_args = f"{command} {args}"

        # only log the command and no arguments to avoid logging sensitive information
        command = mask_sensitive_information(command_with_args)
        logger.debug(f"Executing command: {command}")

        try:
            output = subprocess.run(
                command_with_args,
                input=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                env=os.environ,
            )

            logger.debug(f"{command}:\n{output.stdout}")

            if output.returncode != 0:
                logger.debug(f"{command}:\n Stderr: {output.stderr}\n Stdout: {output.stdout}")
                raise OpenSearchCmdError(output.stderr)
        except (TimeoutError, subprocess.TimeoutExpired) as e:
            raise OpenSearchCmdError(e)
        return output.stdout.strip()

    @abstractmethod
    def _build_paths(self) -> Paths:
        """Build the Paths object."""
        pass

    def _set_env_variables(self):
        """Set the necessary environment variables."""
        os.environ["OPENSEARCH_HOME"] = self.paths.home
        os.environ["JAVA_HOME"] = self.paths.jdk
        os.environ["OPENSEARCH_JAVA_HOME"] = self.paths.jdk
        os.environ["OPENSEARCH_PATH_CONF"] = self.paths.conf
        os.environ["OPENSEARCH_TMPDIR"] = self.paths.tmp
        os.environ["OPENSEARCH_PLUGINS"] = self.paths.plugins

    @cached_property
    def node_id(self) -> str:
        """Get the OpenSearch node id corresponding to the current unit."""
        nodes = self.request("GET", "/_nodes").get("nodes")

        for n_id, node in nodes.items():
            if node["name"] == self._charm.unit_name:
                return n_id

    @property
    def roles(self) -> List[str]:
        """Get the list of the roles assigned to this node."""
        try:
            nodes = self.request("GET", f"/_nodes/{self.node_id}", alt_hosts=self._charm.alt_hosts)
            return nodes["nodes"][self.node_id]["roles"]
        except OpenSearchHttpError:
            return self.config.load("opensearch.yml")["node.roles"]

    @property
    def host(self) -> str:
        """Host IP address of the current node."""
        return get_host_ip(self._charm, self._peer_relation_name)

    @property
    def network_hosts(self) -> List[str]:
        """All HTTP/Transport hosts for the current node."""
        return [socket.getfqdn(), self.host]

    @property
    def public_address(self) -> str:
        """Get the public bind address of this unit."""
        return get_host_public_ip() or str(
            self._charm.model.get_binding(self._peer_relation_name).network.ingress_address
        )

    @property
    def port(self) -> int:
        """Return Port of OpenSearch."""
        return 9200

    def current(self) -> Node:  # noqa: C901
        """Returns current Node."""
        try:
            nodes = self.request("GET", f"/_nodes/{self.node_id}", alt_hosts=self._charm.alt_hosts)

            current_node = nodes["nodes"][self.node_id]
            return Node(
                name=current_node["name"],
                roles=current_node["roles"],
                ip=current_node["ip"],
                app=App(id=current_node["attributes"]["app_id"]),
                unit_number=self._charm.unit_id,
                temperature=current_node.get("attributes", {}).get("temp"),
            )

        except OpenSearchHttpError:

            # we try to get the most accurate description of the node from the static config
            conf = self.config.load("opensearch.yml")

            # also, if possible we rely on the Deployment Description (databag)
            deployment_desc = self._charm.opensearch_peer_cm.deployment_desc()

            # Application Priority: Deployment Description
            # Reason: No reason to re-construct the App object
            #  - it's available 99% of scenarios
            #  - it's the same object as a re-constructed one (i.e. no dynamic changes on App)
            if deployment_desc is None:
                try:
                    app = App(id=conf.get("node.attr.app_id"))
                except ValidationError:
                    raise OpenSearchError("Can not determine app details.")
            else:
                app = deployment_desc.app

            # Roles (Temperature) Priority: local config
            # Reason:
            #  - Deployment Description is holding "expected state" (that may not be applied)
            #  - Static config holds the currently applied settings
            try:
                roles = conf["node.roles"]
            except KeyError:
                if deployment_desc:
                    if deployment_desc.start == StartMode.WITH_PROVIDED_ROLES:
                        roles = deployment_desc.config.roles
                    else:
                        roles = GeneratedRoles
                else:
                    raise OpenSearchError("Can not determine roles.")

            temperature = None
            try:
                temperature = conf["node.attr.temp"]
            except KeyError:
                if deployment_desc:
                    temperature = deployment_desc.config.data_temperature

            return Node(
                # NOTE: We are NOT using self._charm.unit_name, as it refers to deployment_desc()
                # that is not to be assumed to be always available at this point
                name=format_unit_name(self._charm.unit, app=app),
                roles=roles,
                ip=self._charm.unit_ip,
                app=app,
                unit_number=self._charm.unit_id,
                temperature=temperature,
            )

    @staticmethod
    def normalize_allocation_exclusions(exclusions: Union[List[str], Set[str], str]) -> Set[str]:
        """Normalize a list of allocation exclusions into a set."""
        if type(exclusions) is list:
            exclusions = set(exclusions)
        elif type(exclusions) is str:
            exclusions = set(exclusions.split(","))

        return exclusions

    def missing_sys_requirements(self) -> List[str]:
        """Checks the system requirements."""

        def apply(prop: str, value: int) -> bool:
            """Apply a sysctl value and check if it was set."""
            try:
                self._run_cmd(f"sysctl -w {prop}={value}")
                return int(self._run_cmd(f"sysctl -n {prop}")) == value
            except OpenSearchCmdError:
                return False

        missing_requirements = []

        prop, val = "vm.max_map_count", 262144
        if int(self._run_cmd(f"sysctl -n {prop}")) < val and not apply(prop, val):
            missing_requirements.append(f"{prop} should be at least {val}")

        prop, val = "vm.swappiness", 1
        if int(self._run_cmd(f"sysctl -n {prop}")) > val and not apply(prop, 0):
            missing_requirements.append(f"{prop} should be at most 1")

        prop, val = "net.ipv4.tcp_retries2", 5
        if int(self._run_cmd(f"sysctl -n {prop}")) > val and not apply(prop, val):
            missing_requirements.append(f"{prop} should be at most {val}")

        return missing_requirements

    @cached_property
    def version(self) -> str:
        """Returns the version number of this opensearch instance.

        Raises:
            OpenSearchError if the GET request fails.
        """
        # Will have a format similar to:
        # Version: 2.14.0, Build: tar/.../2024-05-27T21:17:37.476666822Z, JVM: 21.0.2
        output = self.run_bin("opensearch-bin", "--version 2>/dev/null")
        logger.debug(f"version call output: {output}")
        return output.split(", ")[0].split(": ")[1]
