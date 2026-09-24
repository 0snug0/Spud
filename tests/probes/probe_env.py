"""The shape and the isolation every probe that builds a scratch SPUD_HOME runs under (SPD-101, SPD-244), shared, as
tests/helpers.Home is the suite's.

Not a probe: the probes that build a home import it -- `python3.14 -I -S` puts no script directory on sys.path, so each
sets sys.dont_write_bytecode and puts its own directory first before `import probe_env`.  It imports nothing of the
suite's (tests/helpers.py), and so repeats the few lines of it that it needs.

The shape (SPD-244, the probes' half of SPD-233): a scratch directory, `root`, holding the home and, beside it, the tool,
`<root>/tool/Spud` (build_tool): the main checkout of a git repository with one commit on main, holding a copy of a
checkout's bin/ and a link to its share/, neither inside the other -- what a real home's tool is, and the only shape the
CLI accepts.  Project 1 is that tool, registered by `spud init`'s own step 3 (init_args: `--project-root`), never by SQL.

The isolation: a probe asks `isolated_env` for the environment its `spud` runs take, which sets what helpers.Home sets for
a test, for the same reasons:

  SPUD_HOME               the scratch home
  SPUD_TOOL_DIR           the tool beside it (build_tool): what the hook lines, the allow rules and the /spud skill
                          name, and where init reads share/ from
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
import shutil
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
TOOL_DIR = "Spud"  # helpers.TOOL_DIR: the tool checkout's directory name, from which init derives project 1's name
PROJECT_KEY = "spud"  # project 1's key, as helpers.Home.init registers it
# helpers.isolated_git_env's identity: the scratch tool's one commit reads none of this Mac's git configuration.
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Spud probe", "GIT_AUTHOR_EMAIL": "probe@example.invalid",
                "GIT_COMMITTER_NAME": "Spud probe", "GIT_COMMITTER_EMAIL": "probe@example.invalid"}


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


def isolated_env(home, tool, scratch=None, base=None):
    """The environment for a probe's `spud` runs against the scratch `home` and the `tool` beside it (build_tool), which is
    SPUD_TOOL_DIR: `base` (default os.environ) without the session's variables, with every override the module docstring
    lists.  `scratch` holds the user-scope directories, the LaunchAgents directory and the refusing launchctl (default:
    `home` itself, as helpers.Home does).  Checked by assert_isolated before it is returned."""
    home = Path(home)
    scratch = home if scratch is None else Path(scratch)
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in SESSION_VARIABLES}
    env.update(
        SPUD_HOME=str(home),
        SPUD_TOOL_DIR=str(tool),
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


def git_env(base=None):
    """helpers.isolated_git_env: `base` (default os.environ) with git reading none of this Mac's configuration -- no global
    or system config, no global excludes file, no signing -- and a fixed identity."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="2",
               GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0="/dev/null",
               GIT_CONFIG_KEY_1="commit.gpgsign", GIT_CONFIG_VALUE_1="false", **GIT_IDENTITY)
    return env


def git(repo, *args):
    """git in `repo` under git_env, its stdout; stops the probe, naming the command, when git fails."""
    proc = subprocess.run(["git", "-C", str(repo), *[str(a) for a in args]], capture_output=True, text=True, env=git_env())
    if proc.returncode != 0:
        sys.exit("git %s in %s exited %d: %s" % (" ".join(str(a) for a in args), repo, proc.returncode, proc.stderr))
    return proc.stdout


def tool_path(root):
    """Where build_tool puts the tool of the scratch directory `root`: `<root>/tool/Spud`."""
    return Path(root) / "tool" / TOOL_DIR


def build_tool(root, checkout=None):
    """The tool beside the scratch home, as helpers.build_tool builds the suite's (SPD-233): `<root>/tool/Spud`, the main
    checkout of a git repository with one commit on main.  Its bin/ is a copy of `checkout`'s (default this checkout's),
    without bytecode, so the launcher the hook lines and the allow rules name runs; its share/ is a link to `checkout`'s,
    which init and `project install` render from, since no probe writes a shipped file.  `.claude/settings.local.json`,
    which init's step 7 writes into it, is ignored as the real repository ignores it.  Returns the tool's path."""
    checkout = Path(REPO if checkout is None else checkout)
    tool = tool_path(root)
    tool.mkdir(parents=True)
    shutil.copytree(checkout / "bin", tool / "bin", ignore=shutil.ignore_patterns("__pycache__"))
    os.symlink(str(checkout / "share"), str(tool / "share"), target_is_directory=True)
    (tool / ".gitignore").write_text(".claude/settings.local.json\n", encoding="utf-8")
    git(tool, "init", "-q", "-b", "main")
    git(tool, "add", "-A")
    git(tool, "commit", "-q", "-m", "tool")
    return tool


def init_args(tool):
    """A probe's `spud init`: the `tool` registered as project 1 by init's own step 3, key `spud`, its name the directory's
    and its prefixes the config's, as helpers.Home.init registers it; and `--no-schedule`, since step 8 would reach
    launchctl, and the scratch's is the refusing stub above."""
    return ["init", "--no-schedule", "--project-root", str(tool), "--project-key", PROJECT_KEY]


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
