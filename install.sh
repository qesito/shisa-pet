#!/usr/bin/env bash
# Sets up Shisa: checks GTK bindings, builds a virtualenv, links the launcher.
set -euo pipefail
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

if ! python3 -c "import gi, cairo; gi.require_version('Gtk', '3.0')" 2>/dev/null; then
  echo "Missing GTK Python bindings. On Debian/Kali/Ubuntu run:"
  echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-venv"
  exit 1
fi

# --system-site-packages lets the venv see the distro's PyGObject/cairo
python3 -m venv --system-site-packages "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/shisa-pet" "$HOME/.local/bin/shisa-pet"

cat <<'MSG'

Installed! Run:  shisa-pet

If you use picom, add Shisa to these lists in picom.conf so it isn't
blurred, shadowed, rounded or dimmed:

  shadow-exclude           = [ "class_g = 'Shisa-pet'", ... ];
  blur-background-exclude  = [ "class_g = 'Shisa-pet'", ... ];
  rounded-corners-exclude  = [ "class_g = 'Shisa-pet'", ... ];
  opacity-rule             = [ "100:class_g = 'Shisa-pet'", ... ];

Hide/show shortcut for sxhkd (bspwm), then reload with: pkill -USR1 -x sxhkd

  super + shift + s
  	shisa-pet --toggle

To start Shisa at login, add to your bspwmrc (only one Shisa ever runs):

  (sleep 2; shisa-pet) &
MSG
