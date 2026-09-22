"""projects/agentdef: the spudagent definition project install writes, rendered from Ctx (SPW-002)."""

from pathlib import Path

from ..core import kernel, shipped


# The one machine-specific value the shipped definition leaves to install (SPW-002).  Spelled out rather than read from
# core/shipped at import time; tests/test_share.py holds it equal to `shipped.MARK % "launcher"`, whose mark table owns
# the syntax and the name.
LAUNCHER_MARK = "{{launcher}}"
NO_SOURCE = "no %s to install at user scope: the tool repository's spudagent definition is the source"
# Where Claude Code reads a project's own agent definitions, and in preference to the user-scope copy install writes
# (SPW-004).  Nothing here ever writes it: it is named so that doctor can report one and say which definition wins.
PROJECT_SCOPE_REL = ".claude/agents/spudagent.md"


def agent_source(ctx):
    """The tool repository's `share/agents/spudagent.md`: the template install renders and doctor reads (SPD-097).

    Under share/ with the files of SPW-001 because that is what it is -- a template whose {{launcher}} core/shipped fills
    from Ctx -- and never again at the tool repository's own `.claude/agents/`, where SPW-004 found it: Claude Code reads
    a project-scope definition in preference to the installed user-scope one, so a session in that checkout got the
    template itself, launcher mark and all, rather than the copy install rendered for this machine.  A file full of
    {{marks}} was never a usable agent definition; sitting there it only shadowed the one that was."""
    return shipped.share_dir(ctx) / "agents" / "spudagent.md"


def project_scope_agent(root):
    """`<root>/.claude/agents/spudagent.md`: the definition a session launched in that checkout reads instead of the copy
    install writes at user scope (SPW-004).  A path, existing or not -- whether one is there is doctor's question."""
    return Path(root) / PROJECT_SCOPE_REL


def render_launcher(text, launcher):
    """The template with every {{launcher}} replaced by that launcher's path, through core/shipped's one substitution."""
    return shipped.render(text, {"launcher": launcher})


def agent_markdown(ctx):
    """What project install writes to ~/.claude/agents/spudagent.md, the way sessions.skill_markdown makes the /spud skill:
    the tool repository's definition with the launcher that actually runs filled in, so the repository ships no machine's
    absolute path and every installed copy names its own machine's `bin/spud` (SPW-002).  The home and the project roots
    stay out of it: the definition already names them as what `spud session show` prints.  Raises when the source is gone,
    which is how install refuses before it writes anything and how doctor reports the gap.

    It reads the file here rather than through core/shipped.read, whose refusal names share/ as the source of a home's own
    files: this is the one shipped file no home holds a copy of, so its refusal names user scope instead (NO_SOURCE)."""
    source = agent_source(ctx)
    if not source.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_SOURCE % source)
    return render_launcher(source.read_text(encoding="utf-8"), ctx.launcher)
