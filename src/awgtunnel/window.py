"""The main window."""

from __future__ import annotations

import threading
from datetime import timedelta

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from . import socks
from .engine import EngineError, State
from .profile import Profile, ProfileError
from .storage import ProfileStore, StorageError, StoredProfile

#: Third-party service queried through the tunnel to prove egress works.
_ECHO_HOST = "icanhazip.com"
_ECHO_PATH = "/"

_STATE_LABELS = {
    State.IDLE: ("Not connected", "dim-label"),
    State.STARTING: ("Starting…", "dim-label"),
    State.HANDSHAKING: ("Negotiating…", "warning"),
    State.CONNECTED: ("Connected", "success"),
    State.DEGRADED: ("Idle", "warning"),
    State.STOPPING: ("Stopping…", "dim-label"),
    State.FAILED: ("Failed", "error"),
}


def _set_clipboard(widget: Gtk.Widget, text: str) -> None:
    clipboard = widget.get_clipboard()
    try:
        clipboard.set_text(text)
    except (AttributeError, TypeError):
        value = GObject.Value(str, text)
        clipboard.set_content(Gdk.ContentProvider.new_for_value(value))


def _is_dismissed(error: GLib.Error) -> bool:
    """True when a file dialog was simply cancelled rather than failing.

    ``Gtk.DialogError.quark()`` is not exposed by every PyGObject build, so fall
    back to matching the message. Guessing wrong here only costs a spurious
    error dialog; letting the exception escape would break the import path.
    """
    try:
        return error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED)
    except (AttributeError, TypeError):
        return "dismissed" in (error.message or "").lower()


def _format_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _format_age(seconds: float) -> str:
    delta = timedelta(seconds=int(seconds))
    if delta.total_seconds() < 60:
        return f"{int(delta.total_seconds())}s ago"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h {minutes % 60}m ago"


class AwgTunnelWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("AWG Tunnel")
        self.set_default_size(560, 720)

        self._app = self.get_application()
        self._engine = self._app.engine
        self._store: ProfileStore | None = None
        self._profiles: list[StoredProfile] = []
        self._poll_source: int | None = None
        self._suppress_switch = False

        self._build_ui()
        self._load_profiles()

    # -- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.pack_start(self._build_import_button())
        header.pack_end(self._build_menu_button())
        toolbar.add_top_bar(header)

        self._banner = Adw.Banner(
            title="Only apps pointed at the proxy are tunnelled — there is no kill switch.",
            revealed=True,
        )
        toolbar.add_top_bar(self._banner)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(self._build_empty_page(), "empty")
        self._stack.add_named(self._build_main_page(), "main")
        toolbar.set_content(self._stack)

        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

    def _build_import_button(self) -> Gtk.Widget:
        menu = Gio.Menu()
        menu.append("Import from File…", "win.import-file")
        menu.append("Paste from Clipboard", "win.import-clipboard")

        for name, handler in (
            ("import-file", self._on_import_file),
            ("import-clipboard", self._on_import_clipboard),
            ("remove", self._on_remove),
            ("test", self._on_test_connection),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        button = Gtk.MenuButton(
            icon_name="list-add-symbolic",
            tooltip_text="Add a profile",
            menu_model=menu,
        )
        return button

    def _build_menu_button(self) -> Gtk.Widget:
        menu = Gio.Menu()
        menu.append("How to use the proxy", "win.usage")
        menu.append("About AWG Tunnel", "app.about")

        action = Gio.SimpleAction.new("usage", None)
        action.connect("activate", self._on_usage)
        self.add_action(action)

        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)

    def _build_empty_page(self) -> Gtk.Widget:
        button = Gtk.Button(label="Import a Profile…", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.add_css_class("suggested-action")
        button.set_action_name("win.import-file")

        return Adw.StatusPage(
            icon_name="network-vpn-symbolic",
            title="No Profiles",
            description=(
                "Import a WireGuard or AmneziaWG configuration file to get "
                "started. Obfuscation parameters are read directly from the "
                "profile."
            ),
            child=button,
        )

    def _build_main_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        profile_group = Adw.PreferencesGroup(title="Profile")
        self._profile_row = Adw.ComboRow(title="Active profile")
        self._profile_row.connect("notify::selected", self._on_profile_changed)
        profile_group.add(self._profile_row)

        self._protocol_row = Adw.ActionRow(title="Protocol", subtitle="—")
        profile_group.add(self._protocol_row)

        self._endpoint_row = Adw.ActionRow(title="Server", subtitle="—")
        self._endpoint_row.add_css_class("property")
        profile_group.add(self._endpoint_row)

        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text="Remove this profile",
            valign=Gtk.Align.CENTER,
        )
        remove_button.add_css_class("flat")
        remove_button.set_action_name("win.remove")
        remove_row = Adw.ActionRow(title="Remove profile")
        remove_row.add_suffix(remove_button)
        remove_row.set_activatable_widget(remove_button)
        profile_group.add(remove_row)
        page.add(profile_group)

        tunnel_group = Adw.PreferencesGroup(title="Tunnel")
        self._switch_row = Adw.SwitchRow(
            title="Connect", subtitle="Start the userspace tunnel"
        )
        self._switch_row.connect("notify::active", self._on_switch_toggled)
        tunnel_group.add(self._switch_row)

        self._status_row = Adw.ActionRow(title="Status", subtitle="Not connected")
        self._status_icon = Gtk.Image(icon_name="media-playback-stop-symbolic")
        self._status_row.add_prefix(self._status_icon)
        tunnel_group.add(self._status_row)

        self._proxy_row = Adw.ActionRow(title="SOCKS5 proxy", subtitle="—")
        self._proxy_row.add_css_class("property")
        copy_button = Gtk.Button(
            icon_name="edit-copy-symbolic",
            tooltip_text="Copy the proxy address",
            valign=Gtk.Align.CENTER,
        )
        copy_button.add_css_class("flat")
        copy_button.connect("clicked", self._on_copy_proxy)
        self._proxy_row.add_suffix(copy_button)
        tunnel_group.add(self._proxy_row)

        self._traffic_row = Adw.ActionRow(title="Traffic", subtitle="—")
        self._traffic_row.add_css_class("property")
        tunnel_group.add(self._traffic_row)

        self._handshake_row = Adw.ActionRow(title="Last handshake", subtitle="—")
        self._handshake_row.add_css_class("property")
        tunnel_group.add(self._handshake_row)

        self._test_button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self._test_button.add_css_class("flat")
        self._test_button.set_action_name("win.test")
        test_row = Adw.ActionRow(
            title="Test connection",
            subtitle=f"Fetches your visible IP address from {_ECHO_HOST}",
        )
        test_row.add_suffix(self._test_button)
        test_row.set_activatable_widget(self._test_button)
        tunnel_group.add(test_row)
        page.add(tunnel_group)

        obfuscation_group = Adw.PreferencesGroup(
            title="Obfuscation",
            description="Parameters found in the profile, passed to the engine unchanged.",
        )
        self._obfuscation_row = Adw.ActionRow(title="Parameters", subtitle="—")
        self._obfuscation_row.set_subtitle_selectable(True)
        obfuscation_group.add(self._obfuscation_row)
        page.add(obfuscation_group)

        log_group = Adw.PreferencesGroup(title="Diagnostics")
        self._log_expander = Adw.ExpanderRow(
            title="Engine log", subtitle="Keys are redacted"
        )
        self._log_buffer = Gtk.TextBuffer()
        log_view = Gtk.TextView(
            buffer=self._log_buffer,
            editable=False,
            monospace=True,
            left_margin=8,
            right_margin=8,
            top_margin=8,
            bottom_margin=8,
        )
        scroller = Gtk.ScrolledWindow(
            min_content_height=180, max_content_height=280, child=log_view
        )
        log_row = Adw.PreferencesRow(activatable=False, child=scroller)
        self._log_expander.add_row(log_row)
        log_group.add(self._log_expander)
        page.add(log_group)

        return page

    # -- profiles --------------------------------------------------------

    def _load_profiles(self) -> None:
        try:
            self._store = ProfileStore()
            self._profiles = self._store.list()
        except StorageError as exc:
            self._profiles = []
            self._error("Could not load profiles", str(exc))

        model = Gtk.StringList()
        for stored in self._profiles:
            model.append(stored.name)
        self._profile_row.set_model(model)

        if self._profiles:
            self._stack.set_visible_child_name("main")
            self._profile_row.set_selected(0)
            self._refresh_profile_details()
        else:
            self._stack.set_visible_child_name("empty")

    @property
    def _selected(self) -> StoredProfile | None:
        index = self._profile_row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= len(self._profiles):
            return None
        return self._profiles[index]

    def _resolve_selected(self) -> Profile | None:
        stored = self._selected
        if stored is None or self._store is None:
            return None
        try:
            return self._store.resolve(stored.id)
        except (StorageError, ProfileError) as exc:
            self._error("Could not load this profile", str(exc))
            return None

    def _refresh_profile_details(self) -> None:
        stored = self._selected
        if stored is None:
            return

        profile = self._resolve_selected()
        if profile is None:
            self._protocol_row.set_subtitle("—")
            self._endpoint_row.set_subtitle("—")
            self._obfuscation_row.set_subtitle("—")
            return

        obfuscated = profile.is_obfuscated
        self._protocol_row.set_subtitle(
            "AmneziaWG (obfuscated)" if obfuscated else "WireGuard (no obfuscation)"
        )
        self._endpoint_row.set_subtitle(profile.endpoint or "—")

        params = profile.awg_params
        self._obfuscation_row.set_subtitle(
            ", ".join(f"{key}={value}" for key, value in params.items())
            if params
            else "None — this is a plain WireGuard profile"
        )

        if stored.is_secret_on_disk:
            self._banner.set_title(
                "No system keyring available: this profile's private key is "
                "stored in the app's data directory."
            )

    # -- actions ---------------------------------------------------------

    def _on_profile_changed(self, *_args: object) -> None:
        if self._engine.is_running:
            self._engine.stop()
            self._update_status()
        self._refresh_profile_details()

    def _on_switch_toggled(self, *_args: object) -> None:
        if self._suppress_switch:
            return
        if self._switch_row.get_active():
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        profile = self._resolve_selected()
        if profile is None:
            self._set_switch(False)
            return

        try:
            self._engine.validate(profile)
            self._engine.start(profile)
        except (EngineError, ProfileError) as exc:
            self._set_switch(False)
            self._error("Could not start the tunnel", str(exc))
            return

        self._start_polling()
        self._update_status()

    def _disconnect(self) -> None:
        self._engine.stop()
        self._stop_polling()
        self._update_status()
        self._refresh_log()

    def _on_copy_proxy(self, _button: Gtk.Button) -> None:
        address = self._engine.status.socks_address
        if not address:
            self._toast("Connect first — there is no proxy to copy yet.")
            return
        _set_clipboard(self, f"socks5://{address}")
        self._toast(f"Copied socks5://{address}")

    def _on_remove(self, *_args: object) -> None:
        stored = self._selected
        if stored is None or self._store is None:
            return

        dialog = Adw.AlertDialog(
            heading=f"Remove “{stored.name}”?",
            body="The profile and its private key will be deleted from this device.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_remove_response, stored.id)
        dialog.present(self)

    def _on_remove_response(
        self, _dialog: Adw.AlertDialog, response: str, profile_id: str
    ) -> None:
        if response != "remove" or self._store is None:
            return
        if self._engine.is_running:
            self._disconnect()
        try:
            self._store.remove(profile_id)
        except StorageError as exc:
            self._error("Could not remove the profile", str(exc))
            return
        self._load_profiles()
        self._toast("Profile removed")

    def _on_import_file(self, *_args: object) -> None:
        filters = Gio.ListStore.new(Gtk.FileFilter)
        conf_filter = Gtk.FileFilter(name="Tunnel configuration (*.conf)")
        conf_filter.add_pattern("*.conf")
        conf_filter.add_pattern("*.CONF")
        filters.append(conf_filter)
        all_filter = Gtk.FileFilter(name="All files")
        all_filter.add_pattern("*")
        filters.append(all_filter)

        dialog = Gtk.FileDialog(title="Import Profile", filters=filters)
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error as exc:
            if not _is_dismissed(exc):
                self._error("Could not open the file", exc.message)
            return
        if file is None:
            return

        try:
            ok, contents, _etag = file.load_contents(None)
        except GLib.Error as exc:
            self._error("Could not read the file", exc.message)
            return
        if not ok:
            self._error("Could not read the file", "The file could not be read.")
            return

        basename = file.get_basename() or "Imported profile"
        name = basename.rsplit(".", 1)[0]
        self._import_text(contents.decode("utf-8", "replace"), name)

    def _on_import_clipboard(self, *_args: object) -> None:
        self.get_clipboard().read_text_async(None, self._on_clipboard_read)

    def _on_clipboard_read(
        self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult
    ) -> None:
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error as exc:
            self._error("Could not read the clipboard", exc.message)
            return
        if not text or not text.strip():
            self._toast("The clipboard is empty.")
            return
        self._import_text(text, "Pasted profile")

    def _import_text(self, text: str, name: str) -> None:
        if self._store is None:
            return
        try:
            self._store.add(name, text)
        except (ProfileError, StorageError) as exc:
            self._error("That profile cannot be used", str(exc))
            return

        self._load_profiles()
        self._profile_row.set_selected(len(self._profiles) - 1)
        self._toast(f"Imported “{name}”")

    def _on_usage(self, *_args: object) -> None:
        address = self._engine.status.socks_address or "127.0.0.1:<port>"
        dialog = Adw.AlertDialog(
            heading="Pointing apps at the tunnel",
            body=(
                f"While connected, the tunnel is available as a SOCKS5 proxy at "
                f"{address}.\n\n"
                "• Firefox: Settings → Network Settings → Manual proxy "
                "configuration → SOCKS Host, and enable “Proxy DNS when using "
                "SOCKS v5”.\n"
                '• Chromium: launch with --proxy-server="socks5://'
                f'{address}".\n'
                "• GNOME: Settings → Network → Proxy → Manual → Socks Host.\n"
                "• KDE Plasma: System Settings → Network → Proxy → Manually.\n\n"
                "Apps that ignore the proxy keep using your normal connection. "
                "This tool cannot protect them."
            ),
        )
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _on_test_connection(self, *_args: object) -> None:
        address = self._engine.status.socks_address
        if not self._engine.is_running or not address:
            self._toast("Connect first, then test.")
            return

        host, _, port = address.rpartition(":")
        self._test_button.set_sensitive(False)
        self._test_button.set_label("Testing…")

        def worker() -> None:
            try:
                body = socks.https_get(host, int(port), _ECHO_HOST, _ECHO_PATH)
                GLib.idle_add(self._on_test_done, body.strip(), None)
            except Exception as exc:  # noqa: BLE001 - reported verbatim to the user
                GLib.idle_add(self._on_test_done, None, str(exc))

        threading.Thread(target=worker, name="connectivity-test", daemon=True).start()

    def _on_test_done(self, address: str | None, error: str | None) -> bool:
        self._test_button.set_sensitive(True)
        self._test_button.set_label("Test")
        if error:
            self._error("The tunnel did not carry traffic", error)
        elif address:
            self._toast(f"Traffic is leaving via {address}")
        return False

    # -- status ----------------------------------------------------------

    def _start_polling(self) -> None:
        if self._poll_source is None:
            self._poll_source = GLib.timeout_add_seconds(1, self._on_poll)

    def _stop_polling(self) -> None:
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
            self._poll_source = None

    def _on_poll(self) -> bool:
        self._update_status()
        self._refresh_log()
        if not self._engine.status.state.is_active:
            self._poll_source = None
            return False
        return True

    def _update_status(self) -> None:
        status = self._engine.poll()
        label, css = _STATE_LABELS.get(status.state, ("Unknown", "dim-label"))

        self._status_row.set_title(label)
        self._status_row.set_subtitle(status.detail or "—")
        for candidate in ("success", "warning", "error", "dim-label"):
            self._status_row.remove_css_class(candidate)
        self._status_row.add_css_class(css)
        self._status_icon.set_from_icon_name(_status_icon(status.state))

        self._proxy_row.set_subtitle(
            f"socks5://{status.socks_address}" if status.socks_address else "—"
        )
        self._traffic_row.set_subtitle(
            f"↑ {_format_bytes(status.tx_bytes)}   ↓ {_format_bytes(status.rx_bytes)}"
            if status.state.is_active or status.rx_bytes or status.tx_bytes
            else "—"
        )
        age = status.handshake_age
        self._handshake_row.set_subtitle(_format_age(age) if age is not None else "—")

        self._set_switch(status.state.is_active)
        self._test_button.set_sensitive(
            status.state in {State.CONNECTED, State.DEGRADED}
        )

        if status.state is State.FAILED and status.detail:
            self._error("The tunnel stopped", status.detail)
            self._log_expander.set_expanded(True)

    def _set_switch(self, active: bool) -> None:
        if self._switch_row.get_active() == active:
            return
        self._suppress_switch = True
        try:
            self._switch_row.set_active(active)
        finally:
            self._suppress_switch = False

    def _refresh_log(self) -> None:
        lines = self._engine.log_lines()
        text = "\n".join(lines)
        if self._log_buffer.get_char_count() != len(text):
            self._log_buffer.set_text(text)

    # -- feedback --------------------------------------------------------

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _error(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)


def _status_icon(state: State) -> str:
    if state is State.CONNECTED:
        return "network-vpn-symbolic"
    if state in {State.STARTING, State.HANDSHAKING, State.STOPPING}:
        return "network-vpn-acquiring-symbolic"
    if state is State.DEGRADED:
        return "network-vpn-no-route-symbolic"
    if state is State.FAILED:
        return "dialog-warning-symbolic"
    return "media-playback-stop-symbolic"
