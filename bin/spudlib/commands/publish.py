"""commands/publish: render, one pass at a time.  Moved from bin/spud_ledger.py (SPD-065); since SPD-097 the pass the watcher
repeats, under a lock, writing the database only when it wrote a file, restyled one or found a conflict it had not logged."""

import contextlib
import fcntl
import os
from pathlib import Path

from ..core import kernel, launchagents, markdown
from ..imports import accept
from ..render import notefiles
from ..state import actors, ledgerdb

RENDER_LOCK = "render.lock"  # <home>/.spud/render.lock: one render at a time (SPD-097); a manual render waits for the watcher's pass


@contextlib.contextmanager
def render_lock(ctx):
    """Exclusive flock on <home>/.spud/render.lock for the pass; waits for a pass in progress.  A no-op before `spud init`
    (no state directory yet)."""
    state = ctx.home / ".spud"
    if not state.is_dir():
        yield
        return
    fd = os.open(str(state / RENDER_LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def conflict_logged(con, rel, on_disk):
    """Whether a render event already refused this path at this on-disk hash: a conflict is logged once, not on every pass."""
    return con.execute("SELECT 1 FROM events WHERE kind = 'render' AND json_extract(data, '$.conflict') = 1 AND json_extract(data, '$.path') = ?"
                       " AND json_extract(data, '$.sha256') = ? LIMIT 1", (rel, on_disk)).fetchone() is not None


def render_pass(ctx, con, out_root=None, check_only=False):
    """One pass over every generated file.  Into the home (out_root None): records the hashes, writes each new conflict once
    per path and on-disk hash, and touches the database only when it wrote a file, re-rendered a style-only rewrite,
    found a new conflict or has a hash to record (SPD-097: a render that changes nothing writes nothing).  Into --out:
    checks and records nothing.  check_only: what the pass would write and refuse, writing nothing at all (home move's
    preconditions, doctor).  Returns written, unchanged, conflicts, restyled and new_conflicts as relative paths, and
    `through`, the highest event id the pass rendered."""
    into_home = out_root is None
    root = ctx.home if into_home else out_root
    targets = notefiles.render_targets(con, ctx.pricing)
    records = {r["path"]: r["sha256"] for r in con.execute("SELECT path, sha256 FROM renders").fetchall()} if into_home else {}
    written, unchanged, conflicts, restyled, unreadable, to_record, new_conflicts = [], [], [], [], {}, [], []
    for rel, content in targets:
        path = root / rel
        data = content.encode("utf-8")
        new_hash = kernel.sha256_bytes(data)
        raw = path.read_bytes() if path.is_file() else None
        on_disk = kernel.sha256_bytes(raw) if raw is not None else None
        if into_home:
            recorded = records.get(rel)
            if recorded is not None and on_disk is not None and on_disk not in (recorded, new_hash):
                # Neither the last render nor the new one, byte for byte.  A note may still be one of them with its
                # frontmatter restyled (Obsidian rewrites the YAML of the notes it has open): that is no hand edit, so
                # the render goes over it and the event keeps what it replaced.  A report has no frontmatter: any
                # change to it is the conflict.
                style_only, why = False, None
                if content.startswith("---\n"):
                    last = con.execute("SELECT content FROM renders WHERE path = ?", (rel,)).fetchone()["content"]
                    style_only, why = markdown.restyled_render(raw, (last, content))
                if not style_only:
                    conflicts.append(rel)
                    if why:
                        unreadable[rel] = why
                    if not conflict_logged(con, rel, on_disk):
                        new_conflicts.append((rel, on_disk))
                    continue
                restyled.append((rel, raw.decode("utf-8")))
            if on_disk == new_hash:
                unchanged.append(rel)
                if recorded != new_hash:
                    to_record.append((rel, new_hash, content))
                continue
            written.append(rel)
            if check_only:
                continue
            kernel.write_whole(path, content)
            to_record.append((rel, new_hash, content))
        elif on_disk == new_hash:
            unchanged.append(rel)
        else:
            written.append(rel)
            if not check_only:
                kernel.write_whole(path, content)
    through = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    if into_home and not check_only and (to_record or restyled or new_conflicts):
        at = kernel.now()
        with ledgerdb.write_txn(con):
            through = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
            for rel, new_hash, content in to_record:
                con.execute(
                    "INSERT INTO renders (path, sha256, content, rendered_at, through_event_id) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, content = excluded.content,"
                    " rendered_at = excluded.rendered_at, through_event_id = excluded.through_event_id",
                    (rel, new_hash, content, at, through),
                )
            for rel, text in restyled:
                ledgerdb.write_event(con, at, "spud", "render", "re-rendered %s over a style-only frontmatter rewrite" % rel,
                                     data={"style_only": True, "path": rel, "text": text})
            for rel, on_disk in new_conflicts:
                conflict = {"conflict": True, "path": rel, "sha256": on_disk}
                if rel in unreadable:
                    conflict["unreadable"] = unreadable[rel]
                ledgerdb.write_event(con, at, "spud", "render", "refused to overwrite hand-edited %s" % rel, data=conflict)
            if written or restyled or new_conflicts:
                ledgerdb.write_event(con, at, "spud", "render", "rendered %d files, %d unchanged, %d conflicts" % (len(written), len(unchanged), len(conflicts)),
                                     data={"written": written, "unchanged": len(unchanged), "conflicts": conflicts,
                                           "restyled": [rel for rel, _ in restyled], "through_event_id": through})
    if into_home and not check_only:
        # SPD-117: every pass leaves its watermark, the pass that wrote nothing included, so doctor, the board and the
        # SessionStart context can tell a vault that has caught up from one a stuck watcher left behind.  Not a row: this
        # is the one record a no-op pass makes, and SPD-097's rule is that such a pass writes nothing to the database.
        launchagents.record_render(ctx, through, kernel.now())
    return {"out": str(root), "written": written, "unchanged": unchanged, "conflicts": conflicts, "restyled": [rel for rel, _ in restyled],
            "new_conflicts": [rel for rel, _ in new_conflicts], "through": through}


def cmd_render(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        out_root = Path(args.out).expanduser().resolve() if args.out else None
        discarded = []
        with render_lock(ctx) if out_root is None else contextlib.nullcontext():
            if args.discard:
                if out_root is not None:
                    raise kernel.SpudError(kernel.EXIT_USAGE, "--discard applies to the files under SPUD_HOME, not to --out")
                actor = actors.resolve_actor(con, args.actor)
                actors.require_spud(con, actor, "discarding a hand edit")
                kind, rel = accept.classify_path(ctx, args.discard)
                path = ctx.home / rel
                content = dict(notefiles.render_targets(con, ctx.pricing)).get(rel)
                if content is None:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not a file this ledger generates" % rel)
                if not path.is_file():
                    raise kernel.SpudError(kernel.EXIT_ERROR, "no such file: %s" % path)
                old_text = path.read_text(encoding="utf-8")
                kernel.write_whole(path, content)
                discarded.append(rel)
                at = kernel.now()
                with ledgerdb.write_txn(con):
                    con.execute(
                        "INSERT INTO renders (path, sha256, content, rendered_at, through_event_id) VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(id), 0) FROM events))"
                        " ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, content = excluded.content, rendered_at = excluded.rendered_at, through_event_id = excluded.through_event_id",
                        (rel, kernel.sha256_bytes(content.encode("utf-8")), content, at),
                    )
                    ledgerdb.write_event(con, at, actor.label, "render", "discarded the hand edit of %s" % rel, data={"discarded": True, "path": rel, "text": old_text})
            result = render_pass(ctx, con, out_root)
    finally:
        con.close()
    written, unchanged, conflicts, restyled = result["written"], result["unchanged"], result["conflicts"], result["restyled"]
    data = {"out": result["out"], "written": written, "unchanged": unchanged, "conflicts": conflicts, "discarded": discarded, "restyled": restyled}
    lines = ["rendered into %s: %d written, %d unchanged%s%s" % (
        result["out"], len(written), len(unchanged),
        (", %d hand edit discarded" % len(discarded)) if discarded else "",
        (", %d style-only rewrite re-rendered" % len(restyled)) if restyled else "",
    )]
    lines += ["  discarded the hand edit of %s" % rel for rel in discarded]
    lines += ["  re-rendered %s over a style-only frontmatter rewrite" % rel for rel in restyled]
    lines += ["  wrote %s" % rel for rel in written]
    if conflicts:
        raise kernel.SpudError(
            kernel.EXIT_CONFLICT,
            "hand-edited, not overwritten (accept with `spud import --file <path>`): " + ", ".join(conflicts),
            data=data,
        )
    return kernel.Result(data, "\n".join(lines))
