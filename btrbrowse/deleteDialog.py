from btrbrowse import context
from btrbrowse.snapshotsView import user_name
from gi.repository import Gtk


class deleteDialog(object):
    """Confirm which snapshots to delete"""

    def __init__(self, parent, config, snapshots):
        super(deleteDialog, self).__init__()
        builder = Gtk.Builder()
        builder.add_from_file(context.resource("glade", "deleteSnapshot.glade"))

        self.dialog = builder.get_object("dialogDelete")
        self.dialog.set_transient_for(parent)
        self.deletetreeview = builder.get_object("deletetreeview")
        builder.connect_signals(self)

        self.to_delete = list(snapshots)

        self.deleteTreeStore = Gtk.TreeStore(bool, int, str, str)
        for snapshot in snapshots:
            snapinfo = context.backend.GetSnapshot(config, snapshot)
            self.deleteTreeStore.append(None, [True, snapinfo[0], user_name(snapinfo[4]), snapinfo[5]])
        self.deletetreeview.set_model(self.deleteTreeStore)
        self.deletetreeview.expand_all()

    def run(self):
        response = self.dialog.run()
        self.dialog.destroy()
        return response

    def on_toggle_delete_snapshot(self, widget, path):
        treeiter = self.deleteTreeStore.get_iter(path)
        checked = not self.deleteTreeStore.get_value(treeiter, 0)
        self.deleteTreeStore.set_value(treeiter, 0, checked)
        snapshot_num = self.deleteTreeStore.get_value(treeiter, 1)
        if checked and snapshot_num not in self.to_delete:
            self.to_delete.append(snapshot_num)
        elif not checked and snapshot_num in self.to_delete:
            self.to_delete.remove(snapshot_num)
