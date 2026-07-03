# Plan 01 — Slim down and restructure the repo

Goal: only what is deployed remains, with a layout a new person understands in 5 minutes.
Read `plans/README.md` first.

## 1. Port the one thing worth saving from dead code

Before deleting anything: commit `060da5c` added a nighttime keep-warm capture loop to
`applications/video/continuous_capture.py` (unused app). The concept — during night hours,
keep the SoC mildly busy so the device stays warm — must be ported into the deployed app.
Plan 03 specifies how; for this plan, just do not lose the reference (the diff is in git
history, the file itself gets deleted below).

## 2. Delete

- `applications/container/`, `applications/entrance/`, `applications/video/` — unused apps.
- `applications/entrance-video/verbose_upload_test.py` — one-off debug script.
- `api/` — abandoned Go metrics API (superseded by `metrics.py`); includes an 8MB binary.
- `tailscale_1.58.2_arm64/` and note that `tailscale_1.58.2_arm64.tgz` (untracked) should be
  removed locally — tailscale is provided by the Magisk module on-device, not from this repo.
- `magisk_patched-30700_Lns4s.img` (untracked, 100MB) — keep on Google Drive only.
- `applications/common/` — only consumed by deleted apps and by the trivial
  `pytest applications/common/verify.py` call in `run.sh` (a single vacuous assertion).
  Delete it and drop the pytest call; replace with a real preflight check (see §4).
- `stop.sh`, `root_ssh_stop.sh` — fold into a single ops script (see §4).
- `.ruff_cache/` — add to `.gitignore` along with `*.img`, `*.tgz`, `run/`, `test.jpg`.

## 3. Target layout

```
AndroidBoot/
  README.md              # what this is, architecture diagram, common ops commands
  SETUP.md               # rewritten new_phone_todo.md (see §5)
  .env.example           # single documented template (replaces .env.example + phone.env confusion)
  .gitignore
  setup.sh               # one-shot provisioning (replaces install.sh + add_ssh_key.sh)
  start.sh               # start/assert the stack (replaces run.sh + tmux-starter.sh)
  ops.sh                 # stop | status | logs  (replaces stop.sh, get_logs.sh, root_ssh_stop.sh)
  watchdog.sh            # slimmed (see below)
  boot/                  # everything that must run at boot (populated by plans 02/03)
    service.d-androidboot.sh   # installed to /data/adb/service.d/ (root)
    termux-boot.sh             # installed to ~/.termux/boot/ (termux uid)
  app/
    analyzer.py          # from applications/entrance-video/ (unchanged behavior)
    metrics.py           # from applications/entrance-video/ (unchanged behavior)
  plans/                 # this folder; delete when all plans are executed and reviewed
```

Notes:

- `analyzer.py` reads `../../device_id.txt` relative to its own dir — when moving it, switch
  to `REPO_DIR` from env (already exported by the starter) or a path relative to repo root.
- Since only one app exists, kill the `run.sh -a <app>` selector entirely. `start.sh` takes
  no arguments.
- Keep tmux as the process supervisor (operators know it, and attaching to a session is the
  main debugging tool). Do NOT migrate to termux-services/runit in this plan.

## 4. Script consolidation details

**`start.sh`** (idempotent — safe to run any number of times, from cron/job-scheduler/boot):

- Sources `.env` (export via `set -a`).
- Fixes the existing bug: the session-exists check for the analyzer tests `-t uploader` but
  the session is named `analyzer` (`applications/entrance-video/tmux-starter.sh` line 24).
  Use one loop over `(metrics analyzer watchdog)` so name mismatches are impossible.
- Must NOT die on duplicate sessions (`set -e` + unguarded `tmux new-session` is the current
  failure). Every session start is guarded.
- Keeps the `termux-notification` + `termux-wake-lock` calls.
- Registers the `termux-job-scheduler` job (id 700, 15 min, persisted) pointing at itself,
  if not already registered (`termux-job-scheduler -p` to check).
- Preflight (replaces the pytest call): `.env` exists, `device_id.txt` exists and non-empty,
  `SUPABASE_URL`/key set, `termux-camera-photo` available. Fail loudly with a clear message.

**`setup.sh`** (run once per phone, replaces `install.sh`):

- Package install as today (`install.sh` lines 7–17), minus golang (nothing Go remains).
- Installs boot scripts from `boot/` (per plans 02/03).
- Installs the operator's ssh public key robustly: create `~/.ssh` with `chmod 700`, append
  key if absent, `chmod 600 authorized_keys`. The current `add_ssh_key.sh` is marked "not
  working properly" — the likely cause is `grep -q` against a nonexistent file exiting 2
  under older strictness; handle the file-not-exists case.
- Prompts for / accepts `DEVICE_ID` and writes `device_id.txt` and `.env` from
  `.env.example`, so the manual "vim phone.env, rename to .env" steps disappear.

**`ops.sh`**: `ops.sh status` (tmux ls, sshd/tailscaled pids, disk, battery via
termux-battery-status), `ops.sh logs [n]` (current `get_logs.sh` behavior), `ops.sh stop`
(cancel job, kill tmux server), `ops.sh stop-sshd` (current `root_ssh_stop.sh` behavior).

**`watchdog.sh`**: keep the loop but strip what plans 02/03 take over (sshd and tailscaled
ownership move to the root boot script). What remains here: hotspot/Wi-Fi role assertion and
error-file emission. Coordinate with plan 02/03 — implement those first if doing all three
in one pass, then slim this file once.

## 5. Rewrite `new_phone_todo.md` → `SETUP.md`

Same content, restructured. Keep every hard-won detail (MSM download tool recovery, driver
signature workaround, Magisk flow) but:

- Number the phases: 1 Flash, 2 Android setup, 3 Root, 4 Termux, 5 Tailscale, 6 AndroidBoot
  (`git clone` + `./setup.sh`), 7 Box install, 8 Verification checklist.
- Phase 6 collapses ~15 manual steps into running `setup.sh` (that's the point of §4).
- Move "Bugs to look out for" / "Known issues" into a Troubleshooting section, and delete the
  entries that plans 02/03 fix (note them as fixed instead).
- Fix typos ("fastboot oem unluck", "Qiuckly") and dead formatting (the `install.sh` link).
- Do NOT put the Wi-Fi password, phone unlock PIN, or ssh password in this file. Reference
  "the shared vault" placeholder — the operator can fill in where those actually live.

## 6. Secrets flag (do not fix silently — surface to operator)

`.env.full` contains a Supabase JWT labeled `SUPABASE_ANON_KEY` whose payload role is
`service_role`. It is gitignored but lives on every phone; a stolen phone yields full DB
access. In the final summary, tell the operator to: (a) rotate the service_role key,
(b) issue a true anon key + storage-write RLS policy for the phones. Do not attempt the
Supabase-side work in this repo.

## 7. Git history (optional, ask via summary — do not do unilaterally)

`.git` is ~50MB because `api/metrics-api` and the tailscale binaries are in history. Deleting
them now stops growth but doesn't shrink clones. Offer the operator a one-time
`git filter-repo` / fresh-repo option in the final summary; phones `git pull` from GitHub, so
a history rewrite requires re-cloning on every phone — their call, not yours.

## Acceptance criteria

- `git ls-files | wc -l` drops from 34 to roughly 15; no binaries tracked.
- A fresh phone goes from "Termux + Tailscale installed" to fully running with:
  `git clone … && cd AndroidBoot && ./setup.sh && ./start.sh`.
- `start.sh` run twice in a row: second run exits 0 and changes nothing.
- `shellcheck` clean (or with justified disables) on all shell scripts.
- Existing deployed phones: `git pull` then a documented one-liner migration (old job id 700
  cancelled/re-registered, old tmux sessions can keep running until next reboot).

## On-device verification checklist (for the operator)

1. `git pull`, run the migration one-liner from the summary.
2. `./start.sh` → `tmux ls` shows `metrics`, `analyzer`, `watchdog`.
3. `curl localhost:5000/health` returns healthy JSON.
4. A new mp4 appears in Supabase within 20 minutes.
5. `./ops.sh status` and `./ops.sh logs` produce sane output.
