"""projects/userskills: the user-scope skills project install writes beside /spud, rendered from the tool's share/.

`/spud-cleanup` and `/spud-autopilot` (SPD-335) are skills of every session on this machine, like `/spud`, so they
live at `~/.claude/skills/<name>/SKILL.md` and project install and sync keep them current.  Their text is a shipped
template, `share/user-skills/<name>/SKILL.md`, rather than a string here as `/spud`'s is (`projects/sessions.SKILL_STEPS`):
it is prose a person edits as prose, and its marks -- the launcher, the home, the identity and the owner -- are
core/shipped's.
Not under `share/skills/`, which `commands/homesync` copies into a home's own `.claude/skills/`: these belong at user
scope, never in a home, as `share/agents/spudagent.md` does."""

from pathlib import Path

from ..core import kernel, shipped


USER_SKILLS = ("spud-cleanup", "spud-autopilot")  # in the order install writes them and doctor names them
USER_SKILLS_DIR = "user-skills"  # under share/
NO_SKILL_SOURCE = "no %s to install at user scope: the tool repository's share/ is the source"
SKILL_NOT_RENDERED = "the shipped %s still carries a {{mark}} after rendering; core/shipped.MARKS and share/ have drifted"


def skill_source(ctx, name):
    """The tool's `share/user-skills/<name>/SKILL.md`: the template install renders."""
    return shipped.share_dir(ctx) / USER_SKILLS_DIR / name / "SKILL.md"


def skill_paths(skills_dir):
    """{skill name: its file} for every user skill install writes into `skills_dir` (`~/.claude/skills`)."""
    return {name: Path(skills_dir) / name / "SKILL.md" for name in USER_SKILLS}


def skill_texts(ctx):
    """[(skill name, text)] of every user skill, rendered with core/shipped's marks for this home (no project's: the
    skills name the project at run time, from their argument).  Raises when a source is gone or keeps a mark, so install
    refuses before it writes anything."""
    values = shipped.marks(ctx)
    texts = []
    for name in USER_SKILLS:
        source = skill_source(ctx, name)
        if not source.is_file():
            raise kernel.SpudError(kernel.EXIT_ERROR, NO_SKILL_SOURCE % source)
        text = shipped.render(source.read_text(encoding="utf-8"), values)
        if "{{" in text:  # a mark core/shipped does not name, which tests/test_share.py also fails on
            raise kernel.SpudError(kernel.EXIT_ERROR, SKILL_NOT_RENDERED % source)
        texts.append((name, text))
    return texts
