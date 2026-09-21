"""core/shipped: the files the tool ships for a home, <tool>/share/, and the marks a command fills from Ctx (SPW-001)."""

from . import homeconf, kernel


MARK = "{{%s}}"  # SPW-002's syntax, established by projects/agentdef's {{launcher}}: no {{ appears in any shipped file's prose
NO_SHIPPED = "no shipped %s: the tool repository's share/ is the source"
# Every mark a shipped file may carry, and where its value comes from.  The set is complete: a {{…}} under share/ that is
# not here is a typo that would ship unrendered into someone's home, and a mark here that no file uses is one left behind
# -- tests/test_share.py fails on either.  They stay distinct from the <…> forms in the shipped CLAUDE.md (<agent_id>,
# <lineage>, <slug> and twenty more), which are runtime placeholders Spud fills per child when he writes a brief: render
# is a plain replace of these names alone and leaves every one of those alone.
MARKS = {
    "launcher": "ctx.launcher: the running tool's bin/spud, in every command line the shipped prose spells",
    "home": "ctx.home",
    "tool": "ctx.tool: the checkout whose bin/spud runs",
    "ticket_prefix": "tickets.prefix in the config, which is project 1's",
    "team_prefix": "teams.prefix in the config, which is project 1's",
    "project_key": "project 1's key, or the empty string when no project is registered",
    "project_root": "project 1's root_path, or the empty string",
    "project_remote": "the project root's origin URL, or -",
    "identity_name": "identity.name in the config",
    "identity_model": "identity.model in the config",
    "pronoun_subject": "identity.pronouns.subject",
    "pronoun_object": "identity.pronouns.object",
    "pronoun_possessive": "identity.pronouns.possessive",
    "memory_dir": "the harness's memory directory for this home",
}


def share_dir(ctx):
    """<tool>/share/: the one directory for every file the tool ships for a home -- the config, CLAUDE.md, the vault
    scaffolding.  Read as data; nothing in bin/spudlib/ imports it."""
    return ctx.tool / "share"


def marks(ctx, project=None):
    """{mark: value} for this home and, when one is registered, this project row (the `projects` row of project 1).

    Every key of MARKS, so a rendered file keeps no mark.  The prefixes come from the config rather than from the row
    because doctor compares the two and the config is what `config sync` refreshes the row from.  With no project the
    three project marks are empty, which is what `init --no-project` leaves: the sentence that carries them is dropped by
    the command that writes the file, not here."""
    config = ctx.config
    identity = config.get("identity", {})
    pronouns = identity.get("pronouns", {})
    root = str(project["root_path"]) if project is not None else ""
    # Claude Code's own name for the memory directory of a project: ~/.claude/projects/, then the absolute path with every
    # slash and every dot written as a hyphen (this machine's home and this worktree both confirm the dot), then /memory.
    # It is a mark rather than a derivation in the template because no reader would reconstruct that transform correctly.
    memory = "~/.claude/projects/" + str(ctx.home).replace("/", "-").replace(".", "-") + "/memory"
    return {
        "launcher": str(ctx.launcher),
        "home": str(ctx.home),
        "tool": str(ctx.tool),
        "ticket_prefix": config.get("tickets", {}).get("prefix", ""),
        "team_prefix": config.get("teams", {}).get("prefix", ""),
        "project_key": project["key"] if project is not None else "",
        "project_root": root,
        "project_remote": (root and homeconf.git_remote_url(root)) or "-",
        "identity_name": identity.get("name", "Spud"),
        "identity_model": identity.get("model", ""),
        "pronoun_subject": pronouns.get("subject", ""),
        "pronoun_object": pronouns.get("object", ""),
        "pronoun_possessive": pronouns.get("possessive", ""),
        "memory_dir": memory,
    }


def render(text, values):
    """`text` with each {{mark}} in `values` replaced by its value: plain str.replace per mark and nothing more, so a
    shipped file is a file, not a template language, and anything that is not one of these marks passes through."""
    for mark, value in values.items():
        text = text.replace(MARK % mark, str(value))
    return text


def read(ctx, relative):
    """A shipped file's text, by its path under share/ (`CLAUDE.md`, `ledger/Home.md`).  Raises the way
    agentdef.agent_markdown does when the source is gone, which is how a command refuses before it writes anything."""
    path = share_dir(ctx) / relative
    if not path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_SHIPPED % path)
    return path.read_text(encoding="utf-8")
