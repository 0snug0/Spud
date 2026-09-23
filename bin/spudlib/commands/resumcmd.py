"""commands/resumcmd: member resum."""

from pathlib import Path

from ..core import kernel
from ..state import actors, ledgerdb, lookup, transcripts


# member resum: a transcript sum stored before sums counted each API request once added every transcript entry; it is
# re-summed once per API request from the member's transcript, merged through run_totals, its old figures kept in a
# member.edited event.  The events table's kinds are fixed by its CHECK constraint, so the re-sum is recorded
# as the member edit it is rather than as a kind that would take a migration of the append-only table.
RESUM_FIGURES = ("total_tokens", "tool_uses", "duration_ms")
RESUM_KEPT = {None: "no sum", "imported": "imported", "breakdown": "counted"}  # stored_counting -> the action: nothing to do
RESUM_LEFT = ("not found", "ambiguous", "unreadable", "no usage")  # a sum without its breakdown left as it is: exit 1


def stored_counting(usage_json):
    """How a member's stored transcript sum was counted: "breakdown" (once per API request, with the per-model
    breakdown that prices it), "request" (once per request without it, as sums were stored before the breakdown),
    "entry" (the oldest sums, every transcript entry added) or "imported" (read back from a rendered note by the
    importer, with no transcript behind it); None when usage_json holds no transcript sum.  member resum
    re-sums "entry" and "request"."""
    stored, _ = transcripts.usage_parts(usage_json)
    if stored is None:
        return None
    if stored.get("imported") is True:
        return "imported"
    if stored.get("counting") != transcripts.REQUEST_COUNTING:
        return "entry"
    return "breakdown" if isinstance(stored.get("breakdown"), list) else "request"


def find_transcript(recorded):
    """(path, how, note) for a member's transcript: the recorded transcript_path when it is a file ("recorded");
    else the one file with the same session directory and file name, <session>/subagents/agent-<id>.jsonl,
    under any project directory beside the recorded one ("moved": the harness moves a worktree session's
    transcripts into another project directory when the session leaves the worktree); else None with
    "not found" or "ambiguous" and a note saying where it looked."""
    if not recorded:
        return None, "not found", "no transcript path recorded"
    path = Path(recorded)
    if path.is_file():
        return path, "recorded", None
    if path.parent.name != "subagents" or len(path.parts) < 5:
        return None, "not found", "no file at %s" % path
    project = path.parents[2]
    tail = Path(path.parents[1].name, "subagents", path.name)
    try:
        siblings = sorted(project.parent.iterdir())
    except OSError:
        siblings = []
    matches = [d / tail for d in siblings if (d / tail).is_file()]
    if len(matches) == 1:
        return matches[0], "moved", None
    if not matches:
        return None, "not found", "no file at %s, nor at <project>/%s under %s" % (path, tail, project.parent)
    return None, "ambiguous", "%d files: %s" % (len(matches), ", ".join(str(m) for m in matches))


def resum_row(member, ref):
    """What `member resum` does with one member: (its row for the table and --json, the new transcript sum
    when there is one to write).  The row's action is "re-sum", "counted" (already by request), "imported"
    or "no sum" (left, nothing to do), or one of RESUM_LEFT."""
    row = {"ref": ref, "action": None, "found": None, "transcript": None, "note": None, "requests": None,
           "old": {k: member[k] for k in RESUM_FIGURES}, "new": None}
    counting = stored_counting(member["usage_json"])
    if counting in RESUM_KEPT:
        row["action"] = RESUM_KEPT[counting]
        return row, None
    path, how, note = find_transcript(member["transcript_path"])
    if path is None:
        row.update(action=how, transcript=member["transcript_path"], note=note)
        return row, None
    row.update(found=how, transcript=str(path))
    try:
        summed = transcripts.transcript_usage(path)
    except OSError as e:
        row.update(action="unreadable", note="%s: %s" % (path, e.strerror or e))
        return row, None
    if summed is None:
        row.update(action="no usage", note="%s holds no assistant usage" % path)
        return row, None
    totals = transcripts.run_totals(member, summed=summed)
    row.update(action="re-sum", requests=summed["usage_json"]["messages"], new={k: totals[k] for k in RESUM_FIGURES})
    return row, summed


def resum_table(data):
    """member resum's text: the table, one line per member with its old -> new figures, then one line saying
    what was written."""
    lines = []
    for r in data["members"]:
        line = {"ref": r["ref"], "action": r["action"], "requests": "-" if r["requests"] is None else r["requests"]}
        for k in RESUM_FIGURES:
            old = "-" if r["old"][k] is None else str(r["old"][k])
            line[k] = old if r["new"] is None else "%s -> %s" % (old, "-" if r["new"][k] is None else r["new"][k])
        line["transcript"] = ("%s: %s" % (r["found"], r["transcript"])) if r["action"] == "re-sum" else (r["note"] or "-")
        lines.append(line)
    text = kernel.table(lines, [("member", "ref"), ("action", "action"), ("requests", "requests"), ("total_tokens", "total_tokens"),
                         ("tool_uses", "tool_uses"), ("duration_ms", "duration_ms"), ("transcript", "transcript")])
    total = len(data["members"])
    resum = sum(1 for r in data["members"] if r["action"] == "re-sum")
    left = sum(1 for r in data["members"] if r["action"] in RESUM_LEFT)
    return text + "\n" + "%s %d of %d member%s once per API request, with the per-model breakdown; %d left with a sum that added every entry or lacks the breakdown; %d already counted with the breakdown, imported, or without a transcript sum" % (
        "dry run, nothing written: would re-sum" if data["dry_run"] else "re-summed", resum, total, "" if total == 1 else "s", left, total - resum - left)


def cmd_member_resum(ctx, args):
    """member resum: stored transcript sums counted per entry, re-summed once per API request (Spud's)."""
    if bool(args.refs) == bool(args.all):
        raise kernel.SpudError(kernel.EXIT_USAGE, "member resum takes the members to re-sum (SPUD-nnn/<Name> ...) or --all, one of the two")
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "re-summing members' stored run totals (member resum)")
        if args.all:
            members = con.execute("SELECT * FROM members WHERE json_extract(usage_json, '$.source') = 'transcript' ORDER BY ticket_id, lineage").fetchall()
        else:
            members, seen = [], set()
            for ref in args.refs:
                m = lookup.get_member(con, ref)
                if m["id"] not in seen:
                    seen.add(m["id"])
                    members.append(m)
        # Transcripts are read before the write lock is taken; the write re-reads each row and merges the sum into it.
        plans = [resum_row(m, lookup.member_ref(con, m["id"])) for m in members]
        resummed = []
        if not args.dry_run and any(summed for _, summed in plans):
            at = kernel.now()
            with ledgerdb.write_txn(con):
                for (row, summed), member in zip(plans, members):
                    if summed is None:
                        continue
                    fresh = lookup.get_member_by_id(con, member["id"])
                    counting = stored_counting(fresh["usage_json"])
                    if counting in RESUM_KEPT:  # changed since it was read (a concurrent re-sum): nothing left to do
                        row.update(action=RESUM_KEPT[counting], requests=None, old={k: fresh[k] for k in RESUM_FIGURES}, new=None)
                        continue
                    totals = transcripts.run_totals(fresh, summed=summed)
                    new = {k: totals[k] for k in RESUM_FIGURES}
                    new.update(transcript_path=row["transcript"], usage_json=totals["usage_json"])
                    old = {k: fresh[k] for k in new}
                    con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in new), (*new.values(), fresh["id"]))
                    row.update(old={k: old[k] for k in RESUM_FIGURES}, new={k: new[k] for k in RESUM_FIGURES})
                    ledgerdb.write_event(con, at, actor.label, "member.edited",
                                "%s re-summed once per API request, with the per-model breakdown, from its transcript: total_tokens %s -> %s, tool_uses %s -> %s, duration_ms %s -> %s"
                                % (row["ref"], old["total_tokens"], new["total_tokens"], old["tool_uses"], new["tool_uses"], old["duration_ms"], new["duration_ms"]),
                                ticket_id=fresh["ticket_id"], member_id=fresh["id"],
                                data={"fields": sorted(k for k in new if new[k] != old[k]), "counting": transcripts.REQUEST_COUNTING, "breakdown": True, "found": row["found"],
                                      "transcript": row["transcript"], "requests": row["requests"], "old": old, "new": new})
                    resummed.append(row["ref"])
    finally:
        con.close()
    rows = [row for row, _ in plans]
    left = [r for r in rows if r["action"] in RESUM_LEFT]
    data = {"dry_run": bool(args.dry_run), "members": rows, "resummed": resummed, "left": [r["ref"] for r in left]}
    if left:
        return kernel.Result(data, resum_table(data), exit_code=kernel.EXIT_ERROR, stderr="%d sum%s without the per-model breakdown left as %s: %s" % (
            len(left), "" if len(left) == 1 else "s", "it is" if len(left) == 1 else "they are",
            "; ".join("%s (%s)" % (r["ref"], r["action"]) for r in left)))
    return kernel.Result(data, resum_table(data))
