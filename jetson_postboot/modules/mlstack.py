"""ML stack module: CUDA truth report (Tier 0, Phase 1); Ollama install and
model-fit advisor arrive in Phase 3 (PLAN.md 6.6).

Detection reads nvcc, the /usr/local/cuda symlink, dpkg's cuDNN entries, and
Ollama presence. It states explicitly that nvidia-smi reporting N/A is
expected on Jetson integrated GPUs, because that is the single most common
false alarm for newcomers (PLAN problem statement 3).
"""

import re

from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN
from jetson_postboot.lib.runner import SimulationMissError

_CUDA_SYMLINK = "/usr/local/cuda"


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
