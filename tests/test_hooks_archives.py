"""PreToolUse(Bash): the names an archive or a patch the line names would write (SPD-144), held to the path rule and to
SPD-066's .git refusal as a spelled write target is, and what the hook cannot list refused to a member (SPD-217)."""

import io
import tarfile
import zipfile

from hookcase import AGENT_A, GIT_DIR_WORDING, GIT_FILE_WORDING, OUTSIDE, BashHookCase


AGENT_K = "b44a4c1e5a0d7f921"  # SPD-144: a member holding home:out/** and home:linked/**
ANYWHERE_WORDING = "places files where the line cannot say"  # SPD-126's reason for a write the line cannot place
UNLISTED_WORDING = "so the hook cannot list the names it writes"  # SPD-144: an archive or patch the hook cannot read
WRITTEN_WORDING = "the line may write that file before"  # SPD-144, in SPD-151's shape: read at hook time, written first
STDIN_WORDING = "from standard input"  # SPD-144: an archive or patch the line does not name as a file
UNSPELLED_WORDING = "through a word the line does not spell"  # SPD-144: an archive named by a substitution or xargs
OUTSIDE_DIR_WORDING = "which lands outside the directory"  # SPD-144: a patch's name that leaves -d's directory
GENERATED_WORDING = "generated from the ledger database"  # Law 5's reason for ledger/** and reports/**


def write_tar(path, names, mode="w"):
    """A tar at `path` holding one small member per name, a name ending in `/` a directory."""
    with tarfile.open(path, mode) as tf:
        for name in names:
            info = tarfile.TarInfo(name.rstrip("/"))
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                info.size = 2
                tf.addfile(info, io.BytesIO(b"x\n"))


def write_zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(zipfile.ZipInfo(name), b"" if name.endswith("/") else b"x\n")


class ArchiveCase(BashHookCase):
    """AGENT_K plans home:out/** and home:linked/**; out/ exists, and linked/ holds `ledger`, a symlink to the home's own
    ledger/ directory.  The archives and patches are built with the standard library in the home, the directory the Bash
    hook runs in."""

    def build_home(self):
        super().build_home()
        self.member = self.spawn(self.plan(actor=self.lead["ref"], persona="engineer", model="opus",
                                           deliverable=["home:out/**", "home:linked/**"]), AGENT_K, caller=AGENT_A)
        home = self.home.path
        for d in ("out", "linked", "ledger/tickets"):
            (home / d).mkdir(parents=True, exist_ok=True)
        (home / "linked" / "ledger").symlink_to(home / "ledger")
        clean = ["a.txt", "sub/", "sub/b.txt", "./dot/c.txt", "/abs/d.txt", "../up/e.txt"]
        write_tar(home / "clean.tar", clean)
        write_tar(home / "clean.tgz", clean, "w:gz")
        write_zip(home / "clean.zip", [n for n in clean if not n.startswith(("/", ".."))])
        write_tar(home / "git.tar", ["a.txt", ".git/config"])
        write_tar(home / "git.tar.xz", ["a.txt", "sub/.git/config"], "w:xz")
        write_tar(home / "hook.tar", ["a.txt", ".git/hooks/post-index-change"])
        write_zip(home / "git.zip", ["a.txt", ".git/config"])
        write_zip(home / "hook.zip", ["a.txt", "../.git/hooks/post-index-change"])  # unzip drops the `..`, keeps .git
        write_tar(home / "ledger.tar", ["ledger/tickets/SPD-001.md"])
        write_zip(home / "ledger.zip", ["ledger/tickets/SPD-001.md"])
        write_tar(home / "many.tar", ["f%04d" % i for i in range(1001)])
        write_zip(home / "tar-as.tar", ["a.txt", ".git/config"])  # bsdtar extracts a zip too
        (home / "nota.tar").write_text("not an archive\n", encoding="utf-8")
        (home / "nota.zip").write_text("not an archive\n", encoding="utf-8")
        (home / "in.patch").write_text("--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
        (home / "esc.patch").write_text("--- x.txt\n+++ ../escaped.txt\n@@ -0,0 +1 @@\n+b\n", encoding="utf-8")
        (home / "abs.patch").write_text("--- /dev/null\n+++ /Users/Nobody/.zshrc\n@@ -0,0 +1 @@\n+b\n", encoding="utf-8")
        (home / "git.patch").write_text("diff --git a/.git/hooks/pre-commit b/.git/hooks/pre-commit\n"
                                        "--- /dev/null\n+++ b/.git/hooks/pre-commit\n@@ -0,0 +1 @@\n+b\n", encoding="utf-8")
        write_tar(home / "dollar.tar", ["a$b.txt"])
        write_tar(home / "deep.tar", ["x/.git/config"])
        with zipfile.ZipFile(home / "unicode.zip", "w") as zf:  # unzip writes the Unicode Path field's name, not the header's
            info = zipfile.ZipInfo("safe.txt")
            name = b".git/config"
            info.extra = (0x7075).to_bytes(2, "little") + (5 + len(name)).to_bytes(2, "little") + b"\x01" + b"\0" * 4 + name
            zf.writestr(info, b"x\n")
        (home / "hunk.patch").write_text("--- a/x.txt\n+++ b/x.txt\n@@ -1,2 +1,2 @@\n--- ../../.git/config\n+++ y\n"
                                         " kept\n", encoding="utf-8")


class ArchiveNamesTest(ArchiveCase):
    """SPD-144: SPD-126 reads tar -x, unzip, ditto -x and patch as a write anywhere under the directory they land in, so a
    member whose globs cover that directory whole wrote whatever the archive or patch held -- a `.git/config`, a hook, a
    name that leaves the directory -- unread.  Each name is now held to the path rule as a spelled target is."""

    def test_an_archive_holding_a_git_file_is_refused(self):
        for command, needle in (("tar -xf git.tar -C out", GIT_FILE_WORDING), ("tar xf git.tar -C out", GIT_FILE_WORDING),
                                ("tar -xJf git.tar.xz -C out", GIT_FILE_WORDING), ("tar -xf hook.tar -C out", GIT_DIR_WORDING),
                                ("tar -xf git.tar -C /tmp/spd-144-x", GIT_FILE_WORDING),
                                ("tar -xf tar-as.tar -C out", GIT_FILE_WORDING),
                                ("cd out && tar -xf ../git.tar", GIT_FILE_WORDING),
                                ("unzip -q git.zip -d out", GIT_FILE_WORDING), ("unzip -o hook.zip -d out", GIT_DIR_WORDING),
                                ("ditto -x -k git.zip out", GIT_FILE_WORDING)):
            with self.subTest(command):
                reason = self.assertRefused(command, needle, agent_id=AGENT_K).reason
                self.assertIn(".git/", reason)  # the member's name is the reason's target

    def test_an_archive_holding_a_ledger_file_is_refused(self):
        # linked/ledger is the home's ledger/: the tree linked/ is the member's own, the file it lands is the ledger's
        for command in ("tar -xf ledger.tar -C linked", "unzip ledger.zip -d linked", "ditto -x -k ledger.zip linked"):
            with self.subTest(command):
                self.assertRefused(command, GENERATED_WORDING, agent_id=AGENT_K)

    def test_an_archive_holding_only_the_members_files_is_allowed(self):
        for command in ("tar -xf clean.tar -C out", "tar -xzf clean.tgz -C out/new", "tar -xf clean.tar -C out --strip-components 1",
                        "unzip -q clean.zip -d out", "unzip -j clean.zip -d out", "ditto -x -k clean.zip out",
                        "cd out && unzip ../clean.zip", "tar -tf git.tar", "unzip -l git.zip", "tar -xOf git.tar"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_K)

    def test_a_patch_is_read_as_patch_places_its_names(self):
        for command in ("patch -d out -p1 -i in.patch", "patch -p1 -d out -i ../in.patch", "patch -d out -p1 --dry-run -i esc.patch"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_K)
        for command, needle in (("patch -d out -p0 -i esc.patch", OUTSIDE_DIR_WORDING),
                                ("patch -d out -i esc.patch", OUTSIDE_DIR_WORDING),  # no -p: the whole relative name too
                                ("patch -d out -p0 -i abs.patch", OUTSIDE_DIR_WORDING),
                                ("patch -d out -p1 -i git.patch", GIT_DIR_WORDING),
                                ("patch -d out -i git.patch", GIT_DIR_WORDING)):  # git's b/ taken off with no -p
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_K)

    def test_the_analysis_records_each_name_under_the_directory(self):
        writes = [(w[1], w[6]) for w in self.hook_reading("tar -xf clean.tar -C out")["arg_writes"]]
        self.assertEqual(writes, [("out", "tree"), ("out/a.txt", None), ("out/sub", "make"), ("out/sub/b.txt", None),
                                  ("out/dot/c.txt", None), ("out/abs/d.txt", None)])  # bsdtar skips ../up/e.txt
        # patch's name, its backup and numbered backup (patch_backups), and its reject file beside it
        targets = [w[1] for w in self.hook_reading("patch -d out -p1 -i in.patch")["arg_writes"]]
        self.assertEqual((len(targets), targets[:3], targets[-1]), (5, ["out", "out/x.txt", "out/./x.txt.orig"], "out/./x.txt.rej"))

    def test_strip_components_and_the_unicode_path_field_are_read(self):
        self.assertSilent("tar -xf git.tar -C out --strip-components 1", agent_id=AGENT_K)  # .git/config lands as config
        self.assertRefused("tar -xf deep.tar -C out --strip-components 1", GIT_FILE_WORDING, agent_id=AGENT_K)
        self.assertRefused("unzip unicode.zip -d out", GIT_FILE_WORDING, agent_id=AGENT_K)
        self.assertRefused("tar -xf dollar.tar -C out", "cannot place as a path", agent_id=AGENT_K)

    def test_a_hunks_own_lines_are_not_headers(self):
        self.assertSilent("patch -d out -p1 -i hunk.patch", agent_id=AGENT_K)

    def test_a_patch_the_line_spells_on_standard_input_is_read(self):
        self.assertSilent("patch -d out -p1 <<'EOF'\n--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n+b\nEOF", agent_id=AGENT_K)
        self.assertRefused("patch -d out -p0 <<'EOF'\n--- x.txt\n+++ ../escaped.txt\n@@ -0,0 +1 @@\n+b\nEOF",
                           OUTSIDE_DIR_WORDING, agent_id=AGENT_K)


class UnlistedArchiveTest(ArchiveCase):
    """SPD-144 under SPD-217: what the hook cannot list is refused to a member, the form named with a readable respelling;
    Spud is answered as before."""

    def test_an_archive_the_hook_cannot_read_is_refused(self):
        for command in ("tar -xf nota.tar -C out", "unzip nota.zip -d out", "tar -xf missing.tar -C out",
                        "tar -xf many.tar -C out", "ditto -x clean.tar out", "patch -d out -p1 -i missing.patch"):
            with self.subTest(command):
                reason = self.assertRefused(command, UNLISTED_WORDING, agent_id=AGENT_K).reason
                self.assertNotIn(ANYWHERE_WORDING, reason)  # SPD-275: its own reason, not SPD-126's generic one

    def test_an_archive_the_line_writes_first_is_refused(self):
        for command in ("cp clean.tar out/c.tar && tar -xf out/c.tar -C out", "curl -sS -o out/d.zip https://example.com/d.zip; unzip out/d.zip -d out",
                        "cp in.patch out/p.patch && patch -d out -p1 -i p.patch", "cp -R . out/w; tar -xf out/w/clean.tar -C out"):
            with self.subTest(command):
                self.assertRefused(command, WRITTEN_WORDING, agent_id=AGENT_K)

    def test_an_archive_the_line_does_not_name_is_refused(self):
        # standard input the line does not name as a file: a pipe from a program, two inputs zsh reads one after the other,
        # a descriptor, a compound command's input
        for command, needle in (("cat clean.tar | tar -xf - -C out", STDIN_WORDING), ("cat in.patch | patch -d out -p1", STDIN_WORDING),
                                ("cat in.patch | patch -d out -p1 < in.patch", STDIN_WORDING),
                                ("patch -d out -p1 < in.patch < esc.patch", STDIN_WORDING),
                                ("patch -d out -p1 <&3 3< in.patch", STDIN_WORDING),
                                ("{ patch -d out -p1; } < in.patch", STDIN_WORDING),
                                ("cat git.zip | ditto -x -k - out", STDIN_WORDING),
                                ("tar -xf $(ls *.tar) -C out", UNSPELLED_WORDING),
                                ("find . -name '*.tar' -exec tar -xf {} -C out \\;", UNSPELLED_WORDING),
                                ("unzip '*.zip' -d out", UNSPELLED_WORDING), ("tar -xf *.tar -C out", UNSPELLED_WORDING),
                                ("patch -d out -p1 < $(ls *.patch)", UNSPELLED_WORDING), ("tar -x -C out < *.tar", UNSPELLED_WORDING),
                                ("cp esc.patch out/p.patch && patch -d out -p0 < out/p.patch", WRITTEN_WORDING)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_K)

    def test_a_file_the_line_redirects_to_standard_input_is_read(self):
        # SPD-144: `< file` names exactly what patch, tar with -f - or no -f, and ditto -x with `-` read
        for command in ("patch -d out -p1 < in.patch", "patch -p1 -d out 0< in.patch", "tar -x -C out < clean.tar",
                        "tar -xf - -C out < clean.tar", "ditto -x -k - out < clean.zip", "cd out && patch -p1 < ../in.patch"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_K)
        for command, needle in (("patch -d out -p0 < esc.patch", OUTSIDE_DIR_WORDING), ("patch -d out -p1 < git.patch", GIT_DIR_WORDING),
                                ("tar -xf - -C out < git.tar", GIT_FILE_WORDING), ("tar -x -C out < hook.tar", GIT_DIR_WORDING),
                                ("ditto -x -k - out < git.zip", GIT_FILE_WORDING), ("tar -x -C out < nota.tar", UNLISTED_WORDING)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=AGENT_K)

    def test_spuds_answers_are_unchanged(self):
        for command in ("tar -xf git.tar -C /tmp/spd-144-x", "tar -xf nota.tar -C /tmp/spd-144-x", "unzip git.zip -d /tmp/spd-144-x",
                        "tar -x -C /tmp/spd-144-x < clean.tar", "patch -d /tmp/spd-144-x -p0 -i esc.patch"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)
        for command, needle in (("tar -xf clean.tar -C out", "Law 1"), ("unzip nota.zip -d ledger", GENERATED_WORDING)):
            with self.subTest(command):
                self.assertRefused(command, needle, agent_id=None)

    def test_the_outside_allowlist_still_holds_a_name(self):
        self.assertRefused("patch -d /tmp/spd-144-x -p0 -i abs.patch", OUTSIDE_DIR_WORDING, agent_id=AGENT_K)
        self.assertRefused("tar -xf clean.tar -C /Users/Nobody", OUTSIDE, agent_id=AGENT_K)


ARCHIVE_WORDING = "write it in one Bash call and extract or apply it in the next"  # SPD-275: the respelling every cause gets
CAPS_WORDING = "1000 names"  # SPD-275: the caps the reason names


class ArchiveReasonTest(ArchiveCase):
    """SPD-275 (proposal by SPUD-144/Locutus): SPD-144 refused a member an archive or a patch the hook cannot list as a
    write anywhere, syntax.ANY_PATH with the cause after it, so the reason read as SPD-126's generic one ("places files where
    the line cannot say", tar's -P and curl's config among it).  Each cause is now an "archive" finding of its own, read in
    bash_rule's member loop, with a reason naming the archive, the cause and the respelling.  out/in.tar, out/in.zip and
    out/p.patch are archives in the member's own tree, which a line may write."""

    def build_home(self):
        super().build_home()
        home = self.home.path
        write_tar(home / "out" / "in.tar", ["a.txt", "sub/b.txt"])
        write_zip(home / "out" / "in.zip", ["a.txt", "sub/b.txt"])
        (home / "out" / "p.patch").write_text("--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
        (home / "big.patch").write_text("--- a/x.txt\n+++ b/x.txt\n" + "#" * (1 << 20), encoding="utf-8")
        write_tar(home / "out" / "self.tar", ["a.txt", "self.tar"])  # extracting it writes the archive itself

    def test_each_cause_is_refused_with_a_reason_of_its_own(self):
        for command, cause, shown in (("tar -xf nota.tar -C out", "is not a tar or zip archive", "`nota.tar`"),
                                      ("unzip nota.zip -d out", "is not a tar or zip archive", "`nota.zip`"),
                                      ("tar -xf missing.tar -C out", "does not exist now", "`missing.tar`"),
                                      ("tar -xf many.tar -C out", "holds more than 1000 names", "`many.tar`"),
                                      ("ditto -x clean.tar out", "cpio", "`clean.tar`"),
                                      ("tar -xf dollar.tar -C out", "cannot place as a path", "`dollar.tar`"),
                                      ("patch -d out -p1 -i big.patch", "larger than the 1024 KiB", "`big.patch`"),
                                      ("cat clean.tar | tar -xf - -C out", STDIN_WORDING, "`tar`"),
                                      ("cat in.patch | patch -d out -p1", STDIN_WORDING, "`patch`"),
                                      ("tar -xf $(ls *.tar) -C out", UNSPELLED_WORDING, "$(...)"),
                                      ("tar -xf *.tar -C out", UNSPELLED_WORDING, "*.tar"),
                                      ("cp clean.tar out/c.tar && tar -xf out/c.tar -C out", WRITTEN_WORDING, "`out/c.tar`"),
                                      ("patch -d out -p0 -i esc.patch", OUTSIDE_DIR_WORDING, "`esc.patch`"),
                                      ("tar -xf clean.tar -s /a/b/ -C out", "rewrites the names", "`clean.tar`"),
                                      ("tar -x --use-compress-program cat -f clean.tar -C out", "--use-compress-program",
                                       "`clean.tar`")):
            with self.subTest(command):
                reason = self.assertRefused(command, cause, agent_id=AGENT_K).reason
                self.assertIn(shown, reason)
                self.assertIn(ARCHIVE_WORDING, reason)
                self.assertIn(CAPS_WORDING, reason)
                self.assertNotIn(ANYWHERE_WORDING, reason)
        self.assertIn("../escaped.txt", self.bash("patch -d out -p0 -i esc.patch", AGENT_K).reason)

    def test_the_cause_is_a_finding_not_a_write(self):
        reading = self.hook_reading("tar -xf nota.tar -C out")
        self.assertEqual([(w[1], w[6]) for w in reading["arg_writes"]], [("out", "tree")])
        self.assertEqual([kind for kind, _ in reading["findings"]], ["archive"])

    def test_a_write_of_the_line_that_may_land_on_the_archive_first_is_refused(self):
        # beside it (a background job, a pipeline), or in a loop's next pass, a write the reading meets after the command
        for command in ("tar -xf out/in.tar -C out & cp git.tar out/in.tar",
                        "for i in 1 2; do tar -xf out/in.tar -C out; cp git.tar out/in.tar; done",
                        "while true; do tar -xf out/in.tar -C out; cp git.tar out/in.tar; done",
                        "tar -xvf out/in.tar -C out | cat > out/in.tar",
                        "tar -xvf out/in.tar -C out | cp git.tar out/in.tar",
                        "tar -xvf out/in.tar -C out | tee out/x.txt out/in.tar",
                        "tar -x -C out < out/in.tar & cp git.tar out/in.tar",
                        "unzip -o out/in.zip -d out & cp git.zip out/in.zip",
                        "unzip -o out/in -d out & cp git.zip out/in.zip",  # unzip tries in.zip when `in` is not there
                        "ditto -x -k out/in.zip out & cp git.zip out/in.zip",
                        "patch -p1 -d out < out/p.patch & cp git.patch out/p.patch",
                        "for i in 1 2; do patch -p1 -d out < out/p.patch; cp git.patch out/p.patch; done",
                        "tar -xf out/in.tar -C out & cp git.tar out/*.tar",
                        "tar -xf out/in.tar -C out & rsync -a git.tar out/",
                        "sh -c 'tar -xf out/in.tar -C out & cp git.tar out/in.tar'",  # a nested reading
                        "f() { tar -xf out/in.tar -C out; }; f; cp git.tar out/in.tar; f",  # a function's next call
                        # a pass that writes the archive itself before the next pass reads it
                        "for i in 1 2; do tar -xf out/self.tar -C out; done",
                        "find out -name a.txt -exec tar -xf out/self.tar -C out \\;"):
            with self.subTest(command):
                reason = self.assertRefused(command, WRITTEN_WORDING, agent_id=AGENT_K).reason
                self.assertIn(ARCHIVE_WORDING, reason)

    def test_an_archive_the_line_never_writes_is_read_as_today(self):
        # SPD-144's reading, unchanged: beside a write that does not land on it, in a loop, after it on the line
        for command in ("tar -xf out/in.tar -C out | cat", "tar -xf out/in.tar -C out & echo x > out/x.txt",
                        "for i in 1 2; do tar -xf out/in.tar -C out; done", "cd out && tar -xf in.tar",
                        "for i in 1 2; do tar -xf clean.tar -C out; done", "for i in 1 2; do unzip -o out/in.zip -d out; done",
                        "patch -p1 -d out < out/p.patch | cat", "tar -xf out/in.tar -C out; cp git.tar out/in.tar",
                        "for i in 1 2; do ditto -x -k out/in.zip out; done", "tar -xf clean.tar -C out & cp git.tar out/other.tar",
                        "tar -xf out/self.tar -C out", "tar -xf out/self.tar -C out | cat"):  # it opens the file before it writes
            with self.subTest(command):
                self.assertSilent(command, agent_id=AGENT_K)

    def test_spuds_answers_are_unchanged(self):
        for command in ("tar -xf /tmp/spd-275-x/in.tar -C /tmp/spd-275-x & cp clean.tar /tmp/spd-275-x/in.tar",
                        "cat clean.tar | tar -xf - -C /tmp/spd-275-x", "tar -xf nota.tar -C /tmp/spd-275-x",
                        "for i in 1 2; do tar -xf /tmp/spd-275-x/c.tar -C /tmp/spd-275-x; cp git.tar /tmp/spd-275-x/c.tar; done"):
            with self.subTest(command):
                self.assertSilent(command, agent_id=None)
