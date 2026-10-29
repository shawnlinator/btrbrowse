"""SSH_ASKPASS helper: shows ssh's question (passphrase, host key confirmation) in a dialog.

ssh runs this as `askpass.py "<prompt>"` and reads the answer from stdout.
"""
import sys

import btrbrowse  # noqa: F401  (selects the GTK versions)
from gi.repository import Gtk


def main(argv):
    prompt = argv[1] if len(argv) > 1 else "Password:"
    secret = "yes/no" not in prompt and "fingerprint" not in prompt.lower()
    dialog = Gtk.MessageDialog(None, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL, "ssh")
    dialog.format_secondary_text(prompt)
    entry = Gtk.Entry()
    entry.set_visibility(not secret)
    entry.set_activates_default(True)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.get_content_area().pack_start(entry, False, False, 6)
    dialog.show_all()
    response = dialog.run()
    answer = entry.get_text()
    dialog.destroy()
    if response != Gtk.ResponseType.OK:
        return 1
    sys.stdout.write(answer + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
