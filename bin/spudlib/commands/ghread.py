"""commands/ghread: The one read of a pull request, `gh pr view --json state,mergedAt,url,number`, and the program that answers it (SPD-077)."""

import json
import os

from ..core import lazy

# SPD-077.  This module is the whole of the ledger's contact with GitHub, and the seam the suite patches: prcmds calls
# `ghread.pr_view(...)`, so `mock.patch("spudlib.commands.ghread.pr_view", ...)` reaches the call site.  Nothing on the
# hook path imports it, and nothing here writes the ledger, runs git or merges anything: it reads one pull request and
# says what GitHub said, or why it could not ask.
#
# SPUD_GH names the program (default `gh`).  The literal `off` turns every read off: helpers.Home sets it, so no suite run
# can reach the network whatever a test forgets, and a test that wants a read points it at a fake gh of its own the way
# LaunchdMixin points SPUD_LAUNCHCTL at a fake launchctl.  `off` is not a failure: the reconciler reports it as off and
# stores no failed check, because a read that was never attempted says nothing about the pull request.

OFF = "off"
FIELDS = "state,mergedAt,url,number"
CALL_TIMEOUT = 10  # seconds for one `gh pr view`; the reconciler's own budget caps the run
STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
ERROR_CAP = 200  # characters of gh's own complaint kept as the stored failed check


def gh_program(env=None):
    """The program that answers for GitHub: SPUD_GH, else `gh`."""
    env = os.environ if env is None else env
    return (env.get("SPUD_GH") or "").strip() or "gh"


def enabled(env=None):
    """False when SPUD_GH is `off`: no read is attempted anywhere."""
    return gh_program(env) != OFF


def first_line(text):
    """The first line of gh's complaint, capped, for the stored failed check."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:ERROR_CAP]
    return ""


def pr_view(url, cwd=None, timeout=CALL_TIMEOUT):
    """({state, merged_at, url, number}, None) for one pull request, or (None, why) when the read failed: no gh, no auth,
    no network, a rate limit, a URL GitHub does not know.  Never raises and never writes anything -- a failed read is a
    stored failed check, and the run goes on."""
    program = gh_program()
    if program == OFF:
        return None, "the gh reader is off (SPUD_GH=off)"
    try:
        proc = lazy.subprocess.run([program, "pr", "view", url, "--json", FIELDS], capture_output=True, text=True,
                                   errors="replace", cwd=cwd if cwd and os.path.isdir(cwd) else None, timeout=timeout)
    except (OSError, lazy.subprocess.TimeoutExpired) as e:
        return None, "cannot run %s: %s" % (program, e)
    if proc.returncode != 0:
        return None, first_line(proc.stderr) or first_line(proc.stdout) or "%s exited %d" % (program, proc.returncode)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return None, "%s printed no JSON: %s" % (program, e)
    if not isinstance(payload, dict):
        return None, "%s printed %s, not a pull request object" % (program, type(payload).__name__)
    state = STATES.get(str(payload.get("state") or "").upper())
    if state is None:
        return None, "%s reported the state %r, which is not OPEN, MERGED or CLOSED" % (program, payload.get("state"))
    number = payload.get("number")
    return {"state": state, "merged_at": payload.get("mergedAt") or None, "url": payload.get("url") or url,
            "number": number if isinstance(number, int) and not isinstance(number, bool) else None}, None
