"""Tests for SyncState persistence."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest

from state import SyncState


class TestSyncState(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        # Remove so we can test nonexistent-file behaviour
        os.unlink(self.path)

    def tearDown(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_new_state_file(self) -> None:
        s = SyncState(self.path)
        self.assertIsNone(s.get_last_sync("any_mapping"))

    def test_record_and_retrieve_sync(self) -> None:
        s = SyncState(self.path)
        s.record_sync("gw_to_personal", synced_at="2026-03-16T12:00:00Z", created=2, updated=1, deleted=0)
        s.save()

        s2 = SyncState(self.path)
        last = s2.get_last_sync("gw_to_personal")
        self.assertIsNotNone(last)
        self.assertEqual(last["syncedAt"], "2026-03-16T12:00:00Z")
        self.assertEqual(last["created"], 2)
        self.assertEqual(last["updated"], 1)
        self.assertEqual(last["deleted"], 0)

    def test_error_counting(self) -> None:
        s = SyncState(self.path)
        s.record_error("gw_to_personal", "timeout")
        s.record_error("gw_to_personal", "timeout again")
        s.save()

        s2 = SyncState(self.path)
        self.assertEqual(s2.get_error_count("gw_to_personal"), 2)

    def test_error_count_resets_on_success(self) -> None:
        s = SyncState(self.path)
        s.record_error("m", "err1")
        s.record_error("m", "err2")
        s.record_sync("m", synced_at="2026-03-16T12:00:00Z", created=0, updated=0, deleted=0)
        self.assertEqual(s.get_error_count("m"), 0)

    def test_file_permissions(self) -> None:
        s = SyncState(self.path)
        s.save()
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
