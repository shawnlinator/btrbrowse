import sys, signal, argparse
from gi.repository import Gtk, GLib, Gio
from btrbrowse import context
from btrbrowse.mainWindow import BtrbrowseGUI


def start_ui():
    parser = argparse.ArgumentParser(description="Browse and compare btrbk snapshots")
    parser.add_argument("-c", "--config", help="btrbk configuration file (default /etc/btrbk/btrbk.conf)")
    args, rest = parser.parse_known_args()
    context.init(args.config)
    app = Application()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    exit_status = app.run([sys.argv[0]] + rest)
    sys.exit(exit_status)


class Application(Gtk.Application):
    def __init__(self):
        Gtk.Application.__init__(self)
        GLib.set_application_name("btrbrowse")
        GLib.set_prgname('btrbrowse')

        self.btrbrowse = None

    def build_app_menu(self):
        builder = Gtk.Builder()
        builder.add_from_file(context.resource("ui", "app-menu.ui"))
        menu = builder.get_object('app-menu')
        self.set_app_menu(menu)

        aboutAction = Gio.SimpleAction.new('about', None)
        aboutAction.connect('activate', self.about)
        self.add_action(aboutAction)

        quitAction = Gio.SimpleAction.new('quit', None)
        quitAction.connect('activate', self.quit)
        self.add_action(quitAction)

    def about(self, action, param):
        dialog = Gtk.AboutDialog(transient_for=self.btrbrowse.window, modal=True)
        dialog.set_program_name("btrbrowse")
        dialog.set_comments("Browse, compare and restore btrbk snapshots. Based on snapper-gui.")
        dialog.set_license_type(Gtk.License.GPL_2_0)
        dialog.run()
        dialog.destroy()

    def quit(self, action=None, param=None):
        self.btrbrowse.window.destroy()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.build_app_menu()

    def do_activate(self):
        if not self.btrbrowse:
            self.btrbrowse = BtrbrowseGUI(self)
        self.btrbrowse.window.present()
