"""commands/projectboards: one Kanban `ledger/<project name>.base` per registered project (SPD-324), and which of a
home's `ledger/*.base` files are those boards rather than the views the tool ships.

A project's board is the Kanban view of the shipped `ledger/Board.base` -- its formulas, its properties and its one
`notion-board` view -- filtered to `project == "<key>"` and named after the project.  It is rendered from one shipped
template, `share/ledger/_templates/project.base`, with the two marks `{{project_key}}` and `{{project_name}}` filled
from the project's row.  The template is a `.base` so that `tests/test_share.py`'s restricted reader and its view-type
guard read it with every other shipped `.base`, and it sits under `_templates/` because every glob that takes
`share/ledger/*.base` for the tool's shipped views (`vault capture`'s stale files, doctor's vault notes) is not
recursive, and `commands/homesync.SCAFFOLDING` names its files one by one: the template is never a shipped view, never
written into a home's own `ledger/_templates/`, and never captured over.

Four rules an edit here must keep.

- **Written only when absent.**  `project add`, `project install`, `home sync` and `spud init` (project 1's, at its
  step 7, through `projects/install.install_with_board`, which `project install` calls too -- SPD-325) each write a
  board that is not there and keep one that is, whatever it holds: a board is the home's to tune once written (the
  first one, Spud.base, was built by hand), so no command takes an edit of one back, and none keeps a copy because none
  overwrites.
- **Archived projects get none**, and a project whose name is not one plain file name, starts with a dot, or is the
  name of a view the tool ships (`Board`, `Fleet`) gets none either: the command says which and why rather than writing
  a file somewhere else or over a shipped view.
- **A rename moves the board, and never rewrites it** (SPD-325).  `project edit --name` renames `ledger/<old>.base` to
  `ledger/<new>.base`, bytes untouched, when the old one is there, the new name can name a board and nothing is at the
  new path; otherwise it leaves the old file where it is and says, in one line, that it is no longer the project's
  board and why (`plan_rename`, `rename_board`).
- **A board is never the tool's.**  `board_paths` is the one answer to "which `ledger/*.base` files are boards": the
  name of every registered project, archived ones included (a board outlives its project's archiving), and every name
  a project was renamed from (a board a rename could not move outlives the rename), less the names a shipped view
  holds.  `vault capture` leaves them out of what it ships, and doctor's vault notes leave them out of the
  missing-plugin scan, whose note the same Kanban view in `Board.base` already carries once for the whole home;
  doctor names each board a rename left behind in a note of its own (`orphan_notes`).

Past 250 lines since SPD-325 and kept whole: it is one file per project, from the name that may or may not give one,
through writing, moving and recognising it, and every part reads `name_problem` and `board_rel`; a rename half here
and half elsewhere would be two answers to "which file is this project's board".

Nothing on the hook path imports this module: `projects/registry`, `projects/install`, `commands/homesync`,
`commands/homeinit`, `commands/vaultcapture` and `commands/doctor` are its readers, all off it.  It imports `commands/vaultlock` for the
plain-file-name rule and must not import `commands/homesync`, which imports it.
"""

import json
import os
import re

from . import vaultlock
from ..core import kernel, shipped
from ..state import ledgerdb

BOARD_TEMPLATE = "ledger/_templates/project.base"  # under <tool>/share/
BOARD_SUFFIX = ".base"
# A name YAML reads back as the same string when written bare: a letter first, so no number, date or indicator
# (`-`, `?`, `[`, `&`, `*`, `!`, `|`, `>`, `'`, `"`, `%`, `@`, `#`), and none of `:` or `#` after it.  Everything
# else is written as a double-quoted scalar, whose escapes are JSON's.
PLAIN_BOARD_NAME = re.compile(r"[A-Za-z](?:[A-Za-z0-9 _.()-]*[A-Za-z0-9_.)])?")
YAML_TYPED_WORDS = frozenset(("true", "false", "null", "yes", "no", "on", "off", "y", "n"))
BOARD_MARK_LEFT = re.compile(r"\{\{[a-z_]+\}\}")
BOARD_NOT_RENDERED = "the shipped %s still carries %s after rendering; core/shipped.MARKS and <tool>/share/ have drifted"
BOARD_DOT_NAME = "starts with a dot, which Obsidian hides"
BOARD_NUL_NAME = "holds a NUL"
BOARD_SHIPPED_NAME = "is the name of the view %s the tool ships"
BOARD_ARCHIVED = "it is archived"
BOARD_UNUSABLE = "its name %r %s"
BOARD_WROTE = "%s %s, project %s's board"
BOARD_KEPT = "%s is already there, so project %s's board is left as it is"
NO_BOARD = "no board for project %s: %s"
# `project edit --name`'s one line about the board (SPD-325), and doctor's note about a board a rename left behind.
BOARD_MOVED = "moved %s to %s, project %s's board, its contents as they were"
BOARD_LEFT = "%s stays where it is and is no longer project %s's board: %s"
NOTHING_TO_MOVE = "project %s had no board to move; %s"
BOARD_TAKEN = "%s is already there, and is its board now"
BOARD_RENAMED_UNUSABLE = "the project gets none, as its new name %r %s"
BOARD_ARCHIVED_NONE = "it gets none while it is archived"
BOARD_LATER = "`home sync` or `project install` writes %s"
BOARD_MOVE_FAILED = "moving it to %s failed (%s)"
ORPHAN_BOARD = ("%s was project %s's board under a name it no longer has, and is no project's board now: it still shows"
                " that project's tickets, and it is the home's to keep as a view of its own or to delete")


def board_rel(name):
    """The board's path in the home: `ledger/<name>.base`."""
    return "%s/%s%s" % (vaultlock.VAULT_BASES, name, BOARD_SUFFIX)


def board_name_scalar(name):
    """`name` as it goes into the template's `name:` line: bare when YAML reads it back as the same string, otherwise
    double-quoted, which is JSON's string syntax and a subset of YAML's."""
    if PLAIN_BOARD_NAME.fullmatch(name) and name.lower() not in YAML_TYPED_WORDS:
        return name
    return json.dumps(name, ensure_ascii=False)


def shipped_views(ctx):
    """{casefolded file name: its path in the home} for every `.base` view the tool ships, `share/ledger/*.base`.
    Casefolded because a home's file system usually is not case-sensitive, and `board.base` there is `Board.base`."""
    folder = shipped.share_dir(ctx) / vaultlock.VAULT_BASES
    return {path.name.casefold(): "%s/%s" % (vaultlock.VAULT_BASES, path.name) for path in sorted(folder.glob("*" + BOARD_SUFFIX))}


def name_problem(name, known):
    """Why a project's name cannot name its board, as a phrase; None when it can.  `known` is `shipped_views`."""
    problem = vaultlock.component_problem(name)
    if problem:
        return problem
    if name.startswith("."):
        return BOARD_DOT_NAME
    if "\x00" in name:
        return BOARD_NUL_NAME
    shipped_view = known.get((name + BOARD_SUFFIX).casefold())
    if shipped_view:
        return BOARD_SHIPPED_NAME % shipped_view
    return None


def board_text(ctx, project):
    """The board a project gets, rendered from the shipped template.  Raises before anything is written when the
    template is gone (`core/shipped.read`) or a mark is left over after rendering."""
    text = shipped.render(shipped.read(ctx, BOARD_TEMPLATE),
                          {"project_key": project["key"], "project_name": board_name_scalar(project["name"])})
    left = sorted(set(BOARD_MARK_LEFT.findall(text)))
    if left:
        raise kernel.SpudError(kernel.EXIT_ERROR, BOARD_NOT_RENDERED % (BOARD_TEMPLATE, ", ".join(left)))
    return text


def plan_board(ctx, project, known=None):
    """What to do about one project's board, decided and rendered before anything is written:
    {"project", "path", "board": "write" | "kept" | "skipped", "reason" (skipped), "text" (write)}.

    `project` is a row or a dict with `key`, `name` and `archived_at`."""
    known = shipped_views(ctx) if known is None else known
    row = dict(project)
    key, name = row["key"], row["name"]
    if row.get("archived_at"):
        return {"project": key, "path": None, "board": "skipped", "reason": BOARD_ARCHIVED}
    problem = name_problem(name, known)
    if problem:
        return {"project": key, "path": None, "board": "skipped", "reason": BOARD_UNUSABLE % (name, problem)}
    rel = board_rel(name)
    path = ctx.home / rel
    if path.exists() or path.is_symlink():
        return {"project": key, "path": rel, "board": "kept"}
    return {"project": key, "path": rel, "board": "write", "text": board_text(ctx, row)}


def plan_boards(ctx, projects):
    """`plan_board` for every row of `projects`, in their order, the shipped views read once."""
    known = shipped_views(ctx)
    return [plan_board(ctx, p, known) for p in projects]


def write_boards(ctx, planned, check_only=False):
    """Write each planned board that is still absent, and answer what each one came to, without its text:
    "written" (or, with `check_only`, the board that would be), "kept" or "skipped".  A board that appeared since it
    was planned is kept like any other."""
    out = []
    for entry in planned:
        done = {k: v for k, v in entry.items() if k != "text"}
        if entry["board"] == "write":
            path = ctx.home / entry["path"]
            if path.exists() or path.is_symlink():
                done["board"] = "kept"
            else:
                if not check_only:
                    kernel.write_whole(path, entry["text"])
                done["board"] = "written"
        out.append(done)
    return out


def board_lines(done, check_only=False):
    """One line per board: what was written, what was kept, and which project got none and why."""
    out = []
    for entry in done:
        if entry["board"] == "written":
            out.append(BOARD_WROTE % ("would write" if check_only else "wrote", entry["path"], entry["project"]))
        elif entry["board"] == "kept":
            out.append(BOARD_KEPT % (entry["path"], entry["project"]))
        else:
            out.append(NO_BOARD % (entry["project"], entry["reason"]))
    return out


def plan_rename(ctx, project, new_name, known=None):
    """What `project edit --name` does about the project's board, decided before the edit's transaction commits and
    carried out by `rename_board` after it (SPD-325):
    {"project", "from", "to", "taken", "board": "move" | "left" | "none", "why"}.

    `from` is `ledger/<old name>.base` when the old name could name a board, `to` the same for the new name; either is
    None when its name cannot.  The board moves when the old one is there, the new name can name one, and no file is
    at the new path already -- or the one there is the old board itself, which is a change of case on a file system
    that ignores case.  An old board that cannot move is `left` where it is, with `why`: the new path is taken, or the
    new name gets no board.  With no old board there is nothing to move (`none`), and `why` says what the project's
    board is now.  An archived project's board moves like any other, since a board outlives its project's archiving."""
    known = shipped_views(ctx) if known is None else known
    row = dict(project)
    key, old_name = row["key"], row["name"]
    old_rel = None if name_problem(old_name, known) else board_rel(old_name)
    old = ctx.home / old_rel if old_rel else None
    problem = name_problem(new_name, known)
    new_rel = None if problem else board_rel(new_name)
    new = ctx.home / new_rel if new_rel else None
    had = old is not None and present(old)
    taken = new is not None and present(new) and not (had and same_file(old, new))
    entry = {"project": key, "from": old_rel, "to": new_rel, "taken": taken}
    if had and new_rel and not taken:
        return dict(entry, board="move", why=None)
    if problem:
        why = BOARD_RENAMED_UNUSABLE % (new_name, problem)
    elif row.get("archived_at") and not taken:
        why = BOARD_ARCHIVED_NONE
    else:
        why = None
    return dict(entry, board="left" if had else "none", why=why)


def present(path):
    """Whether anything, a dangling symlink included, is at `path`: `plan_board`'s test of a board that is there."""
    return path.exists() or path.is_symlink()


def same_file(a, b):
    """Whether two paths are one directory entry's, read without following a symlink: `ledger/Old.base` and
    `ledger/old.base` are one file where the file system ignores case."""
    try:
        sa, sb = a.lstat(), b.lstat()
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def rename_board(ctx, planned):
    """Carry out `plan_rename`'s plan: move the old board to the new name, its bytes untouched, and answer what came of
    it with its one line.  A board is the home's to tune once written, so a move is a rename and never a rewrite from
    the template.  A file that appeared at the new path since the plan, or a rename the file system refuses, leaves the
    old board where it is, and the line says so."""
    done = dict(planned)
    if planned["board"] == "move":
        old, new = ctx.home / planned["from"], ctx.home / planned["to"]
        if present(new) and not same_file(old, new):
            done.update(board="left", taken=True)
        else:
            try:
                os.rename(old, new)
            except OSError as e:
                done.update(board="left", why=BOARD_MOVE_FAILED % (planned["to"], e.strerror or e))
    return done, rename_line(done)


def rename_line(done):
    """The one line `project edit --name` prints about the board."""
    key = done["project"]
    if done["board"] == "move":
        return BOARD_MOVED % (done["from"], done["to"], key)
    tail = done["why"] or (BOARD_TAKEN if done["taken"] else BOARD_LATER) % done["to"]
    if done["board"] == "left":
        return BOARD_LEFT % (done["from"], key, tail)
    return NOTHING_TO_MOVE % (key, tail)


def former_names(con):
    """{name: project key} for every name a project has been renamed from, read from the `project.edited` events
    `project edit --name` writes, oldest first so a name used twice answers with the last project that left it."""
    out = {}
    for row in con.execute("SELECT data FROM events WHERE kind = 'project.edited' AND data IS NOT NULL ORDER BY id").fetchall():
        try:
            data = json.loads(row["data"])
        except ValueError:
            continue
        before = data.get("from") if isinstance(data, dict) else None
        if "name" in (data.get("fields") or ()) and isinstance(before, dict) and isinstance(before.get("name"), str):
            out[before["name"]] = data.get("project")
    return out


def board_paths(ctx, con):
    """{casefolded home-relative path} of every file in `ledger/` that is a project's board: `ledger/<name>.base` for
    every registered project, archived ones included, whose name can name one -- and for every name a project was
    renamed from (SPD-325), since `project edit --name` leaves the old board where it is when it cannot move it, and
    that file is still a board and never a view the tool ships.  Casefolded for `shipped_views`' reason; test a path
    with `is_board`."""
    known = shipped_views(ctx)
    names = [row["name"] for row in con.execute("SELECT name FROM projects").fetchall()] + list(former_names(con))
    return frozenset(board_rel(name).casefold() for name in names if name_problem(name, known) is None)


def orphan_notes(ctx, con):
    """doctor's note for each board a rename left behind: `ledger/<former name>.base` that is there, and is no current
    project's board.  A note and not a problem: it still shows the project's tickets, since it filters on the key, and
    whether to keep it as a view of the home's own or delete it is the owner's call, never a command's."""
    known = shipped_views(ctx)
    current = {board_rel(row["name"]).casefold(): row["key"]
               for row in con.execute("SELECT key, name FROM projects").fetchall() if name_problem(row["name"], known) is None}
    notes = []
    for name, key in sorted(former_names(con).items()):
        if name_problem(name, known) is not None:
            continue
        rel = board_rel(name)
        if rel.casefold() not in current and present(ctx.home / rel):
            notes.append(ORPHAN_BOARD % (rel, key))
    return notes


def home_boards(ctx):
    """(`board_paths`, `orphan_notes`) read from this home's database, or (the empty set, []) when it has no usable
    one: doctor reads the vault of a home with no database too, and there every `.base` file is what it would be
    without SPD-324."""
    try:
        con = ledgerdb.connect(ctx)
    except kernel.SpudError:
        return frozenset(), []
    try:
        return board_paths(ctx, con), orphan_notes(ctx, con)
    finally:
        con.close()


def is_board(rel, boards):
    """Whether the home-relative `rel` is one of `boards` (`board_paths`)."""
    return rel.casefold() in boards
