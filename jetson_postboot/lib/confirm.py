"""Per-action confirmation prompts (GUARDRAILS 5.6: every mutation confirms).

Default answer is always no: empty input declines, and EOF (non-interactive
stdin) declines, so unattended runs can never mutate anything.
"""


def ask(prompt, input_fn=None, output=print):
    """Return True only on an explicit yes; re-prompt on anything ambiguous."""
    if input_fn is None:
        # Resolved at call time, not bound as a default, so tests can patch
        # builtins.input and never block on a real prompt.
        input_fn = input
    while True:
        try:
            answer = input_fn("{} [y/N] ".format(prompt))
        except EOFError:
            output("No interactive input available; treating as 'no'.")
            return False
        answer = answer.strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", ""):
            return False
        output("Please answer 'y' or 'n'.")
