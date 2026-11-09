"""Tray GTK4 via StatusNotifierItem/DBusMenu, sem depender de widgets GTK3.

Protocolos: https://api.kde.org/kstatusnotifieritem.html
           https://github.com/AyatanaIndicators/libdbusmenu
"""
import json
import os
import tempfile
from pathlib import Path

from gi.repository import Gio, GLib


class DesktopPreferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(GLib.get_user_config_dir()) / "tailscale-gui" / "settings.json"
        self.minimize_to_tray = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.minimize_to_tray = data.get("minimize_to_tray") is True
        except (OSError, ValueError):
            pass

    def save(self, enabled):
        """Só atualiza o valor em memória após uma gravação atômica bem-sucedida."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".settings-", delete=False) as output:
                temporary = output.name
                json.dump({"minimize_to_tray": bool(enabled)}, output)
                output.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        self.minimize_to_tray = bool(enabled)


ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionMovieName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ProvideXdgActivationToken"><arg type="s" direction="in"/></method>
  <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/>
  <signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
    <arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
  <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{sv})" direction="out"/></method>
  <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="in"/><arg type="u" direction="in"/></method>
  <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
</interface></node>"""


class StatusNotifierTray:
    WATCHER = "org.kde.StatusNotifierWatcher"
    ITEM_PATH = "/StatusNotifierItem"
    MENU_PATH = "/Menu"

    def __init__(self, connection, show_window, quit_app, availability_changed, icon_dir, command=None):
        self.connection = connection
        self.show_window = show_window
        self.quit_app = quit_app
        self.availability_changed = availability_changed
        self.icon_dir = icon_dir
        self.command = command
        self._can_up = self._can_down = False
        self.available = False
        self._stopped = False
        self._generation = 0
        self._proxy = None
        self._registering = False
        self._registered = False
        self._host_present = False
        self._objects = []
        self._watch_id = None
        self._revision = 1
        self._state = "Status indisponível"

    def start(self):
        if self.connection is None:
            return
        try:
            item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
            menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
            self._objects.append(self.connection.register_object(
                self.ITEM_PATH, item, self._item_method, self._item_property, None))
            self._objects.append(self.connection.register_object(
                self.MENU_PATH, menu, self._menu_method, self._menu_property, None))
            self._watch_id = Gio.bus_watch_name_on_connection(
                self.connection, self.WATCHER, Gio.BusNameWatcherFlags.NONE,
                self._watcher_appeared, self._watcher_vanished)
        except GLib.Error:
            self.stop()

    def _set_available(self, available):
        if available != self.available:
            self.available = available
            self.availability_changed(available)

    def _watcher_appeared(self, connection, name, owner):
        if self._stopped:
            return
        self._generation += 1
        generation = self._generation

        def ready(_source, result):
            try:
                proxy = Gio.DBusProxy.new_finish(result)
            except GLib.Error:
                return
            if self._stopped or generation != self._generation:
                return
            self._proxy = proxy
            proxy.connect("g-properties-changed", self._watcher_properties)
            proxy.connect("g-signal", self._watcher_signal)
            self._check_host()

        Gio.DBusProxy.new(connection, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                          name, "/StatusNotifierWatcher", name, None, ready)

    def _watcher_vanished(self, _connection, _name):
        self._generation += 1
        self._proxy = None
        self._registered = self._registering = False
        self._host_present = False
        self._set_available(False)

    def _watcher_properties(self, proxy, changed, _invalidated):
        if proxy is self._proxy:
            values = changed.unpack()
            if "IsStatusNotifierHostRegistered" in values:
                self._check_host(host_present=values["IsStatusNotifierHostRegistered"])

    def _watcher_signal(self, proxy, _sender, signal, _parameters):
        if proxy is not self._proxy:
            return
        if signal == "StatusNotifierHostRegistered":
            self._check_host(host_present=True)
        elif signal == "StatusNotifierHostUnregistered":
            self._check_host(host_present=False)

    def _check_host(self, host_present=None):
        if self._stopped or self._proxy is None:
            return
        if host_present is None:
            value = self._proxy.get_cached_property("IsStatusNotifierHostRegistered")
            host_present = value is not None and value.unpack() is True
        self._host_present = bool(host_present)
        if not host_present:
            self._set_available(False)
            return
        if self._registered:
            self._set_available(True)
            return
        if self._registering:
            return
        self._registering = True
        generation = self._generation

        def registered(proxy, result):
            try:
                proxy.call_finish(result)
            except GLib.Error:
                if generation == self._generation:
                    self._registering = False
                return
            if self._stopped or generation != self._generation:
                return
            self._registering = False
            self._registered = True
            self._check_host(host_present=self._host_present)

        # O caminho permite ao watcher identificar o nome único do processo remetente.
        self._proxy.call("RegisterStatusNotifierItem", GLib.Variant("(s)", (self.ITEM_PATH,)),
                         Gio.DBusCallFlags.NONE, 5000, None, registered)

    def _item_property(self, _connection, _sender, _path, _interface, name):
        properties = {
            "Category": ("s", "ApplicationStatus"), "Id": ("s", "tailscale-gui"),
            "Title": ("s", "Tailscale GUI"), "Status": ("s", "Active"),
            "WindowId": ("i", 0), "IconThemePath": ("s", self.icon_dir),
            "Menu": ("o", self.MENU_PATH), "ItemIsMenu": ("b", False),
            "IconName": ("s", "tailscale-gui-tray"), "IconPixmap": ("a(iiay)", []),
            "OverlayIconName": ("s", ""), "OverlayIconPixmap": ("a(iiay)", []),
            "AttentionIconName": ("s", "tailscale-gui-tray"), "AttentionIconPixmap": ("a(iiay)", []),
            "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", ("tailscale-gui-tray", [], "Tailscale GUI", self._state)),
        }
        return GLib.Variant(*properties[name]) if name in properties else None

    def _item_method(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        if method in ("Activate", "SecondaryActivate", "ContextMenu"):
            GLib.idle_add(self._invoke, self.show_window)
        invocation.return_value(None)

    @staticmethod
    def _invoke(callback):
        callback()
        return False

    def _menu_items(self):
        def props(**values):
            return {key: GLib.Variant("b" if isinstance(value, bool) else "s", value)
                    for key, value in values.items()}
        return {
            0: props(**{"children-display": "submenu"}),
            1: props(label="Abrir Tailscale GUI", enabled=True, visible=True),
            2: props(label=self._state, enabled=False, visible=True),
            3: props(type="separator", visible=True),
            4: props(label="Sair", enabled=True, visible=True),
            5: props(label="Conectar", enabled=self._can_up, visible=True),
            6: props(label="Desconectar", enabled=self._can_down, visible=True),
            7: props(type="separator", visible=True),
        }

    def _layout(self, parent, depth, names):
        items = self._menu_items()
        properties = items[parent]
        if names:
            properties = {key: value for key, value in properties.items() if key in names}
        children = []
        if parent == 0 and depth != 0:
            children = [GLib.Variant("(ia{sv}av)", self._layout(item, 0, names)) for item in (1, 2, 3, 5, 6, 7, 4)]
        return parent, properties, children

    def _menu_property(self, _connection, _sender, _path, _interface, name):
        values = {"Version": ("u", 3), "TextDirection": ("s", "ltr"),
                  "Status": ("s", "normal"), "IconThemePath": ("as", [self.icon_dir])}
        return GLib.Variant(*values[name]) if name in values else None

    def _event(self, item, event):
        if item not in self._menu_items():
            return False
        if event == "clicked" and item in (1, 4):
            GLib.idle_add(self._invoke, self.show_window if item == 1 else self.quit_app)
        elif event == "clicked" and item in (5, 6):
            GLib.idle_add(self._connection_event, item)
        return True

    def _connection_event(self, item):
        # Revalide ao executar: outro clique pode ter iniciado uma operação.
        if not self._stopped and self.command and (self._can_up if item == 5 else self._can_down):
            self.command("up" if item == 5 else "down")
        return False

    def set_controls(self, can_up, can_down):
        can_up, can_down = bool(can_up and self.command), bool(can_down and self.command)
        if (can_up, can_down) == (self._can_up, self._can_down):
            return
        self._can_up, self._can_down = can_up, can_down
        if self._objects and not self._stopped:
            updates = [(5, {"enabled": GLib.Variant("b", can_up)}),
                       (6, {"enabled": GLib.Variant("b", can_down)})]
            self.connection.emit_signal(
                None, self.MENU_PATH, "com.canonical.dbusmenu", "ItemsPropertiesUpdated",
                GLib.Variant("(a(ia{sv})a(ias))", (updates, [])))

    def _menu_method(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        items = self._menu_items()
        try:
            if method == "GetLayout":
                parent, depth, names = args
                response = GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout(parent, depth, names)))
            elif method == "GetGroupProperties":
                ids, names = args
                result = [(item, {key: value for key, value in items[item].items() if not names or key in names})
                          for item in (ids or list(items)) if item in items]
                response = GLib.Variant("(a(ia{sv}))", (result,))
            elif method == "GetProperty":
                response = GLib.Variant("(v)", (items[args[0]][args[1]],))
            elif method == "Event":
                if not self._event(args[0], args[1]):
                    raise KeyError(args[0])
                response = None
            elif method == "EventGroup":
                errors = [item for item, event, _data, _time in args[0] if not self._event(item, event)]
                response = GLib.Variant("(ai)", (errors,))
            elif method == "AboutToShow":
                if args[0] not in items:
                    raise KeyError(args[0])
                response = GLib.Variant("(b)", (False,))
            elif method == "AboutToShowGroup":
                response = GLib.Variant("(aiai)", ([], [item for item in args[0] if item not in items]))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            invocation.return_value(response)
        except KeyError:
            invocation.return_dbus_error("com.canonical.dbusmenu.Error.InvalidMenuItem", "Item ou propriedade inexistente")

    def update(self, state):
        if state == self._state:
            return
        self._state = state
        if self._objects and not self._stopped:
            self.connection.emit_signal(None, self.ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)
            tooltip = self._item_property(None, None, None, None, "ToolTip")
            self.connection.emit_signal(
                None, self.ITEM_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", ("org.kde.StatusNotifierItem", {"ToolTip": tooltip}, [])))
            # O rótulo mudou, mas a estrutura não. O GNOME consulta apenas
            # type/children-display em LayoutUpdated e mantém os rótulos em cache.
            # Emitir somente a atualização das propriedades evita esse estado antigo.
            self.connection.emit_signal(
                None, self.MENU_PATH, "com.canonical.dbusmenu", "ItemsPropertiesUpdated",
                GLib.Variant("(a(ia{sv})a(ias))", ([(2, {"label": GLib.Variant("s", state)})], [])))

    def stop(self):
        self._stopped = True
        self._generation += 1
        if self._watch_id is not None:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = None
        for object_id in self._objects:
            self.connection.unregister_object(object_id)
        self._objects.clear()
        self._set_available(False)
