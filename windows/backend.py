"""Integração com tailscale.exe, independente de Qt e de GTK."""
from dataclasses import dataclass
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse


STATE_LABELS = {
    "Running": "Conectado", "Stopped": "Desconectado", "NeedsLogin": "Login necessário",
    "NeedsMachineAuth": "Aguardando aprovação", "Starting": "Conectando", "NoState": "Inicializando",
}
PREF_KEYS = ("RouteAll", "RunSSH", "ShieldsUp", "ExitNodeID", "ExitNodeIP")


@dataclass(frozen=True)
class Result:
    stdout: str = ""
    stderr: str = ""
    code: int = 0


@dataclass(frozen=True)
class Snapshot:
    data: dict | None = None
    prefs: dict | None = None
    error: str = ""

    @property
    def backend(self):
        return (self.data or {}).get("BackendState", "Unknown")

    @property
    def label(self):
        return STATE_LABELS.get(self.backend, "Indisponível")


def parse_object(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else None
    except (ValueError, TypeError):
        return None


def find_tailscale():
    # O instalador oficial nem sempre acrescenta o executável ao PATH.
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        if directory := os.environ.get(variable):
            candidate = ntpath.join(directory, "Tailscale", "tailscale.exe")
            if Path(candidate).is_file():
                return candidate
    return shutil.which("tailscale.exe")


def login_url(value):
    for url in re.findall(r"https?://[^\s<>\"']+", value or ""):
        url = url.rstrip(".,)")
        try:
            parsed = urlparse(url)
            if parsed.hostname and not parsed.username and not parsed.password:
                return url
        except ValueError:
            pass
    return ""


def peers(data):
    mapping = (data or {}).get("Peer") or {}
    if not isinstance(mapping, dict):
        return []
    return sorted((peer for peer in mapping.values() if isinstance(peer, dict)),
                  key=lambda p: (not p.get("Online"), str(p.get("HostName") or "").casefold(), node_ip(p)))


def node_ip(node):
    addresses = node.get("TailscaleIPs") or []
    return next((address for address in addresses if ":" not in address), addresses[0] if addresses else "")


def exit_choices(snapshot):
    choices = [("Nenhum (desabilitar)", "")]
    prefs = snapshot.prefs or {}
    selected = ""
    saved_id = prefs.get("ExitNodeID")
    for peer in peers(snapshot.data):
        ip = node_ip(peer)
        if not ip or not (peer.get("ExitNodeOption") or peer.get("ExitNode")):
            continue
        if ip not in [value for _, value in choices]:
            name = peer.get("HostName") or ip
            choices.append((f"{name} · {ip}" + (" · offline" if not peer.get("Online") else ""), ip))
        if peer.get("ExitNode") or (saved_id and saved_id == peer.get("ID")):
            selected = ip
    if not selected and (saved_id or prefs.get("ExitNodeIP")):
        selected = prefs.get("ExitNodeIP") or node_ip((snapshot.data or {}).get("ExitNodeStatus") or {}) or None
        if selected not in [value for _, value in choices]:
            choices.append((f"{selected} · indisponível" if selected else "Exit Node configurado · indisponível", selected))
    return choices, selected


def permission_error(result):
    text = (result.stdout + "\n" + result.stderr).casefold()
    return result.code != 0 and any(token in text for token in
                                    ("access denied", "access is denied", "permission denied", "administrator", "acesso negado"))


class Preferences:
    def __init__(self, path=None):
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        self.path = Path(path) if path else root / "TailscaleGUI" / "settings.json"
        self.minimize_to_tray = False
        try:
            data = parse_object(self.path.read_text(encoding="utf-8")) or {}
            self.minimize_to_tray = data.get("minimize_to_tray") is True
        except (OSError, ValueError):
            pass

    def save(self, enabled):
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


class TailscaleClient:
    def __init__(self, executable=None):
        self.executable = executable
        self.cancelled = threading.Event()

    def run(self, *arguments, timeout=10, on_output=None):
        executable = self.executable or find_tailscale()
        if not executable:
            return Result(stderr="Cliente Tailscale não encontrado. Instale o Tailscale para Windows e mantenha seu serviço em execução.", code=127)
        if self.cancelled.is_set():
            return Result(stderr="Operação cancelada.", code=130)
        command = [executable, *arguments]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        try:
            if on_output:
                return self._stream(command, timeout, on_output, options)
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout, **options)
            return Result(result.stdout.strip(), result.stderr.strip(), result.returncode)
        except subprocess.TimeoutExpired as error:
            def decode(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            return Result(decode(error.stdout), decode(error.stderr) + "\nTempo limite ao consultar o Tailscale.", 124)
        except OSError as error:
            return Result(stderr=f"Não foi possível executar tailscale.exe: {error}", code=1)

    def _stream(self, command, timeout, on_output, options):
        lines = []
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", bufsize=1, **options) as process:
            def read():
                for line in process.stdout:
                    lines.append(line)
                    on_output(line.rstrip())
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            deadline = time.monotonic() + timeout
            code = None
            while process.poll() is None:
                if self.cancelled.is_set() or time.monotonic() >= deadline:
                    process.kill()
                    process.wait()
                    code = 130 if self.cancelled.is_set() else 124
                    break
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
            reader.join()
            code = process.returncode if code is None else code
        error = ("Tempo limite. Conclua o login no navegador e atualize o status." if code == 124
                 else "Operação cancelada." if code == 130 else "")
        return Result("".join(lines).strip(), error, code)

    def fetch(self):
        status = self.run("status", "--json")
        data = parse_object(status.stdout)
        if status.code or data is None:
            return Snapshot(error=status.stderr or "Resposta de status inválida do Tailscale.")
        result = self.run("debug", "prefs")
        prefs = parse_object(result.stdout) if result.code == 0 else None
        if prefs is None:
            return Snapshot(data, None, "Não foi possível ler as preferências: " + (result.stderr or "JSON inválido."))
        # debug prefs inclui chaves privadas em Persist. Não encaminhe esse campo à interface ou ao log.
        return Snapshot(data, {key: prefs[key] for key in PREF_KEYS if key in prefs})

    def change(self, *arguments, on_output=None):
        # Reconectar usa somente 'up', para preservar todas as preferências do usuário.
        if not arguments or arguments[0] not in ("up", "down", "set"):
            return Result(stderr="Operação inválida.", code=2)
        if arguments[0] == "set" and any(arg.startswith("--ssh") for arg in arguments[1:]):
            return Result(stderr="O servidor Tailscale SSH não está disponível no Windows.", code=2)
        result = self.run(*arguments, timeout=120, on_output=on_output)
        if permission_error(result):
            return Result(result.stdout, result.stderr + "\nFeche o gerenciador e abra-o com 'Executar como administrador' para realizar esta alteração.", result.code)
        return result
