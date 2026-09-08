# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import MagicMock, call

from charms.opensearch.v0.opensearch_fixes import OpenSearchFixes


class TestOpenSearchFixes(unittest.TestCase):
    def setUp(self) -> None:
        self.charm = MagicMock()
        self.fixes = OpenSearchFixes(self.charm)

    def test_reconfigure_replicas_of_builtin_indices_targets_expected_indices(self) -> None:
        """All hardcoded built-in indices, including the Wazuh audit log, get the fix applied."""
        self.fixes.apply_on_start()

        expected_calls = [
            call(
                method="PUT",
                endpoint=f"/{index}/_settings",
                payload={"index": {"auto_expand_replicas": "0-all"}},
            )
            for index in [
                ".plugins-ml-config",
                ".opensearch-sap-log-types-config",
                ".opensearch-sap-pre-packaged-rules-config",
                "security-auditlog-*",
            ]
        ]

        self.charm.opensearch.request.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(self.charm.opensearch.request.call_count, len(expected_calls))
