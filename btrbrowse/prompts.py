"""Ask the user how to connect to the remote host. Nothing is stored between sessions."""
import getpass
import os
import stat
import sys
import tempfile

from btrbrowse.remote import (ConnectionRequest, KEY_AGENT, KEY_DEFAULT, KEY_FILE,
                              DEFAULT_CONFIG, agent_keys)

PATH_MAP_HINT = "/mnt/share=/srv/share"


def key_choices():
    """[(label, (kind, value))] offered to the user."""
    choices = [("Let ssh choose (agent or default keys)", (KEY_DEFAULT, None))]
    for key in agent_keys():
        label = "%s  %s" % (key["label"], key["fingerprint"])
        choices.append(("agent: " + label.strip(), (KEY_AGENT, key["public"])))
    choices.append(("key file...", (KEY_FILE, None)))
    return choices


def tty_prompt(previous=None, stdin=None, out=None):
    """Prompt on the terminal. `previous` (this session only) supplies the suggestions."""
    stdin, out = stdin or sys.stdin, out or sys.stderr

    def ask(question, default=""):
        out.write("%s%s: " % (question, " [%s]" % default if default else ""))
        out.flush()
        line = stdin.readline()
        if not line:
            return None
        return line.strip() or default

    target = ask("Remote host (user@host)", previous.target if previous else "")
    if not target:
        return None
    choices = key_choices()
    for i, (label, _) in enumerate(choices):
        out.write("  %d) %s\n" % (i, label))
    picked = ask("SSH key", "0")
    try:
        key = choices[int(picked)][1]
    except (TypeError, ValueError, IndexError):
        return None
    if key[0] == KEY_FILE:
        path = ask("Path to private key file")
        if not path:
            return None
        key = (KEY_FILE, os.path.expanduser(path))
    path_map = ask("Path map, local=remote (e.g. %s)" % PATH_MAP_HINT, previous.path_map if previous else "")
    config = ask("btrbk config on the remote", previous.config if previous else DEFAULT_CONFIG)
    if path_map is None or config is None:
        return None
    return ConnectionRequest(target, key, path_map, config)


def make_askpass_script():
    """A throwaway SSH_ASKPASS executable that shows ssh's questions in a GTK dialog."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fd, path = tempfile.mkstemp(prefix="btrbrowse-askpass-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    with os.fdopen(fd, "w") as f:
        f.write('#!/bin/sh\nPYTHONPATH=%s exec %s -m btrbrowse.askpass "$@"\n'
                % (_sh_quote(root), _sh_quote(sys.executable)))
    os.chmod(path, stat.S_IRWXU)
    return path


def _sh_quote(text):
    import shlex
    return shlex.quote(text)


def gtk_prompt(parent):
    """Returns a prompt callable that shows the connection dialog."""
    from gi.repository import Gtk

    def prompt(previous=None):
        dialog = Gtk.Dialog(title="Connect to remote host", transient_for=parent, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Connect", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        dialog.get_content_area().pack_start(grid, True, True, 0)

        def row(i, label, widget):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, i, 1, 1)

        target = Gtk.Entry(activates_default=True, placeholder_text="user@host")
        target.set_text(previous.target if previous else "")
        choices = key_choices()
        combo = Gtk.ComboBoxText()
        for label, _ in choices:
            combo.append_text(label)
        combo.set_active(0)
        chooser = Gtk.FileChooserButton(title="Private key file")
        chooser.set_sensitive(False)
        combo.connect("changed", lambda c: chooser.set_sensitive(choices[c.get_active()][1][0] == KEY_FILE))
        path_map = Gtk.Entry(activates_default=True, placeholder_text="local=remote, e.g. " + PATH_MAP_HINT)
        path_map.set_text(previous.path_map if previous else "")
        config = Gtk.Entry(activates_default=True, placeholder_text=DEFAULT_CONFIG)
        config.set_text(previous.config if previous else "")
        row(0, "Remote host", target)
        row(1, "SSH key", combo)
        row(2, "Key file", chooser)
        row(3, "Path map", path_map)
        row(4, "btrbk config", config)
        note = Gtk.Label(label="Nothing here is saved. You will be asked again for each new connection.",
                         xalign=0)
        note.get_style_context().add_class("dim-label")
        grid.attach(note, 0, 5, 2, 1)
        dialog.show_all()
        response = dialog.run()
        request = None
        if response == Gtk.ResponseType.OK and target.get_text().strip():
            key = choices[combo.get_active()][1]
            if key[0] == KEY_FILE:
                key = (KEY_FILE, chooser.get_filename())
            request = ConnectionRequest(target.get_text(), key, path_map.get_text(), config.get_text())
        dialog.destroy()
        return request

    return prompt
