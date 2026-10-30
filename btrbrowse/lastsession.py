"""Remembers the last remote session that connected, to pre-fill the next prompt.

Only what the user typed is kept (host, path map, btrbk config) plus which key they picked:
an ssh-agent public key or the path of a key file, never private key material. The file is
private to the user.
"""
import json
import os

from btrbrowse.remote import ConnectionRequest, KEY_AGENT, KEY_DEFAULT, KEY_FILE


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "btrbrowse", "last-session.json")


def save(request):
    kind, value = request.key
    data = {"target": request.target, "key_kind": kind, "key_value": value,
            "path_map": request.path_map, "config": request.config}
    try:
        os.makedirs(os.path.dirname(path()), mode=0o700, exist_ok=True)
        fd = os.open(path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
    except OSError:
        pass  # remembering is a convenience, never a reason to fail a connection


def load():
    """The remembered ConnectionRequest, or None."""
    try:
        with open(path()) as f:
            data = json.load(f)
        kind = data["key_kind"]
        if kind not in (KEY_DEFAULT, KEY_AGENT, KEY_FILE):
            return None
        return ConnectionRequest(data["target"], (kind, data.get("key_value")),
                                 data.get("path_map", ""), data.get("config", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def forget():
    try:
        os.unlink(path())
    except OSError:
        pass
