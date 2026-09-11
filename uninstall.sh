#!/usr/bin/env bash

set -eu

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data_home/miller-columns"
desktop_dir="$data_home/applications"
desktop_file="$desktop_dir/miller-columns.desktop"
previous_handler_file="$app_dir/previous-directory-handler"

printf 'Uninstall Miller Columns from %s? [y/N] ' "$app_dir"
read -r reply
case "$reply" in
    y|Y|yes|YES)
        ;;
    *)
        printf '%s\n' "Uninstallation cancelled."
        exit 0
        ;;
esac

current_handler=""
if command -v xdg-mime >/dev/null 2>&1; then
    current_handler=$(xdg-mime query default inode/directory 2>/dev/null || true)
fi

if [ "$current_handler" = "miller-columns.desktop" ]; then
    if [ -s "$previous_handler_file" ]; then
        previous_handler=$(sed -n '1p' "$previous_handler_file")
        xdg-mime default "$previous_handler" inode/directory
        printf 'Restored default directory handler: %s\n' "$previous_handler"
    else
        printf '%s\n' \
            "Warning: no previous directory handler was recorded." \
            "Choose another default file manager after uninstalling." >&2
    fi
fi

if [ -f "$desktop_file" ]; then
    rm "$desktop_file"
fi
if [ -d "$app_dir" ]; then
    rm -r "$app_dir"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_dir"
fi

printf '%s\n' "Miller Columns was removed."
