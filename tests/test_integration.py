"""End-to-end tests against a real btrbk setup.

Opt-in: they need a btrfs fixture and root. Set
  BTRBROWSE_IT_CONFIG  root-owned btrbk.conf describing the fixture
  BTRBROWSE_IT_SOURCE  its source subvolume
  BTRBROWSE_ELEVATE    e.g. "sudo -n" (default pkexec)
See README.md "Testing" for how to build the loop-device fixture.
"""
import os
import subprocess
import unittest

from btrbrowse.btrbk import Backend, BtrbkError, CURRENT

CONFIG = os.environ.get("BTRBROWSE_IT_CONFIG")
SOURCE = os.environ.get("BTRBROWSE_IT_SOURCE")
HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "helper", "btrbrowse-helper")


@unittest.skipUnless(CONFIG and SOURCE, "integration fixture not configured")
class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.backend = Backend(CONFIG, HELPER)

    def names(self):
        self.backend.invalidate()
        return [s[5] for s in self.backend.ListSnapshots(SOURCE)]

    def test_config_and_current(self):
        self.assertEqual([c[0] for c in self.backend.ListConfigs()], [SOURCE])
        self.assertEqual(self.backend.ListSnapshots(SOURCE)[0][0], CURRENT)
        self.assertEqual(self.backend.GetMountPoint(SOURCE, CURRENT), SOURCE)

    def test_create_compare_delete(self):
        marker = os.path.join(SOURCE, "it-marker.txt")
        before = len(self.names())
        self.backend.CreateSingleSnapshot(SOURCE)
        self.assertEqual(len(self.names()), before + 1)
        newest = self.backend.ListSnapshots(SOURCE)[-1][0]
        with open(marker, "w") as f:
            f.write("x")
        try:
            files = dict(self.backend.GetFiles(SOURCE, newest, CURRENT))
            self.assertEqual(files.get("/it-marker.txt"), 1)
        finally:
            os.unlink(marker)
        self.backend.DeleteSnapshots(SOURCE, [newest])
        self.assertEqual(len(self.names()), before)

    def test_restore_file(self):
        path = os.path.join(SOURCE, "it-restore.txt")
        with open(path, "w") as f:
            f.write("original")
        self.backend.CreateSingleSnapshot(SOURCE)
        newest = self.backend.ListSnapshots(SOURCE)[-1][0]
        try:
            with open(path, "w") as f:
                f.write("modified")
            self.assertIn("restore", self.backend.RestoreFile(SOURCE, newest, "/it-restore.txt", dry_run=True))
            with open(path) as f:
                self.assertEqual(f.read(), "modified")
            self.backend.RestoreFile(SOURCE, newest, "/it-restore.txt")
            with open(path) as f:
                self.assertEqual(f.read(), "original")
        finally:
            os.unlink(path)
            self.backend.DeleteSnapshots(SOURCE, [newest])

    def test_helper_rejects_paths_outside_snapshots(self):
        for bad in ("/etc", SOURCE, "/mnt"):
            with self.assertRaises(BtrbkError, msg=bad):
                self.backend._call("delete", bad)
        newest_dir = self.backend.GetMountPoint(SOURCE, self.backend.ListSnapshots(SOURCE)[-1][0])
        for rel in ("../../etc/passwd", "/..", "a/../../b"):
            with self.assertRaises(BtrbkError, msg=rel):
                self.backend._call("restore", newest_dir, rel)

    def test_helper_rejects_user_owned_config(self):
        evil = os.path.join(os.path.dirname(HELPER), "..", "tests", "evil.conf")
        with open(evil, "w") as f:
            f.write("volume /\n")
        try:
            with self.assertRaises(BtrbkError):
                Backend(evil, HELPER)._call("list", "snapshots")
        finally:
            os.unlink(evil)

    def test_rollback_roundtrip(self):
        path = os.path.join(SOURCE, "it-rollback.txt")
        self.backend.CreateSingleSnapshot(SOURCE)
        newest = self.backend.ListSnapshots(SOURCE)[-1][0]
        try:
            with open(path, "w") as f:
                f.write("after snapshot")
            plan = self.backend.Rollback(SOURCE, newest, dry_run=True)
            self.assertIn("rename", plan)
            self.assertTrue(os.path.exists(path))
            self.backend.Rollback(SOURCE, newest)
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.unlink(path)
            parent = os.path.dirname(SOURCE)
            for d in os.listdir(parent):
                if ".rollback." in d:
                    subprocess.run(os.environ.get("BTRBROWSE_ELEVATE", "pkexec").split() +
                                   ["btrfs", "subvolume", "delete", os.path.join(parent, d)],
                                   check=True, stdout=subprocess.DEVNULL)
            self.backend.DeleteSnapshots(SOURCE, [newest])


if __name__ == "__main__":
    unittest.main()
