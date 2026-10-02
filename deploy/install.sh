#!/bin/sh
# Install or update the web app on the Red Pitaya. Run as root from the
# project folder:  sh deploy/install.sh
# Saved measurements in /var/lib/spectro are kept on updates.
set -e
cd "$(dirname "$0")/.."

# Red Pitaya OS may mount the SD card read-only.
if ! touch /opt/.spectro-write-test 2>/dev/null; then
    echo "Root filesystem is read-only, remounting read-write"
    mount -o remount,rw /
fi
rm -f /opt/.spectro-write-test

mkdir -p /opt/spectro /var/lib/spectro
cp server.py measurements.py /opt/spectro/
rm -rf /opt/spectro/static
cp -r static /opt/spectro/static

cp deploy/spectro.service /etc/systemd/system/spectro.service
systemctl daemon-reload
systemctl enable spectro
systemctl restart spectro

# Optional: tile in the Red Pitaya app menu that opens the web app.
# /opt/redpitaya is a separate, read-only partition: make it writable just
# for the copy, then restore it.
if [ -d /opt/redpitaya/www/apps ]; then
    if ! touch /opt/redpitaya/.spectro-write-test 2>/dev/null; then
        mount -o remount,rw /opt/redpitaya
        REMOUNT_RO=1
    fi
    rm -f /opt/redpitaya/.spectro-write-test
    rm -rf /opt/redpitaya/www/apps/spectro
    cp -r deploy/rp_app/spectro /opt/redpitaya/www/apps/spectro
    sync
    if [ -n "$REMOUNT_RO" ]; then
        mount -o remount,ro /opt/redpitaya
    fi
fi

echo "Done. Open http://<red-pitaya-address>:8000"
