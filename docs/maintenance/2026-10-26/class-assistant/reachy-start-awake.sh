#!/bin/bash
# Keep the vendor's environment/setup, changing only the boot posture policy.
# The systemd override leaves the installed package untouched across upgrades.
set -e
launcher=/venvs/mini_daemon/lib/python3.12/site-packages/reachy_mini/daemon/app/services/wireless/launcher.sh
exec /bin/bash <(sed 's/--no-wake-up-on-start/--wake-up-on-start/g' "$launcher")
