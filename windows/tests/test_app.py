import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from windows.app import Window, serve_single_instance
from windows.backend import Preferences, Result, Snapshot

DATA = {"BackendState": "Running", "Self": {"HostName": "desktop", "TailscaleIPs": ["100.64.0.1"]},
        "Peer": {"a": {"ID": "a", "HostName": "server", "Online": True, "ExitNodeOption": True,
                        "TailscaleIPs": ["100.64.0.2"]},
                 "b": {"ID": "b", "HostName": "server", "Online": False, "ExitNodeOption": True,
                        "TailscaleIPs": ["100.64.0.3"]}}}
PREFS = {"RouteAll": True, "ShieldsUp": False, "ExitNodeID": "b", "ExitNodeIP": ""}


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def window(self, preferences=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        client = Mock()
        client.cancelled = threading.Event()
        window = Window(client, preferences or Preferences(Path(directory.name) / "settings.json"),
                        autorefresh=False, enable_tray=False)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.shutdown)
        window.render(Snapshot(copy.deepcopy(DATA), dict(PREFS)))
        return window

    def test_packaged_identity_icon_loads(self):
        self.assertFalse(self.window().windowIcon().isNull())

    def test_rendering_real_preferences_does_not_write_them(self):
        window = self.window()
        self.assertTrue(window.switches["RouteAll"].isChecked())
        self.assertFalse(window.switches["ShieldsUp"].isChecked())
        window.client.change.assert_not_called()

    def test_switch_only_changes_requested_flag(self):
        window = self.window()
        with patch.object(window, "change") as change:
            window.switches["ShieldsUp"].setChecked(True)
        change.assert_called_once_with("set", "--shields-up=true")

    def test_exit_node_selection_uses_ip_not_duplicate_name(self):
        window = self.window()
        self.assertEqual(window.exit_combo.currentData(), "100.64.0.3")
        with patch.object(window, "change") as change:
            window.apply_exit()
        change.assert_called_once_with("set", "--exit-node=100.64.0.3")

    def test_refresh_keeps_unapplied_exit_selection(self):
        window = self.window()
        window.exit_combo.setCurrentIndex(1)
        window.render(Snapshot(DATA, PREFS))
        self.assertEqual(window.exit_combo.currentData(), "100.64.0.2")

    def test_force_refresh_restores_actual_exit_node(self):
        window = self.window()
        window.exit_combo.setCurrentIndex(1)
        window.render(Snapshot(DATA, PREFS), force_exit=True)
        self.assertEqual(window.exit_combo.currentData(), "100.64.0.3")

    def test_unknown_preferences_disable_network_controls(self):
        window = self.window()
        window.render(Snapshot(DATA, None, "sem permissão"))
        self.assertFalse(window.exit_button.isEnabled())
        self.assertFalse(window.switches["RouteAll"].isEnabled())
        self.assertEqual(window.state_action.text(), "Conectado")

    def test_daemon_error_clears_old_ip(self):
        window = self.window()
        window.render(Snapshot(error="Serviço indisponível"))
        self.assertEqual(window.details["ip"].text(), "—")
        self.assertEqual(window.state_action.text(), "Indisponível")
        self.assertFalse(window.up_button.isEnabled())

    def test_hidden_window_updates_tray_text_and_tooltip_through_all_transitions(self):
        window = self.window()
        window.hide()
        for backend, label in (("Stopped", "Desconectado"), ("Running", "Conectado"),
                               ("Stopped", "Desconectado"), ("Running", "Conectado")):
            window.render(Snapshot(dict(DATA, BackendState=backend), PREFS))
            self.assertFalse(window.isVisible())
            self.assertEqual(window.state_action.text(), label)
            self.assertIn(label, window.tray.toolTip())

    def test_close_to_tray_keeps_refresh_timer_alive(self):
        window = self.window()
        window.preferences.minimize_to_tray = True
        window.timer.start()
        event = QCloseEvent()
        with patch.object(window, "tray_available", return_value=True):
            window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(window.closed)
        self.assertTrue(window.timer.isActive())

    def test_no_tray_closes_safely_and_cancels_workers(self):
        window = self.window()
        window.preferences.minimize_to_tray = True
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertTrue(window.closed)
        self.assertTrue(window.client.cancelled.is_set())

    def test_tray_loss_restores_hidden_window(self):
        window = self.window()
        window.preferences.minimize_to_tray = True
        window.hide()
        with patch.object(window, "show_window") as show:
            window._sync_desktop()
        show.assert_called_once()

    def test_refresh_jobs_are_coalesced(self):
        window = self.window()
        with patch.object(window, "_start_job") as start:
            window.refresh()
            window.refresh()
            window.refresh()
        self.assertEqual(start.call_count, 1)
        self.assertTrue(window.refresh_pending)

    def test_command_serialization_and_stale_reads(self):
        window = self.window()
        with patch.object(window, "_start_job") as start:
            window.change("set", "--shields-up=true")
            window.change("down")
            window._fetched((0, Snapshot({"BackendState": "Stopped"}, PREFS)))
        self.assertEqual(start.call_count, 1)
        self.assertTrue(window.busy)
        self.assertTrue(window.refresh_pending)
        self.assertEqual(window.snapshot.label, "Conectado")

    def test_command_failure_restores_preferences_before_refreshing(self):
        window = self.window()
        with patch.object(window, "change"):
            window.switches["ShieldsUp"].setChecked(True)
        with patch.object(window, "refresh") as refresh:
            window._changed(Result(stderr="denied", code=1))
        self.assertFalse(window.switches["ShieldsUp"].isChecked())
        refresh.assert_called_once()

    def test_login_link_is_presented_without_changing_preferences(self):
        window = self.window()
        with patch.object(window, "show_window") as show:
            window.command_line("Visit https://headscale.example/register/test")
        self.assertEqual(window.auth_link, "https://headscale.example/register/test")
        show.assert_called_once()
        window.client.change.assert_not_called()

    def test_preference_write_failure_reverts_switch(self):
        window = self.window()
        with patch.object(window.preferences, "save", side_effect=OSError("disco cheio")):
            window.save_tray_preference(True)
        self.assertFalse(window.tray_switch.isChecked())
        self.assertIn("disco cheio", window.tray_help.text())

    def test_second_instance_reopens_first_window(self):
        first = self.window()
        second = self.window(Preferences(first.preferences.path))
        server = serve_single_instance(first)
        self.addCleanup(server.close)
        with patch.object(first, "show_window") as show:
            self.assertIsNone(serve_single_instance(second))
            for _ in range(5):
                self.app.processEvents()
            show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
