import importlib.machinery
import importlib.util
import os
import tempfile
import unittest

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "helper", "btrbrowse-helper")


def load_helper():
    loader = importlib.machinery.SourceFileLoader("btrbrowse_helper", HELPER)
    spec = importlib.util.spec_from_loader("btrbrowse_helper", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HelperValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_safe_relpath_accepts_normal_paths(self):
        self.assertEqual(self.helper.safe_relpath("/a/b.txt"), "a/b.txt")
        self.assertEqual(self.helper.safe_relpath("a/b"), "a/b")

    def test_safe_relpath_rejects_traversal(self):
        for bad in ("", "/", "..", "a/../b", "../etc/passwd", "a//b", "a/./b"):
            with self.assertRaises(self.helper.HelperError, msg=bad):
                self.helper.safe_relpath(bad)

    def test_user_owned_config_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".conf") as f:
            if os.geteuid() == 0:
                self.skipTest("running as root")
            with self.assertRaises(self.helper.HelperError):
                self.helper.trusted_file(f.name)

    def test_missing_config_is_rejected(self):
        with self.assertRaises(self.helper.HelperError):
            self.helper.trusted_file("/nonexistent/btrbk.conf")

    def test_within(self):
        self.assertTrue(self.helper.within("/mnt/a/b", "/mnt/a"))
        self.assertTrue(self.helper.within("/mnt/a", "/mnt/a"))
        self.assertFalse(self.helper.within("/mnt/ab", "/mnt/a"))

    def test_requires_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        self.assertEqual(self.helper.main(["list", "config"]), 2)

    def test_unknown_verb_exits(self):
        with self.assertRaises(SystemExit):
            self.helper.main(["format-disk"])


if __name__ == "__main__":
    unittest.main()


class MountpointTest(unittest.TestCase):
    def test_root_is_a_mountpoint_and_a_plain_dir_is_not(self):
        helper = load_helper()
        self.assertTrue(helper.is_mountpoint("/"))
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(helper.is_mountpoint(d))
