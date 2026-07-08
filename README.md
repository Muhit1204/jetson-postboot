# jetson-postboot

A friendly helper for a brand-new NVIDIA Jetson Orin Nano. You run one
command, and it checks your board, explains in plain words what it finds,
and offers to fix a few common things for you — always asking first, and
never touching anything risky.

Primary target: **Jetson Orin Nano Developer Kit 8 GB (JetPack 6.x).** On
other boards it still reports what it can and tells you so.

## What it does

A Jetson that just finished its first boot usually needs a little setup
before it is ready for AI work. This tool helps with four things:

1. **Storage.** If your system was copied from a small SD card onto a big
   SSD, a lot of the SSD can be sitting unused. The tool measures exactly
   how much and shows you the commands to reclaim it. *(It shows them; it
   never runs them.)*
2. **Memory / swap.** The factory settings trade away memory that AI models
   need. The tool can tune this for you, and every change can be undone.
3. **AI software (CUDA / Ollama).** It confirms your NVIDIA AI software is
   healthy (and explains why `nvidia-smi` saying "N/A" is normal on Jetson,
   not a problem), and can install [Ollama](https://ollama.com) and suggest
   a chat model that fits your board's memory.
4. **Boot setup.** It checks that your board is set to start correctly and,
   if something looks off, prints the exact fix for you to apply by hand.

You do **not** need to be technical to use it. Every line it prints is
written to be understood without a computer-science background, and it
explains what it is about to do — and asks your permission — before making
any change.

## Requirements

- git
- Python 3.8 or newer (already on a Jetson; check with `python3 --version`)
- a normal user account that can use `sudo` (do **not** run the tool as root)

Nothing else. There is nothing to `pip install`.

## Get it and run it

```
git clone https://github.com/Muhit1204/jetson-postboot.git
cd jetson-postboot
python3 postboot.py
```

That first run only **looks** — it changes nothing. It prints a report
grouped by topic (`[system]`, `[storage]`, `[swap]`, `[boot]`, `[mlstack]`)
and saves a copy under `./reports/`.

### Reading the report

Each line starts with one of three words:

- **PASS** — everything is fine here; nothing to do.
- **WARN** — something to be aware of; the line explains what and why.
- **ACTION** — something you may want to change. The line tells you how.

At the bottom is a one-line summary. If there is nothing to act on, the tool
exits quietly; if there are WARN or ACTION items, it exits with a non-zero
code (handy for scripts).

## Letting it fix things (optional, always asks first)

```
python3 postboot.py --apply swap        # tune memory/swap for AI work
python3 postboot.py --apply mlstack     # install Ollama + suggest a model
python3 postboot.py --undo swap         # put the swap settings back
```

Add `--dry-run` to any of these to see the exact commands it *would* run
without running them:

```
python3 postboot.py --apply swap --dry-run
```

Useful extras:

- `--swapfile-size 8` — set the swap file size in GiB (default 8).
- `--model qwen2.5:3b` — pick a specific chat model for `--apply mlstack`
  (by default the tool suggests one that fits your memory). Every model is
  size-checked first; one too big for your board is refused with a plain
  explanation instead of crashing later.

Before each change the tool shows you the exact commands, takes a backup,
and asks you to type `y`. If you say no (or just press Enter), nothing
happens. Swap changes are recorded so `--undo swap` can put them back.

## What this tool will never do

Your safety comes first, so some things are permanently off-limits — no
confirmation can override them:

- No flashing, firmware, QSPI, or UEFI changes.
- No writing to `/boot` or `extlinux.conf` (boot problems are reported with
  the exact manual fix instead).
- No partition or filesystem changes of any kind (an undersized cloned disk
  is reported with the exact manual commands instead — the
  [JetsonHacks NVMe boot guide](https://jetsonhacks.com) covers full
  SD-to-NVMe migration).
- No third-party Python packages.

Everything the tool creates stays inside its own folder: `./logs`,
`./backups`, `./reports`, `./state`, and `./downloads`. Nothing is written
anywhere else on your system except the small, backed-up, reversible changes
you explicitly approve.

## For developers

The whole tool runs on any machine — no Jetson required — by replaying
captured hardware output:

```
python3 postboot.py --simulate tests/fixtures/orin-nano-8gb
python3 -m unittest discover -s tests -t .        # the test suite
```

See `PLAN.md` for the roadmap, `GUARDRAILS.md` for the safety rules that
bind every change, and `PROJECT_CONTEXT.md` for current status.
