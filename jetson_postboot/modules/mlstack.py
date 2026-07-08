"""ML stack module: CUDA truth report (Tier 0, Phase 1); Ollama install and
fit-checked model pull (Tier 1, Phase 3, PLAN 6.6).

Detection reads nvcc, the /usr/local/cuda symlink, dpkg's cuDNN entries, and
Ollama presence. It states explicitly that nvidia-smi reporting N/A is
expected on Jetson integrated GPUs, because that is the single most common
false alarm for newcomers (PLAN problem statement 3).

Apply: download the install script into ./downloads/ (GUARDRAILS 4: behind
confirmation, size and sha256 shown, never piped from the network into a
shell), run it with sudo after a second confirmation, then pull the
requested model only when the fit check says it can actually run. All text
to the PLAN G6 bar: understandable by a non-technical user.
"""

import hashlib
import re
from pathlib import Path
from urllib import request

from jetson_postboot.checks.system_info import parse_meminfo
from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN, human_gib
from jetson_postboot.lib.runner import RunnerError, SimulationMissError

_CUDA_SYMLINK = "/usr/local/cuda"
# GUARDRAILS 4: the one download URL this tool may fetch.
OLLAMA_INSTALL_URL = "https://ollama.com/install.sh"
_INSTALL_SCRIPT_NAME = "ollama-install.sh"
# Provisional default until PLAN Q2 names the featured tag: a 3B model
# fits the 8 GB board comfortably.
DEFAULT_MODEL = "qwen2.5:3b"
_GIB = 1 << 30

# PLAN 6.6 model-fit estimate. Deliberate rough constants, kept together:
# a Q4-quantized model occupies about 0.65 GiB per billion parameters, plus
# about 1.5 GiB for the runtime and the growing conversation memory
# (KV cache). The ratios draw the comfortable/tight/too-big lines against
# total RAM so that on the 8 GB board roughly: up to 4B comfortable,
# 7B-8B tight, larger refused.
MODEL_GIB_PER_BILLION = 0.65
MODEL_OVERHEAD_GIB = 1.5
_FIT_COMFORT_RATIO = 0.70
_FIT_TIGHT_RATIO = 0.92

# Matches the parameter count in tags like qwen2.5:3b, llama3:8b-instruct
# (the last <number>b token wins, so a 2.5 in the model name never matches).
_PARAMS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)[bB]\b")


def parse_model_params(tag):
    """Billions of parameters from a model tag; None when the tag does not
    say (no <number>b token)."""
    matches = _PARAMS_PATTERN.findall(tag)
    return float(matches[-1]) if matches else None


def estimate_resident_bytes(params_billions):
    """Rough resident size of a Q4 model while answering questions."""
    gib = params_billions * MODEL_GIB_PER_BILLION + MODEL_OVERHEAD_GIB
    return int(gib * _GIB)


def model_fit(tag, mem_total):
    """('fits'|'tight'|'refuse'|'unknown', plain-language explanation).

    PLAN G6: the explanation must make sense to a non-technical user - it
    says what the model needs, what the board has, and what to do instead.
    """
    params = parse_model_params(tag)
    if params is None:
        return ("unknown",
                "The tag '{}' does not say how big the model is, so the "
                "fit cannot be checked. Use a tag that ends in a size, "
                "like qwen2.5:3b (the '3b' means 3 billion "
                "parameters).".format(tag))
    estimate = estimate_resident_bytes(params)
    need, have = human_gib(estimate), human_gib(mem_total)
    if estimate <= mem_total * _FIT_COMFORT_RATIO:
        return ("fits",
                "This model needs about {} of memory while running, and "
                "this board has {} in total - it fits comfortably.".format(
                    need, have))
    if estimate <= mem_total * _FIT_TIGHT_RATIO:
        return ("tight",
                "This model needs about {} of memory while running, and "
                "this board has {} in total. It should work, but it will "
                "be a tight fit: close other programs first, and expect "
                "slower answers.".format(need, have))
    comfortable = int((mem_total * _FIT_COMFORT_RATIO / _GIB -
                       MODEL_OVERHEAD_GIB) / MODEL_GIB_PER_BILLION)
    return ("refuse",
            "This model ({}B parameters) needs about {} of memory while "
            "running, but this board only has {} in total - it would run "
            "out of memory and crash. The biggest size that runs "
            "comfortably here is about {}B, so try a tag like "
            "qwen2.5:3b instead.".format(
                ("{:g}".format(params)), need, have, comfortable))


def parse_dpkg_cudnn(text):
    """[(package, version), ...] for installed (ii) cudnn packages from
    `dpkg -l` output; the grep-style filter happens here in Python."""
    packages = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == "ii" and "cudnn" in fields[1].lower():
            packages.append((fields[1], fields[2]))
    return packages


def parse_nvcc_release(text):
    """'12.6, V12.6.68' from nvcc --version; None when unrecognizable."""
    match = re.search(r"release\s+([\d.]+,\s*V[\d.]+)", text)
    return match.group(1) if match else None


def check(runner, report):
    """Tier 0 CUDA/Ollama findings (registry entry)."""
    nvcc = runner.run(["nvcc", "--version"])
    if nvcc.returncode == 0:
        release = parse_nvcc_release(nvcc.stdout)
        report.add(LEVEL_PASS, "mlstack",
                   "nvcc release {}".format(release or "unknown"))
    else:
        report.add(LEVEL_WARN, "mlstack",
                   "nvcc is not on PATH (the CUDA toolkit may still be "
                   "installed; if {}/bin exists, add it to PATH)".format(
                       _CUDA_SYMLINK))

    try:
        target = runner.read_link(_CUDA_SYMLINK)
    except (OSError, SimulationMissError):
        target = None
    if target:
        report.add(LEVEL_PASS, "mlstack",
                   "CUDA toolkit: {} -> {}".format(_CUDA_SYMLINK, target))
    else:
        report.add(LEVEL_WARN, "mlstack",
                   "no {} symlink: CUDA toolkit not installed".format(
                       _CUDA_SYMLINK))

    cudnn = parse_dpkg_cudnn(runner.run(["dpkg", "-l"]).stdout)
    if cudnn:
        report.add(LEVEL_PASS, "mlstack",
                   "cuDNN installed: {}".format(", ".join(
                       "{} {}".format(name, version)
                       for name, version in cudnn[:3])))
    else:
        report.add(LEVEL_WARN, "mlstack", "no cuDNN packages installed")

    report.add(LEVEL_PASS, "mlstack",
               "nvidia-smi reporting N/A is expected on Jetson integrated "
               "GPUs and is not a fault")

    ollama = runner.run(["which", "ollama"])
    if ollama.returncode == 0:
        report.add(LEVEL_PASS, "mlstack",
                   "Ollama installed: {}".format(ollama.stdout.strip()))
    else:
        report.add(LEVEL_PASS, "mlstack",
                   "Ollama not installed (--apply mlstack installs it in "
                   "Phase 3)")


def _real_fetch(url):
    """Runtime download (GUARDRAILS 4). Returns raw bytes; the caller writes
    them into ./downloads/ - the stream is never fed to a shell."""
    with request.urlopen(url, timeout=120) as response:
        return response.read()


def apply(runner, report, ctx):
    """Tier 1 ML stack setup (--apply mlstack): install Ollama if missing,
    then pull a model that actually fits. ctx carries downloads_dir, echo,
    confirm, dry_run, model (tag or None for the default), and optionally
    fetch (tests and simulate inject one; real mode downloads)."""
    echo = ctx["echo"]
    dry_run = ctx["dry_run"]
    fetch = ctx.get("fetch") or _real_fetch
    downloads_dir = Path(ctx["downloads_dir"])
    script = downloads_dir / _INSTALL_SCRIPT_NAME
    tag = ctx.get("model") or DEFAULT_MODEL

    installed = runner.run(["which", "ollama"]).returncode == 0
    if installed:
        report.add(LEVEL_PASS, "mlstack", "Ollama is already installed; "
                   "skipping the installer")
    else:
        echo("")
        echo("Ollama is a free program that runs AI chat models directly on "
             "this computer, with no account and no cloud.")
        echo("To install it, this tool will download Ollama's official "
             "install script from {} into the tool's own downloads folder, "
             "show you its size and fingerprint, and only run it if you "
             "agree.".format(OLLAMA_INSTALL_URL))
        if dry_run:
            echo("DRY-RUN would download: {} -> {}".format(
                OLLAMA_INSTALL_URL, script))
            runner.run(["sh", str(script)], sudo=True, mutate=True)
        elif not ctx["confirm"](
                "Download the Ollama installer from ollama.com now?"):
            report.add(LEVEL_WARN, "mlstack",
                       "Ollama install declined; nothing was downloaded "
                       "and no model can be set up without it")
            return
        else:
            data = fetch(OLLAMA_INSTALL_URL)
            downloads_dir.mkdir(parents=True, exist_ok=True)
            script.write_bytes(data)
            echo("downloaded {} ({} bytes)".format(script, len(data)))
            echo("sha256 {}".format(hashlib.sha256(data).hexdigest()))
            echo("(the sha256 is the file's fingerprint: you can compare "
                 "it against other sources to check the download was not "
                 "tampered with)")
            if not ctx["confirm"](
                    "Run the installer now? It needs administrator (sudo) "
                    "rights to put Ollama on this computer."):
                report.add(LEVEL_WARN, "mlstack",
                           "installer downloaded but declined; it stays in "
                           "{} and nothing was installed".format(script))
                return
            result = runner.run(["sh", str(script)], sudo=True, mutate=True)
            if result.returncode != 0:
                raise RunnerError("the Ollama installer failed (rc={}): "
                                  "{}".format(result.returncode,
                                              result.stderr.strip()))
            installed = True
            report.add(LEVEL_PASS, "mlstack", "Ollama installed")

    mem = parse_meminfo(runner.read_file("/proc/meminfo"))
    verdict, explanation = model_fit(tag, mem.get("MemTotal", 0))
    if verdict in ("refuse", "unknown"):
        report.add(LEVEL_WARN, "mlstack", explanation)
        return
    report.add(LEVEL_PASS if verdict == "fits" else LEVEL_WARN,
               "mlstack", explanation)
    echo("")
    echo(explanation)
    if dry_run:
        runner.run(["ollama", "pull", tag], mutate=True)
        return
    if not ctx["confirm"](
            "Download the {} AI model now? Model files are a few GB, so "
            "this can take a while.".format(tag)):
        report.add(LEVEL_WARN, "mlstack",
                   "model pull declined; run --apply mlstack again when "
                   "ready")
        return
    result = runner.run(["ollama", "pull", tag], mutate=True)
    if result.returncode != 0:
        raise RunnerError("ollama pull failed (rc={}): {}".format(
            result.returncode, result.stderr.strip()))
    report.add(LEVEL_PASS, "mlstack",
               "model {} downloaded; try it with: ollama run {}".format(
                   tag, tag))
