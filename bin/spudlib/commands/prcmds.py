"""commands/prcmds: Landing pull requests: pr record, pr reconcile, pr list, and the blocks board, card and doctor show (SPD-077)."""

import re
from datetime import datetime

from . import ghread
from ..core import kernel
from ..hooks import worktrees
from ..state import actors, ledgerdb, lookup

# SPD-077.  Under `landing: pr` a ticket's done move used to depend on the session that opened the pull request still being
# alive when it merged: BAD-058 sat `active` for 44 minutes after its work had landed, because its session had stopped and
# nothing in the ledger knew a pull request existed.  So: Spud records the pull request when he opens it (`pr record`, his,
# because opening one is part of landing and a member neither commits nor pushes), the reconciler reads each recorded,
# not-yet-settled one with a single `gh pr view` off every hook path, and the board says what a merge leaves owed.
#
# Four things this never does.  It never merges a pull request.  It never acts on one the ledger did not record.  It runs
# no git: the worktree and branch cleanup a landing owes is named on the board and performed by Spud.  And it never moves a
# ticket to done -- that is a judgment, and the whole job here is to make sure the judgment gets asked for.
#
# The reconciler's writes name the actor `reconcile` (kernel.RECONCILE_ACTOR), not a person: `pr reconcile` takes no --as,
# which is what lets `spud board` run it on its own.  `board --brief` and every hook read stored state only.
#
# Why this is one module past the 250-line look-again point: it is one command family and the three blocks its callers
# show, and the two halves have the same users -- `views` reads reconcile, open_ticket_ids, board_block and pr_read_text
# for one board run, and `doctor` reads failed_checks.  Splitting the display off would make every caller import both
# halves, which is a page break, not a seam.  The words a hook must also say -- the state, the owed phrase and the nag
# line -- are in state/lookup, the lower layer projects/sessions can reach without putting this module on the hook path.

PR_URL = re.compile(r"\Ahttps?://[^\s/]+/\S+\Z")
PR_NUMBER = re.compile(r"/pull/(\d+)(?:[/?#]|\Z)")

STALE = 600          # seconds a stored check may be old before `spud board` reads the pull request again
BUDGET = 8.0         # seconds the whole automatic run may spend, however many pull requests are unsettled
OFF_TEXT = "the gh reader is off (SPUD_GH=off): no pull request was read"


def parse_pr_url(url):
    """(url, number) for a pull request URL, the number None when the URL carries none; refuses anything that is not an
    absolute http(s) URL, since the reconciler hands it to `gh pr view` unchanged."""
    text = (url or "").strip()
    if not PR_URL.match(text):
        raise kernel.SpudError(kernel.EXIT_ERROR, "--url must be the pull request's absolute http(s) URL, as `gh pr create`"
                        " printed it (https://github.com/<owner>/<repo>/pull/<n>), not %r" % url)
    m = PR_NUMBER.search(text)
    return text, int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# pr record (Spud's)
# ----------------------------------------------------------------------------


def cmd_pr_record(ctx, args):
    """Record the pull request a ticket lands through: its URL, its head branch (also the local branch the cleanup will
    owe) and the worktree the cleanup will owe, which defaults to the one the ticket is bound to.  Recording the same URL
    on the same ticket again refreshes the branch and the worktree; on another ticket it is refused, since one pull
    request lands one ticket."""
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        actors.require_spud(con, actor, "recording a landing pull request")
        url, number = parse_pr_url(args.url)
        branch = (args.branch or "").strip()
        if not branch:
            raise kernel.SpudError(kernel.EXIT_USAGE, "--branch is the pull request's head branch, which is also the local branch"
                            " the landing owes a `git branch -D`: name it")
        at = kernel.now()
        with ledgerdb.write_txn(con):
            t = lookup.get_ticket(con, args.ticket)
            if t["status"] in ("done", "declined"):
                raise kernel.SpudError(kernel.EXIT_TRANSITION, "%s is %s; a landing pull request is recorded on a ticket still open"
                                % (t["key"], t["status"]))
            worktree = args.worktree if args.worktree is not None else t["worktree"]
            existing = con.execute("SELECT * FROM pull_requests WHERE url = ?", (url,)).fetchone()
            if existing is not None and existing["ticket_id"] != t["id"]:
                other = lookup.get_ticket_by_id(con, existing["ticket_id"])
                raise kernel.SpudError(kernel.EXIT_ERROR, "%s is already recorded against %s; one pull request lands one ticket"
                                % (url, other["key"] if other else "another ticket"))
            if existing is not None:
                con.execute("UPDATE pull_requests SET branch = ?, worktree = ?, number = ? WHERE id = ?",
                            (branch, worktree, number, existing["id"]))
                pr_id, again = existing["id"], True
            else:
                cur = con.execute(
                    "INSERT INTO pull_requests (ticket_id, url, number, branch, worktree, state, recorded_at, recorded_by)"
                    " VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                    (t["id"], url, number, branch, worktree, at, actor.label))
                pr_id, again = cur.lastrowid, False
            ledgerdb.write_event(con, at, actor.label, "pr.recorded",
                                 "%s lands through pull request %s (%s)" % (t["key"], lookup.pr_name({"number": number, "url": url}), url),
                                 ticket_id=t["id"], data={"url": url, "number": number, "branch": branch, "worktree": worktree, "again": again})
        d = lookup.pr_dict(con, con.execute("SELECT * FROM pull_requests WHERE id = ?", (pr_id,)).fetchone())
    finally:
        con.close()
    text = "%s: pull request %s recorded (branch %s%s)%s" % (
        d["ticket"], lookup.pr_name(d), d["branch"], ", worktree %s" % d["worktree"] if d["worktree"] else ", no worktree",
        " again" if again else "")
    return kernel.Result({"pull_request": d, "again": again}, text)


# ----------------------------------------------------------------------------
# pr reconcile (any actor or none; its writes are the actor `reconcile`)
# ----------------------------------------------------------------------------


def pr_checkout(ctx, con, pr_row):
    """The directory a pull request's `gh pr view` runs in: its ticket's project's main checkout (never the ticket's
    worktree, which a landing deletes).  None when the project cannot be read, and ghread ignores one that is gone."""
    t = lookup.get_ticket_by_id(con, pr_row["ticket_id"])
    project = con.execute("SELECT * FROM projects WHERE id = ?", (t["project_id"],)).fetchone() if t else None
    return worktrees.project_root(ctx, project) if project else None


def reconcile(ctx, con, ticket_ids=None, stale=0, budget=BUDGET, timeout=ghread.CALL_TIMEOUT):
    """One `gh pr view` per recorded, still-open pull request whose stored check is older than `stale` seconds, each read
    in its ticket's project checkout, stopping once `budget` seconds are spent.  A read that fails -- no gh, no auth, no
    network, a rate limit -- is stored as a failed check and never stops the run, and a settled pull request is never read
    again.  Each answer is written in its own short transaction, after its subprocess, so no read is made under the write
    lock."""
    out = {"off": not ghread.enabled(), "checked": [], "skipped": 0, "failed": 0, "settled": [], "out_of_budget": False}
    if out["off"]:
        return out
    started = datetime.now().astimezone()
    deadline = None if budget is None else started.timestamp() + budget
    for p in lookup.pull_requests(con, ticket_ids):
        if p["state"] != "open":
            continue
        age = kernel.seconds_since(p["checked_at"], started) if p["checked_at"] else None
        if age is not None and age < stale:  # a stamp that cannot be read counts as no check, and is read again
            out["skipped"] += 1
            continue
        if deadline is not None and datetime.now().astimezone().timestamp() >= deadline:
            out["out_of_budget"] = True
            break
        answer, why = ghread.pr_view(p["url"], cwd=pr_checkout(ctx, con, p), timeout=timeout)
        at = kernel.now()
        with ledgerdb.write_txn(con):
            fresh = con.execute("SELECT * FROM pull_requests WHERE id = ?", (p["id"],)).fetchone()
            if fresh is None or fresh["state"] != "open":
                continue  # another run settled it while this read was in flight
            if answer is None:
                con.execute("UPDATE pull_requests SET checked_at = ?, check_error = ? WHERE id = ?", (at, why, p["id"]))
                out["failed"] += 1
            else:
                settled = at if answer["state"] != "open" else None
                con.execute("UPDATE pull_requests SET checked_at = ?, check_error = NULL, state = ?, merged_at = ?,"
                            " number = COALESCE(number, ?), settled_at = ? WHERE id = ?",
                            (at, answer["state"], answer["merged_at"], answer["number"], settled, p["id"]))
                if settled is not None:
                    t = lookup.get_ticket_by_id(con, p["ticket_id"])
                    ledgerdb.write_event(con, at, kernel.RECONCILE_ACTOR, "pr.state",
                                         "%s: pull request %s is %s (%s)" % (t["key"], lookup.pr_name(p), answer["state"], p["url"]),
                                         ticket_id=p["ticket_id"],
                                         data={"url": p["url"], "from": "open", "to": answer["state"], "merged_at": answer["merged_at"],
                                               "branch": p["branch"], "worktree": p["worktree"]})
                    out["settled"].append(p["url"])
            out["checked"].append({"url": p["url"], "state": (answer or {}).get("state", "open"), "error": why})
    return out


def cmd_pr_reconcile(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        ticket_ids = None
        if args.ticket:
            ticket_ids = [lookup.get_ticket(con, args.ticket)["id"]]
        out = reconcile(ctx, con, ticket_ids, stale=args.stale, budget=args.budget, timeout=args.timeout)
        rows = [lookup.pr_dict(con, p) for p in lookup.pull_requests(con, ticket_ids)]
    finally:
        con.close()
    if out["off"]:
        return kernel.Result({"reconcile": out, "pull_requests": rows}, OFF_TEXT)
    lines = ["read %d pull request(s), %d skipped as fresh, %d read failed, %d newly settled%s" % (
        len(out["checked"]), out["skipped"], out["failed"], len(out["settled"]),
        "; the budget ran out before the rest" if out["out_of_budget"] else "")]
    lines += list_lines(rows)
    return kernel.Result({"reconcile": out, "pull_requests": rows}, "\n".join(lines))


# ----------------------------------------------------------------------------
# pr list, and the blocks board, card and doctor show
# ----------------------------------------------------------------------------


def pr_read_text(d, now=None):
    """A pull request's state with the read behind it: `merged <minute>`, `closed unmerged <minute>`, or `open` with how
    long ago it was read and why the last read failed."""
    if d["state"] != "open":
        return lookup.pr_state_text(d)
    if d["check_error"]:
        return "open, last read failed %s: %s" % (kernel.ago_text(kernel.seconds_since(d["checked_at"], now)), d["check_error"])
    if not d["checked_at"]:
        return "open, never read"
    return "open, read %s" % kernel.ago_text(kernel.seconds_since(d["checked_at"], now))


def pr_lines(rows, now=None):
    """One indented line per pull request -- ticket, number, state, URL -- with what a merge leaves owed on its own line
    under it, so a merge nobody has acted on is the loudest thing in the block."""
    if not rows:
        return []
    width = max(len(d["ticket"]) for d in rows)
    lines = []
    for d in rows:
        lines.append("  %s  %s %s  %s" % (d["ticket"].ljust(width), lookup.pr_name(d), pr_read_text(d, now), d["url"]))
        owed = lookup.pr_owed(d)
        if owed:
            lines.append("  %s  owed: %s" % (" " * width, "; ".join(owed)))
    return lines


def open_ticket_ids(con, rows):
    """The ids of the tickets a board row set shows that are still open: v_board carries keys, not ids."""
    keys = [r["key"] for r in rows if r["status"] not in ("done", "declined")]
    if not keys:
        return []
    found = con.execute("SELECT id FROM tickets WHERE key IN (%s)" % ",".join("?" * len(keys)), keys).fetchall()
    return [r["id"] for r in found]


def board_block(con, ticket_ids):
    """(the dicts, the board's `pull requests:` block): every recorded pull request of a ticket the board shows that is
    still open.  A closed ticket keeps its rows as history, the way it keeps its worktree path, and they are not shown."""
    prs = [lookup.pr_dict(con, p) for p in lookup.pull_requests(con, ticket_ids)]
    return prs, (["", "pull requests:"] + pr_lines(prs)) if prs else []


def list_lines(rows):
    return pr_lines(rows) or ["(no pull request recorded)"]


def cmd_pr_list(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        ticket_ids = [lookup.get_ticket(con, args.ticket)["id"]] if args.ticket else None
        rows = [lookup.pr_dict(con, p) for p in lookup.pull_requests(con, ticket_ids)]
        if args.project:
            lookup.get_project(con, args.project)
            rows = [d for d in rows if d["project"] == args.project]
        if args.open:
            rows = [d for d in rows if d["state"] == "open"]
    finally:
        con.close()
    return kernel.Result({"pull_requests": rows}, "\n".join(list_lines(rows)))


def failed_checks(con):
    """Every still-open pull request of an open ticket whose last read failed (SPD-077): what `doctor` reports, because
    while a read keeps failing the ledger cannot see whether the landing happened."""
    out = []
    for p in lookup.pull_requests(con):
        if p["state"] != "open" or not p["check_error"]:
            continue
        d = lookup.pr_dict(con, p)
        if d["ticket_status"] in ("done", "declined"):
            continue
        out.append(d)
    return out
