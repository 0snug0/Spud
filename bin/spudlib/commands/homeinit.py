"""commands/homeinit: `spud init`, one command from a fresh clone to a working Spud.

All ten of init's steps.  Steps 1 to 5 build the home: the directory and the config, the database, the first
project with one report entry, the vault -- its scaffolding, and then, unless `--no-vault`, the Obsidian settings and
every plugin and theme the lock pins (step 4b) -- and the home pointer.  Steps 6 to 10 are the install tail --
`finish_install` (`settings sync` into the home's own `.claude/settings.json`, `project install` for the first project,
the two LaunchAgents) and `verify` (the first render under the render lock, then doctor) -- and when they end green one
command has taken a fresh clone to `spud doctor` reporting `problems none`, which is the ticket's definition of done.
What is left by hand after that is two lines: open the home as a vault in Obsidian, and start a session there.

**Kept whole, and past 250 lines on purpose.**  ~250 is the look-again point, never a cap (`.claude/skills/spudlib-modules`
section 6), and the argument here is `commands/homemove`'s own: this is one procedure whose every step reads what the ones
before it did.  Step 1 decides what the config says; step 2 will not open a database until that config has cleared
`config_problems`; step 3's `INSERT` carries the prefixes step 1 wrote, because doctor compares the two; step 4 renders
prose from the config step 1 wrote and the project row step 3 inserted, and must not run before step 3, so that no
`CLAUDE.md` describes a project whose `INSERT` then failed on a `UNIQUE` constraint; step 5 writes the pointer only once
the four before it made the directory a home; step 7 installs the project row step 3 inserted; step 9 renders what steps
3 to 8 wrote and refuses anything else; step 10 asks doctor about all of it.  Cutting that sequence at any point would
give two modules that may only ever be called in one order, with the order itself written nowhere.  The one seam that is
real is the install tail, which is `finish_install` and `verify` -- the two functions the suite calls on their own.

Three properties an edit here must keep:

- **Init resolves no actor.**  `state/actors.resolve_actor(con, None)` refuses outright and `require_spud`
  refuses an unclaimed session in a `claim` project -- which is exactly the session init creates when it registers
  someone's repository as project 1.  So `--as` is checked lexically, every event and the report entry are written under
  the literal actor label `spud` the way `homemove.move_resync` writes them, and `reportentry.check_next` (which goes
  through `require_spud`) is never called: init validates `--next` itself.
- **`bin/spud_ledger.main` resolves the home for every other command** and `init` is the one that may run without one,
  through `init_ctx` in main's own branch.  `core/homeconf.resolve_home` is untouched, and nothing here joins the hook path.
- **No step names a day from the clock.**  Step 3 writes init's report entry and step 9 renders it, and a run that
  crosses midnight between the two would otherwise render a day file step 9 had not been told to expect and fail a
  command that did everything right.  `report_days` reads the days from the entries themselves; nothing here calls
  `kernel.now()` to name a file.
"""

import json
import os
import re
import sys
from pathlib import Path

from . import doctor, homesync, publish, reportentry, schedule, settings_sync, vaultinstall, vaultlock
from ..core import homeconf, kernel, launchagents, lazy, shipped
from ..projects import install, registry
from ..render import notefiles
from ..state import ledgerdb, schema

# The default home the prompt offers when neither --home, nor SPUD_HOME, nor the pointer names one.  Shortest of the four candidates, outside iCloud Drive -- a SQLite database
# with a WAL in a synced directory is a silent corruption risk -- and a plain directory a person can open in Obsidian.
DEFAULT_HOME = "~/SpudHome"
DEFAULT_NAME = "Spud"
DEFAULT_PRONOUNS = "he/him/his"
# The owner's pronouns have a default and the owner's name does not.  Pronouns nobody gave are the ones that
# are right for anyone; a name nobody gave cannot be guessed at all, and it is in the generated CLAUDE.md, in every
# brief template and in the note that says whose ledger this is -- so it joins the two prefixes as a value `--yes`
# must be given rather than assume (the prompts' rule, one more value under it).
DEFAULT_OWNER_PRONOUNS = "they/them/their"
CONFIG_NAME = "spud.config.json"
# Step 4's five directories.  The files it writes are `commands/homesync.tool_owned` -- the seven a new home starts
# from and every shipped skill beside them -- which moved there when `home sync` became the second
# command that writes them, and which is where the rendering, the project-line rule and the unrendered-mark refusal
# live now.  Not every shipped file is in that list: share/agents/spudagent.md is rendered to user scope by step 7's
# `project install`, never into a home, and share/obsidian/ is step 4b's.
DIRECTORIES = ("ledger/tickets", "ledger/teams", "reports", "docs/spikes", "docs/design")

NOT_SPUD = ("init resolves no actor and `--as %s` names one: the first run has no database and no members table for"
            " anyone to own anything in, and `init` is Spud's own (hooks/hookio.SPUD_ONLY_COMMANDS)."
            "  Run `spud init` or `spud init --as spud`.")
NO_HOME = ("no home to build: give `--home <dir>` (%s is the usual choice), or set SPUD_HOME."
           "  %s names none, so there is nothing to continue.")
POINTER_ELSEWHERE = ("%s names %s, not %s; a machine has one pointer and repointing it silently orphans a working home."
                     "  Rerun with `--home %s` to work on that home, or with `--repoint` to point this machine here instead.")
SPUD_HOME_ELSEWHERE = ("SPUD_HOME is %s, not %s; after init the shell's SPUD_HOME beats the pointer in resolve_home, so"
                       " you would be working on a different home than the one init just built.  Unset it, or rerun with `--home %s`.")
IN_WORK_TREE = "%s is inside a git work tree (%s); the home is a plain directory, and a home inside a repository puts the vault into somebody's history"
TOOL_IS_WORKTREE = ("the running bin/spud is in a linked worktree (%s); run the main checkout's.  Init writes that path into"
                    " this home's CLAUDE.md, and a worktree is deleted when its ticket lands.")
NOT_A_HOME = "%s is not an empty directory and is no Spud home (no %s and no .spud/): it holds %s"
PREFIX_DIFFERS = ("%s %s differs from the %s already in %s, which carries %s; init leaves a config it finds alone"
                  " and doctor compares project 1's prefixes with the config's.  Rerun with `%s %s`, or edit the config first.")
NO_PREFIXES = ("%s has no %s yet, and a config carries the two prefixes: give `--ticket-prefix XXX --team-prefix XXXS`."
               "  They have no default on purpose -- a prefix is in every rendered file name and every wikilink forever,"
               " and `project edit` refuses to change one once the project has a ticket.")
NO_OWNER = ("%s has no %s yet, and a config carries the name of the person the home is for: give `--owner-name '<name>'`."
            "  It has no default on purpose -- it is in the CLAUDE.md the tool generates for this home, in the brief"
            " template every spudagent is spawned with, and in the note that says whose ledger this is.  The pronouns"
            " are `--owner-pronouns subject/object/possessive` and default to %s.")
CONFIG_IS_THEIRS = "%s is already there and init leaves it alone, so %s has nothing to write; edit the file instead"
LEFT_BEHIND =("nothing init wrote is removed: %s is as the steps above left it, and a rerun continues from there,"
               " because every step is idempotent by content")
# Step 9's refusal.  Init reads `move_check_vault`'s precondition -- the copied vault must already be what
# the copied database renders -- as init's postcondition: a fresh vault has nothing rendered yet, so the scaffolding is
# written first (step 4) and the first render must write nothing but what a fresh ledger generates.
RENDER_UNEXPECTED = ("the render into %s wrote %s and found %d conflict(s); a fresh home renders %s and nothing else."
                     "  Either a shipped file collides with a render target, which tests/test_share.py is the guard"
                     " against, or this home was not fresh and its vault was behind -- and then the pass above has just"
                     " brought it up to date, so a rerun is green.  A conflict is a hand edit instead:"
                     " `%s --as spud render --discard <path>` takes one file back, `%s --as spud import --file <path>`"
                     " keeps it.")
DOCTOR_RED = ("doctor on %s found %d problem(s): %s.  The home is complete and nothing is undone: fix each and rerun"
              " `%s doctor` -- or `%s init`, which continues from here.")
WATCHER_JUST_UP = ("; the render watcher was installed seconds ago and is not up yet: `%s doctor` from the new session"
                   " confirms it")
NO_PROJECT_TO_INSTALL = ("7. no project to install (--no-project): the home's own hooks are step 6's, and"
                         " `%s --as spud project install <key>` follows the `project add` that registers one")
SCHEDULE_SKIPPED = ("8. %s: %s and %s not installed, so nothing renders or backs up on its own until"
                    " `%s --as spud schedule install` -- which doctor reports as a note, never a problem")
NOT_DARWIN = "%s is not darwin, and launchctl is macOS's"
# Step 4b's two skips, both notes and never a failure: a home whose plugins did not download is a home
# that opens and works, with the views the tool ships and no plugin behind them, and one command later it is complete.
VAULT_SKIPPED = "4b. no Obsidian vault installed (--no-vault): `%s --as spud vault install` sets one up later"
VAULT_REFUSED = ("4b. %d download(s) refused, so those plugins and themes are not installed and the vault opens without"
                 " them: run `%s --as spud vault install` once the network is back")
INIT_BY_HAND = """by hand, now:
  - open %(home)s as a vault in Obsidian, and choose Trust author and enable plugins
  - start a Claude Code session there: it is Spud's"""


# ----------------------------------------------------------------------------
# The home, before there is one
# ----------------------------------------------------------------------------


def ask(args, question, default=None):
    """One of init's prompts, or `default`: the interface is flags and the prompt is a convenience, so a value
    not given takes its default whenever stdin is not a tty or `--yes` was given.  `input` is a builtin, which costs the
    `python3.14 -I -S` launcher nothing."""
    if args.yes or not sys.stdin.isatty():
        return default
    answer = input("%s%s: " % (question, "" if default is None else " [%s]" % default)).strip()
    return answer or default


def ask_offered(args, question, default):
    """A prompt whose default is *offered* rather than taken: the home is the one value with a default that a run off a
    tty must not silently use (`~/SpudHome` is a convention, and a person told which directory they
    are about to fill is a person who can say no), so away from a prompt the refusal names `--home` instead."""
    return ask(args, question, default) if not args.yes and sys.stdin.isatty() else None


def home_path(value):
    """A path the way `homeconf.resolve_home` returns one, so the pointer init writes resolves back to the same string."""
    return Path(str(value)).expanduser().resolve()


def init_ctx(env, args):
    """The home `spud init` will build, and how it was found: `--home`, then SPUD_HOME, then the existing pointer, then
    DEFAULT_HOME offered as a prompt when stdin is a tty, then the usage refusal naming `--home`.

    `bin/spud_ledger.main` calls this instead of `homeconf.resolve_home` for `init` alone: resolve_home
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
    """`--as`, read lexically: absent or `spud` proceeds, anything else is refused (init resolves no actor, the reason the whole
    module writes the literal actor label `spud` rather than resolving one)."""
    if args.actor is not None and args.actor != "spud":
        raise kernel.SpudError(kernel.EXIT_OWNERSHIP, NOT_SPUD % args.actor)


def check_next_line(args):
    """`--next`, validated here rather than by `reportentry.check_next`, which goes through `require_spud`."""
    if args.next is not None and not args.next.strip():
        raise kernel.SpudError(kernel.EXIT_USAGE, "--next is empty: give the Next line, or leave --next out")


# ----------------------------------------------------------------------------
# What init was told, resolved once
# ----------------------------------------------------------------------------


def project_key_for(root):
    """The `--project-key` default: the root's directory name, lower-cased and sanitized towards
    `registry.PROJECT_KEY_RE`.  A name that cannot become a key is not guessed at -- the refusal names `--project-key`."""
    return re.sub(r"[^a-z0-9-]+", "-", os.path.basename(str(root)).lower()).strip("-")


def pronouns_of(args, flag, given, question, default):
    """`subject/object/possessive` as one flag spells it, or the prompt's answer, or `default`: the three values the
    config carries for one person and the shipped prose renders separately.

    One function for both `--pronouns` (the identity's) and `--owner-pronouns`, because a second copy of the
    three-word check is a second place for the two to drift.  `given` is passed already read rather than the flag's
    name looked up, so a flag that was given skips its prompt, as every other value here does."""
    value = given or ask(args, question, default) or default
    parts = [p.strip() for p in value.split("/")]
    if len(parts) != 3 or not all(parts):
        raise kernel.SpudError(kernel.EXIT_USAGE, "%s is subject/object/possessive, three words separated by slashes (%r)" % (flag, value))
    return parts


def init_plan(ctx, args):
    """Every value init needs, resolved once: the identity, the two prefixes, and the first project's root, key and name.

    The prompts run here and nowhere else, which is why `cmd_init` builds this once and hands it to
    `init_preconditions` and `init_steps` rather than letting each resolve its own -- a person must be asked once.

    Two rules about a config already on disk, which init leaves alone entirely: the identity flags have nothing to write and are refused; and the config's
    prefixes are authoritative, so a prefix flag that differs is refused and one that is absent is taken from the file.
    Doctor compares project 1's prefixes with the config's, so the two must agree the moment init writes them both.
    """
    config = ctx.config if ctx.config_path.is_file() else None  # Ctx.config raises when the file is absent, so ask first
    plan = {"config_exists": config is not None}
    if config is None:
        plan["name"] = args.name or ask(args, "Spud's name", DEFAULT_NAME) or DEFAULT_NAME
        plan["pronouns"] = pronouns_of(args, "--pronouns", args.pronouns, "Spud's pronouns (subject/object/possessive)", DEFAULT_PRONOUNS)
        plan["owner_name"] = args.owner_name or ask(args, "Your name (the person this home is for)")
        plan["owner_pronouns"] = pronouns_of(args, "--owner-pronouns", args.owner_pronouns,
                                             "Your pronouns (subject/object/possessive)", DEFAULT_OWNER_PRONOUNS)
        ticket_prefix = args.ticket_prefix or ask(args, "Ticket prefix (SPD gives ticket SPD-001)")
        team_prefix = args.team_prefix or ask(args, "Team prefix (SPUD gives team SPUD-001)")
        if not plan["owner_name"]:
            raise kernel.SpudError(kernel.EXIT_USAGE, NO_OWNER % (ctx.home, CONFIG_NAME, DEFAULT_OWNER_PRONOUNS))
        if not ticket_prefix or not team_prefix:
            raise kernel.SpudError(kernel.EXIT_USAGE, NO_PREFIXES % (ctx.home, CONFIG_NAME))
    else:
        given = [flag for flag, value in (("--name", args.name), ("--pronouns", args.pronouns),
                                          ("--owner-name", args.owner_name), ("--owner-pronouns", args.owner_pronouns)) if value]
        if given:
            raise kernel.SpudError(kernel.EXIT_USAGE, CONFIG_IS_THEIRS % (ctx.config_path, " and ".join(given)))
        identity = config.get("identity", {})
        plan["name"] = identity.get("name", DEFAULT_NAME)
        plan["pronouns"] = [identity.get("pronouns", {}).get(k, "") for k in ("subject", "object", "possessive")]
        # A config that predates the `owner` block, or one whose block is empty, reads as core/shipped's placeholder --
        # the same value every shipped file renders for that home, so the dry run says what the files will say.
        owner = config.get("owner") or {}
        owner_pronouns = owner.get("pronouns") or {}
        plan["owner_name"] = owner.get("name") or shipped.DEFAULT_OWNER["name"]
        plan["owner_pronouns"] = [owner_pronouns.get(k) or shipped.DEFAULT_OWNER[k] for k in ("subject", "object", "possessive")]
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
        # Neither flag, and not a tty: no project.  The home and the prefixes are the values with no default, and the
        # first project is not one of them -- a home with no project (`--no-project`) is a supported one.
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
    """Refusal 7: the shape of the first project's root, its key, and the two prefixes -- everything a
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
    """Every reason init is refused, each naming what is in the way.  Five are
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
    """All ten steps as `--dry-run` prints them, with this machine's paths filled in -- `homemove.move_steps`' shape.

    Steps 8's two skips are decided here as well as in `install_schedule`, because a dry run that promised a LaunchAgent
    it would not install would be the one thing a dry run may not be."""
    project = ("register %s as project %s (%s-nnn tickets, %s-nnn teams, landing %s, sessions %s) with its project.added"
               " event, and one report entry in the same transaction"
               % (plan["project_root"], plan["project_key"], plan["ticket_prefix"], plan["team_prefix"], args.landing, args.sessions)
               if plan["project_root"] else
               "register no project (--no-project), leaving an empty registry that can hold no ticket until `project add`,"
               " and write one report entry")
    user = homeconf.user_claude_dir()
    install_step = ("install project %s: %s, the .git/info/exclude line for it, %s with its %d effort variants (%s) and %s"
                    % (plan["project_key"], Path(plan["project_root"]) / install.SETTINGS_LOCAL,
                       user / "agents" / "spudagent.md", len(kernel.SPUDAGENT_VARIANTS),
                       ", ".join(name + ".md" for name in kernel.SPUDAGENT_VARIANTS), user / "skills" / "spud" / "SKILL.md")
                    if plan["project_root"] else "install no project (--no-project)")
    if args.no_schedule or sys.platform != "darwin":
        schedule_step = ("install neither LaunchAgent (%s): %s and %s"
                         % ("--no-schedule" if args.no_schedule else NOT_DARWIN % sys.platform,
                            launchagents.SCHEDULE_LABEL, launchagents.RENDER_LABEL))
    else:
        schedule_step = ("install %s (daily at %s) and %s (at load, and again whenever it exits) and load both"
                         % (launchagents.SCHEDULE_LABEL, schedule.SCHEDULE_AT, launchagents.RENDER_LABEL))
    owned = [home_rel for _rel, home_rel in homesync.tool_owned(ctx)]  # the files step 4 writes, named as the home has them
    return [
        ("check the %s already in %s with config_problems" % (CONFIG_NAME, ctx.home) if plan["config_exists"] else
         "mkdir %s and write %s from %s (identity %s, %s/%s, working for %s, %s/%s, %s-nnn tickets, %s-nnn teams),"
         " then check it with config_problems"
         % (ctx.home, ctx.config_path, shipped.share_dir(ctx) / CONFIG_NAME, plan["name"], plan["pronouns"][0], plan["pronouns"][1],
            plan["owner_name"], plan["owner_pronouns"][0], plan["owner_pronouns"][1],
            plan["ticket_prefix"], plan["team_prefix"])),
        ("create %s in WAL at user_version %d" % (ctx.db_path, schema.SCHEMA_VERSION) if not ctx.db_path.exists() else
         "migrate %s to user_version %d if it is behind, backup first" % (ctx.db_path, schema.SCHEMA_VERSION)),
        project,
        "write the %d scaffolding files absent from %s (%s) and the %d directories (%s), keeping every file already there;"
        " then %s"
        % (len(owned), ctx.home, ", ".join(owned), len(DIRECTORIES), ", ".join(DIRECTORIES),
           "install no vault (--no-vault)" if args.no_vault else
           "write %s from %s and download every plugin and theme %s pins, checking each SHA-256"
           % (ctx.home / vaultlock.OBSIDIAN, vaultlock.share_vault(ctx), vaultlock.lock_path(ctx))),
        "write %s" % (homeconf.spud_config_dir() / "home"),
        "settings sync into %s: %d ledger hook lines, the two CLI allow rules, the two Agent deny rules, the two env caps"
        % (ctx.home / ".claude" / "settings.json", len(settings_sync.HOOK_TABLE)),
        install_step,
        schedule_step,
        "render into %s under the render lock, and require that it wrote nothing but %s and the day file of each report"
        " entry, and found no conflict" % (ctx.home, notefiles.PROJECTS_NOTE),
        "run doctor on %s and fail on every problem but a render watcher installed seconds ago and not up yet" % ctx.home,
    ]


# ----------------------------------------------------------------------------
# The steps
# ----------------------------------------------------------------------------


def write_config(ctx, plan):
    """Step 1: the home directory, and the config rendered from `<tool>/share/spud.config.json` when the home has none.

    A config already there is left alone entirely: a person's config is theirs after the first run and
    a template that has moved on must not take an edit back.  Either way `config_problems` runs against what is now on
    disk and a refusal stops the command here, because nothing downstream may read a config that has not cleared that bar.
    """
    ctx.home.mkdir(parents=True, exist_ok=True)
    kept = plan["config_exists"]
    if not kept:
        subject, obj, possessive = plan["pronouns"]
        owner_subject, owner_object, owner_possessive = plan["owner_pronouns"]
        text = shipped.render(shipped.read(ctx, CONFIG_NAME), {
            "identity_name": plan["name"], "pronoun_subject": subject, "pronoun_object": obj, "pronoun_possessive": possessive,
            "owner_name": plan["owner_name"], "owner_subject": owner_subject, "owner_object": owner_object,
            "owner_possessive": owner_possessive,
            "ticket_prefix": plan["ticket_prefix"], "team_prefix": plan["team_prefix"]})
        left = homesync.unrendered(text)
        if left:
            raise kernel.SpudError(kernel.EXIT_ERROR, homesync.NOT_RENDERED % (CONFIG_NAME, ", ".join(left)))
        kernel.write_whole(ctx.config_path, text)
    problems = homeconf.config_problems(ctx.config)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not usable: %s" % (ctx.config_path, "; ".join(problems)), data={"problems": problems})
    return "1. %s %s" % ("kept" if kept else "wrote", ctx.config_path), kept


def create_database(ctx):
    """Step 2: `<home>/.spud/ledger.db`, created in WAL and migrated, or migrated if behind -- `admincmds.cmd_init`'s
    body, moved here whole, and the reason `spud init` is still what every message about a database
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
    and `project.added` event, under the literal actor label `spud`.  With no project, the entry alone.

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


def write_scaffolding(ctx, project):
    """Step 4: every file the tool owns in a home and the five directories, from `<tool>/share/` through
    `commands/homesync` (the seven files a new home starts from, and the shipped skills beside them).

    Each file is written **only when absent** and a present one is kept: `CLAUDE.md`, `ledger/Home.md`, `ledger/Spud.md`,
    the two `.base` files and `ledger/_templates/**` are all in `hooks/hookio.SPUD_PATHS`, Spud's hand-written set, so
    they are his to edit after the first run -- and the mechanism that keeps them from a member must keep them from a
    second init too.  Deliberately the opposite of `install.install_project`, which rewrites what it generates, and of
    `home sync`, which is the command that takes such an edit back on purpose and keeps a copy of it first.
    Returns (written, kept, lines dropped for want of a project)."""
    values = shipped.marks(ctx, project)
    written, kept, dropped = [], [], 0
    for rel, home_rel in homesync.tool_owned(ctx):
        path = ctx.home / home_rel
        if path.exists():
            kept.append(home_rel)
            continue
        text, count = homesync.shipped_text(ctx, rel, values, project)
        dropped += count
        kernel.write_whole(path, text)
        written.append(home_rel)
    for rel in DIRECTORIES:
        (ctx.home / rel).mkdir(parents=True, exist_ok=True)
    return written, kept, dropped


def install_home_vault(ctx, args, done):
    """Step 4b: the Obsidian vault, `vault install` over the home step 4a just scaffolded.

    Part of step 4 and not a step of its own, because it is the same job -- the files a new home
    needs before anybody opens it -- and because the ten steps' numbers are what every other message here names.  It
    calls `install_vault` rather than `cmd_vault_install`: init resolves no actor and opens no database for this.

    A refused download is a note and never a failure: the settings and the views are written whatever the network did,
    the vault opens, and one `vault install` later it is complete.
    """
    if args.no_vault:
        done.append(VAULT_SKIPPED % ctx.launcher)
        return {"skipped": "--no-vault"}
    record, lines = vaultinstall.install_vault(ctx)
    done.append("4b. %s" % lines[0])
    done.extend("    %s" % line.strip() for line in lines[1:])
    if record["refused"]:
        done.append(VAULT_REFUSED % (len(record["refused"]), ctx.launcher))
    return record


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
# The install tail
# ----------------------------------------------------------------------------


def install_first_project(ctx, project, done):
    """Step 7: `project install` for the first project, skipped with `--no-project` -- `homemove.move_resync`'s 5d over
    one project, with the `projects.installed` record and the `project.installed` event written beside it.

    Idempotent by content: `install.install_project` writes each file only when its text changes, so a second run
    reports `unchanged`, and the record and the event follow only a run that wrote something or found the project not
    installed.  A run that changed nothing writes no row at all, which is the rule the whole command keeps.
    """
    if project is None:
        done.append(NO_PROJECT_TO_INSTALL % ctx.launcher)
        return None
    con = ledgerdb.connect(ctx)
    try:
        p = con.execute("SELECT * FROM projects WHERE id = ?", (project["id"],)).fetchone()
        record, written, first_agent = install.install_project(ctx, con, p)
        stored = json.loads(p["installed"]) if p["installed"] else None
        if written or stored != record:
            at = kernel.now()
            with ledgerdb.write_txn(con):
                con.execute("UPDATE projects SET installed = ? WHERE id = ?", (json.dumps(record), p["id"]))
                ledgerdb.write_event(con, at, "spud", "project.installed",
                                     "project %s installed by init: %d file(s) written" % (p["key"], len(written)),
                                     data={"project": p["key"], "written": written, "sync": False})
    finally:
        con.close()
    done.append("7. project %s installed: %s" % (p["key"], ", ".join(written) or "unchanged"))
    return {"project": p["key"], "written": written, "restart": first_agent}


def install_schedule(ctx, args, done):
    """Step 8: the two LaunchAgents, `homemove`'s step 6 -- skipped with `--no-schedule`, and skipped with a printed note
    where `launchctl` does not exist (`sys.platform != "darwin"`).

    Both skips leave a green doctor, which is what makes them safe to offer: doctor reports a watcher never installed as
    a note and only a watcher installed and *not running* as a problem, and step 10 excuses that one, because it is what
    a watcher bootstrapped seconds ago looks like.  The two halves of that rule belong to one step and are written here
    and in `verify` for that reason.
    """
    if args.no_schedule or sys.platform != "darwin":
        why = "--no-schedule" if args.no_schedule else NOT_DARWIN % sys.platform
        done.append(SCHEDULE_SKIPPED % (why, launchagents.SCHEDULE_LABEL, launchagents.RENDER_LABEL, ctx.launcher))
        return {"skipped": why, "agents": []}
    records = schedule.install_agents(ctx, schedule.at_arg(schedule.SCHEDULE_AT))
    for record in records:
        done.append("8. %s loaded from %s" % (record["label"], record["path"]))
    return {"skipped": None, "agents": records, "at": schedule.SCHEDULE_AT}


def finish_install(ctx, args, project, done):
    """Steps 6 to 8: `settings sync` into the home's own `.claude/settings.json`, `project install` for the first
    project, and the two LaunchAgents -- `homemove.move_resync`'s 5b and 5d and its step 6, over a home just built.

    Each line goes into `done` as its step completes rather than being returned at the end, so the failure report of
    `cmd_init` can name a step 6 that completed when step 7 is the one that failed.  Step 6 is first
    because it is the step that makes a session in the home Spud's, and it is the one step no flag skips: a home whose
    own `.claude/settings.json` carries no ledger hook loses every hook in every home session, and nothing downstream
    of this step but doctor's settings line would notice.
    """
    synced = settings_sync.cmd_settings_sync(ctx, lazy.argparse.Namespace(path=None, dry_run=False))
    done.append("6. %s %s (%d ledger hook lines)"
                % (synced.data["path"], "written" if synced.data["written"] else "unchanged", synced.data["hooks"]))
    data = {"settings": {k: synced.data[k] for k in ("path", "written", "hooks")}}
    data["install"] = install_first_project(ctx, project, done)  # three statements, not one dict literal: the order is
    data["schedule"] = install_schedule(ctx, args, done)  # init's own, and it should not rest on how a literal evaluates
    return data


def report_days(con):
    """`reports/<day>.md` for every day the event log holds a report entry for: exactly the day files
    `render/notefiles.render_targets` generates, read from the entries themselves and never from the clock.

    Step 3 writes init's own entry and step 9 renders it.  A run that crossed midnight between the two would render a
    day file that `kernel.now()[:10]` had not named, and step 9 would call it unexpected and fail a command that had
    done everything right.  `homemove.move_verify`'s `allowed` set takes that risk (its own two steps are seconds
    apart); init's does not, and `tests/helpers.init_report_day` reads the day the same way for the same reason.
    """
    return {"reports/%s.md" % r[0] for r in
            con.execute("SELECT DISTINCT substr(at, 1, 10) FROM events WHERE kind = 'report.entry'").fetchall()}


def verify(ctx, done):
    """Steps 9 and 10, `homemove.move_verify` over a home just built: the first render under `publish.render_lock`,
    which must write nothing but `ledger/Projects.md` and each report entry's day file and find no conflict, then
    `doctor`, where every problem but the just-bootstrapped watcher's fails the command with the report attached.

    `home move` requires a zero-write render *before* it writes the home it copied (`move_check_vault`),
    because its vault arrives already rendered.  A fresh vault has nothing rendered yet, so the same rule is init's
    **postcondition** instead -- the scaffolding is written first, at step 4, and the first render must write only what a
    fresh ledger generates.  A shipped file that collided with a render target would surface here.

    The strict set is kept on a rerun too, where init is also the migration path ("the database is behind; run `spud
    init`"): a rerun whose pass writes a ticket note found a vault that was behind, which is worth a refusal rather than
    a silent pass, and `RENDER_UNEXPECTED` names that reading beside the collision -- the pass has brought the vault up
    to date, so the next run is green.
    """
    con = ledgerdb.connect(ctx)
    try:
        allowed = {notefiles.PROJECTS_NOTE} | report_days(con)
        with publish.render_lock(ctx):
            result = publish.render_pass(ctx, con, None)
    finally:
        con.close()
    unexpected = [rel for rel in result["written"] if rel not in allowed]
    if unexpected or result["conflicts"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, RENDER_UNEXPECTED
                               % (ctx.home, ", ".join(unexpected) or "nothing unexpected", len(result["conflicts"]),
                                  ", ".join(sorted(allowed)), ctx.launcher, ctx.launcher), data=result)
    done.append("9. render into %s: %s written, %d unchanged"
                % (ctx.home, ", ".join(result["written"]) or "nothing", len(result["unchanged"])))
    report, problems, _lines = doctor.doctor_report(ctx)
    real = [p for p in problems if p != doctor.WATCHER_DOWN]
    if real:
        raise kernel.SpudError(kernel.EXIT_ERROR, DOCTOR_RED % (ctx.home, len(real), "; ".join(real), ctx.launcher, ctx.launcher), data=report)
    done.append("10. doctor ok" + ("" if len(real) == len(problems) else WATCHER_JUST_UP % ctx.launcher))
    # `conflicts` is empty by the time anything reads this -- a conflict raised above -- and it is in the data because
    # step 9's postcondition is two clauses, and a test that can only read one of them proves only half of it.
    return {"render": {"written": result["written"], "unchanged": len(result["unchanged"]),
                       "conflicts": result["conflicts"], "through": result["through"]},
            "doctor": {"problems": problems, "notes": report["notes"]}}


# ----------------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------------


def cmd_init(ctx, args):
    """`spud init`: the ten steps in their order, each step reported; a step that fails stops the command, names every
    step that completed, and removes nothing, so a rerun continues from where it stopped."""
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
        done.append("4a. scaffolding: %s written, %d kept%s; %d directories"
                    % (", ".join(written) or "nothing", len(kept), "" if not dropped else
                       " (%d line(s) about project 1 left out, for want of one: `project add`, then they are yours to write back)" % dropped,
                       len(DIRECTORIES)))
        data["vault"] = install_home_vault(ctx, args, done)
        line, pointed = write_pointer(ctx)
        done.append(line)
        data["pointer"] = {"path": str(homeconf.spud_config_dir() / "home"), "written": pointed}
        data.update(finish_install(ctx, args, project, done))
        data.update(verify(ctx, done))
    except kernel.SpudError as e:
        raise kernel.SpudError(e.code, "init stopped after:\n  %s\n%s\n%s" % ("\n  ".join(done) or "nothing", e.message, LEFT_BEHIND % ctx.home),
                               data=dict(e.data, done=done, home=str(ctx.home)))
    by_hand = INIT_BY_HAND % {"home": ctx.home}
    data.update(done=done, by_hand=by_hand)
    if entry is not None:  # its line is step 3b already, so the entry goes into --json and the text is left alone
        data["report_entry"] = entry
    return kernel.Result(data, "\n".join(done) + "\n" + by_hand)
