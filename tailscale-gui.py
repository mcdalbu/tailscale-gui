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
import re
import shutil
from urllib.parse import urlparse

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Gdk

# O RPM mantém o módulo do tray em /usr/share/tailscale-gui.
module_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "share", "tailscale-gui")
if os.path.isdir(module_dir):
    sys.path.insert(0, module_dir)
from tailscale_tray import DesktopPreferences, StatusNotifierTray

APP_ID = "io.tailscale.TailscaleGUI"
APP_NAME = "Tailscale GUI"
VERSION = "1.2.2"
ICON_NAME = "tailscale-gui"


# ───────────────────────────── helpers ──────────────────────────────

def run_tailscale(*args, as_root=False, timeout=15, on_output=None):
    """Executa 'tailscale <args>' e retorna (stdout, stderr, returncode)."""
    executable = shutil.which("tailscale")
    if not executable:
        return "", "Tailscale não encontrado. Instale o cliente Tailscale.", 127
    cmd = [executable] + list(args)
    if as_root:
        pkexec = shutil.which("pkexec")
        if not pkexec:
            return "", "Autorização necessária, mas pkexec não está instalado.", 127
        cmd = [pkexec] + cmd
    try:
        if on_output is not None:
            return _run_streamed(cmd, timeout, on_output)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired as exc:
        # Preserve o link de login e os diagnósticos produzidos antes do timeout.
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        return decode(exc.stdout).strip(), decode(exc.stderr).strip() + "\nTempo limite ao executar Tailscale.", 124
    except OSError as exc:
        return "", f"Não foi possível executar {cmd[0]}: {exc}", 1


def _run_streamed(cmd, timeout, on_output):
    """Mostra o link de autenticação enquanto 'up' aguarda o navegador."""
    lines = []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, bufsize=1) as process:
        def read_output():
            for line in process.stdout:
                lines.append(line)
                on_output(line.rstrip())
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = 124
        reader.join()
    error = "Tempo limite. Conclua o login no navegador e atualize o status." if code == 124 else ""
    return "".join(lines).strip(), error, code


def permission_denied(result):
    out, err, code = result
    message = (out + "\n" + err).lower()
    return code != 0 and any(text in message for text in (
        "access denied", "permission denied", "must be run as root",
        "requires root", "sudo tailscale",
    ))


def run_change(*args, on_output=None):
    """Pede autorização do sistema somente se o daemon negar uma alteração."""
    result = run_tailscale(*args, timeout=120, on_output=on_output)
    if permission_denied(result):
        if on_output:
            on_output("Autorização de administrador necessária; confirme no diálogo do sistema.")
        result = run_tailscale(*args, as_root=True, timeout=120, on_output=on_output)
    return result


def parse_object(out):
    try:
        data = json.loads(out)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def fetch_snapshot():
    """Lê status e preferências; nunca registra chaves privadas de debug prefs."""
    out, err, code = run_tailscale("status", "--json")
    data = parse_object(out)
    if code != 0 or data is None:
        return None, None, err or "Resposta de status inválida do Tailscale."
    out, err, code = run_tailscale("debug", "prefs")
    prefs = parse_object(out) if code == 0 else None
    if prefs is None:
        return data, None, "Não foi possível ler as preferências: " + (err or "JSON inválido.")
    # Persist pode conter material secreto. Somente os campos usados pela UI saem daqui.
    prefs = {key: prefs[key] for key in (
        "RouteAll", "RunSSH", "ShieldsUp", "ExitNodeID", "ExitNodeIP",
    ) if key in prefs}
    return data, prefs, ""


def peers_from(data):
    peers = (data or {}).get("Peer") or {}
    return [peer for peer in peers.values() if isinstance(peer, dict)] if isinstance(peers, dict) else []


def node_ip(node):
    ips = node.get("TailscaleIPs") or []
    return next((ip for ip in ips if ":" not in ip), ips[0] if ips else "")


def auth_url(value):
    """Aceita links HTTP(S), incluindo servidores Headscale personalizados."""
    for url in re.findall(r"https?://[^\s<>\"']+", value or ""):
        url = url.rstrip(".,)")
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        if parsed.hostname and not parsed.username and not parsed.password:
            return url
    return ""


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
        self.row_state.set_use_markup(False)
        self.row_state.set_title("Estado")
        self.status_group.add(self.row_state)

        self.state_badge = Gtk.Label(label="…")
        self.state_badge.set_valign(Gtk.Align.CENTER)
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

        self.login_btn = Gtk.LinkButton(label="Abrir página de autenticação", uri="https://login.tailscale.com")
        self.login_btn.set_visible(False)
        inner.append(self.login_btn)

        self.message = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.message.add_css_class("dim-label")
        inner.append(self.message)

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

        self._command_cb = None
        self._busy = False
        self._data = None
        self._log_mark = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
        self._set_busy(False)

    def set_command_callback(self, cb):
        self._command_cb = cb

    def _log_main(self, text):
        end = self.log_buf.get_end_iter()
        self.log_buf.insert(end, text + "\n")
        # Limite o log e reutilize a marca para não acumular objetos a cada operação.
        if self.log_buf.get_line_count() > 500:
            _found, start = self.log_buf.get_iter_at_line(100)
            self.log_buf.delete(self.log_buf.get_start_iter(), start)
        end = self.log_buf.get_end_iter()
        self.log_buf.move_mark(self._log_mark, end)
        self.log_view.scroll_to_mark(self._log_mark, 0, True, 0, 1)
        return False

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = (self._data or {}).get("BackendState")
        self.btn_up.set_sensitive(not busy and state in ("Stopped", "NeedsLogin", "NeedsMachineAuth"))
        self.btn_down.set_sensitive(not busy and state in ("Running", "Starting", "NeedsMachineAuth"))
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _on_up(self, _btn):
        if self._command_cb:
            # Sem flags: retoma a conexão sem sobrescrever preferências existentes.
            self._command_cb("up")

    def _on_down(self, _btn):
        if self._command_cb:
            self._command_cb("down")

    def show_login(self, url):
        if url:
            self.login_btn.set_uri(url)
            self.login_btn.set_visible(True)

    def update(self, data: dict | None, error=""):
        self._data = data
        backend = (data or {}).get("BackendState", "Unknown")
        labels = {
            "Running": "Conectado", "Stopped": "Desconectado",
            "NeedsLogin": "Login necessário", "NeedsMachineAuth": "Aguardando aprovação",
            "Starting": "Conectando", "NoState": "Inicializando",
        }
        self.state_badge.set_label(labels.get(backend, "Indisponível"))
        for cls in ("badge-green", "badge-red", "badge-yellow"):
            self.state_badge.remove_css_class(cls)
        color = "green" if backend == "Running" else "red" if backend in ("Stopped", "Unknown") else "yellow"
        self.state_badge.add_css_class("badge-" + color)

        self_node = (data or {}).get("Self") or {}
        self.lbl_ip.set_label(node_ip(self_node) or "—")
        self.lbl_hostname.set_label(self_node.get("HostName") or "—")
        self.lbl_relay.set_label(self_node.get("Relay", "—") or "—")
        tailnet = (data or {}).get("CurrentTailnet") or {}
        self.lbl_network.set_label(tailnet.get("Name") or (data or {}).get("MagicDNSSuffix") or "—")
        messages = [error] if error else []
        if backend == "NeedsMachineAuth":
            messages.append("Aprove este dispositivo no console de administração do Tailnet.")
        messages.extend((data or {}).get("Health") or [])
        self.message.set_text("\n".join(messages))
        self.message.set_visible(bool(messages))
        if backend in ("Running", "Stopped", "Unknown"):
            self.login_btn.set_visible(False)
        self.show_login(auth_url((data or {}).get("AuthURL")))
        self._set_busy(self._busy)


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
        self._last_view = None

    def update(self, data: dict | None):
        peers = sorted(peers_from(data), key=lambda peer: (
            not peer.get("Online", False), (peer.get("HostName") or "").casefold(), node_ip(peer),
        ))
        view = [(p.get("HostName"), node_ip(p), p.get("Online"), p.get("ExitNodeOption"),
                 p.get("ExitNode"), p.get("Relay"), p.get("OS")) for p in peers]
        signature = ((data or {}).get("BackendState"), view, data is None)
        if signature == self._last_view:
            return
        self._last_view = signature
        # Limpar rows antigas
        for row in self._rows:
            self.peers_group.remove(row)
        self._rows.clear()

        if not peers:
            row = Adw.ActionRow()
            row.set_title("Status indisponível" if data is None else "Nenhum peer encontrado")
            row.set_subtitle("Verifique o cliente e o serviço tailscaled" if data is None else "Conecte-se ao Tailscale para ver os dispositivos")
            self.peers_group.add(row)
            self._rows.append(row)
            return

        self.peers_group.set_description("Dispositivos do seu Tailnet" if data.get("BackendState") == "Running" else "Último estado conhecido; o cliente local está desconectado")
        for peer in peers:
            hostname = peer.get("HostName") or "Dispositivo sem nome"
            ip_str = node_ip(peer) or "—"
            online = peer.get("Online", False)
            exit_node = peer.get("ExitNodeOption", False)
            relay = peer.get("Relay", "") or ""
            os_name = peer.get("OS", "")

            row = Adw.ActionRow()
            row.set_use_markup(False)
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
                badge = Gtk.Label(label="Exit Node ativo" if peer.get("ExitNode") else "Exit Node")
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
        self.exit_row.set_use_markup(False)
        self.exit_row.set_title("Exit Node")
        self.exit_row.set_subtitle("Selecione um peer como exit node")
        exit_group.add(self.exit_row)

        self._exit_model = Gtk.StringList()
        self.exit_row.set_model(self._exit_model)
        self._exit_peers = []  # IPs estáveis, inclusive para hostnames duplicados.
        self._exit_labels = []
        self._exit_dirty = False

        apply_exit_btn = Gtk.Button(label="Aplicar")
        self.apply_exit_btn = apply_exit_btn
        apply_exit_btn.add_css_class("suggested-action")
        apply_exit_btn.set_valign(Gtk.Align.CENTER)
        apply_exit_btn.connect("clicked", self._on_apply_exit)
        self.exit_row.add_suffix(apply_exit_btn)
        self.exit_row.connect("notify::selected", self._on_exit_selected)

        # ── Switches ──
        opts_group = Adw.PreferencesGroup()
        opts_group.set_title("Opções")
        inner.append(opts_group)

        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.error_label.add_css_class("dim-label")
        inner.append(self.error_label)

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

        desktop_group = Adw.PreferencesGroup(title="Comportamento do aplicativo")
        inner.append(desktop_group)
        self.sw_tray = Adw.SwitchRow(title="Minimizar para o tray ao fechar")
        self.sw_tray.set_subtitle("Mantém o app na bandeja do sistema; use Sair para encerrá-lo")
        desktop_group.add(self.sw_tray)
        self._desktop_cb = None
        self._block_desktop_signals = False
        self.sw_tray.set_sensitive(False)
        self.sw_tray.connect("notify::active", self._on_tray_toggled)

        # ── Sobre ──
        about_group = Adw.PreferencesGroup()
        about_group.set_title("Sobre")
        inner.append(about_group)

        about_row = Adw.ActionRow()
        about_row.set_title("Tailscale GUI")
        about_row.set_subtitle(f"Versão {VERSION}  ·  GTK4 + libadwaita")
        about_icon = Gtk.Image.new_from_icon_name(ICON_NAME)
        about_icon.set_pixel_size(32)
        about_row.add_prefix(about_icon)
        about_group.add(about_row)

        self._command_cb = None
        self._block_signals = True
        self._busy = False
        self._status_available = False
        self._prefs = None
        self._exit_model.append("Nenhum (desabilitar)")
        self._block_signals = False
        self._set_busy(False)

    def set_command_callback(self, cb):
        self._command_cb = cb

    def set_desktop_callback(self, cb):
        self._desktop_cb = cb

    def set_desktop_settings(self, enabled, available, error=""):
        self._block_desktop_signals = True
        try:
            self.sw_tray.set_active(enabled)
            self.sw_tray.set_sensitive(available)
            subtitle = ("Mantém o app na bandeja do sistema; use Sair para encerrá-lo" if available
                        else "Tray indisponível nesta sessão. No GNOME, habilite o suporte a AppIndicator")
            self.sw_tray.set_subtitle(error or subtitle)
        finally:
            self._block_desktop_signals = False

    def _on_tray_toggled(self, row, _pspec):
        if not self._block_desktop_signals and self._desktop_cb:
            self._desktop_cb(row.get_active())

    def _set_busy(self, busy):
        self._busy = busy
        self.exit_row.set_sensitive(not busy and self._status_available and self._prefs is not None)
        self.apply_exit_btn.set_sensitive(not busy and self._status_available and self._prefs is not None)
        idx = self.exit_row.get_selected()
        if idx >= len(self._exit_peers) or self._exit_peers[idx] is None:
            self.apply_exit_btn.set_sensitive(False)
        for sw, key in ((self.sw_routes, "RouteAll"), (self.sw_ssh, "RunSSH"),
                        (self.sw_shields, "ShieldsUp")):
            sw.set_sensitive(not busy and self._status_available and isinstance((self._prefs or {}).get(key), bool))

    def _run_set_bg(self, *args):
        if self._command_cb:
            self._command_cb("set", *args)

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
        if idx >= len(self._exit_peers) or self._exit_peers[idx] is None:
            return
        self._exit_dirty = False
        self._run_set_bg(f"--exit-node={self._exit_peers[idx]}")

    def _on_exit_selected(self, _row, _pspec):
        if not self._block_signals:
            self._exit_dirty = True
            self._set_busy(self._busy)

    def update(self, data: dict | None, prefs=None, error="", force=False):
        previous_idx = self.exit_row.get_selected()
        pending = self._exit_peers[previous_idx] if previous_idx < len(self._exit_peers) else ""
        self._status_available = data is not None
        self._prefs = prefs
        self.error_label.set_text(error)
        self.error_label.set_visible(bool(error))
        self._block_signals = True
        try:
            peers = peers_from(data)
            choices = [p for p in peers if p.get("ExitNodeOption") or p.get("ExitNode")]
            choices.sort(key=lambda p: ((p.get("HostName") or "").casefold(), node_ip(p)))
            values, labels = [""], ["Nenhum (desabilitar)"]
            current = ""
            for peer in choices:
                ip = node_ip(peer)
                if not ip or ip in values:
                    continue
                values.append(ip)
                name = peer.get("HostName") or ip
                labels.append(f"{name} · {ip}" + (" · offline" if not peer.get("Online") else ""))
                if peer.get("ExitNode") or ((prefs or {}).get("ExitNodeID") and prefs["ExitNodeID"] == peer.get("ID")):
                    current = ip
            # A preferência continua válida mesmo quando o exit node não está na lista de peers.
            saved_ip = (prefs or {}).get("ExitNodeIP")
            saved_id = (prefs or {}).get("ExitNodeID")
            exit_status = (data or {}).get("ExitNodeStatus") or {}
            if not current and (saved_id or saved_ip):
                current = saved_ip or node_ip(exit_status)
                if current and current not in values:
                    values.append(current)
                    labels.append(f"{current} · indisponível")
                elif not current:
                    # Não exiba 'Nenhum' para uma preferência cujo dispositivo ficou inacessível.
                    values.append(None)
                    labels.append("Exit Node configurado · indisponível")
            if labels != self._exit_labels or values != self._exit_peers:
                self._exit_model.splice(0, self._exit_model.get_n_items(), labels)
                self._exit_labels, self._exit_peers = labels, values
            if force:
                self._exit_dirty = False
            selected = pending if self._exit_dirty and pending in values else current
            idx = values.index(selected) if selected in values else 0
            if not self._exit_dirty and not current and saved_id and None in values:
                idx = values.index(None)
            self.exit_row.set_selected(idx)
            for sw, key in ((self.sw_routes, "RouteAll"), (self.sw_ssh, "RunSSH"),
                            (self.sw_shields, "ShieldsUp")):
                sw.set_active((prefs or {}).get(key) is True)
        finally:
            self._block_signals = False
        self._set_busy(self._busy)


# ──────────────────────────── Main Window ───────────────────────────

class TailscaleWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(APP_NAME)
        self.set_default_size(960, 640)
        self.set_icon_name(ICON_NAME)
        self._closed = False
        self._busy = False
        self._command_running = False
        self._refreshing = False
        self._refresh_requested = False
        self._generation = 0
        self._snapshot = (None, None, "")
        self._last_error = ""
        self._reset_exit_selection = False
        self.connect("close-request", self._on_close)

        # ── Toolbar + NavigationSplitView ──
        toolbar_view = Adw.ToolbarView()
        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar_view)
        self.set_content(self._toasts)

        header = Adw.HeaderBar()
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

        app_menu = Gio.Menu()
        app_menu.append("Sair", "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=app_menu)
        menu_button.set_tooltip_text("Menu do aplicativo")
        header.pack_end(menu_button)

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
        sidebar_header.set_show_start_title_buttons(False)
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
        self._content_header.set_show_start_title_buttons(False)
        self._content_header.set_show_end_title_buttons(False)
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

        self._status_page.set_command_callback(self._run_command)
        self._settings_page.set_command_callback(self._run_command)
        self._settings_page.set_desktop_callback(app.set_minimize_to_tray)
        self._settings_page.set_desktop_settings(app.desktop_preferences.minimize_to_tray,
                                                app.tray is not None and app.tray.available)

        self._pages = [
            ("Status", "network-wireless-symbolic", self._status_page),
            ("Peers", "system-users-symbolic", self._peers_page),
            ("Configurações", "preferences-system-symbolic", self._settings_page),
        ]

        # Cada página mantém seu próprio pai e posição de rolagem.
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        for idx, (_label, _icon, page) in enumerate(self._pages):
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(page)
            self._stack.add_named(scroll, str(idx))
        self._content_toolbar.set_content(self._stack)

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

        # Auto-refresh a cada 5 segundos
        self._refresh()
        self._timer_id = GLib.timeout_add_seconds(5, self._auto_refresh)

    def _on_sidebar_selected(self, listbox, row):
        if row is None:
            return
        idx = row.get_index()
        self._show_page(idx)

    def _show_page(self, idx: int):
        label = self._pages[idx][0]
        self._content_title.set_title(label)
        self._content_page.set_title(label)
        self._stack.set_visible_child_name(str(idx))

    def _refresh(self):
        if self._closed:
            return False
        if self._refreshing or self._command_running:
            self._refresh_requested = True
            return False
        self._refresh_requested = False
        self._refreshing = True
        self._refresh_spinner.start()
        threading.Thread(target=self._fetch_status, args=(self._generation,), daemon=True).start()
        return False  # para GLib.timeout_add

    def _auto_refresh(self):
        if self._closed:
            return False
        self._refresh()
        return True  # continua o timer

    def _fetch_status(self, generation):
        snapshot = fetch_snapshot()
        GLib.idle_add(self._apply_status, generation, snapshot)

    def _apply_status(self, generation, snapshot):
        if self._closed:
            return False
        self._refreshing = False
        if generation != self._generation or self._command_running:
            self._refresh_requested = True
        else:
            data, prefs, error = snapshot
            self._snapshot = snapshot
            self._status_page.update(data, error)
            app = self.get_application()
            if app.tray is not None:
                app.tray.update(self._status_page.state_badge.get_text())
            self._peers_page.update(data)
            self._settings_page.update(data, prefs, error, force=self._reset_exit_selection)
            self._reset_exit_selection = False
            self._set_busy(False)
            if error and error != self._last_error:
                self._status_page._log_main("⚠ " + error)
            self._last_error = error
        self._refresh_spinner.stop()
        if self._refresh_requested and not self._command_running:
            self._refresh()
        return False

    def _set_busy(self, busy):
        self._busy = busy
        self._status_page._set_busy(busy)
        self._settings_page._set_busy(busy)
        tray = self.get_application().tray
        if tray is not None:
            tray.set_controls(self._status_page.btn_up.get_sensitive(),
                              self._status_page.btn_down.get_sensitive())

    def _run_command(self, *args):
        if self._busy or self._closed:
            return False
        self._generation += 1  # Descarte leituras iniciadas antes desta alteração.
        self._command_running = True
        self._reset_exit_selection = any(arg.startswith("--exit-node=") for arg in args)
        self._set_busy(True)
        self._status_page._log_main("→ tailscale " + " ".join(args))
        threading.Thread(target=self._command_worker, args=(args,), daemon=True).start()
        return True

    def _command_worker(self, args):
        def output(line):
            GLib.idle_add(self._command_output, line, args[0] == "up")
        result = run_change(*args, on_output=output if args[0] == "up" else None)
        GLib.idle_add(self._command_done, args, result)

    def _command_output(self, line, connecting):
        if not self._closed:
            self._status_page._log_main(line)
            if connecting:
                self._status_page.show_login(auth_url(line))
        return False

    def _command_done(self, args, result):
        if self._closed:
            return False
        self._command_running = False
        out, err, code = result
        message = "Operação concluída" if code == 0 else f"Operação falhou (código {code})"
        self._status_page._log_main("← " + message)
        if args[0] != "up" and out:
            self._status_page._log_main(out)
        if err:
            self._status_page._log_main(err)
        self._toasts.add_toast(Adw.Toast.new(message if code == 0 else "Falha ao alterar Tailscale. Veja o log em Status."))
        # Reponha as preferências anteriores em caso de erro, depois confirme no daemon.
        if code != 0:
            data, prefs, error = self._snapshot
            self._settings_page.update(data, prefs, error, force=self._reset_exit_selection)
        self._refresh()
        return False

    def _on_close(self, _window):
        app = self.get_application()
        if app is not None and app.can_minimize_to_tray():
            self.set_visible(False)
            return True
        self._dispose()
        if app is not None and app._window is self:
            app._window = None
        return False

    def _dispose(self):
        self._closed = True
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None


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
    background-color: alpha(@success_color, 0.15);
    color: @success_color;
}
.badge-red {
    background-color: alpha(@error_color, 0.15);
    color: @error_color;
}
.badge-blue {
    background-color: alpha(@accent_color, 0.15);
    color: @accent_color;
}
.badge-yellow {
    background-color: alpha(@warning_color, 0.15);
    color: @warning_color;
}
.online-dot {
    color: @success_color;
    font-size: 1.1em;
    margin-right: 4px;
}
.offline-dot {
    color: alpha(@window_fg_color, 0.45);
    font-size: 1.1em;
    margin-right: 4px;
}
"""


# ──────────────────────────── Application ───────────────────────────

class TailscaleApp(Adw.Application):
    def __init__(self, desktop_preferences=None):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.desktop_preferences = desktop_preferences or DesktopPreferences()
        self.tray = None
        self._window = None
        self._held = False
        self._quitting = False

    def do_activate(self):
        if self._window is None:
            self._window = TailscaleWindow(self)
        self._window.present()

    def can_minimize_to_tray(self):
        return (not self._quitting and self.desktop_preferences.minimize_to_tray
                and self.tray is not None and self.tray.available)

    def _tray_command(self, command):
        window = self._window
        if self._quitting or window is None or window._closed:
            return
        button = {"up": window._status_page.btn_up, "down": window._status_page.btn_down}.get(command)
        if button is not None and button.get_sensitive():
            window._run_command(command)

    def _sync_hold(self):
        hold = bool(self.can_minimize_to_tray())
        if hold and not self._held:
            self.hold()
            self._held = True
        elif not hold and self._held:
            self.release()
            self._held = False

    def _tray_available_changed(self, available):
        if self._quitting:
            return
        # Reabre a janela se o painel desaparecer enquanto o app estiver oculto.
        if not available and self._window is not None and not self._window.get_visible():
            self._window.present()
        self._sync_hold()
        if self._window is not None:
            self._window._settings_page.set_desktop_settings(self.desktop_preferences.minimize_to_tray, available)

    def set_minimize_to_tray(self, enabled):
        try:
            self.desktop_preferences.save(enabled)
            error = ""
        except OSError as exc:
            error = f"Não foi possível salvar a preferência: {exc}"
        if self._window is not None:
            self._window._settings_page.set_desktop_settings(
                self.desktop_preferences.minimize_to_tray,
                self.tray is not None and self.tray.available, error)
            if not self.can_minimize_to_tray() and not self._window.get_visible():
                self._window.present()
        self._sync_hold()

    def quit_app(self):
        self._quitting = True
        if self._window is not None:
            self._window._dispose()
            self._window.destroy()
            self._window = None
        if self.tray is not None:
            self.tray.stop()
        self._sync_hold()
        self.quit()

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Registrar ícone se necessário
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        # Permite usar o ícone do projeto ao executar sem instalar.
        icon_dir = os.path.dirname(os.path.realpath(__file__))
        if os.path.isfile(os.path.join(icon_dir, ICON_NAME + ".svg")):
            # O ícone do código em execução tem prioridade sobre versões instaladas.
            theme_dir = os.path.join(icon_dir, "icons")
            theme.set_search_path([theme_dir] + [path for path in theme.get_search_path() if path != theme_dir])
        else:
            icon_dir = "/usr/share/icons/hicolor/scalable/apps"
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit_app())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.tray = StatusNotifierTray(self.get_dbus_connection(), self.activate, self.quit_app,
                                       self._tray_available_changed, icon_dir, self._tray_command)
        self.tray.start()

    def do_shutdown(self):
        self._quitting = True
        if self._window is not None:
            self._window._dispose()
        if self.tray is not None:
            self.tray.stop()
        self._sync_hold()
        Adw.Application.do_shutdown(self)


def main():
    app = TailscaleApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
