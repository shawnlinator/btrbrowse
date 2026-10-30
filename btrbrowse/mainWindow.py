from btrbrowse import context
from btrbrowse.btrbk import BtrbkError, CURRENT
import subprocess
from btrbrowse.deleteDialog import deleteDialog
from btrbrowse.changesWindow import changesWindow
from btrbrowse.snapshotsView import snapshotsView
from btrbrowse.backupsView import backupsView
from gi.repository import Gtk, GLib
import os

BACKUPS = "Backups"
POLL_SECONDS = 15


class BtrbrowseGUI():
    """Main window: one page per btrbk source subvolume plus a backups page"""

    def __init__(self, app):
        super(BtrbrowseGUI, self).__init__()
        self.builder = Gtk.Builder()
        self.builder.add_from_file(context.resource("glade", "mainWindow.glade"))
        self.statusbar = self.builder.get_object("statusbar")
        self.configsGroup = self.builder.get_object("configsGroup")
        self.window = self.builder.get_object("applicationwindow1")
        self.stack = self.builder.get_object("stack1")
        self.builder.connect_signals(self)

        self.window.set_application(app)
        self.add_toolbar_buttons()

        self.configView = {}
        self.backupsView = None
        self.dir_mtimes = {}
        self.load()
        GLib.timeout_add_seconds(POLL_SECONDS, self.poll_snapshot_dirs)
        self.window.show()

    def add_toolbar_buttons(self):
        toolbar = self.builder.get_object("toolbar1")
        for label, tooltip, handler in (
                ("Connect...", "Use btrbk on a remote host (asks for the host and ssh key)", self.on_connect),
                ("Compare in meld", "Compare the first and last selected snapshot in meld", self.on_compare_meld),
                ("Roll back", "Replace the live subvolume with the selected snapshot", self.on_rollback),
                ("Refresh", "Reload snapshots, backups and the log", self.on_refresh)):
            button = Gtk.ToolButton.new(None, label)
            button.set_tooltip_text(tooltip)
            button.connect("clicked", handler)
            toolbar.insert(button, -1)
            button.show()

    def load(self):
        """(Re)build all pages from btrbk's current state."""
        context.backend.invalidate()
        for child in self.stack.get_children():
            self.stack.remove(child)
        self.configView = {}
        try:
            configs = context.backend.ListConfigs()
            self.dir_mtimes = self.read_dir_mtimes()
        except BtrbkError as e:
            configs = []
            if not context.backend.remote and not os.path.exists(context.backend.config):
                GLib.idle_add(self.offer_connect, str(e))
            else:
                self.show_error("Could not read btrbk configuration %s:\n%s" % (context.backend.config, e))
        for config in configs:
            name = str(config[0])
            self.configView[name] = snapshotsView(name)
            self.stack.add_titled(self.configView[name].scrolledwindow, name, name)
            self.configView[name].selection.connect("changed",
                                                    self.on_snapshots_selection_changed)
        if context.backend.remote and configs:
            GLib.idle_add(self.check_remote_paths)
        self.backupsView = backupsView()
        try:
            self.backupsView.update_view()
        except BtrbkError as e:
            self.statusbar.push(1, "Backups unavailable: %s" % e)
        self.stack.add_titled(self.backupsView.widget, BACKUPS, BACKUPS)
        self.stack.show_all()
        self.update_controlls_and_userdatatreeview()

    def read_dir_mtimes(self):
        mtimes = {}
        for path in context.backend.snapshot_dirs():
            try:
                mtimes[path] = os.stat(path).st_mtime_ns
            except OSError:
                mtimes[path] = None
        return mtimes

    def poll_snapshot_dirs(self):
        try:
            current = self.read_dir_mtimes()
        except BtrbkError:
            return True
        if current != self.dir_mtimes:
            self.statusbar.push(1, "Snapshots changed on disk, reloading")
            self.load()
        return True

    def check_remote_paths(self):
        """Snapshots are read from disk here, so the host's paths must exist on this machine."""
        dirs = context.backend.snapshot_dirs()
        missing = [d for d in dirs if not os.path.isdir(d)]
        if missing and len(missing) < len(dirs):
            self.statusbar.push(1, "Not visible on this machine (skipped): %s" % ", ".join(missing))
        elif missing:
            self.show_error(
                "The host's snapshot directory is not visible on this machine:\n\n%s\n\n"
                "Browsing, comparing in meld and restoring need the files here. Reconnect and set "
                "the path map (this machine's mount = the host's path, for example "
                "/mnt/share=/srv/share), or mount the same filesystem at the host's path."
                % "\n".join(missing))
        return False

    def on_connect(self, *args):
        self.window.get_application().activate_action("connect", None)

    def offer_connect(self, error):
        dialog = Gtk.MessageDialog(self.window, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.YES_NO,
                                   "No local btrbk configuration found.")
        dialog.format_secondary_text("%s\n\nConnect to a remote host that runs btrbk (for example the "
                                     "hypervisor of this VM)?" % error)
        answer = dialog.run()
        dialog.destroy()
        if answer == Gtk.ResponseType.YES:
            self.on_connect()
        return False

    def show_error(self, message):
        dialog = Gtk.MessageDialog(self.window, 0, Gtk.MessageType.ERROR, Gtk.ButtonsType.OK, message)
        dialog.run()
        dialog.destroy()

    def get_current_config(self):
        name = self.stack.get_visible_child_name()
        return name if name in self.configView else None

    def selected(self):
        """(config, [snapshot numbers]) of the visible page, in list order (newest first)"""
        config = self.get_current_config()
        if config is None:
            return None, []
        model, paths = self.configView[config].selection.get_selected_rows()
        return config, [model[path][0] for path in paths]

    def on_stack_visible_child_changed(self, stack, property):
        self.update_controlls_and_userdatatreeview()

    def on_snapshots_selection_changed(self, selection):
        self.update_controlls_and_userdatatreeview()

    def update_controlls_and_userdatatreeview(self):
        config, numbers = self.selected()
        userdatatreeview = self.builder.get_object("userdatatreeview")
        self.builder.get_object("configActions").set_sensitive(config is not None)
        if config is None or not numbers:
            self.builder.get_object("snapshotActions").set_sensitive(False)
            userdatatreeview.set_model(None)
            return
        self.builder.get_object("snapshotActions").set_sensitive(True)
        self.builder.get_object("view-changes").set_sensitive(len(numbers) > 1)
        try:
            snapshot_data = context.backend.GetSnapshot(config, numbers[0])
        except BtrbkError:
            return
        details = Gtk.ListStore(str, str)
        details.append(["Path", context.backend.GetMountPoint(config, numbers[0])])
        if snapshot_data[6]:
            details.append(["Backup status", snapshot_data[6]])
        userdatatreeview.set_model(details)

    def on_create_snapshot(self, widget):
        config = self.get_current_config()
        if config is None:
            return
        try:
            context.backend.CreateSingleSnapshot(config)
        except BtrbkError as e:
            self.show_error("Creating the snapshot failed:\n%s" % e)
            return
        self.statusbar.push(1, "Snapshot created for %s" % config)
        self.load()
        self.stack.set_visible_child_name(config)

    def on_delete_snapshot(self, widget):
        config, numbers = self.selected()
        numbers = [n for n in numbers if n != CURRENT]
        if not numbers:
            return
        dialog = deleteDialog(self.window, config, numbers)
        response = dialog.run()
        if response == Gtk.ResponseType.YES and len(dialog.to_delete) > 0:
            try:
                context.backend.DeleteSnapshots(config, dialog.to_delete)
            except BtrbkError as e:
                self.show_error("Deleting failed:\n%s" % e)
            self.statusbar.push(1, "Deleted %d snapshot(s) from %s" % (len(dialog.to_delete), config))
            self.load()
            self.stack.set_visible_child_name(config)

    def on_open_snapshot_folder(self, widget):
        config, numbers = self.selected()
        for number in numbers:
            mountpoint = context.backend.GetMountPoint(config, number)
            subprocess.Popen(['xdg-open', mountpoint])
            self.statusbar.push(1, "Snapshot %s of %s is at %s" % (number, config, mountpoint))

    def on_viewchanges_clicked(self, widget):
        config, numbers = self.selected()
        if len(numbers) > 1:
            # list is newest first; compare the oldest selected against the newest
            changesWindow(config, numbers[-1], numbers[0])

    def on_compare_meld(self, widget):
        config, numbers = self.selected()
        if len(numbers) < 2:
            self.statusbar.push(1, "Select two snapshots to compare in meld")
            return
        paths = [context.backend.GetMountPoint(config, n) for n in (numbers[-1], numbers[0])]
        try:
            subprocess.Popen(["meld"] + paths)
        except OSError as e:
            self.show_error("Could not start meld: %s" % e)

    def on_rollback(self, widget):
        config, numbers = self.selected()
        if len(numbers) != 1 or numbers[0] == CURRENT:
            self.statusbar.push(1, "Select exactly one snapshot to roll back to")
            return
        backend = context.backend
        try:
            plan = backend.Rollback(config, numbers[0], dry_run=True)
        except BtrbkError as e:
            self.show_error("Cannot roll back:\n%s" % e)
            return
        dialog = Gtk.MessageDialog(self.window, 0, Gtk.MessageType.WARNING, Gtk.ButtonsType.OK_CANCEL,
                                   "Roll back %s?" % config)
        dialog.format_secondary_text(plan.strip() + "\n\nPrograms using the old subvolume should be closed first.")
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            backend.Rollback(config, numbers[0])
        except BtrbkError as e:
            self.show_error("Roll back failed:\n%s" % e)
            return
        self.statusbar.push(1, "Rolled %s back" % config)
        self.load()

    def on_refresh(self, widget):
        self.load()

    def on_main_destroy(self, args):
        pass
