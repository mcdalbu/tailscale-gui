"""Regressões de CLI e GTK; todos os comandos Tailscale são simulados."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tailscale_gui", ROOT / "tailscale-gui.py")
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)

STATUS = {
    "BackendState": "Running", "Self": {"HostName": "desktop", "TailscaleIPs": ["100.64.0.1"]},
    "CurrentTailnet": {"Name": "Minha rede"},
    "Peer": {
        "a": {"ID": "node-a", "HostName": "servidor", "TailscaleIPs": ["100.64.0.2"],
              "Online": True, "ExitNodeOption": True},
        "b": {"ID": "node-b", "HostName": "servidor", "TailscaleIPs": ["100.64.0.3"],
              "Online": False, "ExitNodeOption": True},
    },
}
PREFS = {"RouteAll": True, "RunSSH": False, "ShieldsUp": True, "ExitNodeID": "node-b", "ExitNodeIP": ""}


class CliTests(unittest.TestCase):
    def test_missing_client(self):
        with patch.object(gui.shutil, "which", return_value=None):
            self.assertEqual(gui.run_tailscale("status")[2], 127)

    def test_missing_pkexec_is_reported(self):
        with patch.object(gui.shutil, "which", side_effect=["/usr/bin/tailscale", None]):
            result = gui.run_tailscale("down", as_root=True)
        self.assertIn("pkexec", result[1])
        self.assertEqual(result[2], 127)

    def test_command_is_argument_list_with_absolute_binary(self):
        with patch.object(gui.shutil, "which", return_value="/usr/bin/tailscale"), \
             patch.object(gui.subprocess, "run", return_value=Mock(stdout="", stderr="warning", returncode=0)) as run:
            self.assertEqual(gui.run_tailscale("set", "--exit-node=100.64.0.2"), ("", "warning", 0))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/tailscale", "set", "--exit-node=100.64.0.2"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_timeout_preserves_auth_link_and_error(self):
        error = subprocess.TimeoutExpired("tailscale", 15, output=b"https://login.tailscale.com/a/test", stderr=b"waiting")
        with patch.object(gui.shutil, "which", return_value="/usr/bin/tailscale"), \
             patch.object(gui.subprocess, "run", side_effect=error):
            out, err, code = gui.run_tailscale("up")
        self.assertIn("/a/test", out)
        self.assertIn("waiting", err)
        self.assertEqual(code, 124)

    def test_os_error_is_reported(self):
        with patch.object(gui.shutil, "which", return_value="/usr/bin/tailscale"), \
             patch.object(gui.subprocess, "run", side_effect=PermissionError("denied")):
            self.assertEqual(gui.run_tailscale("status")[2], 1)

    def test_permission_failure_retries_same_command_with_polkit(self):
        with patch.object(gui, "run_tailscale", side_effect=[("", "Access denied", 1), ("", "", 0)]) as run:
            self.assertEqual(gui.run_change("set", "--ssh=true")[2], 0)
        self.assertEqual(run.call_args_list[1].args, ("set", "--ssh=true"))
        self.assertTrue(run.call_args_list[1].kwargs["as_root"])

    def test_other_errors_do_not_escalate(self):
        with patch.object(gui, "run_tailscale", return_value=("", "Daemon unavailable", 1)) as run:
            self.assertEqual(gui.run_change("down")[2], 1)
        self.assertEqual(run.call_count, 1)

    def test_streamed_authentication_and_failure_code(self):
        lines = []
        out, err, code = gui._run_streamed(
            [sys.executable, "-c", "print('https://login.tailscale.com/a/test', flush=True); raise SystemExit(42)"], 5, lines.append)
        self.assertEqual(code, 42)
        self.assertIn("https://login.tailscale.com/a/test", lines)
        self.assertIn("/a/test", out)

    def test_stream_timeout_terminates_child_and_preserves_output(self):
        out, err, code = gui._run_streamed([sys.executable, "-c", "import time; print('waiting', flush=True); time.sleep(10)"], .3, lambda _: None)
        self.assertEqual(code, 124)
        self.assertEqual(out, "waiting")
        self.assertIn("Tempo limite", err)

    def test_snapshot_uses_real_prefs_and_removes_private_material(self):
        prefs = dict(PREFS, Persist={"PrivateNodeKey": "secret"})
        with patch.object(gui, "run_tailscale", side_effect=[(json.dumps(STATUS), "", 0), (json.dumps(prefs), "", 0)]):
            status, actual, error = gui.fetch_snapshot()
        self.assertEqual(status, STATUS)
        self.assertEqual(actual, PREFS)
        self.assertEqual(error, "")

    def test_daemon_failure_does_not_query_prefs(self):
        with patch.object(gui, "run_tailscale", return_value=("", "daemon stopped", 1)) as run:
            self.assertEqual(gui.fetch_snapshot(), (None, None, "daemon stopped"))
        self.assertEqual(run.call_count, 1)

    def test_invalid_status_objects(self):
        for value in ("invalid", "null", "[]", "42"):
            with self.subTest(value=value), patch.object(gui, "run_tailscale", return_value=(value, "", 0)):
                self.assertIsNone(gui.fetch_snapshot()[0])

    def test_pref_failure_keeps_status_available(self):
        with patch.object(gui, "run_tailscale", side_effect=[(json.dumps(STATUS), "", 0), ("", "Access denied", 1)]):
            status, prefs, error = gui.fetch_snapshot()
        self.assertEqual(status, STATUS)
        self.assertIsNone(prefs)
        self.assertIn("Access denied", error)

    def test_ip_prefers_ipv4_and_supports_ipv6_only(self):
        self.assertEqual(gui.node_ip({"TailscaleIPs": ["fd7a::1", "100.64.0.1"]}), "100.64.0.1")
        self.assertEqual(gui.node_ip({"TailscaleIPs": ["fd7a::1"]}), "fd7a::1")
        self.assertEqual(gui.node_ip({"TailscaleIPs": None}), "")

    def test_auth_url_supports_headscale_and_rejects_other_schemes(self):
        self.assertEqual(gui.auth_url("Visit https://headscale.example/register/test."), "https://headscale.example/register/test")
        self.assertEqual(gui.auth_url("javascript:alert(1)"), "")
        self.assertEqual(gui.auth_url("https://user:password@example.com/"), "")
        self.assertEqual(gui.auth_url("https://[invalid/"), "")

    def test_nullable_peers(self):
        self.assertEqual(gui.peers_from({"Peer": None}), [])
        self.assertEqual(gui.peers_from({"Peer": {"invalid": None}}), [])


class GtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gui.Gtk.init_check()
        if gui.Gdk.Display.get_default() is None:
            raise unittest.SkipTest("GTK precisa de um display (real, Xvfb ou Broadway).")
        gui.Adw.init()
        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.settings_dir.cleanup)
        cls.app = gui.TailscaleApp(gui.DesktopPreferences(Path(cls.settings_dir.name) / "settings.json"))
        cls.app.set_application_id("io.tailscale.TailscaleGUI.Tests")
        cls.app.set_flags(gui.Gio.ApplicationFlags.NON_UNIQUE)
        with patch.object(gui.StatusNotifierTray, "start"):
            cls.app.register(None)
        cls.app.tray = None

    def settings(self):
        page = gui.SettingsPage()
        page.set_command_callback(Mock())
        page.update(copy.deepcopy(STATUS), dict(PREFS))
        return page

    def window(self):
        self.app.desktop_preferences.minimize_to_tray = False
        self.app.tray = None
        self.app._held = False
        self.app._quitting = False
        with patch.object(gui.TailscaleWindow, "_refresh"):
            window = gui.TailscaleWindow(self.app)
        self.app._window = window
        self.addCleanup(window.destroy)
        self.addCleanup(window._on_close, window)
        return window

    def test_preferences_are_loaded_without_triggering_commands(self):
        page = self.settings()
        self.assertTrue(page.sw_routes.get_active())
        self.assertFalse(page.sw_ssh.get_active())
        self.assertTrue(page.sw_shields.get_active())
        page._command_cb.assert_not_called()

    def test_toggle_changes_only_requested_preference(self):
        page = self.settings()
        page.sw_ssh.set_active(True)
        page._command_cb.assert_called_once_with("set", "--ssh=true")

    def test_missing_peer_ids_do_not_select_an_exit_node(self):
        page = self.settings()
        status = copy.deepcopy(STATUS)
        for peer in status["Peer"].values():
            peer.pop("ID")
        page.update(status, {"RouteAll": True, "RunSSH": False, "ShieldsUp": False}, force=True)
        self.assertEqual(page.exit_row.get_selected(), 0)

    def test_successful_unrelated_change_preserves_pending_exit_selection(self):
        window = self.window()
        window._settings_page.update(STATUS, PREFS)
        window._settings_page.exit_row.set_selected(1)
        window._busy = True
        window._apply_status(0, (STATUS, dict(PREFS, RunSSH=True), ""))
        self.assertEqual(window._settings_page.exit_row.get_selected(), 1)
        self.assertFalse(window._busy)

    def test_unknown_preferences_disable_controls(self):
        page = self.settings()
        page.update(STATUS, None, "Sem permissão")
        self.assertFalse(page.sw_routes.get_sensitive())
        self.assertFalse(page.sw_ssh.get_sensitive())
        self.assertFalse(page.apply_exit_btn.get_sensitive())
        self.assertEqual(page.error_label.get_text(), "Sem permissão")

    def test_exit_node_uses_unique_ip_for_duplicate_names(self):
        page = self.settings()
        self.assertEqual(page._exit_peers[page.exit_row.get_selected()], "100.64.0.3")
        page._on_apply_exit(None)
        page._command_cb.assert_called_once_with("set", "--exit-node=100.64.0.3")

    def test_refresh_preserves_unapplied_exit_selection(self):
        page = self.settings()
        page.exit_row.set_selected(page._exit_peers.index("100.64.0.2"))
        page.update(STATUS, PREFS)
        self.assertEqual(page._exit_peers[page.exit_row.get_selected()], "100.64.0.2")

    def test_failed_operation_restores_actual_exit_node(self):
        page = self.settings()
        page.exit_row.set_selected(page._exit_peers.index("100.64.0.2"))
        page.update(STATUS, PREFS, force=True)
        self.assertEqual(page._exit_peers[page.exit_row.get_selected()], "100.64.0.3")

    def test_missing_current_exit_node_is_not_shown_as_none(self):
        page = self.settings()
        page.update({"BackendState": "Running", "Peer": {}}, PREFS, force=True)
        self.assertIsNone(page._exit_peers[page.exit_row.get_selected()])
        self.assertFalse(page.apply_exit_btn.get_sensitive())
        page.exit_row.set_selected(0)
        self.assertTrue(page.apply_exit_btn.get_sensitive())
        page._on_apply_exit(None)
        page._command_cb.assert_called_once_with("set", "--exit-node=")

    def test_selected_exit_status_ip_without_peer(self):
        page = self.settings()
        page.update({"BackendState": "Running", "Peer": {}, "ExitNodeStatus": {"TailscaleIPs": ["100.64.0.9"]}}, PREFS, force=True)
        self.assertEqual(page._exit_peers[page.exit_row.get_selected()], "100.64.0.9")

    def test_connection_preserves_preferences(self):
        page = gui.StatusPage()
        command = Mock()
        page.set_command_callback(command)
        page._on_up(None)
        command.assert_called_once_with("up")

    def test_tray_commands_follow_status_and_busy_state_while_hidden(self):
        window = self.window()
        tray = self.app.tray = Mock(available=True)
        with patch.object(gui.threading, "Thread") as worker:
            for backend, command, enabled in (("Stopped", "up", (True, False)),
                                              ("Running", "down", (False, True))):
                with self.subTest(backend=backend):
                    window._command_running = False
                    window._apply_status(window._generation, (dict(STATUS, BackendState=backend), PREFS, ""))
                    tray.set_controls.assert_called_with(*enabled)
                    window.set_visible(False)
                    self.app._tray_command(command)
                    self.assertEqual(worker.call_args.kwargs["args"], ((command,),))
                    tray.set_controls.assert_called_with(False, False)
                    count = worker.call_count
                    self.app._tray_command(command)
                    self.assertEqual(worker.call_count, count)
                    self.assertFalse(window.get_visible())

    def test_tray_commands_are_disabled_when_status_is_unavailable(self):
        window = self.window()
        tray = self.app.tray = Mock(available=True)
        window._apply_status(window._generation, (None, None, "daemon indisponível"))
        tray.set_controls.assert_called_with(False, False)
        with patch.object(window, "_run_command") as command:
            self.app._tray_command("up")
            self.app._tray_command("down")
        command.assert_not_called()

    def test_status_error_clears_stale_details_and_disables_commands(self):
        page = gui.StatusPage()
        page.update(STATUS)
        page.update(None, "daemon indisponível")
        self.assertEqual(page.lbl_ip.get_text(), "—")
        self.assertFalse(page.btn_up.get_sensitive())
        self.assertFalse(page.btn_down.get_sensitive())
        self.assertEqual(page.message.get_text(), "daemon indisponível")

    def test_authentication_and_machine_approval_states(self):
        page = gui.StatusPage()
        page.update({"BackendState": "NeedsLogin", "Self": None, "AuthURL": "https://headscale.example/register/1"})
        self.assertEqual(page.state_badge.get_text(), "Login necessário")
        self.assertTrue(page.login_btn.get_visible())
        page.update({"BackendState": "NeedsMachineAuth"})
        self.assertIn("Aprove", page.message.get_text())
        page.update(STATUS)
        self.assertFalse(page.login_btn.get_visible())

    def test_logs_remain_bounded(self):
        page = gui.StatusPage()
        for idx in range(1000):
            page._log_main(str(idx))
        self.assertLessEqual(page.log_buf.get_line_count(), 500)

    def test_peers_are_not_rebuilt_for_unchanged_display_data(self):
        page = gui.PeersPage()
        page.update(STATUS)
        first_row = page._rows[0]
        changed = copy.deepcopy(STATUS)
        changed["Peer"]["a"]["RxBytes"] = 999
        page.update(changed)
        self.assertIs(page._rows[0], first_row)

    def test_navigation_keeps_parents_and_all_pages(self):
        window = self.window()
        parents = [page.get_parent() for _, _, page in window._pages]
        for idx in (0, 1, 2, 0, 2, 1, 0):
            window._show_page(idx)
        self.assertEqual(window._stack.get_pages().get_n_items(), 3)
        self.assertEqual([page.get_parent() for _, _, page in window._pages], parents)

    def test_refreshes_are_coalesced(self):
        window = self.window()
        with patch.object(gui.threading, "Thread") as thread:
            window._refresh()
            window._refresh()
            window._refresh()
        self.assertEqual(thread.call_count, 1)
        self.assertTrue(window._refresh_requested)

    def test_command_disables_other_operations_and_invalidates_old_refresh(self):
        window = self.window()
        window._snapshot = (STATUS, PREFS, "")
        with patch.object(gui.threading, "Thread"):
            self.assertTrue(window._run_command("set", "--ssh=true"))
            self.assertFalse(window._run_command("down"))
        window._apply_status(0, (STATUS, PREFS, ""))
        self.assertTrue(window._busy)
        self.assertTrue(window._refresh_requested)

    def test_failure_rolls_back_then_refreshes_after_completion(self):
        window = self.window()
        window._snapshot = (STATUS, PREFS, "")
        window._settings_page.update(STATUS, PREFS)
        window._settings_page._block_signals = True
        window._settings_page.sw_ssh.set_active(True)
        window._settings_page._block_signals = False
        with patch.object(window, "_refresh") as refresh:
            window._command_done(("set", "--ssh=true"), ("", "denied", 1))
        self.assertFalse(window._settings_page.sw_ssh.get_active())
        refresh.assert_called_once()

    def test_close_removes_timer_and_ignores_late_updates(self):
        window = self.window()
        window._on_close(window)
        self.assertIsNone(window._timer_id)
        self.assertFalse(window._apply_status(0, (STATUS, PREFS, "")))
        self.assertFalse(window._refresh())

    def test_close_hides_window_and_preserves_timer_when_tray_enabled(self):
        window = self.window()
        self.app.desktop_preferences.minimize_to_tray = True
        self.app.tray = Mock(available=True)
        timer = window._timer_id
        self.assertTrue(window._on_close(window))
        self.assertFalse(window.get_visible())
        self.assertFalse(window._closed)
        self.assertEqual(window._timer_id, timer)
        self.app.desktop_preferences.minimize_to_tray = False

    def test_no_tray_host_closes_instead_of_hiding(self):
        window = self.window()
        self.app.desktop_preferences.minimize_to_tray = True
        self.app.tray = Mock(available=False)
        self.assertFalse(window._on_close(window))
        self.assertTrue(window._closed)

    def test_connection_status_keeps_updating_while_window_is_hidden(self):
        window = self.window()
        self.app.desktop_preferences.minimize_to_tray = True
        tray = self.app.tray = Mock(available=True)
        self.assertTrue(window._on_close(window))
        timer = window._timer_id
        for backend, label in (("Stopped", "Desconectado"), ("Running", "Conectado"),
                               ("Stopped", "Desconectado"), ("Running", "Conectado")):
            status = dict(STATUS, BackendState=backend)
            window._apply_status(window._generation, (status, PREFS, ""))
            tray.update.assert_called_with(label)
            self.assertFalse(window.get_visible())
            self.assertEqual(window._timer_id, timer)
            self.assertFalse(window._closed)
        self.app.desktop_preferences.minimize_to_tray = False

    def test_hidden_window_is_reused_on_activate(self):
        window = self.window()
        self.app.desktop_preferences.minimize_to_tray = True
        self.app.tray = Mock(available=True)
        window._on_close(window)
        with patch.object(window, "present") as present, patch.object(gui, "TailscaleWindow") as constructor:
            self.app.do_activate()
        constructor.assert_not_called()
        present.assert_called_once()
        self.app.desktop_preferences.minimize_to_tray = False

    def test_tray_disappearing_restores_hidden_window_and_releases_hold(self):
        window = self.window()
        self.app.desktop_preferences.minimize_to_tray = True
        self.app.tray = Mock(available=False)
        self.app._held = True
        with patch.object(window, "present") as present, patch.object(self.app, "release") as release:
            self.app._tray_available_changed(False)
        present.assert_called_once()
        release.assert_called_once()
        self.assertFalse(self.app._held)

    def test_tray_preference_is_saved_and_does_not_modify_tailscale(self):
        window = self.window()
        self.app.tray = Mock(available=True)
        with patch.object(self.app, "hold") as hold, patch.object(gui, "run_change") as change:
            self.app.set_minimize_to_tray(True)
        hold.assert_called_once()
        change.assert_not_called()
        self.assertTrue(gui.DesktopPreferences(self.app.desktop_preferences.path).minimize_to_tray)
        self.app._held = False
        self.app.desktop_preferences.minimize_to_tray = False

    def test_preference_save_failure_restores_switch(self):
        window = self.window()
        self.app.tray = Mock(available=True)
        with patch.object(self.app.desktop_preferences, "save", side_effect=OSError("disco cheio")):
            self.app.set_minimize_to_tray(True)
        self.assertFalse(window._settings_page.sw_tray.get_active())
        self.assertIn("disco cheio", window._settings_page.sw_tray.get_subtitle())

    def test_quit_action_bypasses_minimize_and_cleans_up(self):
        window = self.window()
        self.app.desktop_preferences.minimize_to_tray = True
        tray = self.app.tray = Mock(available=True)
        with patch.object(self.app, "quit") as quit_app:
            self.app.quit_app()
        self.assertTrue(window._closed)
        self.assertIsNone(self.app._window)
        tray.stop.assert_called_once()
        quit_app.assert_called_once()

    def test_desktop_switch_is_independent_of_daemon_availability(self):
        page = gui.SettingsPage()
        callback = Mock()
        page.set_desktop_callback(callback)
        page.set_desktop_settings(False, True)
        page.update(None, None, "daemon indisponível")
        self.assertTrue(page.sw_tray.get_sensitive())
        page.sw_tray.set_active(True)
        callback.assert_called_once_with(True)

    def test_local_icon_has_priority_over_previously_installed_versions(self):
        theme = gui.Gtk.IconTheme.get_for_display(gui.Gdk.Display.get_default())
        icon = theme.lookup_icon(gui.ICON_NAME, None, 32, 1, gui.Gtk.TextDirection.NONE,
                                 gui.Gtk.IconLookupFlags.FORCE_REGULAR)
        self.assertEqual(Path(icon.get_file().get_path()).resolve(), ROOT / "tailscale-gui.svg")


if __name__ == "__main__":
    unittest.main()
