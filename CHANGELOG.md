# Changelog

All notable changes to jetson-postboot are recorded here. Dates are UTC.
The project develops in phases behind a strict set of safety rules
(summarised in the README) that bind every change.

## [0.1.0] - 2026-07-09

First working release: a read-only Jetson report plus two reversible,
always-confirmed setup actions, all runnable on any machine via fixture
simulation. Every message is written to be understood by a non-technical
user (PLAN goal G6).

### Added

- **Read-only report (Tier 0).** Board model, Jetson software version
  (L4T/JetPack), memory, and power mode; storage geometry; swap and zram
  state; boot configuration; and CUDA/cuDNN/Ollama status. Saved to
  `./reports/` as text and JSON. PASS/WARN/ACTION lines; WARN or ACTION
  sets a non-zero exit code.
- **Storage advisory (Tier 3, advice only).** Measures unused space behind
  the root partition on a cloned disk and prints the exact `growpart` /
  `resize2fs` sequence — checking every precondition first, and never
  running the commands itself.
- **Swap tuning (`--apply swap`, Tier 1, reversible).** Sets and persists
  `vm.swappiness`, disables JetPack zram behind a memory-headroom safety
  check (offering a reboot-apply path when memory is tight), and creates an
  NVMe swap file — skipping any step already satisfied. `--undo swap`
  restores the prior state; `--swapfile-size` sets the size.
- **AI stack (`--apply mlstack`, Tier 1).** Installs Ollama from its
  official script (downloaded to `./downloads/`, shown with size and
  sha256, run only after confirmation — never piped from the network into a
  shell) and pulls a model. The tool suggests a model sized to leave at
  least half of memory free, and refuses any model too big for the board
  with a plain-language explanation. `--model` overrides the suggestion.
- **Boot advisor (Tier 3, advice only).** Compares the configured root
  against the mounted root and gives one of three verdicts, printing exact
  manual fixes (and backing up `extlinux.conf`) without ever writing to
  `/boot`.
- `--dry-run` on every apply path; `--simulate <fixtures>` to run the whole
  tool on any machine.

### Safety

- One command gateway with a strict binary + argument allowlist; a missing
  optional binary is reported, never a crash.
- Every mutation is shown, backed up, and individually confirmed;
  unattended runs make no changes. All artifacts stay inside the repo
  (`./logs`, `./backups`, `./reports`, `./state`, `./downloads`).
- No flashing/firmware/UEFI, no writes to `/boot` or `extlinux.conf`, no
  partition or filesystem changes, no third-party Python dependencies.
