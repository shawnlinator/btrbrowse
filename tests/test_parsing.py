import calendar
import os
import tempfile
import time
import unittest

from btrbrowse.btrbk import parse_raw, parse_timestamp, parse_transaction_log
from btrbrowse.compare import StatusFlags, compare_trees


class TimestampTest(unittest.TestCase):
    def test_short(self):
        epoch, seq = parse_timestamp("data.20150825")
        self.assertEqual(time.localtime(epoch)[:6], (2015, 8, 25, 0, 0, 0))
        self.assertEqual(seq, 0)

    def test_long(self):
        epoch, _ = parse_timestamp("home.20150825T1531")
        self.assertEqual(time.localtime(epoch)[:5], (2015, 8, 25, 15, 31))

    def test_long_iso_uses_offset(self):
        epoch, _ = parse_timestamp("home.20150825T153123+0200")
        self.assertEqual(epoch, calendar.timegm((2015, 8, 25, 13, 31, 23)))

    def test_sequence_suffix(self):
        first = parse_timestamp("x.20150825T1531")
        second = parse_timestamp("x.20150825T1531_2")
        self.assertEqual(first[0], second[0])
        self.assertEqual(second[1], 2)
        self.assertLess(first, second)

    def test_dots_in_name(self):
        self.assertIsNotNone(parse_timestamp("my.vol.name.20150825T1531"))

    def test_no_timestamp(self):
        self.assertIsNone(parse_timestamp("data.backup"))

    def test_invalid_date(self):
        self.assertIsNone(parse_timestamp("data.20151345T1531"))


class RawTest(unittest.TestCase):
    def test_parse_raw(self):
        text = ("format=\"snapshots\" source_url='/mnt/a/data' source_host='' "
                "snapshot_subvolume='/mnt/a/snaps/data.20261029T1308' status='up-to-date'\n"
                "noise line\n")
        rows = parse_raw(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["snapshot_subvolume"], "/mnt/a/snaps/data.20261029T1308")
        self.assertEqual(rows[0]["source_host"], "")
        self.assertEqual(rows[0]["status"], "up-to-date")


class LogTest(unittest.TestCase):
    PUSH = (
        "2026-10-29T13:08:41-0600 startup v0.32.6 - - - # btrbk command line client, version 0.32.6\n"
        "2026-10-29T13:09:42-0600 send-receive starting /mnt/t/backups/data.20261029T1309 "
        "/mnt/s/snaps/data.20261029T1309 /mnt/s/snaps/data.20261029T1308 -\n"
        "2026-10-29T13:09:42-0600 send-receive success /mnt/t/backups/data.20261029T1309 "
        "/mnt/s/snaps/data.20261029T1309 /mnt/s/snaps/data.20261029T1308 -\n")
    PULL = (
        "2026-10-29T02:00:01+0000 send-receive success /srv/backup/laptop/home.20261029T0200 "
        "ssh://laptop.lan/mnt/btr/snaps/home.20261029T0200 - -\n"
        "2026-10-29T02:00:09+0000 send-receive ERROR /srv/backup/laptop/home.20261030T0200 "
        "ssh://laptop.lan/mnt/btr/snaps/home.20261030T0200 - - # ssh: connect timed out\n")

    def test_push(self):
        events = parse_transaction_log(self.PUSH)
        self.assertEqual([e["action"] for e in events], ["startup", "send-receive", "send-receive"])
        self.assertEqual(events[2]["status"], "success")
        self.assertEqual(events[2]["parent"], "/mnt/s/snaps/data.20261029T1308")
        self.assertEqual(events[2]["message"], "")

    def test_pull_has_remote_source_and_local_target(self):
        events = parse_transaction_log(self.PULL)
        self.assertEqual(events[0]["target"], "/srv/backup/laptop/home.20261029T0200")
        self.assertTrue(events[0]["source"].startswith("ssh://laptop.lan/"))
        self.assertEqual(events[1]["status"], "ERROR")
        self.assertEqual(events[1]["parent"], "")


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = os.path.join(self.tmp.name, "a")
        self.b = os.path.join(self.tmp.name, "b")
        os.makedirs(self.a)
        os.makedirs(self.b)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, root, rel, text, mtime=None):
        path = root + rel
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        if mtime:
            os.utime(path, (mtime, mtime))

    def test_created_deleted_changed(self):
        self.write(self.a, "/same.txt", "x", 1000)
        self.write(self.b, "/same.txt", "x", 1000)
        self.write(self.a, "/changed.txt", "one", 1000)
        self.write(self.b, "/changed.txt", "two!", 2000)
        self.write(self.a, "/d/gone.txt", "bye")
        self.write(self.b, "/new/x.txt", "hi")
        result = dict(compare_trees(self.a, self.b))
        self.assertNotIn("/same.txt", result)
        self.assertEqual(result["/changed.txt"], StatusFlags.CONTENT)
        self.assertEqual(result["/d/gone.txt"], StatusFlags.DELETED)
        self.assertEqual(result["/new"], StatusFlags.CREATED)
        self.assertEqual(result["/new/x.txt"], StatusFlags.CREATED)

    def test_touched_but_identical_is_not_a_change(self):
        self.write(self.a, "/f", "same", 1000)
        self.write(self.b, "/f", "same", 2000)
        self.assertEqual(compare_trees(self.a, self.b), [])

    def test_permissions_and_type(self):
        self.write(self.a, "/p", "x", 1000)
        self.write(self.b, "/p", "x", 1000)
        os.chmod(self.b + "/p", 0o600)
        os.chmod(self.a + "/p", 0o644)
        self.write(self.a, "/t", "x")
        os.makedirs(self.b + "/t")
        result = dict(compare_trees(self.a, self.b))
        self.assertEqual(result["/p"], StatusFlags.PERMISSIONS)
        self.assertEqual(result["/t"], StatusFlags.TYPE)

    def test_symlink_target_change(self):
        os.symlink("one", self.a + "/l")
        os.symlink("two", self.b + "/l")
        self.assertEqual(compare_trees(self.a, self.b), [("/l", StatusFlags.CONTENT)])


if __name__ == "__main__":
    unittest.main()


class WalkAbortTest(unittest.TestCase):
    def _tree(self, tmp, files):
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        os.makedirs(a)
        os.makedirs(b)
        for root, mtime in ((a, 1000), (b, 2000)):
            for name, size in files:
                path = os.path.join(root, name)
                with open(path, "wb") as f:
                    f.write(b"0" * size)
                os.utime(path, (mtime, mtime))
        return a, b

    def test_abort_stops_between_entries(self):
        from btrbrowse.compare import WalkAborted
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._tree(tmp, [("f%d" % i, 1) for i in range(5)])
            calls = []

            def abort():
                calls.append(1)
                return len(calls) > 2
            with self.assertRaises(WalkAborted):
                compare_trees(a, b, abort=abort)

    def test_abort_interrupts_a_large_file_compare(self):
        from btrbrowse.compare import WalkAborted
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._tree(tmp, [("big", 3 << 20)])
            calls = []

            def abort():
                calls.append(1)
                return len(calls) > 3  # past the walk's own polls, trips inside the compare
            with self.assertRaises(WalkAborted):
                compare_trees(a, b, abort=abort)

    def test_skip_paths_are_not_entered(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
            os.makedirs(a + "/.snaps")
            os.makedirs(b + "/.snaps")
            with open(b + "/.snaps/new", "w") as f:
                f.write("x")
            self.assertTrue(compare_trees(a, b))
            self.assertEqual(compare_trees(a, b, skip=("/.snaps",)), [])
