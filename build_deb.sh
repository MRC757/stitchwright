#!/usr/bin/env bash
# Build dist/stitchwright_<version>_amd64.deb, the Ubuntu package (the Linux counterpart
# of build_exe.ps1). Run from the project folder on Ubuntu:   ./build_deb.sh
#
# Needs Python 3.12 or later with Tk (Ubuntu 24.04: sudo apt install python3-venv python3-tk),
# or another Python given as PYTHON=/path/to/python3.12 ./build_deb.sh
# Uses its own virtual environment (.venv-build-linux), so your normal Python is untouched.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "Needs Python 3.12 or later (requirements.txt pins libraries that do); found $("$PYTHON" -V)." >&2
    echo "Give another one with PYTHON=/path/to/python3.12 ./build_deb.sh" >&2
    exit 1
fi
"$PYTHON" -c 'import tkinter' 2>/dev/null || { echo "Tk is missing: sudo apt install python3-tk" >&2; exit 1; }

if [ ! -x .venv-build-linux/bin/python ]; then
    echo "Creating build environment (.venv-build-linux)..."
    "$PYTHON" -m venv .venv-build-linux
fi
py=.venv-build-linux/bin/python

echo "Installing requirements and PyInstaller..."
# requirements.txt and the PyInstaller version are pinned so every build of a release is the same
"$py" -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller==6.22.3 pytest pyflakes

echo "Running tests..."
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xvfb-run >/dev/null; then
    xvfb-run -a "$py" -m pytest tests -q -p no:cacheprovider     # no screen: a virtual one
else
    "$py" -m pytest tests -q -p no:cacheprovider
fi

echo "Building..."
"$py" -m PyInstaller --clean --noconfirm stitchwright.spec

version=$("$py" -c 'import stitchwright; print(stitchwright.__version__)')
pkg=build/deb/stitchwright_${version}_amd64
rm -rf "$pkg"
mkdir -p "$pkg/DEBIAN" "$pkg/opt" "$pkg/usr/bin" "$pkg/usr/share/applications" \
         "$pkg/usr/share/mime/packages" "$pkg/usr/share/doc/stitchwright"
cp -a dist/stitchwright "$pkg/opt/stitchwright"
ln -s /opt/stitchwright/stitchwright "$pkg/usr/bin/stitchwright"
ln -s /opt/stitchwright/stitchwright "$pkg/usr/bin/stitchwright-gui"
cp packaging/linux/stitchwright.desktop "$pkg/usr/share/applications/"
cp packaging/linux/stitchwright-mime.xml "$pkg/usr/share/mime/packages/stitchwright.xml"
cp LICENSE "$pkg/usr/share/doc/stitchwright/copyright"
# the icon in the sizes desktops look for
"$py" - "$pkg" <<'EOF'
import os, sys
from PIL import Image
icon = Image.open("stitchwright/assets/icon.png").convert("RGBA")
for size in (32, 48, 64, 128, 256):
    folder = os.path.join(sys.argv[1], f"usr/share/icons/hicolor/{size}x{size}/apps")
    os.makedirs(folder, exist_ok=True)
    icon.resize((size, size), Image.LANCZOS).save(os.path.join(folder, "stitchwright.png"))
EOF
size=$(du -sk --exclude=DEBIAN "$pkg" | cut -f1)
sed -e "s/@VERSION@/$version/" -e "s/@SIZE@/$size/" packaging/linux/control.in > "$pkg/DEBIAN/control"
chmod -R u=rwX,go=rX "$pkg"
chmod 755 "$pkg/opt/stitchwright/stitchwright"
# Desktop menus, file types and icons are refreshed by dpkg triggers: no install scripts needed.

deb=dist/stitchwright_${version}_amd64.deb
dpkg-deb --root-owner-group -Zxz --build "$pkg" "$deb"
echo "Built $deb ($(du -h "$deb" | cut -f1))"
echo "Install it with:  sudo apt install ./$deb"
