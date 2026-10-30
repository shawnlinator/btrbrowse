import io
import os
import tempfile
import unittest
from unittest import mock

from btrbrowse import lastsession, prompts
from btrbrowse.btrbk import Backend, BtrbkError
from btrbrowse.remote import (Cancelled, ConnectionRequest, KEY_AGENT, KEY_DEFAULT, KEY_FILE,
                              Remote, RemoteError, parse_path_map)

MAP = "/mnt/Local=/srv/remote"


def make_remote(path_map=MAP, config=""):
    return Remote(request=ConnectionRequest("someone@example.invalid", path_map=path_map, config=config))


class PathMapTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_path_map("/a/b=/c, /x=/y/"), [("/a/b", "/c"), ("/x", "/y")])

    def test_longest_prefix_first(self):
        self.assertEqual(parse_path_map("/mnt=/h,/mnt/Local=/srv/remote")[0][0], "/mnt/Local")

    def test_rejects_relative(self):
        for bad in ("a=/b", "/a=b", "/a"):
            with self.assertRaises(ValueError):
                parse_path_map(bad)

    def test_empty_means_no_translation(self):
        self.assertEqual(parse_path_map(""), [])


class TranslationTest(unittest.TestCase):
    def setUp(self):
        self.remote = make_remote()

    def test_local_to_remote(self):
        self.assertEqual(self.remote.to_host("/mnt/Local/.snaps/data.20261029T0119"),
                         "/srv/remote/.snaps/data.20261029T0119")

    def test_remote_output_to_local_keeps_other_paths(self):
        raw = ("snapshot_subvolume='/srv/remote/.snaps/data.1' "
               "target_url='/srv/remote-backup-pool/data' source_url='/srv/remote'")
        out = self.remote.to_guest(raw)
        self.assertIn("'/mnt/Local/.snaps/data.1'", out)
        self.assertIn("source_url='/mnt/Local'", out)
        self.assertIn("'/srv/remote-backup-pool/data'", out)

    def test_similar_prefix_untouched(self):
        self.assertEqual(self.remote.to_host("/mnt/Localx/f"), "/mnt/Localx/f")
        self.assertEqual(self.remote.to_host("/data/mnt/Local/f"), "/data/mnt/Local/f")

    def test_argv_quotes_maps_and_uses_the_existing_connection(self):
        argv = self.remote.argv("stream-diff", "/mnt/Local/a b", "/mnt/Local/c;rm -rf /")
        command = argv[-1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ControlMaster=no", argv)
        self.assertIn("'/srv/remote/a b'", command)
        self.assertIn("'/srv/remote/c;rm -rf /'", command)
        self.assertTrue(command.startswith("/usr/libexec/btrbrowse-helper --config /etc/btrbk/btrbk.conf"))
        self.assertEqual(argv[-2], "someone@example.invalid")

    def test_custom_remote_config(self):
        self.assertIn("--config /srv/other.conf", make_remote(config="/srv/other.conf").argv("list", "config")[-1])


class IsolatedConfig(unittest.TestCase):
    """Keeps the tests away from the real remembered session."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class TtyPromptTest(IsolatedConfig):
    def run_prompt(self, answers, previous=None):
        keys = [{"label": "work key", "fingerprint": "SHA256:abc", "public": "ssh-ed25519 AAAA work key"}]
        with mock.patch.object(prompts, "agent_keys", return_value=keys):
            return prompts.tty_prompt(previous, stdin=io.StringIO(answers), out=io.StringIO())

    def test_default_key(self):
        req = self.run_prompt("me@host\n0\n/a=/b\n\n")
        self.assertEqual((req.target, req.key, req.path_map), ("me@host", (KEY_DEFAULT, None), "/a=/b"))
        self.assertEqual(req.config, "/etc/btrbk/btrbk.conf")

    def test_agent_key(self):
        req = self.run_prompt("me@host\n1\n\n\n")
        self.assertEqual(req.key, (KEY_AGENT, "ssh-ed25519 AAAA work key"))

    def test_key_file(self):
        req = self.run_prompt("me@host\n2\n/some/key\n\n\n")
        self.assertEqual(req.key, (KEY_FILE, "/some/key"))

    def test_cancel_on_empty_input_or_bad_choice(self):
        self.assertIsNone(self.run_prompt(""))
        self.assertIsNone(self.run_prompt("me@host\n9\n"))

    def test_previous_answers_are_only_suggestions(self):
        previous = ConnectionRequest("old@host", path_map="/x=/y", config="/etc/btrbk/x.conf")
        req = self.run_prompt("\n0\n\n\n", previous)
        self.assertEqual((req.target, req.path_map, req.config), ("old@host", "/x=/y", "/etc/btrbk/x.conf"))


class EnsureTest(unittest.TestCase):
    def test_asks_every_time_the_connection_is_gone(self):
        asked = []
        remote = Remote(prompt=lambda prev: asked.append(prev) or ConnectionRequest("a@b", path_map=""))
        with mock.patch.object(remote, "alive", return_value=False), \
                mock.patch.object(remote, "_open") as opened:
            remote.ensure()
            remote.ensure()
        self.assertEqual(len(asked), 2)
        self.assertEqual(opened.call_count, 2)

    def test_does_not_ask_while_connected(self):
        remote = Remote(prompt=lambda prev: self.fail("should not prompt"),
                        request=ConnectionRequest("a@b"))
        with mock.patch.object(remote, "alive", return_value=True):
            remote.ensure()

    def test_cancel(self):
        remote = Remote(prompt=lambda prev: None)
        with self.assertRaises(Cancelled):
            remote.ensure()

    def test_no_prompt_no_connection(self):
        with self.assertRaises(RemoteError):
            Remote().ensure()

    def test_bad_path_map_is_reported_before_connecting(self):
        remote = Remote(prompt=lambda prev: ConnectionRequest("a@b", path_map="nonsense"))
        with mock.patch.object(remote, "alive", return_value=False), \
                mock.patch("btrbrowse.remote.subprocess.run") as run:
            with self.assertRaises(RemoteError):
                remote.ensure()
        run.assert_not_called()

    def test_backend_turns_remote_errors_into_btrbk_errors(self):
        remote = Remote(prompt=lambda prev: None)
        with self.assertRaises(BtrbkError):
            Backend(remote=remote)._call("list", "config")


if __name__ == "__main__":
    unittest.main()


class LastSessionTest(IsolatedConfig):
    def test_round_trip_and_private_file(self):
        lastsession.save(ConnectionRequest("a@b.invalid", (KEY_AGENT, "ssh-ed25519 AAAA"), "/x=/y", "/c"))
        got = lastsession.load()
        self.assertEqual((got.target, got.key, got.path_map, got.config),
                         ("a@b.invalid", (KEY_AGENT, "ssh-ed25519 AAAA"), "/x=/y", "/c"))
        self.assertEqual(os.stat(lastsession.path()).st_mode & 0o777, 0o600)

    def test_missing_or_corrupt_file_gives_none(self):
        self.assertIsNone(lastsession.load())
        os.makedirs(os.path.dirname(lastsession.path()))
        with open(lastsession.path(), "w") as f:
            f.write("{not json")
        self.assertIsNone(lastsession.load())

    def test_forget(self):
        lastsession.save(ConnectionRequest("a@b.invalid"))
        lastsession.forget()
        self.assertIsNone(lastsession.load())

    def test_tty_prompt_suggests_the_remembered_session(self):
        lastsession.save(ConnectionRequest("a@b.invalid", (KEY_FILE, "/k"), "/x=/y", "/c"))
        out = io.StringIO()
        request = prompts.tty_prompt(stdin=io.StringIO("\n\n/k\n\n\n"), out=out)
        self.assertEqual((request.target, request.path_map, request.config),
                         ("a@b.invalid", "/x=/y", "/c"))

    def test_successful_connection_is_remembered(self):
        request = ConnectionRequest("a@b.invalid", (KEY_DEFAULT, None), "/x=/y")
        remote = Remote(prompt=lambda prev=None: request, remember=lastsession.save)
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stderr="")):
            remote.ensure()
        self.assertEqual(lastsession.load().target, "a@b.invalid")
