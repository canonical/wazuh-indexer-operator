# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit test for the opensearch keystore library."""
import os
import unittest
from unittest.mock import MagicMock, call

from charms.opensearch.v0.opensearch_exceptions import OpenSearchCmdError
from charms.opensearch.v0.opensearch_keystore import OpenSearchKeystoreError
from ops.testing import Harness

from charm import OpenSearchOperatorCharm

RETURN_LIST_KEYSTORE = """key1
key2
keystore.seed"""


class TestOpenSearchKeystore(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness(OpenSearchOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm
        self.keystore = self.charm.plugin_manager._keystore
        os.path.exists = MagicMock(return_value=True)

    def test_list_except_keystore_not_found(self):
        """Throws exception for missing file when calling list."""
        self.charm.opensearch.run_bin = MagicMock(
            side_effect=OpenSearchCmdError(
                "ERROR: OpenSearch keystore not found at ["
                "/snap/wazuh-indexer/current/config/opensearch.keystore]. "
                "Use 'create' command to create one."
            )
        )
        succeeded = False
        try:
            self.keystore.list
        except OpenSearchKeystoreError as e:
            assert "ERROR: OpenSearch keystore not found at [" in str(e)
            succeeded = True
        finally:
            # We may reach this point because of another exception, check it:
            assert succeeded is True

    def test_keystore_list(self):
        """Tests opensearch-keystore list with real output."""
        self.charm.opensearch.run_bin = MagicMock(return_value=RETURN_LIST_KEYSTORE)
        assert ["key1", "key2", "keystore.seed"] == self.keystore.list

    def test_keystore_add_keypair(self) -> None:
        """Add data to keystore."""
        self.charm.opensearch.request = MagicMock(return_value={"status": 200})
        self.charm.opensearch.run_bin = MagicMock(return_value="")
        self.keystore._add("key1", "secret1")
        self.charm.opensearch.run_bin.assert_has_calls(
            [call("keystore", "add --force key1", stdin="secret1\n")]
        )

    def test_keystore_delete_keypair(self) -> None:
        """Delete data to keystore."""
        self.charm.opensearch.request = MagicMock(return_value={"status": 200})
        self.charm.opensearch.run_bin = MagicMock(return_value="")
        self.keystore._delete("key1")
        self.charm.opensearch.run_bin.assert_has_calls([call("keystore", "remove key1")])
