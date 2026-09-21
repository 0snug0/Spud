"""projects/agentdef: the spudagent definition project install writes, rendered from Ctx (SPW-002)."""

from ..core import kernel


LAUNCHER_MARK = "{{launcher}}"  # SPW-002: the one machine-specific value the shipped definition leaves to install
NO_SOURCE = "no %s to install at user scope: the tool repository's spudagent definition is the source"


def agent_source(ctx):
    """The tool repository's `.claude/agents/spudagent.md`: the template install renders and doctor reads (SPD-097)."""
    return ctx.tool / ".claude" / "agents" / "spudagent.md"


def render_launcher(text, launcher):
    """The template with every {{launcher}} replaced by that launcher's path."""
    return text.replace(LAUNCHER_MARK, str(launcher))


def agent_markdown(ctx):
    """What project install writes to ~/.claude/agents/spudagent.md, the way sessions.skill_markdown makes the /spud skill:
    the tool repository's definition with the launcher that actually runs filled in, so the repository ships no machine's
    absolute path and every installed copy names its own machine's `bin/spud` (SPW-002).  The home and the project roots
    stay out of it: the definition already names them as what `spud session show` prints.  Raises when the source is gone,
    which is how install refuses before it writes anything and how doctor reports the gap."""
    source = agent_source(ctx)
    if not source.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_SOURCE % source)
    return render_launcher(source.read_text(encoding="utf-8"), ctx.launcher)
