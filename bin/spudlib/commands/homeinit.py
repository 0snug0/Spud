"""commands/homeinit: `spud init`, one command from a fresh clone to a working Spud (SPW-001,
docs/design/2026-09-21-spud-init.md sections 2 and 6).

Steps 1 to 5 of the design's ten: the home directory and the config, the database, the first project with one report
entry, the vault scaffolding, the home pointer.  Steps 6 to 10 -- `settings sync`, `project install`, the two
LaunchAgents, the render and doctor -- are `finish_install` and `verify`, which land with phase 4; until they do, `init`
prints them as the lines left by hand, and `--no-schedule` arrives with the step it skips rather than shipping as a flag
that does nothing.

**Kept whole, and past 250 lines on purpose.**  ~250 is the look-again point, never a cap (`.claude/skills/spudlib-modules`
section 6), and the argument here is `commands/homemove`'s own: this is one procedure whose every step reads what the ones
before it did.  Step 1 decides what the config says; step 2 will not open a database until that config has cleared
`config_problems`; step 3's `INSERT` carries the prefixes step 1 wrote, because doctor compares the two; step 4 renders
prose from the config step 1 wrote and the project row step 3 inserted, and must not run before step 3, so that no
`CLAUDE.md` describes a project whose `INSERT` then failed on a `UNIQUE` constraint; step 5 writes the pointer only once
the four before it made the directory a home.  Cutting that sequence at any point would give two modules that may only
ever be called in one order, with the order itself written nowhere.  The seam that is real is the install tail, and
phase 4 takes it as its own two functions.

Two properties an edit here must keep:

- **Init resolves no actor** (design 2.3).  `state/actors.resolve_actor(con, None)` refuses outright and `require_spud`
  refuses an unclaimed session in a `claim` project -- which is exactly the session init creates when it registers
  someone's repository as project 1.  So `--as` is checked lexically, every event and the report entry are written under
  the literal actor label `spud` the way `homemove.move_resync` writes them, and `reportentry.check_next` (which goes
  through `require_spud`) is never called: init validates `--next` itself.
- **`bin/spud_ledger.main` resolves the home for every other command** and `init` is the one that may run without one,
  through `init_ctx` in main's own branch.  `core/homeconf.resolve_home` is untouched, and nothing here joins the hook path.
"""

import os
import re
import sys
from pathlib import Path

from . import reportentry
from ..core import homeconf, kernel, shipped
from ..projects import registry
from ..state import ledgerdb, schema

# The default home the prompt offers when neither --home, nor SPUD_HOME, nor the pointer names one: Eric's call
# (design section 10), made 2026-09-21.  Shortest of the four candidates, outside iCloud Drive -- a SQLite database
# with a WAL in a synced directory is a silent corruption risk -- and a plain directory a person can open in Obsidian.
DEFAULT_HOME = "~/SpudHome"
DEFAULT_NAME = "Spud"
DEFAULT_PRONOUNS = "he/him/his"
CONFIG_NAME = "spud.config.json"
# Step 4's seven files and five directories, in the order the design lists them (section 2.2).  Each relative path is
# both the path under <tool>/share/ and the path in the home: share/ mirrors the home's own layout.
SCAFFOLDING = ("CLAUDE.md", "ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base",
               "ledger/_templates/ticket.md", "ledger/_templates/spudagent.md")
DIRECTORIES = ("ledger/tickets", "ledger/teams", "reports", "docs/spikes", "docs/design")
# The three marks that have no value in a home with no project (core/shipped.marks leaves them empty).  A line carrying
# one is a line about project 1, so with no project registered write_scaffolding leaves that line out and says how many
# it left out: the shipped prose is share/'s to write and this command's to place (shipped.marks' own docstring).
PROJECT_MARKS = ("project_key", "project_root", "project_remote")
MARK_LEFT = re.compile(r"\{\{[a-z_]+\}\}")

NOT_SPUD = ("init resolves no actor and `--as %s` names one: the first run has no database and no members table for"
            " anyone to own anything in, and `init` is Spud's own (hooks/hookio.SPUD_ONLY_COMMANDS)."
            "  Run `spud init` or `spud init --as spud`.")
NO_HOME = ("no home to build: give `--home <dir>` (%s is the usual choice), or set SPUD_HOME."
           "  %s names none, so there is nothing to continue.")
POINTER_ELSEWHERE = ("%s names %s, not %s; a machine has one pointer and repointing it silently orphans a working home."
                     "  Rerun with `--home %s` to work on that home, or with `--repoint` to point this machine here instead.")
SPUD_HOME_ELSEWHERE = ("SPUD_HOME is %s, not %s; after init the shell's SPUD_HOME beats the pointer in resolve_home, so"
                       " you would be working on a different home than the one init just built.  Unset it, or rerun with `--home %s`.")
IN_WORK_TREE = "%s is inside a git work tree (%s); the home is a plain directory (SPD-097), and a home inside a repository puts the vault into somebody's history"
TOOL_IS_WORKTREE = ("the running bin/spud is in a linked worktree (%s); run the main checkout's.  Init writes that path into"
                    " this home's CLAUDE.md, and a worktree is deleted when its ticket lands.")
NOT_A_HOME = "%s is not an empty directory and is no Spud home (no %s and no .spud/): it holds %s"
PREFIX_DIFFERS = ("%s %s differs from the %s already in %s, which carries %s; init leaves a config it finds alone (design section 6)"
                  " and doctor compares project 1's prefixes with the config's.  Rerun with `%s %s`, or edit the config first.")
NO_PREFIXES = ("%s has no %s yet, and a config carries the two prefixes: give `--ticket-prefix XXX --team-prefix XXXS`."
               "  They have no default on purpose -- a prefix is in every rendered file name and every wikilink forever,"
               " and `project edit` refuses to change one once the project has a ticket.")
CONFIG_IS_THEIRS = "%s is already there and init leaves it alone (design section 6), so %s has nothing to write; edit the file instead"
NOT_RENDERED = "the shipped %s still carries %s after rendering; core/shipped.MARKS and <tool>/share/ have drifted (tests/test_share.py is the guard)"
LEFT_BEHIND = ("nothing init wrote is removed: %s is as the steps above left it, and a rerun continues from there,"
               " because every step is idempotent by content")
INIT_BY_HAND = """by hand, now (steps 6 to 10, which `finish_install` takes over in phase 4):
  - %(launcher)s --as spud settings sync%(install)s
  - %(launcher)s --as spud schedule install
  - %(launcher)s --as spud render
  - %(launcher)s doctor
  - open %(home)s as a vault in Obsidian, and start a Claude Code session there: it is Spud's"""


# ----------------------------------------------------------------------------
# The home, before there is one
# ----------------------------------------------------------------------------


def ask(args, question, default=None):
    """One prompt of design section 3.3, or `default`: the interface is flags and the prompt is a convenience, so a value
    not given takes its default whenever stdin is not a tty or `--yes` was given.  `input` is a builtin, which costs the
    `python3.14 -I -S` launcher nothing."""
    if args.yes or not sys.stdin.isatty():
        return default
    answer = input("%s%s: " % (question, "" if default is None else " [%s]" % default)).strip()
    return answer or default


def ask_offered(args, question, default):
    """A prompt whose default is *offered* rather than taken: the home is the one value with a default that a run off a
    tty must not silently use (design section 10 -- `~/SpudHome` is a convention, and a person told which directory they
    are about to fill is a person who can say no), so away from a prompt the refusal names `--home` instead."""
    return ask(args, question, default) if not args.yes and sys.stdin.isatty() else None


def home_path(value):
    """A path the way `homeconf.resolve_home` returns one, so the pointer init writes resolves back to the same string."""
    return Path(str(value)).expanduser().resolve()


def init_ctx(env, args):
    """The home `spud init` will build, and how it was found: `--home`, then SPUD_HOME, then the existing pointer, then
    DEFAULT_HOME offered as a prompt when stdin is a tty, then the usage refusal naming `--home` (design section 10).

    `bin/spud_ledger.main` calls this instead of `homeconf.resolve_home` for `init` alone (design 2.3): resolve_home
    raises on a machine with no SPUD_HOME and no pointer, which is every fresh machine, and `init` is the command that
    has to run there.  Returns (path, how), which main hands to `homeconf.Ctx`.
    """
    if args.home:
        return home_path(args.home), "--home"
    if env.get("SPUD_HOME"):
        return home_path(env["SPUD_HOME"]), "SPUD_HOME"
    pointer = homeconf.spud_config_dir(env) / "home"
    if pointer.is_file():
        named = pointer.read_text(encoding="utf-8").strip()
        if named:
            return home_path(named), "~/.config/spud/home"
    answer = ask_offered(args, "Spud's home", DEFAULT_HOME)
    if answer:
        return home_path(answer), "the prompt"
    raise kernel.SpudError(kernel.EXIT_USAGE, NO_HOME % (DEFAULT_HOME, pointer))


def check_actor(args):
    """`--as`, read lexically: absent or `spud` proceeds, anything else is refused (design 2.3, and the reason the whole
    module writes the literal actor label `spud` rather than resolving one)."""
    if args.actor is not None and args.actor != "spud":
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, NOT_SPUD % args.actor)


def check_next_line(args):
    """`--next`, validated here rather than by `reportentry.check_next`, which goes through `require_spud` (design 2.3)."""
    if args.next is not None and not args.next.strip():
        raise kernel.SpudError(kernel.EXIT_USAGE, "--next is empty: give the Next line, or leave --next out")


# ----------------------------------------------------------------------------
# What init was told, resolved once
# ----------------------------------------------------------------------------


def project_key_for(root):
    """The `--project-key` default of design section 3.3: the root's directory name, lower-cased and sanitized towards
    `registry.PROJECT_KEY_RE`.  A name that cannot become a key is not guessed at -- the refusal names `--project-key`."""
    return re.sub(r"[^a-z0-9-]+", "-", os.path.basename(str(root)).lower()).strip("-")


def pronouns_of(args):
    """`--pronouns subject/object/possessive`, default DEFAULT_PRONOUNS: the three values the config and the shipped
    prose carry."""
    value = args.pronouns or ask(args, "Pronouns (subject/object/possessive)", DEFAULT_PRONOUNS) or DEFAULT_PRONOUNS
    parts = [p.strip() for p in value.split("/")]
    if len(parts) != 3 or not all(parts):
        raise kernel.SpudError(kernel.EXIT_USAGE, "--pronouns is subject/object/possessive, three words separated by slashes (%r)" % value)
    return parts


def init_plan(ctx, args):
    """Every value init needs, resolved once: the identity, the two prefixes, and the first project's root, key and name.

    The prompts of design section 3.3 run here and nowhere else, which is why `cmd_init` builds this once and hands it to
    `init_preconditions` and `init_steps` rather than letting each resolve its own -- a person must be asked once.

    Two rules the design's section 3 implies and this makes explicit, both about a config already on disk, which init
    leaves alone entirely (section 6): the identity flags have nothing to write and are refused; and the config's
    prefixes are authoritative, so a prefix flag that differs is refused and one that is absent is taken from the file.
    Doctor compares project 1's prefixes with the config's, so the two must agree the moment init writes them both.
    """
    config = ctx.config if ctx.config_path.is_file() else None  # Ctx.config raises when the file is absent, so ask first
    plan = {"config_exists": config is not None}
    if config is None:
        plan["name"] = args.name or ask(args, "Spud's name", DEFAULT_NAME) or DEFAULT_NAME
        plan["pronouns"] = pronouns_of(args)
        ticket_prefix = args.ticket_prefix or ask(args, "Ticket prefix (SPD gives ticket SPD-001)")
        team_prefix = args.team_prefix or ask(args, "Team prefix (SPUD gives team SPUD-001)")
        if not ticket_prefix or not team_prefix:
            raise kernel.SpudError(kernel.EXIT_USAGE, NO_PREFIXES % (ctx.home, CONFIG_NAME))
    else:
        given = [flag for flag, value in (("--name", args.name), ("--pronouns", args.pronouns)) if value]
        if given:
            raise kernel.SpudError(kernel.EXIT_USAGE, CONFIG_IS_THEIRS % (ctx.config_path, " and ".join(given)))
        identity = config.get("identity", {})
        plan["name"] = identity.get("name", DEFAULT_NAME)
        plan["pronouns"] = [identity.get("pronouns", {}).get(k, "") for k in ("subject", "object", "possessive")]
        ticket_prefix = config.get("tickets", {}).get("prefix")
        team_prefix = config.get("teams", {}).get("prefix")
        for flag, value, have in (("--ticket-prefix", args.ticket_prefix, ticket_prefix), ("--team-prefix", args.team_prefix, team_prefix)):
            if value is not None and value != have:
                raise kernel.SpudError(kernel.EXIT_USAGE, PREFIX_DIFFERS % (flag, value, CONFIG_NAME, ctx.config_path, have, flag, have))
    plan["ticket_prefix"], plan["team_prefix"] = ticket_prefix, team_prefix
    project_flags = [f for f, v in (("--project-key", args.project_key), ("--project-name", args.project_name),
                                    ("--default-branch", args.default_branch)) if v]
    if args.no_project and args.project_root:
        raise kernel.SpudError(kernel.EXIT_USAGE, "--no-project and --project-root <path> are the two shapes of init's first project; give one or the other")
    if args.no_project and project_flags:
        raise kernel.SpudError(kernel.EXIT_USAGE, "--no-project registers no project, so %s has nothing to describe" % " and ".join(project_flags))
    root = args.project_root
    if root is None and not args.no_project:
        # Neither flag, and not a tty: no project.  The design gives the home and the prefixes as the two values with no
        # default (section 3.3), and the first project is not one of them -- `--no-project` is a supported home (1.3).
        root = ask(args, "The first project's repository (empty for none)")
    plan["project_root"] = str(root) if root else None
    plan["project_key"] = (args.project_key or project_key_for(root)) if root else None
    plan["project_name"] = (args.project_name or os.path.basename(str(root))) if root else None
    return plan


# ----------------------------------------------------------------------------
# The refusals, and the steps as --dry-run prints them
# ----------------------------------------------------------------------------


def is_spud_home(target):
    """A directory that already holds a home: the one exception refusal 2 needs and `homemove.move_preconditions` does
    not, because a non-empty target that is this home is init's second-run path rather than a refusal."""
    return (target / ".spud").exists() or (target / CONFIG_NAME).is_file()


def project_problems(ctx, plan):
    """Refusal 7 (design section 6): the shape of the first project's root, its key, and the two prefixes -- everything a
    candidate answers on its own, before anything is written.

    The uniqueness halves of the key and prefix checks need a connection and run inside step 3's transaction, where
    `registry.check_project_key` and `registry.check_prefixes` are the authority; what runs here is the lexical half,
    against registry's own patterns and naming init's flags.  On the empty registry a first run starts from, the
    uniqueness half is vacuous anyway.  A root that passes is resolved into the plan, so step 3 runs no second git.
    """
    problems = []
    for flag, value in (("--ticket-prefix", plan["ticket_prefix"]), ("--team-prefix", plan["team_prefix"])):
        if value is not None and not registry.PREFIX_RE.fullmatch(value):
            problems.append("%s %r must be upper-case letters and digits, starting with a letter" % (flag, value))
    if plan["ticket_prefix"] and plan["ticket_prefix"] == plan["team_prefix"]:
        problems.append("the ticket and team prefixes must differ (both %s)" % plan["ticket_prefix"])
    if plan["project_root"] is None:
        return problems
    try:
        plan["project_root"] = registry.project_root_shape(ctx, plan["project_root"])
    except kernel.SpudError as e:
        problems.append(e.message)
    key = plan["project_key"]
    if not registry.PROJECT_KEY_RE.fullmatch(key or ""):
        problems.append("--project-key %r must be lower-case letters, digits and hyphens, starting with a letter, at most 32 characters" % key)
    elif key == kernel.HOME_KEY:
        problems.append("--project-key %s is reserved: it names Spud's home in a deliverable glob (home:<glob>), and the home is not a project" % key)
    return problems


def init_preconditions(ctx, args, plan):
    """Every reason init is refused (design section 6), each naming what is in the way.  Five are
    `homemove.move_preconditions`', copied rather than re-derived; the interpreter's version is the cheapest check there
    is and doctor makes it a problem, so init cannot end green without it."""
    problems = []
    if sys.version_info[:2] != (3, 14):
        problems.append("interpreter is Python %d.%d.%d, not 3.14" % sys.version_info[:3])
    target = ctx.home
    if target.exists():
        if not target.is_dir():
            problems.append("%s exists and is not a directory" % target)
        elif not is_spud_home(target) and any(target.iterdir()):
            holds = sorted(p.name for p in target.iterdir())
            problems.append(NOT_A_HOME % (target, CONFIG_NAME, ", ".join(holds[:5]) + (" and %d more" % (len(holds) - 5) if len(holds) > 5 else "")))
    nearest = target
    while not nearest.exists():
        nearest = nearest.parent
    proc = homeconf.run_git(nearest, "rev-parse", "--is-inside-work-tree", timeout=10)
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        problems.append(IN_WORK_TREE % (target, nearest))
    pointer = homeconf.spud_config_dir() / "home"
    if pointer.is_file() and not args.repoint:
        named = pointer.read_text(encoding="utf-8").strip()
        if named and home_path(named) != target:
            problems.append(POINTER_ELSEWHERE % (pointer, named, target, named))
    if os.environ.get("SPUD_HOME") and home_path(os.environ["SPUD_HOME"]) != target:
        problems.append(SPUD_HOME_ELSEWHERE % (os.environ["SPUD_HOME"], target, target))
    if homeconf.tool_checkout_kind(ctx.tool) == "worktree":
        problems.append(TOOL_IS_WORKTREE % ctx.tool)
    problems.extend(project_problems(ctx, plan))
    return problems


def init_steps(ctx, args, plan):
    """Steps 1 to 5 as `--dry-run` prints them, with this machine's paths filled in -- `homemove.move_steps`' shape.

    Steps 6 to 10 are not printed: they do not run yet (see the module docstring), and a dry run that promised them
    would be the one thing a dry run may not be."""
    project = ("register %s as project %s (%s-nnn tickets, %s-nnn teams, landing %s, sessions %s) with its project.added"
               " event, and one report entry in the same transaction"
               % (plan["project_root"], plan["project_key"], plan["ticket_prefix"], plan["team_prefix"], args.landing, args.sessions)
               if plan["project_root"] else
               "register no project (--no-project), leaving an empty registry that can hold no ticket until `project add`,"
               " and write one report entry")
    return [
        ("check the %s already in %s with config_problems" % (CONFIG_NAME, ctx.home) if plan["config_exists"] else
         "mkdir %s and write %s from %s (identity %s, %s/%s, %s-nnn tickets, %s-nnn teams), then check it with config_problems"
         % (ctx.home, ctx.config_path, shipped.share_dir(ctx) / CONFIG_NAME, plan["name"], plan["pronouns"][0], plan["pronouns"][1],
            plan["ticket_prefix"], plan["team_prefix"])),
        ("create %s in WAL at user_version %d" % (ctx.db_path, schema.SCHEMA_VERSION) if not ctx.db_path.exists() else
         "migrate %s to user_version %d if it is behind, backup first" % (ctx.db_path, schema.SCHEMA_VERSION)),
        project,
        "write the %d scaffolding files absent from %s (%s) and the %d directories (%s), keeping every file already there"
        % (len(SCAFFOLDING), ctx.home, ", ".join(SCAFFOLDING), len(DIRECTORIES), ", ".join(DIRECTORIES)),
        "write %s" % (homeconf.spud_config_dir() / "home"),
    ]


# ----------------------------------------------------------------------------
# The steps
# ----------------------------------------------------------------------------


def unrendered(text):
    """The marks a rendered shipped file still carries, which must be none: a `{{…}}` reaching a person's home unrendered
    is `core/shipped.MARKS` and `<tool>/share/` out of step, and init refuses rather than writing it."""
    return sorted(set(MARK_LEFT.findall(text)))


def write_config(ctx, plan):
    """Step 1: the home directory, and the config rendered from `<tool>/share/spud.config.json` when the home has none.

    A config already there is left alone entirely (design section 6): a person's config is theirs after the first run and
    a template that has moved on must not take an edit back.  Either way `config_problems` runs against what is now on
    disk and a refusal stops the command here, because nothing downstream may read a config that has not cleared that bar.
    """
    ctx.home.mkdir(parents=True, exist_ok=True)
    kept = plan["config_exists"]
    if not kept:
        subject, obj, possessive = plan["pronouns"]
        text = shipped.render(shipped.read(ctx, CONFIG_NAME), {
            "identity_name": plan["name"], "pronoun_subject": subject, "pronoun_object": obj, "pronoun_possessive": possessive,
            "ticket_prefix": plan["ticket_prefix"], "team_prefix": plan["team_prefix"]})
        left = unrendered(text)
        if left:
            raise kernel.SpudError(kernel.EXIT_ERROR, NOT_RENDERED % (CONFIG_NAME, ", ".join(left)))
        kernel.write_whole(ctx.config_path, text)
    problems = homeconf.config_problems(ctx.config)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not usable: %s" % (ctx.config_path, "; ".join(problems)), data={"problems": problems})
    return "1. %s %s" % ("kept" if kept else "wrote", ctx.config_path), kept


def create_database(ctx):
    """Step 2: `<home>/.spud/ledger.db`, created in WAL and migrated, or migrated if behind -- `admincmds.cmd_init`'s
    body, moved here whole (design section 7), and the reason `spud init` is still what every message about a database
    behind or missing names.  Init never creates a second database over an existing one: `apply_migrations` takes its own
    backup when the file was not just created, and refuses a database a newer spud wrote."""
    created = not ctx.db_path.exists()
    ctx.config  # the config must exist before a ledger is created
    ctx.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = ledgerdb.open_connection(ctx.db_path)
    try:
        if created:
            con.execute("PRAGMA journal_mode = WAL")
        applied, backups = ledgerdb.apply_migrations(ctx, con, created)
        with ledgerdb.write_txn(con):
            ledgerdb.sync_config_rows(ctx, con)
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    return {"database": str(ctx.db_path), "created": created, "user_version": version, "applied": applied, "backups": backups}


def database_line(database):
    if database["created"]:
        return "2. created %s (user_version %d)" % (database["database"], database["user_version"])
    if database["applied"]:
        return "2. migrated %s to user_version %d (%s)" % (database["database"], database["user_version"], ", ".join(database["applied"]))
    return "2. %s is up to date (user_version %d)" % (database["database"], database["user_version"])


def add_first_project(ctx, con, args, plan, created):
    """Step 3: the first project and one report entry, in one transaction -- `registry.cmd_project_add`'s own `INSERT`
    and `project.added` event, under the literal actor label `spud` (design 2.3).  With no project, the entry alone.

    Idempotent by content, like every step: a project already registered at that root or under that key is reported and
    kept, and the entry is written only by a run that created the database or registered the project -- a second run
    writes nothing at all.  Returns (lines, the project row as a dict or None, the report entry or None).
    """
    root, key = plan["project_root"], plan["project_key"]
    row = None
    if root is not None:
        row = con.execute("SELECT * FROM projects WHERE root_path = ? OR key = ?", (root, key)).fetchone()
    add = root is not None and row is None
    entry = None
    if not add and not created:
        if args.next is not None:
            raise reportentry.no_entry_for_next("%s is initialized already, so init writes no report entry" % ctx.home)
        unchanged = ("3. project %s is registered already (%s); no report entry: this run changed nothing" % (row["key"], row["root_path"])
                     if row is not None else "3. no project registered (--no-project); no report entry: this run changed nothing")
        return [unchanged], None if row is None else dict(row), None
    lines = ["Home: %s (%s)" % (ctx.home, ctx.resolved_by), "Tool: %s" % ctx.tool]
    if root is not None:
        lines.append("Project %s: %s (%s-nnn tickets, %s-nnn teams, landing %s, sessions %s)"
                     % (key, root, plan["ticket_prefix"], plan["team_prefix"], args.landing, args.sessions))
    else:
        lines.append("No project registered: `%s --as spud project add <path> --key <key> --ticket-prefix %s --team-prefix %s --landing merge`"
                     " registers project 1, and a home with none holds no ticket" % (ctx.launcher, plan["ticket_prefix"], plan["team_prefix"]))
    at = kernel.now()
    with ledgerdb.write_txn(con):
        if add:
            registry.check_project_key(con, key)
            registry.check_prefixes(con, plan["ticket_prefix"], plan["team_prefix"])
            remote = homeconf.git_remote_url(root)
            branch = args.default_branch or registry.origin_head_branch(root) or "main"
            con.execute(
                "INSERT INTO projects (key, name, root_path, remote, ticket_prefix, team_prefix, created_at, default_branch, landing, sessions)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, plan["project_name"], root, remote, plan["ticket_prefix"], plan["team_prefix"], at, branch, args.landing, args.sessions),
            )
            data = {"project": key, "root": root, "ticket_prefix": plan["ticket_prefix"], "team_prefix": plan["team_prefix"],
                    "landing": args.landing, "sessions": args.sessions, "default_branch": branch, "remote": remote}
            ledgerdb.write_event(con, at, "spud", "project.added", "project %s added: %s" % (key, root), data=data)
            row = con.execute("SELECT * FROM projects WHERE key = ?", (key,)).fetchone()
        entry = reportentry.write_report_entry(con, at, "Spud initialized at %s" % ctx.home, "init", None, lines=lines, next_line=args.next)
    done = []
    if add:
        done.append("3a. project %s registered: %s (id %d, %s-nnn tickets, landing %s, sessions %s)"
                    % (key, root, row["id"], plan["ticket_prefix"], args.landing, args.sessions))
    elif root is not None:
        done.append("3a. project %s is registered already (%s)" % (row["key"], row["root_path"]))
    else:
        done.append("3a. no project registered (--no-project): an empty registry, which holds no ticket")
    done.append("3b. %s" % reportentry.report_entry_line(entry))
    return done, None if row is None else dict(row), entry


def without_project_lines(text):
    """`text` without the lines that carry a project mark, and how many were dropped: what step 4 writes into a home with
    no project.  Each of those lines is a sentence about project 1 -- the `This machine` bullet naming its root and
    remote, the ticket key's project in `ledger/Home.md`, the `<key>:<glob>` example in the deliverable-path law -- and a
    line rendered with the marks empty would say something untrue rather than nothing.  Init reports the count, because a
    person who registers a project later will want those lines back and the files are theirs to edit from then on."""
    marks = tuple(shipped.MARK % name for name in PROJECT_MARKS)
    lines = text.split("\n")
    kept = [line for line in lines if not any(mark in line for mark in marks)]
    return "\n".join(kept), len(lines) - len(kept)


def write_scaffolding(ctx, project):
    """Step 4: the seven shipped files and the five directories, from `<tool>/share/` through `core/shipped` (design
    section 2.2), after step 3 and not before.

    Each file is written **only when absent** and a present one is kept: `CLAUDE.md`, `ledger/Home.md`, `ledger/Spud.md`,
    the two `.base` files and `ledger/_templates/**` are all in `hooks/hookio.SPUD_PATHS`, Spud's hand-written set, so
    they are his to edit after the first run -- and the mechanism that keeps them from a member must keep them from a
    second init too.  Deliberately the opposite of `install.install_project`, which rewrites what it generates.
    Returns (written, kept, lines dropped for want of a project)."""
    values = shipped.marks(ctx, project)
    written, kept, dropped = [], [], 0
    for rel in SCAFFOLDING:
        path = ctx.home / rel
        if path.exists():
            kept.append(rel)
            continue
        text = shipped.read(ctx, rel)
        if project is None:
            text, count = without_project_lines(text)
            dropped += count
        text = shipped.render(text, values)
        left = unrendered(text)
        if left:
            raise kernel.SpudError(kernel.EXIT_ERROR, NOT_RENDERED % (rel, ", ".join(left)))
        kernel.write_whole(path, text)
        written.append(rel)
    for rel in DIRECTORIES:
        (ctx.home / rel).mkdir(parents=True, exist_ok=True)
    return written, kept, dropped


def write_pointer(ctx):
    """Step 5: `~/.config/spud/home`, written after the home is a home, as `homemove` writes it after the database and the
    vault exist -- a pointer written earlier would, on a failure, name a directory that is not yet usable."""
    pointer = homeconf.spud_config_dir() / "home"
    named = pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else None
    if named == str(ctx.home):
        return "5. %s names %s already" % (pointer, ctx.home), False
    kernel.write_whole(pointer, str(ctx.home) + "\n")
    return "5. %s names %s%s" % (pointer, ctx.home, "" if named is None else " (it named %s; that home is untouched, and writing its path back into the file returns to it)" % named), True


# ----------------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------------


def cmd_init(ctx, args):
    """`spud init`: design section 2, in its order, each step reported; a step that fails stops the command, names every
    step that completed, and removes nothing (section 2.4), so a rerun continues from where it stopped."""
    check_actor(args)
    check_next_line(args)
    plan = init_plan(ctx, args)
    problems = init_preconditions(ctx, args, plan)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, "init refused: " + "; ".join(problems),
                               data={"problems": problems, "home": str(ctx.home)})
    steps = init_steps(ctx, args, plan)
    if args.dry_run:
        return kernel.Result({"dry_run": True, "home": str(ctx.home), "how": ctx.resolved_by, "tool": str(ctx.tool), "steps": steps},
                             "init --dry-run: the preconditions hold; init would\n" + "\n".join("  %d. %s" % (n, s) for n, s in enumerate(steps, start=1)))
    data = {"dry_run": False, "home": str(ctx.home), "how": ctx.resolved_by, "tool": str(ctx.tool)}
    done = []
    entry = None
    try:
        line, kept_config = write_config(ctx, plan)
        done.append(line)
        data["config"] = {"path": str(ctx.config_path), "written": not kept_config}
        database = create_database(ctx)
        data.update(database)
        done.append(database_line(database))
        con = ledgerdb.open_connection(ctx.db_path)
        try:
            lines, project, entry = add_first_project(ctx, con, args, plan, database["created"])
        finally:
            con.close()
        done.extend(lines)
        data["project"] = project and {k: project[k] for k in ("key", "name", "root_path", "ticket_prefix", "team_prefix",
                                                               "landing", "sessions", "default_branch", "remote")}
        written, kept, dropped = write_scaffolding(ctx, project)
        data["scaffolding"] = {"written": written, "kept": kept, "dropped_lines": dropped}
        done.append("4. scaffolding: %s written, %d kept%s; %d directories"
                    % (", ".join(written) or "nothing", len(kept), "" if not dropped else
                       " (%d line(s) about project 1 left out, for want of one: `project add`, then they are yours to write back)" % dropped,
                       len(DIRECTORIES)))
        line, pointed = write_pointer(ctx)
        done.append(line)
        data["pointer"] = {"path": str(homeconf.spud_config_dir() / "home"), "written": pointed}
    except kernel.SpudError as e:
        raise kernel.SpudError(e.code, "init stopped after:\n  %s\n%s\n%s" % ("\n  ".join(done) or "nothing", e.message, LEFT_BEHIND % ctx.home),
                               data=dict(e.data, done=done, home=str(ctx.home)))
    by_hand = INIT_BY_HAND % {"launcher": ctx.launcher, "home": ctx.home,
                         "install": ("\n  - %s --as spud project install %s" % (ctx.launcher, data["project"]["key"])) if data["project"] else ""}
    data.update(done=done, by_hand=by_hand)
    if entry is not None:  # its line is step 3b already, so the entry goes into --json and the text is left alone
        data["report_entry"] = entry
    return kernel.Result(data, "\n".join(done) + "\n" + by_hand)
