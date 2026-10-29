from btrbrowse import context
from btrbrowse.btrbk import BtrbkError, CURRENT
from btrbrowse.compare import StatusFlags
import os, subprocess, time, difflib, collections
from gi.repository import Gtk, GtkSource, GObject, Gdk


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
        self.window.show_all()

    def add_action_buttons(self, box):
        self.restore_button = Gtk.Button.new_with_label("Restore file")
        self.restore_button.set_tooltip_text("Copy the selected file from the first snapshot back into the live subvolume")
        self.restore_button.set_sensitive(False)
        self.restore_button.connect("clicked", self.on_restore_clicked)
        meld_button = Gtk.Button.new_with_label("Open in meld")
        meld_button.connect("clicked", self.on_meld_clicked)
        box.pack_start(self.restore_button, False, False, 0)
        box.pack_start(meld_button, False, False, 0)

    def selected_path(self):
        model, treeiter = self.pathstreeview.get_selection().get_selected()
        if treeiter is None or not model[treeiter][2]:
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

    def get_treestore_from_tree(self, tree):
        # Row: [gtk-stock-icon, file name, file complete path, entry color, tooltip]
        treestore = Gtk.TreeStore(str, str, str, Gdk.RGBA, str)

        def get_children(subtree, parent=None):
            for file_name, child in subtree.children.items():
                color = Gdk.RGBA(0.0, 0.0, 0.0)
                if child.status & StatusFlags.CREATED:  # Created file
                    color = Gdk.RGBA(0.0, 0.57, 0.0)
                elif child.status & StatusFlags.DELETED:  # Deleted file
                    color = Gdk.RGBA(0.6, 0.0, 0.0)
                elif child.status > 0:  # Modified file
                    color = Gdk.RGBA(0.49, 0.47, 0.0)

                node = treestore.append(parent, [
                    Gtk.STOCK_DIRECTORY if child.is_dir else Gtk.STOCK_FILE,
                    file_name, child.path, color, self.file_status_to_string(child.status)
                ])
                # if this child is a directory get childs
                if child.children is not None:
                    get_children(child, node)

        get_children(tree)
        return treestore

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

    def on_idle_init_paths_tree(self):
        dbus_array = context.backend.GetFiles(self.config, self.snapshot_begin, self.snapshot_end)

        # create structure to sort paths into tree
        files_tree = changesWindow.TreeNode("/", {}, 0, True)
        for entry in dbus_array:
            self.add_path_to_tree(str(entry[0]), int(entry[1]), files_tree)

        # self.print_tree(files_tree)
        self.pathstreeview.set_model(self.get_treestore_from_tree(files_tree))
        # self.pathstreeview.expand_all()

        # display in statusbar how many files have changed
        self.statusbar.push(1, "%d files" % len(dbus_array))

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
