import subprocess

from btrbrowse import context
from gi.repository import Gtk


class backupsView(object):
    """Backup targets and transfer history, including pull-based backups.

    With pull backups the source is a remote ssh:// url and the data lives on
    this machine's targets, so there are no local snapshots to browse; the
    targets are discovered from btrbk's own config and its transaction log.
    """

    def __init__(self):
        self.widget = Gtk.Notebook()
        self.targets = Gtk.ListStore(str, str, str, str)  # target, source, last success, status
        self.backups = Gtk.ListStore(str, str, str, str, str, bool)  # snapshot, target, host, type, status, local
        self.history = Gtk.ListStore(str, str, str, str, str)  # time, action, status, target, source
        self.backups_view = self._tree(self.backups, ["Snapshot", "Backup", "Host", "Type", "Status"])
        self.backups_view.connect("row-activated", self.on_backup_activated)
        self.widget.append_page(self._scroll(self._tree(self.targets, [
            "Target (from config and log)", "Source", "Last success", "Last status"])), Gtk.Label("Targets"))
        self.widget.append_page(self._scroll(self.backups_view), Gtk.Label("Backups"))
        self.widget.append_page(self._scroll(self._tree(self.history, [
            "Time", "Action", "Status", "Target", "Source"])), Gtk.Label("History"))
        self.widget.show_all()

    def _scroll(self, child):
        window = Gtk.ScrolledWindow()
        window.set_shadow_type(Gtk.ShadowType.IN)
        window.add(child)
        return window

    def _tree(self, model, titles):
        view = Gtk.TreeView(model=model)
        for i, title in enumerate(titles):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            column.set_resizable(True)
            view.append_column(column)
        return view

    def on_backup_activated(self, view, path, column):
        row = self.backups[path]
        if row[5]:
            subprocess.Popen(["xdg-open", row[1]])

    def update_view(self):
        backend = context.backend
        self.targets.clear()
        self.backups.clear()
        self.history.clear()
        seen = set()
        for t in backend.LogTargets():
            seen.add(t["target"])
            self.targets.append([t["target"], t["source"], t["last"], t["last_status"]])
        for t in backend.ListTargets():
            if t["target"] not in seen:
                self.targets.append([t["target"], "", "", "(configured, no log entries)"])
        for b in backend.ListBackups():
            self.backups.append([b["snapshot"], b["target"], b["host"] or "local",
                                 b["kind"], b["status"], b["local"]])
        for e in backend.History():
            self.history.append([e["time"], e["action"], e["status"], e["target"], e["source"]])
