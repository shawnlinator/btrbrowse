import os
import unittest

from btrbrowse.compare import StatusFlags as F
from btrbrowse.stream import fold, parse_dump, unescape

DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "send-dump.txt")


class UnescapeTest(unittest.TestCase):
    def test_space_and_backslash(self):
        self.assertEqual(unescape(r"sp\ ace\\x"), "sp ace\\x")

    def test_octal_and_control(self):
        self.assertEqual(unescape(r"a\011b\nc"), "a\tb\nc")

    def test_plain_text_unchanged(self):
        self.assertEqual(unescape("./s-new/tree/f.txt"), "./s-new/tree/f.txt")


class ParseTest(unittest.TestCase):
    def test_root_and_ops(self):
        root, ops = parse_dump("snapshot  ./s-new   uuid=1 transid=2\n"
                               "unlink    ./s-new/sp\\ ace.txt\n")
        self.assertEqual(root, "./s-new")
        self.assertEqual(ops[1]["path"], "./s-new/sp ace.txt")


class FoldRealDumpTest(unittest.TestCase):
    """tests/data/send-dump.txt is real `btrfs receive --dump` output of a fixture."""

    @classmethod
    def setUpClass(cls):
        with open(DUMP) as f:
            cls.changes = fold(f.read())

    def has(self, path, flag):
        self.assertIn(path, self.changes)
        self.assertTrue(self.changes[path].status & flag,
                        "%s: status %d lacks %d" % (path, self.changes[path].status, flag))

    def text(self, path):
        return " | ".join(self.changes[path].details)

    def test_new_file_is_created_not_a_temp_name(self):
        self.has("/newdir/n.txt", F.CREATED)
        self.assertFalse([p for p in self.changes if "/o27" in p])

    def test_new_directory(self):
        self.has("/newdir", F.CREATED)

    def test_deleted_file(self):
        self.assertEqual(self.changes["/tree/hard.txt"].status, F.DELETED)

    def test_hardlink_plus_unlink_is_a_rename(self):
        self.has("/keep.txt", F.RENAMED)
        self.has("/kept.txt", F.RENAMED)
        self.assertIn("renamed from /keep.txt", self.text("/kept.txt"))
        self.assertIn("renamed to /kept.txt", self.text("/keep.txt"))

    def test_rename_with_escaped_space(self):
        self.has("/sp ace2.txt", F.RENAMED)
        self.assertIn("renamed to /sp ace2.txt", self.text("/sp ace.txt"))

    def test_plain_rename_of_directory(self):
        self.has("/other/sub-moved", F.RENAMED)
        self.assertIn("renamed to /other/sub-moved", self.text("/tree/sub")
                      + self.text("/tree/sub").replace("the previous item here was moved: ", ""))

    def test_metadata_changes_on_renamed_file(self):
        self.has("/kept.txt", F.PERMISSIONS)
        self.has("/kept.txt", F.XATTRS)
        self.assertIn("mode set to 600", self.text("/kept.txt"))
        self.assertIn("xattr user.tag set to 'two'", self.text("/kept.txt"))

    def test_extent_update_is_content_with_size(self):
        self.has("/tree/f.txt", F.CONTENT)
        self.assertIn("1 data range rewritten (11 bytes)", self.text("/tree/f.txt"))

    def test_truncate(self):
        self.has("/sparse.bin", F.CONTENT)
        self.assertIn("truncated to 48.8 KB", self.text("/sparse.bin"))

    def test_replaced_file_is_a_content_change(self):
        self.has("/reflink.txt", F.CONTENT)
        self.assertFalse(self.changes["/reflink.txt"].status & F.CREATED)
        self.assertIn("replaced", self.text("/reflink.txt"))

    def test_real_hardlink_is_created(self):
        self.has("/tree/hard2.txt", F.CREATED)
        self.assertIn("hard link to /tree/f.txt", self.text("/tree/hard2.txt"))

    def test_symlink_replaced(self):
        self.has("/tree/link", F.CONTENT)
        self.assertIn("symlink -> g.txt", self.text("/tree/link"))

    def test_directory_mtime_only_entries_exist_for_the_backend_to_filter(self):
        self.assertEqual(self.changes["/tree"].status, F.MTIME)

    def test_slot_reused_after_move(self):
        self.assertIn("previous item here was moved", self.text("/tree/sub"))


class FoldEdgeCasesTest(unittest.TestCase):
    def test_created_then_deleted_leaves_nothing(self):
        out = fold("snapshot ./r uuid=1\nmkfile ./r/o1-1-0\nrename ./r/o1-1-0 dest=./r/x\nunlink ./r/x\n")
        self.assertEqual(out, {})

    def test_root_entry_is_dropped(self):
        out = fold("snapshot ./r uuid=1\nutimes ./r/ mtime=2026-01-01T00:00:00+0000\n")
        self.assertEqual(out, {})

    def test_remove_xattr_and_acl(self):
        out = fold("snapshot ./r uuid=1\n"
                   "remove_xattr ./r/f name=user.a\n"
                   "set_xattr ./r/f name=system.posix_acl_access len=28\n")
        self.assertTrue(out["/f"].status & F.XATTRS)
        self.assertTrue(out["/f"].status & F.ACL)

    def test_clone_mentions_source(self):
        out = fold("snapshot ./r uuid=1\nclone ./r/f offset=0 len=4096 from=./r/g clone_offset=0\n")
        self.assertTrue(out["/f"].status & F.CONTENT)
        self.assertIn("cloned data from", " ".join(out["/f"].details))


if __name__ == "__main__":
    unittest.main()


class OrphanTest(unittest.TestCase):
    def test_deleted_directory_parked_under_a_temp_name(self):
        from btrbrowse.stream import fold
        dump = ("snapshot        ./nasdata4.1 uuid=a transid=1 parent_uuid=b parent_transid=1\n"
                "rename          ./old/dir dest=./o1-2-0\n"
                "unlink          ./o1-2-0/file\n"
                "rmdir           ./o1-2-0\n"
                "end\n")
        changes = fold(dump)
        self.assertEqual(sorted(changes), ["/old/dir", "/old/dir/file"])
        self.assertTrue(all(c.status == 2 for c in changes.values()))


class DirsTest(unittest.TestCase):
    DUMP = ("snapshot        ./s.1 uuid=a transid=1 parent_uuid=b parent_transid=1\n"
            "utimes          ./vault/ab mtime=2026-10-26T00:32:39-0600 atime=x ctime=y\n"
            "mkfile          ./vault/ab/o5-6-0\n"
            "rename          ./vault/ab/o5-6-0 dest=./vault/ab/chunk\n"
            "mkdir           ./newdir\n"
            "end\n")

    def test_stream_reports_created_dirs(self):
        from btrbrowse.stream import fold_with_dirs
        changes, dirs = fold_with_dirs(self.DUMP)
        self.assertIn("/newdir", dirs)
        self.assertNotIn("/vault/ab/chunk", dirs)

    def test_parents_are_dirs_even_when_the_share_is_unreadable(self):
        from btrbrowse.btrbk import fold_stream
        result, details, dirs = fold_stream(self.DUMP, "/nonexistent/a", "/nonexistent/b")
        paths = [p for p, _ in result]
        self.assertIn("/vault/ab/chunk", paths)
        self.assertNotIn("/vault/ab", paths)  # mtime-only directory, known from its child
        self.assertTrue({"/vault", "/vault/ab", "/newdir"} <= dirs)
