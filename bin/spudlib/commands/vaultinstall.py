"""commands/vaultinstall: `spud vault install`, and the same work as `spud init`'s step 4b (SPD-156).

Two halves.  The settings are copied out of `share/obsidian/` into the home's `.obsidian/`; the plugins and the theme
are *downloaded*, because Obsidian loads only the plugins whose files are already in the vault and the tool repository
ships nobody else's code.  Every download is checked against the SHA-256 the lock pins before it is renamed into place,
so a release that was replaced upstream refuses that one plugin by name and the rest still install.

Three rules an edit here must keep:

- **A file the tool does not ship is never touched.**  The vault is the person's; what the tool owns is the list in
  `core/vaultlock` and the files the lock pins, and nothing else in `.obsidian/` is read, moved or removed.
- **A tool-owned file the home has changed is replaced, and a copy is kept** under `.spud/backups/vault-install/<when>/`
  and named in the output.  The shipped `.base` views and settings are the tool's and an install refreshes them; a
  person's own view survives because it carries its own name, which is the one sentence the output and `ledger/Home.md`
  both say.  `--force` is for re-writing what matched anyway.
- **A refused download is never fatal.**  `install_vault` collects them, names each, and returns; `spud init` turns the
  list into a note and a line telling the person to run `spud vault install` later (design section 3).
"""

from . import vaultlock
from ..core import kernel
from ..state import actors, backup, ledgerdb

NO_SHIPPED_VAULT = ("no %s: the tool repository's share/ is the source of the vault, and"
                    " `spud --as <agent_id> vault capture --into <worktree>` on a ticket writes it")
VIEWS_ARE_THE_TOOL_S = ("this command does not touch the shipped `.base` views in %s -- `spud home sync` refreshes"
                        " them, keeping a copy of what it replaces, while a `.base` file of your own survives")
HASH_MISMATCH = "%s: its SHA-256 is not the one the lock pins (the release was replaced upstream)"


def kept(ctx, stamp, rel, data, check_only):
    """The path of the copy `state/backup.keep_copy` keeps -- or would keep -- of a tool-owned file this install is
    about to overwrite, under the vault-install folder of the home's backups directory."""
    return str(backup.keep_copy(ctx, vaultlock.BACKUP_DIR, stamp, vaultlock.OBSIDIAN + "/" + rel, data, check_only))


def install_settings(ctx, stamp, force, record, check_only=False):
    """The settings half: every file under `share/obsidian/`, written into `<home>/.obsidian/` at the same relative path.

    Written when absent, kept when it already holds the shipped text, and replaced -- with a copy kept -- when it holds
    something else.  `--force` rewrites one that matched, which is what makes it useful after a hand edit was taken back
    by something other than this command.  `check_only` records all of that and writes none of it.
    """
    files = vaultlock.shipped_settings(ctx)
    if not files:
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_SHIPPED_VAULT % vaultlock.share_vault(ctx))
    for rel, text in files:
        path = ctx.home / vaultlock.OBSIDIAN / rel
        want = text.encode("utf-8")
        have = path.read_bytes() if path.is_file() else None
        if have == want and not force:
            record["unchanged"].append(rel)
            continue
        if have is not None and have != want:
            record["replaced"].append({"path": rel, "backup": kept(ctx, stamp, rel, have, check_only)})
        elif have is None:
            record["written"].append(rel)
        else:
            record["rewritten"].append(rel)
        if not check_only:
            kernel.write_bytes(path, want)


def install_locked(ctx, stamp, force, record, lock, check_only=False):
    """The downloaded half: each locked plugin and theme, all of its files or none of them.

    A plugin's files are fetched and checked before any of them is written, so a release replaced upstream leaves the
    plugin as it was rather than half-updated; the refusal names it and the loop goes on to the next one.  A file the
    home already has that is what the lock pins is not fetched at all, which is what makes a second install cheap --
    Obsidian's `/* nosourcemap */` suffix included (`vaultlock.matches`).

    `lock` is read by `install_vault` before the settings are written, not here, so a lock this tool cannot install --
    one naming a path where a plugin id belongs, above all -- refuses the whole command before it has written a file.

    `check_only` makes **no download at all**: a file the home already has that the lock pins is as unchanged as ever,
    and every other one is what a real install would fetch, recorded by what the home holds now.  That is the whole
    reason `spud home sync --check` can be run against a home with the network off and still say what would change.
    """
    for kind, name, entry in vaultlock.locked(lock):
        directory = vaultlock.target_dir(ctx, kind, name)
        wanted, why = [], None
        for f in entry["files"]:
            path = directory / f["name"]
            have = path.read_bytes() if path.is_file() else None
            pinned = have is not None and vaultlock.matches(f["sha256"], have)
            if pinned and not force:
                continue
            if check_only:  # `pinned` is --force over a file that is already right: a rewrite, and no copy to keep
                wanted.append((path, f["name"], have, None, pinned))
                continue
            try:
                data = vaultlock.download(f["url"])
            except vaultlock.VaultDownloadError as e:
                why = str(e)
                break
            if kernel.sha256_bytes(data) != f["sha256"]:
                why = HASH_MISMATCH % f["url"]
                break
            wanted.append((path, f["name"], have, data, False))
        if why is not None:
            record["refused"].append({"kind": kind, "name": name, "version": entry["version"], "why": why})
            continue
        if not wanted:
            record["unchanged"].append("%s %s" % (kind, name))
            continue
        for path, fname, have, data, pinned in wanted:
            rel = "%s/%s/%s" % (vaultlock.PLUGINS if kind == "plugin" else vaultlock.THEMES, name, fname)
            if have is None:
                record["written"].append(rel)
            elif pinned or have == data:  # --force re-downloaded what was already right: nothing to keep a copy of
                record["rewritten"].append(rel)
            else:
                record["replaced"].append({"path": rel, "backup": kept(ctx, stamp, rel, have, check_only)})
            if not check_only:
                kernel.write_bytes(path, data)
        record["installed"].append({"kind": kind, "name": name, "version": entry["version"],
                                    "files": [f for _p, f, _h, _d, _m in wanted]})


def install_vault(ctx, force=False, check_only=False):
    """(record, lines): the whole install, as `spud vault install` prints it and as `spud init`'s step 4b folds it in.

    Takes no actor and opens no database, so init -- which resolves neither (`commands/homeinit`) -- calls it directly
    and `cmd_vault_install` does the ownership check for a person who typed the command.

    The lock is read first: it is the one thing here that can refuse outright (`vaultlock.lock_problems`, and a name in
    it that is not one plain file name is a lock this tool will not install from), and a refusal before the settings are
    written leaves the vault exactly as it was.

    `check_only` (SPD-157) records the same install and performs none of it -- no file written, no copy kept, no
    download made -- which is the vault half of `spud home sync --check`.
    """
    lock = vaultlock.read_lock(ctx)
    stamp = backup.copy_stamp(ctx, vaultlock.BACKUP_DIR)
    record = {"home": str(ctx.home), "vault": str(ctx.home / vaultlock.OBSIDIAN), "force": bool(force),
              "check": bool(check_only),
              "written": [], "rewritten": [], "replaced": [], "unchanged": [], "installed": [], "refused": []}
    install_settings(ctx, stamp, force, record, check_only)
    install_locked(ctx, stamp, force, record, lock, check_only)
    return record, install_lines(ctx, record)


def install_lines(ctx, record):
    """What the command prints: what it wrote, what it replaced and where the copy is, what it could not download, and
    the one sentence about the shipped views.  A check says every one of them in the conditional and nothing else."""
    check = record.get("check")
    lines = ["vault %s: %d file(s) %s, %d %s, %d unchanged"
             % (record["vault"], len(record["written"]) + len(record["rewritten"]),
                "would be written" if check else "written",
                len(record["replaced"]), "would be replaced" if check else "replaced", len(record["unchanged"]))]
    lines.extend("  %s %s %s %s (%s)" % ("would install" if check else "installed", i["kind"], i["name"], i["version"],
                                         ", ".join(i["files"])) for i in record["installed"])
    lines.extend("  %s %s (the copy it %s %s)"
                 % ("would replace" if check else "replaced", r["path"],
                    "holds would be kept at" if check else "held is", r["backup"]) for r in record["replaced"])
    lines.extend("  refused %s %s %s: %s" % (r["kind"], r["name"], r["version"], r["why"]) for r in record["refused"])
    lines.append(VIEWS_ARE_THE_TOOL_S % (ctx.home / vaultlock.VAULT_BASES))
    return lines


def cmd_vault_install(ctx, args):
    """`spud vault install [--force]` (Spud's): the home's own vault, from the tool's `share/`."""
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "installing the home's Obsidian vault")
    finally:
        con.close()
    record, lines = install_vault(ctx, args.force)
    if record["refused"]:
        return kernel.Result(record, "\n".join(lines), exit_code=kernel.EXIT_ERROR,
                             stderr="%d download(s) refused: %s"
                             % (len(record["refused"]), "; ".join("%s %s" % (r["kind"], r["name"]) for r in record["refused"])))
    return kernel.Result(record, "\n".join(lines))
