"""The files the tool ships for a home, share/** (SPW-001, the `spud init` design section 4.4; SPD-156 for the vault).

Nothing caught the `Board.base` that shipped a first view declaring `type: bases` -- which was not a view type in that
vault -- and a half-created trailing view; both were fixed by hand on the machine that hit them.  This module is the
guard.  It reads every shipped `.base` with a deliberately restricted block-YAML reader written here, its only user: the
subset is block mappings and sequences, `key:`, `key: value`, `- value`, `- key: value`, plain scalars to the end of the
line, quoted scalars, integers and an *empty* flow collection, and the reader *raises* on a tab, a flow collection with
anything in it, an anchor, an alias, a tag, a block scalar, a document marker, a comment, a duplicate key and any line
it cannot place.  A shipped file that reaches for a YAML feature the reader does not know therefore fails instead of
passing unchecked, which is the property the broken file needed and a permissive parser would not have given.  (`{}` and
`[]` joined the subset with SPD-156: Obsidian writes `columnNames: {}` into a view it has never renamed a column in, so
the shipped files carry it, and an empty collection cannot hide a view the reader would otherwise have read.)

**A shipped view's type is the lock's question, not this module's** (SPD-156).  `table` is Obsidian's own, and every
other type comes from a plugin -- `bases`, `notion-board` and `notion-list` are registered by extended-base, which is
why the plugin-free copy of `Board.base` broke on a vault that did not have it.  So the allowed set is `table` plus the
`views` each plugin in `share/obsidian.lock.json` names beside itself, and a bare set here would have to be kept in step
with the lock by hand.  Tied to the lock, the incident this module exists for stays caught in both directions: a view of
a type no locked plugin provides still fails, and a view whose plugin is dropped from the lock starts failing the moment
it is.

It also holds the shipped set as a whole: every `{{mark}}` under share/ is one core/shipped names and every mark it
names is used; no shipped file spells a machine's path or a person's address, rendered or not; the rendered config is
one `config_problems` passes; the couplings nothing else checks -- the `Fleet.base` view `render/teamcard` embeds in
every ticket note, the `ledger/Spud.md` that `render/notefiles` links as every root member's parent, and the folders
`render/notefiles.render_targets` writes into -- hold.
"""

import ast
import fnmatch
import json
import re
import unittest
from pathlib import Path

import helpers
from helpers import REPO, load_spud_module

spud = load_spud_module()

SHARE = REPO / "share"
NOTEFILES = REPO / "bin" / "spudlib" / "render" / "notefiles.py"
LOCK = SHARE / "obsidian.lock.json"
# Obsidian's own view type, the one no plugin provides.  Every other allowed type comes from the lock (the docstring).
BUILT_IN_VIEW_TYPE = "table"


def shipped_lock():
    """The lock the tool ships, parsed."""
    return json.loads(LOCK.read_text(encoding="utf-8"))


def view_types(lock=None):
    """The view types a shipped `.base` may use: `table`, and each locked plugin's own, named beside it in the lock.
    Adding a view of a type no locked plugin provides is the reviewed edit that writes that type into the lock."""
    return spud.view_types(shipped_lock() if lock is None else lock)
MARK_RE = re.compile(r"\{\{([^{}]*)\}\}")
# A person's address, which no shipped file may name (design 4.3: the two values init must not put in anybody's mouth).
# Not a bare `@`: the CLI's own spelling for a brief on stdin or from a file, `--brief @-` and `--brief @file`, is in the
# shipped protocol and in the shipped ticket template, and has to stay.
ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[A-Za-z]")
KEY_RE = re.compile(r"([A-Za-z_][\w.]*):(?: (.*))?$")
FLOW = {"[": "a flow sequence", "{": "a flow mapping", "&": "an anchor", "*": "an alias", "!": "a tag",
        "|": "a block scalar", ">": "a folded block scalar", "%": "a directive"}
# The bootstrap skill (SPW-001 design section 5): it ships outside share/, at project scope, because it has to reach a
# session before there is a home for share/'s own templates to be rendered into.
SPUD_INIT_SKILL = REPO / ".claude" / "skills" / "spud-init" / "SKILL.md"
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
# SPW-005: every top-level block the shipped config carries, and what reads it.  The config `spud init` writes is the one
# file a person edits expecting an effect, so a block in it with no reader is a lie the program tells; the `ledger` block
# that shipped until SPW-005 was one -- `format` read `markdown-v0` long after the ledger's format became `sqlite-v1`,
# and `tickets`, `teams`, `reports` and `spikes` moved nothing, because `render/notefiles.render_targets` and
# `commands/homeinit.DIRECTORIES` hard-code the vault's layout that `hooks/hookio.GENERATED_ROOTS`, `imports/accept`,
# `imports/bulkimport`, `commands/homemove.COPIED_DIRS` and the shipped `Board.base` and `Fleet.base` all depend on.
# The readers are named rather than found: `"ledger"` is all over `bin/` as a path segment, so any grep for a block's
# name would have passed the very block this set exists to have caught.  Adding a block is naming its reader here.
CONFIG_BLOCK_READERS = {
    "identity": "core/shipped.marks (five marks), commands/homeinit",
    "naming": "core/homeconf.Ctx.id_pad and config_problems, state/ledgerdb.sync_config_rows, state/ops (the pool)",
    "personas": "core/homeconf.Ctx.persona_tier and Ctx.personas and config_problems, state/ops",
    "limits": "core/homeconf.Ctx.limits and config_problems, commands/doctor",
    "tickets": "tickets.prefix: core/shipped.marks, state/ledgerdb.sync_config_rows, core/homeconf.config_problems, commands/doctor",
    "teams": "teams.prefix: core/shipped.marks, state/ledgerdb.sync_config_rows, core/homeconf.config_problems, commands/doctor",
    "pricing": "render/prices.price_table, core/homeconf.Ctx.pricing, commands/doctor",
}


def skill_frontmatter(text):
    """A skill's frontmatter as {key: value}, one line each -- the shape every skill in this repository uses (the
    `/spud` skill's own SKILL_HEAD, `projects/sessions.py`), not full YAML."""
    match = FRONTMATTER_RE.match(text)
    assert match is not None, "no --- frontmatter block"
    fields = {}
    for line in match.group(1).split("\n"):
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class YamlRefusal(Exception):
    """The restricted reader met something the shipped subset leaves out.  Raised, never tolerated: a reader that
    accepted everything would have accepted `type: bases`."""


def base_lines(text):
    """[(indent, content, line number)] for every line that carries anything, refusing what the subset leaves out."""
    rows = []
    for number, raw in enumerate(text.split("\n"), start=1):
        if "\t" in raw:
            raise YamlRefusal("line %d: a tab" % number)
        content = raw.strip()
        if not content:
            continue
        if content.startswith("#"):
            raise YamlRefusal("line %d: a comment" % number)
        if content.startswith("---") or content.startswith("..."):
            raise YamlRefusal("line %d: a document marker" % number)
        if raw[:1] == "%":
            raise YamlRefusal("line %d: a directive" % number)
        rows.append((len(raw) - len(raw.lstrip(" ")), content, number))
    if not rows:
        raise YamlRefusal("nothing to read")
    if rows[0][0] != 0:
        raise YamlRefusal("line %d: the first line is indented" % rows[0][2])
    return rows


def read_scalar(content, number):
    """A plain scalar to the end of the line, a single- or double-quoted scalar, an integer, or an empty flow
    collection -- `columnNames: {}`, which Obsidian writes into a view whose columns it has never renamed."""
    if content in ("{}", "[]"):
        return {} if content == "{}" else []
    if content[:1] in FLOW:
        raise YamlRefusal("line %d: %s" % (number, FLOW[content[0]]))
    if content[0] in "'\"":
        quote = content[0]
        if len(content) < 2 or content[-1] != quote or content.count(quote) != 2:
            raise YamlRefusal("line %d: a quoted scalar the reader cannot read whole" % number)
        return content[1:-1]
    if re.fullmatch(r"-?\d+", content):
        return int(content)
    return content


def split_key(content, number):
    """(key, value) for `key:` -- value the empty string -- or `key: value`.  Refuses every other shape of line."""
    match = KEY_RE.fullmatch(content)
    if match is None:
        raise YamlRefusal("line %d: %r is not `key:` or `key: value`" % (number, content))
    return match.group(1), (match.group(2) or "").strip()


def read_block(rows, at, indent):
    """(value, the next row) for the block that starts at rows[at] and is indented `indent`."""
    return read_sequence(rows, at, indent) if rows[at][1].startswith("-") else read_mapping(rows, at, indent)


def read_mapping(rows, at, indent):
    out = {}
    while at < len(rows) and rows[at][0] == indent:
        _, content, number = rows[at]
        key, value = split_key(content, number)
        if key in out:
            raise YamlRefusal("line %d: %s twice in one mapping" % (number, key))
        at += 1
        if value == "":
            if at < len(rows) and rows[at][0] > indent:
                out[key], at = read_block(rows, at, rows[at][0])
            else:
                out[key] = None
        else:
            out[key] = read_scalar(value, number)
            if at < len(rows) and rows[at][0] > indent:
                raise YamlRefusal("line %d: indented under a key that already has a value" % rows[at][2])
    if at < len(rows) and rows[at][0] > indent:
        raise YamlRefusal("line %d: indented past the mapping it is in" % rows[at][2])
    return out, at


def read_sequence(rows, at, indent):
    out = []
    while at < len(rows) and rows[at][0] == indent and rows[at][1].startswith("-"):
        _, content, number = rows[at]
        if not content.startswith("- "):
            raise YamlRefusal("line %d: %r is not `- value`" % (number, content))
        rest = content[2:].strip()
        at += 1
        if KEY_RE.fullmatch(rest) is None:  # `- value`
            out.append(read_scalar(rest, number))
            if at < len(rows) and rows[at][0] > indent:
                raise YamlRefusal("line %d: indented under a sequence entry that already has a value" % rows[at][2])
            continue
        key, value = split_key(rest, number)  # `- key:` or `- key: value`, the first key of a mapping entry
        entry = {}
        if value == "":
            if at < len(rows) and rows[at][0] > indent + 2:
                entry[key], at = read_block(rows, at, rows[at][0])
            elif at < len(rows) and rows[at][0] == indent + 2 and rows[at][1].startswith("- "):
                raise YamlRefusal("line %d: a sequence at the entry's own key column reads either way" % rows[at][2])
            else:
                entry[key] = None
        else:
            entry[key] = read_scalar(value, number)
        if at < len(rows) and rows[at][0] == indent + 2:  # the entry's remaining keys
            rest_of_entry, at = read_mapping(rows, at, indent + 2)
            for name, held in rest_of_entry.items():
                if name in entry:
                    raise YamlRefusal("line %d: %s twice in one sequence entry" % (number, name))
                entry[name] = held
        out.append(entry)
    if at < len(rows) and rows[at][0] >= indent and not rows[at][1].startswith("-"):
        raise YamlRefusal("line %d: %r is in a sequence and is not an entry" % (rows[at][2], rows[at][1]))
    return out, at


def read_base(text):
    """The parsed file, or YamlRefusal.  Every line is placed: a line left over is a line the reader cannot read."""
    rows = base_lines(text)
    value, at = read_block(rows, 0, 0)
    if at != len(rows):
        raise YamlRefusal("line %d: left unread" % rows[at][2])
    return value


def view_problems(data, types=None):
    """What is wrong with a parsed `.base` file's views, as a list of sentences; [] when there is nothing.

    `views` is a non-empty list and every view is a mapping with a non-empty `name` and a `type` the lock allows -- the
    whole of the incident this module guards: a view of a type no plugin in the vault provides, and a half-created view
    at the end with neither of the two keys every one of its siblings carries."""
    allowed = view_types() if types is None else types
    problems = []
    views = data.get("views") if isinstance(data, dict) else None
    if not isinstance(views, list) or not views:
        return ["views is not a non-empty list (%r)" % (views,)]
    for n, view in enumerate(views):
        where = "view %d" % (n + 1)
        if not isinstance(view, dict):
            problems.append("%s is not a mapping (%r)" % (where, view))
            continue
        for key in ("type", "name"):
            if not isinstance(view.get(key), str) or not view[key].strip():
                problems.append("%s has no %s (%r)" % (where, key, view.get(key)))
        if isinstance(view.get("type"), str) and view["type"] not in allowed:
            problems.append("%s has type %r, which is not one of %s" % (where, view["type"], ", ".join(sorted(allowed))))
    names = [v.get("name") for v in views if isinstance(v, dict)]
    for name in sorted({n for n in names if isinstance(n, str) and names.count(n) > 1}):
        problems.append("two views are called %r" % name)
    return problems


def shipped():
    """[(the path under share/, the file's text)] for every shipped file."""
    return [(path.relative_to(SHARE).as_posix(), path.read_text(encoding="utf-8"))
            for path in sorted(SHARE.rglob("*")) if path.is_file()]


def render_target_paths():
    """The relative paths render/notefiles.render_targets writes, read from that module's own string literals.

    The folders are hard-coded in the renderer, not read from `config.ledger` (no module reads that block: proposal 3 of
    the design), so the renderer is what a shipped `file.inFolder(…)` is checked against."""
    tree = ast.parse(NOTEFILES.read_text(encoding="utf-8"), str(NOTEFILES))
    found = {node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".md") and "/" in node.value}
    assert len(found) > 2, "the renderer's target paths were not found; these checks would pass vacuously"
    return sorted(found)


def render_target_folders():
    """The folders those paths put a file in: ledger, ledger/tickets, ledger/teams, reports."""
    folders = set()
    for path in render_target_paths():
        parts = path.split("/")[:-1]
        while parts and "%s" in parts[-1]:
            parts.pop()
        folders.add("/".join(parts))
    return folders


class RestrictedReaderTest(unittest.TestCase):
    """The reader itself: it reads the subset, and it refuses -- a guard that accepted everything would pass everything."""

    def test_it_reads_the_forms_the_subset_allows(self):
        text = ("filters:\n  and:\n    - file.hasTag(\"spudagent\")\n    - not:\n        - file.inFolder(\"ledger/_templates\")\n"
                "views:\n  - type: table\n    name: Team\n    order:\n      - id\n    columnSize:\n      note.ticket: 154\n"
                "    empty:\n")
        self.assertEqual(read_base(text), {
            "filters": {"and": ["file.hasTag(\"spudagent\")", {"not": ["file.inFolder(\"ledger/_templates\")"]}]},
            "views": [{"type": "table", "name": "Team", "order": ["id"], "columnSize": {"note.ticket": 154}, "empty": None}],
        })

    def test_it_reads_a_quoted_scalar_and_an_integer(self):
        self.assertEqual(read_base("a: '!status.containsAny(\"done\", \"declined\")'\nb: \"\"\nc: 12\nd: x y z\n"),
                         {"a": "!status.containsAny(\"done\", \"declined\")", "b": "", "c": 12, "d": "x y z"})

    def test_it_reads_an_empty_flow_collection_and_still_refuses_one_with_anything_in_it(self):
        # SPD-156: `columnNames: {}` is what Obsidian writes into a view whose columns it has never renamed, so the
        # shipped files carry it; an empty collection hides nothing the reader would otherwise have had to read.
        self.assertEqual(read_base("columnNames: {}\norder: []\n"), {"columnNames": {}, "order": []})
        for text in ("columnNames: {a: 1}\n", "order: [id]\n", "order: [ ]\n"):
            with self.assertRaises(YamlRefusal, msg=text):
                read_base(text)

    def test_it_refuses_what_the_subset_leaves_out(self):
        for what, text in (("a tab", "views:\n\t- type: table\n"),
                           ("a flow sequence", "order: [id, name]\n"),
                           ("a flow mapping", "columnNames: {note.title: 386}\n"),
                           ("an anchor", "views: &v\n"),
                           ("an alias", "views: *v\n"),
                           ("a tag", "views: !seq\n"),
                           ("a block scalar", "name: |\n  text\n"),
                           ("a folded scalar", "name: >\n  text\n"),
                           ("a document marker", "---\nviews:\n  - type: table\n"),
                           ("a comment", "# a note\nviews:\n"),
                           ("a line that is no key", "views\n"),
                           ("a key with no space after the colon", "type:table\n"),
                           ("a duplicate key", "type: table\ntype: table\n"),
                           ("a sequence entry that is not one", "views:\n  - type: table\n  name: Team\n"),
                           ("an indented line under a value", "type: table\n  name: Team\n"),
                           ("an ambiguous sequence", "views:\n  - not:\n    - a\n"),
                           ("an empty file", "\n\n")):
            with self.assertRaises(YamlRefusal, msg=what):
                read_base(text)


class ShippedBaseTest(unittest.TestCase):
    """Every shipped .base file, read and checked."""

    def bases(self):
        found = sorted(SHARE.rglob("*.base"))
        self.assertTrue(found, "no shipped .base file was found; these checks would pass vacuously")
        return found

    def test_every_shipped_base_file_reads_in_the_subset(self):
        for path in self.bases():
            try:
                read_base(path.read_text(encoding="utf-8"))
            except YamlRefusal as refusal:
                self.fail("%s: %s" % (path.relative_to(REPO), refusal))

    def test_every_view_of_every_shipped_base_file_is_a_whole_view_of_a_type_they_use(self):
        for path in self.bases():
            self.assertEqual(view_problems(read_base(path.read_text(encoding="utf-8"))), [], path.relative_to(REPO))

    def test_a_planted_view_type_and_a_truncated_view_both_fail(self):
        # The incident, in both halves, against the file as it ships: this is what the check would have caught.
        text = (SHARE / "ledger" / "Board.base").read_text(encoding="utf-8")
        planted = text.replace("- type: table\n    name: Board\n", "- type: kanban\n    name: Board\n", 1)
        self.assertNotEqual(planted, text)
        self.assertTrue(any("kanban" in problem for problem in view_problems(read_base(planted))), view_problems(read_base(planted)))
        truncated = text.rstrip("\n") + "\n  - type: table\n"
        self.assertTrue(any("no name" in problem for problem in view_problems(read_base(truncated))), view_problems(read_base(truncated)))

    def test_the_view_type_rule_is_the_lock_s_in_both_directions(self):
        # SPD-156: what makes a type allowed is that a locked plugin names it, and nothing else.  Drop extended-base's
        # own list from the lock and the shipped Board.base fails exactly as it did before the plugin was in the vault.
        lock = shipped_lock()
        self.assertEqual(view_types(lock) - {BUILT_IN_VIEW_TYPE}, {"bases", "notion-board", "notion-list"})
        data = read_base((SHARE / "ledger" / "Board.base").read_text(encoding="utf-8"))
        self.assertEqual(view_problems(data, view_types(lock)), [])
        for entry in lock["plugins"]:
            entry["views"] = []
        problems = view_problems(data, view_types(lock))
        self.assertTrue(any("'bases'" in p for p in problems), problems)
        self.assertTrue(any("'notion-board'" in p for p in problems), problems)

    def test_every_view_type_the_lock_names_is_used_by_a_shipped_base_file_or_is_a_sibling_of_one(self):
        # A type in the lock that no shipped view uses is not wrong -- extended-base registers three and the files use
        # two -- but a type must come from a plugin that is actually pinned, never from a hand-written set.
        used = {view["type"] for path in self.bases() for view in read_base(path.read_text(encoding="utf-8"))["views"]}
        self.assertTrue(used - {BUILT_IN_VIEW_TYPE}, "no shipped view uses a plugin's type; this check passes vacuously")
        self.assertLessEqual(used, view_types())

    def test_fleet_base_carries_the_view_every_ticket_note_embeds(self):
        # render/teamcard.TEAM_VIEW_EMBED is written into the ## Team section of every rendered ticket note, so a shipped
        # Fleet.base without that view would give every ticket in every new home a broken embed.
        embed = re.fullmatch(r"!\[\[(?P<file>[^#\]]+)#(?P<view>[^\]]+)\]\]", spud.TEAM_VIEW_EMBED)
        self.assertIsNotNone(embed, spud.TEAM_VIEW_EMBED)
        path = SHARE / "ledger" / embed.group("file")
        self.assertTrue(path.is_file(), "%s embeds %s, which the tool does not ship" % (spud.TEAM_VIEW_EMBED, path.relative_to(REPO)))
        views = read_base(path.read_text(encoding="utf-8"))["views"]
        self.assertIn(embed.group("view"), [view.get("name") for view in views], spud.TEAM_VIEW_EMBED)

    def test_every_folder_a_shipped_base_file_filters_on_is_one_the_renderer_writes(self):
        allowed = render_target_folders() | {"ledger/_templates"}  # where the renderer writes, and the templates it skips
        for path in self.bases():
            for folder in re.findall(r"file\.inFolder\(\"([^\"]*)\"\)", path.read_text(encoding="utf-8")):
                self.assertIn(folder, allowed, path.relative_to(REPO))


class ShippedMarkTest(unittest.TestCase):
    """The marks: core/shipped names every one the files use, the files use every one it names, and a rendered file
    keeps none of them and names no machine."""

    @classmethod
    def setUpClass(cls):
        cls.home = helpers.Home()
        cls.ctx = spud.Ctx(cls.home.path, "SPUD_HOME", False, tool=REPO)

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def values(self):
        """This home's marks, with every path and the project replaced by ones that name no machine, so the rendered text
        can be read for a machine's absolute path wherever the suite runs."""
        marks = spud.marks(self.ctx, project={"key": "myrepo", "root_path": "/nowhere/myrepo"})
        marks.update(home="/nowhere/SpudHome", tool="/nowhere/Spud", launcher="/nowhere/Spud/bin/spud",
                     memory_dir="~/.claude/projects/-nowhere-SpudHome/memory")
        return marks

    def test_marks_answers_every_mark_and_nothing_else(self):
        self.assertEqual(set(spud.marks(self.ctx)), set(spud.MARKS))
        empty = spud.marks(self.ctx)
        self.assertEqual([empty[key] for key in ("project_key", "project_root", "project_remote")], ["", "", "-"])
        filled = spud.marks(self.ctx, project={"key": "myrepo", "root_path": "/nowhere/myrepo"})
        self.assertEqual([filled[key] for key in ("project_key", "project_root", "project_remote")],
                         ["myrepo", "/nowhere/myrepo", "-"])
        self.assertEqual(filled["launcher"], str(REPO / "bin" / "spud"))
        self.assertEqual(filled["memory_dir"],
                         "~/.claude/projects/" + str(self.home.path).replace("/", "-").replace(".", "-") + "/memory")

    def test_every_mark_the_shipped_files_carry_is_one_core_shipped_names(self):
        used = set()
        for rel, text in shipped():
            for mark in MARK_RE.findall(text):
                self.assertIn(mark, spud.MARKS, "%s carries {{%s}}, which core/shipped does not name" % (rel, mark))
                used.add(mark)
        self.assertEqual(set(spud.MARKS) - used, set(), "a mark core/shipped names is used by no shipped file")

    def test_the_launcher_mark_install_renders_is_the_mark_table_s(self):
        self.assertEqual(spud.LAUNCHER_MARK, spud.MARK % "launcher")

    def test_a_rendered_shipped_file_keeps_no_mark_and_names_no_machine_and_nobody(self):
        values = self.values()
        for rel, text in shipped():
            rendered = spud.render(text, values)
            self.assertNotIn("{{", rendered, rel)
            self.assertNotIn("/Users/", rendered, rel)
            self.assertEqual(ADDRESS_RE.findall(rendered), [], rel)

    def test_a_shipped_file_names_no_machine_unrendered_either(self):
        for rel, text in shipped():
            self.assertNotIn("/Users/", text, rel)
            self.assertEqual(ADDRESS_RE.findall(text), [], rel)

    def test_render_leaves_the_runtime_placeholders_of_the_shipped_claude_md_alone(self):
        # <agent_id>, <lineage>, <slug> and twenty more are what Spud fills per child when he writes a brief, not marks.
        rendered = spud.render((SHARE / "CLAUDE.md").read_text(encoding="utf-8"), self.values())
        for placeholder in ("<agent_id>", "<lineage>", "<persona>", "<Name>", "<child_fan_out>", "<depth remaining>"):
            self.assertIn(placeholder, rendered)

    def test_no_shipped_file_sits_where_the_renderer_writes(self):
        # A shipped path that a render also produces would be overwritten, or would be read as a hand edit of a rendered
        # file: ledger/Projects.md is the near miss, and it is not shipped.
        patterns = [path.replace("%s", "*") for path in render_target_paths()]
        for rel, _ in shipped():
            for pattern in patterns:
                self.assertFalse(fnmatch.fnmatchcase(rel, pattern), "%s is also what the renderer writes (%s)" % (rel, pattern))

    def test_the_root_note_is_the_file_the_renderer_links_as_every_root_member_s_parent(self):
        # render/notefiles hard-codes "[[Spud]]" for a member with no parent, so the shipped root note is Spud.md and
        # links itself that way whatever identity.name says.  The same coupling as the Fleet.base Team view.
        source = NOTEFILES.read_text(encoding="utf-8")
        link = re.search(r"\"\[\[(\w+)\]\]\"", source)
        self.assertIsNotNone(link, "render/notefiles no longer hard-codes a root member's parent link")
        path = SHARE / "ledger" / (link.group(1) + ".md")
        self.assertTrue(path.is_file(), "the renderer links [[%s]]; the tool ships no %s" % (link.group(1), path.relative_to(REPO)))
        self.assertIn("[[%s]]" % link.group(1), path.read_text(encoding="utf-8"))


class ShippedConfigTest(unittest.TestCase):
    """The shipped config: rendered it is a config doctor is green on, and it is the one the suite builds every home from."""

    def test_the_rendered_config_is_one_config_problems_passes(self):
        text = (SHARE / "spud.config.json").read_text(encoding="utf-8")
        config = json.loads(spud.render(text, {"identity_name": "Tuber", "pronoun_subject": "they", "pronoun_object": "them",
                                               "pronoun_possessive": "their", "ticket_prefix": "ZZZ", "team_prefix": "ZZZS"}))
        self.assertEqual(spud.config_problems(config), [])
        self.assertIn(config["identity"]["model"], config["pricing"]["models"])  # or cost renders as NO_TABLE
        self.assertEqual((config["identity"]["name"], config["tickets"]["prefix"]), ("Tuber", "ZZZ"))

    def test_the_unrendered_template_is_json_and_is_not_a_config(self):
        # Deliberate (design 3.2): "{{ticket_prefix}}" is valid JSON and an invalid prefix, so every check above runs
        # against the rendered text and never against the file's bytes.
        problems = spud.config_problems(json.loads((SHARE / "spud.config.json").read_text(encoding="utf-8")))
        self.assertTrue(any("prefix" in problem for problem in problems), problems)

    def test_the_suite_builds_every_home_from_the_shipped_template(self):
        self.assertEqual(helpers.CONFIG, SHARE / "spud.config.json")  # no second copy of the config exists to drift from it
        self.assertFalse((REPO / "tests" / "fixtures" / "spud.config.json").exists())
        self.assertNotIn("{{", helpers.config_text())
        config = helpers.real_config()
        self.assertEqual((config["tickets"]["prefix"], config["teams"]["prefix"]), ("SPD", "SPUD"))
        self.assertEqual((config["identity"]["name"], config["identity"]["pronouns"]["object"]), ("Spud", "him"))
        self.assertEqual(spud.config_problems(config), [])

    def test_every_block_it_ships_is_one_the_program_reads(self):
        """SPW-005: no decorative key in the file every home starts from.  The `ledger` block is gone, and a block added
        without a reader named in CONFIG_BLOCK_READERS fails here.  A home whose own config still carries `ledger` is a
        separate promise, kept by `tests/test_init.UnknownConfigBlockTest`: nothing validates a config's key set, so an
        extra block in a home is harmless and is never migrated away."""
        config = json.loads((SHARE / "spud.config.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(config), sorted(CONFIG_BLOCK_READERS))
        self.assertNotIn("ledger", config)
        self.assertNotIn("markdown-v0", (SHARE / "spud.config.json").read_text(encoding="utf-8"))


class ShippedSetTest(unittest.TestCase):
    """What the tool ships, as a set: the eight files of the `spud init` design's section 4, the spudagent definition
    SPW-004 moved in beside them, and since SPD-156 the Obsidian vault -- the settings, each turned-on plugin's own
    data, and the lock.  Nothing that is state, and nothing a person's window layout or a plugin's code."""

    def test_the_shipped_set_is_the_files_a_home_needs(self):
        self.assertEqual([rel for rel, _ in shipped()], [
            "CLAUDE.md",
            "agents/spudagent.md",  # SPW-004: shipped, but rendered to user scope by install, never into a home
            "ledger/Board.base",
            "ledger/Fleet.base",
            "ledger/Home.md",
            "ledger/Spud.md",
            "ledger/_templates/spudagent.md",
            "ledger/_templates/ticket.md",
            "obsidian/app.json",
            "obsidian/appearance.json",
            "obsidian/community-plugins.json",
            "obsidian/core-plugins.json",
            "obsidian/graph.json",
            "obsidian/page-preview.json",
            "obsidian/plugins/pretty-properties/data.json",
            "obsidian/types.json",
            "obsidian.lock.json",
            "spud.config.json",
        ])

    def test_the_spudagent_source_is_the_shipped_file_and_no_home_gets_a_copy(self):
        """SPW-004: `projects/agentdef.agent_source` reads share/agents/spudagent.md through core/shipped.share_dir, so
        share/ has one owner; and it is the one shipped file `spud init` does not write into the home, because it belongs
        at user scope, where Claude Code reads an agent definition from.  The vault SPD-156 ships is subtracted below
        rather than counted against that: init's step 4b does write it into the home, under `.obsidian/` instead of at
        its own share-relative path, and the lock beside it is what that install reads."""
        home = helpers.Home()
        try:
            ctx = spud.Ctx(home.path, "SPUD_HOME", False, tool=REPO)
            self.assertEqual(spud.agent_source(ctx), SHARE / "agents" / "spudagent.md")
            self.assertTrue(spud.agent_source(ctx).is_file())
            self.assertNotIn("agents/spudagent.md", spud.SCAFFOLDING)
            shipped_paths = [rel for rel, _ in shipped()]
            vault = {rel for rel in shipped_paths if rel == LOCK.name or rel.startswith("obsidian/")}
            self.assertEqual(sorted(set(shipped_paths) - set(spud.SCAFFOLDING) - vault - {"spud.config.json"}),
                             ["agents/spudagent.md"])
        finally:
            home.cleanup()

    def test_the_shipped_vault_holds_no_layout_no_plugin_code_and_no_note(self):
        # Design section 1: `workspace.json` and `workspace-mobile.json` are one person's window layout, `.DS_Store` is
        # the Finder's, and a plugin's or a theme's code comes from the lock, never from this repository.
        shipped_names = {rel for rel, _ in shipped()}
        for never in spud.NEVER:
            self.assertNotIn("obsidian/" + never, shipped_names)
        for rel in shipped_names:
            self.assertFalse(rel.endswith((".js", ".css")) and "/plugins/" in rel, rel)
            self.assertFalse(rel.startswith("obsidian/themes/"), rel)

    def test_the_shipped_core_plugins_turn_sync_off(self):
        # Design section 1: Sync is tied to one person's Obsidian account, and every other core-plugin setting is the
        # captured one.  `core-plugins.json` is the one file `vaultlock.canonical` rewrites a value in.
        core = json.loads((SHARE / "obsidian" / spud.CORE_PLUGINS).read_text(encoding="utf-8"))
        self.assertIs(core[spud.SYNC], False)
        self.assertTrue(core.get("bases"), "the shipped .base views need the Bases core plugin on")

    def test_the_lock_is_one_every_command_can_read(self):
        lock = shipped_lock()
        self.assertEqual(spud.lock_problems(lock), [])
        self.assertTrue(lock["plugins"] and lock["themes"], "the lock pins no plugin or no theme")
        for _kind, name, entry in spud.locked(lock):
            self.assertTrue(name, entry)
            for f in entry["files"]:
                self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(f["url"].startswith("https://"), f["url"])

    def test_every_enabled_plugin_and_the_theme_the_settings_name_are_the_ones_the_lock_pins(self):
        # The two halves of the vault have to agree: `community-plugins.json` turns a plugin on and the lock is what
        # puts its files in the vault, so a plugin enabled and not pinned is a vault that opens without it.
        lock = shipped_lock()
        enabled = json.loads((SHARE / "obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(enabled), sorted(e["id"] for e in lock["plugins"]))
        appearance = json.loads((SHARE / "obsidian" / "appearance.json").read_text(encoding="utf-8"))
        self.assertEqual([appearance["cssTheme"]] if appearance.get("cssTheme") else [],
                         [e["name"] for e in lock["themes"]])
        self.assertEqual(appearance.get("enabledCssSnippets") or [],
                         [rel.split("/")[-1][:-len(".css")] for rel, _ in shipped() if rel.startswith("obsidian/snippets/")])

    def test_every_shipped_plugin_data_file_belongs_to_a_locked_plugin(self):
        ids = {e["id"] for e in shipped_lock()["plugins"]}
        for rel, _ in shipped():
            if rel.startswith("obsidian/plugins/"):
                self.assertIn(rel.split("/")[2], ids, rel)
                self.assertTrue(rel.endswith("/" + spud.PLUGIN_DATA), rel)

    def test_read_gives_a_shipped_file_and_refuses_one_that_is_gone(self):
        home = helpers.Home()
        try:
            ctx = spud.Ctx(home.path, "SPUD_HOME", False, tool=REPO)
            self.assertEqual(spud.read(ctx, "ledger/Spud.md"), (SHARE / "ledger" / "Spud.md").read_text(encoding="utf-8"))
            self.assertEqual(spud.share_dir(ctx), SHARE)
            with self.assertRaises(spud.SpudError) as caught:
                spud.read(ctx, "ledger/Nothing.md")
            self.assertEqual(caught.exception.code, helpers.EXIT_ERROR)
            self.assertIn("ledger/Nothing.md", str(caught.exception))
        finally:
            home.cleanup()


class SpudInitSkillTest(unittest.TestCase):
    """`.claude/skills/spud-init/SKILL.md` (SPW-001 design section 5): the project-scope skill that reaches a fresh
    clone of this repository with nothing installed at all, because the user-scope `/spud` skill cannot exist until
    `spud init` has already built the home that `project install` writes it into.  Every bullet of section 5 that
    the text itself can carry -- as opposed to `ShippedPathsTest`'s reach for the machine-path guard every shipped
    file gets -- is asserted here, once, so a later edit of the skill cannot quietly drop one."""

    def text(self):
        self.assertTrue(SPUD_INIT_SKILL.is_file(), "%s is missing" % SPUD_INIT_SKILL.relative_to(REPO))
        return SPUD_INIT_SKILL.read_text(encoding="utf-8")

    def test_frontmatter_names_the_skill_spud_init_not_init(self):
        # "init" would collide with Claude Code's own built-in /init (design section 5).
        fields = skill_frontmatter(self.text())
        self.assertEqual(fields.get("name"), "spud-init")
        self.assertTrue(fields.get("description"), "no description")

    def test_the_skill_runs_only_when_a_person_types_it(self):
        # disable-model-invocation: true, as the /spud skill has (projects/sessions.SKILL_HEAD) -- never on the
        # model's own initiative.
        self.assertEqual(skill_frontmatter(self.text()).get("disable-model-invocation"), "true")

    def test_the_skill_names_no_machine_and_nobody(self):
        text = self.text()
        self.assertNotIn("/Users/", text)
        self.assertEqual(ADDRESS_RE.findall(text), [], text)

    def test_the_skill_finds_the_repository_root_instead_of_spelling_one(self):
        self.assertIn("rev-parse --show-toplevel", self.text())

    def test_the_skill_carries_both_recipes_with_the_collision_warning_in_the_second(self):
        text = self.text()
        self.assertIn("--project-root", text)
        recipe_1, recipe_2 = text.index("Recipe 1"), text.index("Recipe 2")
        self.assertLess(recipe_1, recipe_2)
        self.assertNotIn("collide", text[:recipe_2].lower())  # the warning belongs to the second recipe, not the first
        self.assertIn("collide", text[recipe_2:].lower())

    def test_the_skill_says_it_is_idempotent_and_names_doctor(self):
        text = self.text()
        self.assertIn("idempotent", text.lower())
        self.assertIn("spud doctor", text)


class InitReadmeAndClaudeMdTest(unittest.TestCase):
    """The two files design section 5 asks for beside the skill itself."""

    def test_the_readme_carries_the_one_command_a_fresh_clone_can_run(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("bin/spud init", readme)
        self.assertNotIn("/Users/", readme)
        self.assertEqual(ADDRESS_RE.findall(readme), [], readme)

    def test_the_tools_claude_md_names_the_skill(self):
        claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("spud-init", claude_md)


if __name__ == "__main__":
    unittest.main()
