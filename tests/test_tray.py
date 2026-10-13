import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib
from tailscale_tray import DesktopPreferences, StatusNotifierTray, ITEM_XML, MENU_XML


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config" / "settings.json"

    def test_default_is_disabled(self):
        self.assertFalse(DesktopPreferences(self.path).minimize_to_tray)

    def test_setting_survives_restart(self):
        preferences = DesktopPreferences(self.path)
        preferences.save(True)
        self.assertTrue(DesktopPreferences(self.path).minimize_to_tray)
        preferences.save(False)
        self.assertFalse(DesktopPreferences(self.path).minimize_to_tray)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_or_non_boolean_settings_do_not_enable_hiding(self):
        self.path.parent.mkdir()
        for content in ("invalid", "[]", "null", '{"minimize_to_tray":"false"}', '{"minimize_to_tray":1}'):
            with self.subTest(content=content):
                self.path.write_text(content)
                self.assertFalse(DesktopPreferences(self.path).minimize_to_tray)

    def test_failed_save_preserves_previous_file_and_setting(self):
        preferences = DesktopPreferences(self.path)
        preferences.save(False)
        with patch("tailscale_tray.os.replace", side_effect=OSError("disco cheio")):
            with self.assertRaises(OSError):
                preferences.save(True)
        self.assertFalse(preferences.minimize_to_tray)
        self.assertEqual(json.loads(self.path.read_text()), {"minimize_to_tray": False})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tray = StatusNotifierTray(Mock(), Mock(), Mock(), Mock(), "/tmp/icons")

    def test_introspection_and_recursive_layout_are_valid(self):
        Gio.DBusNodeInfo.new_for_xml(ITEM_XML)
        Gio.DBusNodeInfo.new_for_xml(MENU_XML)
        layout = GLib.Variant("(u(ia{sv}av))", (1, self.tray._layout(0, -1, []))).unpack()
        self.assertEqual([child[0] for child in layout[1][2]], [1, 2, 3, 4])
        self.assertEqual(layout[1][2][-1][1]["label"], "Sair")

    def test_layout_depth_and_property_filter(self):
        self.assertEqual(self.tray._layout(0, 0, ["label"])[2], [])
        self.assertEqual(set(self.tray._layout(1, 0, ["label"])[1]), {"label"})

    def test_unknown_menu_item_returns_protocol_error(self):
        invocation = Mock()
        self.tray._menu_method(None, None, None, None, "GetLayout",
                               GLib.Variant("(iias)", (99, -1, [])), invocation)
        invocation.return_dbus_error.assert_called_once()

    def test_unknown_events_are_rejected(self):
        self.assertFalse(self.tray._event(99, "clicked"))
        self.assertTrue(self.tray._event(2, "clicked"))
        self.tray.show_window.assert_not_called()

    def test_status_updates_do_not_duplicate_notifications(self):
        self.tray._objects = [1, 2]
        self.tray.update("Conectado")
        self.tray.update("Conectado")
        self.assertEqual(self.tray.connection.emit_signal.call_count, 3)
        tooltip = self.tray._item_property(None, None, None, None, "ToolTip").unpack()
        self.assertEqual(tooltip[3], "Conectado")

    def test_status_change_updates_label_without_invalidating_layout(self):
        self.tray._objects = [1, 2]
        revision = self.tray._revision
        self.tray.update("Conectado")
        signals = {call.args[3]: call.args[4] for call in self.tray.connection.emit_signal.call_args_list}
        self.assertIn("ItemsPropertiesUpdated", signals)
        self.assertNotIn("LayoutUpdated", signals)
        self.assertEqual(signals["ItemsPropertiesUpdated"].unpack(), ([(2, {"label": "Conectado"})], []))
        self.assertEqual(self.tray._revision, revision)
        self.assertEqual(signals["PropertiesChanged"].unpack()[1]["ToolTip"][3], "Conectado")


WATCHER_XML = """<node><interface name="org.kde.StatusNotifierWatcher">
  <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
  <property name="ProtocolVersion" type="i" access="read"/>
  <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
  <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
  <signal name="StatusNotifierHostRegistered"/><signal name="StatusNotifierHostUnregistered"/>
</interface></node>"""


@unittest.skipUnless(os.environ.get("TAILSCALE_GUI_TEST_DBUS") == "1",
                     "Integração DBus exige um barramento isolado: dbus-run-session e TAILSCALE_GUI_TEST_DBUS=1")
class DBusIntegrationTests(unittest.TestCase):
    def connection(self):
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        connection = Gio.DBusConnection.new_for_address_sync(os.environ["DBUS_SESSION_BUS_ADDRESS"], flags, None, None)
        self.addCleanup(connection.close_sync, None)
        return connection

    def setUp(self):
        self.host = self.connection()
        self.client = self.connection()
        self.reader = self.connection()
        self.host_present = True
        self.registrations = []
        self.host.register_object("/StatusNotifierWatcher", Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0],
                                  self.register_item, self.property, None)
        self.host.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                            GLib.Variant("(su)", (StatusNotifierTray.WATCHER, 0)), None,
                            Gio.DBusCallFlags.NONE, 1000, None)
        self.show, self.quit, self.changed = Mock(), Mock(), Mock()
        self.tray = StatusNotifierTray(self.client, self.show, self.quit, self.changed, "/tmp/icons")
        self.addCleanup(self.tray.stop)
        self.tray.start()
        self.pump(lambda: self.tray.available)

    def register_item(self, _connection, sender, _path, _interface, _method, parameters, invocation):
        self.registrations.append((sender, parameters.unpack()[0]))
        invocation.return_value(None)

    def property(self, _connection, _sender, _path, _interface, name):
        if name == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", self.host_present)
        if name == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return GLib.Variant("as", [])

    def pump(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        while not condition() and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(.005)
        self.assertTrue(condition(), "Operação DBus não concluiu no tempo limite")

    def call(self, path, interface, method, parameters):
        results = []
        def finished(connection, result):
            try:
                results.append(connection.call_finish(result))
            except GLib.Error as error:
                results.append(error)
        self.reader.call(self.client.get_unique_name(), path, interface, method, parameters, None,
                          Gio.DBusCallFlags.NONE, 2000, None, finished)
        self.pump(lambda: bool(results))
        if isinstance(results[0], GLib.Error):
            raise results[0]
        return results[0]

    def set_host(self, present):
        self.host_present = present
        self.host.emit_signal(None, "/StatusNotifierWatcher", "org.freedesktop.DBus.Properties", "PropertiesChanged",
                              GLib.Variant("(sa{sv}as)", (StatusNotifierTray.WATCHER,
                                                          {"IsStatusNotifierHostRegistered": GLib.Variant("b", present)}, [])))

    def test_native_registration_and_menu_roundtrip(self):
        self.assertEqual(self.registrations, [(self.client.get_unique_name(), "/StatusNotifierItem")])
        self.changed.assert_called_with(True)
        result = self.call("/Menu", "com.canonical.dbusmenu", "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
        self.assertEqual(result.unpack()[1][2][0][1]["label"], "Abrir Tailscale GUI")
        result = self.call("/StatusNotifierItem", "org.freedesktop.DBus.Properties", "GetAll",
                            GLib.Variant("(s)", ("org.kde.StatusNotifierItem",)))
        self.assertEqual(result.unpack()[0]["IconName"], "tailscale-gui-tray")

    def test_activate_and_quit_event(self):
        self.call("/StatusNotifierItem", "org.kde.StatusNotifierItem", "Activate", GLib.Variant("(ii)", (0, 0)))
        self.pump(lambda: self.show.called)
        self.call("/Menu", "com.canonical.dbusmenu", "Event", GLib.Variant("(isvu)", (4, "clicked", GLib.Variant("s", ""), 0)))
        self.pump(lambda: self.quit.called)

    def test_host_loss_and_recovery(self):
        self.set_host(False)
        self.pump(lambda: not self.tray.available)
        self.changed.assert_called_with(False)
        self.set_host(True)
        self.pump(lambda: self.tray.available)
        self.changed.assert_called_with(True)
        self.assertEqual(len(self.registrations), 1)

    def test_watcher_restart_reregisters_item(self):
        self.host.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReleaseName",
                            GLib.Variant("(s)", (StatusNotifierTray.WATCHER,)), None, Gio.DBusCallFlags.NONE, 1000, None)
        self.pump(lambda: not self.tray.available)
        self.host.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                            GLib.Variant("(su)", (StatusNotifierTray.WATCHER, 0)), None, Gio.DBusCallFlags.NONE, 1000, None)
        self.pump(lambda: self.tray.available)
        self.assertEqual(len(self.registrations), 2)

    def test_no_host_means_not_available(self):
        self.set_host(False)
        self.pump(lambda: not self.tray.available)
        self.assertFalse(self.tray.available)

    def test_host_appearing_via_signal_without_property_notification(self):
        self.set_host(False)
        self.pump(lambda: not self.tray.available)
        self.tray.stop()
        tray = StatusNotifierTray(self.client, self.show, self.quit, self.changed, "/tmp/icons")
        self.addCleanup(tray.stop)
        tray.start()
        self.pump(lambda: tray._proxy is not None)
        self.assertFalse(tray.available)
        self.assertEqual(len(self.registrations), 1)
        self.host_present = True
        self.host.emit_signal(None, "/StatusNotifierWatcher", StatusNotifierTray.WATCHER, "StatusNotifierHostRegistered", None)
        self.pump(lambda: tray.available)
        self.assertEqual(len(self.registrations), 2)

    def test_menu_group_properties_and_event_errors(self):
        result = self.call("/Menu", "com.canonical.dbusmenu", "GetGroupProperties", GLib.Variant("(aias)", ([1, 4], ["label"])))
        self.assertEqual([item[1]["label"] for item in result.unpack()[0]], ["Abrir Tailscale GUI", "Sair"])
        result = self.call("/Menu", "com.canonical.dbusmenu", "EventGroup",
                            GLib.Variant("(a(isvu))", ([(99, "clicked", GLib.Variant("s", ""), 0)],)))
        self.assertEqual(result.unpack()[0], [99])

    def test_cached_menu_label_follows_connection_changes(self):
        # Como o GNOME, o host carrega os rótulos uma vez e depois pede só
        # type/children-display ao receber alterações da estrutura do menu.
        cache = {item: props for item, props in self.call(
            "/Menu", "com.canonical.dbusmenu", "GetGroupProperties",
            GLib.Variant("(aias)", ([2], ["label"]))).unpack()[0]}
        updates = []

        def changed(_connection, _sender, _path, _interface, signal, parameters):
            if signal == "ItemsPropertiesUpdated":
                properties, _removed = parameters.unpack()
                for item, props in properties:
                    cache.setdefault(item, {}).update(props)
                updates.append(cache[2]["label"])

        subscription = self.reader.signal_subscribe(
            self.client.get_unique_name(), "com.canonical.dbusmenu", None, "/Menu", None,
            Gio.DBusSignalFlags.NONE, changed)
        self.addCleanup(self.reader.signal_unsubscribe, subscription)
        # Aguarde uma resposta pelo mesmo cliente para garantir que a regra de
        # assinatura já chegou ao barramento antes da primeira alteração.
        self.call("/Menu", "com.canonical.dbusmenu", "GetProperty", GLib.Variant("(is)", (2, "label")))
        for state in ("Desconectado", "Conectado", "Desconectado", "Conectado"):
            with self.subTest(state=state):
                count = len(updates)
                self.tray.update(state)
                self.pump(lambda: len(updates) > count)
                self.assertEqual(cache[2]["label"], state)
                # Uma leitura parcial da estrutura não precisa refazer o rótulo.
                layout = self.call("/Menu", "com.canonical.dbusmenu", "GetLayout",
                                   GLib.Variant("(iias)", (0, -1, ["type", "children-display"])))
                self.assertNotIn("label", layout.unpack()[1][2][1][1])
                tooltip = self.call("/StatusNotifierItem", "org.freedesktop.DBus.Properties", "Get",
                                    GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "ToolTip")))
                self.assertEqual(tooltip.unpack()[0][3], state)


if __name__ == "__main__":
    unittest.main()
