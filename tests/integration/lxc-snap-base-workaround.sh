#!/bin/bash
#
# There's currently an issue with snap running on 22.04 lxc
# containers on 24.04 systems
#
# This script should be removed when transitioning to 24.04.

echo "WORKAROUND: waiting for machines to start (sleep 30)"
sleep 30

for m in $(lxc list -cn -fcsv); do
        echo "WORKAROUND: applying to $m"
        lxc shell "$m" -- systemctl stop snapd.seeded.service
done

echo "WORKAROUND: completed (sleep 30)"
sleep 30
exit 0
