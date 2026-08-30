"""A patch is not a fix until current tree fails, the patch holds, and pytest is green."""

from claimidx.discovery import ROOT

_NEEDLES = (
    "## Fix gate",
    "fails on the current tree",
    "python -m pytest -q",
    "not a miss",
    "A comment is not `eval.cmd`",
)


def test_contributing_fix_gate_is_the_ship_rule():
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    missing = [n for n in _NEEDLES if n not in text]
    assert missing == [], missing


def test_harness_briefs_repeat_the_fix_gate():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    copilot = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    for blob in (claude, copilot):
        assert "fail on the current tree" in blob
        assert "python -m pytest -q" in blob
        assert "eval.cmd" in blob
