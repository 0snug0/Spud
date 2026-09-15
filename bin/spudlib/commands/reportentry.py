"""commands/reportentry: Report entries and --next, shared by the recording commands.  Moved from bin/spud_ledger.py (SPD-065)."""

from ..core import kernel
from ..state import actors, ledgerdb


def check_next(con, actor, args):
    """--next on a command that writes a report entry: Spud's alone (exit 3), and never empty (exit 2).
    Checked before anything is written; a command that turns out to write no entry refuses --next itself."""
    if args.next is None:
        return
    actors.require_spud(con, actor, "the Next line of a report entry (--next)")
    if not args.next.strip():
        raise kernel.SpudError(kernel.EXIT_USAGE, "--next is empty: give the Next line, or leave --next out")


def no_entry_for_next(why):
    """The usage error of --next given to a command that writes no report entry; raised before it writes."""
    return kernel.SpudError(kernel.EXIT_USAGE, "--next has no report entry to go on: %s (leave --next out, or use `spud report add`)" % why)


def write_report_entry(con, at, title, generated, ticket_id, member_id=None, lines=(), next_line=None):
    """A generated report.entry, Spud's, in the caller's transaction: its body is `- ` lines, the Next line last."""
    body = "\n".join(["- " + line for line in lines] + ([] if next_line is None else ["- Next: " + next_line]))
    event_id = ledgerdb.write_event(con, at, "spud", "report.entry", body, ticket_id=ticket_id, member_id=member_id,
                           data={"title": title, "generated": generated})
    return {"id": event_id, "at": at, "title": title, "body": body}


def report_entry_line(entry):
    """The line `report add` prints, and every command that writes an entry prints after its own."""
    return "report entry %s — %s added to reports/%s.md" % (entry["at"][11:16], entry["title"], entry["at"][:10])


def with_report_entry(data, text, entry):
    """A command's Result with the report entry it wrote, if it wrote one: `report_entry` in --json, its line in the text."""
    if entry is None:
        return kernel.Result(data, text)
    return kernel.Result(dict(data, report_entry=entry), text + "\n" + report_entry_line(entry))
