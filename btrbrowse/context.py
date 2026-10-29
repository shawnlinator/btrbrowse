import os

from btrbrowse.btrbk import Backend, DEFAULT_HELPER, helper_from_checkout

backend = None


def resource(*parts):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def init(config=None, remote=None):
    global backend
    helper = os.environ.get("BTRBROWSE_HELPER")
    if not helper:
        helper = DEFAULT_HELPER if os.path.exists(DEFAULT_HELPER) else helper_from_checkout()
    backend = Backend(config, helper, remote)
    return backend
