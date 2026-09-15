"""core/homeconf: Where the ledger is: home resolution, Ctx and config, git subprocess helpers, user-scope directories.  Moved from bin/spud_ledger.py (SPD-065)."""

import json
import os
import re
from pathlib import Path

from . import kernel, lazy
from ..render import prices


# ----------------------------------------------------------------------------
# Home and config
# ----------------------------------------------------------------------------


def resolve_home(env, script_path):
    """SPUD_HOME; else the git common dir of this script's real path; else the
    ~/.config/spud/home pointer.  Returns (path, how)."""
    if env.get("SPUD_HOME"):
        return Path(env["SPUD_HOME"]).expanduser().resolve(), "SPUD_HOME"
    try:
        proc = lazy.subprocess.run(
            ["git", "-C", str(Path(script_path).resolve().parent), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
        common = proc.stdout.strip()
        if common:
            return Path(common).resolve().parent, "git common dir"
    except (OSError, lazy.subprocess.CalledProcessError):
        pass
    pointer = spud_config_dir(env) / "home"
    if pointer.is_file():
        target = pointer.read_text(encoding="utf-8").strip()
        if target:
            return Path(target).expanduser().resolve(), "~/.config/spud/home"
    raise kernel.SpudError(kernel.EXIT_ERROR, "cannot find Spud's home: set SPUD_HOME to the main checkout")


class Ctx:
    """Everything a command needs: home, config, database path, output mode."""

    def __init__(self, home, resolved_by, json_mode):
        self.home = home
        self.resolved_by = resolved_by
        self.json = json_mode
        self.db_path = home / ".spud" / "ledger.db"
        self._config = None
        self.hook_project = None  # `spud hook <event> --project <key>`: the failure policy of a project's hook line (SPD-014)

    @property
    def config_path(self):
        return self.home / "spud.config.json"

    @property
    def config(self):
        if self._config is None:
            if not self.config_path.is_file():
                raise kernel.SpudError(kernel.EXIT_ERROR, "no spud.config.json in %s" % self.home)
            with open(self.config_path, encoding="utf-8") as f:
                try:
                    self._config = json.load(f)
                except json.JSONDecodeError as e:
                    raise kernel.SpudError(kernel.EXIT_ERROR, "spud.config.json is not valid JSON: %s" % e)
        return self._config

    @property
    def limits(self):
        limits = self.config.get("limits", {})
        out = {}
        for key in ("max_depth", "root_fan_out", "child_fan_out", "max_concurrent_total"):
            value = limits.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise kernel.SpudError(kernel.EXIT_ERROR, "spud.config.json limits.%s must be a positive integer" % key)
            out[key] = value
        return out

    def persona_tier(self, persona):
        personas = self.config.get("personas", {})
        if persona in personas:
            return personas[persona].get("tier")
        return None

    def personas(self):
        return list(self.config.get("personas", {}).keys()) + ["contractor"]

    def id_pad(self):
        pad = self.config.get("naming", {}).get("id_pad", 2)
        return pad if isinstance(pad, int) and pad > 0 else 2

    @property
    def pricing(self):
        """The price table spud.config.json gives (`pricing`, SPD-013), or None: what cost renders from."""
        return prices.price_table(self.config)[0]


def config_problems(config):
    """Config sanity for doctor: unique pool names, integer limits, known tiers, a readable price table."""
    problems = []
    pool = config.get("naming", {}).get("pool")
    if not isinstance(pool, list) or not pool:
        problems.append("naming.pool is missing or empty")
    else:
        seen = set()
        for name in pool:
            if name in seen:
                problems.append("naming.pool repeats %r" % name)
            seen.add(name)
    limits = config.get("limits", {})
    for key in ("max_depth", "root_fan_out", "child_fan_out", "max_concurrent_total"):
        value = limits.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            problems.append("limits.%s is not a positive integer (%r)" % (key, value))
    for persona, spec in config.get("personas", {}).items():
        if not isinstance(spec, dict) or spec.get("tier") not in kernel.MODELS:
            problems.append("personas.%s.tier is not one of %s" % (persona, ", ".join(kernel.MODELS)))
    for key in ("tickets", "teams"):
        prefix = config.get(key, {}).get("prefix")
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix):
            problems.append("%s.prefix must be upper-case letters and digits (%r)" % (key, prefix))
    problems.extend(prices.price_table(config)[1])
    return problems


def git_env():
    """The environment for a git call the CLI or a hook makes: without the variables that could point it away from the
    repository it names (GIT_REDIRECTS)."""
    return {k: v for k, v in os.environ.items() if k not in GIT_REDIRECTS}


def run_git(root, *args, input=None, timeout=60):
    """`git -C <root> <args>` with git_env(): the CompletedProcess (text), or SpudError when git cannot run at all."""
    try:
        return lazy.subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace", env=git_env(),
                              input=input, timeout=timeout)
    except (OSError, lazy.subprocess.TimeoutExpired) as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "cannot run git in %s: %s" % (root, e))


def git_remote_url(root):
    """`git remote get-url origin` for a repository root, or None (no .git, no origin, git missing)."""
    if not os.path.lexists(os.path.join(str(root), ".git")):
        return None
    try:
        proc = run_git(root, "remote", "get-url", "origin", timeout=10)
    except kernel.SpudError:
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def user_claude_dir():
    """~/.claude, where install puts the user-scope spudagent and /spud skill; SPUD_USER_CLAUDE_DIR overrides it, for tests."""
    return Path(os.path.abspath(os.path.expanduser(os.environ.get("SPUD_USER_CLAUDE_DIR") or "~/.claude")))


def spud_config_dir(env=None):
    """~/.config/spud, which holds the home pointer; SPUD_CONFIG_DIR overrides it, for tests."""
    env = os.environ if env is None else env
    return Path(os.path.abspath(os.path.expanduser(env.get("SPUD_CONFIG_DIR") or "~/.config/spud")))


# -- worktrees: every checkout of the home repository, wherever `git worktree add` put it (SPD-016) -----------------

GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")  # what could point a git call away from the home
