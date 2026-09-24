"""SPD-156: the Obsidian vault the tool ships -- `spud vault install`, `spud vault capture`, `spud init`'s step 4b and
doctor's vault notes (the portable-home design sections 1 to 3 and 5, read against `share/`).

**No test here reaches the network, and none can.**  Every download goes through `commands/vaultlock.download`, which
reads SPUD_VAULT_DOWNLOADS the way the GitHub reader reads SPUD_GH: `tests/helpers.Home` sets it to `off` on every
scratch home in the suite, so a test that forgot gets a refusal rather than a request, and a test that wants a download
points it at a scratch directory that serves each URL from a file named by that URL.  What that directory holds is a
fixture lock naming two plugins and a theme that do not exist, with the real SHA-256 of the bytes it serves for them --
so the hash check is exercised for real, against files nobody has to fetch.

The home each case builds is the whole round trip: `spud init` installs the fixture vault, `vault capture` reads that
vault back into a worktree, and what it writes is the fixture `share/` it started from.  That is also the shape of the
two Obsidian facts `commands/vaultlock` is built on, which are asserted here rather than only described: an installed
`main.js` may carry Obsidian's `/* nosourcemap */` suffix and still be the pinned file, and a theme is pinned to a
commit rather than to its version.
"""

import hashlib
import http.client
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from helpers import EXIT_ERROR, EXIT_OWNERSHIP, EXIT_USAGE, Home, RepoMixin, SpudTestCase, git, load_spud_module

spud = load_spud_module()

# Two plugins and a theme that do not exist, so nothing here can accidentally work by reaching the real store.
PLUGIN_ONE = {"id": "notes-plus", "name": "Notes Plus", "version": "1.2.3", "repo": "nobody/obsidian-notes-plus",
              "views": ["notes-board"]}
PLUGIN_TWO = {"id": "quick-open", "name": "Quick Open", "version": "0.9.0", "repo": "nobody/obsidian-quick-open",
              "views": []}
THEME = {"name": "Slate", "version": "2.0.0", "repo": "nobody/slate"}
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PLUGINS = (PLUGIN_ONE, PLUGIN_TWO)
# What the vault's own settings say, before `vaultlock.canonical` sorts them and turns Sync off.
SETTINGS = {
    "app.json": {"defaultViewMode": "preview", "userIgnoreFilters": ["ledger/_templates/"]},
    "appearance.json": {"accentColor": "", "cssTheme": THEME["name"], "enabledCssSnippets": [], "theme": "obsidian"},
    "community-plugins.json": [PLUGIN_ONE["id"], PLUGIN_TWO["id"]],
    "core-plugins.json": {"bases": True, "file-explorer": True, "graph": True, "sync": True},
    "graph.json": {"showOrphans": True, "showTags": True},
    "page-preview.json": {"bases": True, "editor": True},
    "types.json": {"types": {"status": "text"}},
}
PLUGIN_DATA = {"greeting": "hello"}


def plugin_bytes(plugin, name):
    """The bytes the fixture's release serves for one of a plugin's three files."""
    if name == "manifest.json":
        return (json.dumps({"id": plugin["id"], "name": plugin["name"], "version": plugin["version"],
                            "minAppVersion": "1.0.0"}, indent=2) + "\n").encode("utf-8")
    if name == "main.js":
        return ("/* %s %s */\n" % (plugin["id"], plugin["version"])).encode("utf-8")
    return (".%s { color: red; }\n" % plugin["id"]).encode("utf-8")


def theme_bytes(name):
    if name == "manifest.json":
        return (json.dumps({"name": THEME["name"], "version": THEME["version"], "minAppVersion": "1.0.0"},
                           indent=2) + "\n").encode("utf-8")
    return ("/* %s */\n" % THEME["name"]).encode("utf-8")


def plugin_url(plugin, name):
    return spud.RELEASE_URL % (plugin["repo"], plugin["version"], name)


def theme_url(name, commit=COMMIT):
    return spud.RAW_URL % (THEME["repo"], commit, name)


def no_fixture_directory():
    """`os.environ` without SPUD_VAULT_DOWNLOADS: the branch of `download` that really opens a URL, which is the one the
    scheme check and the exceptions below are about.  Nothing is fetched under it -- urllib itself is replaced."""
    return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != spud.DOWNLOAD_ENV}, clear=True)


def truncated_urllib():
    """`urllib.request`, with a response whose body stops half way: `read()` raises `http.client.IncompleteRead`, which
    is what a connection cut mid-download raises and is *not* an OSError.  It goes over `spudlib.core.lazy`'s own name
    for urllib, which is where every vault download reaches it (`commands/vaultlock.download`), and carries `http` for
    the same reason the real module does: that is where `download` names the exception it catches."""
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"half a plugin", 900)

    return types.SimpleNamespace(http=http, Request=lambda url, headers=None: url,
                                 urlopen=lambda request, timeout=None: Response())


def fixture_lock():
    """The lock the fixture `share/` ships: real SHA-256 of the bytes the scratch directory serves."""
    plugins = []
    for plugin in PLUGINS:
        plugins.append({"id": plugin["id"], "name": plugin["name"], "version": plugin["version"],
                        "repo": plugin["repo"], "views": list(plugin["views"]),
                        "files": [{"name": name, "url": plugin_url(plugin, name),
                                   "sha256": hashlib.sha256(plugin_bytes(plugin, name)).hexdigest()}
                                  for name in spud.PLUGIN_FILES]})
    return {"plugins": plugins,
            "themes": [{"name": THEME["name"], "version": THEME["version"], "repo": THEME["repo"], "commit": COMMIT,
                        "files": [{"name": name, "url": theme_url(name),
                                   "sha256": hashlib.sha256(theme_bytes(name)).hexdigest()}
                                  for name in spud.THEME_FILES]}]}


class VaultCase(RepoMixin, SpudTestCase):
    """A scratch home and the tool checkout beside it, whose share/ is the tool's own (a copy of this repository's) with
    the shipped vault replaced by the fixture one, and a scratch directory serving its releases.  The home is not
    initialised: each case runs `spud init` itself, because what init does to the vault is half of what is under test."""

    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.tool = self.home.tool
        self.home.own_share()
        self.served = self.scratch_dir("vault-served-")
        self.lock = fixture_lock()
        self.write_fixture_share()
        self.serve_releases()
        self.home.env["SPUD_VAULT_DOWNLOADS"] = str(self.served)

    # -- the fixture ---------------------------------------------------------

    def share(self, *parts):
        return self.tool.joinpath("share", *parts)

    def write_fixture_share(self):
        """`share/obsidian/` and `share/obsidian.lock.json` in the scratch tool, in place of the ones this repository
        really ships: two plugins and a theme nobody has to fetch.

        Committed, because every linked worktree a capture test writes into is checked out from the commit, not from
        the tool's working tree, and a capture into a worktree still holding the real vault would report the whole
        fixture as written."""
        shutil.rmtree(self.share("obsidian"), ignore_errors=True)
        for name, value in SETTINGS.items():
            self.write_share("obsidian/" + name, spud.canonical(name, json.dumps(value)))
        self.write_share("obsidian/plugins/%s/%s" % (PLUGIN_ONE["id"], spud.PLUGIN_DATA),
                         spud.canonical(spud.PLUGIN_DATA, json.dumps(PLUGIN_DATA)))
        self.write_share(spud.LOCK, json.dumps(fixture_lock(), indent=2, sort_keys=True) + "\n")
        git(self.tool, "add", "-A")
        git(self.tool, "commit", "-q", "-m", "the fixture vault")

    def write_share(self, rel, text):
        path = self.share(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def serve(self, url, data):
        (self.served / spud.served_name(url)).write_bytes(data)

    def serve_releases(self):
        """Every URL the fixture lock pins, plus the two community lists and the theme's HEAD commit, which only
        `vault capture` asks for."""
        for plugin in PLUGINS:
            for name in spud.PLUGIN_FILES:
                self.serve(plugin_url(plugin, name), plugin_bytes(plugin, name))
        for name in spud.THEME_FILES:
            self.serve(theme_url(name), theme_bytes(name))
        self.serve(spud.PLUGIN_LIST, json.dumps(
            [{"id": p["id"], "name": p["name"], "repo": p["repo"]} for p in PLUGINS]).encode("utf-8"))
        self.serve(spud.THEME_LIST, json.dumps(
            [{"name": THEME["name"], "repo": THEME["repo"]}]).encode("utf-8"))
        self.serve(spud.HEAD_SHA_URL % THEME["repo"], COMMIT.encode("utf-8"))

    # -- the home ------------------------------------------------------------

    def init(self, *extra, check=True):
        """`spud init` of the home, registering the tool as project 1 as Home.init does (SPD-233)."""
        proc = self.home.run("--json", "init", "--no-schedule", "--project-root", self.tool, "--project-key", "spud",
                             "--sessions", "always", *extra, check=check)
        return json.loads(proc.stdout) if check else proc

    def vault(self, *parts):
        return self.home.path.joinpath(spud.OBSIDIAN, *parts)

    def installed(self, plugin, name):
        return self.vault(spud.PLUGINS, plugin["id"], name)

    def assert_vault_is_the_shipped_one(self):
        """Every shipped settings file is in the home byte for byte, and every locked file is the bytes the lock pins."""
        for rel, text in spud.shipped_settings(self.ctx()):
            self.assertEqual(self.vault(*rel.split("/")).read_text(encoding="utf-8"), text, rel)
        for plugin in PLUGINS:
            for name in spud.PLUGIN_FILES:
                self.assertEqual(self.installed(plugin, name).read_bytes(), plugin_bytes(plugin, name))
        for name in spud.THEME_FILES:
            self.assertEqual(self.vault(spud.THEMES, THEME["name"], name).read_bytes(), theme_bytes(name))

    def ctx(self):
        return spud.Ctx(self.home.path, "SPUD_HOME", False, tool=self.tool)


class InstallTest(VaultCase):
    """`spud vault install`: the settings copied, the plugins downloaded and checked, and what it must not touch."""

    def test_a_fresh_install_writes_the_settings_and_every_pinned_file(self):
        record = self.init()["vault"]
        self.assert_vault_is_the_shipped_one()
        self.assertEqual([i["name"] for i in record["installed"]], [PLUGIN_ONE["id"], PLUGIN_TWO["id"], THEME["name"]])
        self.assertEqual(record["refused"], [])
        self.assertIn("plugins/%s/%s" % (PLUGIN_ONE["id"], spud.PLUGIN_DATA), record["written"])
        # the line the design asks for, in the output a person reads
        self.assertIn("does not touch the shipped", self.home.run("vault", "install", actor="spud").stdout)

    def test_a_second_install_changes_nothing_and_downloads_nothing(self):
        self.init()
        shutil.rmtree(self.served)  # nothing may be fetched: everything the lock pins is already right
        self.served.mkdir()
        record = self.home.json("vault", "install", actor="spud")
        self.assertEqual((record["written"], record["rewritten"], record["replaced"], record["refused"]), ([], [], [], []))
        self.assertIn("plugin %s" % PLUGIN_ONE["id"], record["unchanged"])

    def test_a_tool_owned_file_the_home_changed_is_replaced_and_a_copy_kept(self):
        self.init()
        self.vault("app.json").write_text('{"defaultViewMode": "source"}\n', encoding="utf-8")
        self.installed(PLUGIN_TWO, "main.js").write_bytes(b"/* edited by hand */\n")
        record = self.home.json("vault", "install", actor="spud")
        replaced = {r["path"]: r["backup"] for r in record["replaced"]}
        self.assertEqual(sorted(replaced), ["app.json", "plugins/%s/main.js" % PLUGIN_TWO["id"]])
        self.assert_vault_is_the_shipped_one()
        for path, held in ((replaced["app.json"], '{"defaultViewMode": "source"}\n'),
                           (replaced["plugins/%s/main.js" % PLUGIN_TWO["id"]], "/* edited by hand */\n")):
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), held)

    def test_a_replaced_file_and_the_copy_of_it_are_both_named_in_the_output(self):
        self.init()
        self.vault("types.json").write_text('{"types": {}}\n', encoding="utf-8")
        text = self.home.run("vault", "install", actor="spud").stdout
        self.assertIn("replaced types.json (the copy it held is ", text)
        self.assertIn("1 replaced", text)

    def test_a_file_the_tool_does_not_ship_is_never_touched(self):
        self.init()
        mine = {self.vault("workspace.json"): '{"main": "mine"}\n',
                self.vault("snippets", "mine.css") : "/* mine */\n",
                self.installed(PLUGIN_ONE, "extra.txt"): "mine\n",
                self.vault(spud.PLUGINS, "sideloaded", "main.js"): "/* sideloaded */\n"}
        for path, text in mine.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.home.json("vault", "install", "--force", actor="spud")
        for path, text in mine.items():
            self.assertEqual(path.read_text(encoding="utf-8"), text, path)

    def test_a_hash_mismatch_refuses_that_plugin_by_name_and_the_rest_still_install(self):
        self.serve(plugin_url(PLUGIN_TWO, "main.js"), b"/* not what the lock pins */\n")
        proc = self.init(check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)  # a refused download never fails init
        record = json.loads(proc.stdout)["vault"]
        self.assertEqual([(r["kind"], r["name"]) for r in record["refused"]], [("plugin", PLUGIN_TWO["id"])])
        self.assertIn("SHA-256", record["refused"][0]["why"])
        self.assertEqual([i["name"] for i in record["installed"]], [PLUGIN_ONE["id"], THEME["name"]])
        # all of the refused plugin or none of it: the manifest it downloaded first is not left behind
        self.assertFalse(self.installed(PLUGIN_TWO, "manifest.json").exists())
        self.assertTrue(self.installed(PLUGIN_ONE, "main.js").is_file())

    def test_force_rewrites_what_matched_and_keeps_no_copy_of_it(self):
        self.init()
        record = self.home.json("vault", "install", "--force", actor="spud")
        self.assertEqual(record["replaced"], [])
        self.assertIn("app.json", record["rewritten"])
        self.assertIn("plugins/%s/main.js" % PLUGIN_ONE["id"], record["rewritten"])
        self.assert_vault_is_the_shipped_one()

    def test_an_installed_main_js_with_obsidian_s_marker_is_the_pinned_file(self):
        # Obsidian appends `\n/* nosourcemap */` to every main.js it installs, so a comparison that did not know it
        # would call every plugin locally changed and re-download it on every run.
        self.init()
        path = self.installed(PLUGIN_ONE, "main.js")
        path.write_bytes(plugin_bytes(PLUGIN_ONE, "main.js") + spud.NOSOURCEMAP)
        shutil.rmtree(self.served)
        self.served.mkdir()
        record = self.home.json("vault", "install", actor="spud")
        self.assertEqual((record["written"], record["replaced"], record["refused"]), ([], [], []))
        self.assertEqual(path.read_bytes(), plugin_bytes(PLUGIN_ONE, "main.js") + spud.NOSOURCEMAP)

    def test_a_lock_naming_a_path_installs_nothing_and_writes_no_file_outside_the_vault(self):
        # The lock is the tool's own file, and it is also the only place a plugin's id, a theme's name and a file's
        # name come from -- all three joined onto a path.  `Path(vault) / "/tmp/x"` is `/tmp/x`, so a lock holding one
        # of these is refused whole, before the install has written anything at all.
        self.init("--no-vault")
        escape = self.scratch_dir("vault-escape-")
        lock = fixture_lock()
        lock["plugins"][0]["id"] = str(escape / "escaped")  # absolute: anywhere on this machine
        # a file name that climbs out of .obsidian/ -- as far as the home and no further, so a regression here litters
        # this test's own temporary home rather than the machine's temp directory
        lock["plugins"][1]["files"][1]["name"] = "../../../escaped.js"
        lock["themes"][0]["name"] = ".."
        self.write_share(spud.LOCK, json.dumps(lock, indent=2, sort_keys=True) + "\n")
        proc = self.home.run("vault", "install", actor="spud", check=False)
        self.assertEqual(list(escape.iterdir()), [])  # nothing outside the home
        self.assertFalse((self.home.path / "escaped.js").exists())  # nothing above the vault either
        self.assertFalse(self.vault().exists())  # and not the settings half either: the lock is read first
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("not one plain file name", proc.stderr)
        for named in (str(escape / "escaped"), "escaped.js", "'..'"):
            self.assertIn(named, proc.stderr)

    def test_a_lock_url_that_is_not_https_refuses_that_plugin_and_reads_no_local_file(self):
        # urllib opens `file://` as willingly as `https://`, so without the scheme check a lock could have an install
        # copy a local file into the vault under a plugin's name.
        secret = self.scratch_dir("vault-secret-") / "main.js"
        secret.write_bytes(b"/* a local file nobody asked for */\n")
        lock = fixture_lock()
        lock["plugins"][0]["files"][1]["url"] = "file://" + str(secret)
        self.write_share(spud.LOCK, json.dumps(lock, indent=2, sort_keys=True) + "\n")
        record = self.init()["vault"]
        self.assertEqual([(r["kind"], r["name"]) for r in record["refused"]], [("plugin", PLUGIN_ONE["id"])])
        self.assertIn("its scheme is file,", record["refused"][0]["why"])
        self.assertFalse(self.installed(PLUGIN_ONE, "main.js").exists())
        self.assertEqual([i["name"] for i in record["installed"]], [PLUGIN_TWO["id"], THEME["name"]])

    def test_a_body_cut_short_refuses_the_plugin_and_installs_none_of_it(self):
        # In this process rather than through the CLI: what is under test is which exception `download` turns into a
        # refusal, and no mock reaches the subprocess the rest of these cases run.  `http.client.IncompleteRead` is an
        # HTTPException and not an OSError, so before SPD-156's review it left `vault install` -- and `spud init` --
        # as a traceback instead of a refusal.
        self.init("--no-vault")
        with no_fixture_directory():
            with mock.patch("spudlib.core.lazy.urllib_request", truncated_urllib()):
                record, lines = spud.install_vault(self.ctx())
        self.assertEqual([r["name"] for r in record["refused"]],
                         [PLUGIN_ONE["id"], PLUGIN_TWO["id"], THEME["name"]])
        self.assertIn("IncompleteRead", record["refused"][0]["why"])
        self.assertTrue(any("refused plugin %s" % PLUGIN_ONE["id"] in line for line in lines), lines)
        for plugin in PLUGINS:  # nothing truncated is installed: not one downloaded file of either plugin
            for name in spud.PLUGIN_FILES:
                self.assertFalse(self.installed(plugin, name).exists(), (plugin["id"], name))
        self.assertFalse(self.vault(spud.THEMES).exists())
        # the settings half downloads nothing and still ran, the shipped plugin data.json with it
        self.assertTrue(self.vault("app.json").is_file())
        self.assertTrue(self.installed(PLUGIN_ONE, spud.PLUGIN_DATA).is_file())

    def test_a_refused_plugin_with_views_names_what_will_not_render(self):
        self.init()
        shutil.rmtree(self.vault(spud.PLUGINS, PLUGIN_ONE["id"]))  # force install to fetch it again, not skip it as pinned
        self.serve(plugin_url(PLUGIN_ONE, "main.js"), b"/* not what the lock pins */\n")
        text = self.home.run("vault", "install", actor="spud", check=False).stdout
        self.assertIn("refused plugin %s" % PLUGIN_ONE["id"], text)
        self.assertIn(PLUGIN_ONE["views"][0], text)
        self.assertIn("will not render", text)

    def test_a_refused_plugin_with_no_views_names_nothing_extra(self):
        self.init()
        shutil.rmtree(self.vault(spud.PLUGINS, PLUGIN_TWO["id"]))
        self.serve(plugin_url(PLUGIN_TWO, "main.js"), b"/* not what the lock pins */\n")
        text = self.home.run("vault", "install", actor="spud", check=False).stdout
        self.assertIn("refused plugin %s" % PLUGIN_TWO["id"], text)
        self.assertEqual(PLUGIN_TWO["views"], [])
        self.assertNotIn("will not render", text)

    def test_install_is_spud_s(self):
        self.init()
        t = self.new_ticket("Vault", status="active")
        m = self.new_member(t["key"], deliverable=["home:docs/x.md"])  # no bare glob: this ticket binds no worktree
        proc = self.home.run("vault", "install", actor=m["ref"], check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("is Spud's", proc.stderr)


class InitVaultTest(VaultCase):
    """Step 4b: init installs the vault, `--no-vault` leaves it out, and a refused download is a note."""

    def test_init_builds_a_home_whose_vault_is_set_up(self):
        data = self.init()
        self.assert_vault_is_the_shipped_one()
        self.assertTrue(any(line.startswith("4a. scaffolding") for line in data["done"]), data["done"])
        self.assertTrue(any(line.startswith("4b. vault") for line in data["done"]), data["done"])
        self.assertIn("Trust author and enable plugins", data["by_hand"])
        self.assertEqual(self.home.json("doctor")["vault"]["differences"], [])

    def test_no_vault_skips_it_and_leaves_a_green_doctor(self):
        data = self.init("--no-vault")
        self.assertFalse(self.vault().exists())
        self.assertEqual(data["vault"], {"skipped": "--no-vault"})
        self.assertTrue(any("--no-vault" in line for line in data["done"]), data["done"])
        report = self.home.json("doctor")
        self.assertEqual(report["problems"], [])
        self.assertFalse(report["vault"]["vault"])

    def test_the_network_refused_is_a_note_and_a_line_never_a_failed_init(self):
        self.home.env["SPUD_VAULT_DOWNLOADS"] = "off"
        data = self.init()
        self.assertEqual(sorted(r["name"] for r in data["vault"]["refused"]),
                         sorted([PLUGIN_ONE["id"], PLUGIN_TWO["id"], THEME["name"]]))
        self.assertTrue(any("vault install` once the network is back" in line for line in data["done"]), data["done"])
        # the settings and the views are there all the same, and the home is complete: doctor found no problem
        self.assertEqual(self.vault("app.json").read_text(encoding="utf-8"),
                         spud.canonical("app.json", json.dumps(SETTINGS["app.json"])))
        self.assertEqual(self.home.json("doctor")["problems"], [])
        self.assertEqual(self.home.json("doctor")["vault"]["differences"], [])

    def test_the_dry_run_says_which_of_the_two_it_would_do(self):
        with_vault = self.init("--dry-run")["steps"][3]
        self.assertIn("download every plugin and theme", with_vault)
        self.assertIn("install no vault (--no-vault)", self.init("--dry-run", "--no-vault")["steps"][3])


class CaptureTest(VaultCase):
    """`spud vault capture --into <worktree>`: a member's, into a linked worktree of this repository, and only what is
    turned on."""

    def setUp(self):
        super().setUp()
        self.init()
        self.wt = self.add_worktree(self.tool, "spd-001-vault", inside=True)
        self.ticket = self.new_ticket("The vault", status="active")

    def member(self, deliverable=("share/**",)):
        return self.new_member(self.ticket["key"], persona="engineer", model="opus", deliverable=list(deliverable),
                               cwd=self.wt)

    def capture(self, into=None, actor=None, check=True, deliverable=("share/**",)):
        ref = actor or self.member(deliverable)["ref"]
        return self.cli("--json", "vault", "capture", "--into", str(into or self.wt), actor=ref, check=check)

    def captured_lock(self):
        return json.loads((self.wt / "share" / spud.LOCK).read_text(encoding="utf-8"))

    def stale_share(self, worktree=None):
        """A worktree's `share/` put into a state a capture would visibly change: the lock replaced by a marker, and a
        settings file removed.

        Without it every worktree here already holds exactly what a capture writes -- it is checked out from the commit
        the fixture `share/` was made in -- and a case that asserts "nothing was written" passes whether the refusal
        happened or not.  Returns what `assert_share_untouched` checks.
        """
        worktree = worktree or self.wt
        lock = worktree / "share" / spud.LOCK
        lock.write_bytes(b"{}\n")
        gone = worktree / "share" / spud.SHARE_OBSIDIAN / "app.json"
        gone.unlink()
        return lock, gone

    def assert_share_untouched(self, lock, gone):
        self.assertEqual(lock.read_bytes(), b"{}\n")
        self.assertFalse(gone.exists())

    def test_capture_writes_back_the_vault_init_installed_from(self):
        record = json.loads(self.capture().stdout)
        self.assertEqual(record["written"], [], "the round trip changed something: %s" % record["written"])
        self.assertEqual([p["id"] for p in record["plugins"]], [PLUGIN_ONE["id"], PLUGIN_TWO["id"]])
        self.assertEqual(record["themes"], [{"name": THEME["name"], "version": THEME["version"],
                                             "repo": THEME["repo"], "commit": COMMIT}])
        self.assertEqual(self.captured_lock(), self.lock)

    def test_a_capture_writes_back_a_share_that_has_drifted(self):
        # The other half of the three refusals below: they assert `stale_share`'s state is left exactly as it was, and
        # this asserts a capture that does run puts it back -- so "nothing was written" there means something.
        lock, gone = self.stale_share()
        record = json.loads(self.capture().stdout)
        self.assertEqual(record["written"], ["share/obsidian.lock.json", "share/obsidian/app.json"])
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8")), self.lock)
        self.assertTrue(gone.is_file())

    def test_it_captures_only_what_is_turned_on(self):
        self.vault("community-plugins.json").write_text(json.dumps([PLUGIN_ONE["id"]]), encoding="utf-8")
        record = json.loads(self.capture().stdout)
        self.assertEqual([p["id"] for p in record["plugins"]], [PLUGIN_ONE["id"]])
        self.assertEqual([e["id"] for e in self.captured_lock()["plugins"]], [PLUGIN_ONE["id"]])
        self.assertEqual(json.loads((self.wt / "share" / "obsidian" / "community-plugins.json").read_text()),
                         [PLUGIN_ONE["id"]])
        # the plugin turned off leaves the lock, and its files stay in the vault untouched: capture reads, never writes
        self.assertTrue(self.installed(PLUGIN_TWO, "main.js").is_file())

    def test_it_never_copies_a_note_and_never_the_window_layout(self):
        self.vault("workspace.json").write_text('{"main": "mine"}\n', encoding="utf-8")
        (self.home.path / "ledger" / "Private.md").write_text("mine\n", encoding="utf-8")
        self.capture()
        shipped = sorted(p.relative_to(self.wt).as_posix() for p in (self.wt / "share").rglob("*") if p.is_file())
        self.assertNotIn("share/obsidian/workspace.json", shipped)
        self.assertNotIn("share/ledger/Private.md", shipped)
        self.assertIn("share/ledger/Board.base", shipped)

    def test_a_locally_changed_plugin_is_refused_by_name_and_nothing_is_written(self):
        self.installed(PLUGIN_TWO, "main.js").write_bytes(b"/* mine now */\n")
        state = self.stale_share()  # a share/ a capture that ran would put back
        proc = self.capture(check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn(PLUGIN_TWO["id"], proc.stderr)
        self.assertIn("what anyone can download", proc.stderr)
        self.assert_share_untouched(*state)

    def test_obsidian_s_marker_on_an_installed_main_js_is_not_a_local_change(self):
        path = self.installed(PLUGIN_ONE, "main.js")
        path.write_bytes(plugin_bytes(PLUGIN_ONE, "main.js") + spud.NOSOURCEMAP)
        self.assertEqual(json.loads(self.capture().stdout)["written"], [])

    def test_a_plugin_no_community_list_names_is_refused(self):
        self.serve(spud.PLUGIN_LIST, json.dumps([{"id": PLUGIN_ONE["id"], "name": "x", "repo": PLUGIN_ONE["repo"]}]).encode())
        proc = self.capture(check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn(PLUGIN_TWO["id"], proc.stderr)
        self.assertIn("outside the store", proc.stderr)

    def test_the_main_checkout_and_every_other_repository_are_refused(self):
        other = self.make_repo("other-")
        for into, needle in ((self.tool, "main checkout"),
                             (self.add_worktree(other, "wt"), "another repository"),
                             (self.scratch_dir("plain-"), "not inside a git repository")):
            proc = self.capture(into=into, check=False)
            self.assertEqual(proc.returncode, EXIT_USAGE, proc)
            self.assertIn(needle, proc.stderr)

    def test_a_path_outside_the_actor_s_deliverables_is_refused_and_nothing_is_written(self):
        state = self.stale_share()  # a share/ a capture that ran would put back
        proc = self.capture(check=False, deliverable=["bin/**"])
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("Law 5", proc.stderr)
        self.assertIn("deliverables (bin/**)", proc.stderr)
        self.assertIn("share/", proc.stderr)
        self.assert_share_untouched(*state)

    def test_another_worktree_of_this_repository_is_refused_when_the_ticket_is_bound(self):
        # SPD-098: a member writes in its ticket's own worktree.  A capture is a CLI write, which no edit hook sees, so
        # the rule is made in the command -- and the other worktree is a real one of this repository, so the three
        # refusals of `--into` have all passed by the time this one fires.
        elsewhere = self.add_worktree(self.tool, "spd-001-elsewhere")
        state = self.stale_share(elsewhere)  # the share/ a capture into it would put back
        proc = self.capture(into=elsewhere, check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("Law 5", proc.stderr)
        self.assertIn(str(self.wt), proc.stderr)
        self.assert_share_untouched(*state)

    def test_spud_captures_nothing(self):
        proc = self.capture(actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP)
        self.assertIn("Law 1", proc.stderr)

    def test_a_shipped_file_this_capture_does_not_produce_is_removed_and_the_rest_of_share_is_left(self):
        # A plugin turned off since the last capture leaves its `data.json` behind, and a `.base` file the home no
        # longer has leaves the whole file: both are the tool's shipped vault, and both go.  Everything else under
        # share/ belongs to some other part of the tool and a capture that swept the directory would take it too.
        stale_data = self.wt / "share" / spud.SHARE_OBSIDIAN / spud.PLUGINS / PLUGIN_ONE["id"] / spud.PLUGIN_DATA
        stale_base = self.wt / "share" / spud.VAULT_BASES / "Old.base"
        stale_base.write_text("views: []\n", encoding="utf-8")
        keep = [self.wt / "share" / spud.VAULT_BASES / "Home.md", self.wt / "share" / "CLAUDE.md"]
        self.assertTrue(stale_data.is_file() and all(p.is_file() for p in keep))
        self.vault("community-plugins.json").write_text(json.dumps([PLUGIN_TWO["id"]]), encoding="utf-8")
        record = json.loads(self.capture().stdout)
        self.assertEqual(record["removed"], ["share/ledger/Old.base",
                                             "share/obsidian/plugins/%s/%s" % (PLUGIN_ONE["id"], spud.PLUGIN_DATA)])
        self.assertFalse(stale_data.exists())
        self.assertFalse(stale_base.exists())
        for path in keep:
            self.assertTrue(path.is_file(), path)

    def test_a_file_a_capture_removes_goes_through_the_deliverable_check_with_the_rest(self):
        # The written paths are all inside these globs and the stale one is not, so the refusal can only be about what
        # this capture would delete.
        stale_base = self.wt / "share" / spud.VAULT_BASES / "Old.base"
        stale_base.write_text("views: []\n", encoding="utf-8")
        proc = self.capture(check=False, deliverable=["share/obsidian/**", "share/" + spud.LOCK,
                                                      "share/ledger/Board.base", "share/ledger/Fleet.base"])
        self.assertEqual(proc.returncode, EXIT_OWNERSHIP, proc.stdout)
        self.assertIn("share/ledger/Old.base", proc.stderr)
        self.assertIn("Law 5", proc.stderr)
        self.assertEqual(stale_base.read_text(encoding="utf-8"), "views: []\n")

    def test_a_turned_on_name_that_is_not_one_plain_file_name_is_refused_and_no_lock_is_written(self):
        # The same rule the lock is read under, made where the names are read: a plugin id, a theme name or a snippet
        # name is joined onto a path both under share/ here and in every home a later `vault install` writes.
        state = self.stale_share()
        ref = self.member()["ref"]
        for kind, where, value in (("plugin", "community-plugins.json", [PLUGIN_ONE["id"], "../../escaped"]),
                                   ("theme", "appearance.json",
                                    dict(SETTINGS["appearance.json"], cssTheme="/tmp/escaped")),
                                   ("snippet", "appearance.json",
                                    dict(SETTINGS["appearance.json"], enabledCssSnippets=["../escaped"]))):
            with self.subTest(kind):
                self.vault(where).write_text(json.dumps(value), encoding="utf-8")
                proc = self.capture(actor=ref, check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
                self.assertIn("not one plain file name", proc.stderr)
                self.assertIn("escaped", proc.stderr)
                self.assertIn(kind, proc.stderr)
                self.assert_share_untouched(*state)
                self.vault(where).write_text(json.dumps(SETTINGS[where]), encoding="utf-8")  # back for the next one

    def test_a_plugin_s_view_types_are_carried_forward_from_the_lock_it_overwrites(self):
        # Nothing in the tool can read a view type out of a plugin's code, so the list is written down once, by hand,
        # and every later capture keeps it.  A plugin new to the lock arrives with none.
        self.capture()
        self.assertEqual([e["views"] for e in self.captured_lock()["plugins"]], [PLUGIN_ONE["views"], []])
        self.assertEqual(spud.view_types(self.captured_lock()), {"table"} | set(PLUGIN_ONE["views"]))


class DoctorVaultTest(VaultCase):
    """doctor's vault section: quiet when the home and the shipped copy match, one note per thing that differs."""

    def setUp(self):
        super().setUp()
        self.init()

    def notes(self):
        return self.home.json("doctor")["vault"]["differences"]

    def report(self):
        return self.home.json("doctor")

    def test_it_is_quiet_when_they_match(self):
        report = self.report()
        self.assertEqual(report["vault"]["differences"], [])
        self.assertEqual([n for n in report["notes"] if "vault capture" in n], [])
        self.assertEqual(report["vault"]["pinned"], 3)
        self.assertIn("nothing changed here", self.home.run("doctor").stdout)

    def test_a_changed_settings_file_is_a_note_naming_the_command_that_settles_it(self):
        self.vault("graph.json").write_text('{"showTags": false}\n', encoding="utf-8")
        self.assertEqual(self.notes(), [".obsidian/graph.json"])
        report = self.report()
        self.assertEqual(report["problems"], [])  # a note, never a problem: init must still be able to run
        self.assertTrue(any("vault capture --into" in n for n in report["notes"]), report["notes"])

    def test_obsidian_s_own_rewrite_of_whitespace_and_key_order_is_no_difference(self):
        value = json.loads(self.vault("graph.json").read_text(encoding="utf-8"))
        self.vault("graph.json").write_text(json.dumps(dict(reversed(list(value.items())))), encoding="utf-8")
        self.assertEqual(self.notes(), [])

    def test_sync_turned_on_in_the_home_is_no_difference(self):
        # The template turns Sync off because it is tied to one person's account; the home's own answer is the home's.
        value = json.loads(self.vault(spud.CORE_PLUGINS).read_text(encoding="utf-8"))
        value[spud.SYNC] = True
        self.vault(spud.CORE_PLUGINS).write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(self.notes(), [])

    def test_a_changed_base_file_is_a_note(self):
        # Obsidian rewrites a `.base` whenever Eric drags a column or adds a view, which is exactly the drift a ticket
        # has to capture -- and the reason these are notes and not problems.
        path = self.home.path / "ledger" / "Board.base"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(self.notes(), ["ledger/Board.base"])

    def test_a_plugin_at_another_version_is_a_note_naming_both(self):
        manifest = self.installed(PLUGIN_ONE, "manifest.json")
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["version"] = "9.9.9"
        manifest.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(self.notes(), [PLUGIN_ONE["id"]])
        note = [n for n in self.report()["notes"] if PLUGIN_ONE["id"] in n][0]
        self.assertIn("9.9.9", note)
        self.assertIn(PLUGIN_ONE["version"], note)

    def test_a_pinned_file_whose_bytes_differ_is_a_note_naming_the_file(self):
        self.installed(PLUGIN_TWO, "styles.css").write_bytes(b".mine { }\n")
        self.assertEqual(self.notes(), [PLUGIN_TWO["id"]])
        self.assertIn("styles.css", [n for n in self.report()["notes"] if PLUGIN_TWO["id"] in n][0])

    def test_a_theme_whose_css_moved_upstream_is_a_note(self):
        self.vault(spud.THEMES, THEME["name"], "theme.css").write_bytes(b"/* a newer Slate */\n")
        self.assertEqual(self.notes(), [THEME["name"]])

    def test_what_the_home_does_not_have_at_all_is_no_finding(self):
        # `vault install` is the command for a vault that was never installed, and doctor does not nag about it: a
        # plugin the lock pins and the home lacks says nothing about whether the shipped copy is behind.
        shutil.rmtree(self.vault(spud.PLUGINS, PLUGIN_ONE["id"]))
        self.vault("types.json").unlink()
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.report()["problems"], [])

    def base_view(self, view_type, view_name="View", filename="Extra.base"):
        """A `.base` file, written straight into the home's `ledger/`, with one view of `view_type` -- the shape
        `spud.read_base` reads and `missing_plugin_findings` looks at (SPD-159)."""
        (self.home.path / "ledger" / filename).write_text(
            "views:\n  - type: %s\n    name: %s\n" % (view_type, view_name), encoding="utf-8")
        return "ledger/" + filename

    def test_a_view_whose_plugin_is_installed_is_quiet(self):
        # PLUGIN_ONE's own view type, and a normal init installs PLUGIN_ONE: nothing for this check to say.
        self.base_view(PLUGIN_ONE["views"][0])
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.report()["problems"], [])

    def test_a_view_whose_plugin_is_missing_is_a_note_naming_the_view_the_file_the_plugin_and_the_command(self):
        rel = self.base_view(PLUGIN_ONE["views"][0], view_name="Notes")
        shutil.rmtree(self.vault(spud.PLUGINS, PLUGIN_ONE["id"]))
        self.assertEqual(self.notes(), [rel])
        note = [n for n in self.report()["notes"] if rel in n][0]
        self.assertIn("Notes", note)
        self.assertIn(rel, note)
        self.assertIn(PLUGIN_ONE["id"], note)
        self.assertIn("vault install", note)

    def test_a_table_only_base_file_is_quiet_even_without_the_plugin(self):
        # `table` is Obsidian's own view type: it names no plugin, so removing one changes nothing for it.
        shutil.rmtree(self.vault(spud.PLUGINS, PLUGIN_ONE["id"]))
        self.base_view("table")
        self.assertEqual(self.notes(), [])

    def test_no_obsidian_at_all_is_quiet(self):
        # `vault install` is already the command doctor names for a vault that was never installed (the test above
        # this class); a view whose plugin the home cannot possibly have, because it has no vault, says nothing new.
        self.base_view(PLUGIN_ONE["views"][0])
        shutil.rmtree(self.vault())
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.report()["problems"], [])


class VaultLockTest(unittest.TestCase):
    """`commands/vaultlock` on its own: the comparisons and the one door to the network."""

    def test_released_takes_obsidian_s_marker_off_and_matches_accepts_a_file_with_or_without_it(self):
        body = b"console.log(1);\n"
        self.assertEqual(spud.released(body + spud.NOSOURCEMAP), body)
        self.assertEqual(spud.released(body), body)
        self.assertEqual(spud.released(body + spud.NOSOURCEMAP + spud.NOSOURCEMAP), body + spud.NOSOURCEMAP)
        sha = hashlib.sha256(body).hexdigest()
        self.assertTrue(spud.matches(sha, body))
        self.assertTrue(spud.matches(sha, body + spud.NOSOURCEMAP))
        self.assertFalse(spud.matches(sha, body + b"x"))

    def test_canonical_sorts_keys_turns_sync_off_and_leaves_a_snippet_alone(self):
        text = spud.canonical("app.json", '{"b": 2, "a": {"d": 4, "c": 3}}')
        self.assertEqual(text, '{\n  "a": {\n    "c": 3,\n    "d": 4\n  },\n  "b": 2\n}\n')
        core = json.loads(spud.canonical(spud.CORE_PLUGINS, '{"sync": true, "bases": true}'))
        self.assertEqual(core, {"bases": True, "sync": False})
        self.assertIs(json.loads(spud.canonical("app.json", '{"sync": true}'))["sync"], True)  # only core-plugins.json
        self.assertEqual(spud.canonical("mine.css", "/* mine */\n"), "/* mine */\n")
        with self.assertRaises(spud.SpudError):
            spud.canonical("app.json", "not json")

    def test_downloads_off_refuses_every_url_and_a_directory_serves_by_its_name(self):
        with mock.patch.dict(os.environ, {spud.DOWNLOAD_ENV: spud.DOWNLOAD_OFF}):
            with self.assertRaises(spud.VaultDownloadError) as caught:
                spud.download("https://example.invalid/main.js")
            self.assertIn("downloads are off", str(caught.exception))
        with tempfile.TemporaryDirectory(prefix="spud-served-") as served:
            url = "https://example.invalid/a/b.js?v=1"
            with mock.patch.dict(os.environ, {spud.DOWNLOAD_ENV: served}):
                with self.assertRaises(spud.VaultDownloadError) as caught:
                    spud.download(url)
                self.assertIn("is not served by", str(caught.exception))
                with open(os.path.join(served, spud.served_name(url)), "wb") as f:
                    f.write(b'{"a": 1}')
                self.assertEqual(spud.download(url), b'{"a": 1}')
                self.assertEqual(spud.download_json(url), {"a": 1})
                with open(os.path.join(served, spud.served_name(url)), "wb") as f:
                    f.write(b"not json")
                with self.assertRaises(spud.VaultDownloadError) as caught:
                    spud.download_json(url)
                self.assertIn("not JSON", str(caught.exception))

    def test_only_https_is_fetched_and_every_other_scheme_is_refused_by_name(self):
        # Every URL a vault command opens comes from the lock, and urllib opens `file://` and `ftp://` as willingly as
        # `https://`.  The refusal comes before anything else reads the URL -- the fixture directory included, which is
        # why each one below is served and still not read.
        with tempfile.TemporaryDirectory(prefix="spud-served-") as served:
            with mock.patch.dict(os.environ, {spud.DOWNLOAD_ENV: served}):
                for url, scheme in (("file:///etc/passwd", "file"), ("ftp://example.invalid/main.js", "ftp"),
                                    ("http://example.invalid/main.js", "http"), ("/etc/passwd", "none")):
                    with open(os.path.join(served, spud.served_name(url)), "wb") as f:
                        f.write(b"served all the same")
                    with self.assertRaises(spud.VaultDownloadError) as caught:
                        spud.download(url)
                    self.assertIn("is not fetched", str(caught.exception))
                    self.assertIn("its scheme is %s," % scheme, str(caught.exception))

    def test_a_body_cut_short_is_a_refusal_and_not_a_traceback(self):
        # http.client.IncompleteRead is an HTTPException, which is not an OSError: catching the OSError family alone
        # let a truncated body out of `vault install` and `spud init` as a traceback.
        with no_fixture_directory():
            with mock.patch("spudlib.core.lazy.urllib_request", truncated_urllib()):
                with self.assertRaises(spud.VaultDownloadError) as caught:
                    spud.download("https://example.invalid/main.js")
        self.assertIn("https://example.invalid/main.js", str(caught.exception))
        self.assertIn("IncompleteRead", str(caught.exception))

    def test_lock_problems_refuses_a_name_that_is_not_one_plain_file_name(self):
        # An id, a theme name and a file name are each joined onto the vault's path, and `Path("/a") / "/tmp/x"` is
        # `/tmp/x`: a lock holding a separator, a drive, a root or a `..` would write outside `.obsidian/`.
        spoils = ("plugins", 0, "id"), ("themes", 0, "name"), ("plugins", 1, "files", 0, "name")
        for bad in ("/tmp/escaped", "../../escaped", "sub/dir", "..", ".", "C:/escaped", "back\\slash"):
            for spoil in spoils:
                lock = fixture_lock()
                where = lock[spoil[0]][spoil[1]]
                for step in spoil[2:-1]:
                    where = where[step]
                where[spoil[-1]] = bad
                problems = spud.lock_problems(lock)
                self.assertTrue(any(repr(bad) in p and "plain file name" in p for p in problems), (bad, spoil, problems))
        lock = fixture_lock()
        lock["plugins"][0]["id"] = ""
        self.assertTrue(any("is empty" in p for p in spud.lock_problems(lock)), spud.lock_problems(lock))
        self.assertEqual(spud.component_problem("AnuPpuccin"), None)  # and an ordinary name is one

    def test_lock_problems_names_what_a_lock_is_missing(self):
        self.assertEqual(spud.lock_problems(fixture_lock()), [])
        self.assertEqual(spud.lock_problems([]), ["not an object ([])"])
        lock = fixture_lock()
        del lock["plugins"][0]["views"]
        lock["plugins"][1]["files"] = [{"name": "main.js"}]
        lock["themes"][0]["version"] = None
        problems = spud.lock_problems(lock)
        self.assertTrue(any("views" in p for p in problems), problems)
        self.assertTrue(any("{name, url, sha256}" in p for p in problems), problems)

    def test_view_types_is_table_plus_what_the_locked_plugins_name(self):
        self.assertEqual(spud.view_types({"plugins": []}), {"table"})
        self.assertEqual(spud.view_types(fixture_lock()), {"table"} | set(PLUGIN_ONE["views"]))


if __name__ == "__main__":
    unittest.main()
