"""spud hook <event> across repositories (SPD-014): the ledger's hooks in a project other than Spud's home.

Written against docs/design/2026-09-14-cross-repository-projects.md: section 3.2 (who may write where), 6.1 (a
session's mode), 6.2 (each hook), 6.4 (the failure policy of a project's hook lines) and the test_hooks_projects.py
bullets of 7.3.  Eric's answers of 2026-09-14 replace the design's assumptions: `project add` defaults to
`--sessions claim` (the home stays `always`), and the stand-in for BadTakes is the key `badtakes` with the ticket
prefix BAD and the team prefix BADS (tickets BAD-001, teams BADS-001).

Every run is against a scratch SPUD_HOME (tests/helpers.py).  The other repository is a scratch git repository built
in setUp beside it, with one linked worktree elsewhere, the way WorktreeElsewhereTest builds the home's.  A hook runs
the way a project's installed hook line runs it: SPUD_HOME in the environment, CLAUDE_PROJECT_DIR the session's launch
directory (it stays there after EnterWorktree, probe P5), CLAUDE_CODE_SESSION_ID equal to the payload's session_id,
the process working directory the payload's cwd, and `--project badtakes` on the command line when the session was
launched in that project (a home session's line carries none).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

from helpers import EXIT_ERROR, SPUD, HookResult
from test_hooks import AGENT_A, AGENT_B, AGENT_C, AGENT_D, SESSION, TRANSCRIPT, HookCase, quote_split
from test_hooks import GIT_DIR_WORDING, GIT_HOOK_WORDING, GIT_NESTED_WORDING, GIT_SCOPE_WORDING, SPUD_PLANTED_WORDING, plant_git_dir

KEY = "badtakes"
TICKET_PREFIX = "BAD"
TEAM_PREFIX = "BADS"
SESSION_CLAIMED = "b7c1d2e3-4f5a-4b6c-8d7e-9f0a1b2c3d4e"  # a session launched in badtakes that ran /spud (session claim)
SESSION_PLAIN = "c8d2e3f4-5a6b-4c7d-9e8f-0a1b2c3d4e5f"  # a session launched in badtakes that did not: Eric's own

# The refusal needles the design and the lead's brief fix (substrings of the reason).
LAW_1 = "Law 1"  # a Spud session writing a deliverable: in another project every path is one
LAW_5 = "Law 5"  # a member outside its globs; the home's generated ledger/** and reports/** for everyone
NOT_SPUD = "not Spud"  # a plain session, or an unbound agent_id of one, writing in Spud's home
NOT_BOUND = "not bound"  # an unbound agent_id in a Spud session writing a project path
STATE_WORDING = "ledger database"  # the state directory .spud/ at any project root, for everyone
BOUND_WORKTREE = "SPD-098"  # a member of a bound ticket writing its project's path outside the worktree the ticket is bound to
GIT_DIR = GIT_DIR_WORDING  # SPD-066: a path with a .git component, for every agent_id in a Spud session
SPUD_PLANTED = SPUD_PLANTED_WORDING  # SPD-123: Spud's own git call reaching what a member may have planted in a known checkout

# The columns of the section 3.2 table, in its order.
SPUD_COL, PLAIN_COL, MEMBER_COL, UNBOUND_SPUD_COL, UNBOUND_PLAIN_COL = range(5)

# A session as the hooks see it: the launch directory (CLAUDE_PROJECT_DIR, None when the variable is absent), the
# session_id, the payload's cwd, and the `--project` key its hook line carries (None: a home line).
Session = namedtuple("Session", "label launch session cwd project")


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return env


def ago(minutes):
    return (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


class ProjectHookCase(HookCase):
    """A scratch home with SPD-001 (HookCase), a scratch repository registered as project badtakes with a linked worktree
    elsewhere, its ticket BAD-001, and three sessions: one launched in the home, one launched in badtakes that claimed,
    and one launched in badtakes that did not."""

    def setUp(self):
        super().setUp()
        # A suite run from a Claude Code session inherits its launch directory: no hook here sees it unless a test sets it.
        self.home.env.pop("CLAUDE_PROJECT_DIR", None)
        self.outside = self.scratch_dir("spud-outside-")
        self.bad = self.make_repo("badtakes-")
        self.bad_wt = self.add_worktree(self.bad, "bad-001-thing")
        self.register(self.bad, KEY, TICKET_PREFIX, TEAM_PREFIX, "pr")
        self.bad_ticket = self.cli_json("ticket", "new", "--project", KEY, "--title", "A BadTakes ticket", "--status", "active", actor="spud")["ticket"]
        self.assertEqual((self.bad_ticket["key"], self.bad_ticket["team_key"]), ("BAD-001", "BADS-001"), self.bad_ticket)
        self.HOME = Session("home", self.home.path, SESSION, self.home.path, None)
        self.CLAIMED = Session("claimed", self.bad, SESSION_CLAIMED, self.bad, KEY)
        self.PLAIN = Session("plain", self.bad, SESSION_PLAIN, self.bad, KEY)
        self.claim(self.CLAIMED)

    # -- scratch repositories -------------------------------------------------------
    def scratch_dir(self, prefix):
        path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()  # /var is /private/var: a project root is compared resolved
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def git(self, repo, *args):
        proc = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Spud", "-c", "user.email=spud@example.invalid", "-c", "commit.gpgsign=false", *args],
                              capture_output=True, text=True, env=git_env())
        self.assertEqual(proc.returncode, 0, proc)
        return proc.stdout

    def make_repo(self, prefix):
        repo = self.scratch_dir(prefix)
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "root")
        return repo

    def add_worktree(self, repo, name):
        path = repo.parent / ("%s-%s" % (repo.name, name))
        self.addCleanup(shutil.rmtree, path, True)
        self.git(repo, "worktree", "add", "-q", "-b", name, str(path))
        return path

    # -- the CLI, run from a directory in a session ------------------------------------
    def cli(self, *args, actor=None, cwd=None, session=SESSION, check=True, stdin=None):
        """bin/spud against the scratch home, from `cwd` (default the home) in `session` (None: outside every session)."""
        env = dict(self.home.env)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if session is None:
            env.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            env["CLAUDE_CODE_SESSION_ID"] = session
        cmd = [sys.executable, "-I", "-S", str(SPUD)] + (["--as", actor] if actor else []) + [str(a) for a in args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(cwd or self.home.path), input=stdin)
        if check and proc.returncode != 0:
            raise AssertionError("spud %s (cwd %s) exited %d\nstdout: %s\nstderr: %s"
                                 % (" ".join(cmd[4:]), cwd or self.home.path, proc.returncode, proc.stdout, proc.stderr))
        return proc

    def cli_json(self, *args, **kw):
        proc = self.cli("--json", *args, **kw)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise AssertionError("not JSON: %r (stderr %r)" % (proc.stdout, proc.stderr)) from e

    def register(self, root, key, ticket_prefix, team_prefix, landing, sessions=None):
        extra = ["--sessions", sessions] if sessions else []
        return self.cli("project", "add", root, "--key", key, "--ticket-prefix", ticket_prefix, "--team-prefix", team_prefix, "--landing", landing, *extra, actor="spud")

    def claim(self, s):
        return self.cli("session", "claim", "--project", s.project, actor="spud", cwd=s.cwd, session=s.session)

    def release(self, s):
        return self.cli("session", "release", actor="spud", cwd=s.cwd, session=s.session)

    # -- hooks, run as a session's installed hook line runs them ------------------------
    def hook_in(self, s, event, payload):
        env = dict(self.home.env)
        if s.launch is None:
            env.pop("CLAUDE_PROJECT_DIR", None)
        else:
            env["CLAUDE_PROJECT_DIR"] = str(s.launch)
        sid = payload.get("session_id") if isinstance(payload, dict) else None
        if sid:
            env["CLAUDE_CODE_SESSION_ID"] = sid  # set in the hook environment and equal to the payload's (probe P5)
        else:
            env.pop("CLAUDE_CODE_SESSION_ID", None)
        cmd = [sys.executable, "-I", "-S", str(SPUD), "hook", event] + (["--project", s.project] if s.project else [])
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(s.cwd), input=stdin)
        return HookResult(proc.returncode, proc.stdout, proc.stderr)

    def place(self, s, payload):
        """A HookCase payload moved into session s: its cwd and its session."""
        payload["cwd"] = str(s.cwd)
        payload["session_id"] = s.session
        payload["transcript_path"] = TRANSCRIPT % s.session
        return payload

    def edit_p(self, s, path, agent_id=None, tool="Write"):
        return self.place(s, self.pre_edit(path, agent_id=agent_id, tool=tool))

    def bash_p(self, s, command, agent_id=None):
        return self.place(s, self.pre_bash(command, agent_id=agent_id))

    def agent_p(self, s, description, agent_id=None, **kw):
        return self.place(s, self.pre_agent(description, agent_id=agent_id, session=s.session, **kw))

    def post_p(self, s, tool_use_id, agent_id, description, subagent_type="spudagent"):
        p = self.place(s, self.post_agent_launched(tool_use_id, agent_id, description, session=s.session))
        p["tool_input"]["subagent_type"] = subagent_type
        return p

    def start_p(self, s, agent_id, agent_type="spudagent"):
        return self.place(s, self.sub_start(agent_id, agent_type, session=s.session))

    def sub_stop_p(self, s, agent_id, agent_type="spudagent"):
        return self.place(s, self.sub_stop(agent_id, agent_type=agent_type, session=s.session))

    def session_start_p(self, s, source="startup"):
        return self.place(s, self.session_start(source))

    def stop_p(self, s):
        return self.place(s, self.stop(session=s.session))

    # -- asserts ------------------------------------------------------------------------
    def assertHookSilent(self, r, what=None):
        self.assertEqual((r.code, r.stdout, r.stderr), (0, "", ""), (what, r))

    def assertDenied(self, r, needle=None, what=None):
        self.assertEqual((r.code, r.decision), (0, "deny"), (what, r))
        if needle is not None:
            self.assertIn(needle, r.reason, (what, r.reason))
        return r

    def write(self, s, path, agent_id=None):
        return self.hook_in(s, "PreToolUse", self.edit_p(s, path, agent_id))

    def redirect(self, s, path, agent_id=None):
        return self.hook_in(s, "PreToolUse", self.bash_p(s, "echo x > %s" % path, agent_id))

    def copy(self, s, path, agent_id=None):
        """SPD-121: a write by argument, the path cp's destination, which every caller is answered for as for a redirection."""
        return self.hook_in(s, "PreToolUse", self.bash_p(s, "cp /dev/null %s" % path, agent_id))

    def expect(self, s, agent_id, label, path, expected):
        """One cell of the path table, through PreToolUse(Write), a Bash redirection and a Bash write by argument: None is
        silent, a string the needle a refusal carries."""
        for tool, run in (("Write", self.write), ("Bash redirect", self.redirect), ("Bash cp", self.copy)):
            with self.subTest(session=s.label, agent_id=agent_id, target=label, tool=tool):
                r = run(s, path, agent_id)
                if expected is None:
                    self.assertHookSilent(r, (s.label, agent_id, label, tool))
                else:
                    self.assertDenied(r, expected, (s.label, agent_id, label, tool))

    # -- a BAD-001 team --------------------------------------------------------------------
    def plan_bad(self, name=None, deliverables=("src/**", "home:docs/x.md"), session=SESSION_CLAIMED):
        args = ["member", "new", "--ticket", self.bad_ticket["key"], "--persona", "scout", "--model", "haiku", "--brief", "Do the BadTakes thing."]
        for glob in deliverables:
            args += ["--deliverable", glob]
        if name:
            args += ["--name", name]
        # SPD-098: planned from the worktree bad_wt, which binds BAD-001 to it, as a claimed session in its worktree would
        return self.cli_json(*args, actor="spud", session=session, cwd=self.bad_wt)["member"]

    def bad_description(self, m):
        return "%s/%s (%s, %s)" % (self.bad_ticket["team_key"], m["name"], m["lineage"], m["persona"])

    def spawn_in(self, s, m, agent_id):
        """PreToolUse(Agent) allow, SubagentStart and the background PostToolUse binding, all in session s."""
        tool_use_id = "toolu_" + agent_id
        description = self.bad_description(m)
        pre = self.hook_in(s, "PreToolUse", self.agent_p(s, description, model=m["model"], subagent_type=m["agent_type"], tool_use_id=tool_use_id))
        self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)
        start = self.hook_in(s, "SubagentStart", self.start_p(s, agent_id, m["agent_type"]))
        self.assertEqual(start.code, 0, start)
        post = self.hook_in(s, "PostToolUse", self.post_p(s, tool_use_id, agent_id, description, m["agent_type"]))
        self.assertEqual((post.code, post.stdout), (0, ""), post)
        return self.cli_json("member", "show", m["ref"])["member"]

    def events_of_agent(self, agent_id):
        return self.home.scalar("SELECT count(*) FROM events WHERE agent_id = ?", agent_id)


# =============================================================================
# Section 3.2: who may write where
# =============================================================================


class PathTableTest(ProjectHookCase):
    """Every cell of the section 3.2 table, through PreToolUse(Write) and through a shell redirection, for a member of
    BAD-001 whose globs are `src/**` (bare: relative to badtakes, in the worktree BAD-001 is bound to since SPD-098, and in
    no other checkout of it) and `home:docs/x.md` (qualified: the home's checkout), bound in the claimed session."""

    def setUp(self):
        super().setUp()
        self.member = self.spawn_in(self.CLAIMED, self.plan_bad(name="Russet"), AGENT_A)
        self.assertEqual((self.member["status"], self.member["agent_id"]), ("active", AGENT_A), self.member)
        home, bad, wt, out = self.home.path, self.bad, self.bad_wt, self.outside
        state = (STATE_WORDING,) * 5
        self.table = (
            # target                                              path                                  Spud     plain     member  unbound, Spud  unbound, plain
            ("badtakes root: src/a.txt",                          bad / "src" / "a.txt",                LAW_1,   None,     BOUND_WORKTREE, NOT_BOUND, None),
            ("badtakes root: README.md",                          bad / "README.md",                    LAW_1,   None,     LAW_5,  NOT_BOUND,     None),
            ("badtakes root: docs/x.md (the glob names the home)", bad / "docs" / "x.md",              LAW_1,   None,     LAW_5,  NOT_BOUND,     None),
            ("badtakes root: ledger/x.md (ordinary code there)",  bad / "ledger" / "x.md",              LAW_1,   None,     LAW_5,  NOT_BOUND,     None),
            ("badtakes worktree: src/a.txt",                      wt / "src" / "a.txt",                 LAW_1,   None,     None,   NOT_BOUND,     None),
            ("badtakes worktree: README.md",                      wt / "README.md",                     LAW_1,   None,     LAW_5,  NOT_BOUND,     None),
            ("home, Spud's set: CLAUDE.md",                       home / "CLAUDE.md",                   None,    NOT_SPUD, LAW_5,  NOT_BOUND,     NOT_SPUD),
            ("home, Spud's set: .claude/settings.json",           home / ".claude" / "settings.json",   None,    NOT_SPUD, LAW_5,  NOT_BOUND,     NOT_SPUD),
            ("home: bin/x",                                       home / "bin" / "x",                   LAW_1,   NOT_SPUD, LAW_5,  NOT_BOUND,     NOT_SPUD),
            ("home: docs/x.md (home:docs/x.md)",                  home / "docs" / "x.md",               LAW_1,   NOT_SPUD, None,   NOT_BOUND,     NOT_SPUD),
            ("home: src/a.txt (a bare glob is BAD-001's project)", home / "src" / "a.txt",              LAW_1,   NOT_SPUD, LAW_5,  NOT_BOUND,     NOT_SPUD),
            ("home: ledger/x.md",                                 home / "ledger" / "x.md",             LAW_5,   LAW_5,    LAW_5,  LAW_5,         LAW_5),
            ("home: reports/2026-09-14.md",                       home / "reports" / "2026-09-14.md",   LAW_5,   LAW_5,    LAW_5,  LAW_5,         LAW_5),
            ("badtakes root: .spud/ledger.db",                    bad / ".spud" / "ledger.db") + state,
            ("badtakes worktree: .spud/worktrees.json",           wt / ".spud" / "worktrees.json") + state,
            # SPD-066: a path with a .git component is no agent_id's in a Spud session; Spud and a plain session keep their answers
            ("badtakes root: .git/hooks/pre-auto-gc",             bad / ".git" / "hooks" / "pre-auto-gc", LAW_1, None,     GIT_DIR, GIT_DIR,      None),
            ("badtakes worktree: .git (its gitfile)",             wt / ".git",                          LAW_1,   None,     GIT_DIR, GIT_DIR,      None),
            ("badtakes worktree: src/.git/index (under the glob)", wt / "src" / ".git" / "index",       LAW_1,   None,     GIT_DIR, GIT_DIR,      None),
            ("outside every project",                             out / "x.txt",                        None,    None,     None,   None,          None),
        )

    def check_column(self, s, column, agent_id):
        for row in self.table:
            self.expect(s, agent_id, row[0], row[1], row[2 + column])

    def test_a_spud_session_launched_in_the_home(self):
        """v1 said nothing about a badtakes path from the home, since it lies outside the home; it is Law 1 now."""
        self.check_column(self.HOME, SPUD_COL, None)

    def test_a_claimed_session_launched_in_badtakes(self):
        self.check_column(self.CLAIMED, SPUD_COL, None)

    def test_a_plain_session_launched_in_badtakes(self):
        self.check_column(self.PLAIN, PLAIN_COL, None)

    def test_a_member_of_bad_001(self):
        self.check_column(self.CLAIMED, MEMBER_COL, AGENT_A)

    def test_an_unbound_agent_id_in_a_spud_session(self):
        for s in (self.CLAIMED, self.HOME):
            self.check_column(s, UNBOUND_SPUD_COL, AGENT_D)

    def test_an_unbound_agent_id_in_a_plain_session(self):
        self.check_column(self.PLAIN, UNBOUND_PLAIN_COL, AGENT_D)

    def test_the_state_directory_of_badtakes_is_refused_by_the_path_rule(self):
        """The redirection spelled so that the Bash hook's raw-text database regex misses it: the refusal must come from the
        path rule mapping the badtakes root and its worktree (SPD-031's state directory, at every project root)."""
        for s, agent_id in ((self.CLAIMED, AGENT_A), (self.CLAIMED, None), (self.HOME, None)):
            for target in (self.bad / ".spud" / "ledger.db", self.bad_wt / ".spud" / "worktrees.json"):
                with self.subTest(session=s.label, agent_id=agent_id, target=str(target)):
                    r = self.hook_in(s, "PreToolUse", self.bash_p(s, "echo x > %s" % quote_split(target), agent_id))
                    self.assertDenied(r, STATE_WORDING, str(target))
                    self.assertIn("redirection or tee", r.reason)
                    self.assertNotIn("Law", r.reason)

    def test_a_worktree_under_claude_worktrees_maps_to_badtakes(self):
        """EnterWorktree's own spelling, .claude/worktrees/<name>/ in the badtakes root, is a checkout of badtakes too: Spud's and
        a plain session's writes there are the root's, and a member of BAD-001, bound to bad_wt, is refused it (SPD-098)."""
        wt = self.add_worktree_inside(self.bad, "bad-001-inside")
        self.expect(self.CLAIMED, AGENT_A, "inside worktree: src/a.txt", wt / "src" / "a.txt", BOUND_WORKTREE)
        self.expect(self.CLAIMED, AGENT_A, "inside worktree: README.md", wt / "README.md", LAW_5)
        self.expect(self.CLAIMED, None, "inside worktree: src/a.txt", wt / "src" / "a.txt", LAW_1)
        self.expect(self.PLAIN, None, "inside worktree: src/a.txt", wt / "src" / "a.txt", None)

    def add_worktree_inside(self, repo, name):
        path = repo / ".claude" / "worktrees" / name
        self.git(repo, "worktree", "add", "-q", "-b", "worktree-" + name, str(path))
        return path


# =============================================================================
# Section 6.1: a session's mode
# =============================================================================


class SessionModeTest(ProjectHookCase):
    """`spud` when the session holds an unreleased claim or its launch project is `always`; `plain` otherwise; `outside`
    (behaving as spud) when the launch directory maps to no project.  Each probe is a Write with no agent_id: into
    badtakes (Law 1 for Spud, silent for a plain session) and into the home's CLAUDE.md (silent for Spud, "not Spud")."""

    def assertSpud(self, s, project_root):
        self.assertDenied(self.write(s, project_root / "src" / "a.txt"), LAW_1, s.label)
        self.assertHookSilent(self.write(s, self.home.path / "CLAUDE.md"), s.label)

    def assertPlain(self, s, project_root):
        self.assertHookSilent(self.write(s, project_root / "src" / "a.txt"), s.label)
        self.assertDenied(self.write(s, self.home.path / "CLAUDE.md"), NOT_SPUD, s.label)

    def test_a_claim_is_its_sessions_alone_and_release_ends_it(self):
        self.assertSpud(self.CLAIMED, self.bad)
        self.assertPlain(self.PLAIN, self.bad)
        self.release(self.CLAIMED)
        self.assertPlain(self.CLAIMED, self.bad)
        r = self.hook_in(self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED, "resume"))
        self.assertEqual(r.code, 0, r)
        self.assertIn("/spud", r.context)  # plain again, and it says so at its next SessionStart
        self.assertNotIn("you are Spud here", r.context)
        self.claim(self.CLAIMED)
        self.assertSpud(self.CLAIMED, self.bad)

    def test_the_launch_directory_decides_after_enter_worktree(self):
        """CLAUDE_PROJECT_DIR stays the launch directory while the payload's cwd follows EnterWorktree (probe P5); a session
        the desktop app starts inside a worktree has the worktree as its launch directory, which maps to badtakes."""
        for s in (self.CLAIMED._replace(label="claimed, cwd in the worktree", cwd=self.bad_wt),
                  self.CLAIMED._replace(label="claimed, launched in the worktree", launch=self.bad_wt, cwd=self.bad_wt)):
            with self.subTest(s.label):
                self.assertSpud(s, self.bad_wt)
        for s in (self.PLAIN._replace(label="plain, cwd in the worktree", cwd=self.bad_wt),
                  self.PLAIN._replace(label="plain, launched in the worktree", launch=self.bad_wt, cwd=self.bad_wt)):
            with self.subTest(s.label):
                self.assertPlain(s, self.bad_wt)

    def test_without_claude_project_dir_the_payload_cwd_decides(self):
        self.assertPlain(Session("no launch directory, cwd badtakes", None, SESSION_PLAIN, self.bad, KEY), self.bad)
        home = Session("no launch directory, cwd home", None, SESSION_PLAIN, self.home.path, None)
        self.assertSpud(home, self.bad)
        self.assertDenied(self.write(home, self.home.path / "bin" / "x"), LAW_1)

    def test_a_launch_directory_in_no_project_behaves_as_spud(self):
        """`outside`: a scratch home run with --settings, or a hook line left behind after `project remove`."""
        s = Session("outside", self.outside, SESSION_PLAIN, self.outside, None)
        self.assertSpud(s, self.bad)
        self.assertDenied(self.write(s, self.home.path / "bin" / "x"), LAW_1)

    def test_an_always_project_makes_every_session_launched_there_spud(self):
        always = self.make_repo("alwaysrepo-")
        self.register(always, "alwaysrepo", "ALW", "ALWS", "merge", sessions="always")
        s = Session("always, unclaimed", always, SESSION_PLAIN, always, "alwaysrepo")
        self.assertSpud(s, always)
        self.assertDenied(self.write(s, self.bad / "src" / "a.txt"), LAW_1)
        r = self.hook_in(s, "SessionStart", self.session_start_p(s))
        self.assertEqual(r.code, 0, r)
        self.assertIn("run /spud now", r.context)
        self.assertPlain(self.PLAIN, self.bad)  # badtakes took `project add`'s default, claim


# =============================================================================
# Section 6.2: each hook, for a claimed, an unclaimed and a home session
# =============================================================================


class SessionStartProjectTest(ProjectHookCase):
    def test_a_plain_session_gets_one_line_of_at_most_300_bytes(self):
        for source in ("startup", "resume", "compact", "clear"):
            with self.subTest(source=source):
                r = self.hook_in(self.PLAIN, "SessionStart", self.session_start_p(self.PLAIN, source))
                self.assertEqual(r.code, 0, r)
                self.assertEqual(r.json["hookSpecificOutput"]["hookEventName"], "SessionStart", r)
                context = r.context
                self.assertTrue(context.strip(), r)
                self.assertNotIn("\n", context.strip(), context)
                self.assertLessEqual(len(context.encode("utf-8")), 300, context)
                self.assertIn("/spud", context)
                self.assertIn(KEY, context)

    def test_a_claimed_session_is_told_it_is_spud_within_the_inline_cap(self):
        """P2: an additionalContext past the harness's inline limit reaches the model as a 2 KB preview, so the header and
        the board fit in 8000 bytes whatever the board holds (SPD-048 measured the limit: 10,000 UTF-16 code units)."""
        for n in range(20):
            self.cli("ticket", "new", "--project", KEY, "--status", "active", "--title",
                     "BadTakes ticket %02d with a long title that makes the board brief grow well past eight kilobytes %s" % (n, "and on " * 45), actor="spud")
        for source in ("resume", "compact", "clear"):
            with self.subTest(source=source):
                r = self.hook_in(self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED, source))
                self.assertEqual(r.code, 0, r)
                self.assertLessEqual(len(r.context.encode("utf-8")), 8000, len(r.context.encode("utf-8")))
                self.assertGreater(len(r.context.encode("utf-8")), 7000, len(r.context.encode("utf-8")))
                self.assertIn(KEY, r.context)
                self.assertIn("you are Spud here", r.context)
                self.assertRegex(r.context, r"\n\(\d+ more lines? cut to fit; run `spud board --brief` for the rest\)\Z")

    def test_a_home_session_gets_the_board_as_today(self):
        for source in ("startup", "resume"):
            with self.subTest(source=source):
                r = self.hook_in(self.HOME, "SessionStart", self.session_start_p(self.HOME, source))
                self.assertEqual(r.code, 0, r)
                self.assertIn("Ledger board", r.context)
                self.assertIn(self.t["key"], r.context)


class AgentHookProjectTest(ProjectHookCase):
    ORDINARY = (
        dict(description="Explore the render code", subagent_type="general-purpose", model=None, isolation="worktree"),
        dict(description="parallel-worktrees: build the feed", subagent_type="general-purpose", model="sonnet"),
        dict(description="Find the config loader", subagent_type="Explore", model=None),
    )

    # -- PreToolUse(Agent) ---------------------------------------------------------------
    def test_an_ordinary_spawn_in_a_plain_session_is_silent_and_unrecorded(self):
        for n, spawn in enumerate(self.ORDINARY):
            spawn = dict(spawn)
            with self.subTest(**spawn):
                r = self.hook_in(self.PLAIN, "PreToolUse", self.agent_p(self.PLAIN, spawn.pop("description"), tool_use_id="toolu_plain%02d" % n, **spawn))
                self.assertHookSilent(r)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM spawn_requests"), 0)
        self.assertEqual(self.denied(), [])

    def test_a_spudagent_shaped_spawn_in_a_plain_session_is_denied(self):
        m = self.plan_bad(session=SESSION_CLAIMED)
        exact = self.bad_description(m)
        cases = (
            ("the planned member, spawned exactly", dict(description=exact, subagent_type="spudagent", model="haiku")),
            ("subagent_type spudagent, a free description", dict(description="Do a thing", subagent_type="spudagent", model="haiku")),
            ("a BADS description, another agent type", dict(description="%s-001/Nobody (01, scout)" % TEAM_PREFIX, subagent_type="general-purpose", model="haiku")),
            ("a BADS description with isolation", dict(description=exact, subagent_type="spudagent", model="haiku", isolation="worktree")),
        )
        for n, (what, spawn) in enumerate(cases):
            spawn = dict(spawn)
            with self.subTest(what):
                r = self.hook_in(self.PLAIN, "PreToolUse", self.agent_p(self.PLAIN, spawn.pop("description"), tool_use_id="toolu_shaped%02d" % n, **spawn))
                self.assertDenied(r, what=what)
        shown = self.cli_json("member", "show", m["ref"])["member"]
        self.assertEqual((shown["status"], shown["agent_id"]), ("planned", None), shown)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM spawn_requests WHERE decision = 'allow'"), 0)

    def test_an_ordinary_spawn_in_a_spud_session_is_denied_as_today(self):
        for s in (self.CLAIMED, self.HOME):
            for n, spawn in enumerate(self.ORDINARY):
                spawn = dict(spawn)
                with self.subTest(session=s.label, **spawn):
                    r = self.hook_in(s, "PreToolUse", self.agent_p(s, spawn.pop("description"), tool_use_id="toolu_%s%02d" % (s.label, n), **spawn))
                    self.assertDenied(r, "SPUD-nnn/<Name> (<lineage>, <persona>)", s.label)

    def test_a_bads_description_binds_like_a_spud_one(self):
        for s, agent_id in ((self.CLAIMED, AGENT_A), (self.HOME, AGENT_B)):
            with self.subTest(session=s.label):
                m = self.plan_bad(session=s.session)
                description = self.bad_description(m)
                self.assertRegex(description, r"^BADS-001/[A-Za-z][\w-]* \(0\d, scout\)$")
                tool_use_id = "toolu_" + agent_id
                pre = self.hook_in(s, "PreToolUse", self.agent_p(s, description, model="haiku", subagent_type="spudagent", tool_use_id=tool_use_id))
                self.assertEqual((pre.code, pre.decision), (0, "allow"), pre)
                row = self.home.rows("SELECT * FROM spawn_requests WHERE tool_use_id = ?", tool_use_id)[0]
                self.assertEqual((row["decision"], row["member_id"], row["session_id"], row["agent_id"]), ("allow", m["id"], s.session, None), row)
                self.assertEqual(self.cli_json("member", "show", m["ref"])["member"]["status"], "planned")
                start = self.hook_in(s, "SubagentStart", self.start_p(s, agent_id))
                self.assertEqual(start.code, 0, start)
                post = self.hook_in(s, "PostToolUse", self.post_p(s, tool_use_id, agent_id, description))
                self.assertEqual((post.code, post.stdout), (0, ""), post)
                shown = self.cli_json("member", "show", m["ref"])["member"]
                self.assertEqual((shown["status"], shown["agent_id"]), ("active", agent_id), shown)
                self.assertEqual(self.home.scalar("SELECT agent_id FROM spawn_requests WHERE tool_use_id = ?", tool_use_id), agent_id)
                kinds = [e["kind"] for e in self.events(member=m["ref"])]
                self.assertEqual(kinds[:2], ["member.planned", "member.spawned"], kinds)
        self.assertEqual(self.events("hook.error"), [])

    # -- PostToolUse(Agent) ----------------------------------------------------------------
    def test_post_tool_use_without_a_request_writes_no_hook_error_in_a_plain_session(self):
        r = self.hook_in(self.PLAIN, "PostToolUse", self.post_p(self.PLAIN, "toolu_erics", AGENT_C, "Explore the render code", "general-purpose"))
        self.assertHookSilent(r)
        self.assertEqual(self.events("hook.error"), [])
        for n, s in enumerate((self.CLAIMED, self.HOME)):  # a Spud session still records the gap, as today
            with self.subTest(session=s.label):
                r = self.hook_in(s, "PostToolUse", self.post_p(s, "toolu_unseen%d" % n, AGENT_B, "Explore the render code", "general-purpose"))
                self.assertEqual((r.code, r.stdout), (0, ""), r)
                self.assertEqual(len(self.events("hook.error")), n + 1)

    # -- SubagentStart and SubagentStop --------------------------------------------------------
    def test_subagent_start_and_stop_of_an_unbound_agent_in_a_plain_session_write_nothing(self):
        r = self.hook_in(self.PLAIN, "SubagentStart", self.start_p(self.PLAIN, AGENT_C, "general-purpose"))
        self.assertHookSilent(r)
        r = self.hook_in(self.PLAIN, "SubagentStop", self.sub_stop_p(self.PLAIN, AGENT_C, "general-purpose"))
        self.assertHookSilent(r)
        self.assertEqual(self.events_of_agent(AGENT_C), 0)
        # a home session records both, as today
        r = self.hook_in(self.HOME, "SubagentStart", self.start_p(self.HOME, AGENT_B, "general-purpose"))
        self.assertEqual(r.code, 0, r)
        self.assertIn("Ledger: your agent_id is `%s`" % AGENT_B, r.context)
        r = self.hook_in(self.HOME, "SubagentStop", self.sub_stop_p(self.HOME, AGENT_B, "general-purpose"))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertEqual(self.events_of_agent(AGENT_B), 2)

    def test_subagent_start_names_the_tickets_project(self):
        m = self.spawn_in(self.CLAIMED, self.plan_bad(), AGENT_A)
        r = self.hook_in(self.CLAIMED, "SubagentStart", self.start_p(self.CLAIMED, AGENT_A))
        self.assertEqual(r.code, 0, r)
        self.assertIn("%s/%s" % (self.bad_ticket["team_key"], m["name"]), r.context)
        self.assertIn(KEY, r.context)
        self.assertIn(str(self.bad), r.context)
        self.assertIn("bound to its worktree `%s`" % os.path.realpath(self.bad_wt), r.context)  # SPD-098

    def test_a_bound_members_stop_is_recorded_and_held_in_a_plain_session_as_today(self):
        m = self.spawn_in(self.CLAIMED, self.plan_bad(), AGENT_A)
        self.release(self.CLAIMED)  # the session that spawned it is plain now; its member is still the ledger's
        r = self.hook_in(self.CLAIMED, "SubagentStop", self.sub_stop_p(self.CLAIMED, AGENT_A))
        self.assertEqual((r.code, r.json and r.json.get("decision")), (0, "block"), r)
        self.cli("member", "result", "Built it.", actor=AGENT_A, session=SESSION_CLAIMED)
        r = self.hook_in(self.CLAIMED, "SubagentStop", self.sub_stop_p(self.CLAIMED, AGENT_A))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        self.assertIsNotNone(self.cli_json("member", "show", m["ref"])["member"]["stopped_at"])


class BashHookProjectTest(ProjectHookCase):
    def setUp(self):
        super().setUp()
        # the home's own launcher (HookCase copies it), through the interpreter the hook runs on: a call the hook vouches for
        self.spud_cli = "%s -I -S %s" % (sys.executable, self.home.path / "bin" / "spud")

    def bash(self, s, command, agent_id=None):
        return self.hook_in(s, "PreToolUse", self.bash_p(s, command, agent_id))

    def test_a_plain_session_is_refused_spuds_writes_and_allowed_its_claim(self):
        for command in ("--as spud ticket new --title x", "--as spud ticket new --project badtakes --title x --status active"):
            with self.subTest(command):
                r = self.assertDenied(self.bash(self.PLAIN, "%s %s" % (self.spud_cli, command)), "Law 6")
                self.assertIn("/spud", r.reason)
        for command in ("--as spud session claim", "--as spud session claim --project badtakes"):
            with self.subTest(command):
                r = self.bash(self.PLAIN, "%s %s" % (self.spud_cli, command))
                self.assertEqual((r.code, r.decision), (0, "allow"), r)

    def test_a_spud_session_keeps_todays_answers(self):
        for s in (self.CLAIMED, self.HOME):
            with self.subTest(session=s.label):
                r = self.bash(s, "%s --as spud ticket new --project badtakes --title x --status active" % self.spud_cli)
                self.assertEqual((r.code, r.decision), (0, "allow"), r)

    def test_the_ledgers_own_refusals_hold_in_a_plain_session(self):
        self.assertDenied(self.bash(self.PLAIN, "%s hook Stop" % self.spud_cli), "spud hook")
        self.assertDenied(self.bash(self.PLAIN, "cat %s" % (self.bad / ".spud" / "ledger.db")), STATE_WORDING)
        self.assertDenied(self.bash(self.PLAIN, "cat %s" % (self.home.path / ".spud" / "ledger.db")), STATE_WORDING)
        self.assertDenied(self.bash(self.PLAIN, "%s --as %s member result done" % (self.spud_cli, AGENT_A)), LAW_5)

    def test_law_7_binds_members_and_not_erics_own_subagents(self):
        for command in ("git commit -m x", "git push", "git checkout -b feat/x"):
            with self.subTest(command=command):
                self.assertHookSilent(self.bash(self.PLAIN, command, AGENT_D), command)  # Eric's own subagent in his own session
                self.assertHookSilent(self.bash(self.PLAIN, command), command)
                self.assertDenied(self.bash(self.CLAIMED, command, AGENT_D), "Law 7", command)  # unbound in a Spud session, as today
        self.spawn_in(self.CLAIMED, self.plan_bad(), AGENT_A)
        self.assertDenied(self.bash(self.CLAIMED, "git commit -m x", AGENT_A), "Law 7")
        self.assertDenied(self.bash(self.CLAIMED._replace(cwd=self.bad_wt), "git commit -m x", AGENT_A), "Law 7")

    def test_a_nested_repository_binds_members_and_not_erics_own_subagents(self):
        """SPD-066 (2) in another project: a repository planted below badtakes' worktree or root is no checkout's own, for a
        member of BAD-001 and an unbound agent_id of a Spud session; Eric's plain session and its subagents keep today's
        answer, and the checkouts' own repositories stay silent for everyone.  Since SPD-123 Spud is refused too: the
        repository lies in a checkout the ledger knows, so git would run its hooks under his own call (PlantedRepositoryTest)."""
        nested = plant_git_dir(self.bad_wt / "src" / "fake" / ".git").parent
        bare = plant_git_dir(self.bad / "vendor" / "bare")
        self.spawn_in(self.CLAIMED, self.plan_bad(), AGENT_A)
        in_wt = self.CLAIMED._replace(cwd=self.bad_wt)
        for command, repository in (("git -C %s status" % nested, nested), ("cd src/fake && git fetch", nested),
                                    ("GIT_DIR=%s git log" % bare, bare), ("git -C %s log" % bare, bare)):
            with self.subTest(command=command):
                r = self.assertDenied(self.bash(in_wt, command, AGENT_A), GIT_NESTED_WORDING, command)
                self.assertIn(str(repository), r.reason)
                self.assertDenied(self.bash(in_wt, command, AGENT_D), GIT_NESTED_WORDING, command)  # unbound, Spud session
                plain_wt = self.PLAIN._replace(cwd=self.bad_wt)
                self.assertHookSilent(self.bash(plain_wt, command, AGENT_D), command)  # Eric's own subagent
                self.assertHookSilent(self.bash(plain_wt, command), command)
                r = self.assertDenied(self.bash(in_wt, command), GIT_NESTED_WORDING, command)  # Spud, since SPD-123
                self.assertIn(SPUD_PLANTED, r.reason)
        for s, command in ((in_wt, "git status"), (in_wt, "git -C %s log" % self.bad_wt), (self.CLAIMED, "git fetch"),
                           (in_wt, "git -C %s/src status" % self.bad_wt)):
            with self.subTest(command=command, cwd=str(s.cwd)):
                self.assertHookSilent(self.bash(s, command, AGENT_A), command)
                self.assertHookSilent(self.bash(s, command), command)


PLANTED_LINE = "repository check: "  # SPD-123: the one line `spud board --brief` and a Spud session's context add


class PlantedRepositoryTest(ProjectHookCase):
    """SPD-123: SPD-066 refuses a member every path it spells into a git directory and every repository but a known
    checkout's own, and SPD-063 the program keys at a repository's local and worktree scopes -- all of it read from what a
    member spells.  A member that writes through a program the hook cannot read (python -c, a script under its globs, node)
    can still put a hook in a known checkout's common git directory, a program key in its config or in a worktree's
    config.worktree, or a repository below the checkout, and git runs it with nothing on the line: post-index-change under
    any `git status`, reference-transaction under `git fetch` (SPD-066's probe, git 2.54.0), and pre-commit, commit-msg,
    post-commit, post-merge and post-checkout under Spud's own commit, merge and worktree calls, which he runs as Eric.

    So one check runs before every git call that is not a plain session's, Spud's own included.  For a repository in a
    checkout the ledger knows it refuses (a) an entry of the common directory's hooks/ that is not a *.sample file, whatever
    its mode; (b) a program-naming key at the local or worktree scope; (c) a repository that is not the checkout's own.
    Spud alone stays silent where the line is his own: a repository outside every known checkout (a scratch clone), and a
    directory the hook cannot follow.  Eric's plain session and its subagents keep today's answers.  The harness and Eric's
    terminal run git no hook sees, so doctor names every finding and `spud board --brief`, with every Spud session's
    context, carries one line naming the checkout."""

    SPUD_COMMANDS = ("git status", "git commit -m x", "git merge --no-ff b", "git fetch")

    def setUp(self):
        super().setUp()
        self.common = self.bad / ".git"  # the common git directory of the checkout and of its worktree
        self.wt_gitdir = Path((self.bad_wt / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip())
        self.IN_WT = self.CLAIMED._replace(label="claimed, cwd in the worktree", cwd=self.bad_wt)
        self.PLAIN_WT = self.PLAIN._replace(label="plain, cwd in the worktree", cwd=self.bad_wt)
        self.config_text = (self.common / "config").read_text(encoding="utf-8")
        (self.common / "hooks").mkdir(exist_ok=True)
        (self.common / "hooks" / "pre-push.sample").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")  # git init's kind

    def bash(self, s, command, agent_id=None):
        return self.hook_in(s, "PreToolUse", self.bash_p(s, command, agent_id))

    # -- what a member could plant through an interpreter ------------------------------------
    def plant_hook(self, name="post-index-change", mode=0o755):
        hook = self.common / "hooks" / name
        hook.write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
        hook.chmod(mode)
        return hook

    def plant_local(self, text):
        """The common config as git init left it, then `text`; plant_local("") restores it."""
        (self.common / "config").write_text(self.config_text + text, encoding="utf-8")

    def plant_worktree_key(self, local=""):
        self.plant_local(local + "[extensions]\n\tworktreeConfig = true\n")
        (self.wt_gitdir / "config.worktree").write_text("[core]\n\tfsmonitor = /bin/echo\n", encoding="utf-8")

    # -- who calls, where ----------------------------------------------------------------------
    def spud_in(self, s):
        return [(s, command) for command in self.SPUD_COMMANDS]

    def spud_places(self):
        """Spud's commit, merge, status and fetch in the checkout and in its worktree, and git -C from the home's session."""
        return (self.spud_in(self.CLAIMED) + self.spud_in(self.IN_WT)
                + [(self.HOME, "git -C %s status" % self.bad), (self.HOME, "git -C %s fetch" % self.bad_wt)])

    def assertSpudRefused(self, needles, places=None):
        for s, command in (self.spud_places() if places is None else places):
            with self.subTest(session=s.label, command=command):
                r = self.assertDenied(self.bash(s, command), SPUD_PLANTED, command)
                for needle in needles:
                    self.assertIn(needle, r.reason, command)
                self.assertNotIn("Law 7", r.reason)  # Law 7 is a member's; Spud's refusal is about what git would run

    def assertSpudSilent(self, places=None):
        for s, command in (self.spud_places() if places is None else places):
            with self.subTest(session=s.label, command=command):
                self.assertHookSilent(self.bash(s, command), command)

    def assertPlainSilent(self, commands=()):
        """Eric's own session and its own subagents: today's answers, whatever is planted."""
        for s in (self.PLAIN, self.PLAIN_WT):
            for command in self.SPUD_COMMANDS + tuple(commands):
                for agent_id in (None, AGENT_D):
                    with self.subTest(session=s.label, command=command, agent_id=agent_id):
                        self.assertHookSilent(self.bash(s, command, agent_id), command)

    def doctor(self):
        proc = self.cli("--json", "doctor", check=False)
        return proc, json.loads(proc.stdout)

    # -- the Bash hook ----------------------------------------------------------------------------
    def test_a_clean_checkout_is_silent_for_everyone(self):
        """hooks/ holding only samples and a config setting no program: nothing changes for anyone."""
        self.assertSpudSilent()
        self.assertPlainSilent()
        self.spawn_in(self.CLAIMED, self.plan_bad(), AGENT_A)
        for command in ("git status", "git fetch", "git -C %s log" % self.bad):
            self.assertHookSilent(self.bash(self.IN_WT, command, AGENT_A), command)

    def test_a_planted_hook_refuses_spud_in_the_checkout_and_in_its_worktree(self):
        for name, mode in (("post-index-change", 0o755), ("post-index-change", 0o644), ("reference-transaction", 0o755),
                           ("pre-commit", 0o700), ("post-merge", 0o755)):
            with self.subTest(hook=name, mode=oct(mode)):  # git runs one once it is executable, and a program can set the bit
                hook = self.plant_hook(name, mode)
                self.assertSpudRefused([str(hook), GIT_HOOK_WORDING, "with nothing on the line"])
                self.assertPlainSilent()
                hook.unlink()
        hook = self.plant_hook()
        for command in ("git --git-dir=%s/.git status" % self.bad, "GIT_DIR=%s/.git git fetch" % self.bad,
                        "GIT_COMMON_DIR=%s/.git git log" % self.bad):  # the git directory taken as given, as git takes it
            self.assertSpudRefused([str(hook)], [(self.CLAIMED, command), (self.HOME, command)])
        hook.unlink()
        for odd in ("pre-commit.d", "x.sample"):  # a directory named like anything, and one named like a sample: fail closed
            with self.subTest(entry=odd):
                (self.common / "hooks" / odd).mkdir()
                self.assertSpudRefused([str(self.common / "hooks" / odd)], self.spud_in(self.CLAIMED))
                (self.common / "hooks" / odd).rmdir()
        self.assertSpudSilent()  # samples only again

    def test_a_program_key_at_the_local_scope_refuses_spud(self):
        for key, text in (("core.fsmonitor", "[core]\n\tfsmonitor = /bin/echo\n"),
                          ("core.hookspath", "[core]\n\thooksPath = /tmp/planted-hooks\n")):
            with self.subTest(key):
                self.plant_local(text)
                self.assertSpudRefused([key, "local"])
                self.assertPlainSilent()
        self.plant_local("")
        self.assertSpudSilent()

    def test_spuds_refusal_names_every_finding_of_the_repository(self):
        hook = self.plant_hook()
        self.plant_local("[core]\n\tfsmonitor = /bin/echo\n")
        r = self.assertDenied(self.bash(self.CLAIMED, "git merge --no-ff b"), SPUD_PLANTED)
        for needle in (str(hook), "core.fsmonitor", "local", "spud doctor", "interpreter", str(self.bad)):
            self.assertIn(needle, r.reason)

    def test_a_program_key_in_a_worktrees_config_worktree_refuses_spud_there(self):
        self.plant_worktree_key()
        self.assertSpudRefused(["core.fsmonitor", "worktree"], self.spud_in(self.IN_WT) + [(self.HOME, "git -C %s fetch" % self.bad_wt)])
        self.assertSpudSilent(self.spud_in(self.CLAIMED))  # git reads a worktree's config.worktree in that worktree alone
        self.assertPlainSilent()

    def test_a_nested_bare_layout_reached_by_git_C_refuses_spud(self):
        for root, s, plain in ((self.bad, self.CLAIMED, self.PLAIN), (self.bad_wt, self.IN_WT, self.PLAIN_WT)):
            fake = plant_git_dir(root / "tests" / "fake")
            with self.subTest(checkout=str(root)):
                r = self.assertDenied(self.bash(s, "git -C tests/fake status"), GIT_NESTED_WORDING)
                self.assertIn(SPUD_PLANTED, r.reason)
                self.assertIn(str(fake), r.reason)
                for agent_id in (None, AGENT_D):
                    self.assertHookSilent(self.bash(plain, "git -C tests/fake status", agent_id))
        self.assertSpudSilent()  # the checkouts' own repositories beside them

    def test_a_worktree_whose_gitfile_or_commondir_names_another_repository_refuses_spud(self):
        """SPD-066 read a work tree found through a listed worktree's own .git as that checkout's repository, wherever the
        gitfile pointed; the common directory must be a registered checkout's own too."""
        elsewhere = self.make_repo("spud-elsewhere-")
        gitfile = self.bad_wt / ".git"
        own = gitfile.read_text(encoding="utf-8")
        gitfile.write_text("gitdir: %s\n" % (elsewhere / ".git"), encoding="utf-8")
        self.assertSpudRefused([GIT_NESTED_WORDING, str(self.bad_wt)], self.spud_in(self.IN_WT))
        gitfile.write_text(own, encoding="utf-8")
        (self.wt_gitdir / "commondir").write_text("%s\n" % (elsewhere / ".git"), encoding="utf-8")
        self.assertSpudRefused([GIT_NESTED_WORDING], self.spud_in(self.IN_WT))
        (self.wt_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        self.assertSpudSilent()

    def test_spud_is_silent_outside_every_checkout_and_where_the_hook_cannot_follow(self):
        clone = self.make_repo("spud-scratch-clone-")
        (clone / ".git" / "hooks").mkdir(exist_ok=True)
        (clone / ".git" / "hooks" / "post-index-change").write_text("#!/bin/sh\n", encoding="utf-8")
        (clone / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n\tfsmonitor = /bin/echo\n", encoding="utf-8")
        for s in (self.CLAIMED, self.HOME):
            for command in ("git -C %s status" % clone, "cd %s && git fetch" % clone, "git --git-dir=%s/.git log" % clone,
                            "cd %s && git commit -m x" % clone):
                with self.subTest(session=s.label, command=command):
                    self.assertHookSilent(self.bash(s, command), command)  # a scratch clone is Spud's own business
        self.assertDenied(self.bash(self.CLAIMED, "cd %s && git status" % clone, AGENT_D), GIT_NESTED_WORDING)  # a member's is not
        self.plant_hook()
        for s in (self.CLAIMED, self.IN_WT):
            for command in ("cd - && git status", "popd && git fetch", "cd ~x && git log"):
                with self.subTest(session=s.label, command=command):
                    self.assertHookSilent(self.bash(s, command), command)  # Spud's own spelling: the check is about files

    def test_a_member_is_refused_on_a_planted_hook_and_keeps_its_older_reasons(self):
        self.spawn_in(self.CLAIMED, self.plan_bad(), AGENT_A)
        hook = self.plant_hook()
        for command in ("git status", "git fetch", "git log --oneline", "git -C %s diff" % self.bad):
            for agent_id in (AGENT_A, AGENT_D):  # a bound member, and an unbound agent_id of a Spud session
                with self.subTest(command=command, agent_id=agent_id):
                    r = self.assertDenied(self.bash(self.IN_WT, command, agent_id), GIT_HOOK_WORDING, command)
                    self.assertIn("Law 7", r.reason)
                    self.assertIn(str(hook), r.reason)
                    self.assertIn("with nothing on the line", r.reason)
                    self.assertIn("interpreter", r.reason)
        # each refusal a member earned before SPD-123 keeps its own reason
        r = self.assertDenied(self.bash(self.IN_WT, "git commit -m x", AGENT_A), "never run")
        self.assertNotIn(GIT_HOOK_WORDING, r.reason)
        self.plant_local("[core]\n\tfsmonitor = /bin/echo\n")
        r = self.assertDenied(self.bash(self.IN_WT, "git status", AGENT_A), GIT_SCOPE_WORDING)
        self.assertNotIn(GIT_HOOK_WORDING, r.reason)
        self.plant_local("")
        plant_git_dir(self.bad_wt / "src" / "fake")
        r = self.assertDenied(self.bash(self.IN_WT, "git -C src/fake status", AGENT_A), GIT_NESTED_WORDING)
        self.assertNotIn(GIT_HOOK_WORDING, r.reason)
        r = self.assertDenied(self.bash(self.IN_WT, "git -C %s status" % self.outside, AGENT_A), "outside every checkout")
        self.assertNotIn(GIT_HOOK_WORDING, r.reason)
        self.assertPlainSilent()

    def imports_of(self, s, event, payload):
        """The modules a hook run imports, as the installed line runs it (hook_in's environment), from -X importtime."""
        env = dict(self.home.env, CLAUDE_PROJECT_DIR=str(s.launch), CLAUDE_CODE_SESSION_ID=s.session)
        cmd = [sys.executable, "-I", "-S", "-X", "importtime", str(SPUD), "hook", event] + (["--project", s.project] if s.project else [])
        proc = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, env=env, cwd=str(s.cwd))
        self.assertEqual(proc.returncode, 0, proc)
        return {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}

    def test_the_check_runs_no_git_once_its_caches_are_warm(self):
        """The cost rule: the repository check adds no subprocess to a hook beyond the config scopes' own git run, which
        happens only after a config file changes; the hooks listing is a scandir."""
        for s, event, payload in ((self.IN_WT, "PreToolUse", self.bash_p(self.IN_WT, "git status")),
                                  (self.CLAIMED, "PreToolUse", self.bash_p(self.CLAIMED, "git -C %s log" % self.bad_wt)),
                                  (self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED))):
            with self.subTest(event=event, cwd=str(s.cwd)):
                self.hook_in(s, event, payload)  # warms the worktree list, git's command list and the config scopes
                self.assertNotIn("subprocess", self.imports_of(s, event, payload))
        self.plant_local("[core]\n\tfsmonitor = /bin/echo\n")  # a config edit: the one git run, then warm again
        self.assertIn("subprocess", self.imports_of(self.IN_WT, "PreToolUse", self.bash_p(self.IN_WT, "git status")))
        self.assertNotIn("subprocess", self.imports_of(self.IN_WT, "PreToolUse", self.bash_p(self.IN_WT, "git status")))

    # -- doctor, the board and the SessionStart context -----------------------------------------------
    def test_doctor_names_every_finding_with_what_to_do_and_none_when_clean(self):
        proc, report = self.doctor()
        self.assertEqual(report["repositories"]["findings"], [], report["repositories"])
        self.assertLessEqual({str(self.bad), str(self.bad_wt)}, set(report["repositories"]["checkouts"]), report["repositories"])
        self.assertFalse([p for p in report["problems"] if "repository" in p], report["problems"])
        hook = self.plant_hook()
        self.plant_worktree_key(local="[core]\n\thooksPath = /tmp/planted-hooks\n")
        proc, report = self.doctor()
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        found = {(f["kind"], f["what"]) for f in report["repositories"]["findings"]}
        self.assertLessEqual({("hook", str(hook)), ("key", "core.hookspath"), ("key", "core.fsmonitor")}, found, found)
        for needle in (str(hook), "core.hookspath", "core.fsmonitor"):
            with self.subTest(needle):
                named = [p for p in report["problems"] if needle in p]
                self.assertEqual(len(named), 1, report["problems"])  # once, though the checkout and its worktree share it
                self.assertIn("is Eric's call", named[0])
        self.assertIn(str(self.wt_gitdir / "config.worktree"), " ".join(report["problems"]))  # the worktree scope's file
        text = self.cli("doctor", check=False)
        self.assertEqual(text.returncode, EXIT_ERROR)
        self.assertIn(str(hook), text.stderr)
        hook.unlink()
        self.plant_local("")
        (self.wt_gitdir / "config.worktree").unlink()
        gitfile = self.bad_wt / ".git"
        own = gitfile.read_text(encoding="utf-8")
        gitfile.write_text("gitdir: %s\n" % (self.make_repo("spud-elsewhere-") / ".git"), encoding="utf-8")
        proc, report = self.doctor()
        self.assertEqual([(f["kind"], f["checkout"]) for f in report["repositories"]["findings"]], [("foreign", str(self.bad_wt))])
        self.assertIn(GIT_NESTED_WORDING, " ".join(report["problems"]))
        gitfile.write_text(own, encoding="utf-8")
        proc, report = self.doctor()
        self.assertEqual(report["repositories"]["findings"], [])

    def test_the_board_and_every_spud_sessions_context_carry_one_line_naming_the_checkout(self):
        def brief():
            return self.cli("board", "--brief").stdout.rstrip("\n").split("\n")

        def contexts():
            return {s.label: self.hook_in(s, "SessionStart", self.session_start_p(s)).context for s in (self.HOME, self.CLAIMED, self.PLAIN)}

        self.assertFalse([line for line in brief() if line.startswith(PLANTED_LINE)])
        self.assertFalse([label for label, c in contexts().items() if PLANTED_LINE in c])
        self.plant_hook()
        lines = [line for line in brief() if line.startswith(PLANTED_LINE)]
        self.assertEqual(len(lines), 1, brief())
        self.assertIn(str(self.bad), lines[0])
        self.assertIn("spud doctor", lines[0])
        shown = contexts()
        for label in (self.HOME.label, self.CLAIMED.label):
            with self.subTest(session=label):
                head = shown[label].split("\nLedger board (", 1)[0].split("\n")
                self.assertIn(lines[0], head)  # in the head, above the board, where no cut reaches it
        self.assertNotIn(PLANTED_LINE, shown[self.PLAIN.label])  # Eric's own session: its one-line notice, as before


class ArgumentWriteProjectTest(ProjectHookCase):
    """SPD-121: a write by argument answers every caller as a redirection does (PathTableTest's `Bash cp` column), and here
    each command's representative line is pinned for the callers that are not a member: a plain session and its own
    subagents write freely in badtakes and nowhere in Spud's home, and Spud is held to Law 1 in a project and free outside
    every project.  A member of BAD-001 with `src/**` is held to its globs in the worktree the ticket is bound to."""

    FORMS = ("cp /dev/null {}", "mv /tmp/spd-121-src {}", "ln -s /tmp/x {}", "install /dev/null {}", "mkdir -p {}", "touch {}", "rm -f {}",
             "rmdir {}", "truncate -s 0 {}", "chmod +x {}", "chown nobody {}", "sed -i '' s/a/b/ {}")

    def run_form(self, s, form, path, agent_id=None):
        return self.hook_in(s, "PreToolUse", self.bash_p(s, form.replace("{}", str(path)), agent_id))

    def test_each_command_for_a_plain_session_spud_and_a_member(self):
        home, wt, out = self.home.path, self.bad_wt, self.outside
        self.spawn_in(self.CLAIMED, self.plan_bad(name="Russet"), AGENT_A)
        cells = (
            # session, agent_id, path, expected needle (None: silent)
            (self.PLAIN, None, wt / "src" / "a.txt", None),
            (self.PLAIN, None, wt / "README.md", None),
            (self.PLAIN, None, home / "CLAUDE.md", NOT_SPUD),
            (self.PLAIN, None, home / "ledger" / "x.md", LAW_5),
            (self.PLAIN, None, out / "x.txt", None),
            (self.PLAIN, AGENT_D, wt / "README.md", None),  # Eric's own subagent
            (self.PLAIN, AGENT_D, home / "CLAUDE.md", NOT_SPUD),
            (self.CLAIMED, None, wt / "src" / "a.txt", LAW_1),
            (self.CLAIMED, None, home / "CLAUDE.md", None),
            (self.CLAIMED, None, out / "x.txt", None),
            (self.HOME, None, wt / "src" / "a.txt", LAW_1),
            (self.HOME, None, home / "reports" / "x.md", LAW_5),
            (self.CLAIMED, AGENT_A, wt / "src" / "a.txt", None),
            (self.CLAIMED, AGENT_A, wt / "README.md", LAW_5),
            (self.CLAIMED, AGENT_A, self.bad / "src" / "a.txt", BOUND_WORKTREE),
            (self.CLAIMED, AGENT_A, wt / "src" / ".git" / "index", GIT_DIR),
        )
        for form in self.FORMS:
            for s, agent_id, path, expected in cells:
                with self.subTest(form=form, session=s.label, agent_id=agent_id, path=str(path)):
                    r = self.run_form(s, form, path, agent_id)
                    if expected is None:
                        self.assertHookSilent(r)
                    else:
                        self.assertDenied(r, expected)
                        self.assertIn("a write by argument", r.reason)


class DirectoryWriteProjectTest(ProjectHookCase):
    """SPD-129: a member makes, and removes, the directory its own deliverable glob covers, and a bare glob opens that
    directory in its own project's bound worktree alone.  A member of BAD-001 holds `src/**` and `home:docs/x.md`: it may
    `mkdir -p src` and `rm -rf src` in the worktree BAD-001 is bound to, and nowhere else -- not the same path in the main
    checkout (SPD-098), not the home's own `src`, and not `docs`, whose glob names a file in another scope."""

    def setUp(self):
        super().setUp()
        self.spawn_in(self.CLAIMED, self.plan_bad(name="Russet"), AGENT_A)

    def run_line(self, s, line, agent_id=AGENT_A):
        return self.hook_in(s, "PreToolUse", self.bash_p(s, line, agent_id))

    def test_the_bound_worktrees_own_directory_is_the_members(self):
        for line in ("mkdir -p src", "mkdir -p src/lib", "install -d src", "rm -rf src", "rmdir src", "rm -r src/lib"):
            with self.subTest(line):
                self.assertHookSilent(self.run_line(self.CLAIMED, "cd %s && %s" % (self.bad_wt, line)), line)

    def test_the_same_directory_elsewhere_is_not(self):
        for cwd, line, needle in ((self.bad, "mkdir -p src", BOUND_WORKTREE), (self.bad, "rm -rf src", BOUND_WORKTREE),
                                  (self.home.path, "mkdir -p src", LAW_5), (self.home.path, "rm -rf src", LAW_5),
                                  (self.bad_wt, "mkdir -p docs", LAW_5), (self.bad_wt, "rm -rf .", LAW_5),
                                  (self.home.path, "rm -rf docs", LAW_5), (self.home.path, "rm -rf ledger", LAW_5)):
            with self.subTest(cwd=str(cwd), line=line):
                self.assertDenied(self.run_line(self.CLAIMED, "cd %s && %s" % (cwd, line)), needle, line)
        # `home:docs/x.md` names a file in the home, so the home's `docs` is the directory that glob covers -- and only the
        # home's: the same name in badtakes is no glob of this member's (the cell above).
        self.assertHookSilent(self.run_line(self.CLAIMED, "cd %s && mkdir -p docs" % self.home.path))


CHECKOUT_WORDING = "the root of a checkout the ledger knows"  # SPD-126: a whole-subtree write at or above a checkout's root


class TreeWriteProjectTest(ProjectHookCase):
    """SPD-126: a whole-subtree write -- what find deletes or runs, an archive extracted, a tree synced or copied -- in a
    project's bound worktree.  BAD-001's members: AGENT_A holds `src/**`, AGENT_B `**`.  A subtree `src/**` covers is
    AGENT_A's in the worktree BAD-001 is bound to and nowhere else; the worktree's root is no glob's subtree for AGENT_A,
    and for AGENT_B, whose `**` covers it, it holds the worktree's .git gitfile, as the directory above both checkouts
    holds both: SPD-066's rule refuses the write before the globs are asked.  A copied source holding a git directory is
    refused wherever it lands."""

    def setUp(self):
        super().setUp()
        self.spawn_in(self.CLAIMED, self.plan_bad(name="Russet", deliverables=("src/**",)), AGENT_A)
        self.spawn_in(self.CLAIMED, self.plan_bad(name="Yukon", deliverables=("**",)), AGENT_B)
        for checkout in (self.bad, self.bad_wt):
            (Path(checkout) / "src" / "plain").mkdir(parents=True, exist_ok=True)
            (Path(checkout) / "a.tar").write_text("a\n", encoding="utf-8")
        plant_git_dir(Path(self.bad_wt) / "src" / "fake" / ".git")

    def run_line(self, s, cwd, line, agent_id=AGENT_A):
        return self.hook_in(s, "PreToolUse", self.bash_p(s, "cd %s && %s" % (cwd, line), agent_id))

    def test_the_members_own_subtree_in_the_bound_worktree(self):
        for line in ("find src -name '*.pyc' -delete", "find src/plain -exec touch {} +", "tar -xf a.tar -C src",
                     "rsync -a src/plain/ src/copy/", "cp -R src/plain src/copy", "rm -rf src/plain", "unzip -o a.zip -d src/z"):
            with self.subTest(line):
                self.assertHookSilent(self.run_line(self.CLAIMED, self.bad_wt, line), line)

    def test_the_root_the_main_checkout_and_a_copied_git_directory_are_refused(self):
        for cwd, line, needle, agent in ((self.bad_wt, "find . -delete", LAW_5, AGENT_A), (self.bad_wt, "tar -xf a.tar", LAW_5, AGENT_A),
                                         (self.bad_wt, "find . -delete", CHECKOUT_WORDING, AGENT_B),
                                         (self.bad_wt, "tar -xf a.tar", CHECKOUT_WORDING, AGENT_B),
                                         (self.bad_wt, "find %s -delete" % os.path.dirname(self.bad), CHECKOUT_WORDING, AGENT_B),
                                         (self.bad, "find src -delete", BOUND_WORKTREE, AGENT_A),
                                         (self.bad, "tar -xf a.tar -C src", BOUND_WORKTREE, AGENT_A),
                                         (self.bad_wt, "cp -R src/fake src/copy", GIT_DIR, AGENT_A),
                                         (self.bad_wt, "find src -exec touch {} +", GIT_DIR, AGENT_A)):  # src holds src/fake/.git
            with self.subTest(cwd=str(cwd), line=line, agent=agent):
                self.assertDenied(self.run_line(self.CLAIMED, cwd, line, agent), needle, line)

    def test_spud_and_a_plain_session_keep_their_readings(self):
        self.assertDenied(self.run_line(self.CLAIMED, self.bad_wt, "find src -delete", None), LAW_1)  # Spud: every path a deliverable
        self.assertHookSilent(self.run_line(self.PLAIN, self.bad_wt, "find src -delete", None))  # Eric's own session


class StopProjectTest(ProjectHookCase):
    """A member with no known session holds every Spud session once it has waited ten minutes (SPD-018); a plain session
    it never holds."""

    def setUp(self):
        super().setUp()
        self.sessionless = self.cli_json("member", "new", "--ticket", self.t["key"], "--persona", "scout", "--model", "haiku", "--brief", "Do the thing.",
                                         "--deliverable", "tests/**", actor="spud", session=None)["member"]
        self.assertIsNone(self.home.scalar("SELECT session_id FROM members WHERE id = ?", self.sessionless["id"]))
        self.set_member(self.sessionless["id"], planned_at=ago(11))

    def test_a_plain_session_is_never_held(self):
        for s in (self.PLAIN, self.PLAIN._replace(label="plain, cwd in the worktree", cwd=self.bad_wt)):
            with self.subTest(session=s.label):
                self.assertHookSilent(self.hook_in(s, "Stop", self.stop_p(s)))
        self.assertEqual(self.denied(), [])

    def test_a_spud_session_is_held_as_today(self):
        outside = Session("outside", self.outside, SESSION_PLAIN, self.outside, None)
        for s in (self.CLAIMED, self.HOME, outside):
            with self.subTest(session=s.label):
                r = self.hook_in(s, "Stop", self.stop_p(s))
                self.assertEqual((r.code, r.json and r.json.get("decision")), (0, "block"), r)
                self.assertIn(self.sessionless["ref"], r.json["reason"])


# =============================================================================
# Section 6.4: the failure policy of a project's hook lines
# =============================================================================


class FailurePolicyProjectTest(ProjectHookCase):
    def break_database(self):
        con = self.home.connect()
        con.execute("PRAGMA user_version = 99")
        con.close()

    def pre_tool_payloads(self, s, agent_id):
        return (("Bash", self.bash_p(s, "ls", agent_id)), ("Write", self.edit_p(s, self.bad / "src" / "a.txt", agent_id)))

    def test_a_project_line_fails_closed_only_for_a_call_with_an_agent_id(self):
        self.break_database()
        for tool, payload in self.pre_tool_payloads(self.PLAIN, AGENT_A):
            with self.subTest(tool=tool, agent_id=AGENT_A):
                r = self.hook_in(self.PLAIN, "PreToolUse", payload)
                self.assertEqual((r.code, r.stdout), (2, ""), r)
        for tool, payload in self.pre_tool_payloads(self.PLAIN, None):
            with self.subTest(tool=tool, agent_id=None):
                r = self.hook_in(self.PLAIN, "PreToolUse", payload)
                self.assertEqual((r.code, r.stdout, r.decision), (0, "", None), r)
        self.assertTrue(self.home.spool.exists())  # the gap is spooled, as the recording hooks' is

    def test_a_line_without_project_keeps_todays_policy(self):
        self.break_database()
        for s in (self.HOME, self.PLAIN._replace(label="badtakes launch, a home line", project=None)):
            for agent_id in (AGENT_A, None):
                for tool, payload in self.pre_tool_payloads(s, agent_id):
                    with self.subTest(session=s.label, tool=tool, agent_id=agent_id):
                        r = self.hook_in(s, "PreToolUse", payload)
                        self.assertEqual((r.code, r.stdout), (2, ""), r)
                        self.assertIn("failing closed", r.stderr)

    def test_recording_hooks_on_a_project_line_still_fail_open(self):
        self.break_database()
        for event, payload in (("PostToolUse", self.post_p(self.PLAIN, "toolu_x", AGENT_A, "x")), ("SubagentStart", self.start_p(self.PLAIN, AGENT_A)),
                               ("SubagentStop", self.sub_stop_p(self.PLAIN, AGENT_A)), ("SessionStart", self.session_start_p(self.PLAIN))):
            with self.subTest(event=event):
                r = self.hook_in(self.PLAIN, event, payload)
                self.assertEqual((r.code, r.stdout), (0, ""), r)
