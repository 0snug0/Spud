"""The isolation every probe that builds a scratch SPUD_HOME runs under (SPD-101), shared, as tests/helpers.Home is the suite's.

Not a probe: the probes that build a home import it -- `python3.14 -I -S` puts no script directory on sys.path, so each
sets sys.dont_write_bytecode and puts its own directory first before `import probe_env` -- and ask `isolated_env` for the
environment their `spud` runs take.  What it sets is what helpers.Home sets for a test, for the same reasons:

  SPUD_HOME               the scratch home
  SPUD_TOOL_DIR           the tool: the scratch home, which ships share/ (link_share), unless the probe names another
  SPUD_CONFIG_DIR         the home pointer init writes, under the scratch -- never ~/.config/spud
  SPUD_USER_CLAUDE_DIR    what `project install` writes, under the scratch -- never ~/.claude
  SPUD_LAUNCH_AGENTS_DIR  the two plists, under the scratch -- never ~/Library/LaunchAgents, which doctor and
                          `board --brief` also read, so the machine's own watcher never answers for a scratch home
  SPUD_LAUNCHCTL          a launchctl that refuses and runs nothing: SPUD_LAUNCH_AGENTS_DIR moves the plist, never the
                          gui/<uid> job under local.spud.backup and local.spud.render that a bootout removes, and
                          those are this Mac's own.  A probe that wants step 8 gives its own fake (headless.py)
  SPUD_GH, SPUD_VAULT_DOWNLOADS   off: no probe's `spud` reaches GitHub unless it asks to

What happened without it, on 2026-09-22: hook_timing.py's `spud init` ran step 8 against ~/Library/LaunchAgents and
/bin/launchctl, replaced the real render watcher with one pointing at a scratch home that was removed minutes later, and the
vault went stale.  commands/schedule refuses that now for any home but the machine's own; this is the probes' own half.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG_MARKS = REPO / "tests" / "fixtures" / "config_marks.json"
NAME_POOL = REPO / "tests" / "fixtures" / "name_pool.json"
# The defaults commands/schedule and core/launchagents use when nothing moves them: this Mac's own.
MACHINE_AGENTS_DIR = "~/Library/LaunchAgents"
MACHINE_LAUNCHCTL = "/bin/launchctl"
# What a Claude Code session running a probe would otherwise hand the probe's own `spud` runs.
SESSION_VARIABLES = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")
REFUSAL = "spud probe guard: refusing to run launchctl"
REFUSAL_EXIT = 70  # helpers.GUARD_LAUNCHCTL_EXIT's value, and for its reason: not launchctl's own 3, 5 or 113


def refusing_launchctl(directory):
    """A launchctl that says what it was asked and runs nothing, written as `directory`/launchctl: the program."""
    path = Path(directory) / "launchctl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'echo "%s $*" >&2\n'
        "echo 'SPUD_LAUNCHCTL names tests/probes/probe_env.py'\"'\"'s refusing stub; a probe runs `spud init --no-schedule`, or gives a fake launchctl of its own.' >&2\n"
        "exit %d\n" % (REFUSAL, REFUSAL_EXIT), encoding="utf-8")
    path.chmod(0o755)
    return path


def isolated_env(home, scratch=None, tool=None, base=None):
    """The environment for a probe's `spud` runs against the scratch `home`: `base` (default os.environ) without the
    session's variables, with every override the module docstring lists.  `scratch` holds the user-scope directories,
    the LaunchAgents directory and the refusing launchctl (default: `home` itself, as helpers.Home does); `tool` is
    SPUD_TOOL_DIR (default: `home`).  Checked by assert_isolated before it is returned."""
    home = Path(home)
    scratch = home if scratch is None else Path(scratch)
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in SESSION_VARIABLES}
    env.update(
        SPUD_HOME=str(home),
        SPUD_TOOL_DIR=str(home if tool is None else tool),
        SPUD_CONFIG_DIR=str(scratch / ".user-config"),
        SPUD_USER_CLAUDE_DIR=str(scratch / ".user-claude"),
        SPUD_LAUNCH_AGENTS_DIR=str(scratch / "LaunchAgents"),
        SPUD_LAUNCHCTL=str(refusing_launchctl(scratch / ".probe-launchctl")),
        SPUD_GH="off",
        SPUD_VAULT_DOWNLOADS="off",
    )
    assert_isolated(env)
    return env


def machine_reach(env):
    """What of this Mac's own a `spud` run under `env` could write or unload: the default LaunchAgents directory, the
    real launchctl, the real home pointer and ~/.claude.  Empty when the environment is isolated.  Resolved paths, so a
    symlink or a second spelling of one of them counts as it."""
    def same(value, default):
        return os.path.realpath(os.path.expanduser(value or default)) == os.path.realpath(os.path.expanduser(default))
    reach = []
    if same(env.get("SPUD_LAUNCH_AGENTS_DIR"), MACHINE_AGENTS_DIR):
        reach.append("SPUD_LAUNCH_AGENTS_DIR is %s" % MACHINE_AGENTS_DIR)
    if same(env.get("SPUD_LAUNCHCTL"), MACHINE_LAUNCHCTL):
        reach.append("SPUD_LAUNCHCTL is %s" % MACHINE_LAUNCHCTL)
    if same(env.get("SPUD_CONFIG_DIR"), "~/.config/spud"):
        reach.append("SPUD_CONFIG_DIR is ~/.config/spud")
    if same(env.get("SPUD_USER_CLAUDE_DIR"), "~/.claude"):
        reach.append("SPUD_USER_CLAUDE_DIR is ~/.claude")
    return reach


def assert_isolated(env):
    """Stop the probe, before it runs anything, when `env` reaches anything of this Mac's own."""
    reach = machine_reach(env)
    if reach:
        sys.exit("probe not isolated, nothing run: %s (tests/probes/probe_env.py)" % "; ".join(reach))
    return env


def main_checkout(launcher):
    """The main checkout of the repository `launcher` (`<checkout>/bin/spud`) belongs to, or that checkout itself when git
    names no repository there.  What a probe comparing two launchers roots project 1 at, so main's launcher and a
    worktree's read the same row: rooted at each launcher's own checkout, `project list` pads a longer root and doctor
    calls a worktree's root `not the main checkout`, and no refactor could ever compare identical (SPD-101)."""
    checkout = os.path.dirname(os.path.dirname(os.path.abspath(launcher)))
    proc = subprocess.run(["git", "-C", checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
    common = proc.stdout.strip() if proc.returncode == 0 else ""
    return os.path.dirname(common) if common else checkout


def link_share(home, share=None):
    """The scratch home plays the tool, so it ships share/ (SPW-001): a symlink to `share` (default this checkout's)."""
    os.symlink(str(REPO / "share" if share is None else share), str(Path(home) / "share"), target_is_directory=True)


def write_config(home, template=None, name_pool=False):
    """Render the shipped config template (`template`, default this checkout's share/spud.config.json) with the suite's
    marks into `home`/spud.config.json, and with the suite's own naming.pool when `name_pool` (SPD-157); returns it parsed."""
    text = Path(REPO / "share" / "spud.config.json" if template is None else template).read_text(encoding="utf-8")
    for mark, value in json.loads(CONFIG_MARKS.read_text(encoding="utf-8")).items():
        text = text.replace(mark, value)
    config = json.loads(text)
    if name_pool:
        config["naming"]["pool"] = json.loads(NAME_POOL.read_text(encoding="utf-8"))
        text = json.dumps(config, indent=2) + "\n"
    (Path(home) / "spud.config.json").write_text(text, encoding="utf-8")
    return config
