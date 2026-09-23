"""SPD-098: ticket-bound worktrees (the home and tool split design's section 6, approved by Eric on 2026-09-16 as a refusal,
not a warning).

A code ticket -- one whose member's deliverables resolve into a checkout of its project, a bare or `<key>:` glob -- is bound
to one linked worktree of that project by the first `member new` that needs one, run from inside it.  `member new` refuses
(exit 5, writing nothing) from the project's main checkout, from outside the project, and from a worktree other than the
bound one while that one exists; a binding whose worktree is gone is stale, and Spud's next plan from a worktree rebinds it.
A member binds an unbound ticket as Spud does and never rebinds one.  A `<key>:` glob naming another project is refused.
The edit hook and the Bash hook's write targets then hold a member of a bound ticket to that worktree; a ticket left unbound
by members planned before this landed keeps the rule it had.  `spud card` and `spud board` show the worktree and its branch.

Every repository is a scratch one beside a scratch home (tests/helpers.py RepoMixin): project spud's root is moved to a
scratch main checkout, with a linked worktree under its .claude/worktrees/ (EnterWorktree's spelling) and one elsewhere.
"""

import json
import os
import shutil
import unittest

from helpers import EXIT_OK, EXIT_TRANSITION, REPO, RepoMixin, SpudTestCase, git, load_spud_module
from test_hooks import AGENT_A, AGENT_B, AGENT_C, SESSION, HookCase

BRIEF = "Build it."
BOUND_WORDING = "a member of a bound ticket writes its project's paths there alone"  # the refusal outside the bound worktree


class BoundCase(RepoMixin, HookCase):
    """HookCase's scratch home and its active SPD-001, with project spud's root moved to a scratch repository `tool` that has
    two linked worktrees: `wt` under .claude/worktrees/ and `elsewhere` beside it."""

    def setUp(self):
        super().setUp()
        self.tool = self.make_repo("tool-")
        self.cli("project", "edit", "spud", "--root", self.tool, actor="spud")
        self.wt = self.add_worktree(self.tool, "spd-001-hooks", inside=True)
        self.elsewhere = self.add_worktree(self.tool, "spd-001-elsewhere")

    def plan_in(self, cwd, deliverable=("bin/**",), actor="spud", ticket=None, persona="engineer", model="opus", check=True, **kw):
        """`member new` run from `cwd` in SESSION; the CompletedProcess."""
        args = ["member", "new", "--persona", persona, "--model", model, "--brief", BRIEF]
        if actor == "spud":
            args += ["--ticket", ticket or self.t["key"]]
        for glob in deliverable:
            args += ["--deliverable", glob]
        for k, v in kw.items():
            args += ["--" + k.replace("_", "-"), v]
        return self.cli("--json", *args, actor=actor, cwd=cwd, session=SESSION, check=check)

    def planned(self, cwd, **kw):
        return json.loads(self.plan_in(cwd, **kw).stdout)["member"]

    def refused(self, cwd, needles=(), **kw):
        """A plan refused with exit 5 that wrote nothing: no member row, no binding, no ticket.worktree event."""
        members = self.home.scalar("SELECT count(*) FROM members")
        bindings = self.home.rows("SELECT key, worktree FROM tickets ORDER BY id")
        events = self.home.scalar("SELECT count(*) FROM events WHERE kind = 'ticket.worktree'")
        proc = self.plan_in(cwd, check=False, **kw)
        self.assertEqual(proc.returncode, EXIT_TRANSITION, proc)
        for needle in needles:
            self.assertIn(str(needle), proc.stderr)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM members"), members)
        self.assertEqual(self.home.rows("SELECT key, worktree FROM tickets ORDER BY id"), bindings)
        self.assertEqual(self.home.scalar("SELECT count(*) FROM events WHERE kind = 'ticket.worktree'"), events)
        return proc.stderr

    def worktree_of(self, key=None):
        return self.home.scalar("SELECT worktree FROM tickets WHERE key = ?", key or self.t["key"])

    def worktree_events(self, key=None):
        return [e for e in self.events("ticket.worktree") if e["ticket"] == (key or self.t["key"])]

    def remove_worktree(self, path):
        git(self.tool, "worktree", "remove", "--force", path)
        self.assertFalse(os.path.exists(path))


# =============================================================================
# Binding at member new
# =============================================================================


class BindingTest(BoundCase):
    def test_the_main_checkout_is_refused_naming_the_step_and_nothing_is_written(self):
        err = self.refused(self.tool, ("EnterWorktree name: spd-001-<slug>", "main checkout", self.tool))
        self.assertIn("rerun", err)
        (self.tool / "bin").mkdir()
        self.refused(self.tool / "bin", ("main checkout",))  # a directory inside it too

    def test_a_linked_worktree_binds_the_ticket_with_an_event(self):
        (self.wt / "bin").mkdir()
        m = self.planned(self.wt / "bin")  # from a directory inside the worktree: its top level is what binds
        self.assertEqual(m["status"], "planned")
        self.assertEqual(self.worktree_of(), os.path.realpath(self.wt))
        [event] = self.worktree_events()
        self.assertEqual(event["actor"], "spud")
        self.assertEqual((event["data"]["worktree"], event["data"]["previous"], event["data"]["project"]), (os.path.realpath(self.wt), None, "spud"))
        self.assertIn(str(os.path.realpath(self.wt)), event["body"])
        self.planned(self.wt)  # a second plan from the bound worktree binds nothing again
        self.assertEqual(len(self.worktree_events()), 1)
        self.assertEqual(self.home.json("ticket", "show", self.t["key"])["ticket"]["worktree"], os.path.realpath(self.wt))

    def test_a_worktree_elsewhere_binds_as_one_under_claude_worktrees_does(self):
        self.planned(self.elsewhere)
        self.assertEqual(self.worktree_of(), os.path.realpath(self.elsewhere))

    def test_the_home_is_refused_naming_the_project_root(self):
        err = self.refused(self.home.path, ("project root %s" % self.tool, "Spud's home"))
        self.assertIn("EnterWorktree", err)

    def test_another_repository_and_no_repository_are_refused_naming_the_project_root(self):
        other = self.make_repo("other-")
        other_wt = self.add_worktree(other, "spd-001-other")
        for cwd, where in ((other, "another repository"), (other_wt, "another repository"), (self.scratch_dir("plain-"), "no git repository")):
            with self.subTest(cwd=str(cwd)):
                self.refused(cwd, ("project root %s" % self.tool, where))

    def test_a_second_worktree_is_refused_while_the_bound_one_exists(self):
        self.planned(self.wt)
        err = self.refused(self.elsewhere, ("bound to the worktree %s" % os.path.realpath(self.wt), "branch worktree-spd-001-hooks"))
        self.assertIn("plan from that worktree", err)
        self.refused(self.tool, ("main checkout",))

    def test_a_stale_binding_is_rebound_with_the_old_path_in_the_event(self):
        self.planned(self.wt)
        old = os.path.realpath(self.wt)
        self.remove_worktree(self.wt)
        self.planned(self.elsewhere)
        self.assertEqual(self.worktree_of(), os.path.realpath(self.elsewhere))
        first, second = self.worktree_events()
        self.assertEqual((second["data"]["worktree"], second["data"]["previous"]), (os.path.realpath(self.elsewhere), old))
        self.assertIn("rebound", second["body"])

    def test_a_binding_whose_directory_is_gone_but_git_still_lists_it_is_stale(self):
        self.planned(self.wt)
        shutil.rmtree(self.wt)  # no `git worktree remove`: git lists it as prunable until a prune
        self.assertIn(os.path.realpath(self.wt), git(self.tool, "worktree", "list", "--porcelain"))
        self.planned(self.elsewhere)
        self.assertEqual(self.worktree_of(), os.path.realpath(self.elsewhere))

    def test_a_batch_binds_two_tickets_to_one_worktree(self):
        second = self.new_ticket("Batched", status="active")
        self.planned(self.wt)
        self.planned(self.wt, ticket=second["key"])
        self.assertEqual((self.worktree_of(), self.worktree_of(second["key"])), (os.path.realpath(self.wt),) * 2)

    def test_home_globs_and_a_contractor_without_deliverables_need_no_worktree(self):
        self.planned(self.home.path, deliverable=("home:docs/x.md",), persona="writer", model="sonnet")
        self.planned(self.tool, deliverable=(), persona="contractor", model="haiku", agent_type="Explore")
        self.assertIsNone(self.worktree_of())
        self.refused(self.home.path, ("project root",), deliverable=("home:docs/**", "tests/**"))  # one bare glob is enough
        self.planned(self.wt, deliverable=("spud:tests/**",))  # the ticket's own project by its key binds as a bare glob does
        self.assertEqual(self.worktree_of(), os.path.realpath(self.wt))

    def test_a_contractor_follows_the_rule_by_its_deliverables(self):
        self.refused(self.tool, ("main checkout",), deliverable=("bin/**",), persona="contractor", model="haiku", agent_type="Explore")
        self.planned(self.wt, deliverable=("bin/**",), persona="contractor", model="haiku", agent_type="Explore")
        self.assertEqual(self.worktree_of(), os.path.realpath(self.wt))

    def test_a_glob_naming_another_project_is_refused(self):
        bad = self.make_repo("badtakes-")
        self.add_project(bad)
        err = self.refused(self.wt, ("project badtakes", "a ticket there"), deliverable=("bin/**", "badtakes:src/**"))
        self.assertIn("badtakes:src/**", err)
        self.refused(self.home.path, ("project badtakes",), deliverable=("home:docs/**", "badtakes:src/**"))

    def test_member_edit_holds_new_deliverables_to_the_same_rule(self):
        m = self.planned(self.home.path, deliverable=("home:docs/x.md",), persona="writer", model="sonnet")
        proc = self.cli("member", "edit", m["ref"], "--deliverable", "bin/**", actor="spud", cwd=self.tool, session=SESSION, check=False)
        self.assertEqual(proc.returncode, EXIT_TRANSITION, proc)
        self.assertIn("main checkout", proc.stderr)
        self.assertEqual(self.home.json("member", "show", m["ref"])["member"]["deliverables"], ["home:docs/x.md"])
        self.cli("member", "edit", m["ref"], "--deliverable", "bin/**", actor="spud", cwd=self.wt, session=SESSION)
        self.assertEqual(self.worktree_of(), os.path.realpath(self.wt))


class MemberBindingTest(BoundCase):
    """Spud's decision 1: a member's `member new` binds an unbound ticket exactly as Spud's does, and never rebinds one."""

    def lead(self, cwd, deliverable=("home:docs/x.md",)):
        m = self.planned(cwd, deliverable=deliverable, persona="engineer", model="opus")
        return m["ref"]

    def test_a_member_binds_an_unbound_ticket(self):
        lead = self.lead(self.home.path)
        self.assertIsNone(self.worktree_of())
        self.plan_in(self.wt, actor=lead, persona="scout", model="haiku")
        self.assertEqual(self.worktree_of(), os.path.realpath(self.wt))
        [event] = self.worktree_events()
        self.assertEqual(event["actor"], "member:%s" % self.home.json("member", "show", lead)["member"]["id"])

    def test_a_member_inherits_the_binding_and_refuses_from_anywhere_else(self):
        lead = self.lead(self.wt, deliverable=("bin/**",))
        self.plan_in(self.wt, actor=lead, persona="scout", model="haiku")
        self.assertEqual(len(self.worktree_events()), 1)
        err = self.refused(self.elsewhere, ("bound to the worktree %s" % os.path.realpath(self.wt),), actor=lead, persona="scout", model="haiku")
        self.assertIn("member never rebinds", err)
        self.refused(self.tool, ("main checkout",), actor=lead, persona="scout", model="haiku")

    def test_a_member_never_rebinds_a_stale_binding_and_spud_does(self):
        lead = self.lead(self.wt, deliverable=("bin/**",))
        self.remove_worktree(self.wt)
        err = self.refused(self.elsewhere, ("was bound to the worktree %s" % os.path.realpath(self.wt), "Spud rebinds"), actor=lead, persona="scout", model="haiku")
        self.assertIn("member never rebinds", err)
        self.planned(self.elsewhere, deliverable=("tests/**",))
        self.assertEqual(self.worktree_of(), os.path.realpath(self.elsewhere))
        self.plan_in(self.elsewhere, actor=lead, persona="scout", model="haiku")


# =============================================================================
# Enforcement in the hooks
# =============================================================================


class EnforcementTest(BoundCase):
    """A member of SPD-001, bound to `wt`, with the globs `bin/**` (the ticket's project) and `home:docs/**` (the home)."""

    def setUp(self):
        super().setUp()
        self.bound = os.path.realpath(self.wt)
        self.member = self.spawn(self.planned(self.wt, deliverable=("bin/**", "home:docs/**")), AGENT_A)
        self.assertEqual((self.member["status"], self.member["agent_id"]), ("active", AGENT_A))

    def write(self, path, agent_id=AGENT_A):
        return self.home.hook("PreToolUse", self.pre_edit(path, agent_id=agent_id))

    def bash(self, command, agent_id=AGENT_A, cwd=None):
        return self.home.hook("PreToolUse", self.pre_bash(command, agent_id=agent_id, cwd=str(cwd) if cwd else None))

    def assertAllowed(self, r, what=None):
        self.assertEqual((r.code, r.decision), (0, None), (what, r))

    def assertOutsideTheWorktree(self, r, what=None):
        self.assertEqual((r.code, r.decision), (0, "deny"), (what, r))
        self.assertIn("bound to %s" % self.bound, r.reason, what)
        self.assertIn(BOUND_WORDING, r.reason, what)

    def unbind(self):
        """The ticket as a member planned before SPD-098 left it: unbound."""
        con = self.home.connect()
        try:
            with con:
                con.execute("UPDATE tickets SET worktree = NULL WHERE key = ?", (self.t["key"],))
        finally:
            con.close()

    def test_the_edit_hook_holds_the_member_to_the_bound_worktree(self):
        self.assertAllowed(self.write(self.wt / "bin" / "x.py"))
        self.assertAllowed(self.write(self.home.path / "docs" / "x.md"))  # a home: glob is unchanged
        for checkout in (self.tool, self.elsewhere):
            with self.subTest(checkout=str(checkout)):
                r = self.write(checkout / "bin" / "x.py")
                self.assertOutsideTheWorktree(r)
                self.assertIn("Write the same path under %s" % self.bound, r.reason)
                self.assertOutsideTheWorktree(self.write(checkout / "README.md"))
        r = self.write(self.wt / "README.md")
        self.assertEqual(r.decision, "deny")
        self.assertIn("not among", r.reason)  # inside the worktree the globs decide, as before

    def test_spuds_own_writes_are_unchanged(self):
        for path in (self.tool / "bin" / "x.py", self.wt / "bin" / "x.py", self.elsewhere / "bin" / "x.py"):
            with self.subTest(path=str(path)):
                r = self.write(path, agent_id=None)
                self.assertEqual(r.decision, "deny")
                self.assertIn("Law 1", r.reason)
                self.assertNotIn(BOUND_WORDING, r.reason)
        self.assertAllowed(self.write(self.home.path / "CLAUDE.md", agent_id=None))

    def test_the_bash_hook_holds_redirections_and_git_write_targets_to_the_bound_worktree(self):
        for checkout in (self.tool, self.elsewhere):
            with self.subTest(checkout=str(checkout)):
                r = self.bash("echo x > %s" % (checkout / "bin" / "x.py"))
                self.assertOutsideTheWorktree(r)
                self.assertIn("redirection", r.reason)
                r = self.bash("git diff --output=%s" % (checkout / "bin" / "d.txt"), cwd=self.wt)
                self.assertOutsideTheWorktree(r)
                self.assertIn("git call writes", r.reason)
        self.assertAllowed(self.bash("echo x > %s" % (self.wt / "bin" / "x.py")))
        self.assertAllowed(self.bash("git diff --output=%s" % (self.wt / "bin" / "d.txt"), cwd=self.wt))
        self.assertAllowed(self.bash("git -C %s status" % self.tool, cwd=self.wt))  # reading the main checkout is no write

    def test_a_ticket_left_unbound_keeps_todays_rule_until_a_plan_binds_it(self):
        """Spud's decision 2, both sides: a member alive at the deploy is not stranded, and the next plan binds its ticket."""
        self.unbind()
        for checkout in (self.tool, self.wt, self.elsewhere):
            with self.subTest(checkout=str(checkout)):
                self.assertAllowed(self.write(checkout / "bin" / "x.py"))
                self.assertAllowed(self.bash("echo x > %s" % (checkout / "bin" / "x.py")))
        self.planned(self.wt, deliverable=("tests/**",), persona="scout", model="haiku")
        self.assertEqual(self.worktree_of(), self.bound)
        self.assertOutsideTheWorktree(self.write(self.tool / "bin" / "x.py"))
        self.assertAllowed(self.write(self.wt / "bin" / "x.py"))

    def test_a_stale_binding_refuses_every_checkout_until_spud_rebinds(self):
        self.remove_worktree(self.wt)
        r = self.write(self.elsewhere / "bin" / "x.py")
        self.assertOutsideTheWorktree(r)
        self.assertIn("That worktree is gone", r.reason)
        self.planned(self.elsewhere, deliverable=("tests/**",), persona="scout", model="haiku")
        self.assertAllowed(self.write(self.elsewhere / "bin" / "x.py"))
        self.assertEqual(self.write(self.tool / "bin" / "x.py").decision, "deny")

    def test_the_subagent_start_context_names_the_bound_worktree(self):
        m = self.planned(self.wt, deliverable=("tests/**",), persona="scout", model="haiku")
        pre = self.home.hook("PreToolUse", self.pre_agent(self.description(m), model="haiku", tool_use_id="toolu_B"))
        self.assertEqual(pre.decision, "allow", pre)
        r = self.home.hook("SubagentStart", self.sub_start(AGENT_B))
        self.assertIn("your ticket is bound to its worktree `%s`" % self.bound, r.context)
        self.assertIn("main checkout `%s`" % self.tool, r.context)
        self.assertNotIn("or a worktree of it", r.context)


# =============================================================================
# Display: card and board
# =============================================================================


class DisplayTest(BoundCase):
    """The worktree and its branch are read from git when `card` or `board` runs, never stored; a worktree that is gone is
    shown as gone, never an error."""

    def setUp(self):
        super().setUp()
        self.bound = os.path.realpath(self.wt)

    def card(self):
        proc = self.home.run("card", self.t["key"])
        self.assertEqual(proc.returncode, EXIT_OK, proc)
        return proc.stdout, self.home.json("card", self.t["key"])

    def test_an_unbound_ticket_shows_no_worktree(self):
        text, data = self.card()
        self.assertNotIn("worktree", text)
        self.assertIsNone(data["worktree"])
        self.assertNotIn("worktrees:", self.home.run("board").stdout)

    def test_the_card_shows_a_live_a_renamed_and_a_removed_worktree(self):
        self.planned(self.wt)
        text, data = self.card()
        self.assertEqual(text.split("\n")[1], "worktree: %s (branch worktree-spd-001-hooks)" % self.bound)
        self.assertEqual(data["worktree"], {"path": self.bound, "present": True, "branch": "worktree-spd-001-hooks", "detached": None})
        git(self.wt, "branch", "-m", "feat/spd-001-renamed")  # Spud renames the branch by the project's rules
        text, data = self.card()
        self.assertIn("worktree: %s (branch feat/spd-001-renamed)\n" % self.bound, text)
        self.assertEqual(data["worktree"]["branch"], "feat/spd-001-renamed")
        self.assertEqual(self.worktree_of(), self.bound)  # the path is stored, the branch never is
        git(self.wt, "checkout", "-q", "--detach")
        self.assertIn("worktree: %s (detached at " % self.bound, self.card()[0])
        self.remove_worktree(self.wt)
        text, data = self.card()
        self.assertIn("worktree: %s (gone)\n" % self.bound, text)
        self.assertEqual(data["worktree"], {"path": self.bound, "present": False, "branch": None, "detached": None})

    def test_the_board_shows_each_open_bound_ticket_with_its_worktree(self):
        batched = self.new_ticket("Batched")
        elsewhere = self.new_ticket("Elsewhere", status="active")
        self.planned(self.wt)
        self.planned(self.wt, ticket=batched["key"])
        self.planned(self.elsewhere, ticket=elsewhere["key"])
        text = self.home.run("board").stdout
        block = text.split("\nworktrees:\n", 1)[1].split("\n")
        self.assertEqual(block[:3], ["  SPD-003  %s (branch spd-001-elsewhere)" % os.path.realpath(self.elsewhere),  # the board's order
                                     "  SPD-001  %s (branch worktree-spd-001-hooks)" % self.bound,
                                     "  SPD-002  %s (branch worktree-spd-001-hooks)" % self.bound])
        rows = {r["key"]: r for r in self.home.json("board")["tickets"]}
        self.assertEqual(rows["SPD-002"]["worktree"]["branch"], "worktree-spd-001-hooks")
        self.remove_worktree(self.elsewhere)
        self.assertIn("  SPD-003  %s (gone)" % os.path.realpath(self.elsewhere), self.home.run("board").stdout)
        self.assertNotIn("worktree", self.home.run("board", "--brief").stdout)  # SessionStart's context is unchanged


# =============================================================================
# The text that says where a code ticket is worked
# =============================================================================


class TextTest(BoundCase):
    def test_the_skill_the_claim_card_the_help_and_the_agent_name_the_refusal(self):
        spud = load_spud_module()
        ctx = spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.tool)
        needle = "`member new` refuses from the main checkout"
        self.assertIn(needle, spud.skill_markdown(ctx))
        self.cli("project", "edit", "spud", "--sessions", "claim", actor="spud")
        card = self.cli("session", "claim", actor="spud", cwd=self.wt, session=SESSION).stdout
        self.assertIn(needle, card)
        self.assertIn("board (spud):", card)  # the added line leaves the board its room
        self.assertIn("refuses from the main checkout, outside the project or another worktree",
                      " ".join(self.home.run("member", "new", "--help").stdout.split()))  # argparse wraps it
        self.assertIn("ticket worktrees: a bare glob", self.home.run("--help").stdout)
        agent = (REPO / "share" / "agents" / "spudagent.md").read_text(encoding="utf-8")  # SPW-004
        self.assertIn("A bare glob is relative to the linked worktree your ticket is bound to", agent)
        self.assertNotIn("the root or a worktree of it", agent)


class NoRepositoryTest(RepoMixin, SpudTestCase):
    """A project whose root is no git checkout has no linked worktree to bind: the suite's scratch homes, where project spud's
    root is the home itself.  Its tickets bind nothing and keep the path rule they had."""

    def test_a_bare_glob_on_a_project_without_a_repository_binds_nothing(self):
        t = self.new_ticket("Plain", status="active")
        m = self.new_member(t["key"], deliverable=["bin/**"])
        self.assertEqual(m["deliverables"], ["bin/**"])
        self.assertIsNone(self.home.scalar("SELECT worktree FROM tickets WHERE key = ?", t["key"]))


if __name__ == "__main__":
    unittest.main()
