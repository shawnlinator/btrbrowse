__version__ = "0.1"

try:
    import gi
    gi.require_version("Gtk", "3.0")
    for _version in ("4", "3.0"):
        try:
            gi.require_version("GtkSource", _version)
            break
        except ValueError:
            continue
except ImportError:
    pass  # the backend and helper work without GTK
