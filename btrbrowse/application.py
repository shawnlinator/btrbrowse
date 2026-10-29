import os, sys, signal, argparse
from gi.repository import Gtk, GLib, Gio
from btrbrowse import context
from btrbrowse.mainWindow import BtrbrowseGUI
from btrbrowse.prompts import gtk_prompt, make_askpass_script
from btrbrowse.remote import Remote


def start_ui():
    parser = argparse.ArgumentParser(description="Browse and compare btrbk snapshots")
    parser.add_argument("-c", "--config", help="btrbk configuration file (default /etc/btrbk/btrbk.conf)")
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

        connectAction = Gio.SimpleAction.new('connect', None)
        connectAction.connect('activate', self.connect_remote)
        self.add_action(connectAction)

        disconnectAction = Gio.SimpleAction.new('disconnect', None)
        disconnectAction.connect('activate', self.disconnect_remote)
        self.add_action(disconnectAction)

        aboutAction = Gio.SimpleAction.new('about', None)
        aboutAction.connect('activate', self.about)
        self.add_action(aboutAction)

        quitAction = Gio.SimpleAction.new('quit', None)
        quitAction.connect('activate', self.quit)
        self.add_action(quitAction)

    def connect_remote(self, action, param):
        """Use a remote host's btrbk (e.g. a VM's hypervisor); asks for every detail."""
        self.disconnect_remote()
        backend = context.backend
        backend.remote = Remote(gtk_prompt(self.btrbrowse.window), askpass=make_askpass_script())
        self.btrbrowse.load()

    def disconnect_remote(self, action=None, param=None):
        backend = context.backend
        if backend.remote:
            backend.remote.close()
            if backend.remote.askpass and os.path.exists(backend.remote.askpass):
                os.unlink(backend.remote.askpass)
            backend.remote = None
            if self.btrbrowse:
                self.btrbrowse.load()

    def about(self, action, param):
        dialog = Gtk.AboutDialog(transient_for=self.btrbrowse.window, modal=True)
        dialog.set_program_name("btrbrowse")
        dialog.set_comments("Browse, compare and restore btrbk snapshots. Based on snapper-gui.")
        dialog.set_license_type(Gtk.License.GPL_2_0)
        dialog.run()
        dialog.destroy()

    def quit(self, action=None, param=None):
        if context.backend.remote:
            context.backend.remote.close()
        self.btrbrowse.window.destroy()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.build_app_menu()

    def do_activate(self):
        if not self.btrbrowse:
            self.btrbrowse = BtrbrowseGUI(self)
        self.btrbrowse.window.present()
