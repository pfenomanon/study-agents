# USB NVMe Primary + eMMC Secondary Rebuild Run Log

## Metadata
- Date (UTC): 2026-03-27
- Host: study-agents-backend
- Requested target USB disk: `/dev/sda`
- Current root at start: `/dev/mapper/ubuntu--vg-ubuntu--lv` on internal eMMC (`/dev/mmcblk0`)

## Objective
1. Install/deploy backend-vps with primary runtime on USB storage.
2. Rebuild internal eMMC with minimal Ubuntu server as secondary boot option.
3. Record deviations/fixes in real time for doc/script updates.

## Execution Log
- [2026-03-27 01:43:36 UTC] Started USB migration run.
- [2026-03-27 01:43:36 UTC] Confirmed target disk /dev/sda unmounted and existing Windows/BitLocker partitions present.
- [2026-03-27 01:43:36 UTC] Tool check: sgdisk/parted/rsync present; debootstrap missing.
- [2026-03-27 01:43:48 UTC] Installed debootstrap package.
- [2026-03-27 01:43:48 UTC] Minor environment quirk: 'command -v debootstrap' returned empty; using explicit path /usr/sbin/debootstrap.
- [2026-03-27 01:44:32 UTC] Repartitioned /dev/sda (GPT): sda1 EFI 1GiB, sda2 root remainder.
- [2026-03-27 01:44:32 UTC] Observed mkfs.ext4 discard warning: 'Remote I/O error' on USB media; proceeding with validation and nodiscard fallback if needed.
- [2026-03-27 01:44:47 UTC] Starting system clone to /dev/sda2 via rsync.
- [2026-03-27 01:50:03 UTC] Completed rsync clone to USB root.
- [2026-03-27 01:51:53 UTC] USB root fstab rewritten to UUID-based mounts (/, /boot/efi).
- [2026-03-27 01:51:53 UTC] Chrooted into USB root; installed/validated grub-efi + linux-image and generated boot config.
- [2026-03-27 01:52:06 UTC] GRUB installed on USB root; chroot could not write EFI NVRAM (expected in this environment).
- [2026-03-27 01:52:06 UTC] Corrective action: created USB UEFI boot entry from host using efibootmgr.
- [2026-03-27 01:52:29 UTC] Rebooting host to switch primary boot to USB (Boot0001 UbuntuUSB).
- [2026-03-27 02:01:44 UTC] Resumed after disconnect; verified backend-vps and local Supabase containers healthy before cutover.
- [2026-03-27 02:01:44 UTC] Verified current root still internal eMMC (/dev/mmcblk0) and USB root clone present on /dev/sda2 (UUID=7759fec5-0f06-4f05-a1b9-f4679e89203b).
- [2026-03-27 02:01:44 UTC] Verified UEFI entries: Boot0001 UbuntuUSB present and first in BootOrder (0001,0000,0002).
- [2026-03-27 02:01:44 UTC] Proceeding with explicit BootNext=0001 and host reboot for USB cutover validation.
