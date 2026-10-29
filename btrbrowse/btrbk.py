"""btrbk backend: presents btrbk snapshots through a small snapper-like API.

Reads and writes that need root go through the btrbrowse-helper (pkexec), the
snapshot contents themselves are read directly from disk.
"""
import calendar
import datetime
import os
import re
import subprocess
import time

from btrbrowse.compare import StatusFlags, compare_trees
from btrbrowse.remote import RemoteError
from btrbrowse.stream import fold

DEFAULT_CONFIG = "/etc/btrbk/btrbk.conf"
DEFAULT_HELPER = "/usr/libexec/btrbrowse-helper"

CURRENT = 0  # snapshot number of the live subvolume, like snapper's "current"


class BtrbkError(Exception):
    pass


class Comparison(object):
    """Result of comparing two snapshots.

    changes: [(path, status)] sorted by path; details: {path: [text, ...]};
    method says how it was computed, note why a fallback was used (or "").
    """
    STREAM = "btrfs send stream"
    WALK = "directory walk"

    def __init__(self, changes, details, method, note=""):
        self.changes, self.details, self.method, self.note = changes, details, method, note


def parse_raw(text):
    """Parse `btrbk ... --format=raw` output into a list of dicts."""
    rows = []
    for line in text.splitlines():
        if line.startswith("format="):
            rows.append(dict((m.group(1), m.group(2))
                             for m in re.finditer(r"(\w+)='([^']*)'", line)))
    return rows


_TS = re.compile(r"\.(\d{4})(\d{2})(\d{2})"
                 r"(?:T(\d{2})(\d{2})(?:(\d{2})([+-]\d{4}))?)?"
                 r"(?:_(\d+))?$")


def parse_timestamp(name):
    """Return (epoch, sequence) for a btrbk snapshot name, None if it has no timestamp.

    Handles timestamp_format short, long and long-iso, plus the `_N` suffix
    btrbk adds when two snapshots share a timestamp.
    """
    m = _TS.search(name)
    if not m:
        return None
    year, mon, day, hour, minute, sec, tz, seq = m.groups()
    fields = [int(year), int(mon), int(day), int(hour or 0), int(minute or 0), int(sec or 0)]
    try:
        datetime.datetime(*fields)  # reject impossible dates, mktime would normalise them
        if tz:
            offset = datetime.timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5]))
            offset = offset if tz[0] == "+" else -offset
            moment = datetime.datetime(*fields, tzinfo=datetime.timezone(offset))
            epoch = calendar.timegm(moment.utctimetuple())
        else:
            epoch = int(time.mktime(tuple(fields) + (0, 0, -1)))
    except (ValueError, OverflowError):
        return None
    return epoch, int(seq or 0)


def parse_transaction_log(text):
    """Parse btrbk's transaction_log into dicts, oldest first.

    Line format: <time> <action> <status> <target> <source> <parent> <message> [# comment]
    A "-" field is reported as an empty string.
    """
    keys = ("time", "action", "status", "target", "source", "parent", "message")
    events = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) < 6:
            continue
        fields += ["-"] * (len(keys) - len(fields))
        events.append(dict((k, "" if v == "-" else v) for k, v in zip(keys, fields)))
    return events


def fold_stream(text, beginpath, endpath):
    """Fold a `btrfs receive --dump` text into ([(path, status)], {path: [details]}).

    Directories whose only change is their mtime are dropped: it changes whenever an
    entry inside is created or removed.
    """
    changes = fold(text)
    result, details = [], {}
    for path in sorted(changes):
        ch = changes[path]
        if ch.status == StatusFlags.MTIME and (os.path.isdir(endpath + path)
                                               or os.path.isdir(beginpath + path)):
            continue
        result.append((path, ch.status))
        details[path] = ch.details
    return result, details


class Backend(object):
    def __init__(self, config=None, helper=None, remote=None):
        self.config = config or os.environ.get("BTRBROWSE_CONFIG", DEFAULT_CONFIG)
        self.helper = helper or os.environ.get("BTRBROWSE_HELPER", DEFAULT_HELPER)
        self.remote = remote
        self._cache = {}

    def invalidate(self):
        self._cache.clear()

    def _call(self, *args):
        if self.remote:
            try:
                self.remote.ensure()
            except RemoteError as e:
                raise BtrbkError(str(e))
            cmd = self.remote.argv(*args)
        else:
            cmd = [self.helper, "--config", self.config] + list(args)
            if os.geteuid() != 0:
                cmd[:0] = os.environ.get("BTRBROWSE_ELEVATE", "pkexec").split()
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True)
        except OSError as e:
            raise BtrbkError("cannot run %s: %s" % (cmd[0], e))
        if proc.returncode != 0:
            raise BtrbkError(proc.stderr.strip() or "helper exited with %d" % proc.returncode)
        return self.remote.to_guest(proc.stdout) if self.remote else proc.stdout

    def _load(self):
        """Fetch everything with one helper call (one polkit elevation)."""
        if self._cache:
            return
        sections, current = {}, None
        for line in self._call("dump").splitlines(True):
            if line.startswith("@@ "):
                current = line[3:].strip()
                sections[current] = []
            elif current:
                sections[current].append(line)
        for kind, lines in sections.items():
            text = "".join(lines)
            self._cache[kind] = parse_transaction_log(text) if kind == "log" else parse_raw(text)

    def _rows(self, kind):
        self._load()
        return self._cache.get(kind, [])

    def ListConfigs(self):
        """[(name, source subvolume, settings)] with one entry per local source subvolume."""
        configs = {}
        for row in self._rows("config"):
            if row.get("source_host"):
                continue  # remote (pull) source: no local snapshots, see ListBackups
            name = row["source_subvolume"]
            configs.setdefault(name, (name, name, {
                "snapshot_path": row["snapshot_path"],
                "snapshot_name": row["snapshot_name"],
                "snapshot_preserve": row["snapshot_preserve"],
            }))
        return sorted(configs.values())

    def _config_row(self, config):
        for cfg in self.ListConfigs():
            if cfg[0] == config:
                return cfg
        raise BtrbkError("unknown configuration: %s" % config)

    def snapshot_dirs(self):
        return sorted(set(c[2]["snapshot_path"] for c in self.ListConfigs()))

    def ListSnapshots(self, config):
        """[number, type, pre number, epoch, uid, name, backup status, userdata], oldest first.

        Number 0 is the live subvolume (epoch -1); the rest are numbered by age.
        """
        self._config_row(config)
        snaps = []
        for row in self._rows("snapshots"):
            if row.get("source_host") or row["source_subvolume"] != config:
                continue
            path = row["snapshot_subvolume"]
            stamp = parse_timestamp(row["snapshot_name"])
            try:
                st = os.stat(path)
                uid, mtime = st.st_uid, int(st.st_mtime)
            except OSError:
                uid, mtime = 0, 0
            key = stamp or (mtime, 0)
            snaps.append((key, path, row["snapshot_name"], uid, row.get("status", "")))
        snaps.sort()
        result = [[CURRENT, 0, 0, -1, self._uid(config), "current", "", {}]]
        for number, (key, path, name, uid, status) in enumerate(snaps, 1):
            result.append([number, 0, 0, key[0], uid, name, status, {"path": path}])
        return result

    @staticmethod
    def _uid(path):
        try:
            return os.stat(path).st_uid
        except OSError:
            return 0

    def GetSnapshot(self, config, number):
        for snap in self.ListSnapshots(config):
            if snap[0] == int(number):
                return snap
        raise BtrbkError("no snapshot %s in %s" % (number, config))

    def GetMountPoint(self, config, number):
        """btrbk snapshots are ordinary directories, nothing to mount."""
        if int(number) == CURRENT:
            return self._config_row(config)[1]
        return self.GetSnapshot(config, number)[7]["path"]

    def GetFiles(self, config, begin, end):
        return compare_trees(self.GetMountPoint(config, begin), self.GetMountPoint(config, end))

    def Compare(self, config, begin, end):
        """Compare two snapshots, preferring the btrfs send stream.

        The stream (via the helper) lists every create, delete, rename, write range,
        chmod, chown, xattr and timestamp change, including deletions and renames, without
        reading file data. It needs two read-only subvolumes of the same source, with the
        older one first; anything else, or any failure, falls back to a directory walk.
        """
        begin, end = int(begin), int(end)
        try:
            if begin == CURRENT:
                raise BtrbkError("the live subvolume can only be the newer side")
            first = self.GetMountPoint(config, begin)
            if end == CURRENT:
                args = ["stream-diff", "--live", first]
            else:
                args = ["stream-diff", first, self.GetMountPoint(config, end)]
            text = self._call(*args)
        except BtrbkError as e:
            text = None
            note = str(e)
        if text is None:
            if self.remote:
                # A walk over a network share can take hours; report the real error instead.
                raise BtrbkError("stream comparison failed and a directory walk over a remote "
                                 "share is not attempted: " + note)
            walked = self.GetFiles(config, begin, end)
            return Comparison(walked, {}, Comparison.WALK, "stream comparison unavailable: %s; the directory walk can be very slow, "
                              "especially on network shares" % note)
        result, details = fold_stream(text, self.GetMountPoint(config, begin),
                                      self.GetMountPoint(config, end))
        return Comparison(result, details, Comparison.STREAM)

    def CreateSingleSnapshot(self, config):
        out = self._call("snapshot", self._config_row(config)[1])
        self.invalidate()
        return out

    def DeleteSnapshots(self, config, numbers, dry_run=False):
        paths = [self.GetMountPoint(config, n) for n in numbers if int(n) != CURRENT]
        if not paths:
            return ""
        args = ["delete"] + (["--dry-run"] if dry_run else []) + paths
        out = self._call(*args)
        self.invalidate()
        return out

    def RestoreFile(self, config, number, path, dry_run=False):
        snap = self.GetMountPoint(config, number)
        args = ["restore"] + (["--dry-run"] if dry_run else []) + [snap, path]
        return self._call(*args)

    def Rollback(self, config, number, dry_run=False):
        if int(number) == CURRENT:
            raise BtrbkError("cannot roll back to the current state")
        snap = self.GetMountPoint(config, number)
        out = self._call(*(["rollback"] + (["--dry-run"] if dry_run else []) + [snap]))
        self.invalidate()
        return out

    def ListBackups(self):
        """Backups btrbk reports, including those of remote (pull) sources."""
        backups = []
        for row in self._rows("backups"):
            if "backup" not in row.get("type", ""):
                continue
            target = row.get("target_subvolume") or row.get("target_url", "")
            backups.append({
                "source": row.get("source_url", ""),
                "snapshot": row.get("snapshot_name", ""),
                "target": target,
                "host": row.get("target_host", ""),
                "kind": row.get("target_type", ""),
                "status": row.get("status", ""),
                "local": not row.get("target_host") and os.path.isdir(target),
            })
        return backups

    def ListTargets(self):
        return [{"target": r.get("target_url", ""), "host": r.get("target_host", ""),
                 "path": r.get("target_path", "")} for r in self._rows("target")]

    def History(self):
        """Transfer events from btrbk's transaction_log, newest first."""
        events = [e for e in self._rows("log") if e["action"] in ("send-receive", "snapshot", "delete")
                  and e["status"] != "starting"]
        return list(reversed(events))

    def LogTargets(self):
        """Targets seen in the log, with last success time; covers pull setups
        where the source is ssh:// and the target is local."""
        seen = {}
        for e in self.History():
            if e["action"] != "send-receive" or not e["target"]:
                continue
            base = os.path.dirname(e["target"]) if not e["target"].startswith("ssh://") \
                else e["target"].rsplit("/", 1)[0]
            entry = seen.setdefault(base, {"target": base, "source": e["source"],
                                           "last": "", "last_status": e["status"]})
            if e["status"] == "success" and not entry["last"]:
                entry["last"] = e["time"]
        return sorted(seen.values(), key=lambda t: t["target"])


def helper_from_checkout():
    """Path of helper/btrbrowse-helper next to a source checkout, if present."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "helper", "btrbrowse-helper")
    return path if os.path.exists(path) else None
