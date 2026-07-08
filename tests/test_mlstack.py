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
        ollama = [f for f in self.report.findings if "Ollama" in f.message]
        self.assertEqual(len(ollama), 1)
        self.assertEqual(ollama[0].level, LEVEL_PASS)
        self.assertIn("not installed", ollama[0].message)

    def test_check_suggests_a_model_for_this_board(self):
        mlstack.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("qwen2.5:3b", text)
        self.assertIn("free for", text)


class ModelFitTests(unittest.TestCase):
    """PLAN 6.6: Q4 estimate = params_B x 0.65 GiB + 1.5 GiB overhead; on the
    8 GB board roughly: up to 4B comfortable, 7-8B tight, larger refused."""

    MEM_TOTAL = 7802760 * 1024  # the captured board's MemTotal

    def test_params_parsed_from_common_tags(self):
        cases = [
            ("qwen2.5:3b", 3.0),   # the 2.5 in the name must not match
            ("llama3:8b", 8.0),
            ("llama3:8b-instruct-q4_0", 8.0),
            ("qwen2.5:0.5b", 0.5),
            ("gemma2:26b", 26.0),
        ]
        for tag, expected in cases:
            with self.subTest(tag=tag):
                self.assertEqual(mlstack.parse_model_params(tag), expected)

    def test_unparseable_tags_return_none(self):
        for tag in ("mistral:latest", "codellama", "llava:34"):
            with self.subTest(tag=tag):
                self.assertIsNone(mlstack.parse_model_params(tag))

    def test_estimate_formula(self):
        expected = int((3 * 0.65 + 1.5) * (1 << 30))
        self.assertEqual(mlstack.estimate_resident_bytes(3.0), expected)

    def test_verdicts_on_the_8gb_board(self):
        cases = [("qwen2.5:3b", "fits"), ("llama3.1:4b", "fits"),
                 ("llama3:7b", "tight"), ("llama3:8b", "tight"),
                 ("gemma2:26b", "refuse"), ("mystery:latest", "unknown")]
        for tag, expected in cases:
            with self.subTest(tag=tag):
                verdict, explanation = mlstack.model_fit(tag, self.MEM_TOTAL)
                self.assertEqual(verdict, expected, explanation)
                self.assertTrue(explanation)

    def test_suggestion_for_the_8gb_board_is_3b_with_headroom(self):
        # Munta 2026-07-08 (Q2): suggest from detected memory, keeping the
        # model within half of RAM so the rest stays free for other work.
        params, tag, explanation = mlstack.suggest_model(self.MEM_TOTAL)
        self.assertEqual(params, 3)
        self.assertEqual(tag, "qwen2.5:3b")
        self.assertIn("free", explanation)

    def test_suggestion_scales_with_memory(self):
        gib = 1 << 30
        cases = [(4 * gib, "qwen2.5:0.5b"), (16 * gib, "qwen2.5:7b"),
                 (64 * gib, "qwen2.5:32b")]
        for mem_total, expected in cases:
            with self.subTest(mem_total=mem_total):
                _params, tag, _explanation = mlstack.suggest_model(mem_total)
                self.assertEqual(tag, expected)

    def test_tiny_memory_suggests_nothing_and_says_why(self):
        params, tag, explanation = mlstack.suggest_model(1 << 30)
        self.assertIsNone(params)
        self.assertIsNone(tag)
        self.assertIn("memory", explanation)

    def test_refusal_explains_the_ceiling_in_plain_words(self):
        # PLAN G6: a high-school student must understand why it said no.
        verdict, explanation = mlstack.model_fit("gemma2:26b", self.MEM_TOTAL)
        self.assertEqual(verdict, "refuse")
        self.assertIn("memory", explanation)
        self.assertIn("26", explanation)
        # names the largest class that would still run comfortably
        self.assertRegex(explanation, r"\dB")


class ApplyTests(unittest.TestCase):
    """--apply mlstack: download-show-confirm-install, then a fit-checked
    model pull. Every prompt and refusal in plain language (PLAN G6)."""

    def setUp(self):
        self.work = make_work_dir(self)
        self.echoed = []
        self.calls = []
        self.fetched_urls = []

    def make_runner(self, fixture_dir, dry_run=False):
        runner = Runner(log_dir=self.work / "logs", fixture_dir=fixture_dir,
                        dry_run=dry_run, downloads_dir=self.work / "downloads",
                        echo=self.echoed.append)
        original = runner.run

        def spying_run(argv, **kwargs):
            self.calls.append(list(argv))
            return original(argv, **kwargs)

        runner.run = spying_run
        return runner

    def run_apply(self, fixture, confirms, model=None, dry_run=False,
                  fetch=None):
        answers = list(confirms)
        self.prompts = []

        def confirm(prompt):
            self.prompts.append(prompt)
            return answers.pop(0) if answers else False

        def default_fetch(url):
            self.fetched_urls.append(url)
            return (FIXTURES / "ollama-install.sh").read_bytes()

        runner = self.make_runner(fixture, dry_run=dry_run)
        report = Report(mode="simulate", tool_version="test")
        ctx = {
            "downloads_dir": self.work / "downloads",
            "echo": self.echoed.append,
            "confirm": confirm,
            "dry_run": dry_run,
            "model": model,
            "fetch": fetch if fetch is not None else default_fetch,
        }
        mlstack.apply(runner, report, ctx)
        return report

    def issued(self, *prefix):
        prefix = list(prefix)
        return [argv for argv in self.calls if argv[:len(prefix)] == prefix]

    def test_install_and_default_pull_happy_path(self):
        report = self.run_apply(FIXTURES, confirms=[True, True, True])
        text = report.render_text()
        self.assertEqual(self.fetched_urls, [mlstack.OLLAMA_INSTALL_URL])
        script = self.work / "downloads" / "ollama-install.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(self.issued("sh", str(script)), text)
        # no --model given: the hardware-based suggestion is the default
        self.assertTrue(self.issued("ollama", "pull", "qwen2.5:3b"), text)
        joined = "\n".join(self.echoed)
        self.assertIn("sha256", joined)
        self.assertRegex(joined, r"\d+ bytes")
        self.assertIn("free for", text)
        self.assertIn("fits comfortably", text)

    def test_oversize_model_refused_without_pull(self):
        report = self.run_apply(FIXTURES, confirms=[True, True, True],
                                model="gemma2:26b")
        text = report.render_text()
        self.assertFalse(self.issued("ollama"))
        self.assertIn("out of memory", text)
        self.assertIn("qwen2.5:3b instead", text)
        self.assertEqual(report.exit_code(), 1)

    def test_declined_download_fetches_and_installs_nothing(self):
        report = self.run_apply(FIXTURES, confirms=[False])
        self.assertEqual(self.fetched_urls, [])
        self.assertFalse(self.issued("sh"))
        self.assertFalse(self.issued("ollama"))
        self.assertIn("declined", report.render_text())

    def test_dry_run_prints_sequence_without_fetching_or_prompting(self):
        def forbidden_fetch(url):
            raise AssertionError("dry-run must not touch the network")

        self.run_apply(FIXTURES, confirms=[], dry_run=True,
                       fetch=forbidden_fetch)
        joined = "\n".join(self.echoed)
        self.assertIn("DRY-RUN would execute: sudo sh", joined)
        self.assertIn("DRY-RUN would execute: ollama pull qwen2.5:3b", joined)
        self.assertIn("ollama.com", joined)  # says what it would download
        self.assertEqual(self.prompts, [])

    def test_installed_board_skips_install_and_pulls(self):
        import json as json_mod
        fixture = self.work / "fixture-installed"
        fixture.mkdir()
        (fixture / "which-ollama.txt").write_text(
            "/usr/local/bin/ollama\n", encoding="utf-8")
        (fixture / "meminfo.txt").write_text(
            (FIXTURES / "meminfo.txt").read_text(encoding="utf-8"),
            encoding="utf-8")
        manifest = {
            "commands": {
                "which ollama": "which-ollama.txt",
                "ollama pull qwen2.5:3b": {"returncode": 0},
            },
            "files": {"/proc/meminfo": "meminfo.txt"},
        }
        (fixture / "manifest.json").write_text(
            json_mod.dumps(manifest), encoding="utf-8")
        report = self.run_apply(fixture, confirms=[True])
        self.assertEqual(self.fetched_urls, [])
        self.assertFalse(self.issued("sh"))
        self.assertTrue(self.issued("ollama", "pull", "qwen2.5:3b"),
                        report.render_text())
        self.assertIn("already installed", report.render_text())


if __name__ == "__main__":
    unittest.main()
