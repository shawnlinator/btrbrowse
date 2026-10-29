import filecmp
import os
import stat


class StatusFlags(object):
    """File status flags, same values snapper uses (snapper/File.h)."""

    # @formatter:off
    CREATED     =   1  # created
    DELETED     =   2  # deleted
    TYPE        =   4  # type has changed
    CONTENT     =   8  # content has changed
    PERMISSIONS =  16  # permissions have changed, see chmod(2)
    OWNER       =  32  # owner has changed, see chown(2)
    GROUP       =  64  # group has changed, see chown(2)
    XATTRS      = 128  # extended attributes changed, see attr(5)
    ACL         = 256  # access control list changed, see acl(5)
    # @formatter:on


def _xattrs(path):
    try:
        names = os.listxattr(path, follow_symlinks=False)
        return {n: os.getxattr(path, n, follow_symlinks=False) for n in names}
    except OSError:
        return {}


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        return None


def _entries(path):
    try:
        with os.scandir(path) as it:
            return {e.name for e in it}
    except OSError:
        return set()


def _compare_entry(a, b, st_a, st_b):
    status = 0
    if stat.S_IFMT(st_a.st_mode) != stat.S_IFMT(st_b.st_mode):
        return StatusFlags.TYPE
    if stat.S_ISREG(st_a.st_mode):
        if st_a.st_size != st_b.st_size or st_a.st_mtime_ns != st_b.st_mtime_ns:
            try:
                if not filecmp.cmp(a, b, shallow=False):
                    status |= StatusFlags.CONTENT
            except OSError:
                status |= StatusFlags.CONTENT
    elif stat.S_ISLNK(st_a.st_mode):
        if os.readlink(a) != os.readlink(b):
            status |= StatusFlags.CONTENT
    if stat.S_IMODE(st_a.st_mode) != stat.S_IMODE(st_b.st_mode):
        status |= StatusFlags.PERMISSIONS
    if st_a.st_uid != st_b.st_uid:
        status |= StatusFlags.OWNER
    if st_a.st_gid != st_b.st_gid:
        status |= StatusFlags.GROUP
    xa, xb = _xattrs(a), _xattrs(b)
    if xa != xb:
        keys = {k for k in set(xa) | set(xb) if xa.get(k) != xb.get(k)}
        if any(k.startswith("system.posix_acl") for k in keys):
            status |= StatusFlags.ACL
        if any(not k.startswith("system.posix_acl") for k in keys):
            status |= StatusFlags.XATTRS
    return status


def _add_tree(root, rel, flag, out):
    """Report everything below rel (inclusive) under root with a single flag."""
    out.append((rel, flag))
    st = _lstat(root + rel)
    if st is not None and stat.S_ISDIR(st.st_mode):
        for name in sorted(_entries(root + rel)):
            _add_tree(root, rel + "/" + name, flag, out)


def compare_trees(begin, end):
    """Compare two directory trees, return [(path relative to the roots, status)].

    btrfs snapshots are plain directories, so this works unprivileged as long as
    the files are readable. Unlike `btrfs subvolume find-new` it sees deletions.
    """
    changes = []

    def walk(rel):
        names_a = _entries(begin + rel)
        names_b = _entries(end + rel)
        for name in sorted(names_a | names_b):
            sub = rel + "/" + name
            st_a, st_b = _lstat(begin + sub), _lstat(end + sub)
            if st_a is None and st_b is None:
                continue
            if st_a is None:
                _add_tree(end, sub, StatusFlags.CREATED, changes)
            elif st_b is None:
                _add_tree(begin, sub, StatusFlags.DELETED, changes)
            else:
                status = _compare_entry(begin + sub, end + sub, st_a, st_b)
                if status:
                    changes.append((sub, status))
                if (stat.S_ISDIR(st_a.st_mode) and stat.S_ISDIR(st_b.st_mode)):
                    walk(sub)

    walk("")
    return changes
