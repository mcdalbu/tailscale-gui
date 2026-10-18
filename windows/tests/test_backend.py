import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from windows.backend import (
    Preferences, Result, Snapshot, TailscaleClient, exit_choices, find_tailscale,
    login_url, node_ip, parse_object, peers,
)


DATA = {"BackendState": "Running", "Self": {"HostName": "desktop", "TailscaleIPs": ["100.64.0.1"]},
        "CurrentTailnet": {"Name": "Rede de teste"}, "Peer": {
            "a": {"ID": "node-a", "HostName": "servidor", "TailscaleIPs": ["100.64.0.2"],
                  "Online": True, "ExitNodeOption": True},
            "b": {"ID": "node-b", "HostName": "servidor", "TailscaleIPs": ["100.64.0.3"],
                  "Online": False, "ExitNodeOption": True}}}
PREFS = {"RouteAll": True, "ShieldsUp": False, "RunSSH": False, "ExitNodeID": "node-b", "ExitNodeIP": ""}


class BackendTests(unittest.TestCase):
    def test_finds_client_in_program_files_when_not_on_path(self):
        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=True), \
             patch("windows.backend.Path.is_file", return_value=True), \
             patch("windows.backend.shutil.which") as which:
            self.assertEqual(find_tailscale(), r"C:\Program Files\Tailscale\tailscale.exe")
        which.assert_not_called()

    def test_falls_back_to_path(self):
        with patch.dict(os.environ, {}, clear=True), patch("windows.backend.shutil.which", return_value="tailscale.exe"):
            self.assertEqual(find_tailscale(), "tailscale.exe")

    def test_missing_client_is_actionable(self):
        with patch("windows.backend.find_tailscale", return_value=None):
            result = TailscaleClient().run("status")
        self.assertEqual(result.code, 127)
        self.assertIn("Instale", result.stderr)

    def test_command_uses_argument_list_without_shell(self):
        with patch("windows.backend.subprocess.run", return_value=Mock(stdout="ok", stderr="warning", returncode=0)) as run:
            result = TailscaleClient("tailscale.exe").run("set", "--exit-node=100.64.0.2")
        self.assertEqual(run.call_args.args[0], ["tailscale.exe", "set", "--exit-node=100.64.0.2"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(result, Result("ok", "warning", 0))

    @unittest.skipUnless(sys.platform == "win32", "Flags de processo específicas do Windows")
    def test_windows_cli_does_not_open_console_windows(self):
        with patch("windows.backend.subprocess.run", return_value=Mock(stdout="", stderr="", returncode=0)) as run:
            TailscaleClient("tailscale.exe").run("status")
        self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_timeout_keeps_output_and_login_link(self):
        error = subprocess.TimeoutExpired("tailscale.exe", 10, output=b"https://login.tailscale.com/a/test", stderr=b"waiting")
        with patch("windows.backend.subprocess.run", side_effect=error):
            result = TailscaleClient("tailscale.exe").run("up")
        self.assertEqual(result.code, 124)
        self.assertIn("/a/test", result.stdout)
        self.assertIn("waiting", result.stderr)

    def test_invalid_json_and_non_object_json_are_rejected(self):
        for value in ("invalid", "[]", "42", "null"):
            self.assertIsNone(parse_object(value))

    def test_fetch_reads_real_preferences_and_removes_secrets(self):
        prefs = dict(PREFS, Persist={"PrivateNodeKey": "secret"})
        client = TailscaleClient("tailscale.exe")
        with patch.object(client, "run", side_effect=[Result(json.dumps(DATA)), Result(json.dumps(prefs))]):
            snapshot = client.fetch()
        self.assertEqual(snapshot.prefs, PREFS)
        self.assertEqual(snapshot.label, "Conectado")
        self.assertNotIn("secret", repr(snapshot))

    def test_pref_error_does_not_hide_connection(self):
        client = TailscaleClient("tailscale.exe")
        with patch.object(client, "run", side_effect=[Result(json.dumps(DATA)), Result(stderr="Access denied", code=1)]):
            snapshot = client.fetch()
        self.assertEqual(snapshot.label, "Conectado")
        self.assertIsNone(snapshot.prefs)
        self.assertIn("Access denied", snapshot.error)

    def test_daemon_error_is_not_reported_as_disconnected(self):
        client = TailscaleClient("tailscale.exe")
        with patch.object(client, "run", return_value=Result(stderr="serviço parado", code=1)) as run:
            snapshot = client.fetch()
        self.assertEqual(snapshot.label, "Indisponível")
        self.assertEqual(run.call_count, 1)

    def test_connect_does_not_modify_existing_settings(self):
        client = TailscaleClient("tailscale.exe")
        with patch.object(client, "run", return_value=Result()) as run:
            client.change("up")
        self.assertEqual(run.call_args.args, ("up",))

    def test_unsupported_ssh_is_not_sent_to_daemon(self):
        client = TailscaleClient("tailscale.exe")
        with patch.object(client, "run") as run:
            self.assertEqual(client.change("set", "--ssh=true").code, 2)
        run.assert_not_called()

    def test_permission_failure_explains_admin_retry_without_pkexec(self):
        client = TailscaleClient("tailscale.exe")
        with patch.object(client, "run", return_value=Result(stderr="Access is denied", code=1)) as run:
            result = client.change("down")
        self.assertIn("Executar como administrador", result.stderr)
        self.assertEqual(run.call_count, 1)

    def test_streamed_login_link_is_delivered_before_completion(self):
        lines = []
        client = TailscaleClient(sys.executable)
        result = client.run("-c", "print('https://login.tailscale.com/a/test', flush=True)", on_output=lines.append)
        self.assertEqual(result.code, 0)
        self.assertEqual(lines, ["https://login.tailscale.com/a/test"])

    def test_stream_timeout_preserves_partial_output(self):
        client = TailscaleClient(sys.executable)
        result = client.run("-c", "import time; print('waiting',flush=True); time.sleep(10)", timeout=.3, on_output=lambda _: None)
        self.assertEqual(result.code, 124)
        self.assertEqual(result.stdout, "waiting")

    def test_cancel_stops_connect_worker(self):
        client = TailscaleClient(sys.executable)
        timer = threading.Timer(.2, client.cancelled.set)
        timer.start()
        try:
            result = client.run("-c", "import time; time.sleep(10)", on_output=lambda _: None)
        finally:
            timer.join()
        self.assertEqual(result.code, 130)

    def test_cancelled_client_does_not_start_more_processes(self):
        client = TailscaleClient("tailscale.exe")
        client.cancelled.set()
        with patch("windows.backend.subprocess.run") as run:
            self.assertEqual(client.run("status").code, 130)
        run.assert_not_called()

    def test_headscale_links_are_supported_and_unsafe_schemes_rejected(self):
        self.assertEqual(login_url("Visit https://headscale.example/register/test."), "https://headscale.example/register/test")
        for value in ("javascript:alert(1)", "https://user:pass@example.com/", "https://[invalid/"):
            self.assertEqual(login_url(value), "")

    def test_duplicate_exit_names_have_distinct_ip_values(self):
        choices, selected = exit_choices(Snapshot(DATA, PREFS))
        self.assertEqual([value for _, value in choices], ["", "100.64.0.2", "100.64.0.3"])
        self.assertEqual(selected, "100.64.0.3")

    def test_missing_exit_node_keeps_unknown_selection(self):
        choices, selected = exit_choices(Snapshot({"Peer": None}, PREFS))
        self.assertIsNone(selected)
        self.assertIsNone(choices[-1][1])

    def test_ipv6_only_peer_and_null_peers(self):
        self.assertEqual(node_ip({"TailscaleIPs": ["fd7a::1"]}), "fd7a::1")
        self.assertEqual(peers({"Peer": None}), [])

    def test_preferences_persist_in_configured_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = Preferences(path)
            store.save(True)
            self.assertTrue(Preferences(path).minimize_to_tray)

    def test_failed_preference_save_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = Preferences(path)
            store.save(False)
            with patch("windows.backend.os.replace", side_effect=OSError("disco cheio")):
                with self.assertRaises(OSError):
                    store.save(True)
            self.assertFalse(store.minimize_to_tray)
            self.assertFalse(Preferences(path).minimize_to_tray)
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
