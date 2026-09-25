"""commands/homesync: the files the tool owns in a home, and `spud home sync`, which regenerates every one of them.

Three things live here, and they are one subject.

- **Which files the tool owns.**  `SCAFFOLDING` is the list `spud init` writes, moved here whole
  because it stopped being init's alone the moment a second command wrote from it; `shipped_skills` adds every file
  under `<tool>/share/skills/`, which a home reads from `.claude/skills/` (`home_relative`).  `tool_owned` is the pair
  both commands walk, and the only answer in the program to "what in a home comes from the tool".
- **How one is rendered for this home**: `shipped_text`, which is what init's step 4 did inline -- the project lines
  dropped when the home has no project, the marks of `core/shipped` replaced, and a mark left over refused rather than
  written into somebody's home.
- **`spud home sync [--check]`**, Spud's command: the same writing again, into a home that exists.

Four rules an edit here must keep.

- **The two commands differ in one word, and it is not the rendering.**  `init` writes a tool-owned file *only when
  absent* and keeps what is there, because those files are in `hooks/hookio.SPUD_PATHS` and Spud edits them by hand
  after the first run.  `home sync` is the command that takes that edit back on purpose, so it writes whatever differs
  -- and keeps a copy first.  Neither renders a file the other way, which is the reason both read `shipped_text`.
- **A copy is kept before every overwrite**, through `state/backup.keep_copy` and under the `home-sync` folder of the
  home's backups directory, beside `vault install`'s own copies and the ledger's daily ones.  The folder says which
  command, the stamp says when and the path inside says what; each is named in the output.  The stamp is one no other
  run of this command has used (`state/backup.copy_stamp`), since the clock reads to the second and a run that shared a
  second with the one before it would overwrite its copies.
- **Nothing is written until everything can be.**  Every file is rendered, and the vault's lock read, before the first
  byte lands: a `{{mark}}` with no value refuses the whole sync naming the mark, exactly as `init` refuses, rather than
  leaving a home half old and half new.
- **The home's own files are never touched.**  The ledger's rendered notes, the reports, `docs/`, the database and
  `spud.config.json` are not the tool's, and the last line of every run says so.

`--check` writes nothing and prints what would change, file by file.  It is what Spud shows Eric before the first real
sync of a home that has been hand-kept for months, so it names the copy it *would* keep as well as the file it would
replace; the vault half answers the same question through `vaultinstall.install_vault(check_only=True)`, which makes no
download at all.

Nothing on the hook path imports this module: `cli/cliparser`, `commands/doctor` and `commands/homeinit` are its
readers, and all three are off it (`tests/test_package.py`'s HOOK_PATH).  It must not import `commands/homeinit` or
`commands/doctor`, both of which import it -- and `homeinit` imports `doctor`.
"""

import re

from . import projectboards, vaultinstall, vaultlock
from ..core import kernel, shipped
from ..state import actors, backup, ledgerdb

# The seven files `spud init` writes into a fresh home, in the order the `spud init` design lists them (section 2.2).
# Each relative path is both the path under `<tool>/share/` and the path in the home: share/ mirrors the home's layout
# for these.  Not every shipped file is here -- `share/agents/spudagent.md` is rendered to user scope by `project
# install`, never into a home, and `share/obsidian/` is the vault's, installed under `.obsidian/`.
SCAFFOLDING = ("CLAUDE.md", "ledger/Home.md", "ledger/Spud.md", "ledger/Board.base", "ledger/Fleet.base",
               "ledger/_templates/ticket.md", "ledger/_templates/spudagent.md")
# The one shipped directory whose path in a home is not its path under share/.  Claude Code reads a skill from
# `.claude/skills/<name>/`, and `share/.claude/` cannot be shipped from this repository at all: a skill under the tool's
# own `.claude/skills/` is a skill of *this* checkout's sessions, as the tool's own `.claude/agents/` once proved.
# So the tool ships `share/skills/<name>/` and the home gets `.claude/skills/<name>/`.  Read from the directory rather
# than listed, so a second shipped skill needs no edit here.
SKILLS = "skills"
HOME_SKILLS = ".claude/skills"
# Where a copy of a file this command overwrites goes, under the home's backups directory.
HOME_SYNC_BACKUPS = "home-sync"
# The four marks that have no value in a home with no project (`core/shipped.marks` leaves them empty).  A line
# carrying one is a line about project 1, so with no project registered `without_project_lines` leaves that line out and
# the command says how many it left out: the shipped prose is share/'s to write and a command's to place.
PROJECT_MARKS = ("project_key", "project_name", "project_root", "project_remote")
MARK_LEFT = re.compile(r"\{\{[a-z_]+\}\}")
NOT_RENDERED = ("the shipped %s still carries %s after rendering; core/shipped.MARKS and <tool>/share/ have drifted"
                " (tests/test_share.py is the guard)")
UNTOUCHED = ("the ledger's rendered notes, the reports, docs/, the database and spud.config.json are untouched:"
             " they are the home's, not the tool's")
NOTHING_WRITTEN = "--check: nothing was written"
SYNC_REFUSED = ("%d download(s) refused, so those plugins and themes are not installed: run"
                " `%s --as spud vault install` once the network is back")
# What doctor says when a tool-owned file in the home is not what the tool would write now.  Notes, never problems, and
# for `commands/vaultlock.DIFFERS`' reason: drift is the normal state of a home somebody works in -- these files are
# Spud's to edit between syncs (`hooks/hookio.SPUD_PATHS`) -- nothing is broken, and only this command settles it.  A
# problem would also fail every later `spud init`, which stops on each one (`commands/homeinit.verify`).
SYNC_HINT = "`%s --as spud home sync` writes it (`--check` first prints what would change)"
HOME_DIFFERS = "the home's %s differs from the one the tool ships; " + SYNC_HINT
HOME_ABSENT = "the home has no %s, which the tool ships; " + SYNC_HINT
# A share/ this tool cannot render is one finding rather than a crash inside doctor: doctor reports, it never refuses.
SHARE_UNREADABLE = "the tool's shipped files cannot be rendered for this home: %s"


# ----------------------------------------------------------------------------
# What the tool owns in a home
# ----------------------------------------------------------------------------


def home_relative(rel):
    """Where a shipped file goes in a home, given its path under `<tool>/share/`.

    Itself for every file whose two paths are the same, and `.claude/skills/…` for the one directory whose are not."""
    if rel == SKILLS or rel.startswith(SKILLS + "/"):
        return HOME_SKILLS + rel[len(SKILLS):]
    return rel


def shipped_skills(ctx):
    """Every file under `<tool>/share/skills/`, share-relative and sorted: the reference skill and anything shipped
    beside it.  A tool with no such directory ships no skill, which is the shape of every home built before one."""
    root = shipped.share_dir(ctx) / SKILLS
    if not root.is_dir():
        return []
    return [SKILLS + "/" + path.relative_to(root).as_posix() for path in sorted(root.rglob("*")) if path.is_file()]


def tool_owned(ctx):
    """[(the path under `<tool>/share/`, the path in the home)] for every file the tool owns in a home: the scaffolding
    and every shipped skill.  The one list `spud init` and `spud home sync` both walk."""
    return [(rel, home_relative(rel)) for rel in tuple(SCAFFOLDING) + tuple(shipped_skills(ctx))]


def unrendered(text):
    """The marks a rendered shipped file still carries, which must be none: a `{{…}}` reaching a person's home
    unrendered is `core/shipped.MARKS` and `<tool>/share/` out of step, and a command refuses rather than write it."""
    return sorted(set(MARK_LEFT.findall(text)))


def without_project_lines(text):
    """`text` without the lines that carry a project mark, and how many were dropped: what a home with no project gets.

    Each of those lines is a sentence about project 1 -- the `This machine` bullet naming its root and remote, the
    ticket key's project in `ledger/Home.md`, the `<key>:<glob>` example in the deliverable-path law -- and a line
    rendered with the marks empty would say something untrue rather than nothing.  The count is reported, because a
    person who registers a project later will want those lines back and the files are theirs to edit from then on."""
    marks = tuple(shipped.MARK % name for name in PROJECT_MARKS)
    lines = text.split("\n")
    kept = [line for line in lines if not any(mark in line for mark in marks)]
    return "\n".join(kept), len(lines) - len(kept)


def shipped_text(ctx, rel, values, project):
    """(the text a shipped file has in this home, the lines dropped for want of a project).

    Raises when the file is gone (`core/shipped.read`) or when a mark is left after rendering, which is a mark with no
    value in `core/shipped.MARKS`: both before the caller has written anything."""
    text = shipped.read(ctx, rel)
    dropped = 0
    if project is None:
        text, dropped = without_project_lines(text)
    text = shipped.render(text, values)
    left = unrendered(text)
    if left:
        raise kernel.SpudError(kernel.EXIT_ERROR, NOT_RENDERED % (rel, ", ".join(left)))
    return text, dropped


def rendered_files(ctx, project):
    """[(share-relative, home-relative, text)] for every tool-owned file, and the lines dropped across all of them.

    Every file is rendered here, before the caller writes the first one: that is what makes a mark with no value refuse
    the whole sync rather than leave a home half regenerated."""
    values = shipped.marks(ctx, project)
    out, dropped = [], 0
    for rel, home_rel in tool_owned(ctx):
        text, count = shipped_text(ctx, rel, values, project)
        dropped += count
        out.append((rel, home_rel, text))
    return out, dropped


def project_one(con):
    """Project 1's row as a dict, or None: the row `core/shipped.marks` renders a home's prose from."""
    row = con.execute("SELECT * FROM projects WHERE id = 1").fetchone()
    return None if row is None else dict(row)


# ----------------------------------------------------------------------------
# The sync
# ----------------------------------------------------------------------------


def sync_files(ctx, files, stamp, check_only, record):
    """The rendered half: each tool-owned file written when absent, kept when it already holds the shipped text, and
    replaced -- with a copy kept first -- when it holds something else."""
    for rel, home_rel, text in files:
        path = ctx.home / home_rel
        want = text.encode("utf-8")
        have = path.read_bytes() if path.is_file() else None
        if have == want:
            record["unchanged"].append(home_rel)
            continue
        if have is None:
            record["written"].append(home_rel)
        else:
            record["replaced"].append({"path": home_rel, "source": rel,
                                       "backup": str(backup.keep_copy(ctx, HOME_SYNC_BACKUPS, stamp, home_rel, have, check_only))})
        if not check_only:
            kernel.write_whole(path, text)


def sync_home(ctx, project, check_only=False, projects=()):
    """(record, lines): every file the tool owns in this home, regenerated from `<tool>/share/`, and the Kanban board
    of each of `projects` that has none (`commands/projectboards`, SPD-324).

    Takes no actor and opens no database -- `cmd_home_sync` does the ownership check and reads project 1 and the
    non-archived projects -- so the two preconditions here are the ones about the tool's own files: every shipped file
    and every missing board renders for this home, and the vault's lock is one this tool will install from.  Both are
    checked before the first byte is written.

    A board is written only when absent and is never replaced, so it is no tool-owned file: it is not in `tool_owned`,
    doctor names no drift in one, and `--check` says only which would be written.
    """
    files, dropped = rendered_files(ctx, project)
    boards = projectboards.plan_boards(ctx, projects)
    vaultlock.read_lock(ctx)  # a lock this tool will not install from refuses here, before anything is written
    stamp = backup.copy_stamp(ctx, HOME_SYNC_BACKUPS)  # this run's own folder, whatever the clock says
    record = {"home": str(ctx.home), "share": str(shipped.share_dir(ctx)), "check": bool(check_only),
              "written": [], "replaced": [], "unchanged": [], "dropped_lines": dropped,
              "backups": str(backup.backups_dir(ctx) / HOME_SYNC_BACKUPS / stamp)}
    sync_files(ctx, files, stamp, check_only, record)
    record["boards"] = projectboards.write_boards(ctx, boards, check_only)
    record["vault"], vault_lines = vaultinstall.install_vault(ctx, check_only=check_only)
    return record, sync_lines(ctx, record, vault_lines)


def sync_lines(ctx, record, vault_lines):
    """What the command prints: the count, then one line per file it wrote or replaced, one naming the files that
    already held the shipped text, the vault's own report, and the sentence about what a sync never touches.

    `--check` says every one of them in the conditional, and ends on the line that says nothing was written: the report
    is what a person decides on, and it has to be readable with nothing else in front of them."""
    check = record["check"]
    lines = ["home %s: %d file(s) %s, %d %s, %d already the shipped text"
             % (record["home"], len(record["written"]), "would be written" if check else "written",
                len(record["replaced"]), "would be replaced" if check else "replaced", len(record["unchanged"]))]
    lines.extend("  %s %s" % ("would write" if check else "wrote", rel) for rel in record["written"])
    if record["replaced"]:
        # The folder once and the files under it, rather than the same long path on every line: each copy is at its own
        # path inside it, and --json carries the exact path of each for anyone who wants to open one.
        lines.append("  a copy of each replaced file %s under %s/, at its own path in the home"
                     % ("would be kept" if check else "is kept", record["backups"]))
    lines.extend("  %s %s" % ("would replace" if check else "replaced", r["path"]) for r in record["replaced"])
    if record["unchanged"]:
        lines.append("  unchanged %s" % ", ".join(record["unchanged"]))
    if record["dropped_lines"]:
        lines.append("  %d line(s) about project 1 left out, for want of one: `project add`, then they are yours to write back"
                     % record["dropped_lines"])
    lines.extend("  %s" % line for line in projectboards.board_lines(record["boards"], check))
    lines.extend("  %s" % line for line in vault_lines)
    if record["vault"]["refused"]:
        lines.append("  " + SYNC_REFUSED % (len(record["vault"]["refused"]), ctx.launcher))
    lines.append(UNTOUCHED)
    if check:
        lines.append(NOTHING_WRITTEN)
    return lines


def cmd_home_sync(ctx, args):
    """`spud home sync [--check]` (Spud's): every file the tool owns in this home, written again from `<tool>/share/`.

    A refused download is never fatal, as it is not in `spud init`'s step 4b: the rendered files and the vault's
    settings are written whatever the network did, and the line names `vault install` for later."""
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "regenerating the files the tool owns in the home")
        project = project_one(con)
        projects = [dict(row) for row in con.execute(
            "SELECT key, name, archived_at FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall()]
    finally:
        con.close()
    record, lines = sync_home(ctx, project, args.check, projects)
    return kernel.Result(record, "\n".join(lines))


# ----------------------------------------------------------------------------
# doctor: where the home and the shipped copy have drifted
# ----------------------------------------------------------------------------


def home_findings(ctx):
    """[(what, sentence)] for every tool-owned file the home does not have, or has and has changed.

    Absent is a finding here where it is not one for the vault (`commands/vaultlock.vault_findings`), and the asymmetry
    is real: a plugin the home does not have is a plugin nobody installed, while a tool-owned file the home does not
    have is a file this home was built before the tool shipped -- which is exactly what `home sync` is for, and the
    shape every home takes the first time a new shipped file lands.
    """
    con = ledgerdb.connect(ctx)
    try:
        project = project_one(con)
    finally:
        con.close()
    try:
        files, _dropped = rendered_files(ctx, project)
    except kernel.SpudError as e:  # a shipped file gone, or a mark with no value: one finding, never a traceback
        return [(str(shipped.share_dir(ctx)), SHARE_UNREADABLE % e.message)]
    findings = []
    for _rel, home_rel, text in files:
        path = ctx.home / home_rel
        if not path.is_file():
            findings.append((home_rel, HOME_ABSENT % (home_rel, ctx.launcher)))
        elif path.read_bytes() != text.encode("utf-8"):
            findings.append((home_rel, HOME_DIFFERS % (home_rel, ctx.launcher)))
    return findings
