# AndroidBoot

AndroidBoot is the on-phone runtime for rooted OnePlus Nord devices that capture 1 image/sec, package each 15-minute segment into mp4, upload to Supabase Storage, and expose local health on port 5000.

## Repo layout

```text
AndroidBoot/
  README.md
  SETUP.md
  .env.example
  .gitignore
  provision-apps.sh   # laptop-side: install Termux/Termux:API/Termux:Boot/Tailscale via adb
  setup.sh
  start.sh
  ops.sh
  watchdog.sh
  boot/
    service.d-androidboot.sh
    termux-boot.sh
  app/
    analyzer.py
    keepwarm.sh
    metrics.py
  keys/
    ops_authorized_keys
  plans/
```

## Runtime architecture

- `start.sh` is the idempotent entrypoint. It loads `.env`, validates prerequisites, ensures tmux sessions (`metrics`, `analyzer`, `watchdog`, `keepwarm`) exist, and registers the 15-minute `termux-job-scheduler` self-heal job (id `700`).
- `app/analyzer.py` captures/compresses images and uploads 15-minute mp4 segments to Supabase bucket `argo`.
- `app/metrics.py` serves `GET /health` on `localhost:5000` and appends a blackbox CSV sample every ~5 minutes to `$LOG_DIR/blackbox.log`.
- `app/keepwarm.sh` runs in tmux and duty-cycles CPU load on cold nights to reduce shutdown risk.
- `watchdog.sh` keeps network role behavior stable (main phone hotspot, support phone Wi-Fi attach) and emits JSON error files.
- `boot/service.d-androidboot.sh` is the root boot owner: Magisk sshd (port 8022, key-only), tailscaled, ADB TCP 5555, and optional nightly reboot. Ops keys synced from `keys/ops_authorized_keys` on every loop.
- `boot/termux-boot.sh` runs at Termux boot: acquires wake-lock and execs `start.sh` (installed to `~/.termux/boot/` by `setup.sh`).

## Unattended recovery

Phones outdoors in Denmark sometimes fully power off (cold-night battery protection). Software on a dead phone cannot fix that — two layers handle it:

1. **Keep-warm** (`KEEPWARM=1`, default 22:00–06:00): bounded CPU load when battery temp is below 15°C, stops above 30°C or outside the window.
2. **Smart plug / timer plug on the site charger:** with `fastboot oem off-mode-charge 0` (see SETUP.md), cutting mains power briefly once per day (e.g. 2 minutes at 06:00) makes a dead phone boot when power returns. The Magisk boot script + Termux:Boot then bring ssh, Tailscale, and the capture stack online without a site visit.

After `git pull`, copy `TS_AUTHKEY` (reusable, pre-authorized Tailscale key) to `/data/local/tmp/androidboot.env` on each phone if not already there — the root boot script cannot read Termux's `.env` at early boot.

## Nightly reboot

The Magisk boot script reboots once per day between 04:00–04:15 local (when `NIGHTLY_REBOOT=1`, the default). A marker file prevents reboot loops. **Trade-off:** 04:00 is inside the cold-risk window; keep-warm stops during reboot (~2 min). Disable with `NIGHTLY_REBOOT=0` in `/data/local/tmp/androidboot.env` if cold nights are the bigger concern.

## Common operations

```bash
# one-time per phone
./setup.sh

# start/assert runtime stack (safe to rerun)
./start.sh

# inspect runtime
./ops.sh status
./ops.sh logs 100

# stop tmux + scheduler
./ops.sh stop

# stop all sshd processes as root (and pause keepalive resurrection)
./ops.sh stop-sshd

# resume sshd after a deliberate stop
./ops.sh start-sshd
```

## Troubleshooting

- **SSH lockout:** auth is key-only. Add keys to `keys/ops_authorized_keys`, `git pull`, reboot or wait for the boot script to sync keys into `~/.ssh/authorized_keys`.
- **Break-glass ADB:** if SSH is broken but Tailscale works: `adb connect <tailscale-ip>:5555` then `adb shell`.

## Existing deployment migration

After `git pull` on an already deployed phone:

```bash
cd ~/AndroidBoot && chmod +x setup.sh start.sh ops.sh watchdog.sh boot/*.sh && termux-job-scheduler --cancel --job-id 700 || true; ./start.sh
```
