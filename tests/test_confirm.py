"""Tests for lib/confirm.py: per-action confirmation prompts (GUARDRAILS 5.6)."""

import unittest

from jetson_postboot.lib import confirm


class AskTests(unittest.TestCase):
    def test_yes_answers_true(self):
        for answer in ("y", "Y", "yes", " YES "):
            with self.subTest(answer=answer):
                self.assertTrue(
                    confirm.ask("Proceed?", input_fn=lambda _p, a=answer: a)
                )

    def test_no_and_empty_answer_false(self):
        for answer in ("n", "N", "no", "", "   "):
            with self.subTest(answer=answer):
                self.assertFalse(
                    confirm.ask("Proceed?", input_fn=lambda _p, a=answer: a)
                )

    def test_garbage_reprompts_until_valid(self):
        answers = iter(["maybe", "ok", "y"])
        outputs = []
        result = confirm.ask(
            "Proceed?", input_fn=lambda _p: next(answers), output=outputs.append
        )
        self.assertTrue(result)
        self.assertEqual(len(outputs), 2)

    def test_eof_declines(self):
        def raise_eof(_prompt):
            raise EOFError

        outputs = []
        self.assertFalse(
            confirm.ask("Proceed?", input_fn=raise_eof, output=outputs.append)
        )
        self.assertTrue(outputs)

    def test_prompt_shows_question_and_default_no_marker(self):
        prompts = []

        def record(prompt):
            prompts.append(prompt)
            return "y"

        confirm.ask("Disable zram?", input_fn=record)
        self.assertIn("Disable zram?", prompts[0])
        self.assertIn("[y/N]", prompts[0])


if __name__ == "__main__":
    unittest.main()
