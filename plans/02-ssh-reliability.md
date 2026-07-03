# Plan 02 — SSH that never locks us out

Problem: after ~7 days a phone is reachable on Tailscale but ssh "requires another
password"; we can't log in or auto-update. Read `plans/README.md` first for the root-cause
analysis; short version below.

## Root cause (established by code reading — verify on device, step V1)

Two sshd owners compete for port 8022:

1. **Termux-context sshd** — started manually during provisioning and re-asserted by
   `watchdog.sh` `ensure_sshd()`. Uses Termux defaults: password auth against the Termux
   password ("tooeasy", set via `passwd`) plus any keys in `~/.ssh/authorized_keys`.
2. **Root-context sshd** — `98-sshd-keepalive.sh`, installed as a Magisk service at
   `/data/adb/service.d/sshd-keepalive.sh`, loops every 10 min and starts sshd *as root*
   if none is running. It writes its own `sshd_config` (only when the file is missing) with
   `PasswordAuthentication no`.

While Termux is alive its sshd holds :8022 and everything works. When OnePlus's aggressive
app management kills Termux after days of uptime (a documented OnePlus behavior, and the
repo's own notes mention losing phones after ~7 days), the Termux sshd dies and the Magisk
loop starts a root-context one. Termux's patched OpenSSH running as uid 0 does not
authenticate like the termux-uid one: the termux-auth password check and the relative
`AuthorizedKeysFile .ssh/authorized_keys` resolution behave differently under uid 0. Result:
the phone "asks for a password" that nothing matches. Nobody changed a password.

Compounding it: `add_ssh_key.sh` self-describes as "not working properly", so pubkey auth
was likely never reliably provisioned, leaving password as the only door — the one that
breaks.

## Design: one owner, key-only, root-context

The sshd that must survive is the one that outlives the Termux *app* being killed
(processes started by Magisk `service.d` as root survive app kills; only their app-context
siblings die). Therefore:

- **The Magisk root-context sshd becomes the ONLY sshd.** `watchdog.sh` stops starting sshd
  entirely (delete `ensure_sshd` and its call).
- **Key-only auth, made to actually work under uid 0.** Password auth stays disabled.
- **The Termux password stops mattering.** Note in SETUP.md that `passwd` is no longer part
  of provisioning.

## Changes

### 1. Rewrite the keepalive script (`boot/service.d-androidboot.sh` per plan 01 layout)

Replace `98-sshd-keepalive.sh`. Requirements:

- Always (over)write a config it fully controls at
  `$PREFIX/etc/ssh/sshd_config_root` (do NOT reuse/mutate Termux's default
  `sshd_config`, and do not keep the "only write if missing" behavior — that is how config
  drift happened) with at least:
  - `Port 8022`
  - `PasswordAuthentication no`, `PubkeyAuthentication yes`
  - `AuthorizedKeysFile /data/data/com.termux/files/home/.ssh/authorized_keys` — absolute
    path so uid-0 home resolution cannot break it.
  - `StrictModes no` — the home dir is owned by the termux uid, not root; default
    StrictModes will reject the authorized_keys file. This is the classic silent
    pubkey-failure under root-context Termux sshd. (Investigate on device: if StrictModes
    can stay `yes` with correct ownership, prefer that; but do not ship untested.)
  - `HostKey` lines pointing at fixed absolute paths; generate once with `ssh-keygen -A` if
    missing, and never regenerate if present (avoids host-key-changed alarms).
  - `ClientAliveInterval 120`, `ClientAliveCountMax 3`, `PidFile` as today.
- Before starting its own sshd, kill any other sshd bound to :8022 (single-owner rule).
- Log to `/data/local/tmp/androidboot-boot.log` with timestamps, as today.
- Investigation task: confirm what login uid/shell a root-run Termux sshd gives on this
  build (Termux openssh is patched single-user). If sessions land as root with a broken
  environment, add a `ForceCommand`/login shell wrapper that execs into a proper Termux
  environment (`PATH`, `LD_PRELOAD`/`LD_LIBRARY_PATH`, `HOME`, `PREFIX`), or use
  `su - <termux_uid>` in the wrapper. The acceptance test is: `ssh phone git -C
  ~/AndroidBoot pull` works — that is the auto-update path we must protect.

### 2. Fix key provisioning (`setup.sh`, per plan 01 §4)

- `mkdir -p ~/.ssh && chmod 700 ~/.ssh`; append the ops public key only if absent (handle
  the file-not-yet-existing case that broke `add_ssh_key.sh`); `chmod 600 authorized_keys`.
- Support multiple keys: keep the key list in one place, e.g. `keys/ops_authorized_keys` in
  the repo, and have setup.sh (and the boot script, defensively, on every boot) sync it into
  `~/.ssh/authorized_keys`. Phones already trust the repo; this gives key rotation via
  `git pull` — which fixes "can't add a new laptop key to 15 phones by hand".

### 3. Second door (cheap insurance, not a second sshd)

SSH has a single point of failure left: sshd config/binary breakage after an update. Add to
the root boot script: `setprop service.adb.tcp.port 5555` + restart adbd (root makes this
possible), so `adb connect <tailscale-ip>:5555` works as a break-glass console. It rides the
same Tailscale transport but a completely different daemon. Document the command in
README/SETUP troubleshooting. (Judgment call: if on-device testing shows OnePlus resets the
prop, note it as unavailable and drop it — do not build anything elaborate.)

### 4. Remove the competition

- Delete `ensure_sshd()` from `watchdog.sh`.
- Delete `add_ssh_key.sh` (absorbed into setup.sh).
- `root_ssh_stop.sh` behavior lives on as `ops.sh stop-sshd` (plan 01) — update it to also
  pause the keepalive loop (e.g. touch a `/data/local/tmp/sshd-stop` flag the loop checks),
  otherwise the keepalive resurrects sshd 10 minutes after you deliberately stop it.

## Acceptance criteria

- Exactly one sshd process exists after boot and after `am force-stop com.termux`.
- Pubkey login works; password login is refused (`ssh -o PreferredAuthentications=password`
  gets `Permission denied`).
- After force-stopping Termux (simulates the 7-day kill): ssh still works within 10 min,
  and `ssh phone 'git -C ~/AndroidBoot pull'` succeeds — the auto-update path survives.
- Host key stays stable across reboots (no client-side MITM warnings).

## On-device verification checklist (for the operator)

- V1 (confirms root cause, do FIRST on a currently-locked-out phone if one exists):
  `adb shell` or physical access → `ps -A | grep sshd` and check which uid owns it, and
  read `$PREFIX/etc/ssh/sshd_config`. Expected: uid 0 sshd + `PasswordAuthentication no`.
  Record findings; if it contradicts the analysis, stop and reassess before rollout.
- V2: fresh install on a test phone → reboot → ssh with key from a machine whose key is in
  `keys/ops_authorized_keys` → works with no password prompt.
- V3: `am force-stop com.termux`, wait 10 min → ssh again → works; `tmux ls` still shows
  sessions (tmux server is a separate process — note for operator: it may die with the app
  on some builds; plan 03's boot path covers that).
- V4: reboot phone → ssh works within 3 minutes of boot, before ever opening Termux.
