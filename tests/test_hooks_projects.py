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

from helpers import SPUD, HookResult
from test_hooks import AGENT_A, AGENT_B, AGENT_C, AGENT_D, SESSION, TRANSCRIPT, HookCase, quote_split

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

    def expect(self, s, agent_id, label, path, expected):
        """One cell of the path table, through PreToolUse(Write) and through a Bash redirection: None is silent, a string the
        needle a refusal carries."""
        for tool, run in (("Write", self.write), ("Bash redirect", self.redirect)):
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
        return self.cli_json(*args, actor="spud", session=session)["member"]

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
    BAD-001 whose globs are `src/**` (bare: relative to badtakes, in any checkout of it) and `home:docs/x.md` (qualified:
    the home's checkout), bound in the claimed session."""

    def setUp(self):
        super().setUp()
        self.member = self.spawn_in(self.CLAIMED, self.plan_bad(name="Russet"), AGENT_A)
        self.assertEqual((self.member["status"], self.member["agent_id"]), ("active", AGENT_A), self.member)
        home, bad, wt, out = self.home.path, self.bad, self.bad_wt, self.outside
        state = (STATE_WORDING,) * 5
        self.table = (
            # target                                              path                                  Spud     plain     member  unbound, Spud  unbound, plain
            ("badtakes root: src/a.txt",                          bad / "src" / "a.txt",                LAW_1,   None,     None,   NOT_BOUND,     None),
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
        """EnterWorktree's own spelling, .claude/worktrees/<name>/ in the badtakes root, is a checkout of badtakes too."""
        wt = self.add_worktree_inside(self.bad, "bad-001-inside")
        self.expect(self.CLAIMED, AGENT_A, "inside worktree: src/a.txt", wt / "src" / "a.txt", None)
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

    def test_a_claimed_session_is_told_it_is_spud_in_at_most_2_kb(self):
        """P2: a large additionalContext reaches the model as a 2 KB preview, so the header and the board fit in 2048 bytes
        whatever the board holds."""
        for n in range(20):
            self.cli("ticket", "new", "--project", KEY, "--status", "active", "--title",
                     "BadTakes ticket %02d with a long title that makes the board brief grow well past two kilobytes" % n, actor="spud")
        for source in ("resume", "compact", "clear"):
            with self.subTest(source=source):
                r = self.hook_in(self.CLAIMED, "SessionStart", self.session_start_p(self.CLAIMED, source))
                self.assertEqual(r.code, 0, r)
                self.assertLessEqual(len(r.context.encode("utf-8")), 2048, len(r.context.encode("utf-8")))
                self.assertIn(KEY, r.context)
                self.assertIn("you are Spud here", r.context)

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
