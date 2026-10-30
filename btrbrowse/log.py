"""File logging, so a crash from the desktop shortcut leaves something to read.

The log is ~/.local/state/btrbrowse/btrbrowse.log (rotated). It records start-up, connections
(never keys or passwords), helper calls with their timing, errors with tracebacks, GTK/GLib
warnings and the open file descriptor count. BTRBROWSE_LOG=debug adds more detail.
"""
import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import time

_fault_file = None


def path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "btrbrowse", "btrbrowse.log")


def open_fds():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


def setup():
    """Call once, as early as possible. Returns the log file path (None if it cannot be written)."""
    global _fault_file
    logger = logging.getLogger("btrbrowse")
    if logger.handlers:
        return path()
    logger.setLevel(logging.DEBUG if os.environ.get("BTRBROWSE_LOG", "").lower() == "debug"
                    else logging.INFO)
    try:
        os.makedirs(os.path.dirname(path()), mode=0o700, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path(), maxBytes=1 << 20, backupCount=3)
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(handler.formatter)
        logger.addHandler(stream)

    def excepthook(kind, value, tb):
        logger.error("uncaught exception", exc_info=(kind, value, tb))
    sys.excepthook = excepthook

    def thread_hook(args):
        logger.error("uncaught exception in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = thread_hook

    try:  # native crashes (segfaults, aborts) leave a Python traceback next to the log
        _fault_file = open(path() + ".fault", "a")
        faulthandler.enable(_fault_file, all_threads=True)
    except OSError:
        pass

    try:  # GTK and GLib print warnings through GLib's logger, not through Python
        from gi.repository import GLib

        def glib_log(domain, level, message, _data=None):
            logger.warning("GLib %s: %s", domain, message)
        for domain in ("Gtk", "Gdk", "GLib", "GLib-GObject", "GtkSourceView", "Gio"):
            GLib.log_set_handler(domain, GLib.LogLevelFlags.LEVEL_MASK, glib_log, None)
    except Exception:  # noqa: logging must never stop the app
        logger.exception("could not hook GLib logging")

    logger.info("btrbrowse started, python %s, pid %d, %d fds open", sys.version.split()[0],
                os.getpid(), open_fds())
    return path()


def watch_fds(interval_seconds=60, warn_at=400):
    """Log the open fd count periodically, loudly when it climbs (a leak crashed the app once)."""
    from gi.repository import GLib
    logger = logging.getLogger("btrbrowse.fds")
    state = {"last": open_fds()}

    def tick():
        n = open_fds()
        if n >= warn_at:
            logger.warning("%d file descriptors open (was %d)", n, state["last"])
            _log_fd_targets(logger)
        elif n > state["last"] + 20:
            logger.info("fd count grew from %d to %d", state["last"], n)
        state["last"] = n
        return True
    GLib.timeout_add_seconds(interval_seconds, tick)


def _log_fd_targets(logger, limit=8):
    """What the open fds point at, grouped, to name the leak."""
    counts = {}
    try:
        for fd in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink("/proc/self/fd/" + fd)
            except OSError:
                continue
            key = target.split(":")[0] if target.startswith(("pipe", "socket", "anon_inode")) else target
            counts[key] = counts.get(key, 0) + 1
    except OSError:
        return
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    logger.warning("fds by target: %s", ", ".join("%s x%d" % kv for kv in top))
