"""commands/publish: render and ledger commit.  Moved from bin/spud_ledger.py (SPD-065)."""

import os
import re
from pathlib import Path

from ..core import homeconf, kernel, lazy, markdown
from ..hooks import hookio, worktrees
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
SUBJECT_TICKET_KEY = re.compile(r"[A-Z][A-Z0-9]*-\d{3}")


def cmd_ledger_commit(ctx, args):
    """spud --as spud ledger commit (design section 5.3): render, stage only ledger/ and reports/ at the home, commit on the
    home's default branch and push.  Refused from a linked worktree of any repository, off the default branch, with other
    paths already staged, and (a usage error, checked first) with a subject that names no ticket."""
    message = args.message or ""
    subject = message.strip().split("\n", 1)[0] if message.strip() else ""
    if not SUBJECT_TICKET_KEY.search(subject):
        raise kernel.SpudError(kernel.EXIT_USAGE, "the commit subject must name its ticket (a key such as SPD-014): %r" % subject)
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "committing the ledger")
        home_row = con.execute("SELECT * FROM projects WHERE id = 1").fetchone()
    finally:
        con.close()
    home = str(ctx.home)
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    if cwd:
        proc = homeconf.run_git(cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir", timeout=30)
        lines = proc.stdout.strip().split("\n") if proc.returncode == 0 else []
        if len(lines) == 2 and worktrees.file_identity(lines[0]) != worktrees.file_identity(lines[1]):
            raise kernel.SpudError(kernel.EXIT_ERROR, "ledger commit refuses from a linked worktree (%s): run it from a main checkout: ExitWorktree (keep) first" % cwd)
    if not os.path.lexists(os.path.join(home, ".git")):
        raise kernel.SpudError(kernel.EXIT_ERROR, "the home %s is not a git repository" % home)
    branch_proc = homeconf.run_git(home, "symbolic-ref", "--quiet", "--short", "HEAD", timeout=30)
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else None
    if branch != home_row["default_branch"]:
        raise kernel.SpudError(kernel.EXIT_ERROR, "the home is on %s, not its default branch %s; the ledger is committed on %s" % (branch or "a detached HEAD", home_row["default_branch"], home_row["default_branch"]))

    def staged():
        proc = homeconf.run_git(home, "diff", "--cached", "--name-only", "-z", timeout=60)
        if proc.returncode != 0:
            raise kernel.SpudError(kernel.EXIT_ERROR, "git diff --cached failed in %s: %s" % (home, proc.stderr.strip()))
        return [f for f in proc.stdout.split("\0") if f]

    elsewhere = [f for f in staged() if not (f.startswith("ledger/") or f.startswith("reports/"))]
    if elsewhere:
        raise kernel.SpudError(kernel.EXIT_ERROR, "already staged outside ledger/ and reports/: %s; commit those with plain git, or unstage them, before a ledger commit" % ", ".join(elsewhere))
    rendered = cmd_render(ctx, lazy.argparse.Namespace(out=None, discard=None, actor=args.actor))  # a hand edit exits 6 and commits nothing
    paths = [d for d in hookio.GENERATED_ROOTS if (ctx.home / d).exists()]
    if paths:
        proc = homeconf.run_git(home, "add", "--", *paths, timeout=120)
        if proc.returncode != 0:
            raise kernel.SpudError(kernel.EXIT_ERROR, "git add failed in %s: %s" % (home, proc.stderr.strip()))
    files = staged()
    if not files:
        return kernel.Result({"committed": False, "rendered": rendered.data, "branch": branch}, "nothing to commit")
    proc = homeconf.run_git(home, "commit", "-q", "-F", "-", input=message if message.endswith("\n") else message + "\n", timeout=120)
    if proc.returncode != 0:
        raise kernel.SpudError(kernel.EXIT_ERROR, "git commit failed in %s: %s" % (home, (proc.stderr or proc.stdout).strip()))
    sha = homeconf.run_git(home, "rev-parse", "HEAD", timeout=30).stdout.strip()
    pushed, push_error = False, None
    if not args.no_push:
        proc = homeconf.run_git(home, "push", "origin", branch, timeout=600)
        pushed = proc.returncode == 0
        push_error = None if pushed else (proc.stderr or proc.stdout).strip()
    con = ledgerdb.connect(ctx)
    try:
        at = kernel.now()
        with ledgerdb.write_txn(con):
            ledgerdb.write_event(con, at, "spud", "commit", subject, data={"sha": sha, "files": files, "branch": branch, "pushed": pushed, "push_error": push_error})
    finally:
        con.close()
    data = {"committed": True, "sha": sha, "files": files, "branch": branch, "pushed": pushed}
    if push_error is not None:
        raise kernel.SpudError(kernel.EXIT_ERROR, "committed %s on %s, but the push failed (the commit is kept): %s" % (sha[:12], branch, push_error), data=data)
    return kernel.Result(data, "committed %s on %s: %d file%s%s" % (sha[:12], branch, len(files), "" if len(files) == 1 else "s", ", pushed" if pushed else ", not pushed (--no-push)"))
