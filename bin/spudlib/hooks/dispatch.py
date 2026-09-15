"""hooks/dispatch: HOOK_HANDLERS and cmd_hook: a hook run imports its own event's handler module alone.  Moved from bin/spud_ledger.py (SPD-065)."""

import contextlib
import importlib
import json
import sys

from . import hookio
from ..core import kernel


HOOK_HANDLERS = {  # event -> (its module in spudlib.hooks, its handler): a hook run imports its own handler's modules alone (SPD-065)
    "PreToolUse": ("pretool", "hook_pre_tool_use"),
    "PostToolUse": ("recording", "hook_post_tool_use"),
    "SubagentStart": ("recording", "hook_subagent_start"),
    "SubagentStop": ("subagent_stop", "hook_subagent_stop"),
    "SessionStart": ("sessionhooks", "hook_session_start"),
    "Stop": ("stophook", "hook_stop"),
    "UserPromptSubmit": ("sessionhooks", "hook_user_prompt_submit"),
}


def cmd_hook(ctx, args):
    """The harness's entry point.  Exit codes are 0 or 2 only, whatever happens."""
    event = args.event
    ctx.hook_project = getattr(args, "project", None)
    raw = sys.stdin.read()
    payload = None
    try:
        try:
            payload = json.loads(raw) if raw.strip() else None
        except ValueError as e:
            raise hookio.HookError("payload is not JSON: %s" % e)
        if not isinstance(payload, dict):
            raise hookio.HookError("payload is not a JSON object")
        name = payload.get("hook_event_name")
        if name is not None and name != event:
            raise hookio.HookError("hook_event_name %r does not match `spud hook %s`" % (name, event))
        module, handler = HOOK_HANDLERS[event]
        out = getattr(importlib.import_module("." + module, __package__), handler)(ctx, payload)
    except Exception as e:
        fields = payload if isinstance(payload, dict) else {}
        record = {"at": kernel.now(), "event": event, "tool_name": fields.get("tool_name"), "tool_use_id": fields.get("tool_use_id"),
                  "agent_id": fields.get("agent_id"), "session_id": fields.get("session_id"), "error": "%s: %s" % (type(e).__name__, e)}
        with contextlib.suppress(Exception):
            hookio.spool_write(ctx, record)
        enforcing = event == "PreToolUse" or (event == "Stop" and not fields.get("stop_hook_active"))
        if enforcing and ctx.hook_project and not (isinstance(fields.get("agent_id"), str) and fields.get("agent_id")):
            # A project's hook line (design section 6.4): a failure with no agent_id to hold fails open, so a ledger outage
            # never stalls a session in that repository; a subagent's call still fails closed.
            return kernel.Result(None, raw="", exit_code=kernel.EXIT_OK, stderr="spud hook %s --project %s: %s (failing open: no agent_id)" % (event, ctx.hook_project, e))
        if enforcing:
            return kernel.Result(None, raw="", exit_code=2, stderr="spud hook %s: %s (failing closed)" % (event, e))
        return kernel.Result(None, raw="", exit_code=kernel.EXIT_OK)
    with contextlib.suppress(Exception):
        hookio.spool_drain(ctx)
    return kernel.Result(None, raw=json.dumps(out.obj) if out.obj else "", exit_code=out.exit_code)
