# OnePlus Nord Camera Phone Setup

This playbook keeps the full recovery/provisioning knowledge but removes plaintext credentials.
Any sensitive values (Wi-Fi password, device unlock PIN, account credentials) must come from the shared vault.

## Phase 1 - Flash / Unbrick Base Firmware (Windows)

1. Enable USB debugging and OEM unlock if the phone is still bootable.
2. Open MSM Download Tool (`Avicii_14_e.13_210204`) as Administrator.
3. Put phone in EDL mode (`adb reboot edl`), quickly move cable to Windows host, and start flash.
4. If Qualcomm device appears with warning icon, disable driver signature enforcement:
   - `Settings -> System -> Recovery -> Advanced startup -> Restart now`
   - `Troubleshoot -> Advanced options -> Startup Settings -> Restart`
   - Press `7`/`F7` to disable driver signature enforcement.
5. Reference guide: [XDA MSM unbrick guide](https://www.xda-developers.com/how-to-unbrick-oneplus-nord-msmdownloadtool/).

## Phase 2 - Android First-Boot Setup

1. Complete initial setup flow in English.
2. Connect to temporary provisioning Wi-Fi from the shared vault.
3. Skip app/data transfer.
4. Sign into the operator Google account from the shared vault.
5. Finish remaining Android prompts (search engine, labs opt-out, update prompts).
6. Set device lock PIN and biometric per vault policy.

## Phase 3 - Root (Bootloader + Magisk)

1. Enable developer options (`Build number` tapped 7 times).
2. Enable `OEM unlocking` and `USB debugging`.
3. Reboot to fastboot (hardware buttons, then advanced fastboot option).
4. Verify host sees the device: `fastboot devices`.
5. Unlock bootloader:
   - `fastboot oem unlock` (or `fastboot flashing unlock`).
6. Disable off-mode charging so the phone **boots when charger power is applied while powered off** (required for remote recovery via a timer/smart plug — see README):
   - `fastboot oem off-mode-charge 0`
   - **Verify on AC2003 (OnePlus Nord):** this command must be confirmed on a real device during provisioning. If it fails or has no effect, try documented alternatives for this model (e.g. `fastboot oem disable-charger-screen` variants seen on other OnePlus builds) and record the working command here before fleet rollout.
7. Re-run Android first-boot setup after unlock wipe.
8. Install Magisk APK, patch stock `boot.img`, then pull patched image via `adb pull`.
9. Boot patched image: `fastboot boot <patched-image>`.
10. Verify root with Magisk / Root Checker.
11. Remove local patched image copies from laptops after successful boot.

## Phase 4 - Install Apps (Termux Tooling + Tailscale)

1. Connect the phone to the operator laptop with USB debugging enabled, then run from the repo:
   - `./provision-apps.sh`
   - This installs Termux, Termux:API, Termux:Boot, and Tailscale over adb (F-Droid builds), skipping any that are already present. It also disables the package verifier that otherwise blocks these installs with an "unknown error" (Google Play Protect, `INSTALL_FAILED_VERIFICATION_FAILURE`).
   - Manual fallback: install the same four apps from f-droid.org in the phone browser.
2. Open Termux:Boot once after install (Android requires this before boot scripts run).
3. In Android app settings for Termux + Termux:API:
   - Allow storage access.
   - Allow camera permission.
   - Allow background activity.
   - Disable battery optimization / standby restrictions.
4. Enable high performance mode where available.

## Phase 5 - Join Tailscale

1. Tailscale is already installed by Phase 4 (`provision-apps.sh`).
2. Open it, allow VPN, and sign in with operator account from shared vault.
3. Confirm device appears in Tailnet.
4. Rename node to production naming convention (`Company-Location-Box-Phone`).

## Phase 6 - Deploy AndroidBoot

1. In Termux:
   - `git clone https://github.com/Easysort/AndroidBoot.git`
   - `cd AndroidBoot`
   - Copy the shared config from the operator laptop onto the phone as `~/AndroidBoot/.env`
     (or as `.env.full`, which `setup.sh` copies to `.env`). For example from the laptop:
     `adb push .env.full /sdcard/Download/env.full` then in Termux `cp /sdcard/Download/env.full ~/AndroidBoot/.env`.
   - `./setup.sh` (prompts only for the per-phone `DEVICE_ID`)
   - `./start.sh`
2. `setup.sh` replaces the old manual sequence (`install.sh`, `add_ssh_key.sh`, manual `.env` editing, manual boot-script placement). Shared config values live in the operator's `.env.full`; nothing is typed in by hand.
3. SSH is key-only. Operator public keys live in `keys/ops_authorized_keys` in the repo; `setup.sh` unions them into `~/.ssh/authorized_keys`. The Termux `passwd` password is not used for SSH and is no longer part of provisioning.
4. Approve root/superuser prompts for Termux when requested.
5. Create `/data/local/tmp/androidboot.env` (readable by root) so the Magisk boot script can bring up Tailscale before Termux mounts storage. Format: one `KEY=VALUE` per line; at minimum set `TS_AUTHKEY=tskey-auth-...` (reusable, pre-authorized key from the Tailscale admin console). See the comment block at the top of `boot/service.d-androidboot.sh` for all supported keys.

## Phase 7 - Box Install

1. Place phones with camera orientation as defined in hardware SOP (`.1` and `.2` same orientation, `.3` opposite).
2. Ensure charge routing and cable strain relief are correct before sealing.
3. If this phone is role `main`, configure hotspot SSID/PSK from shared vault and ensure support phones can join.

## Phase 8 - Verification Checklist

1. `tmux ls` shows `metrics`, `analyzer`, `watchdog`, `keepwarm`.
2. `curl localhost:5000/health` returns JSON.
3. `./ops.sh status` reports sane battery/disk/process data.
4. `./ops.sh logs 100` shows active capture + upload flow.
5. Supabase receives a new mp4 within ~20 minutes.
6. SSH from an operator machine whose key is in `keys/ops_authorized_keys` — no password prompt.
7. Reboot phone once, then confirm SSH works within ~3 minutes (before opening Termux), `tmux ls` shows all sessions, and `./start.sh` still recovers the capture stack.
8. Power off completely, apply charger power — phone should boot by itself (off-mode-charge) and reach full stack within ~5 minutes.

## Troubleshooting

- **MSM/Qualcomm driver problems on Windows:** use driver signature enforcement workaround from Phase 1.
- **Magisk patch fails first try:** retry patch operation; this is intermittently flaky.
- **ADB file transfer not appearing:** reconnect cable, re-enable file transfer mode, re-authorize host fingerprint.
- **Termux command lacks permission:** re-check Android app permissions and battery exclusions.
- **SSH lockout / password prompt:** SSH is key-only via the Magisk root-context sshd on port 8022. Add new operator keys to `keys/ops_authorized_keys`, `git pull` on the phone (or reboot so the boot script syncs keys), then retry. Password auth is disabled.
- **SSH down but Tailscale up:** `./ops.sh start-sshd` clears the deliberate-stop flag; `./ops.sh stop-sshd` stops sshd and sets that flag so the keepalive does not resurrect it.
- **Break-glass console (ADB over TCP):** if SSH is broken but Tailscale works, from a trusted host run `adb connect <tailscale-ip>:5555` then `adb shell`. The Magisk boot script enables this on port 5555. If OnePlus resets the prop after reboot, report it — this path may be unavailable on some builds.
- **Historical issue (cold-night shutdown/manual recovery):** keep-warm runs in the `keepwarm` tmux session (22:00–06:00 by default). For phones that fully power off, use a smart plug or mechanical timer plug on the site charger (see README). The Magisk boot script + Termux:Boot bring the stack back after any boot.
