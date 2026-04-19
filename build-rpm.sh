#!/usr/bin/env bash
# build-rpm.sh — Empacota o tailscale-gui como .rpm
set -e

NAME="tailscale-gui"
VERSION="1.0.0"
SRCDIR="$(cd "$(dirname "$0")" && pwd)"
BUILDDIR="/tmp/rpmbuild-$NAME"

echo "==> Criando estrutura rpmbuild..."
rm -rf "$BUILDDIR"
mkdir -p "$BUILDDIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

echo "==> Criando tarball do código-fonte..."
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/$NAME-$VERSION"
cp "$SRCDIR/tailscale-gui.py"      "$TMPDIR/$NAME-$VERSION/"
cp "$SRCDIR/tailscale-gui.desktop" "$TMPDIR/$NAME-$VERSION/"
cp "$SRCDIR/tailscale-gui.png"     "$TMPDIR/$NAME-$VERSION/"

tar -czf "$BUILDDIR/SOURCES/$NAME-$VERSION.tar.gz" \
    -C "$TMPDIR" "$NAME-$VERSION"
rm -rf "$TMPDIR"

echo "==> Copiando spec..."
cp "$SRCDIR/tailscale-gui.spec" "$BUILDDIR/SPECS/"

echo "==> Buildando RPM..."
rpmbuild \
    --define "_topdir $BUILDDIR" \
    -ba "$BUILDDIR/SPECS/$NAME.spec"

echo ""
echo "==> RPM gerado:"
find "$BUILDDIR/RPMS" -name "*.rpm" | tee /dev/stderr

# Copiar o RPM para o diretório do projeto
find "$BUILDDIR/RPMS" -name "*.rpm" -exec cp {} "$SRCDIR/" \;
echo ""
echo "==> Copiado para: $SRCDIR"
echo "==> Para instalar:  sudo dnf install ./$NAME-$VERSION-*.rpm"
