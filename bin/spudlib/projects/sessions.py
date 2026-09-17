"""projects/sessions: Session mode and claims, the /spud skill text, the board brief, the session commands.  Moved from bin/spud_ledger.py (SPD-065)."""

import os

from ..core import homeconf, kernel
from ..hooks import hookio, worktrees
from ..state import actors, ledgerdb, lookup


def brief_state(m, today=None):
    """A live member's state on the brief board: its status, or `returned HH:MM, unrecorded` for a member that
    returned and has no verdict yet (the date too when it stopped on another day).  A session holds only for its
    own members (SPD-018), so this is how a later or parallel session sees another session's."""
    if m["status"] == "active" and m["stopped_at"] and not (m["outcome"] or "").strip():
        same_day = m["stopped_at"][:10] == (today or kernel.now())[:10]
        return "returned %s, unrecorded" % (m["stopped_at"][11:16] if same_day else kernel.fm_minute(m["stopped_at"]))
    return m["status"]


def board_line(r, day):
    """One ticket's line: key, status, priority, title, and what qualifies it in one parenthesis — its lead, and for a
    parked ticket (SPD-096) the date, read as `due back` from the day it names, and the reason."""
    inside = ["lead %s" % r["lead"]] if r["lead"] else []
    if r["status"] == "parked":
        until = r["parked_until"]
        when = (("due back %s: " if until <= day else "until %s: ") % until) if until else ""
        inside.append(when + (r["parked_reason"] or ""))
    return "%s %s %s %s%s" % (r["key"], r["status"], r["priority"], r["title"], (" (%s)" % "; ".join(inside)) if inside else "")


def settled_pr_lines(con, key):
    """The lines a ticket's merged or closed-unmerged pull requests put on the brief board (SPD-077).  Stored state only:
    no `gh` and no git here, because this text is injected at every session start -- which is the point, since the session
    that opened the pull request is long gone by the time it merges (BAD-058).  An open one says nothing: the full board
    reads it and shows it there."""
    rows = con.execute("SELECT p.* FROM pull_requests p JOIN tickets t ON t.id = p.ticket_id WHERE t.key = ? AND p.state <> 'open'"
                       " ORDER BY p.id", (key,)).fetchall()
    return ["  " + lookup.pr_nag_line(lookup.pr_dict(con, p)) for p in rows]


def board_brief_text(con, rows=None, parked=False):
    """Open tickets and their live members, one line each (also the SessionStart context): active tickets with their live
    members and any settled landing pull request (SPD-077), then the parked tickets whose date has arrived, then queued,
    then one count line for the parked (SPD-096).
    A due-back line sits above the queue and the count line last because every SessionStart context keeps whole lines
    from the top (fit_bytes): the line meant to nag must survive the cut, and the count may be cut.  With no parked ticket the
    text is what it was before SPD-096, byte for byte.  `parked`: every parked ticket instead, due or not."""
    if rows is None:
        rows = [dict(r) for r in con.execute("SELECT * FROM v_board").fetchall()]
    now = kernel.now()
    day = now[:10]
    if parked:  # spud board --parked --brief
        return "\n".join(board_line(r, day) for r in rows if r["status"] == "parked") or "(no parked ticket)"
    active, queued, shelved = [], [], []
    for r in rows:
        if r["status"] in ("done", "declined"):
            continue
        if r["status"] == "parked":
            shelved.append(r)
            continue
        line = board_line(r, day)
        if r["status"] != "active":
            queued += [line] + settled_pr_lines(con, r["key"])
            continue
        active.append(line)
        for m in con.execute(
            "SELECT m.* FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE t.key = ? AND m.status IN ('planned','active') ORDER BY m.lineage",
            (r["key"],),
        ).fetchall():
            active.append("  %s (%s, %s, %s) %s" % (m["name"], m["lineage"], lookup.persona_label(m), m["model"], brief_state(m, now)))
        active.extend(settled_pr_lines(con, r["key"]))
    due = [r for r in shelved if r["parked_until"] and r["parked_until"] <= day]
    lines = active + [line for r in due for line in [board_line(r, day)] + settled_pr_lines(con, r["key"])] + queued
    if shelved:
        lines.append("%d parked%s (spud board --parked)" % (len(shelved), (", %d due back" % len(due)) if due else ""))
    return "\n".join(lines) or "(no open tickets)"


CLAIM_CARD_CAP = 1536        # bytes: what `session claim` prints
# Bytes of UTF-8: every SessionStart context, the home's, outside's and a project's (SPD-048).  The harness keeps a hook's
# additionalContext inline up to 10,000 UTF-16 code units and past that gives the model a ~2 KB preview and a file path
# (code.claude.com/docs/en/hooks, "JSON output"; the design's probe P2 saw that preview).  tests/probes/context_limit.py
# measured the edge on Claude Code 2.1.274: 10,000 units inline, 10,001 persisted, 18,886 bytes of 10,000 two-byte
# characters inline, 6,000 four-byte characters (11,320 units) persisted.  UTF-8 never has fewer bytes than UTF-16 has
# units, so a cap in bytes holds whatever the text, and 8,000 is 80% of the limit.
SESSION_CONTEXT_CAP = 8000
PLAIN_NOTICE_CAP = 300       # bytes: the one line a session that is not Spud gets
# The /spud skill's steps, the one source of the installed SKILL.md and of the context the UserPromptSubmit hook gives a
# session it claims (SPD-057), so the two cannot drift.  Step 2 is the claim: the skill runs it, the hook has made it.
SKILL_HEAD = """---
name: spud
description: Make this session Spud, Eric's second brain, in a repository registered as a Spud project. Only when Eric types /spud or asks for Spud in this session.
disable-model-invocation: true
---
You are becoming Spud in this session.
"""
SKILL_STEPS = (
    "Read {home}/CLAUDE.md in full, then {home}/spud.config.json. They bind you from now on, with the rule in step 3.",
    "{claim}",
    "The claim names the project. This repository's own CLAUDE.md and .claude/skills govern how deliverables are built, verified, committed and landed. Spud's laws govern delegation, the ledger, and who writes what. In a conflict about the first, the project wins; about the second, Spud's laws win."
    " A code ticket is built in the linked worktree it is bound to: `member new` refuses from the main checkout, so enter a worktree first.",
    "Run the session ritual of CLAUDE.md from step 2, including its step 4: set the session title to `{title}`.",
)
SKILL_CLAIM = "Run `python3.14 -I -S {launcher} --as spud session claim`. If it refuses, quote the refusal, say this session is not Spud, and stop following these steps."
HOOK_CLAIM = ("The ledger's hook has made the claim (the card below); do not run `session claim`. If Eric says this session is not to be Spud,"
              " run `python3.14 -I -S {launcher} --as spud session release`.")
SKILL_TITLE = "<KEY> - <what this session does>"


def skill_steps(home, claim, title):
    """The numbered steps, one per line, with the claim step and the title filled in."""
    return "".join("%d. %s\n" % (n, step.format(home=home, claim=claim, title=title)) for n, step in enumerate(SKILL_STEPS, start=1))


def skill_markdown(ctx):
    """What project install writes to ~/.claude/skills/spud/SKILL.md: the home's CLAUDE.md and config, the tool's launcher (SPD-097)."""
    return SKILL_HEAD + "\n" + skill_steps(ctx.home, SKILL_CLAIM.format(launcher=ctx.launcher), SKILL_TITLE)


def spudagent_shaped(tool_input):
    """A spawn the PreToolUse(Agent) check is for in any session: subagent_type spudagent, or a member description."""
    description = tool_input.get("description") if isinstance(tool_input.get("description"), str) else ""
    return tool_input.get("subagent_type") == "spudagent" or hookio.DESCRIPTION.match(description) is not None


def pending_spawn(con, session_id):
    """Whether the session has an allowed spawn still waiting to bind: a subagent starting now may be that member."""
    if not isinstance(session_id, str) or not session_id:
        return False
    return con.execute("SELECT 1 FROM spawn_requests WHERE session_id = ? AND decision = 'allow' AND member_id IS NOT NULL AND agent_id IS NULL LIMIT 1",
                       (session_id,)).fetchone() is not None


def session_mode(ctx, con, payload, env=None):
    """(mode, launch project row or None, claim row or None), once per hook call (design section 6.1).  The launch project
    is the project of CLAUDE_PROJECT_DIR, which stays at the launch directory after EnterWorktree (probe P5), else of the
    payload's cwd.  `outside`: it is in no active project and not the home, and a hook behaves as `spud` there, today's
    strict behaviour.  `spud`: launched in the home (SPD-097: the home is not a project, so the row is None), or the session
    holds a claim, or its project's sessions is `always`.  `plain` otherwise."""
    env = os.environ if env is None else env
    session = payload.get("session_id")
    claim = actors.claim_of(con, session) if isinstance(session, str) and session else None
    if con.execute("SELECT 1 FROM projects WHERE archived_at IS NULL AND sessions = 'claim' LIMIT 1").fetchone() is None:
        return "spud", None, claim  # no claim project: every session, wherever it is launched, is Spud's, and no path is mapped
    launch = env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not isinstance(launch, str) or not launch:
        return "outside", None, claim
    mapped = worktrees.project_of_path(ctx, con, launch)
    if mapped is None:
        return "outside", None, claim
    project = mapped[0]
    if worktrees.is_home(project):
        return "spud", None, claim
    if claim is not None or project["sessions"] == "always":
        return "spud", project, claim
    return "plain", project, None


def cut_note(count):
    """The closing line of a cut: how many of the body's lines it left out, and where the rest is (SPD-048)."""
    return "(%d more line%s cut to fit; run `spud board --brief` for the rest)" % (count, "" if count == 1 else "s")


def fit_bytes(head, body, cap):
    """head, then as many whole lines of body from the top as fit in cap bytes of UTF-8 with cut_note after them, counting
    the lines it cut.  Keeping one more line never makes the text shorter (the line costs at least its newline, and the
    note shrinks by at most one byte), so the first line that does not fit ends the cut.  A head that leaves no room even
    for the note is itself cut to cap bytes, on a character boundary."""
    text = head + ("\n" + body if body else "")
    if len(text.encode("utf-8")) <= cap:
        return text
    size = len(head.encode("utf-8"))
    lines = body.split("\n") if body else []
    kept, used = 0, 0
    while kept < len(lines) - 1:  # keeping every line is the whole text, which does not fit
        n = len(("\n" + lines[kept]).encode("utf-8"))
        if size + used + n + len(("\n" + cut_note(len(lines) - kept - 1)).encode("utf-8")) > cap:
            break
        used += n
        kept += 1
    tail = "\n" + cut_note(len(lines) - kept)
    if not lines or size + used + len(tail.encode("utf-8")) > cap:
        return head.encode("utf-8")[:cap].decode("utf-8", "ignore")
    return head + "".join("\n" + line for line in lines[:kept]) + tail


def home_session_context(con, payload, alerts=()):
    """The SessionStart context of a session in the home, or outside every project: `alerts` (the render watcher's line when
    it is down), the board's header, and the board, cut to SESSION_CONTEXT_CAP (SPD-048).  With no alert and a board that
    fits, the text is what the hook injected before SPD-048, byte for byte."""
    head = "\n".join(list(alerts) + ["Ledger board (`spud board --brief` at %s, source %s):" % (kernel.now(), payload.get("source"))])
    return fit_bytes(head, board_brief_text(con), SESSION_CONTEXT_CAP)


def plain_session_notice(ctx, project):
    root = worktrees.project_root(ctx, project)
    text = ""
    for shown in (root, os.path.basename(root.rstrip("/")) or root):
        text = "`%s` is Spud project `%s` (`%s-nnn` tickets). This session is not Spud; type /spud to make it Spud." % (shown, project["key"], project["ticket_prefix"])
        if len(text.encode("utf-8")) <= PLAIN_NOTICE_CAP:
            return text
    return text.encode("utf-8")[:PLAIN_NOTICE_CAP].decode("utf-8", "ignore")


def project_session_context(ctx, con, project, claim, payload, alerts=()):
    """A Spud session's SessionStart context in a project: the header naming the project, `alerts` under it (the render
    watcher's line when it is down), then the board, cut to SESSION_CONTEXT_CAP.  The alerts are in the head, which a cut
    never reaches."""
    root = worktrees.project_root(ctx, project)
    if claim is not None:
        head = ("Ledger: this session is Spud in project `%s` (%s), claimed %s; root %s; tickets %s-nnn, teams %s-nnn; landing %s."
                " This session is Spud: you are Spud here; re-read %s/CLAUDE.md now." % (project["key"], project["name"], kernel.fm_minute(claim["claimed_at"]), root,
                                                                    project["ticket_prefix"], project["team_prefix"], project["landing"], ctx.home))
    else:
        head = ("Ledger: `%s` is Spud project `%s` (sessions always; tickets %s-nnn). This session is Spud: run /spud now to load his instructions."
                % (root, project["key"], project["ticket_prefix"]))
    board = "Ledger board (`spud board --brief` at %s, source %s):\n%s" % (kernel.now(), payload.get("source"), board_brief_text(con))
    return fit_bytes("\n".join([head] + list(alerts)), board, SESSION_CONTEXT_CAP)


def record_claim(con, at, actor_label, session, project, cwd, ticket=None):
    """The one write of a claim: the session's row, unreleased, and its session.claimed event.  `how` in the event says who
    claimed: `command` (`session claim`, which /spud runs) or `hook`, the UserPromptSubmit hook, with the ticket whose key
    in the prompt made it claim (SPD-057)."""
    con.execute(
        "INSERT INTO sessions (session_id, project_id, claimed_at, released_at, cwd) VALUES (?, ?, ?, NULL, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET project_id = excluded.project_id, claimed_at = excluded.claimed_at, released_at = NULL, cwd = excluded.cwd",
        (session, project["id"], at, cwd),
    )
    data = {"session_id": session, "project": project["key"], "cwd": cwd, "how": "command" if ticket is None else "hook"}
    body = "session %s claimed in project %s" % (session, project["key"])
    if ticket is not None:
        data["ticket"] = ticket["key"]
        body += " by the UserPromptSubmit hook: the prompt names %s" % ticket["key"]
    ledgerdb.write_event(con, at, actor_label, "session.claimed", body, ticket_id=ticket["id"] if ticket is not None else None, data=data)


def claim_card(ctx, con, project, session, at, cap=CLAIM_CARD_CAP):
    root = worktrees.project_root(ctx, project)
    head = "\n".join([
        "Session %s is Spud in project %s (%s), claimed %s." % (session, project["key"], project["name"], kernel.fm_minute(at)),
        "home: %s" % ctx.home,
        "project: %s; tickets %s-nnn, teams %s-nnn; default branch %s; landing %s; sessions %s" % (
            root, project["ticket_prefix"], project["team_prefix"], project["default_branch"], project["landing"], project["sessions"]),
        "rule: this repository's CLAUDE.md and .claude/skills govern how deliverables are built, verified, committed and landed; Spud's laws"
        " govern delegation, the ledger, and who writes what.",
        "code: in the linked worktree the ticket is bound to (SPD-098); `member new` refuses from the main checkout, so enter a worktree first.",
        "board (%s):" % project["key"],
    ])
    rows = [dict(r) for r in con.execute("SELECT * FROM v_board WHERE project = ?", (project["key"],)).fetchall()]
    return fit_bytes(head, board_brief_text(con, rows), cap)


def cmd_session_claim(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        if actor.kind != "spud":
            raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "claiming a session is Spud's; %s may not (use --as spud)" % actor.ref(con))
        session = actors.planning_session(os.environ)
        if session is None:
            raise kernel.SpudError(kernel.EXIT_ERROR, "outside a Claude Code session there is nothing to claim (CLAUDE_CODE_SESSION_ID is not set)")
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
        if args.project:
            project = lookup.get_project(con, args.project)
        else:
            mapped = worktrees.cli_project_of(ctx, con, cwd) if cwd else None
            if mapped is None:
                raise kernel.SpudError(kernel.EXIT_ERROR, "not a registered project: %s is in no project's checkout (spud project list; --project <key>)" % cwd)
            project = mapped[0]
        if project["archived_at"]:
            raise kernel.SpudError(kernel.EXIT_ERROR, "project %s is archived" % project["key"])
        if worktrees.is_home(project):
            return kernel.Result({"session_id": session, "project": project["key"], "claimed": False}, "every session in the home is Spud; nothing to claim")
        at = kernel.now()
        with ledgerdb.write_txn(con):
            record_claim(con, at, actor.label, session, project, cwd)
            card = claim_card(ctx, con, project, session, at)
    finally:
        con.close()
    return kernel.Result({"session_id": session, "project": project["key"], "claimed": True, "claimed_at": at, "card": card}, card)


def cmd_session_release(ctx, args):
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        if actor.kind != "spud":
            raise kernel.SpudError(kernel.EXIT_OWNERSHIP, "releasing a session is Spud's; %s may not (use --as spud)" % actor.ref(con))
        session = actors.planning_session(os.environ)
        if session is None:
            raise kernel.SpudError(kernel.EXIT_ERROR, "outside a Claude Code session there is nothing to release (CLAUDE_CODE_SESSION_ID is not set)")
        at = kernel.now()
        with ledgerdb.write_txn(con):
            claim = actors.claim_of(con, session)
            if claim is None:
                return kernel.Result({"session_id": session, "released": False}, "session %s holds no claim; nothing to release" % session)
            project = con.execute("SELECT key FROM projects WHERE id = ?", (claim["project_id"],)).fetchone()
            con.execute("UPDATE sessions SET released_at = ? WHERE session_id = ?", (at, session))
            ledgerdb.write_event(con, at, actor.label, "session.released", "session %s released in project %s" % (session, project["key"]),
                        data={"session_id": session, "project": project["key"]})
    finally:
        con.close()
    return kernel.Result({"session_id": session, "released": True, "project": project["key"]}, "session %s released: it is not Spud in %s any more" % (session, project["key"]))


def cmd_session_show(ctx, args):
    """The ritual's first step outside the home (design section 6.3): the home, the working directory's project and
    checkout, the session and its mode."""
    session = actors.planning_session(os.environ)
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    con = ledgerdb.connect(ctx)
    try:
        mapped = worktrees.cli_project_of(ctx, con, cwd) if cwd else None
        try:
            mode, launch, claim = session_mode(ctx, con, {"session_id": session, "cwd": cwd})
        except hookio.HookError as e:
            raise kernel.SpudError(kernel.EXIT_ERROR, str(e))
        project = checkout = None
        if mapped is not None and worktrees.is_home(mapped[0]):  # SPD-097: launched in the home, which is not a project
            checkout = {"path": str(ctx.home), "kind": "home", "branch": None}
        elif mapped is not None:
            p, checkout_root, _rel = mapped
            project = {"key": p["key"], "root": worktrees.project_root(ctx, p), "ticket_prefix": p["ticket_prefix"], "team_prefix": p["team_prefix"],
                       "landing": p["landing"], "sessions": p["sessions"]}
            branch = homeconf.run_git(checkout_root, "symbolic-ref", "--quiet", "--short", "HEAD", timeout=10) if os.path.isdir(os.path.join(checkout_root, ".git")) or os.path.isfile(os.path.join(checkout_root, ".git")) else None
            checkout = {"path": checkout_root, "kind": "root" if worktrees.file_identity(checkout_root) == worktrees.file_identity(project["root"]) else "worktree",
                        "branch": branch.stdout.strip() if branch is not None and branch.returncode == 0 else None}
    finally:
        con.close()
    data = {"home": str(ctx.home), "cwd": cwd, "project": project, "checkout": checkout, "session_id": session, "mode": mode,
            "launch_project": launch["key"] if launch is not None else None, "claimed_at": claim["claimed_at"] if claim is not None else None}
    lines = ["home      %s" % ctx.home]
    if project:
        lines.append("project   %s: %s (%s-nnn tickets, %s-nnn teams; landing %s, sessions %s)" % (
            project["key"], project["root"], project["ticket_prefix"], project["team_prefix"], project["landing"], project["sessions"]))
        lines.append("checkout  %s (%s%s)" % (checkout["path"], checkout["kind"], ", branch %s" % checkout["branch"] if checkout["branch"] else ""))
    elif checkout:
        lines.append("project   none: %s is in Spud's home, not a project" % cwd)
        lines.append("checkout  %s (home)" % checkout["path"])
    else:
        lines.append("project   none: %s is in no registered project's checkout" % cwd)
    lines.append("session   %s" % (session or "none (outside a Claude Code session)"))
    lines.append("mode      %s" % {"spud": "spud" + (" (claimed %s)" % kernel.fm_minute(claim["claimed_at"]) if claim is not None else ""),
                                   "plain": "plain: this session is not Spud; type /spud to make it Spud",
                                   "outside": "outside every project (behaves as Spud)"}[mode])
    return kernel.Result(data, "\n".join(lines))
