#!/usr/bin/env bash
# build-rpm.sh — Empacota o tailscale-gui como .rpm
set -euo pipefail

NAME="tailscale-gui"
SRCDIR="$(cd "$(dirname "$0")" && pwd)"
VERSION=$(awk '/^Version:/ {print $2}' "$SRCDIR/$NAME.spec")
command -v rpmbuild >/dev/null || { echo "Instale rpm-build para gerar o RPM." >&2; exit 1; }
TASK_BUILDDIR=$(mktemp -d "/tmp/rpmbuild-$NAME.XXXXXX")
TASK_SOURCE_DIR=$(mktemp -d "/tmp/source-$NAME.XXXXXX")
trap 'rm -rf "$TASK_BUILDDIR" "$TASK_SOURCE_DIR"' EXIT

echo "==> Criando estrutura rpmbuild..."
mkdir -p "$TASK_BUILDDIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,tmp}

echo "==> Criando tarball do código-fonte..."
mkdir -p "$TASK_SOURCE_DIR/$NAME-$VERSION"
cp "$SRCDIR/tailscale-gui.py" "$SRCDIR/tailscale_tray.py" "$SRCDIR/tailscale-gui.desktop" \
   "$SRCDIR/tailscale-gui.png" "$SRCDIR/tailscale-gui.svg" "$SRCDIR/tailscale-gui-tray.svg" \
   "$SRCDIR/README.md" "$TASK_SOURCE_DIR/$NAME-$VERSION/"
mkdir -p "$TASK_SOURCE_DIR/$NAME-$VERSION/tests"
cp "$SRCDIR"/tests/*.py "$TASK_SOURCE_DIR/$NAME-$VERSION/tests/"
cp -r "$SRCDIR/icons" "$TASK_SOURCE_DIR/$NAME-$VERSION/"

tar -czf "$TASK_BUILDDIR/SOURCES/$NAME-$VERSION.tar.gz" \
    -C "$TASK_SOURCE_DIR" "$NAME-$VERSION"

echo "==> Copiando spec..."
cp "$SRCDIR/tailscale-gui.spec" "$TASK_BUILDDIR/SPECS/"

echo "==> Buildando RPM..."
rpmbuild \
    --define "_topdir $TASK_BUILDDIR" \
    --define "_tmppath $TASK_BUILDDIR/tmp" \
    -ba "$TASK_BUILDDIR/SPECS/$NAME.spec"

echo ""
echo "==> RPM gerado:"
find "$TASK_BUILDDIR/RPMS" -name "*.rpm"

# Copiar o RPM para o diretório do projeto
find "$TASK_BUILDDIR/RPMS" -name "*.rpm" -exec cp {} "$SRCDIR/" \;
echo ""
echo "==> Copiado para: $SRCDIR"
echo "==> Para instalar:  sudo dnf install ./$NAME-$VERSION-*.rpm"
