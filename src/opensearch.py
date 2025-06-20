# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""In this class we manage opensearch distributions specific to the VM charm.

This class handles install / start / stop of opensearch services.
It also exposes some properties and methods for interacting with an OpenSearch Installation
"""
import grp
import logging
import os
import pwd
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from charms.opensearch.v0.constants_charm import OPENSEARCH_SNAP_REVISION
from charms.opensearch.v0.helper_charm import run_cmd
from charms.opensearch.v0.opensearch_distro import OpenSearchDistribution, Paths
from charms.opensearch.v0.opensearch_exceptions import (
    OpenSearchCmdError,
    OpenSearchInstallError,
    OpenSearchMissingError,
    OpenSearchStartError,
    OpenSearchStopError,
)
from charms.operator_libs_linux.v1.systemd import service_failed, service_running
from charms.operator_libs_linux.v2 import snap
from charms.operator_libs_linux.v2.snap import SnapError
from overrides import override
from tenacity import Retrying, retry, stop_after_attempt, wait_exponential, wait_fixed

from utils import extract_tarball

logger = logging.getLogger(__name__)


class OpenSearchSnap(OpenSearchDistribution):
    """Snap distribution of opensearch, only overrides properties and logic proper to the snap."""

    _BASE_SNAP_DIR = "/var/snap/wazuh-indexer"
    _SNAP_DATA = f"{_BASE_SNAP_DIR}/current"
    _SNAP_COMMON = f"{_BASE_SNAP_DIR}/common"
    _SNAP = "/snap/wazuh-indexer/current"

    def __init__(self, charm, peer_relation: str):
        super().__init__(charm, peer_relation)

        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(wait=5)):
            with attempt:
                cache = snap.SnapCache()
                self._opensearch = cache["wazuh-indexer"]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    @override
    def install(self):
        """Install/upgrade opensearch from the snapcraft store."""
        try:
            self._opensearch.ensure(snap.SnapState.Latest, revision=OPENSEARCH_SNAP_REVISION)
            self._opensearch.connect("process-control")
            self._opensearch.connect("log-observe")  # required by wazuh
            self._opensearch.connect("mount-observe")  # required by wazuh
            self._opensearch.connect("system-observe")  # required by wazuh
            self._opensearch.connect("sys-fs-cgroup-service")  # required by wazuh
            self._opensearch.connect("shmem-perf-analyzer")  # required by wazuh
            if not self._opensearch.held:
                # hold the snap in charm determined revision
                self._opensearch.hold()

        except SnapError as e:
            logger.error(f"Failed to install/upgrade opensearch. \n{e}")
            raise OpenSearchInstallError()

    @override
    def is_service_started(self, paused: Optional[bool] = False) -> bool:
        """Check if the snap service and JVM process are running.

        Set paused=True if the process was intentionally paused.
        """
        if not self._opensearch.present:
            return False

        if not service_running("snap.wazuh-indexer.daemon.service"):
            return False

        # Now, we must dig deeper into the actual status of systemd and the JVM process.
        # First, we want to make sure the process is not stopped, dead or zombie.
        try:
            pid = run_cmd("lsof", args="-ti:9200").out.rstrip()
            if not pid or not os.path.exists(f"/proc/{pid}/stat"):
                return False
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
        except subprocess.CalledProcessError:
            return False

        # From: https://github.com/torvalds/linux/blob/ \
        #     8d8d276ba2fb5f9ac4984f5c10ae60858090babc/fs/proc/array.c#L126-L140
        # Possible states to consider:
        # "R (running)",		/* 0x00 */
        # "S (sleeping)",		/* 0x01 */
        # "D (disk sleep)",	/* 0x02 */
        # "T (stopped)",		/* 0x04 */
        # "t (tracing stop)",	/* 0x08 */
        # "X (dead)",		/* 0x10 */
        # "Z (zombie)",		/* 0x20 */
        # "P (parked)",		/* 0x40 */
        # "I (idle)",		/* 0x80 */
        # "Parked" state is ignored as it applies to threads.
        if stat[2] == "T" and paused:
            return True

        # We do not check reachability of the service
        # If that is needed, then use the `is_started` method.
        return stat[2] not in ["Z", "T", "X"]

    @override
    def start_service_only(self):
        """Start the snap service only."""
        if not self._opensearch.present:
            raise OpenSearchMissingError()

        try:
            self._opensearch.start([self.SERVICE_NAME])
        except SnapError as e:
            logger.error(f"Failed to start the opensearch.{self.SERVICE_NAME} service. \n{e}")
            raise OpenSearchStartError()

    @override
    def _start_service(self):
        """Start the snap exposed "daemon" service."""
        if not self._opensearch.present:
            raise OpenSearchMissingError()

        if self._opensearch.services[self.SERVICE_NAME]["active"]:
            logger.info(f"The opensearch.{self.SERVICE_NAME} service is already started.")
            return

        try:
            self._opensearch.start([self.SERVICE_NAME])
        except SnapError as e:
            logger.error(f"Failed to start the opensearch.{self.SERVICE_NAME} service. \n{e}")
            raise OpenSearchStartError()

    @override
    def _stop_service(self):
        """Stop the snap exposed "daemon" service."""
        if not self._opensearch.present:
            raise OpenSearchMissingError()

        try:
            self._opensearch.stop([self.SERVICE_NAME])
        except SnapError as e:
            logger.error(f"Failed to stop the opensearch.{self.SERVICE_NAME} service. \n{e}")
            raise OpenSearchStopError()

    def is_failed(self) -> bool:
        """Check if snap service failed."""
        if not self._opensearch.present:
            raise OpenSearchMissingError()

        return service_failed("snap.wazuh-indexer.daemon.service")

    @override
    def _set_env_variables(self):
        """Set the necessary environment variables."""
        super()._set_env_variables()

        os.environ["SNAP_LOG_DIR"] = f"${self._SNAP_COMMON}/ops/snap/logs"
        os.environ["OPS_ROOT"] = f"{self._SNAP}/opt/opensearch"
        os.environ["OPENSEARCH_BIN"] = f"{self._SNAP}/usr/share/opensearch/bin"
        os.environ["OPENSEARCH_LIB"] = f"{self.paths.home}/lib"
        os.environ["OPENSEARCH_MODULES"] = f"{self.paths.home}/modules"

        os.environ["OPENSEARCH_VARLIB"] = self.paths.data
        os.environ["OPENSEARCH_VARLOG"] = self.paths.logs

        os.environ["KNN_LIB_DIR"] = f"{self.paths.plugins}/opensearch-knn/lib"

    @override
    def _build_paths(self) -> Paths:
        """Builds a Path object.

        The main paths are:
          - OPENSEARCH_HOME: read-only path ($SNAP/..), where the opensearch binaries are
          - OPENSEARCH_CONF: writeable by root or snap_daemon ($SNAP_COMMON) where config files are
        """
        return Paths(
            home=f"{self._SNAP_DATA}/usr/share/wazuh-indexer",
            conf=f"{self._SNAP_DATA}/etc/wazuh-indexer",
            data=f"{self._SNAP_COMMON}/var/lib/wazuh-indexer",
            logs=f"{self._SNAP_COMMON}/var/log/wazuh-indexer",
            jdk=f"{self._SNAP}/usr/lib/jvm/java-21-openjdk-amd64",
            tmp=f"{self._SNAP_COMMON}/usr/share/tmp",
            bin=f"{self._SNAP}/usr/share/wazuh-indexer/bin",
        )

    def write_file(self, path: str, data: str, override: bool = True):
        """Snap implementation of the write_file."""
        super().write_file(path, data, override=override)

        uid = pwd.getpwnam("snap_daemon").pw_uid
        gid = grp.getgrnam("root").gr_gid
        os.chown(path, uid, gid)


class OpenSearchTarball(OpenSearchDistribution):
    """Tarball distro of opensearch, only overrides properties and logic proper to the tar."""

    def __init__(self, charm, peer_relation: str):
        super().__init__(charm, peer_relation)
        self._create_directories()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    @override
    def install(self):
        """Temporary (will be deleted later) - Download and Un-tar the opensearch distro."""
        url = "https://artifacts.opensearch.org/releases/bundle/opensearch/2.9.0/opensearch-2.9.0-linux-x64.tar.gz"
        try:
            response = requests.get(url)

            tarball_path = "opensearch.tar.gz"
            with open(tarball_path, "wb") as f:
                f.write(response.content)
        except Exception as e:
            logger.error(e)
            raise OpenSearchInstallError()

        extract_tarball(tarball_path, self.paths.home)
        self._create_systemd_unit()

    @override
    def _start_service(self):
        """Start opensearch."""
        try:
            self._setup_linux_perms()
            self._run_cmd(
                "setpriv",
                "--clear-groups --reuid ubuntu --regid ubuntu -- sudo systemctl start opensearch.service",
            )
        except OpenSearchCmdError:
            raise OpenSearchStartError

    @override
    def _stop_service(self):
        """Stop opensearch."""
        try:
            self._run_cmd("systemctl stop opensearch.service")
        except OpenSearchCmdError:
            logger.debug("Failed stopping the opensearch service.")
            raise OpenSearchStopError()

        start = datetime.now()
        while self.is_started() and (datetime.now() - start).seconds < 60:
            time.sleep(3)

    @override
    def is_failed(self) -> bool:
        """Check if the opensearch daemon has failed."""
        return service_failed("opensearch.service")

    @override
    def _build_paths(self) -> Paths:
        return Paths(
            home="/etc/opensearch",
            conf="/etc/opensearch/config",
            data="/mnt/opensearch/data",
            logs="/mnt/opensearch/logs",
            jdk="/etc/opensearch/jdk",
            tmp="/mnt/opensearch/tmp",
        )

    def _create_directories(self) -> None:
        """Create the directories defined in self.paths."""
        for dir_path in self.paths.__dict__.values():
            Path(dir_path).mkdir(parents=True, exist_ok=True)

    def _setup_linux_perms(self):
        """Create ubuntu:ubuntu user:group."""
        self._run_cmd("chown", f"-R ubuntu:ubuntu {self.paths.home}")
        self._run_cmd("chown", "-R ubuntu:ubuntu /mnt/opensearch")

    def _create_systemd_unit(self):
        """Create a systemd unit file to run OpenSearch as a service."""
        env_variables = ""
        for key, val in os.environ.items():
            if key.startswith("OPENSEARCH"):
                env_variables = f"{env_variables}Environment={key}={val}\n"

        unit_content = f"""[Unit]
        Description=OpenSearch Service

        [Service]
        User=ubuntu
        Group=ubuntu
        ExecStart={self.paths.home}/bin/opensearch
        LimitNOFILE=65536:1048576
        {env_variables}

        [Install]
        WantedBy=multi-user.target
        """

        self.write_file(
            "/etc/systemd/system/wazuh-indexer.service",
            "\n".join([line.strip() for line in unit_content.split("\n")]),
        )

        self._run_cmd("systemctl daemon-reload")
