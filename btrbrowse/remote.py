"""Run btrbrowse-helper on another machine (e.g. the hypervisor) over ssh.

A VM that gets a btrfs disk through virtiofs sees ordinary directories: btrbk and
btrfs can't run there. The helper runs where the real btrfs is, and this module
translates paths between the two views (a share the host calls /srv/share may be
/mnt/share in the guest) so the rest of btrbrowse can keep reading snapshot files locally.

Nothing about the remote (user, host, key, paths) is configured or stored: every time a
connection is made the `prompt` callback is asked for it, and the answers live in memory
only. One ssh master connection serves all helper calls until it is closed or has been
idle for IDLE_SECONDS, after which the next call asks again.
"""
import os
import re
import shlex
import stat
import subprocess
import tempfile

DEFAULT_HELPER = "/usr/libexec/btrbrowse-helper"
DEFAULT_CONFIG = "/etc/btrbk/btrbk.conf"
IDLE_SECONDS = 300

KEY_DEFAULT = "default"  # let ssh choose among the agent's / default keys
KEY_AGENT = "agent"      # a specific key held by the ssh agent (public key line)
KEY_FILE = "file"        # a private key file


class RemoteError(Exception):
    pass


class Cancelled(RemoteError):
    pass


class ConnectionRequest(object):
    """What the user answered when asked to connect."""

    def __init__(self, target, key=(KEY_DEFAULT, None), path_map="", config=""):
        self.target = target.strip()
        self.key = key
        self.path_map = path_map.strip()
        self.config = config.strip()


def parse_path_map(text):
    """"guest=host[,guest=host]" -> [(guest, host)], longest guest prefix first."""
    pairs = []
    for item in filter(None, (text or "").replace(" ", "").split(",")):
        guest, sep, host = item.partition("=")
        if not sep or not guest.startswith("/") or not host.startswith("/"):
            raise ValueError("bad path map entry %r, expected /guest/path=/host/path" % item)
        pairs.append((guest.rstrip("/") or "/", host.rstrip("/") or "/"))
    return sorted(pairs, key=lambda p: -len(p[0]))


def _swap(text, pairs):
    """Replace path prefixes on path boundaries only, so /srv/share does not
    match /srv/share-backup."""
    for src, dst in pairs:
        pattern = r"(?<![\w/.\-])%s(?=[/'\"\s]|$)" % re.escape(src)
        text = re.sub(pattern, lambda m: dst, text)
    return text


def agent_keys():
    """Keys currently offered by the ssh agent: [{"label", "fingerprint", "public"}]."""
    try:
        listing = subprocess.run(["ssh-add", "-L"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 universal_newlines=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    keys = []
    for line in listing.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or not parts[0].startswith(("ssh-", "ecdsa-", "sk-")):
            continue
        fp = subprocess.run(["ssh-keygen", "-lf", "/dev/stdin"], input=line + "\n", stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, universal_newlines=True).stdout.split()
        keys.append({"label": parts[2] if len(parts) > 2 else "(no comment)",
                     "fingerprint": fp[1] if len(fp) > 1 else "", "public": line})
    return keys


class Remote(object):
    def __init__(self, prompt=None, request=None, helper=None, askpass=None):
        self.prompt = prompt
        self.request = request
        self.helper = helper or DEFAULT_HELPER
        self.askpass = askpass  # None: ssh asks on the terminal; else a script path for GUI prompts
        self.path_map = parse_path_map(request.path_map) if request else []

    # -- path translation

    def to_host(self, text):
        return _swap(text, self.path_map)

    def to_guest(self, text):
        return _swap(text, [(host, guest) for guest, host in self.path_map])

    # -- connection

    def _control_path(self):
        runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        return "%s/btrbrowse-ssh-%%C" % runtime

    def _base_options(self):
        return ["-o", "ControlPath=" + self._control_path()]

    def alive(self):
        if not self.request:
            return False
        check = subprocess.run(["ssh"] + self._base_options() + ["-O", "check", self.request.target],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return check.returncode == 0

    def ensure(self):
        """Make sure a connection exists, asking the user for the details if it doesn't."""
        if self.alive():
            return
        if self.prompt is None:
            raise RemoteError("not connected")
        request = self.prompt(self.request)
        if request is None:
            raise Cancelled("connection cancelled")
        self._open(request)

    def _open(self, request):
        keyfile = None
        options = ["-o", "ControlMaster=yes", "-o", "ControlPersist=%d" % IDLE_SECONDS,
                   "-o", "ConnectTimeout=15"] + self._base_options()
        try:
            kind, value = request.key
            if kind == KEY_AGENT:
                fd, keyfile = tempfile.mkstemp(prefix="btrbrowse-key-", suffix=".pub",
                                               dir=os.environ.get("XDG_RUNTIME_DIR") or None)
                with os.fdopen(fd, "w") as f:
                    f.write(value + "\n")
                options += ["-o", "IdentitiesOnly=yes", "-o", "IdentityFile=" + keyfile]
            elif kind == KEY_FILE:
                options += ["-o", "IdentitiesOnly=yes", "-o", "IdentityFile=" + value]
            env = dict(os.environ)
            if self.askpass:
                env.update(SSH_ASKPASS=self.askpass, SSH_ASKPASS_REQUIRE="force")
            try:
                parse_path_map(request.path_map)
            except ValueError as e:
                raise RemoteError(str(e))
            done = subprocess.run(["ssh"] + options + [request.target, "true"], env=env,
                                  stderr=subprocess.PIPE, universal_newlines=True)
            if done.returncode != 0:
                self.request = None
                raise RemoteError("could not connect to %s: %s" % (request.target, done.stderr.strip()))
        finally:
            if keyfile:
                os.unlink(keyfile)
        self.request = request
        self.path_map = parse_path_map(request.path_map)

    def close(self):
        if self.request:
            subprocess.run(["ssh"] + self._base_options() + ["-O", "exit", self.request.target],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- commands

    def argv(self, *args):
        """ssh argv running the helper on the remote through the existing connection."""
        remote_cmd = " ".join(shlex.quote(a) for a in
                              [self.helper, "--config", self.request.config or DEFAULT_CONFIG]
                              + [self.to_host(a) for a in args])
        return self.shell_argv(remote_cmd)

    def shell_argv(self, command):
        """ssh argv running an arbitrary command string (used by the benchmark)."""
        return (["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=no"] + self._base_options()
                + [self.request.target, command])
