#!/bin/bash -e

install -d "${ROOTFS_DIR}/usr/local/lib/whisplay-plus-image"
install -m 0755 ../files/install-whisplay-plus.sh "${ROOTFS_DIR}/usr/local/lib/whisplay-plus-image/install-whisplay-plus.sh"
install -m 0755 ../files/install-sugar-wifi-conf.sh "${ROOTFS_DIR}/usr/local/lib/whisplay-plus-image/install-sugar-wifi-conf.sh"
install -D -m 0755 ../files/expand-rootfs-firstboot.sh "${ROOTFS_DIR}/usr/local/sbin/whisplay-expand-rootfs"
install -D -m 0644 ../files/whisplay-expand-rootfs.service "${ROOTFS_DIR}/etc/systemd/system/whisplay-expand-rootfs.service"
install -m 0755 ../files/provision-image.sh "${ROOTFS_DIR}/usr/local/lib/whisplay-plus-image/provision-image.sh"
