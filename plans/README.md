# AndroidBoot overhaul plans

Context for the implementing agent. Read this file first, then the three plans.

## What this repo is

Rooted OnePlus Nord phones run outdoors in Denmark year-round as cameras. Only the
`applications/entrance-video` app is deployed anywhere. Each phone:

- Takes 1 photo/second via `termux-camera-photo` (`analyzer.py`), compresses to ~100KB JPEG,
  packs each 15-minute window into an mp4, uploads to Supabase storage bucket `argo` under
  `DEVICE_ID/YYYY/mm/dd/HH/`.
- Serves a health JSON endpoint on port 5000 (`metrics.py`), polled by a monitoring site.
- Is reachable over Tailscale + Termux `sshd` on port 8022.

Runtime stack today (all of this is what the plans change):

- `run.sh -a entrance-video` registers a `termux-job-scheduler` job (id 700, every 15 min)
  that re-runs `applications/entrance-video/tmux-starter.sh`.
- `tmux-starter.sh` starts 3 tmux sessions: `metrics`, `analyzer`, `watchdog`.
- `watchdog.sh` (looping inside tmux) re-asserts sshd, tailscaled, and hotspot/Wi-Fi role
  ("main" phone runs a hotspot; "support" phones join it).
- `98-sshd-keepalive.sh` is installed to `/data/adb/service.d/sshd-keepalive.sh` (Magisk),
  runs at boot as root, and loops forever restarting sshd if it dies.
- Manual provisioning procedure is `new_phone_todo.md`.

## The three problems (from the operator)

1. Repo is bloated and confusing; most scripts/apps are unused. Make it much smaller and better.
2. After ~7 days a phone is still on Tailscale but SSH "requires another password" — we get
   locked out and can't auto-update.
3. Phones sometimes power off (suspected cold nights). Someone must physically visit: power on
   (battery is at 100% when found), sometimes restart tmux, restart tailscale/sshd, restart the
   script. This must become unattended.

## Root-cause analysis already done (verified by reading the code)

- **tmux-starter bug:** `applications/entrance-video/tmux-starter.sh` line 24 checks
  `tmux has-session -t uploader` but creates session `analyzer`. With `set -euo pipefail`,
  every 15-min re-run fails at the duplicate `tmux new-session -d -s analyzer` and aborts, so
  the job-scheduler "self-heal" mostly does nothing after first start.
- **SSH lockout (problem 2):** there are TWO sshd owners fighting over port 8022.
  Termux-context sshd (started manually / by `watchdog.sh`) honors the Termux password
  ("tooeasy") and Termux's default config. The Magisk root-context sshd
  (`98-sshd-keepalive.sh`) writes its *own* `sshd_config` (only if none exists) with
  `PasswordAuthentication no`, and when sshd runs as uid 0 the Termux single-user auth
  patches behave differently (password check and `authorized_keys` resolution can both
  fail). When OnePlus's aggressive app management kills Termux after days, the Termux sshd
  dies and the root one takes over → the phone suddenly "wants a different password".
  Additionally `add_ssh_key.sh` is marked "not working properly" in its own comments, so
  pubkey auth may never have been provisioned. Nobody changed any password.
- **Cold shutdown (problem 3):** a nighttime keep-warm workaround exists (commit `060da5c`
  "keep warm at night") but was added to `applications/video/continuous_capture.py`, which is
  NOT the deployed app. `entrance-video` never got it. Also nothing auto-starts the capture
  stack at boot — `termux-job-scheduler` jobs are `--persisted` but Termux:Boot is not used,
  and the whole recovery procedure is manual.
- **Bloat:** `api/` (8MB compiled Go binary in git), `tailscale_1.58.2_arm64/` (44MB in git),
  3 unused applications (`container`, `entrance`, `video`), `.git` is ~50MB.
- **Secret hygiene:** `.env.full` (gitignored, but present on disk and on phones) contains a
  Supabase JWT labeled ANON_KEY whose role claim is actually `service_role`. Flag for rotation.

## The plans

Execute in this order (02 and 03 touch files that 01 deletes/moves — 01 defines the final layout):

1. `01-repo-slim-down.md` — delete dead code, restructure, rewrite setup docs.
2. `02-ssh-reliability.md` — one sshd owner, key-only auth, never lose the door again.
3. `03-uptime-auto-recovery.md` — survive cold nights, and make any boot fully unattended.

Constraints for all plans:

- Target environment is Termux on Android 11+ (OnePlus Nord AC2003), rooted with Magisk.
  There is no systemd; persistence primitives are: Magisk `/data/adb/service.d/` (root, at
  boot), Termux:Boot (`~/.termux/boot/`, Termux uid, at boot), `termux-job-scheduler`
  (periodic), termux-services/runit (`sv`), and tmux.
- Changes cannot be tested on a phone from this machine automatically; every plan must end
  with an explicit on-device verification checklist the operator can run over ssh.
- Do not break currently-deployed phones' expectations more than necessary: the update path
  today is `ssh -> git pull -> re-run starter`, so keep script entry-point names stable where
  possible or provide a migration note in the final summary.
- Keep it boring: plain POSIX-ish shell + the existing Python. No new services, daemons
  written in new languages, or frameworks.
