"""core/shipped: the files the tool ships for a home, <tool>/share/, and the marks a command fills from Ctx."""

from . import homeconf, kernel


MARK = "{{%s}}"  # the syntax projects/agentdef's {{launcher}} established: no {{ appears in any shipped file's prose
NO_SHIPPED = "no shipped %s: the tool repository's share/ is the source"
# What the four owner marks read as in a home whose config carries no `owner` block, or one whose values are empty.
# A person's name cannot be guessed, so `spud init` refuses to write a config without one -- but a config
# that predates the block, or one edited by hand, must still render every shipped file rather than put `{{owner_name}}`
# or, worse, nothing at all into somebody's CLAUDE.md: a mark that renders empty is a file nobody can fix by hand once
# it is generated.  So the fallback is a placeholder that reads as one, and `commands/doctor` says what to set.
DEFAULT_OWNER = {"name": "the owner", "subject": "they", "object": "them", "possessive": "their"}
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
    # SPD-324: the one mark a home's own files do not read.  share/ledger/_templates/project.base carries it with
    # {{project_key}}, and commands/projectboards renders that template once per registered project, filling both with
    # that project's row -- the name as a YAML scalar there, quoted when a plain one would read as something else.
    "project_name": "project 1's name, or the empty string; a project board fills it with its own project's name",
    "project_root": "project 1's root_path, or the empty string",
    "project_remote": "the project root's origin URL, or -",
    "identity_name": "identity.name in the config",
    "identity_model": "identity.model in the config",
    "pronoun_subject": "identity.pronouns.subject",
    "pronoun_object": "identity.pronouns.object",
    "pronoun_possessive": "identity.pronouns.possessive",
    # The person the home works for, beside the identity of the one working.  Four marks rather than a name
    # and a pronoun triple in one, because the shipped prose spells all four separately -- "<name> is the person you
    # work for", "take <possessive> commands", "tell <object>" -- and each falls back to DEFAULT_OWNER on its own.
    "owner_name": "owner.name in the config, else DEFAULT_OWNER",
    "owner_subject": "owner.pronouns.subject, else DEFAULT_OWNER",
    "owner_object": "owner.pronouns.object, else DEFAULT_OWNER",
    "owner_possessive": "owner.pronouns.possessive, else DEFAULT_OWNER",
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
    four project marks are empty, which is what `init --no-project` leaves: the sentence that carries them is dropped by
    the command that writes the file, not here."""
    config = ctx.config
    identity = config.get("identity", {})
    pronouns = identity.get("pronouns", {})
    owner = config.get("owner") or {}
    owner_pronouns = owner.get("pronouns") or {}
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
        # dict(): the row may be a dict or an sqlite3.Row, and a caller's own dict may carry no name at all.
        "project_name": (dict(project).get("name") or "") if project is not None else "",
        "project_root": root,
        "project_remote": (root and homeconf.git_remote_url(root)) or "-",
        "identity_name": identity.get("name", "Spud"),
        "identity_model": identity.get("model", ""),
        "pronoun_subject": pronouns.get("subject", ""),
        "pronoun_object": pronouns.get("object", ""),
        "pronoun_possessive": pronouns.get("possessive", ""),
        # `or` rather than a default argument: a key present and empty is the same absence to a reader of the rendered
        # file, and neither may reach one as nothing at all.
        "owner_name": owner.get("name") or DEFAULT_OWNER["name"],
        "owner_subject": owner_pronouns.get("subject") or DEFAULT_OWNER["subject"],
        "owner_object": owner_pronouns.get("object") or DEFAULT_OWNER["object"],
        "owner_possessive": owner_pronouns.get("possessive") or DEFAULT_OWNER["possessive"],
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
