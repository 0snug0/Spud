"""commands/homemove: spud --as spud home move, a split home moved from one plain directory to another -- the home is
its own directory, never inside a project's checkout, at both ends.  One procedure over two contexts, the old home's and
the new one's, kept whole because every step reads what the ones before it did."""

import json
import os
import shutil
import sqlite3
from pathlib import Path

from . import doctor, publish, reportentry, schedule, settings_sync
from ..core import homeconf, kernel, launchagents, lazy
from ..hooks import worktrees
from ..projects import install, registry
from ..state import actors, backup, ledgerdb, schema

COPIED_DIRS = ("ledger", "reports", "docs", ".obsidian")
# The config, and three the spec's list leaves out for a reason each: CLAUDE.md, so a session in the new home is Spud before
# he rewrites it there; the two settings files, so settings sync keeps Eric's own keys and rules on top of the ledger's.
COPIED_FILES = ("spud.config.json", "CLAUDE.md", ".claude/settings.json", ".claude/settings.local.json")
MOVED_STATE = ".spud-moved"
BY_HAND = """by hand, now:
  - end this session: its hooks still name %(old)s, whose state directory is now %(moved)s, so every tool call is refused from here on
  - open a new session in %(new)s (it is Spud there: the board, then its CLAUDE.md rewritten for the new layout and the memories copied)
  - open %(new)s as the vault in Obsidian
  - keep %(old)s until the new home has proved itself, then remove it when you choose
rollback, while %(old)s is kept: write %(old)s into %(pointer)s, rename %(moved)s back to .spud, and with SPUD_HOME=%(old)s rerun `settings sync`, `project install <key>` for each installed project and `schedule install`; ledger writes made in %(new)s meanwhile are lost"""


def move_preconditions(ctx, con, target):
    """Every reason the move is refused, each naming what is in the way (section 5): live members, the target, an earlier
    move's state directory, the running launcher's checkout, and hand-edited rendered files."""
    problems = []
    live = ["%s/%s" % (r["team_key"], r["name"]) for r in con.execute(
        "SELECT t.team_key, m.name FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE m.status IN ('planned', 'active') ORDER BY m.id").fetchall()]
    if live:
        problems.append("%d member(s) planned or active (%s); finish them first" % (len(live), ", ".join(live)))
    if target.exists():
        if not target.is_dir():
            problems.append("%s exists and is not a directory" % target)
        elif any(target.iterdir()):
            problems.append("%s is not an empty directory" % target)
    nearest = target
    while not nearest.exists():
        nearest = nearest.parent
    home_ident = worktrees.file_identity(ctx.home)
    if home_ident is not None and home_ident in registry.identity_chain(nearest):
        problems.append("%s is the current home or inside it" % target)
    proc = homeconf.run_git(nearest, "rev-parse", "--is-inside-work-tree", timeout=10)
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        problems.append("%s is inside a git work tree (%s); the home is a plain directory" % (target, nearest))
    if (ctx.home / MOVED_STATE).exists():
        problems.append("%s exists from an earlier move; delete or rename it first" % (ctx.home / MOVED_STATE))
    if homeconf.tool_checkout_kind(ctx.tool) == "worktree":
        problems.append("the running bin/spud is in a linked worktree (%s); run the main checkout's" % ctx.tool)
    conflicts = publish.render_pass(ctx, con, None, check_only=True)["conflicts"]
    if conflicts:
        problems.append("hand-edited rendered file(s): %s; accept each with `spud --as spud import --file <path>` or overwrite it with"
                        " `spud --as spud render --discard <path>`" % ", ".join(conflicts))
    return problems


def move_steps(ctx, con, target):
    """The nine steps as --dry-run prints them, with this home's paths filled in.  Step 5's line is true only
    when a row 1 is there to claim; a project-less home has none, so it says that instead."""
    has_project = con.execute("SELECT 1 FROM projects WHERE id = 1").fetchone() is not None
    step5 = ("set project spud's sessions to claim; settings sync into %s; project install spud and re-sync every installed project"
             " and the ~/.claude copies" % (target / ".claude" / "settings.json")
             if has_project else
             "no project registered, so nothing to claim; settings sync into %s; re-sync every installed project and the ~/.claude"
             " copies" % (target / ".claude" / "settings.json"))
    return [
        "write a checked backup in %s" % backup.backups_dir(ctx),
        "copy the database into %s with SQLite's online backup, run integrity_check, compare row counts table by table" % (target / ".spud"),
        "copy %s, %s and %s; render into %s and require zero files written" % (
            ", ".join(d + "/" for d in COPIED_DIRS), ", ".join(COPIED_FILES), backup.backups_dir(ctx), target),
        "write %s" % (homeconf.spud_config_dir() / "home"),
        step5,
        "install %s and %s with the new paths" % (launchagents.SCHEDULE_LABEL, launchagents.RENDER_LABEL),
        "render again (the move's own events) and run doctor on %s" % target,
        "rename %s to %s" % (ctx.home / ".spud", ctx.home / MOVED_STATE),
        "print what is left by hand",
    ]


def move_copy_database(con, new_path):
    """SQLite's online backup of the open database into new_path, then integrity_check, the schema version, WAL, and a row
    count per table compared with the source's.  Returns {table: rows}."""
    dst = sqlite3.connect(str(new_path))
    try:
        con.backup(dst)
        check = [str(r[0]) for r in dst.execute("PRAGMA integrity_check").fetchall()]
        if check != ["ok"]:
            raise kernel.SpudError(kernel.EXIT_ERROR, "integrity_check of the copy %s: %s" % (new_path, "; ".join(check)[:1000]))
        version = dst.execute("PRAGMA user_version").fetchone()[0]
        if version != schema.SCHEMA_VERSION:
            raise kernel.SpudError(kernel.EXIT_ERROR, "the copy %s has user_version %d, not %d" % (new_path, version, schema.SCHEMA_VERSION))
        if dst.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            dst.execute("PRAGMA journal_mode = WAL")
        counts = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            source = con.execute('SELECT count(*) FROM "%s"' % name).fetchone()[0]
            copied = dst.execute('SELECT count(*) FROM "%s"' % name).fetchone()[0]
            if source != copied:
                raise kernel.SpudError(kernel.EXIT_ERROR, "table %s has %d rows in the source and %d in the copy" % (name, source, copied))
            counts[name] = source
    finally:
        dst.close()
    return counts


def move_copy_files(ctx, target):
    """Step 3's copies: the vault's directories, the config and the files a first session needs, then the backups."""
    copied = []
    for name in COPIED_DIRS:
        src = ctx.home / name
        if src.is_dir():
            shutil.copytree(src, target / name, symlinks=True, dirs_exist_ok=True)
            copied.append(name + "/")
    for name in COPIED_FILES:
        src = ctx.home / name
        if src.is_file():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target / name)
            copied.append(name)
    backups = backup.backups_dir(ctx)
    if backups.is_dir():
        shutil.copytree(backups, target / ".spud" / "backups", dirs_exist_ok=True)
        copied.append(".spud/backups/")
    return copied


def move_check_vault(new):
    """Step 3b: the copied vault must already be what the copied database renders, before anything writes that database."""
    con = ledgerdb.connect(new)
    try:
        result = publish.render_pass(new, con, None, check_only=True)
    finally:
        con.close()
    if result["written"] or result["conflicts"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the copied vault in %s would need %d file(s) rendered (%s) and has %d conflict(s): the copy does not match the database"
                               % (new.home, len(result["written"]), ", ".join(result["written"][:5]), len(result["conflicts"])), data=result)
    return "3b. the copied vault matches the copied database: 0 to write, %d unchanged" % len(result["unchanged"])


def move_resync(old, new, args):
    """Step 5 over the new home: project spud claims, the report entry, the new home's settings, project spud installed and
    every installed project re-synced (the user-scope agent and skill with them).  No project's tracked file is touched: a
    split home's own settings live in the home, and each project's in its untracked settings.local.json.
    A project-less home has no row 1 to claim, so 5a records that instead and the move otherwise proceeds -- moving a legal
    home is reasonable whether or not one is registered."""
    done = []
    con = ledgerdb.connect(new)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            row = con.execute("SELECT * FROM projects WHERE id = 1").fetchone()
            if row is None:
                tool_line = "Tool: %s (no project registered)" % new.tool
            else:
                if row["sessions"] != "claim":
                    con.execute("UPDATE projects SET sessions = 'claim' WHERE id = 1")
                    ledgerdb.write_event(con, at, "spud", "project.edited", "project %s edited: sessions" % row["key"],
                                         data={"project": row["key"], "fields": ["sessions"], "from": {"sessions": row["sessions"]}, "to": {"sessions": "claim"}})
                tool_line = "Tool: %s (project %s, sessions claim)" % (new.tool, row["key"])
            entry = reportentry.write_report_entry(con, at, "Home moved from %s to %s" % (old.home, new.home), "home move", None,
                                                   lines=[tool_line], next_line=args.next)
        if row is None:
            done.append("5a. no project registered; nothing to claim; %s" % reportentry.report_entry_line(entry))
        else:
            done.append("5a. project %s: sessions claim; %s" % (row["key"], reportentry.report_entry_line(entry)))
        synced = settings_sync.cmd_settings_sync(new, lazy.argparse.Namespace(path=None, dry_run=False))
        done.append("5b. %s %s" % (synced.data["path"], "written" if synced.data["written"] else "unchanged"))
        for p in con.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id").fetchall():
            if p["id"] != 1 and not p["installed"]:
                continue
            record, written, _first = install.install_project(new, con, p)
            at = kernel.now()
            with ledgerdb.write_txn(con):
                con.execute("UPDATE projects SET installed = ? WHERE id = ?", (json.dumps(record), p["id"]))
                ledgerdb.write_event(con, at, "spud", "project.installed", "project %s installed by home move: %d file(s) written" % (p["key"], len(written)),
                                     data={"project": p["key"], "written": written, "sync": True})
            done.append("5c. project %s: %s" % (p["key"], ", ".join(written) or "unchanged"))
    finally:
        con.close()
    return done


def move_verify(new):
    """Step 7: the pass after the move's own events, which touch Projects.md and today's report and nothing else, then doctor
    with every problem but the just-bootstrapped watcher's."""
    con = ledgerdb.connect(new)
    try:
        with publish.render_lock(new):
            result = publish.render_pass(new, con, None)
    finally:
        con.close()
    allowed = {"ledger/Projects.md", "reports/%s.md" % kernel.now()[:10]}
    unexpected = [rel for rel in result["written"] if rel not in allowed]
    if unexpected or result["conflicts"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the render into %s wrote %s and found %d conflict(s); the copied vault does not match the copied database"
                               % (new.home, ", ".join(unexpected) or "nothing unexpected", len(result["conflicts"])), data=result)
    done = ["7a. render into %s: %s written, %d unchanged" % (new.home, ", ".join(result["written"]) or "nothing", len(result["unchanged"]))]
    report, problems, _lines = doctor.doctor_report(new)
    real = [p for p in problems if p != doctor.WATCHER_DOWN]
    if real:
        raise kernel.SpudError(kernel.EXIT_ERROR, "doctor on %s: %s" % (new.home, "; ".join(real)), data=report)
    done.append("7b. doctor ok" + ("; the watcher was bootstrapped a moment ago and is not up yet: `spud doctor` in the new session confirms it" if len(real) != len(problems) else ""))
    return done


def cmd_home_move(ctx, args):
    """spud --as spud home move --to <dir> [--dry-run]: the move's steps in their order, each reported; a step that fails
    stops the move with the old home untouched (its state directory is renamed last)."""
    target = Path(os.path.abspath(os.path.expanduser(args.to)))
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "moving the home")
        reportentry.check_next(con, actor, args)
        problems = move_preconditions(ctx, con, target)
        if problems:
            raise kernel.SpudError(kernel.EXIT_ERROR, "home move refused: " + "; ".join(problems), data={"problems": problems, "to": str(target)})
        steps = move_steps(ctx, con, target)
        if args.dry_run:
            return kernel.Result({"dry_run": True, "to": str(target), "steps": steps},
                                 "home move --dry-run: the preconditions hold; the move would\n" + "\n".join("  %d. %s" % (n, s) for n, s in enumerate(steps, start=1)))
        done = []
        with publish.render_lock(ctx):  # steps 1 and 2: no pass touches the database while it is copied
            copy = backup.do_backup(ctx, con, "pre-move")
            check = backup.backup_quick_check(copy)
            if check != ["ok"]:
                raise kernel.SpudError(kernel.EXIT_ERROR, "the pre-move backup %s did not check ok (%s); nothing moved" % (copy, "; ".join(check)[:1000]))
            done.append("1. backup %s (quick_check ok)" % copy)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            (target / ".spud").mkdir(parents=True, exist_ok=True)
            counts = move_copy_database(con, target / ".spud" / "ledger.db")
            done.append("2. database copied into %s: integrity ok, %d tables, %d rows" % (target / ".spud", len(counts), sum(counts.values())))
    finally:
        con.close()
    pointer = homeconf.spud_config_dir() / "home"
    new = homeconf.Ctx(target, "home move", ctx.json, tool=ctx.tool)
    try:
        done.append("3. copied " + ", ".join(move_copy_files(ctx, target)))
        done.append(move_check_vault(new))
        kernel.write_whole(pointer, str(target) + "\n")
        done.append("4. %s names %s" % (pointer, target))
        done.extend(move_resync(ctx, new, args))
        for record in schedule.install_agents(new, schedule.at_arg(schedule.SCHEDULE_AT)):
            done.append("6. %s loaded from %s" % (record["label"], record["path"]))
        done.extend(move_verify(new))
    except kernel.SpudError as e:
        pointed = pointer.is_file() and pointer.read_text(encoding="utf-8").strip() == str(target)
        hint = "the old home %s is untouched%s" % (ctx.home, ("; %s names %s now: write %s back into it to return" % (pointer, target, ctx.home)) if pointed else "")
        raise kernel.SpudError(e.code, "home move stopped after:\n  %s\n%s\n%s; remove %s and rerun once that is fixed" % ("\n  ".join(done), e.message, hint, target),
                               data=dict(e.data, done=done))
    os.rename(ctx.home / ".spud", ctx.home / MOVED_STATE)
    done.append("8. %s renamed to %s" % (ctx.home / ".spud", ctx.home / MOVED_STATE))
    by_hand = BY_HAND % {"old": ctx.home, "moved": MOVED_STATE, "new": target, "pointer": pointer}
    return kernel.Result({"to": str(target), "done": done, "by_hand": by_hand}, "\n".join(done) + "\n" + by_hand)
