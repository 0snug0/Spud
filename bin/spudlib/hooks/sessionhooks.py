"""hooks/sessionhooks: SessionStart and UserPromptSubmit.  Moved from bin/spud_ledger.py (SPD-065)."""

import re

from . import hookio
from ..core import kernel
from ..projects import sessions
from ..state import actors, ledgerdb, ops


def hook_session_start(ctx, payload):
    """The board for a Spud session in the home (and outside every project); in another project (SPD-014) the one-line
    notice for a session that is not Spud, and for a Spud one a header naming the project with the board, at most 2 KB
    in all, since a larger additionalContext reaches the model only as a preview (the design's probe, P2)."""
    if not ctx.db_path.is_file():
        return hookio.SILENT
    con = ledgerdb.connect(ctx)
    try:
        mode, project, claim = sessions.session_mode(ctx, con, payload)
        if mode == "plain":
            context = sessions.plain_session_notice(ctx, project)
        elif project is not None and project["id"] != 1:
            context = sessions.project_session_context(ctx, con, project, claim, payload)
        else:
            context = "Ledger board (`spud board --brief` at %s, source %s):\n%s" % (kernel.now(), payload.get("source"), sessions.board_brief_text(con))
    finally:
        con.close()
    return hookio.HookOutput({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}})


# The UserPromptSubmit hook (SPD-057).  Eric starts a session in a claim project with "Work on BAD-006" and no /spud; before
# SPD-057 it stayed plain, and the work happened outside the ledger.  Now a prompt that names a ticket of the session's
# launch project claims the session the way `session claim` does, and hands the model the /spud skill's steps.
PROMPT_KEY_SHAPE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")  # a cheap test before the database is opened: most prompts name no key
SPUD_COMMAND = re.compile(r"\A\s*/spud(?:\s|\Z)|<command-name>\s*/spud\s*</command-name>")  # typed, or as the transcript expands it
PROMPT_CLAIM_CAP = sessions.SESSION_CONTEXT_CAP  # the same inline limit as SessionStart's context (the design's probe P2; SPD-048)


def prompt_is_spud_command(prompt):
    """/spud, with or without arguments: the skill claims the session itself, so the hook stays out of its way."""
    return SPUD_COMMAND.search(prompt) is not None


def prompt_ticket_keys(prompt, prefix):
    """The keys with this ticket prefix a prompt names, in order, each once: word-bounded, case-sensitive as the tickets
    table's keys are, and spelled exactly as the ledger spells a key (ticket_key).  So BAD-006 and BAD-1000, never BAD-23
    (the older tracker's numbers BadTakes cites), BAD-0060, BAD-06, bad-006 or XBAD-006."""
    out = []
    for m in re.finditer(r"\b%s-(\d{1,12})\b" % re.escape(prefix), prompt):
        key = m.group(0)
        if key == ops.ticket_key(prefix, int(m.group(1))) and key not in out:
            out.append(key)
    return out


def prompted_ticket(con, project, prompt):
    """The first ticket of the project the prompt names that is not declined (a done one counts: Eric may be reopening it)."""
    for key in prompt_ticket_keys(prompt, project["ticket_prefix"]):
        row = con.execute("SELECT * FROM tickets WHERE project_id = ? AND key = ? AND status != 'declined'", (project["id"], key)).fetchone()
        if row is not None:
            return row
    return None


def released_by_command(con, session):
    """Whether `session release` ever released this session: its session.released event.  A release sticks, so the hook never
    claims such a session again; `project uninstall` and `project remove` release claims without that event."""
    return con.execute("SELECT 1 FROM events WHERE kind = 'session.released' AND json_extract(data, '$.session_id') = ? LIMIT 1",
                       (session,)).fetchone() is not None


def prompt_claim_context(ctx, con, project, ticket, session, at):
    head = ("Ledger: the prompt names %s, a ticket of Spud project `%s`, so the UserPromptSubmit hook claimed this session: it is Spud now."
            " Before anything else:\n" % (ticket["key"], project["key"]))
    head += sessions.skill_steps(ctx.home, sessions.HOOK_CLAIM.format(home=ctx.home), "%s - <what this session does>" % ticket["key"])
    card = sessions.claim_card(ctx, con, project, session, at, cap=max(PROMPT_CLAIM_CAP - len(head.encode("utf-8")) - 1, 0))
    return sessions.fit_bytes(head, card, PROMPT_CLAIM_CAP)  # a blank line between the steps and the card


def hook_user_prompt_submit(ctx, payload):
    """Claim a plain session (an unclaimed one in a claim project) whose prompt names one of its launch project's tickets.
    Silent for everything else: a subagent's call, a session that is Spud already (the home, an always project, a claim), a
    session `session release` released, /spud itself, and a prompt that names no such ticket."""
    prompt, session = payload.get("prompt"), payload.get("session_id")
    if payload.get("agent_id") or not isinstance(prompt, str) or not isinstance(session, str) or not session or not ctx.db_path.is_file():
        return hookio.SILENT
    if not PROMPT_KEY_SHAPE.search(prompt) or prompt_is_spud_command(prompt):
        return hookio.SILENT
    con = ledgerdb.connect(ctx)
    try:
        mode, project, _claim = sessions.session_mode(ctx, con, payload)
        if mode != "plain" or released_by_command(con, session):
            return hookio.SILENT
        ticket = prompted_ticket(con, project, prompt)
        if ticket is None:
            return hookio.SILENT
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else None
        at = kernel.now()
        with ledgerdb.write_txn(con):
            if actors.claim_of(con, session) is not None:
                return hookio.SILENT  # claimed meanwhile (a /spud in the same breath)
            sessions.record_claim(con, at, "hook:UserPromptSubmit", session, project, cwd, ticket=ticket)
            context = prompt_claim_context(ctx, con, project, ticket, session, at)
    finally:
        con.close()
    return hookio.HookOutput({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}})
