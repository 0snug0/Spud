"""projects/agentdef: the spudagent definitions project install writes, rendered from Ctx: the base and one per effort."""

from pathlib import Path

from ..core import kernel, shipped


# The one machine-specific value the shipped definition leaves to install.  Spelled out rather than read from
# core/shipped at import time; tests/test_share.py holds it equal to `shipped.MARK % "launcher"`, whose mark table owns
# the syntax and the name.
LAUNCHER_MARK = "{{launcher}}"
NO_SOURCE = "no %s to install at user scope: the tool repository's spudagent definition is the source"
FENCE = "---"
NOT_A_BASE = "%s is not a base spudagent definition the effort variants can be rendered from: %s"


def agent_source(ctx):
    """The tool repository's `share/agents/spudagent.md`: the template install renders and doctor reads.

    Under share/ with the other shipped templates because that is what it is -- a template whose {{launcher}} core/shipped
    fills from Ctx -- and never again at the tool repository's own `.claude/agents/`, where it once sat: Claude Code reads
    a project-scope definition in preference to the installed user-scope one, so a session in that checkout got the
    template itself, launcher mark and all, rather than the copy install rendered for this machine.  A file full of
    {{marks}} was never a usable agent definition; sitting there it only shadowed the one that was."""
    return shipped.share_dir(ctx) / "agents" / "spudagent.md"


def project_scope_agent(root, name=kernel.SPUDAGENT):
    """`<root>/.claude/agents/<name>.md`: the definition a session launched in that checkout reads instead of the copy
    install writes at user scope, since Claude Code prefers a project's own agent definitions.  Nothing here ever writes
    one: a path, existing or not, named so that doctor can report one and say which definition wins."""
    return Path(root) / ".claude" / "agents" / (name + ".md")


def agent_paths(agents_dir):
    """{agent type: its file} for every definition install writes into `agents_dir`, the base first and then one per
    effort level in kernel.EFFORTS order: `spudagent.md`, `spudagent-low.md` ... `spudagent-max.md`."""
    return {name: Path(agents_dir) / (name + ".md") for name in kernel.SPUDAGENT_TYPES}


def render_launcher(text, launcher):
    """The template with every {{launcher}} replaced by that launcher's path, through core/shipped's one substitution."""
    return shipped.render(text, {"launcher": launcher})


def agent_markdown(ctx):
    """What project install writes to ~/.claude/agents/spudagent.md, the way sessions.skill_markdown makes the /spud skill:
    the tool repository's definition with the launcher that actually runs filled in, so the repository ships no machine's
    absolute path and every installed copy names its own machine's `bin/spud`.  The home and the project roots
    stay out of it: the definition already names them as what `spud session show` prints.  Raises when the source is gone,
    which is how install refuses before it writes anything and how doctor reports the gap.

    It reads the file here rather than through core/shipped.read, whose refusal names share/ as the source of a home's own
    files: this is the one shipped file no home holds a copy of, so its refusal names user scope instead (NO_SOURCE)."""
    source = agent_source(ctx)
    if not source.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_SOURCE % source)
    return render_launcher(source.read_text(encoding="utf-8"), ctx.launcher)


def variant_markdown(base, effort, where="the spudagent definition"):
    """The base definition as `spudagent-<effort>` (SPD-222): the same body, and the same frontmatter with its own `name`
    and `effort: <effort>` after `model` (last, when there is no model line).  The Agent tool takes a model and no effort,
    and a definition's `effort:` beats the spawning session's level, so a member planned at an effort is spawned as the
    variant that names it; the base names none and runs at the session's level.

    Raises when `base` is not a base: no frontmatter, no `name: spudagent` line, or an `effort:` of its own -- which would
    make the base run at that level instead of the session's and give every variant two lines saying different things."""
    lines = base.split("\n")
    if not lines or lines[0] != FENCE or FENCE not in lines[1:]:
        raise kernel.SpudError(kernel.EXIT_ERROR, NOT_A_BASE % (where, "it has no --- frontmatter block"))
    end = lines.index(FENCE, 1)
    front = lines[1:end]
    keys = [line.partition(":")[0].strip() for line in front]
    name_line = "name: %s" % kernel.SPUDAGENT
    if name_line not in (line.strip() for line in front):
        raise kernel.SpudError(kernel.EXIT_ERROR, NOT_A_BASE % (where, "its frontmatter has no `%s` line" % name_line))
    if "effort" in keys:
        raise kernel.SpudError(kernel.EXIT_ERROR, NOT_A_BASE % (where, "its frontmatter sets an effort, and the base sets none"
                                                                  " so that it runs at the spawning session's level"))
    out = []
    for line, key in zip(front, keys):
        out.append("name: %s-%s" % (kernel.SPUDAGENT, effort) if line.strip() == name_line else line)
        if key == "model":
            out.append("effort: %s" % effort)
    if "model" not in keys:
        out.append("effort: %s" % effort)
    return "\n".join([FENCE] + out + lines[end:])


def definitions(ctx):
    """[(agent type, text)] of every definition install writes at user scope, in agent_paths' order: the base as
    agent_markdown renders it, then a variant of it per effort level.  Raises as agent_markdown does, and when the source
    is not a base, so install refuses before it writes any of them."""
    base = agent_markdown(ctx)
    where = str(agent_source(ctx))
    return [(kernel.SPUDAGENT, base)] + [(name, variant_markdown(base, effort, where))
                                         for name, effort in zip(kernel.SPUDAGENT_VARIANTS, kernel.EFFORTS)]
