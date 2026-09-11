#!/usr/bin/env bash

set -eu

usage() {
    printf '%s\n' \
        "Usage: ./install.sh [--default]" \
        "" \
        "Installs Miller Columns for the current user." \
        "  --default  Also make it the default inode/directory handler."
}

set_default=false
for argument in "$@"; do
    case "$argument" in
        --default)
            set_default=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n\n' "$argument" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "Error: python3 is required." >&2
    exit 1
fi
python_path=$(command -v python3)

if ! "$python_path" -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('GdkPixbuf', '2.0'); from gi.repository import GdkPixbuf, Gio, Gtk" >/dev/null 2>&1; then
    printf '%s\n' \
        "Error: GTK 3 Python introspection bindings are required." \
        "Install Python 3, PyGObject, GTK 3 and GdkPixbuf using your distribution's package manager." >&2
    exit 1
fi

if "$set_default" && ! command -v xdg-mime >/dev/null 2>&1; then
    printf '%s\n' "Error: xdg-mime is required for --default." >&2
    exit 1
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data_home/miller-columns"
desktop_dir="$data_home/applications"
desktop_file="$desktop_dir/miller-columns.desktop"
previous_handler_file="$app_dir/previous-directory-handler"

mkdir -p "$app_dir/assets" "$desktop_dir"
cp "$script_dir/nemo_miller_columns.py" "$app_dir/nemo_miller_columns.py"
cp "$script_dir/assets/unsupported-preview-original.png" \
    "$app_dir/assets/unsupported-preview-original.png"
chmod 755 "$app_dir/nemo_miller_columns.py"

# Escape backslashes and quotes for a quoted Desktop Entry Exec argument.
escaped_app_path=$(printf '%s' "$app_dir/nemo_miller_columns.py" | \
    sed 's/\\/\\\\/g; s/"/\\"/g')

cat > "$desktop_file" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Miller Columns
GenericName=File Manager
Comment=Keyboard-first Miller columns file manager
Exec=$python_path "$escaped_app_path" %U
TryExec=$python_path
Icon=view-column-symbolic
Terminal=false
Categories=System;FileTools;FileManager;
Keywords=file;manager;miller;columns;
MimeType=inode/directory;
StartupNotify=true
EOF
chmod 755 "$desktop_file"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$desktop_file"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_dir"
fi

if "$set_default"; then
    previous_handler=$(xdg-mime query default inode/directory 2>/dev/null || true)
    if [ -n "$previous_handler" ] && \
            [ "$previous_handler" != "miller-columns.desktop" ]; then
        printf '%s\n' "$previous_handler" > "$previous_handler_file"
    fi
    xdg-mime default miller-columns.desktop inode/directory
fi

printf 'Installed Miller Columns in %s\n' "$app_dir"
printf 'Launcher: %s\n' "$desktop_file"
if "$set_default"; then
    printf '%s\n' "Default directory handler: miller-columns.desktop"
else
    printf '%s\n' "The default directory handler was not changed."
fi
