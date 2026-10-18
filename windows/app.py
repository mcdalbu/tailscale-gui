"""Interface Windows com tray nativo. Os comandos nunca executam na thread da UI."""
import argparse
import ctypes
import hashlib
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, QSignalBlocker, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPushButton, QPlainTextEdit, QScrollArea, QStackedWidget, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from windows import VERSION
from windows.backend import Preferences, Result, Snapshot, TailscaleClient, exit_choices, login_url, node_ip, peers


def resource(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / name


class JobSignals(QObject):
    result = Signal(object)
    line = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = JobSignals()

    def run(self):
        try:
            self.signals.result.emit(self.function(self.signals.line.emit))
        except Exception as error:
            # Não encaminhe variáveis locais de debug prefs a um traceback no log.
            self.signals.result.emit(RuntimeError(str(error)))
        finally:
            self.signals.finished.emit()


class Window(QMainWindow):
    def __init__(self, client=None, preferences=None, autorefresh=True, enable_tray=True):
        super().__init__()
        self.client = client or TailscaleClient()
        self.preferences = preferences or Preferences()
        self.snapshot = Snapshot()
        self.busy = False
        self.command_running = False
        self.refreshing = False
        self.refresh_pending = False
        self.generation = 0
        self.closed = False
        self.quitting = False
        self.exit_dirty = False
        self.reset_exit = False
        self.auth_link = ""
        self._jobs = []
        self._tray_enabled = enable_tray
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.setWindowTitle("Tailscale GUI")
        self.setWindowIcon(QIcon(str(resource("tailscale-gui.ico"))))
        self.resize(980, 740)
        self.setMinimumSize(760, 540)
        self._build_ui()
        self._build_tray()
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self._sync_desktop()
        self.render(Snapshot())
        if autorefresh:
            self.timer.start()
            QTimer.singleShot(0, self.refresh)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        image = QLabel()
        image.setPixmap(self.windowIcon().pixmap(40, 40))
        header.addWidget(image)
        titles = QVBoxLayout()
        title = QLabel("Tailscale GUI")
        title.setStyleSheet("font-size: 19px; font-weight: 600;")
        titles.addWidget(title)
        titles.addWidget(QLabel("Gerenciador de rede privada"))
        header.addLayout(titles)
        header.addStretch()
        self.refresh_button = QPushButton("Atualizar")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        body = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.addItems(["Status", "Dispositivos", "Configurações"])
        self.navigation.setFixedWidth(170)
        body.addWidget(self.navigation)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        layout.addLayout(body, 1)
        self._status_ui()
        self._peers_ui()
        self._settings_ui()
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        quit_action = QAction("Sair", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.quit_app)
        self.addAction(quit_action)
        self.menuBar().addMenu("Aplicativo").addAction(quit_action)
        self.statusBar().showMessage(f"Windows · Versão {VERSION}")

    def _page(self):
        contents = QWidget()
        layout = QVBoxLayout(contents)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 8, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(contents)
        self.stack.addWidget(scroll)
        return layout

    def _status_ui(self):
        layout = self._page()
        box = QGroupBox("Conexão")
        form = QFormLayout(box)
        form.setVerticalSpacing(16)
        self.details = {}
        for key, title in (("state", "Estado"), ("ip", "IP Tailscale"), ("hostname", "Hostname"),
                           ("relay", "Relay (DERP)"), ("network", "Rede")):
            value = QLabel("—")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            form.addRow(title, value)
            self.details[key] = value
        self.details["state"].setStyleSheet("font-weight: 600;")
        layout.addWidget(box)
        controls = QHBoxLayout()
        self.up_button = QPushButton("Conectar")
        self.up_button.clicked.connect(lambda: self.change("up"))
        self.down_button = QPushButton("Desconectar")
        self.down_button.clicked.connect(lambda: self.change("down"))
        controls.addWidget(self.up_button)
        controls.addWidget(self.down_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)
        self.login_button = QPushButton("Abrir página de autenticação")
        self.login_button.clicked.connect(self.open_login)
        self.login_button.hide()
        layout.addWidget(self.login_button)
        layout.addWidget(QLabel("Log de operações"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(120)
        layout.addWidget(self.log, 1)

    def _peers_ui(self):
        layout = self._page()
        layout.addWidget(QLabel("Dispositivos do seu Tailnet"))
        self.peer_table = QTableWidget(0, 5)
        self.peer_table.setHorizontalHeaderLabels(["Dispositivo", "IP", "Estado", "Sistema", "Exit Node"])
        self.peer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.peer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.peer_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.peer_table.horizontalHeader().setStretchLastSection(True)
        self.peer_table.setMinimumHeight(280)
        layout.addWidget(self.peer_table, 1)
        self.peer_message = QLabel()
        self.peer_message.setWordWrap(True)
        layout.addWidget(self.peer_message)

    def _settings_ui(self):
        layout = self._page()
        exit_box = QGroupBox("Exit Node")
        line = QHBoxLayout(exit_box)
        self.exit_combo = QComboBox()
        self.exit_combo.setMinimumContentsLength(18)
        self.exit_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.exit_combo.currentIndexChanged.connect(self._exit_changed)
        line.addWidget(self.exit_combo, 1)
        self.exit_button = QPushButton("Aplicar")
        self.exit_button.clicked.connect(self.apply_exit)
        line.addWidget(self.exit_button)
        layout.addWidget(exit_box)
        options = QGroupBox("Opções da rede")
        items = QVBoxLayout(options)
        self.switches = {}
        for key, flag, label in (("RouteAll", "accept-routes", "Aceitar rotas anunciadas por outros dispositivos"),
                                  ("ShieldsUp", "shields-up", "Shields Up — bloquear conexões de entrada")):
            check = QCheckBox(label)
            check.toggled.connect(lambda enabled, option=flag: self.change("set", f"--{option}={'true' if enabled else 'false'}"))
            items.addWidget(check)
            self.switches[key] = check
        ssh = QLabel("O servidor Tailscale SSH não está disponível no Windows. Você pode usar o OpenSSH do Windows pela rede Tailscale.")
        ssh.setWordWrap(True)
        items.addWidget(ssh)
        layout.addWidget(options)
        self.settings_message = QLabel()
        self.settings_message.setWordWrap(True)
        self.settings_message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.settings_message)
        desktop = QGroupBox("Comportamento do aplicativo")
        form = QVBoxLayout(desktop)
        self.tray_switch = QCheckBox("Minimizar para o tray ao fechar")
        self.tray_switch.toggled.connect(self.save_tray_preference)
        form.addWidget(self.tray_switch)
        self.tray_help = QLabel("Use Sair no tray ou Ctrl+Q para encerrar o aplicativo.")
        self.tray_help.setWordWrap(True)
        form.addWidget(self.tray_help)
        layout.addWidget(desktop)
        about = QLabel(f"Tailscale GUI {VERSION} para Windows\nInterface independente para o cliente oficial Tailscale.")
        about.setWordWrap(True)
        layout.addWidget(about)
        layout.addStretch()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_menu = QMenu(self)
        self.show_action = self.tray_menu.addAction("Abrir Tailscale GUI", self.show_window)
        self.state_action = self.tray_menu.addAction("Status indisponível")
        self.state_action.setEnabled(False)
        self.tray_menu.addSeparator()
        self.tray_up = self.tray_menu.addAction("Conectar", lambda: self.change("up"))
        self.tray_down = self.tray_menu.addAction("Desconectar", lambda: self.change("down"))
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Sair", self.quit_app)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        if self.tray_available():
            self.tray.show()

    def tray_available(self):
        return self._tray_enabled and QSystemTrayIcon.isSystemTrayAvailable()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _sync_desktop(self):
        available = self.tray_available()
        with QSignalBlocker(self.tray_switch):
            self.tray_switch.setChecked(self.preferences.minimize_to_tray)
            self.tray_switch.setEnabled(available)
        if not available:
            self.tray_help.setText("A bandeja do sistema não está disponível nesta sessão. Fechar a janela encerra o app.")
            if self.isHidden() and self.preferences.minimize_to_tray and not self.quitting:
                self.show_window()
        else:
            self.tray_help.setText("Use Sair no tray ou Ctrl+Q para encerrar o aplicativo.")

    def save_tray_preference(self, enabled):
        try:
            self.preferences.save(enabled)
            self._sync_desktop()
        except OSError as error:
            self._sync_desktop()
            self.tray_help.setText(f"Não foi possível salvar a preferência: {error}")

    def _exit_changed(self):
        self.exit_dirty = True
        self._controls()

    def apply_exit(self):
        value = self.exit_combo.currentData()
        if value is not None:
            self.change("set", f"--exit-node={value}")

    def _settings(self, snapshot, force_exit=False):
        pending = self.exit_combo.currentData()
        choices, current = exit_choices(snapshot)
        actual = [(self.exit_combo.itemText(i), self.exit_combo.itemData(i)) for i in range(self.exit_combo.count())]
        with QSignalBlocker(self.exit_combo):
            if actual != choices:
                self.exit_combo.clear()
                for label, value in choices:
                    self.exit_combo.addItem(label, value)
            values = [value for _, value in choices]
            if force_exit or pending not in values:
                self.exit_dirty = False
            chosen = pending if self.exit_dirty else current
            self.exit_combo.setCurrentIndex(values.index(chosen) if chosen in values else 0)
        for key, check in self.switches.items():
            with QSignalBlocker(check):
                check.setChecked((snapshot.prefs or {}).get(key) is True)
        self.settings_message.setText(snapshot.error)

    def _controls(self):
        backend = self.snapshot.backend
        can_up = not self.busy and backend in ("Stopped", "NeedsLogin", "NeedsMachineAuth")
        can_down = not self.busy and backend in ("Running", "Starting", "NeedsMachineAuth")
        for widget in (self.up_button, self.tray_up):
            widget.setEnabled(can_up)
        for widget in (self.down_button, self.tray_down):
            widget.setEnabled(can_down)
        available = not self.busy and self.snapshot.data is not None and self.snapshot.prefs is not None
        self.exit_combo.setEnabled(available)
        self.exit_button.setEnabled(available and self.exit_combo.currentData() is not None)
        for key, check in self.switches.items():
            check.setEnabled(available and isinstance((self.snapshot.prefs or {}).get(key), bool))
        self.refresh_button.setEnabled(not self.refreshing and not self.command_running)

    def render(self, snapshot, force_exit=False):
        self.snapshot = snapshot
        self.details["state"].setText(snapshot.label)
        # Atualize o menu e o tooltip diretamente a partir do snapshot, mesmo oculto.
        self.state_action.setText(snapshot.label)
        self.tray.setToolTip("Tailscale GUI — " + snapshot.label)
        data = snapshot.data or {}
        node = data.get("Self") or {}
        values = {"ip": node_ip(node), "hostname": node.get("HostName"), "relay": node.get("Relay"),
                  "network": (data.get("CurrentTailnet") or {}).get("Name") or data.get("MagicDNSSuffix")}
        for key, value in values.items():
            self.details[key].setText(value or "—")
        messages = [snapshot.error] if snapshot.error else []
        if snapshot.backend == "NeedsMachineAuth":
            messages.append("Aprove este dispositivo no console de administração do Tailnet.")
        messages.extend(data.get("Health") or [])
        self.message.setText("\n".join(messages))
        if snapshot.backend in ("Running", "Stopped", "Unknown"):
            self.auth_link = ""
        url = login_url(data.get("AuthURL"))
        if url:
            self.auth_link = url
        self.login_button.setVisible(bool(self.auth_link))
        devices = peers(snapshot.data)
        self.peer_table.setRowCount(len(devices))
        for row, peer in enumerate(devices):
            fields = [peer.get("HostName") or "Dispositivo sem nome", node_ip(peer) or "—",
                      "Online" if peer.get("Online") else "Offline", peer.get("OS") or "—",
                      "Ativo" if peer.get("ExitNode") else "Disponível" if peer.get("ExitNodeOption") else "—"]
            for column, value in enumerate(fields):
                self.peer_table.setItem(row, column, QTableWidgetItem(value))
        self.peer_message.setText("Status indisponível. Verifique o cliente e o serviço Tailscale." if snapshot.data is None
                                  else "Nenhum dispositivo encontrado." if not devices
                                  else "Último estado conhecido; o cliente local está desconectado." if snapshot.backend != "Running" else "")
        self._settings(snapshot, force_exit)
        self._controls()

    def _start_job(self, function, callback, with_lines=False):
        job = Job(function)
        self._jobs.append(job)
        job.signals.result.connect(callback)
        if with_lines:
            job.signals.line.connect(self.command_line)
        job.signals.finished.connect(lambda: self._jobs.remove(job) if job in self._jobs else None)
        self.pool.start(job)

    def refresh(self):
        if self.closed:
            return
        self._sync_desktop()
        if self.refreshing or self.command_running:
            self.refresh_pending = True
            return
        self.refresh_pending = False
        self.refreshing = True
        generation = self.generation
        self._controls()
        self._start_job(lambda _line: (generation, self.client.fetch()), self._fetched)

    def _fetched(self, value):
        if self.closed:
            return
        self.refreshing = False
        if isinstance(value, Exception):
            value = (self.generation, Snapshot(error=str(value)))
        generation, snapshot = value
        if generation != self.generation or self.command_running:
            self.refresh_pending = True
        else:
            self.busy = False
            self.render(snapshot, force_exit=self.reset_exit)
            self.reset_exit = False
        self._controls()
        if self.refresh_pending and not self.command_running:
            self.refresh()

    def change(self, *arguments):
        if self.closed or self.busy:
            return
        self.generation += 1
        self.busy = self.command_running = True
        self.reset_exit = any(arg.startswith("--exit-node=") for arg in arguments)
        self.log.appendPlainText("→ tailscale " + " ".join(arguments))
        self.statusBar().showMessage("Executando operação…")
        self._controls()
        self._start_job(lambda line: self.client.change(*arguments, on_output=line), self._changed, with_lines=True)

    def command_line(self, text):
        if self.closed:
            return
        self.log.appendPlainText(text)
        if url := login_url(text):
            self.auth_link = url
            self.login_button.show()
            self.navigation.setCurrentRow(0)
            if not self.isVisible():
                self.show_window()

    def open_login(self):
        if self.auth_link and login_url(self.auth_link) == self.auth_link:
            QDesktopServices.openUrl(QUrl(self.auth_link))

    def _changed(self, result):
        if self.closed:
            return
        self.command_running = False
        if isinstance(result, Exception):
            result = Result(stderr=str(result), code=1)
        message = "Operação concluída." if result.code == 0 else f"Operação falhou (código {result.code})."
        self.log.appendPlainText("← " + message)
        if result.stderr:
            self.log.appendPlainText(result.stderr)
        self.statusBar().showMessage(message, 10000)
        if result.code:
            self._settings(self.snapshot, force_exit=self.reset_exit)
            if not self.isVisible():
                self.show_window()
            self.navigation.setCurrentRow(0)
        self.refresh()

    def closeEvent(self, event):
        if not self.quitting and self.preferences.minimize_to_tray and self.tray_available():
            self.hide()
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def shutdown(self):
        self.closed = True
        self.quitting = True
        self.timer.stop()
        self.client.cancelled.set()
        self.tray.hide()

    def quit_app(self):
        self.shutdown()
        self.close()
        QApplication.instance().quit()


def serve_single_instance(window):
    identity = hashlib.sha256(str(window.preferences.path).casefold().encode()).hexdigest()[:16]
    name = "TailscaleGUI-Windows-" + identity
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return None
    server = QLocalServer(window)
    if not server.listen(name):
        raise RuntimeError("Não foi possível iniciar a instância do aplicativo. Feche as outras instâncias e tente novamente.")
    def connected():
        while connection := server.nextPendingConnection():
            connection.disconnectFromServer()
            connection.deleteLater()
        window.show_window()
    server.newConnection.connect(connected)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32" and not args.smoke_test:
        raise SystemExit("Este executável é para Windows. No Linux, execute tailscale-gui.py.")
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("io.tailscale.TailscaleGUI")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Tailscale GUI")
    app.setOrganizationName("TailscaleGUI")
    app.setApplicationVersion(VERSION)
    window = Window(autorefresh=not args.smoke_test, enable_tray=not args.smoke_test)
    if args.smoke_test:
        window.render(Snapshot({"BackendState": "Running", "Self": {"HostName": "Teste Windows", "TailscaleIPs": ["100.64.0.1"]}},
                               {"RouteAll": True, "ShieldsUp": False}))
        if window.windowIcon().isNull() or window.state_action.text() != "Conectado":
            return 2
        window.show()
        QTimer.singleShot(250, window.quit_app)
        return app.exec()
    try:
        server = serve_single_instance(window)
    except RuntimeError as error:
        QMessageBox.critical(window, "Tailscale GUI", str(error))
        window.shutdown()
        return 1
    if server is None:
        window.shutdown()
        return 0
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec()
