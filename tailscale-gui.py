#!/usr/bin/python3
"""
Tailscale GUI — GTK4 / libadwaita
Controla o Tailscale instalado localmente via CLI.
"""

import sys
import os
import json
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, GdkPixbuf, Gdk

APP_ID = "io.tailscale.TailscaleGUI"
APP_NAME = "Tailscale GUI"
VERSION = "1.0.0"
ICON_NAME = "tailscale-gui"


# ───────────────────────────── helpers ──────────────────────────────

def run_tailscale(*args, as_root=False):
    """Executa 'tailscale <args>' e retorna (stdout, stderr, returncode)."""
    cmd = ["tailscale"] + list(args)
    if as_root:
        cmd = ["pkexec"] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "Timeout ao executar tailscale", 1
    except FileNotFoundError:
        return "", "tailscale não encontrado no PATH", 1


def get_status_json():
    """Retorna o dict de status do tailscale ou None em caso de erro."""
    out, err, code = run_tailscale("status", "--json")
    if code != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ────────────────────────── Status page ─────────────────────────────

class StatusPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_valign(Gtk.Align.FILL)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(680)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        self.append(clamp)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(inner)

        # ── Status card ──
        self.status_group = Adw.PreferencesGroup()
        self.status_group.set_title("Conexão")
        inner.append(self.status_group)

        self.row_state = Adw.ActionRow()
        self.row_state.set_title("Estado")
        self.status_group.add(self.row_state)

        self.state_badge = Gtk.Label(label="…")
        self.state_badge.add_css_class("badge")
        self.row_state.add_suffix(self.state_badge)

        self.row_ip = Adw.ActionRow()
        self.row_ip.set_title("IP Tailscale")
        self.status_group.add(self.row_ip)
        self.lbl_ip = Gtk.Label(label="—")
        self.lbl_ip.set_selectable(True)
        self.lbl_ip.add_css_class("monospace")
        self.row_ip.add_suffix(self.lbl_ip)

        self.row_hostname = Adw.ActionRow()
        self.row_hostname.set_title("Hostname")
        self.status_group.add(self.row_hostname)
        self.lbl_hostname = Gtk.Label(label="—")
        self.row_hostname.add_suffix(self.lbl_hostname)

        self.row_relay = Adw.ActionRow()
        self.row_relay.set_title("Relay (DERP)")
        self.status_group.add(self.row_relay)
        self.lbl_relay = Gtk.Label(label="—")
        self.row_relay.add_suffix(self.lbl_relay)

        self.row_network = Adw.ActionRow()
        self.row_network.set_title("Rede")
        self.status_group.add(self.row_network)
        self.lbl_network = Gtk.Label(label="—")
        self.lbl_network.add_css_class("monospace")
        self.row_network.add_suffix(self.lbl_network)

        # ── Controles ──
        ctrl_group = Adw.PreferencesGroup()
        ctrl_group.set_title("Controles")
        inner.append(ctrl_group)

        self.connect_row = Adw.ActionRow()
        self.connect_row.set_title("Conectar / Desconectar")
        self.connect_row.set_subtitle("Ativa ou desativa o túnel Tailscale")
        ctrl_group.add(self.connect_row)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_valign(Gtk.Align.CENTER)
        self.connect_row.add_suffix(btn_box)

        self.btn_up = Gtk.Button(label="Conectar")
        self.btn_up.add_css_class("suggested-action")
        self.btn_up.connect("clicked", self._on_up)
        btn_box.append(self.btn_up)

        self.btn_down = Gtk.Button(label="Desconectar")
        self.btn_down.add_css_class("destructive-action")
        self.btn_down.connect("clicked", self._on_down)
        btn_box.append(self.btn_down)

        self.spinner = Gtk.Spinner()
        btn_box.append(self.spinner)

        # ── Log ──
        log_group = Adw.PreferencesGroup()
        log_group.set_title("Log")
        inner.append(log_group)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(120)
        scroll.set_vexpand(False)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        log_group.add(scroll)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.set_margin_start(8)
        self.log_view.set_margin_end(8)
        self.log_view.set_margin_top(8)
        self.log_view.set_margin_bottom(8)
        self.log_buf = self.log_view.get_buffer()
        scroll.set_child(self.log_view)

        self._callback = None  # fn() para pedir refresh global

    def set_refresh_callback(self, cb):
        self._callback = cb

    def _log(self, text):
        GLib.idle_add(self._log_main, text)

    def _log_main(self, text):
        end = self.log_buf.get_end_iter()
        self.log_buf.insert(end, text + "\n")
        # scroll ao fim
        end = self.log_buf.get_end_iter()
        mark = self.log_buf.create_mark(None, end, False)
        self.log_view.scroll_to_mark(mark, 0, True, 0, 1)

    def _set_busy(self, busy: bool):
        self.btn_up.set_sensitive(not busy)
        self.btn_down.set_sensitive(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _on_up(self, _btn):
        self._set_busy(True)
        self._log("→ tailscale up …")
        threading.Thread(target=self._do_up, daemon=True).start()

    def _do_up(self):
        out, err, code = run_tailscale("up", "--accept-routes")
        msg = out or err or "(sem saída)"
        self._log(f"← {msg}")
        GLib.idle_add(self._set_busy, False)
        if self._callback:
            GLib.idle_add(self._callback)

    def _on_down(self, _btn):
        self._set_busy(True)
        self._log("→ tailscale down …")
        threading.Thread(target=self._do_down, daemon=True).start()

    def _do_down(self):
        out, err, code = run_tailscale("down")
        msg = out or err or "(sem saída)"
        self._log(f"← {msg}")
        GLib.idle_add(self._set_busy, False)
        if self._callback:
            GLib.idle_add(self._callback)

    def update(self, data: dict | None):
        if data is None:
            self.state_badge.set_label("Erro")
            self.state_badge.remove_css_class("badge-green")
            self.state_badge.remove_css_class("badge-red")
            self.state_badge.add_css_class("badge-red")
            return

        backend = data.get("BackendState", "Unknown")
        running = backend == "Running"

        self.state_badge.set_label("Conectado" if running else "Desconectado")
        for cls in ("badge-green", "badge-red", "badge-yellow"):
            self.state_badge.remove_css_class(cls)
        self.state_badge.add_css_class("badge-green" if running else "badge-red")

        self_node = data.get("Self", {})
        ips = self_node.get("TailscaleIPs", [])
        self.lbl_ip.set_label(ips[0] if ips else "—")
        self.lbl_hostname.set_label(self_node.get("HostName", "—"))
        self.lbl_relay.set_label(self_node.get("Relay", "—") or "—")
        self.lbl_network.set_label(data.get("MagicDNSSuffix", "—"))


# ──────────────────────────── Peers page ────────────────────────────

class PeersPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(780)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        self.append(clamp)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(inner)

        self.peers_group = Adw.PreferencesGroup()
        self.peers_group.set_title("Dispositivos na rede")
        self.peers_group.set_description("Todos os peers no seu Tailnet")
        inner.append(self.peers_group)

        self._rows = []

    def update(self, data: dict | None):
        # Limpar rows antigas
        for row in self._rows:
            self.peers_group.remove(row)
        self._rows.clear()

        if data is None:
            return

        peers = data.get("Peer", {})
        if not peers:
            row = Adw.ActionRow()
            row.set_title("Nenhum peer encontrado")
            row.set_subtitle("Conecte-se ao Tailscale para ver os dispositivos")
            self.peers_group.add(row)
            self._rows.append(row)
            return

        for key, peer in peers.items():
            hostname = peer.get("HostName", "Unknown")
            ips = peer.get("TailscaleIPs", [])
            ip_str = ips[0] if ips else "—"
            online = peer.get("Online", False)
            exit_node = peer.get("ExitNodeOption", False)
            relay = peer.get("Relay", "") or ""
            os_name = peer.get("OS", "")

            row = Adw.ActionRow()
            row.set_title(hostname)

            subtitle_parts = [ip_str]
            if relay:
                subtitle_parts.append(f"relay: {relay}")
            if os_name:
                subtitle_parts.append(os_name)
            row.set_subtitle("  ·  ".join(subtitle_parts))

            # Ícone de status
            dot = Gtk.Label()
            if online:
                dot.set_label("●")
                dot.add_css_class("online-dot")
                dot.set_tooltip_text("Online")
            else:
                dot.set_label("●")
                dot.add_css_class("offline-dot")
                dot.set_tooltip_text("Offline")
            row.add_prefix(dot)

            # Badge exit node
            if exit_node:
                badge = Gtk.Label(label="Exit Node")
                badge.add_css_class("badge")
                badge.add_css_class("badge-blue")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)

            self.peers_group.add(row)
            self._rows.append(row)


# ──────────────────────────── Settings page ──────────────────────────

class SettingsPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(680)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        self.append(clamp)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(inner)

        # ── Exit Node ──
        exit_group = Adw.PreferencesGroup()
        exit_group.set_title("Exit Node")
        exit_group.set_description("Roteia todo o tráfego por um peer específico")
        inner.append(exit_group)

        self.exit_row = Adw.ComboRow()
        self.exit_row.set_title("Exit Node")
        self.exit_row.set_subtitle("Selecione um peer como exit node")
        exit_group.add(self.exit_row)

        self._exit_model = Gtk.StringList()
        self.exit_row.set_model(self._exit_model)
        self._exit_peers = []  # lista de hostnames

        apply_exit_btn = Gtk.Button(label="Aplicar")
        apply_exit_btn.add_css_class("suggested-action")
        apply_exit_btn.set_valign(Gtk.Align.CENTER)
        apply_exit_btn.connect("clicked", self._on_apply_exit)
        self.exit_row.add_suffix(apply_exit_btn)

        # ── Switches ──
        opts_group = Adw.PreferencesGroup()
        opts_group.set_title("Opções")
        inner.append(opts_group)

        self.sw_routes = Adw.SwitchRow()
        self.sw_routes.set_title("Accept Routes")
        self.sw_routes.set_subtitle("Aceitar rotas anunciadas por outros peers")
        opts_group.add(self.sw_routes)
        self.sw_routes.connect("notify::active", self._on_routes_toggled)

        self.sw_ssh = Adw.SwitchRow()
        self.sw_ssh.set_title("Tailscale SSH")
        self.sw_ssh.set_subtitle("Permitir acesso SSH via Tailscale")
        opts_group.add(self.sw_ssh)
        self.sw_ssh.connect("notify::active", self._on_ssh_toggled)

        self.sw_shields = Adw.SwitchRow()
        self.sw_shields.set_title("Shields Up")
        self.sw_shields.set_subtitle("Bloquear conexões de entrada de outros peers")
        opts_group.add(self.sw_shields)
        self.sw_shields.connect("notify::active", self._on_shields_toggled)

        # ── Sobre ──
        about_group = Adw.PreferencesGroup()
        about_group.set_title("Sobre")
        inner.append(about_group)

        about_row = Adw.ActionRow()
        about_row.set_title("Tailscale GUI")
        about_row.set_subtitle(f"Versão {VERSION}  ·  GTK4 + libadwaita")
        about_row.set_icon_name(ICON_NAME)
        about_group.add(about_row)

        self._refresh_cb = None
        self._block_signals = False

    def set_refresh_callback(self, cb):
        self._refresh_cb = cb

    def _run_set_bg(self, *args):
        threading.Thread(target=lambda: run_tailscale("set", *args), daemon=True).start()
        if self._refresh_cb:
            GLib.timeout_add(1500, self._refresh_cb)

    def _on_routes_toggled(self, sw, _pspec):
        if self._block_signals:
            return
        val = "true" if sw.get_active() else "false"
        self._run_set_bg(f"--accept-routes={val}")

    def _on_ssh_toggled(self, sw, _pspec):
        if self._block_signals:
            return
        val = "true" if sw.get_active() else "false"
        self._run_set_bg(f"--ssh={val}")

    def _on_shields_toggled(self, sw, _pspec):
        if self._block_signals:
            return
        val = "true" if sw.get_active() else "false"
        self._run_set_bg(f"--shields-up={val}")

    def _on_apply_exit(self, _btn):
        idx = self.exit_row.get_selected()
        if idx == 0:
            # "Nenhum"
            threading.Thread(
                target=lambda: run_tailscale("set", "--exit-node="),
                daemon=True
            ).start()
        else:
            hostname = self._exit_peers[idx - 1]
            threading.Thread(
                target=lambda h=hostname: run_tailscale("set", f"--exit-node={h}"),
                daemon=True
            ).start()
        if self._refresh_cb:
            GLib.timeout_add(1500, self._refresh_cb)

    def update(self, data: dict | None):
        self._block_signals = True
        try:
            # Atualizar exit node dropdown
            # Limpar model
            while self._exit_model.get_n_items() > 0:
                self._exit_model.remove(0)
            self._exit_peers.clear()

            self._exit_model.append("Nenhum (desabilitar)")

            if data:
                peers = data.get("Peer", {})
                current_exit = ""
                for k, p in peers.items():
                    if p.get("ExitNodeOption", False):
                        name = p.get("HostName", k)
                        self._exit_model.append(name)
                        self._exit_peers.append(name)
                    if p.get("ExitNode", False):
                        current_exit = p.get("HostName", "")

                # Selecionar o exit node atual
                if current_exit and current_exit in self._exit_peers:
                    idx = self._exit_peers.index(current_exit) + 1
                    self.exit_row.set_selected(idx)
                else:
                    self.exit_row.set_selected(0)

                # Switches — ler de Self.Capabilities (aproximação)
                prefs = data.get("Self", {})
                # tailscale não expõe switches no JSON de status de forma direta,
                # mas podemos checar via tailscale debug prefs
        finally:
            self._block_signals = False


# ──────────────────────────── Main Window ───────────────────────────

class TailscaleWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(APP_NAME)
        self.set_default_size(960, 640)
        self.set_icon_name(ICON_NAME)

        # ── Toolbar + NavigationSplitView ──
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_title(False)
        toolbar_view.add_top_bar(header)

        # Título centralizado
        title_widget = Adw.WindowTitle()
        title_widget.set_title(APP_NAME)
        title_widget.set_subtitle("Gerenciador de rede privada")
        header.set_title_widget(title_widget)

        # Botão refresh no header
        btn_refresh = Gtk.Button()
        btn_refresh.set_icon_name("view-refresh-symbolic")
        btn_refresh.set_tooltip_text("Atualizar status")
        btn_refresh.connect("clicked", lambda _: self._refresh())
        header.pack_end(btn_refresh)

        self._refresh_spinner = Gtk.Spinner()
        header.pack_end(self._refresh_spinner)

        # ── NavigationSplitView ──
        split = Adw.NavigationSplitView()
        split.set_min_sidebar_width(180)
        split.set_max_sidebar_width(220)
        toolbar_view.set_content(split)

        # Sidebar
        sidebar_page = Adw.NavigationPage()
        sidebar_page.set_title("Tailscale GUI")

        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_toolbar.add_top_bar(sidebar_header)

        sidebar_header_title = Adw.WindowTitle()
        sidebar_header_title.set_title("Tailscale")
        sidebar_header.set_title_widget(sidebar_header_title)

        self._sidebar_list = Gtk.ListBox()
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.add_css_class("navigation-sidebar")
        self._sidebar_list.connect("row-selected", self._on_sidebar_selected)
        sidebar_toolbar.set_content(self._sidebar_list)
        sidebar_page.set_child(sidebar_toolbar)

        split.set_sidebar(sidebar_page)

        # Content page
        self._content_page = Adw.NavigationPage()
        self._content_page.set_title("Status")

        self._content_toolbar = Adw.ToolbarView()
        self._content_header = Adw.HeaderBar()
        self._content_toolbar.add_top_bar(self._content_header)

        self._content_title = Adw.WindowTitle()
        self._content_title.set_title("Status")
        self._content_header.set_title_widget(self._content_title)

        self._content_page.set_child(self._content_toolbar)
        split.set_content(self._content_page)

        # ── Páginas de conteúdo ──
        self._status_page = StatusPage()
        self._peers_page = PeersPage()
        self._settings_page = SettingsPage()

        self._status_page.set_refresh_callback(self._refresh)
        self._settings_page.set_refresh_callback(self._refresh)

        self._pages = [
            ("Status", "network-wireless-symbolic", self._status_page),
            ("Peers", "system-users-symbolic", self._peers_page),
            ("Configurações", "preferences-system-symbolic", self._settings_page),
        ]

        for label, icon, _page in self._pages:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            hbox.set_margin_top(10)
            hbox.set_margin_bottom(10)
            hbox.set_margin_start(16)
            hbox.set_margin_end(16)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(16)
            lbl = Gtk.Label(label=label, xalign=0)
            lbl.set_hexpand(True)
            hbox.append(img)
            hbox.append(lbl)
            row.set_child(hbox)
            self._sidebar_list.append(row)

        # Selecionar primeira linha
        self._sidebar_list.select_row(self._sidebar_list.get_row_at_index(0))
        self._show_page(0)

        # Auto-refresh a cada 5 segundos
        self._refresh()
        GLib.timeout_add_seconds(5, self._auto_refresh)

    def _on_sidebar_selected(self, listbox, row):
        if row is None:
            return
        idx = row.get_index()
        self._show_page(idx)

    def _show_page(self, idx: int):
        label, icon, page = self._pages[idx]
        self._content_title.set_title(label)

        # Fazer um ScrolledWindow envolvendo a página
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(page)
        scroll.set_vexpand(True)

        self._content_toolbar.set_content(scroll)

    def _refresh(self):
        self._refresh_spinner.start()
        threading.Thread(target=self._fetch_status, daemon=True).start()
        return False  # para GLib.timeout_add

    def _auto_refresh(self):
        self._refresh()
        return True  # continua o timer

    def _fetch_status(self):
        data = get_status_json()
        GLib.idle_add(self._apply_status, data)

    def _apply_status(self, data):
        self._status_page.update(data)
        self._peers_page.update(data)
        self._settings_page.update(data)
        self._refresh_spinner.stop()


# ───────────────────────────── CSS ──────────────────────────────────

CSS = """
.badge {
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 0.8em;
    font-weight: 600;
    background-color: alpha(@accent_color, 0.15);
    color: @accent_color;
}
.badge-green {
    background-color: alpha(#2ec27e, 0.2);
    color: #2ec27e;
}
.badge-red {
    background-color: alpha(#e01b24, 0.15);
    color: #e01b24;
}
.badge-blue {
    background-color: alpha(#3584e4, 0.15);
    color: #3584e4;
}
.badge-yellow {
    background-color: alpha(#e5a50a, 0.2);
    color: #c88800;
}
.online-dot {
    color: #2ec27e;
    font-size: 1.1em;
    margin-end: 4px;
}
.offline-dot {
    color: @dim-label-color;
    font-size: 1.1em;
    margin-end: 4px;
}
"""


# ──────────────────────────── Application ───────────────────────────

class TailscaleApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            win = TailscaleWindow(self)
            # Carregar CSS
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        win.present()

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Registrar ícone se necessário
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_path = "/usr/share/icons/hicolor/256x256/apps"
        if os.path.isdir(icon_path):
            theme.add_search_path(icon_path)


def main():
    app = TailscaleApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
