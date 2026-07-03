# Plan 03 — Survive cold nights, recover from anything without a site visit

Problem: phones sometimes power off (suspected cold, night/early morning). Someone drives
out, powers on (battery reads 100%), sometimes restarts tmux/tailscale/sshd/script by hand.
Read `plans/README.md` first.

Two independent failure classes; both must be handled:

- **A. The phone turned itself off.** No software running on the phone can fix "off". We
  reduce the odds (keep-warm) and make power-on possible remotely (off-mode-charge +
  switched power).
- **B. The phone is on but the stack is dead** (post-reboot, app killed, tmux gone). This is
  fully fixable in software: boot → everything running, zero hands.

## Part B first (pure software, biggest payoff)

### B1. Root boot script — Magisk `/data/adb/service.d/`

Extend the script from plan 02 (`boot/service.d-androidboot.sh`) to own, at every boot and
in its keepalive loop (interval ~5 min):

1. sshd (plan 02).
2. **tailscaled** — the repo's own notes say the Tailscale Android app dies over time
   ("use magisk tailscale instead"). Standardize on the root tailscaled (Magisk module or
   binary at a fixed path): start if not running, `tailscale up` re-assert. Move
   `ensure_tailscale` logic out of `watchdog.sh` into here — network access must not depend
   on the Termux app being alive, which is exactly the 7-day failure. Requires
   `TS_AUTHKEY` handling: document that a **reusable, pre-authorized, tagged** auth key
   goes in `.env` (or `/data/local/tmp/androidboot.env` readable by root — decide during
   implementation; the root script cannot read Termux's `.env` if Termux storage is not
   mounted yet, so test the timing).
3. **adbd on TCP 5555** (plan 02 §3).
4. **Launch the Termux userland stack**: the most reliable pattern is to let Termux:Boot do
   it (B2), but as a belt-and-braces fallback the root script can
   `am start-foreground-service`/`am broadcast` to wake Termux, or directly run
   `start.sh` via a Termux-env wrapper as the termux uid. Implement Termux:Boot as primary;
   add the root fallback only if it proves simple — do not gold-plate.

### B2. Termux:Boot — `boot/termux-boot.sh` → `~/.termux/boot/`

- SETUP.md gains: install the Termux:Boot app from F-Droid (one-time, alongside Termux:API)
  and open it once.
- The script: `termux-wake-lock`, then exec `~/AndroidBoot/start.sh` (idempotent per
  plan 01, so double-starting with the job-scheduler path is harmless).
- `setup.sh` installs it (`mkdir -p ~/.termux/boot`, copy, `chmod +x`).

### B3. Re-assert while running

- Keep the `termux-job-scheduler` 15-min job calling `start.sh` (now actually effective —
  plan 01 fixes the `uploader`/`analyzer` session-name bug that made re-runs abort).
- `watchdog.sh` after plans 01+02 shrinks to: hotspot/Wi-Fi role assertion + error emission.
  Add one duty: if the tmux server itself is gone (`tmux ls` fails), it can't run (it lives
  in tmux) — so instead put a tmux-server check in the job-scheduler path, i.e. `start.sh`
  already recreates sessions; confirm that covers it and delete any dead watchdog duties.

### B4. Scheduled nightly reboot (wedge-clearer)

Android boxes that run for months accumulate wedge states (media server leaks, camera HAL
hangs — `termux-camera-photo` hanging is a known Termux issue). Add to the root boot
script's loop: at 04:00–04:15 local (pick from `date +%H%M`, guard with a
last-reboot-marker file so it fires once), `svc power reboot` (or `reboot`). At boot,
B1+B2 bring everything back. Make it configurable via env (`NIGHTLY_REBOOT=1` default on).
Note: 04:00 is the cold-risk window; if the operator prefers, make the time configurable —
a reboot takes ~2 min and the keep-warm loop stops during it. Surface this trade-off in the
final summary rather than deciding silently.

## Part A — the phone that is OFF

### A1. Auto power-on when charger power is applied

OnePlus (fastboot) supports disabling off-mode charging so the device **boots instead of
showing the charging animation when power is applied while off**:

```
fastboot oem off-mode-charge 0
```

- Add this as a provisioning step in SETUP.md (phones are already in fastboot during
  rooting — same session).
- Investigate/verify the exact command works on the Nord (AC2003); alternatives seen in the
  wild: `fastboot oem enable-charger-screen` variants. Must be verified on a real device;
  mark the SETUP step with the verified command only.
- **Operational consequence (put prominently in README/SETUP):** with this set, if the site's
  charger is on a smart plug or a cheap mechanical timer plug that cuts power briefly once
  per day (e.g. 2 minutes at 06:00), any phone that died overnight boots automatically when
  power returns, and plan-02/03 boot scripts bring it fully online — zero site visits.
  Recommend the smart-plug purchase in the final summary; this is the only actual fix for
  failure class A. (The phone found "at 100%" was almost certainly killed by a low-temperature
  battery-protection cutoff or kernel thermal policy, not by draining — software cannot
  prevent all of those.)

### A2. Keep-warm at night (reduce shutdown odds)

Port the idea from commit `060da5c` (was added to the deleted `applications/video` app,
never to the deployed one) into the deployed stack — but as its own tiny tmux session or a
thread in `analyzer.py` (implementer's choice; keep it dumb):

- Between configurable hours (default 22:00–06:00 local) and only when
  `termux-battery-status` temperature is below a threshold (default 15°C), run a bounded
  CPU load (e.g. a `sha256sum /dev/zero`-style busy loop on 1–2 cores, duty-cycled: 30s on
  / 30s off) to generate heat.
- Stop immediately outside the window or above the temperature ceiling (default 30°C) —
  phones in direct summer sun overheat; this must never make that worse. `metrics.py`
  already reads battery temp; reuse that approach.
- Note: the phone is on a charger; extra drain is irrelevant. Charging itself warms the
  battery — do NOT implement charge-limiting.
- Expose knobs in `.env`: `KEEPWARM=1`, `KEEPWARM_START=22`, `KEEPWARM_END=6`,
  `KEEPWARM_MIN_C=15`, `KEEPWARM_MAX_C=30`.

### A3. Make "found it dead" diagnosable

We currently guess ("thought to be cold"). Add to `metrics.py`'s health payload (already
polled by the monitoring site): battery temperature is already there — good. Additionally,
have `analyzer.py` or the metrics loop append a once-per-5-min line
(`ts, battery_temp, cpu_temp, battery_pct, charging`) to a local ring file
(`$LOG_DIR/blackbox.log`, logrotate by size, keep ~7 days). After the *next* unexplained
shutdown, the last lines before silence tell us whether it was cold, thermal, or power loss.
Cheap, and ends the guessing.

## Explicitly out of scope

- Charge limiting / battery health management (charging warmth is desirable here).
- Any hardware beyond recommending the timer/smart plug.
- Watchdog-triggered `fastboot` tricks from the phone itself.

## Acceptance criteria

- Reboot the phone and touch nothing: within 5 minutes ssh works, `tmux ls` shows all
  sessions, health endpoint answers, uploads resume. (This is the headline requirement —
  it converts every "site visit" into "power cycle the plug".)
- `am force-stop com.termux` → within 15 min the job scheduler or root fallback has the
  stack running again.
- With `KEEPWARM=1` and a mocked cold reading, the warm loop runs during night hours and
  provably stops above `KEEPWARM_MAX_C` and outside the window (unit-testable by
  parameterizing "now" and the temp reader).
- `blackbox.log` exists, rotates, and contains one line per ~5 min.
- SETUP.md contains the verified off-mode-charge fastboot step and the smart-plug guidance.

## On-device verification checklist (for the operator)

1. Reboot → wait 5 min → ssh in without opening Termux → `tmux ls`, `curl localhost:5000`.
2. `am force-stop com.termux` → wait 15 min → repeat checks.
3. Power off completely; apply charger power → phone boots by itself (verifies
   off-mode-charge) → full stack up within 5 min.
4. One cold night later: check `blackbox.log` overnight temps, confirm keep-warm cycles
   appear in the log when below threshold.
5. Confirm the nightly reboot happened (uptime < 24h in the morning, marker file updated).
