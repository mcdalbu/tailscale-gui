"""python -m windows.build: testa, compila e prepara o pacote portátil Windows."""
import hashlib
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from windows import VERSION

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32":
        raise SystemExit("O executável precisa ser compilado no Windows. Use o workflow Windows no GitHub Actions.")
    subprocess.check_call([sys.executable, "-m", "unittest", "discover", "-s", "windows/tests", "-v"], cwd=ROOT)
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                           str(ROOT / "windows" / "TailscaleGUI.spec")], cwd=ROOT)
    executable = ROOT / "dist" / "TailscaleGUI.exe"
    environment = dict(os.environ)
    environment.pop("QT_QPA_PLATFORM", None)
    # Exercite também o plugin nativo qwindows no .exe, não apenas o modo offscreen dos testes.
    subprocess.run([str(executable), "--smoke-test"], check=True, timeout=90, env=environment)
    output = ROOT / "dist" / "windows"
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"TailscaleGUI-Windows-x64-{VERSION}.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(executable, "TailscaleGUI.exe")
        archive.write(ROOT / "windows" / "README.md", "LEIA-ME.md")
        for license_file in (ROOT / "windows" / "licenses").iterdir():
            if license_file.is_file():
                archive.write(license_file, "licenses/" + license_file.name)
        for name in ("PySide6-Essentials", "shiboken6", "PyInstaller"):
            distribution = importlib.metadata.distribution(name)
            for entry in distribution.files or []:
                if "licenses" in [part.lower() for part in entry.parts] and distribution.locate_file(entry).is_file():
                    archive.write(distribution.locate_file(entry), "licenses/" + name + "/" + "/".join(entry.parts[1:]))
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix(".zip.sha256").write_text(f"{digest}  {package.name}\n", encoding="utf-8")
    print(f"Pacote Windows: {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
