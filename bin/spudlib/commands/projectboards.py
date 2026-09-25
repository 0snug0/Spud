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

Three rules an edit here must keep.

- **Written only when absent.**  `project add`, `project install` and `home sync` each write a board that is not
  there and keep one that is, whatever it holds: a board is the home's to tune once written (the first one, Spud.base,
  was built by hand), so no command takes an edit of one back, and none keeps a copy because none overwrites.
- **Archived projects get none**, and a project whose name is not one plain file name, starts with a dot, or is the
  name of a view the tool ships (`Board`, `Fleet`) gets none either: the command says which and why rather than writing
  a file somewhere else or over a shipped view.
- **A board is never the tool's.**  `board_paths` is the one answer to "which `ledger/*.base` files are boards": the
  name of every registered project, archived ones included (a board outlives its project's archiving), less the names
  a shipped view holds.  `vault capture` leaves them out of what it ships, and doctor's vault notes leave them out of
  the missing-plugin scan, whose note the same Kanban view in `Board.base` already carries once for the whole home.

Nothing on the hook path imports this module: `projects/registry`, `projects/install`, `commands/homesync`,
`commands/vaultcapture` and `commands/doctor` are its readers, all off it.  It imports `commands/vaultlock` for the
plain-file-name rule and must not import `commands/homesync`, which imports it.
"""

import json
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


def board_paths(ctx, con):
    """{casefolded home-relative path} of every file in `ledger/` that is a project's board: `ledger/<name>.base` for
    every registered project, archived ones included, whose name can name one.  Casefolded for `shipped_views`' reason;
    test a path with `is_board`."""
    known = shipped_views(ctx)
    return frozenset(board_rel(row["name"]).casefold() for row in con.execute("SELECT name FROM projects").fetchall()
                     if name_problem(row["name"], known) is None)


def home_board_paths(ctx):
    """`board_paths` read from this home's database, or the empty set when it has no usable one: doctor reads the vault
    of a home with no database too, and there every `.base` file is what it would be without this ticket."""
    try:
        con = ledgerdb.connect(ctx)
    except kernel.SpudError:
        return frozenset()
    try:
        return board_paths(ctx, con)
    finally:
        con.close()


def is_board(rel, boards):
    """Whether the home-relative `rel` is one of `boards` (`board_paths`)."""
    return rel.casefold() in boards
