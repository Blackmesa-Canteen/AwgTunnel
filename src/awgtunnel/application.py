"""The Adw.Application subclass and its global actions."""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .build_config import APP_ID, VERSION
from .engine import Engine
from .window import AwgTunnelWindow


class AwgTunnelApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.engine = Engine()
        self._add_action("about", self._on_about)
        self._add_action("quit", lambda *_: self.quit())
        self.set_accels_for_action("app.quit", ["<primary>q"])

    def _add_action(self, name: str, callback) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def do_activate(self) -> None:  # noqa: N802 - GObject naming
        window = self.props.active_window
        if window is None:
            window = AwgTunnelWindow(application=self)
        window.present()

    def do_shutdown(self) -> None:  # noqa: N802 - GObject naming
        # The engine runs in its own process group; make sure it never outlives
        # the UI, including on session logout.
        self.engine.stop()
        Adw.Application.do_shutdown(self)

    def _on_about(self, *_args: object) -> None:
        about = Adw.AboutDialog(
            application_name="AWG Tunnel",
            application_icon=APP_ID,
            version=VERSION,
            developer_name="AWG Tunnel contributors",
            license_type=Gtk.License.GPL_3_0,
            comments=(
                "Connects to WireGuard and AmneziaWG servers entirely in "
                "userspace and exposes the tunnel as a local SOCKS5 proxy. "
                "No root access, kernel module or system service is required.\n\n"
                "Not affiliated with the WireGuard or Amnezia projects. "
                "WireGuard is a registered trademark of Jason A. Donenfeld."
            ),
        )
        about.add_credit_section(
            "Tunnel engine",
            ["wireproxy-awg (ISC)", "amneziawg-go (MIT)"],
        )
        about.present(self.props.active_window)
