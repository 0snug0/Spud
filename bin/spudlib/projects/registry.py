"""projects/registry: project add, list, show, edit, and their validation.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import os
import re
from pathlib import Path

from ..commands import reportentry, settings_sync
from ..core import homeconf, kernel
from ..hooks import worktrees
from ..state import actors, ledgerdb, lookup


# ----------------------------------------------------------------------------
# Projects and sessions (SPD-014)
# ----------------------------------------------------------------------------
#
# docs/design/2026-09-14-cross-repository-projects.md.  A project is a registered repository; project 1 is the tool
# repository, `spud`; the home is no project (SPD-097).
# A session launched in another project is Spud only after `/spud` claims it (projects.sessions = 'claim', the default
# for `project add`, Eric 2026-09-14), or when its project is 'always', as project spud is until `home move`; a session
# launched in the home is always Spud's.

PROJECT_KEY_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]*")


def identity_chain(path):
    """The file identities of a path and every ancestor of it that exists."""
    out, cur = set(), os.path.abspath(str(path))
    while True:
        ident = worktrees.file_identity(cur)
        if ident is not None:
            out.add(ident)
        parent = os.path.dirname(cur)
        if parent == cur:
            return out
        cur = parent


def validate_project_root(ctx, con, path, exclude_id=None):
    """The rules 1 to 3 of `project add` (design section 1.2): an existing directory, the root of a git repository's main
    checkout, not the home, not inside an active project's root and not containing one.  Returns the resolved root."""
    try:
        root = Path(os.path.expanduser(str(path))).resolve(strict=True)
    except (OSError, RuntimeError):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not an existing directory" % path)
    if not root.is_dir():
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a directory" % root)
    ident = worktrees.file_identity(root)
    if ident == worktrees.file_identity(ctx.home):  # SPD-097: asked before git, since the home is no git repository
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is Spud's home, which is not a project (SPD-097); register the tool repository or another checkout" % root)
    proc = homeconf.run_git(root, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", timeout=30)
    lines = proc.stdout.strip().split("\n") if proc.returncode == 0 else []
    if len(lines) != 2:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a git repository with a work tree (git rev-parse: %s)" % (root, (proc.stderr or proc.stdout).strip() or "no output"))
    toplevel, common = lines
    if worktrees.file_identity(toplevel) != worktrees.file_identity(root):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is inside the repository %s, not its root; register the root" % (root, toplevel))
    if worktrees.file_identity(common) != worktrees.file_identity(os.path.join(toplevel, ".git")):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is a linked worktree; register the main checkout, %s" % (root, os.path.dirname(common.rstrip("/"))))
    chain = identity_chain(root)
    for other in con.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall():
        if other["id"] == exclude_id:
            continue
        other_root = worktrees.project_root(ctx, other)
        other_ident = worktrees.file_identity(other_root)
        if other_ident is None:
            continue
        if other_ident in chain:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is inside project %s's root %s; nested roots would make the nearest root ambiguous" % (root, other["key"], other_root))
        if ident in identity_chain(other_root):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s contains project %s's root %s; nested roots would make the nearest root ambiguous" % (root, other["key"], other_root))
    return str(root)


def check_project_key(con, key):
    if not PROJECT_KEY_RE.fullmatch(key or ""):
        raise kernel.SpudError(kernel.EXIT_ERROR, "--key %r must be lower-case letters, digits and hyphens, starting with a letter, at most 32 characters" % key)
    if key == kernel.HOME_KEY:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the key %s is reserved: it names Spud's home in a deliverable glob (home:<glob>), and the home is not a project (SPD-097)" % key)
    if con.execute("SELECT 1 FROM projects WHERE key = ?", (key,)).fetchone():
        raise kernel.SpudError(kernel.EXIT_ERROR, "project %s exists already" % key)


def check_prefixes(con, ticket_prefix, team_prefix, exclude_id=None):
    """Rule 5: upper-case letters and digits, the two different, and neither equal to any prefix of any other project,
    archived ones included, so no ticket key can ever equal a team key."""
    for option, value in (("--ticket-prefix", ticket_prefix), ("--team-prefix", team_prefix)):
        if not PREFIX_RE.fullmatch(value or ""):
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s %r must be upper-case letters and digits, starting with a letter" % (option, value))
    if ticket_prefix == team_prefix:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the ticket and team prefixes must differ (both %s)" % ticket_prefix)
    for other in con.execute("SELECT * FROM projects ORDER BY id").fetchall():
        if other["id"] == exclude_id:
            continue
        for value in (ticket_prefix, team_prefix):
            if value in (other["ticket_prefix"], other["team_prefix"]):
                raise kernel.SpudError(kernel.EXIT_ERROR, "prefix %s is project %s's already; prefixes are unique across every project, archived ones included,"
                                            " so no ticket key equals a team key" % (value, other["key"]))


def origin_head_branch(root):
    proc = homeconf.run_git(root, "rev-parse", "--abbrev-ref", "origin/HEAD", timeout=10)
    value = proc.stdout.strip() if proc.returncode == 0 else ""
    return value[len("origin/"):] if value.startswith("origin/") and len(value) > len("origin/") else None


def project_dict(ctx, con, p):
    root = worktrees.project_root(ctx, p)
    # SPD-097: every project's hooks live in its untracked local settings; the home's own .claude/settings.json belongs to no project.
    settings = os.path.join(root, ".claude", "settings.local.json")
    record = json.loads(p["installed"]) if p["installed"] else None
    return {
        "key": p["key"], "name": p["name"], "ticket_prefix": p["ticket_prefix"], "team_prefix": p["team_prefix"], "root": root,
        "default_branch": p["default_branch"], "landing": p["landing"], "sessions": p["sessions"], "remote": p["remote"],
        "tickets": con.execute("SELECT count(*) FROM tickets WHERE project_id = ?", (p["id"],)).fetchone()[0],
        "settings_file": settings, "installed": settings_sync.settings_hold_hooks(ctx, settings, p["key"]),
        "install_record": None if record is None else {k: v for k, v in record.items() if k != "original"},
        "created_at": p["created_at"], "archived_at": p["archived_at"],
    }


def format_project(d):
    lines = [
        "%s: %s%s" % (d["key"], d["name"], " (archived %s)" % kernel.fm_date(d["archived_at"]) if d["archived_at"] else ""),
        "root            %s" % d["root"],
        "prefixes        %s-nnn tickets, %s-nnn teams (%d ticket%s)" % (d["ticket_prefix"], d["team_prefix"], d["tickets"], "" if d["tickets"] == 1 else "s"),
        "default branch  %s" % d["default_branch"],
        "landing         %s" % d["landing"],
        "sessions        %s" % d["sessions"],
        "remote          %s" % (d["remote"] or "-"),
        "installed       %s (%s)" % ("yes" if d["installed"] else "no", d["settings_file"]),
    ]
    return "\n".join(lines)


def cmd_project_add(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "adding a project")
        reportentry.check_next(con, actor, args)
        root = validate_project_root(ctx, con, args.path)
        check_project_key(con, args.key)
        check_prefixes(con, args.ticket_prefix, args.team_prefix)
        if con.execute("SELECT key FROM projects WHERE root_path = ?", (root,)).fetchone():
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s is already the root of project %s (archived); `spud --as spud project edit` changes a root"
                            % (root, con.execute("SELECT key FROM projects WHERE root_path = ?", (root,)).fetchone()["key"]))
        remote = homeconf.git_remote_url(root)
        branch = args.default_branch or origin_head_branch(root) or "main"
        name = args.name or os.path.basename(root)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            check_project_key(con, args.key)
            check_prefixes(con, args.ticket_prefix, args.team_prefix)
            con.execute(
                "INSERT INTO projects (key, name, root_path, remote, ticket_prefix, team_prefix, created_at, default_branch, landing, sessions)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (args.key, name, root, remote, args.ticket_prefix, args.team_prefix, at, branch, args.landing, args.sessions),
            )
            data = {"project": args.key, "root": root, "ticket_prefix": args.ticket_prefix, "team_prefix": args.team_prefix, "landing": args.landing,
                    "sessions": args.sessions, "default_branch": branch, "remote": remote}
            ledgerdb.write_event(con, at, actor.label, "project.added", "project %s added: %s" % (args.key, root), data=data)
            entry = reportentry.write_report_entry(con, at, "Project %s added: %s (%s-nnn tickets, landing %s, sessions %s)" % (args.key, root, args.ticket_prefix, args.landing, args.sessions),
                                       "project add", None, next_line=args.next)
            d = project_dict(ctx, con, lookup.get_project(con, args.key))
    finally:
        con.close()
    return reportentry.with_report_entry({"project": d}, "project %s added: %s (%s-nnn tickets, %s-nnn teams, landing %s, sessions %s); nothing installed yet:"
                             " `spud --as spud project install %s`" % (d["key"], d["root"], d["ticket_prefix"], d["team_prefix"], d["landing"], d["sessions"], d["key"]), entry)


def cmd_project_list(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        rows = [project_dict(ctx, con, p) for p in con.execute("SELECT * FROM projects ORDER BY id").fetchall()]
    finally:
        con.close()
    shown = [dict(r, installed_text="yes" if r["installed"] else "no", archived=kernel.fm_date(r["archived_at"])) for r in rows]
    return kernel.Result({"projects": rows}, kernel.table(shown, [("key", "key"), ("name", "name"), ("tickets", "ticket_prefix"), ("teams", "team_prefix"), ("root", "root"),
                                                    ("branch", "default_branch"), ("landing", "landing"), ("sessions", "sessions"), ("installed", "installed_text"),
                                                    ("archived", "archived")]))


def cmd_project_show(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        d = project_dict(ctx, con, lookup.get_project(con, args.key))
    finally:
        con.close()
    return kernel.Result({"project": d}, format_project(d))


def cmd_project_edit(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "editing a project")
        at = kernel.now()
        with ledgerdb.write_txn(con):
            p = lookup.get_project(con, args.key)
            tool_project = p["id"] == 1  # SPD-097: project spud, whose name and prefixes are spud.config.json's
            updates = {}
            if args.name is not None:
                if tool_project:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "project spud's name comes from spud.config.json (identity.name)")
                updates["name"] = args.name
            if args.landing is not None:
                updates["landing"] = args.landing
            if args.sessions is not None:
                updates["sessions"] = args.sessions
            if args.default_branch is not None:
                updates["default_branch"] = args.default_branch
            if args.root is not None:
                root = validate_project_root(ctx, con, args.root, exclude_id=p["id"])
                clash = con.execute("SELECT key FROM projects WHERE root_path = ? AND id != ?", (root, p["id"])).fetchone()
                if clash:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s is already the root of project %s" % (root, clash["key"]))
                updates["root_path"] = root
            if args.ticket_prefix is not None or args.team_prefix is not None:
                if tool_project:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "project spud's prefixes come from spud.config.json (`spud config sync`)")
                if con.execute("SELECT 1 FROM tickets WHERE project_id = ? LIMIT 1", (p["id"],)).fetchone():
                    raise kernel.SpudError(kernel.EXIT_ERROR, "project %s has tickets, so its prefixes are fixed: they are in rendered file names and wikilinks" % p["key"])
                tp, tm = args.ticket_prefix or p["ticket_prefix"], args.team_prefix or p["team_prefix"]
                check_prefixes(con, tp, tm, exclude_id=p["id"])
                updates.update(ticket_prefix=tp, team_prefix=tm)
            updates = {k: v for k, v in updates.items() if v != p[k]}
            if updates:
                con.execute("UPDATE projects SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in updates), (*updates.values(), p["id"]))
                ledgerdb.write_event(con, at, actor.label, "project.edited", "project %s edited: %s" % (p["key"], ", ".join(sorted(updates))),
                            data={"project": p["key"], "fields": sorted(updates), "from": {k: p[k] for k in updates}, "to": updates})
            d = project_dict(ctx, con, lookup.get_project(con, args.key))
    finally:
        con.close()
    return kernel.Result({"project": d, "changed": sorted(updates)}, "project %s edited: %s" % (d["key"], ", ".join(sorted(updates)) or "nothing to change"))
