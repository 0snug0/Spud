"""SPD-169: the home's CLAUDE.md fits one Bash-tool read beside the config.

A session that starts with `cat CLAUDE.md; cat spud.config.json` gets its output cut by the Bash tool at about 30,000
characters, and the home's CLAUDE.md alone had grown to about 37,000 -- after SPD-149 had split it once, with nothing to
stop it regrowing.  The `/spud` skill now says to read both files with the Read tool, and this module is the check that
keeps the file small whichever way a session reads it: the rendered `share/CLAUDE.md` at most 24,000 characters, and it
plus the rendered `share/spud.config.json` at most 29,000.  The fix for a failure is never to raise a budget; it is to
move detail -- histories, dated rulings, reasoning, long recipes -- into `share/skills/spud-reference/SKILL.md`, which
the file points to.

Characters are code points (`len` of the decoded text), what the Bash tool's cut counts.  The marks are rendered with
paths as long as a real machine's and longer, so the budget holds for a home somewhere other than the test's scratch.
"""

import json
import unittest

from helpers import CONFIG_MARKS, REPO, load_spud_module

spud = load_spud_module()

SHARE = REPO / "share"
CLAUDE_MD_BUDGET = 24000
WITH_CONFIG_BUDGET = 29000
FIX = "move detail (histories, dated rulings, reasoning, long recipes) into share/skills/spud-reference/SKILL.md"
# Machine-shaped values for the path marks, each longer than the ones a typical home carries, so the margin is honest.
HOME = "/home/an-account-with-a-long-name/Personal/SpudHome"
TOOL = "/home/an-account-with-a-long-name/Personal/Spud"
PROJECT_ROOT = "/home/an-account-with-a-long-name/Code/a-project-repository"


def values():
    """Every mark core/shipped names, with the suite's own identity, owner and prefixes and the long paths above."""
    with open(CONFIG_MARKS, encoding="utf-8") as f:
        fixture = {mark[2:-2]: value for mark, value in json.load(f).items()}
    filled = dict(fixture, launcher=TOOL + "/bin/spud", home=HOME, tool=TOOL, project_key="a-project",
                  project_root=PROJECT_ROOT, project_remote="https://github.com/an-account/a-project-repository.git",
                  identity_model="claude-fable-5-1",
                  memory_dir="~/.claude/projects/" + HOME.replace("/", "-").replace(".", "-") + "/memory")
    assert set(filled) == set(spud.MARKS), set(filled) ^ set(spud.MARKS)
    return filled


def rendered(relative):
    return spud.render((SHARE / relative).read_text(encoding="utf-8"), values())


class ClaudeMdBudgetTest(unittest.TestCase):
    def test_the_rendered_claude_md_is_within_its_budget(self):
        size = len(rendered("CLAUDE.md"))
        self.assertLessEqual(size, CLAUDE_MD_BUDGET,
                             "the rendered share/CLAUDE.md is %d characters, over its budget of %d (SPD-169): %s"
                             % (size, CLAUDE_MD_BUDGET, FIX))

    def test_the_claude_md_and_the_config_fit_one_bash_read_together(self):
        claude_md, config = len(rendered("CLAUDE.md")), len(rendered("spud.config.json"))
        self.assertLessEqual(claude_md + config, WITH_CONFIG_BUDGET,
                             "the rendered share/CLAUDE.md (%d) and share/spud.config.json (%d) come to %d characters, over"
                             " %d, and the Bash tool cuts output at about 30,000 (SPD-169): %s"
                             % (claude_md, config, claude_md + config, WITH_CONFIG_BUDGET, FIX))

    def test_the_claude_md_points_to_the_skill_the_detail_moved_to(self):
        text = rendered("CLAUDE.md")
        self.assertIn(".claude/skills/spud-reference/SKILL.md", text)
        self.assertIn("with the Read tool, never `cat`", text)


if __name__ == "__main__":
    unittest.main()
