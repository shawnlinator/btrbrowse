"""Turn `btrfs send --no-data -p A B | btrfs receive --dump` output into file changes.

The stream is ordered for replay, not for humans: a new file is created under a
temporary name (oNNN-NN-N) and then renamed, replacing a file is unlink + create,
and a rename involving hard links is a `link` followed by an `unlink`. This module
folds those sequences back into what happened to each path.
"""
import re

from btrbrowse.compare import StatusFlags

_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r",
            "t": "\t", "v": "\v", " ": " ", "\\": "\\"}
_LINE = re.compile(r"^(\w+)\s+((?:\\.|\S)+)(?:\s+(.*))?$")
_TEMP = re.compile(r"(^|/)o\d+-\d+-\d+$")


def unescape(text):
    """Undo btrfs-progs path escaping (\\ for space and backslash, \\ooo for odd bytes)."""
    out = bytearray()
    i = 0
    raw = text.encode("utf-8", "surrogateescape")
    while i < len(raw):
        c = chr(raw[i])
        if c == "\\" and i + 1 < len(raw):
            nxt = chr(raw[i + 1])
            if nxt in "01234567" and i + 3 < len(raw) + 0 and raw[i + 1:i + 4].isdigit():
                out.append(int(raw[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt].encode()
                i += 2
                continue
        out.append(raw[i])
        i += 1
    return out.decode("utf-8", "surrogateescape")


def parse_dump(text):
    """Return (root, ops); ops are dicts with op, path and the raw attribute text."""
    root = None
    ops = []
    for line in text.splitlines():
        m = _LINE.match(line.rstrip("\n"))
        if not m:
            continue
        op, path, rest = m.group(1), unescape(m.group(2)), m.group(3) or ""
        if op in ("snapshot", "subvol") and root is None:
            root = path.rstrip("/")
        ops.append({"op": op, "path": path, "rest": rest})
    return root, ops


class Change(object):
    def __init__(self, status, detail=None):
        self.status = status
        self.details = [detail] if detail else []
        self.extents = 0
        self.bytes = 0
        self.created = status == StatusFlags.CREATED

    def add(self, flag, detail=None):
        self.status |= flag
        if detail:
            self.details.append(detail)


def _attr(rest, key):
    m = re.search(r"(?:^|\s)%s=(\S+)" % key, rest)
    return m.group(1) if m else None


def _dest(rest):
    m = re.match(r"dest=(.*)$", rest)
    return unescape(m.group(1)) if m else None


def fold(text):
    """Return {path: Change}, paths absolute inside the subvolume ("/dir/file")."""
    root, ops = parse_dump(text)
    changes = {}
    link_origin = {}  # existing path -> paths hard-linked from it in this stream

    def rel(path):
        if root and (path == root or path.startswith(root + "/")):
            path = path[len(root):]
        elif path.startswith("./"):
            path = path[1:]
        return "/" + path.strip("/") if path.strip("/") else "/"

    def get(path, flag=None):
        ch = changes.get(path)
        if ch is None:
            ch = changes[path] = Change(0)
        return ch

    def create(path, detail):
        old = changes.get(path)
        if old is not None and old.status & StatusFlags.DELETED:
            ch = changes[path] = Change(StatusFlags.CONTENT, "replaced (deleted and recreated)")
            ch.details.append(detail)
        else:
            ch = changes[path] = Change(StatusFlags.CREATED, detail)
            if old is not None and old.status & StatusFlags.RENAMED:
                ch.details.insert(0, "the previous item here was moved: " + old.details[0])

    def move(src, dst):
        src_ch = changes.pop(src, None)
        prefix = src + "/"
        for key in [k for k in changes if k.startswith(prefix)]:
            changes[dst + key[len(src):]] = changes.pop(key)
        if src_ch is None:
            return
        old = changes.get(dst)
        if old is not None and old.status & StatusFlags.DELETED:
            src_ch.status = (src_ch.status & ~StatusFlags.CREATED) | StatusFlags.CONTENT
            src_ch.details.insert(0, "replaced (deleted and recreated)")
        elif old is not None and old.status & StatusFlags.RENAMED:
            src_ch.details.insert(0, "the previous item here was moved: " + old.details[0])
        changes[dst] = src_ch

    for entry in ops:
        op, rest = entry["op"], entry["rest"]
        if op in ("snapshot", "subvol", "end"):
            continue
        path = rel(entry["path"])
        if op in ("mkfile", "mkdir", "mkfifo", "mknod", "mksock"):
            kinds = {"mkfile": "file", "mkdir": "directory", "mkfifo": "fifo",
                     "mknod": "device node", "mksock": "socket"}
            create(path, "created %s" % kinds[op])
        elif op == "symlink":
            create(path, "created symlink -> %s" % (_dest(rest) or ""))
        elif op == "link":
            origin = rel(_dest(rest) or "")
            create(path, "hard link to %s" % origin)
            link_origin.setdefault(origin, []).append(path)
        elif op in ("unlink", "rmdir"):
            made = changes.get(path)
            if path in link_origin and link_origin[path]:
                new = link_origin[path].pop(0)
                changes[new] = Change(StatusFlags.RENAMED, "renamed from %s" % path)
                changes[path] = Change(StatusFlags.RENAMED, "renamed to %s" % new)
            elif made is not None and made.created:
                del changes[path]
            else:
                changes[path] = Change(StatusFlags.DELETED,
                                       "removed directory" if op == "rmdir" else "deleted")
        elif op == "rename":
            dst = rel(_dest(rest) or "")
            src_ch = changes.get(path)
            if src_ch is not None and src_ch.created:
                move(path, dst)
            else:
                replaced = changes.get(dst)
                changes[path] = Change(StatusFlags.RENAMED, "renamed to %s" % dst)
                new = Change(StatusFlags.RENAMED, "renamed from %s" % path)
                if replaced is not None and replaced.status & StatusFlags.DELETED:
                    new.details.append("replaced the existing file")
                changes[dst] = new
        elif op in ("update_extent", "write", "clone", "fallocate"):
            ch = get(path)
            ch.add(StatusFlags.CONTENT)
            ch.extents += 1
            try:
                ch.bytes += int(_attr(rest, "len") or 0)
            except ValueError:
                pass
            if op == "clone":
                ch.details.append("cloned data from %s" % (_attr(rest, "from") or "?"))
        elif op == "truncate":
            get(path).add(StatusFlags.CONTENT, "truncated to %s bytes" % _attr(rest, "size"))
        elif op == "chmod":
            get(path).add(StatusFlags.PERMISSIONS, "mode set to %s" % _attr(rest, "mode"))
        elif op == "chown":
            get(path).add(StatusFlags.OWNER | StatusFlags.GROUP,
                          "owner set to uid %s, gid %s" % (_attr(rest, "uid"), _attr(rest, "gid")))
        elif op in ("set_xattr", "remove_xattr"):
            m = re.match(r"name=(\S+)(?: (.*?))?(?: len=(\d+))?$", rest)
            name = m.group(1) if m else rest
            flag = StatusFlags.ACL if name.startswith("system.posix_acl") else StatusFlags.XATTRS
            if op == "remove_xattr":
                get(path).add(flag, "xattr %s removed" % name)
            else:
                value = m.group(2) if m and m.group(2) else ""
                get(path).add(flag, "xattr %s set%s" % (name, " to %r" % value if value else ""))
        elif op == "utimes":
            ch = get(path)
            ch.status |= StatusFlags.MTIME
            ch.details.append("mtime %s" % (_attr(rest, "mtime") or "?"))
        # other commands (e.g. fileattr, enable_verity) are ignored

    for ch in changes.values():
        if ch.extents:
            ch.details.insert(0, "%d data range%s rewritten (%d bytes)" % (
                ch.extents, "" if ch.extents == 1 else "s", ch.bytes))
    return dict((p, c) for p, c in changes.items() if c.status and p != "/")
