from btrbrowse import context
from gi.repository import Gtk
from time import strftime, localtime
from pwd import getpwuid


def user_name(uid):
    try:
        return getpwuid(uid)[0]
    except KeyError:
        return str(uid)


class snapshotsView(Gtk.Widget):
    """List of the btrbk snapshots of one source subvolume"""

    def __init__(self, config):
        super(snapshotsView, self).__init__()
        self.config = config
        self.builder = Gtk.Builder()
        self.builder.add_from_file(context.resource("glade", "snapshotsView.glade"))
        self.builder.connect_signals(self)
        self._TreeView = self.builder.get_object("snapstreeview")
        self.selection = self.builder.get_object("snapshotsSelection")
        self.scrolledwindow = self.builder.get_object("scrolledwindow1")
        self.error = None
        self._TreeView.connect("realize", self.update_view)

    def update_view(self, widget=None, user_data=None):
        treestore = self.get_config_treestore()
        if treestore:
            self._TreeView.set_model(treestore)

    def snapshot_columns(self, snapshot):
        if snapshot[3] == -1:
            date = "Now"
        else:
            date = strftime("%a %x %R", localtime(snapshot[3]))
        return [snapshot[0],
                snapshot[1],
                snapshot[2],
                date,
                user_name(snapshot[4]),
                snapshot[5],
                snapshot[6]]

    def get_config_treestore(self):
        configstree = Gtk.TreeStore(int, int, int, str, str, str, str)
        try:
            snapshots_list = context.backend.ListSnapshots(self.config)
        except Exception as e:
            self.error = str(e)
            return
        self.error = None
        # newest first, the live subvolume ("Now") on top
        for snapshot in reversed(snapshots_list):
            configstree.append(None, self.snapshot_columns(snapshot))
        return configstree
