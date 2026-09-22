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
VIEWS_ARE_THE_TOOL_S = ("the shipped views in %s are the tool's and an install refreshes them:"
                        " copy a view to a new name before changing it, and your copy survives")
HASH_MISMATCH = "%s: its SHA-256 is not the one the lock pins (the release was replaced upstream)"


def keep_copy(ctx, stamp, rel, data):
    """A copy of a tool-owned file this install is about to overwrite, under `.spud/backups/vault-install/<when>/<rel>`,
    beside the ledger's own daily copies.  Returns its path, which the output names."""
    path = backup.backups_dir(ctx) / vaultlock.BACKUP_DIR / stamp / rel
    vaultlock.write_bytes(path, data)
    return path


def install_settings(ctx, stamp, force, record):
    """The settings half: every file under `share/obsidian/`, written into `<home>/.obsidian/` at the same relative path.

    Written when absent, kept when it already holds the shipped text, and replaced -- with a copy kept -- when it holds
    something else.  `--force` rewrites one that matched, which is what makes it useful after a hand edit was taken back
    by something other than this command.
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
            record["replaced"].append({"path": rel, "backup": str(keep_copy(ctx, stamp, vaultlock.OBSIDIAN + "/" + rel, have))})
        elif have is None:
            record["written"].append(rel)
        else:
            record["rewritten"].append(rel)
        vaultlock.write_bytes(path, want)


def install_locked(ctx, stamp, force, record, lock):
    """The downloaded half: each locked plugin and theme, all of its files or none of them.

    A plugin's files are fetched and checked before any of them is written, so a release replaced upstream leaves the
    plugin as it was rather than half-updated; the refusal names it and the loop goes on to the next one.  A file the
    home already has that is what the lock pins is not fetched at all, which is what makes a second install cheap --
    Obsidian's `/* nosourcemap */` suffix included (`vaultlock.matches`).

    `lock` is read by `install_vault` before the settings are written, not here, so a lock this tool cannot install --
    one naming a path where a plugin id belongs, above all -- refuses the whole command before it has written a file.
    """
    for kind, name, entry in vaultlock.locked(lock):
        directory = vaultlock.target_dir(ctx, kind, name)
        wanted, why = [], None
        for f in entry["files"]:
            path = directory / f["name"]
            have = path.read_bytes() if path.is_file() else None
            if have is not None and vaultlock.matches(f["sha256"], have) and not force:
                continue
            try:
                data = vaultlock.download(f["url"])
            except vaultlock.VaultDownloadError as e:
                why = str(e)
                break
            if kernel.sha256_bytes(data) != f["sha256"]:
                why = HASH_MISMATCH % f["url"]
                break
            wanted.append((path, f["name"], have, data))
        if why is not None:
            record["refused"].append({"kind": kind, "name": name, "version": entry["version"], "why": why})
            continue
        if not wanted:
            record["unchanged"].append("%s %s" % (kind, name))
            continue
        for path, fname, have, data in wanted:
            rel = "%s/%s/%s" % (vaultlock.PLUGINS if kind == "plugin" else vaultlock.THEMES, name, fname)
            if have is None:
                record["written"].append(rel)
            elif have == data:  # --force re-downloaded what was already right: nothing to keep a copy of
                record["rewritten"].append(rel)
            else:
                record["replaced"].append({"path": rel, "backup": str(keep_copy(ctx, stamp, vaultlock.OBSIDIAN + "/" + rel, have))})
            vaultlock.write_bytes(path, data)
        record["installed"].append({"kind": kind, "name": name, "version": entry["version"],
                                    "files": [f for _p, f, _h, _d in wanted]})


def install_vault(ctx, force=False):
    """(record, lines): the whole install, as `spud vault install` prints it and as `spud init`'s step 4b folds it in.

    Takes no actor and opens no database, so init -- which resolves neither (`commands/homeinit`) -- calls it directly
    and `cmd_vault_install` does the ownership check for a person who typed the command.

    The lock is read first: it is the one thing here that can refuse outright (`vaultlock.lock_problems`, and a name in
    it that is not one plain file name is a lock this tool will not install from), and a refusal before the settings are
    written leaves the vault exactly as it was.
    """
    lock = vaultlock.read_lock(ctx)
    stamp = kernel.now().replace(":", "-")
    record = {"home": str(ctx.home), "vault": str(ctx.home / vaultlock.OBSIDIAN), "force": bool(force),
              "written": [], "rewritten": [], "replaced": [], "unchanged": [], "installed": [], "refused": []}
    install_settings(ctx, stamp, force, record)
    install_locked(ctx, stamp, force, record, lock)
    return record, install_lines(ctx, record)


def install_lines(ctx, record):
    """What the command prints: what it wrote, what it replaced and where the copy is, what it could not download, and
    the one sentence about the shipped views."""
    lines = ["vault %s: %d file(s) written, %d replaced, %d unchanged"
             % (record["vault"], len(record["written"]) + len(record["rewritten"]), len(record["replaced"]), len(record["unchanged"]))]
    lines.extend("  installed %s %s %s (%s)" % (i["kind"], i["name"], i["version"], ", ".join(i["files"]))
                 for i in record["installed"])
    lines.extend("  replaced %s (the copy it held is %s)" % (r["path"], r["backup"]) for r in record["replaced"])
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
