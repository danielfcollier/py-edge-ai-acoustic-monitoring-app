#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------
FORCE=false
for arg in "$@"; do
    case "$arg" in
        --force|--skip) FORCE=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Version guard — refuse to build if a .deb for this version already exists
# ---------------------------------------------------------------------------
VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')

if [ -z "$VERSION" ]; then
    echo "Error: could not read version from pyproject.toml" >&2
    exit 1
fi

EXISTING=$(find deb_dist -name "ai-acoustic-monitor_${VERSION}-*.deb" 2>/dev/null | head -1)

if [ -n "$EXISTING" ] && [ "$FORCE" = false ]; then
    echo ""
    echo "  Version $VERSION already built: $EXISTING"
    echo ""
    echo "  Bump the version in pyproject.toml before building:"
    echo "    version = \"$VERSION\"  →  version = \"X.Y.Z\""
    echo ""
    echo "  To rebuild the same version anyway:"
    echo "    ./build_deb.sh --force"
    echo ""
    exit 1
fi

PYVER=${BUILD_PYTHON_VERSION:-$(uv run python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")}
echo "Building version $VERSION (python${PYVER})..."
echo "Cleaning previous builds..."
rm -rf deb_dist dist build *.egg-info

echo "Generating Debian source tree..."
uv run python3 setup.py --command-packages=stdeb.command sdist_dsc

echo "Patching Debian dependencies..."
cd deb_dist/ai-acoustic-monitoring-app-*/

# Python 3.12 removed distutils; ensure setuptools is available at build time
sed -i 's/^Build-Depends:.*/Build-Depends: debhelper (>= 9), dh-python, python3-all, python3-setuptools/' debian/control

# stdeb expands ${python3:Depends} to include vendored packages as system deps,
# which breaks install. Use an explicit python3.x + system audio libs instead.
sed -i "s/^Depends:.*/Depends: \${misc:Depends}, python${PYVER}, libportaudio2, libsndfile1/" debian/control

# Vendored C extensions are compiled for a specific arch; mark as arch-specific
# so dpkg-buildpackage produces _arm64.deb (not _all.deb).
sed -i 's/^Architecture:.*/Architecture: any/' debian/control

echo "Fixed debian/control:"
grep -E "^(Package|Architecture|Depends|Build-Depends):" debian/control
grep -q "^Architecture: any" debian/control || { echo "ERROR: Architecture patch failed"; exit 1; }

echo "Disabling debhelper steps that break on vendored .so files..."
cat >> debian/rules << 'RULES_APPEND'

override_dh_python3:

override_dh_shlibdeps:

override_dh_strip:

override_dh_dwz:
RULES_APPEND

echo "Writing debian/postinst..."
cat > debian/postinst << POSTINST_EOF
#!/bin/sh
set -e
# Vendored C extensions were compiled for Python ${PYVER}; patch the auto-generated shebang.
for cmd in ai-acoustic-monitor-run ai-acoustic-monitor ai-acoustic-monitor-setup-models ai-acoustic-monitor-install-service; do
    if [ -f "/usr/bin/\$cmd" ]; then
        sed -i "1s|^#!/usr/bin/python3\$|#!/usr/bin/python${PYVER}|" "/usr/bin/\$cmd"
    fi
done
#DEBHELPER#
POSTINST_EOF
chmod +x debian/postinst

echo "Compiling .deb package..."
# Use system Python for dpkg-buildpackage — uv-managed Python lacks setuptools at build time
PATH="/usr/bin:$PATH" dpkg-buildpackage -uc -us -b

echo "Verifying package integrity..."
cd ../..
DEB_FILE=$(find deb_dist -name "*.deb" -type f | head -1)

for _cmd in ai-acoustic-monitor-run ai-acoustic-monitor; do
    if ! dpkg -c "$DEB_FILE" | grep "usr/bin/$_cmd" | grep -q .; then
        echo "Error: CLI entry point '$_cmd' missing from .deb!" >&2
        exit 1
    fi
done

if ! dpkg -c "$DEB_FILE" | grep "usr/lib/ai-acoustic-monitor/setup" | grep -q .; then
    echo "Error: Service templates missing from /usr/lib/ai-acoustic-monitor/setup!" >&2
    exit 1
fi

if ! dpkg -c "$DEB_FILE" | grep "usr/lib/ai-acoustic-monitor/profiles" | grep -q .; then
    echo "Error: Policy profiles missing from /usr/lib/ai-acoustic-monitor/profiles!" >&2
    exit 1
fi

echo "Integrity check passed."
echo ""
echo "Build successful: $(realpath "$DEB_FILE")"
