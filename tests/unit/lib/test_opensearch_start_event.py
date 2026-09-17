# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for _StartOpenSearch event snapshot/restore backward compatibility."""

import unittest
from unittest.mock import MagicMock

from charms.opensearch.v0.opensearch_base_charm import _StartOpenSearch


class TestStartOpenSearchEventRestore(unittest.TestCase):
    def test_restore_round_trip(self) -> None:
        """A snapshot produced by the current code restores back to the same values."""
        event = _StartOpenSearch(
            MagicMock(), ignore_lock=True, after_upgrade=True, is_first_data_node=True
        )

        restored = _StartOpenSearch(MagicMock())
        restored.restore(event.snapshot())

        self.assertTrue(restored.ignore_lock)
        self.assertTrue(restored.after_upgrade)
        self.assertTrue(restored.is_first_data_node)

    def test_restore_tolerates_snapshot_missing_is_first_data_node(self) -> None:
        """Deferred events snapshotted by a charm revision predating `is_first_data_node`.

        (e.g. after a rollback to an older revision, followed by a re-upgrade) must not crash
        `restore()`; the field should default to False instead.
        """
        legacy_snapshot = {"ignore_lock": True, "after_upgrade": False}

        event = _StartOpenSearch(MagicMock())
        event.restore(legacy_snapshot)

        self.assertTrue(event.ignore_lock)
        self.assertFalse(event.after_upgrade)
        self.assertFalse(event.is_first_data_node)

    def test_restore_tolerates_completely_empty_snapshot(self) -> None:
        event = _StartOpenSearch(MagicMock())
        event.restore({})

        self.assertFalse(event.ignore_lock)
        self.assertFalse(event.after_upgrade)
        self.assertFalse(event.is_first_data_node)


if __name__ == "__main__":
    unittest.main()
