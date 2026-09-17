"""commands/publish: render.  Moved from bin/spud_ledger.py (SPD-065); ledger commit removed by SPD-097: the home is no git checkout."""

from pathlib import Path

from ..core import kernel, markdown
from ..imports import accept
from ..render import notefiles
from ..state import actors, ledgerdb


def cmd_render(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        out_root = Path(args.out).expanduser().resolve() if args.out else ctx.home
        targets = notefiles.render_targets(con, ctx.pricing)
        written, unchanged, conflicts, discarded = [], [], [], []
        if args.discard:
            if args.out:
                raise kernel.SpudError(kernel.EXIT_USAGE, "--discard applies to the files under SPUD_HOME, not to --out")
            actor = actors.resolve_actor(con, args.actor)
            actors.require_spud(con, actor, "discarding a hand edit")
            kind, rel = accept.classify_path(ctx, args.discard)
            path = ctx.home / rel
            content = dict(targets).get(rel)
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
        records = {r["path"]: r["sha256"] for r in con.execute("SELECT path, sha256 FROM renders").fetchall()} if not args.out else {}
        to_record = []
        restyled, unreadable = [], {}  # [(path, the text the render went over)], {path: why it could not be read}
        for rel, content in targets:
            path = out_root / rel
            data = content.encode("utf-8")
            new_hash = kernel.sha256_bytes(data)
            raw = path.read_bytes() if path.is_file() else None
            on_disk = kernel.sha256_bytes(raw) if raw is not None else None
            if not args.out:
                recorded = records.get(rel)
                if recorded is not None and on_disk is not None and on_disk not in (recorded, new_hash):
                    # Neither the last render nor the new one, byte for byte.  A note may still be one
                    # of them with its frontmatter restyled (Obsidian rewrites the YAML of the notes it
                    # has open): that is no hand edit, so the render goes over it and the event keeps
                    # what it replaced.  A report has no frontmatter: any change to it is the conflict.
                    style_only, why = False, None
                    if content.startswith("---\n"):
                        last = con.execute("SELECT content FROM renders WHERE path = ?", (rel,)).fetchone()["content"]
                        style_only, why = markdown.restyled_render(raw, (last, content))
                    if not style_only:
                        conflicts.append(rel)
                        if why:
                            unreadable[rel] = why
                        continue
                    restyled.append((rel, raw.decode("utf-8")))
                if on_disk == new_hash:
                    unchanged.append(rel)
                    if recorded != new_hash:
                        to_record.append((rel, new_hash, content))
                    continue
                kernel.write_whole(path, content)
                written.append(rel)
                to_record.append((rel, new_hash, content))
            else:
                if on_disk == new_hash:
                    unchanged.append(rel)
                else:
                    kernel.write_whole(path, content)
                    written.append(rel)
        if not args.out:
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
                for rel in conflicts:
                    conflict = {"conflict": True, "path": rel}
                    if rel in unreadable:
                        conflict["unreadable"] = unreadable[rel]
                    ledgerdb.write_event(con, at, "spud", "render", "refused to overwrite hand-edited %s" % rel, data=conflict)
                ledgerdb.write_event(con, at, "spud", "render", "rendered %d files, %d unchanged, %d conflicts" % (len(written), len(unchanged), len(conflicts)),
                            data={"written": written, "unchanged": len(unchanged), "conflicts": conflicts,
                                  "restyled": [rel for rel, _ in restyled], "through_event_id": through})
    finally:
        con.close()
    data = {"out": str(out_root), "written": written, "unchanged": unchanged, "conflicts": conflicts, "discarded": discarded,
            "restyled": [rel for rel, _ in restyled]}
    lines = ["rendered into %s: %d written, %d unchanged%s%s" % (
        out_root, len(written), len(unchanged),
        (", %d hand edit discarded" % len(discarded)) if discarded else "",
        (", %d style-only rewrite re-rendered" % len(restyled)) if restyled else "",
    )]
    lines += ["  discarded the hand edit of %s" % rel for rel in discarded]
    lines += ["  re-rendered %s over a style-only frontmatter rewrite" % rel for rel, _ in restyled]
    lines += ["  wrote %s" % rel for rel in written]
    if conflicts:
        raise kernel.SpudError(
            kernel.EXIT_CONFLICT,
            "hand-edited, not overwritten (accept with `spud import --file <path>`): " + ", ".join(conflicts),
            data=data,
        )
    return kernel.Result(data, "\n".join(lines))
