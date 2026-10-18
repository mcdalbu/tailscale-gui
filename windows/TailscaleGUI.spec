# PyInstaller: gerar exclusivamente em um host Windows x64.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / "windows" / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "tailscale-gui.ico"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gi", "tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="TailscaleGUI", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    icon=str(root / "tailscale-gui.ico"),
    version=str(root / "windows" / "version_info.txt"),
    uac_admin=False,
)
