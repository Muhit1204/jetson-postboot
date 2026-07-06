"""Tests for modules/mlstack.py Tier 0 detection (Phase 1).

The captured board has the CUDA 12.6 toolkit installed but nvcc NOT on PATH
(rc 127 in the fixture), cuDNN 9.3 present, and Ollama absent - a realistic
newcomer state (PLAN problem statement 3). nvcc-version-fullpath.txt is real
output captured via /usr/local/cuda/bin/nvcc on the same board; it feeds the
version parser only and is not a manifest command (the tool invokes bare
`nvcc` per the GUARDRAILS allowlist). Install and model-fit apply are
Phase 3.
"""

import unittest

from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN, Report
from jetson_postboot.lib.runner import Runner
from jetson_postboot.modules import mlstack
from tests.support import REPO_ROOT, make_work_dir

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "orin-nano-8gb"


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class DpkgCudnnParserTests(unittest.TestCase):
    def test_fixture_packages_parsed(self):
        packages = mlstack.parse_dpkg_cudnn(fixture_text("dpkg-cudnn.txt"))
        self.assertIn(("libcudnn9-cuda-12", "9.3.0.75-1"), packages)
        self.assertEqual(len(packages), 5)

    def test_only_installed_ii_lines_count(self):
        text = ("rc  libcudnn8  8.9.4  arm64  removed leftover\n"
                "ii  libcudnn9-cuda-12  9.3.0.75-1  arm64  cuDNN runtime\n")
        self.assertEqual(mlstack.parse_dpkg_cudnn(text),
                         [("libcudnn9-cuda-12", "9.3.0.75-1")])

    def test_no_match_returns_empty(self):
        self.assertEqual(mlstack.parse_dpkg_cudnn("ii bash 5.1 arm64 shell\n"), [])


class NvccReleaseParserTests(unittest.TestCase):
    def test_real_captured_output(self):
        release = mlstack.parse_nvcc_release(
            fixture_text("nvcc-version-fullpath.txt"))
        self.assertEqual(release, "12.6, V12.6.68")

    def test_garbage_returns_none(self):
        self.assertIsNone(mlstack.parse_nvcc_release("command not found"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.runner = Runner(log_dir=self.work / "logs", fixture_dir=FIXTURES)
        self.report = Report(mode="simulate", tool_version="test")

    def test_missing_nvcc_warns_with_path_hint(self):
        mlstack.check(self.runner, self.report)
        warns = [f for f in self.report.findings if f.level == LEVEL_WARN]
        self.assertTrue(any("nvcc" in f.message for f in warns),
                        self.report.render_text())
        self.assertIn("PATH", self.report.render_text())

    def test_cuda_stack_summarized(self):
        mlstack.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("cuda-12.6", text)
        self.assertIn("libcudnn9-cuda-12", text)
        self.assertIn("9.3.0.75-1", text)

    def test_nvidia_smi_na_explained(self):
        mlstack.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("nvidia-smi", text)
        self.assertIn("N/A", text)
        self.assertIn("not a fault", text)

    def test_ollama_absence_is_informational(self):
        mlstack.check(self.runner, self.report)
        ollama = [f for f in self.report.findings if "llama" in f.message]
        self.assertEqual(len(ollama), 1)
        self.assertEqual(ollama[0].level, LEVEL_PASS)
        self.assertIn("not installed", ollama[0].message)


if __name__ == "__main__":
    unittest.main()
