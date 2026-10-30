from btrbrowse import context
from btrbrowse.btrbk import BtrbkError, CURRENT
from btrbrowse.compare import StatusFlags
from btrbrowse.remote import RemoteError
import os, pwd, stat, threading, subprocess, time, difflib, collections
from gi.repository import Gtk, GtkSource, GObject, Gdk, GLib


class changesWindow(object):
    """docstring for changesWindow"""

    TreeNode = collections.namedtuple("TreeNode", "path, children, status, is_dir")

    def __init__(self, config, begin, end):
        super(changesWindow, self).__init__()
        builder = Gtk.Builder()
        GObject.type_register(GtkSource.View)
        builder.add_from_file(context.resource("glade", "changesWindow.glade"))

        builder.get_object("titlelabel").set_text("%s -> %s" % (begin, end))
        self.window = builder.get_object("changesWindow")
        self.statusbar = builder.get_object("statusbar1")
        self.pathstreeview = builder.get_object("pathstreeview")
        self.pathstreeview.connect("row-expanded", self.on_row_expanded)
        self.fileview = builder.get_object("fileview")
        self.choicesviewgroup = builder.get_object("actiongroup")
        builder.connect_signals(self)

        # save mountpoints for begin and end snapshots
        self.beginpath = context.backend.GetMountPoint(config, begin)
        self.endpath = context.backend.GetMountPoint(config, end)
        self.config = config
        self.snapshot_begin = begin
        self.snapshot_end = end

        self.choicesviewgroup.get_action("begin").set_label(str(begin))
        self.choicesviewgroup.get_action("end").set_label(str(end))

        self.sourcebuffer = GtkSource.Buffer()
        self.fileview.set_buffer(self.sourcebuffer)

        self.add_action_buttons(builder.get_object("buttonbox1"))
        self.add_details_pane(builder.get_object("box3"))
        self.details = {}
        self.statuses = {}
        self.loaded = False
        self.dirs = None
        self.window.show_all()
        GObject.idle_add(self.on_idle_init_paths_tree)

    def add_action_buttons(self, box):
        self.restore_button = Gtk.Button.new_with_label("Restore file")
        self.restore_button.set_tooltip_text("Copy the selected file from the first snapshot back into the live subvolume")
        self.restore_button.set_sensitive(False)
        self.restore_button.connect("clicked", self.on_restore_clicked)
        meld_button = Gtk.Button.new_with_label("Open in meld")
        meld_button.connect("clicked", self.on_meld_clicked)
        box.pack_start(self.restore_button, False, False, 0)
        box.pack_start(meld_button, False, False, 0)

    def add_details_pane(self, box):
        self.details_view = Gtk.TextView()
        self.details_view.set_editable(False)
        self.details_view.set_cursor_visible(False)
        self.details_view.set_monospace(True)
        self.details_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.set_min_content_height(110)
        scrolled.add(self.details_view)
        expander = Gtk.Expander.new("Details")
        expander.set_expanded(True)
        expander.add(scrolled)
        box.pack_start(expander, False, False, 0)
        box.reorder_child(expander, 1)

    def describe_file(self, label, path):
        try:
            st = os.lstat(path)
        except OSError:
            return "%s: not present" % label
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        return "%s: %s %s %d bytes, modified %s" % (
            label, stat.filemode(st.st_mode), owner, st.st_size,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)))

    def show_details(self, path):
        lines = []
        if path:
            lines.append(self.file_status_to_string(self.statuses.get(path, 0)))
            lines += ["  - " + d for d in self.details.get(path, [])]
            lines.append("")
            for label, number, base in (("before", self.snapshot_begin, self.beginpath),
                                        ("after", self.snapshot_end, self.endpath)):
                name = "current" if number == CURRENT else "snapshot %s" % number
                lines.append(self.describe_file("%s (%s)" % (label, name), base + path))
        self.details_view.get_buffer().set_text("\n".join(lines))

    def selected_path(self):
        model, treeiter = self.pathstreeview.get_selection().get_selected()
        if treeiter is None or model[treeiter][2] in ("", self.PLACEHOLDER):
            return None
        return model[treeiter][2]

    def show_error(self, message):
        dialog = Gtk.MessageDialog(self.window, 0, Gtk.MessageType.ERROR, Gtk.ButtonsType.OK, message)
        dialog.run()
        dialog.destroy()

    def on_meld_clicked(self, widget):
        path = self.selected_path() or ""
        try:
            subprocess.Popen(["meld", self.beginpath + path, self.endpath + path])
        except OSError as e:
            self.show_error("Could not start meld: %s" % e)

    def on_restore_clicked(self, widget):
        path = self.selected_path()
        if path is None:
            return
        backend = context.backend
        try:
            plan = backend.RestoreFile(self.config, self.snapshot_begin, path, dry_run=True)
        except BtrbkError as e:
            self.show_error(str(e))
            return
        dialog = Gtk.MessageDialog(self.window, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL,
                                   "Restore %s from snapshot %s?" % (path, self.snapshot_begin))
        dialog.format_secondary_text(plan.strip() + "\n\nThe current version will be overwritten.")
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            backend.RestoreFile(self.config, self.snapshot_begin, path)
            self.statusbar.push(2, "Restored %s" % path)
        except BtrbkError as e:
            self.show_error(str(e))

    def add_path_to_tree(self, path, status, tree):
        if self.dirs is not None:
            is_dir = path in self.dirs
        else:
            is_dir = os.path.isdir(self.beginpath + path) or os.path.isdir(self.endpath + path)
        parts = path.split('/')
        node = tree
        # Add directories to tree
        for file_name in parts[1:-1]:
            if not file_name in node.children:
                node.children[file_name] = changesWindow.TreeNode("", {}, 0, True)
            node = node.children[file_name]
        # Add last part of path to tree
        if is_dir:
            node.children[parts[-1]] = changesWindow.TreeNode("", {}, status, True)
        else:
            node.children[parts[-1]] = changesWindow.TreeNode(path, None, status, False)

    def print_tree(self, tree, indent=""):
        for name, child in tree.children.items():
            print(indent + name + ("/" if child.is_dir else "") + "   (" +
                  self.file_status_to_string(child.status) + ")")
            if child.is_dir:
                self.print_tree(child, indent + "    ")

    def file_status_to_string(self, status):
        if status & StatusFlags.CREATED:  # Created file
            return "Created"
        elif status & StatusFlags.DELETED:  # Deleted file
            return "Deleted"
        elif status > 0:  # Modified file
            modification_types = {
                StatusFlags.RENAMED: "renamed",
                StatusFlags.MTIME: "timestamps",
                StatusFlags.ACL: "acl",
                StatusFlags.CONTENT: "content",
                StatusFlags.GROUP: "group",
                StatusFlags.OWNER: "owner",
                StatusFlags.XATTRS: "xattrs",
                StatusFlags.TYPE: "inode type",
                StatusFlags.PERMISSIONS: "permissions"
            }
            modified_list = []
            for flag, name in modification_types.items():
                if status & flag:
                    modified_list.append(name)
            return "Modified: " + ", ".join(modified_list)
        else:
            return "No changes"

    PLACEHOLDER = "\0loading"
    COLORS = {"created": Gdk.RGBA(0.0, 0.57, 0.0), "deleted": Gdk.RGBA(0.6, 0.0, 0.0),
              "modified": Gdk.RGBA(0.49, 0.47, 0.0), "none": Gdk.RGBA(0.0, 0.0, 0.0)}

    def get_treestore_from_tree(self, tree):
        """A store with only the top level filled in; a folder's rows are added when it is
        expanded (see on_row_expanded), so tens of thousands of changes open instantly."""
        # Row: [icon, file name, file complete path, entry color, tooltip, tree node]
        treestore = Gtk.TreeStore(str, str, str, Gdk.RGBA, str, object)
        self.fill_level(treestore, None, tree)
        return treestore

    def fill_level(self, treestore, parent, subtree):
        status_text = {}
        for file_name, child in sorted(subtree.children.items()):
            if child.status & StatusFlags.CREATED:
                color = self.COLORS["created"]
            elif child.status & StatusFlags.DELETED:
                color = self.COLORS["deleted"]
            elif child.status > 0:
                color = self.COLORS["modified"]
            else:
                color = self.COLORS["none"]
            if child.status not in status_text:
                status_text[child.status] = self.file_status_to_string(child.status)
            node = treestore.append(parent, [
                Gtk.STOCK_DIRECTORY if child.is_dir else Gtk.STOCK_FILE,
                file_name, child.path, color, status_text[child.status], child])
            if child.children:
                treestore.append(node, ["", "", self.PLACEHOLDER, self.COLORS["none"], "", None])

    def on_row_expanded(self, treeview, treeiter, treepath):
        store = treeview.get_model()
        first = store.iter_children(treeiter)
        if first is not None and store[first][2] == self.PLACEHOLDER:
            self.fill_level(store, treeiter, store[treeiter][5])
            store.remove(first)

    def on_query_tooltip(self, widget, x, y, keyboard_tip, tooltip):
        if not widget.get_tooltip_context(x, y, keyboard_tip):
            return False
        else:
            on_row, x, y, model, path, it = widget.get_tooltip_context(x, y, keyboard_tip)
            if on_row:
                tooltip.set_text(model[it][4])
                widget.set_tooltip_row(tooltip, path)
                return True
            else:
                return False

    def on_idle_init_paths_tree(self, *args):
        if self.loaded:
            return False
        self.loaded = True
        self.progress = None
        remote = context.backend.remote
        if remote:
            try:
                remote.ensure()  # may show the connect dialog, which must run on this thread
            except RemoteError as e:
                self.statusbar.push(1, "Comparison failed: %s" % e)
                return False
        self.start_progress()
        threading.Thread(target=self.compare_in_thread, daemon=True).start()
        return False

    def start_progress(self):
        """Pulsing bar plus elapsed time: btrfs send cannot tell its total size in advance."""
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress.set_size_request(160, -1)
        self.statusbar.pack_end(self.progress, False, False, 6)
        self.progress.show()
        self.progress_start = time.monotonic()
        self.received = (0, 0)

        def tick():
            if self.progress is None:
                return False
            self.progress.pulse()
            self.statusbar.pop(3)
            size, lines = self.received
            text = "Comparing... %ds" % (time.monotonic() - self.progress_start)
            if lines:
                text += ", %s of changes received (%s lines)" % (self.human_size(size), format(lines, ","))
            else:
                text += " (waiting for the first changes; far-apart snapshots can take minutes)"
            self.statusbar.push(3, text)
            return True
        tick()
        GLib.timeout_add(150, tick)

    @staticmethod
    def human_size(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
            n /= 1024.0

    def set_received(self, size, lines):
        self.received = (size, lines)  # a plain assignment, safe to make from the worker thread

    def stop_progress(self):
        if self.progress is not None:
            Gtk.Container.remove(self.statusbar, self.progress)
            self.progress = None
            self.statusbar.pop(3)

    def compare_in_thread(self):
        try:
            comparison = context.backend.Compare(self.config, self.snapshot_begin, self.snapshot_end,
                                                 progress=self.set_received)
            self.dirs = comparison.dirs
            tree = changesWindow.TreeNode("/", {}, 0, True)
            for entry in comparison.changes:
                self.add_path_to_tree(str(entry[0]), int(entry[1]), tree)
            result = comparison, tree, None
        except BtrbkError as e:
            result = None, None, e
        GLib.idle_add(self.show_comparison, *result)

    def show_comparison(self, comparison, tree, error):
        self.stop_progress()
        if error is not None:
            self.statusbar.push(1, "Comparison failed: %s" % error)
            return False
        dbus_array = comparison.changes
        self.details = comparison.details
        self.dirs = comparison.dirs
        self.statuses = dict(dbus_array)

        self.pathstreeview.set_model(self.get_treestore_from_tree(tree))
        # self.pathstreeview.expand_all()

        # display in statusbar how many files have changed
        message = "%d files, via %s" % (len(dbus_array), comparison.method)
        if comparison.note:
            message += " (%s)" % comparison.note
        self.statusbar.push(1, message)

        # we dont want this function to be called anymore
        return False

    def get_lines_from_file(self, path):
        try:
            return open(path).readlines()
        except IsADirectoryError:
            pass
        except FileNotFoundError:
            return ""
        except UnicodeDecodeError:
            pass
        except PermissionError:
            print("PermissionError")
            pass  # TODO maybe display a dialog with the error?
        return None

    def _on_pathstree_selection_changed(self, selection):
        (model, treeiter) = selection.get_selected()
        if hasattr(self, "details_view"):
            self.show_details(model[treeiter][2] if treeiter is not None else None)
        if hasattr(self, "restore_button"):
            can_restore = (treeiter is not None and bool(model[treeiter][2]) and
                           self.snapshot_begin != CURRENT and
                           os.path.lexists(self.beginpath + model[treeiter][2]))
            self.restore_button.set_sensitive(can_restore)
        if treeiter is not None and model[treeiter] != "":
            # append file path to snapshot mountpoint
            fromfile = self.beginpath + model[treeiter][2]
            tofile = self.endpath + model[treeiter][2]

            fromlines = self.get_lines_from_file(fromfile)
            if fromlines is None:
                return
            elif fromlines == "":
                fromfile = "New File"
                fromdate = ""
            else:
                fromdate = time.ctime(os.stat(fromfile).st_mtime)

            tolines = self.get_lines_from_file(tofile)
            if tolines is None:
                return
            elif tolines == "":
                tofile = "Deleted File"
                todate = ""
            else:
                todate = time.ctime(os.stat(tofile).st_mtime)

            languagemanager = GtkSource.LanguageManager()
            currentview = self.choicesviewgroup.get_action("end").get_current_value()

            if currentview == 0:  # show file from begin snapshot
                self.sourcebuffer.set_language(languagemanager.get_language("text"))
                self.sourcebuffer.set_text("".join(fromlines))
            elif currentview == 1:  # show diff of file changes between snapshots
                self.sourcebuffer.set_language(languagemanager.get_language("diff"))
                difflines = difflib.unified_diff(fromlines,
                                                 tolines,
                                                 fromfile=fromfile,
                                                 tofile=tofile,
                                                 fromfiledate=fromdate,
                                                 tofiledate=todate)
                self.sourcebuffer.set_text("".join(difflines))
            elif currentview == 2:  # show file from end snapshot
                self.sourcebuffer.set_language(languagemanager.get_language("text"))
                self.sourcebuffer.set_text("".join(tolines))
