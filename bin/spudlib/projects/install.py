"""projects/install: project install, uninstall, sync, remove."""

import contextlib
import json
import os
import shlex
from pathlib import Path

from . import agentdef, registry, sessions, userskills
from ..commands import projectboards, reportentry, settings_sync
from ..core import homeconf, kernel
from ..hooks import worktrees
from ..state import actors, ledgerdb, lookup


SETTINGS_LOCAL = ".claude/settings.local.json"
EXCLUDE_COMMENT = "# spud project %s"
HOME_ASSIGNMENT = "SPUD_HOME="  # the first word of every hook line settings_sync.hook_command writes


def install_files(ctx, p):
    """Where install writes for a project."""
    user = homeconf.user_claude_dir()
    return {
        "settings": Path(worktrees.project_root(ctx, p)) / ".claude" / "settings.local.json",
        "agent": user / "agents" / "spudagent.md",
        "agents": agentdef.agent_paths(user / "agents"),  # every definition: the base above and one per effort (SPD-222)
        "skill": user / "skills" / "spud" / "SKILL.md",
        "user_skills": userskills.skill_paths(user / "skills"),  # /spud-cleanup and /spud-autopilot beside it (SPD-335)
        "pointer": homeconf.spud_config_dir() / "home",
        "source_agent": agentdef.agent_source(ctx),  # in the tool repository, a template, under share/
    }


def read_json_object(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a readable JSON file: %s" % (path, e))
    if not isinstance(data, dict):
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a JSON object" % path)
    return data


def git_common_dir(root):
    proc = homeconf.run_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise kernel.SpudError(kernel.EXIT_ERROR, "cannot find the git common dir of %s: %s" % (root, proc.stderr.strip()))
    return Path(proc.stdout.strip())


def ensure_ignored(root, key):
    """Make sure git ignores the local settings file: True when it appended the path to the common
    dir's info/exclude under its comment line, False when git already ignored it."""
    proc = homeconf.run_git(root, "check-ignore", "-q", "--", SETTINGS_LOCAL, timeout=30)
    if proc.returncode == 0:
        return False
    if proc.returncode != 1:
        raise kernel.SpudError(kernel.EXIT_ERROR, "git check-ignore failed in %s: %s" % (root, proc.stderr.strip()))
    exclude = git_common_dir(root) / "info" / "exclude"
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    kernel.write_whole(exclude, text + "%s\n%s\n" % (EXCLUDE_COMMENT % key, SETTINGS_LOCAL))
    return True


def remove_exclude_block(root, key):
    """Take back the two lines ensure_ignored appended; True when they were there."""
    try:
        exclude = git_common_dir(root) / "info" / "exclude"
    except kernel.SpudError:
        return False
    if not exclude.is_file():
        return False
    lines = exclude.read_text(encoding="utf-8").split("\n")
    out, i, removed = [], 0, False
    while i < len(lines):
        if lines[i] == EXCLUDE_COMMENT % key and i + 1 < len(lines) and lines[i + 1] == SETTINGS_LOCAL:
            i += 2
            removed = True
            continue
        out.append(lines[i])
        i += 1
    if removed:
        kernel.write_whole(exclude, "\n".join(out))
    return removed


def original_permissions(original_text, key):
    """The strings of one permissions list (`deny`, `additionalDirectories`) of the settings file as it stood before install
    first wrote it (the record's `original`): [] when install created it, it does not parse, or it has no such list.  What
    it names is the user's, kept by every install and by uninstall -- SPD-033's rule for the deny rules, SPD-245's for
    the home's directory entry."""
    try:
        data = json.loads(original_text) if original_text is not None else None
    except ValueError:
        return []
    permissions = data.get("permissions") if isinstance(data, dict) else None
    items = permissions.get(key) if isinstance(permissions, dict) else None
    return [d for d in items if isinstance(d, str)] if isinstance(items, list) else []


def installed_home(record, settings):
    """The home an earlier install wrote this settings file for, whose additionalDirectories entry is install's to replace
    or take back (SPD-245): the record's `home`, or, in a record written before install kept one, the SPUD_HOME of the
    file's ledger hook lines, read by the rule every sync replaces them by (settings_sync.is_ledger_hook, SPD-226); None
    when neither names one."""
    if isinstance(record.get("home"), str) and record["home"]:
        return record["home"]
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in (hooks.values() if isinstance(hooks, dict) else ()):
        for group in (groups if isinstance(groups, list) else ()):
            for h in (group.get("hooks") if isinstance(group, dict) and isinstance(group.get("hooks"), list) else ()):
                if not settings_sync.is_ledger_hook(h):
                    continue
                try:
                    words = shlex.split(str(h.get("command")))
                except ValueError:
                    continue
                if words and words[0].startswith(HOME_ASSIGNMENT) and len(words[0]) > len(HOME_ASSIGNMENT):
                    return words[0][len(HOME_ASSIGNMENT):]
    return None


def install_project(ctx, con, p):
    """Write what project install writes, each file only when its content changes: the ledger hooks,
    the CLI allow rules, the state directory's deny rules and the home as an additional directory in the project's
    untracked local settings; the exclude
    line when git does not already ignore that file; the spudagent definitions rendered for this machine -- the base and
    its effort variants, agentdef.definitions -- the /spud skill, and /spud-cleanup and /spud-autopilot
    (projects/userskills) at user scope; the home pointer when absent.
    Returns (the install record for projects.installed, the paths written, whether the user agents directory held no
    agent before)."""
    root = Path(worktrees.project_root(ctx, p))
    if not root.is_dir():
        raise kernel.SpudError(kernel.EXIT_ERROR, "project %s's root %s is not a directory; `spud --as spud project edit %s --root <path>`" % (p["key"], root, p["key"]))
    files = install_files(ctx, p)
    # Rendered from Ctx before anything is written: it refuses when the source is gone or is no base for the variants.
    agent_texts = dict(agentdef.definitions(ctx))
    user_skill_texts = dict(userskills.skill_texts(ctx))  # the same: a source gone refuses the install whole
    if homeconf.run_git(root, "ls-files", "--error-unmatch", "--", SETTINGS_LOCAL, timeout=30).returncode == 0:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is tracked in %s's git; install writes nothing in the tracked tree" % (SETTINGS_LOCAL, p["key"]))
    previous = json.loads(p["installed"]) if p["installed"] else {}
    settings_path = files["settings"]
    current = settings_path.read_text(encoding="utf-8") if settings_path.is_file() else None
    settings = read_json_object(settings_path) if current is not None else {}
    original = previous["original"] if "original" in previous else current
    # SPD-033: the state directory's deny rules are written here too, because the home is this project's additional
    # directory: a session here may edit the home by absolute path, its .spud/ included, and the edit hook refuses that
    # to every caller, plain sessions too, so the native rule narrows nothing the hook allows.  Law 3's Agent rules
    # stay the home's: a session here is plain until claimed, and a plain session's spawns are Eric's.  A rule of the
    # same shape the file held before install is the user's, kept by the merge and by uninstall.
    # SPD-245: the home's additionalDirectories entry is install's by the same rule, whichever home it names: an earlier
    # install's home (a home move reinstalls every project) is replaced where it stands, unless the file listed that
    # directory before install first wrote it, and every other directory is the user's and stays.
    home, held = str(ctx.home), original_permissions(original, "additionalDirectories")
    earlier = installed_home(previous, settings)
    settings_sync.merge_settings(ctx, settings, env=False, agent_deny=False, keep_deny=original_permissions(original, "deny"),
                                 additional_dirs=[home], drop_dirs=[earlier] if earlier and earlier != home and earlier not in held else [],
                                 project_key=p["key"])
    rendered = json.dumps(settings, indent=2) + "\n"
    written = []
    if rendered != current:
        kernel.write_whole(settings_path, rendered)
        written.append(str(settings_path))
    added_exclude = ensure_ignored(root, p["key"])
    if added_exclude:
        written.append(str(git_common_dir(root) / "info" / "exclude"))
    agents = files["agent"].parent
    first_agent = not (agents.is_dir() and any(agents.glob("*.md")))
    skill_text = sessions.skill_markdown(ctx)
    targets = [(files["agents"][name], text) for name, text in agent_texts.items()] + [(files["skill"], skill_text)]
    targets += [(files["user_skills"][name], text) for name, text in user_skill_texts.items()]
    for path, text in targets:
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            kernel.write_whole(path, text)
            written.append(str(path))
    wrote_pointer = False
    if not files["pointer"].exists():
        kernel.write_whole(files["pointer"], str(ctx.home) + "\n")
        written.append(str(files["pointer"]))
        wrote_pointer = True
    record = {
        "path": str(settings_path),
        "created_file": previous.get("created_file", current is None),
        "original": original,
        "home": home,  # the home this install wrote for: the next install for another home replaces its entry (SPD-245)
        "added_additional_dir": home not in held,
        "added_exclude": bool(previous.get("added_exclude")) or added_exclude,
        "agent_sha256": kernel.sha256_bytes(agent_texts[kernel.SPUDAGENT].encode("utf-8")),
        "variant_sha256": {name: kernel.sha256_bytes(agent_texts[name].encode("utf-8")) for name in kernel.SPUDAGENT_VARIANTS},
        "skill_sha256": kernel.sha256_bytes(skill_text.encode("utf-8")),
        "user_skill_sha256": {name: kernel.sha256_bytes(text.encode("utf-8")) for name, text in user_skill_texts.items()},
        "wrote_pointer": bool(previous.get("wrote_pointer")) or wrote_pointer,
        "at": previous.get("at") or kernel.now(),
    }
    return record, written, first_agent


def strip_ledger_settings(settings, original, home):
    """Settings with the ledger's hooks and allow rules taken out, its state-directory deny rules too unless the original
    file held that very rule, and `home` (the home install wrote the file for, None for none) out of additionalDirectories
    unless the original file listed it (SPD-245); containers left empty are dropped when the original file did not have
    them."""
    orig = original if isinstance(original, dict) else {}
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        orig_hooks = orig.get("hooks") if isinstance(orig.get("hooks"), dict) else {}
        for event in list(hooks):
            groups = hooks[event]
            if not isinstance(groups, list):
                continue
            kept = []
            for group in groups:
                if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                    entries = [h for h in group["hooks"] if not settings_sync.is_ledger_hook(h)]
                    if not entries:
                        continue
                    if len(entries) != len(group["hooks"]):
                        group = dict(group, hooks=entries)
                kept.append(group)
            hooks[event] = kept
            if not kept and event not in orig_hooks:
                del hooks[event]
        if not hooks and "hooks" not in orig:
            del settings["hooks"]
    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        orig_permissions = orig.get("permissions") if isinstance(orig.get("permissions"), dict) else {}
        if isinstance(permissions.get("allow"), list):
            permissions["allow"] = [a for a in permissions["allow"] if not (isinstance(a, str) and settings_sync.ALLOW_RULE_MARK.match(a))]
        if isinstance(permissions.get("deny"), list):
            orig_deny = orig_permissions.get("deny") if isinstance(orig_permissions.get("deny"), list) else []
            permissions["deny"] = [d for d in permissions["deny"]
                                   if not (isinstance(d, str) and settings_sync.STATE_DENY_MARK.fullmatch(d) and d not in orig_deny)]
        orig_dirs = orig_permissions.get("additionalDirectories") if isinstance(orig_permissions.get("additionalDirectories"), list) else []
        if home and home not in orig_dirs and isinstance(permissions.get("additionalDirectories"), list):
            permissions["additionalDirectories"] = [d for d in permissions["additionalDirectories"] if d != home]
        for key in ("allow", "deny", "additionalDirectories"):
            if permissions.get(key) == [] and key not in orig_permissions:
                del permissions[key]
        if not permissions and "permissions" not in orig:
            del settings["permissions"]
    return settings


def uninstall_project(ctx, con, p):
    """Undo what install recorded: (what it changed, warnings).  The settings file gets its original
    bytes back when what is left equals what was there, is removed when install created it and nothing else is left,
    and is otherwise written without the ledger's entries.  The user-scope files go only with the last installed project,
    and only while they still match what install wrote."""
    record = json.loads(p["installed"]) if p["installed"] else {}
    files = install_files(ctx, p)
    settings_path = Path(record.get("path") or files["settings"])
    changed, warnings = [], []
    if settings_path.is_file():
        text = settings_path.read_text(encoding="utf-8")
        settings = read_json_object(settings_path)
        original_text = record.get("original")
        try:
            original = json.loads(original_text) if original_text is not None else None
        except ValueError:
            original = None
        strip_ledger_settings(settings, original, installed_home(record, settings) or str(ctx.home))
        if original_text is not None and original == settings:
            new_text = original_text
        elif record.get("created_file") and settings == {}:
            new_text = None
        else:
            new_text = json.dumps(settings, indent=2) + "\n"
        if new_text is None:
            settings_path.unlink()
            changed.append("removed %s" % settings_path)
        elif new_text != text:
            kernel.write_whole(settings_path, new_text)
            changed.append("wrote %s" % settings_path)
    if record.get("added_exclude") and os.path.isdir(worktrees.project_root(ctx, p)) and remove_exclude_block(worktrees.project_root(ctx, p), p["key"]):
        changed.append("removed the exclude line for %s" % SETTINGS_LOCAL)
    others = con.execute("SELECT count(*) FROM projects WHERE id != ? AND installed IS NOT NULL", (p["id"],)).fetchone()[0]
    if others == 0:
        # The base, each effort variant (SPD-222), the /spud skill and the user skills beside it (SPD-335), each against
        # the hash install recorded for it.  A record written before the variants or the user skills existed has none for
        # them, and a file it does not name is not install's to take.
        variants = record.get("variant_sha256") if isinstance(record.get("variant_sha256"), dict) else {}
        user_skills = record.get("user_skill_sha256") if isinstance(record.get("user_skill_sha256"), dict) else {}
        owned = [("agent", files["agent"], record.get("agent_sha256"))]
        owned += [("agent", files["agents"][name], variants.get(name)) for name in kernel.SPUDAGENT_VARIANTS]
        owned.append(("skill", files["skill"], record.get("skill_sha256")))
        owned += [("skill", files["user_skills"][name], user_skills.get(name)) for name in userskills.USER_SKILLS]
        for name, path, sha in owned:
            if not path.is_file():
                continue
            if sha and kernel.sha256_bytes(path.read_bytes()) == sha:
                path.unlink()
                changed.append("removed %s" % path)
                if name == "skill":
                    with contextlib.suppress(OSError):
                        path.parent.rmdir()
            else:
                warnings.append("%s differs from what install wrote, so it is left in place" % path)
    return changed, warnings


def install_with_board(ctx, con, p):
    """`install_project`, and the project's Kanban board written when absent (`commands/projectboards`, SPD-324): what
    `project install` does, and what init's step 7 does for project 1 (SPD-325), in one place.  Returns
    `install_project`'s three values and the board's entry from `projectboards.write_boards`.

    The board is decided and rendered before install writes anything, so a tool whose template is gone refuses the
    install whole.  It is here and not in `install_project`, which `project sync` and a home move run too: those rewrite
    what install generates, and a board is the home's once written."""
    board = projectboards.plan_board(ctx, p)
    record, written, first_agent = install_project(ctx, con, p)
    return record, written, first_agent, projectboards.write_boards(ctx, [board])[0]


def cmd_project_install(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "installing a project")
        reportentry.check_next(con, actor, args)
        p = lookup.get_project(con, args.key)
        if p["archived_at"]:
            raise kernel.SpudError(kernel.EXIT_ERROR, "project %s is archived" % p["key"])
        record, written, first_agent, board = install_with_board(ctx, con, p)
        wrote_board = board["board"] == "written"
        at = kernel.now()
        entry = None
        with ledgerdb.write_txn(con):
            con.execute("UPDATE projects SET installed = ? WHERE id = ?", (json.dumps(record), p["id"]))
            if written or wrote_board or not p["installed"] or args.next is not None:
                ledgerdb.write_event(con, at, actor.label, "project.installed", "project %s installed: %d file%s written" % (p["key"], len(written), "" if len(written) == 1 else "s"),
                            data={"project": p["key"], "written": written, "sync": False, "board": board})
                entry = reportentry.write_report_entry(con, at, "Project %s installed: %s" % (p["key"], worktrees.project_root(ctx, p)), "project install", None, next_line=args.next)
            d = registry.project_dict(ctx, con, lookup.get_project(con, args.key))
    finally:
        con.close()
    lines = ["project %s installed%s" % (d["key"], "" if written or wrote_board else ": unchanged, nothing written")] + ["  wrote %s" % w for w in written]
    # A board already there is the home's and says nothing here, so a second install still reads as unchanged.
    lines += ["  " + line for line in projectboards.board_lines([board]) if board["board"] != "kept"]
    if first_agent:
        lines.append("restart open sessions in %s to see spudagent (the first agent file in a scope is seen only after a restart)" % d["key"])
    return reportentry.with_report_entry({"project": d, "written": written, "board": board, "restart": first_agent}, "\n".join(lines), entry)


def cmd_project_uninstall(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "uninstalling a project")
        reportentry.check_next(con, actor, args)
        p = lookup.get_project(con, args.key)
        if not p["installed"]:
            if args.next is not None:
                raise reportentry.no_entry_for_next("project %s is not installed" % p["key"])
            return kernel.Result({"project": registry.project_dict(ctx, con, p), "changed": [], "warnings": []}, "project %s is not installed; nothing to do" % p["key"])
        changed, warnings = uninstall_project(ctx, con, p)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            con.execute("UPDATE sessions SET released_at = ? WHERE project_id = ? AND released_at IS NULL", (at, p["id"]))
            con.execute("UPDATE projects SET installed = NULL WHERE id = ?", (p["id"],))
            ledgerdb.write_event(con, at, actor.label, "project.uninstalled", "project %s uninstalled" % p["key"], data={"project": p["key"], "changed": changed, "warnings": warnings})
            entry = reportentry.write_report_entry(con, at, "Project %s uninstalled" % p["key"], "project uninstall", None, lines=warnings, next_line=args.next)
            d = registry.project_dict(ctx, con, lookup.get_project(con, args.key))
    finally:
        con.close()
    lines = ["project %s uninstalled" % d["key"]] + ["  " + c for c in changed] + ["  warning: " + w for w in warnings]
    return reportentry.with_report_entry({"project": d, "changed": changed, "warnings": warnings}, "\n".join(lines), entry)


def cmd_project_sync(ctx, args):
    if bool(args.key) == bool(args.all):
        raise kernel.SpudError(kernel.EXIT_USAGE, "project sync takes a project key or --all, one of the two")
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "syncing a project's installation")
        if args.key:
            p = lookup.get_project(con, args.key)
            if not p["installed"]:
                raise kernel.SpudError(kernel.EXIT_ERROR, "project %s is not installed; `spud --as spud project install %s`" % (p["key"], p["key"]))
            rows = [p]
        else:
            rows = con.execute("SELECT * FROM projects WHERE archived_at IS NULL AND installed IS NOT NULL ORDER BY id").fetchall()
        results = []
        for p in rows:
            record, written, first_agent = install_project(ctx, con, p)
            at = kernel.now()
            with ledgerdb.write_txn(con):
                con.execute("UPDATE projects SET installed = ? WHERE id = ?", (json.dumps(record), p["id"]))
                if written:
                    ledgerdb.write_event(con, at, actor.label, "project.installed", "project %s synced: %d file%s written" % (p["key"], len(written), "" if len(written) == 1 else "s"),
                                data={"project": p["key"], "written": written, "sync": True})
            results.append({"project": p["key"], "written": written, "restart": first_agent})
    finally:
        con.close()
    lines = []
    for r in results:
        lines.append("project %s %s" % (r["project"], "synced" if r["written"] else "unchanged"))
        lines += ["  wrote %s" % w for w in r["written"]]
    return kernel.Result({"projects": results}, "\n".join(lines) or "no installed project to sync")


def cmd_project_remove(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "removing a project")
        reportentry.check_next(con, actor, args)
        p = lookup.get_project(con, args.key)
        if p["id"] == 1:
            # Because it is project 1, not because it is the tool repository.  Removing it would leave
            # spud.config.json's prefixes naming no project, which doctor then reports for as long as the home lives.
            raise kernel.SpudError(kernel.EXIT_ERROR, "project %s is project 1, whose name and prefixes spud.config.json names, and is never removed:"
                            " removing it would leave the config naming prefixes no project has" % p["key"])
        # a parked ticket is open: it is not-now, not over, so project remove waits for it too
        open_tickets = [r["key"] for r in con.execute("SELECT key FROM tickets WHERE project_id = ? AND status IN ('queued','active','parked') ORDER BY id", (p["id"],)).fetchall()]
        if open_tickets:
            raise kernel.SpudError(kernel.EXIT_ERROR, "project %s has open tickets (%s); move them to done or declined first" % (p["key"], ", ".join(open_tickets)))
        changed, warnings = uninstall_project(ctx, con, p) if p["installed"] else ([], [])
        at = kernel.now()
        with ledgerdb.write_txn(con):
            tickets = con.execute("SELECT count(*) FROM tickets WHERE project_id = ?", (p["id"],)).fetchone()[0]
            con.execute("UPDATE sessions SET released_at = ? WHERE project_id = ? AND released_at IS NULL", (at, p["id"]))
            if tickets == 0:
                con.execute("DELETE FROM sessions WHERE project_id = ?", (p["id"],))
                con.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
            else:
                con.execute("UPDATE projects SET archived_at = ?, installed = NULL WHERE id = ?", (at, p["id"]))
            archived = tickets > 0
            ledgerdb.write_event(con, at, actor.label, "project.removed", "project %s %s" % (p["key"], "archived" if archived else "removed"),
                        data={"project": p["key"], "archived": archived, "tickets": tickets, "uninstalled": changed, "warnings": warnings})
            entry = reportentry.write_report_entry(con, at, "Project %s %s" % (p["key"], "archived (it has %d ticket%s)" % (tickets, "" if tickets == 1 else "s") if archived else "removed"),
                                       "project remove", None, lines=warnings, next_line=args.next)
    finally:
        con.close()
    text = "project %s %s" % (p["key"], "archived: its %d ticket%s keep%s it in the ledger" % (tickets, "" if tickets == 1 else "s", "s" if tickets == 1 else "") if archived else "removed")
    return reportentry.with_report_entry({"project": p["key"], "archived": archived, "changed": changed, "warnings": warnings},
                             "\n".join([text] + ["  " + c for c in changed] + ["  warning: " + w for w in warnings]), entry)
