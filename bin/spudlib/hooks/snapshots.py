"""hooks/snapshots: the aliases and functions the Bash tool's shell already holds, read from Claude Code's shell snapshot."""

import contextlib
import json
import os
import re

from . import hookio
from ..core import homeconf


# The escapes zsh 5.9 and bash 3.2 both decode inside `$'...'`, each to the one character it names, beside a code of one to
# three octal digits or of `x` and one or two hex digits (ansi_c_value).  Probed through tests/probes/shell_probe.py
# (AnsiCQuotingTest has what each printed): they part on every other escape -- `\u` and `\U`, which bash 3.2 keeps as
# text, `\c`, an unknown letter, whose backslash zsh drops and bash keeps, a bare `\x`, a backslash-newline -- and on NUL.
# The decoder lives here, beside unquote_word, rather than in shell/prepare, whose ANSI-C pass (ansi_c_quotes) and
# shell/heredocs read it too: every module that loads this one -- commands/doctor, and so every `spud` command -- would
# otherwise load the shell package for it (SPD-216).
_ANSI_C_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
                   "\\": "\\", "'": "'", '"': '"', "?": "?"}
_ANSI_C_CODE_RE = re.compile(r"([0-7]{1,3})|x([0-9A-Fa-f]{1,2})")


def ansi_c_value(body):
    """The text `$'body'` stands for, where zsh and bash decode every escape in it alike (_ANSI_C_ESCAPES), or None where
    they part, or where a code is NUL or past 0x7f, one byte of a character a command line held as text cannot spell."""
    if "\\" not in body:
        return body
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        escape = body[i + 1 : i + 2]
        if escape and escape in _ANSI_C_ESCAPES:
            out.append(_ANSI_C_ESCAPES[escape])
            i += 2
            continue
        m = _ANSI_C_CODE_RE.match(body, i + 1)
        if m is None:
            return None
        code = int(m.group(1), 8) if m.group(1) else int(m.group(2), 16)
        if not 0 < code < 0x80:
            return None
        out.append(chr(code))
        i = m.end()
    return "".join(out)


# Claude Code writes a snapshot of the user's interactive shell -- ~/.claude/shell-snapshots/snapshot-<shell>-<stamp>-<id>.sh
# -- and sources it in the shell it starts for every Bash call, so a member's command word is expanded by the aliases and
# functions the user's profile defines before any program runs.  Before this table the hook read the word as written: `gc -m x`
# committed, `gp` and `ggp` pushed, `g commit -m x` committed, each read as an unknown command with no finding.
#
# Which snapshot a session sources is not in the hook's input, so every one of them is read, newest first, and the table is
# their union: they are the same profile, and the newest snapshot that names a name decides it.
#
# A snapshot is machine-written and its shape is fixed: `unalias -a`, one `name () { ... }` per function, `setopt` lines,
# one `alias -- name=body` per alias, then whatever the harness appends -- on this Mac an `unalias` and a `function name {
# ... }` for each command Claude Code shadows (find, grep, pkill, and rg where it is not on PATH), which is why a snapshot
# is read in line order: a later `unalias` clears an alias and a later definition wins, as the shell's own reading does.
#
# Nothing here writes into ~/.claude: the snapshots are the user's files, read and never touched.
SNAPSHOTS = "shell-snapshots"
SNAPSHOT_PREFIX = "snapshot-"
CACHE_NAME = "shell-snapshot.json"  # the parsed table, under the home's .spud/, keyed by every snapshot's size and mtime
BODY_CAP = 64 * 1024  # the most of one function body the hook reads; nothing a profile defines comes near it
# A snapshot's `alias` line: zsh writes `alias -- name=body`, with the options it was defined with before the `--`.  A
# global (`-g`) alias is expanded in every word, not only in command position, which the hook does not yet read on a line
# the member writes, nor this table: it is recorded here all the same, since covering its command position is strictly more
# than covering none of it.  A suffix (`-s`) alias runs a program for a word ending in its extension and names no command
# word, so it is skipped.  Neither form is in this Mac's snapshots (497 aliases, all plain).
ALIAS_RE = re.compile(r"^alias(?P<options>(?: +-[A-Za-z]+)*)(?: +--)? +(?P<rest>\S.*)$", re.S)
UNALIAS_RE = re.compile(r"^unalias(?P<options>(?: +-[A-Za-z]+)*) +(?P<rest>\S.*)$", re.S)
# `name () {` as zsh prints a function, and `function name {` as Claude Code writes the commands it shadows; the body runs
# to the first line that is `}` alone.  A definition may be indented (the harness writes its `rg` shadow inside an `if`),
# and the closing brace of an indented one is at column 0 there, so the closer is read stripped.
FUNCTION_RE = re.compile(r"^\s*(?:function\s+)?(?P<name>[^\s(){}=#|&;<>'\"]+)\s*(?:\(\s*\))?\s*\{$")


UNJUDGED = object()  # Table.verdict's answer where no verdict holds (SPD-134)


class Table:
    """What the shell the Bash tool starts already defines: `aliases`, each name's body as the shell stores it (None for a
    body the hook cannot read); `functions`, each name's body as (which snapshot, its first byte, its length); `gap`, what
    stopped the table being read, or None.  An empty table is the answer on a machine with no snapshots, and it keeps every
    line the reading it had without one.

    SPD-134: `verdicts`, what the hook judged of a function's body (shell/expansions.inert_kinds), each name -> (which
    snapshot, its first byte, its length, the kinds of command its reading runs where it was judged inert, else None);
    `code`, the program_key of the program that judged them; `fingerprint`, the snapshots' (name, size, mtime) the table
    was read at, which a verdict is written back to the cache beside."""

    def __init__(self, aliases=None, functions=None, files=(), gap=None, fingerprint=None, verdicts=None, code=None):
        self.aliases = aliases if aliases is not None else {}
        self.functions = functions if functions is not None else {}
        self.files = list(files)
        self.gap = gap
        self.fingerprint = fingerprint
        self.verdicts = verdicts if verdicts is not None else {}
        self.code = code
        self.program = None  # program_key(), once this process has read it

    def current_program(self):
        """program_key() for this process, read once."""
        if self.program is None:
            self.program = program_key()
        return self.program

    def verdict(self, name):
        """What the hook judged of this function's body: the kinds of command its reading runs where it is inert, None
        where it is not, UNJUDGED where no verdict holds -- none kept, one kept for a body the name no longer names, or
        one another program judged (a merge that changes the reading changes what a body earns)."""
        kept = self.verdicts.get(name)
        if kept is None or tuple(kept[:3]) != self.functions.get(name) or self.code != self.current_program():
            return UNJUDGED
        return kept[3]

    def body(self, name):
        """The shell text a function of this name runs, or None: read from the snapshot only when a line names it, so no
        hook run pays for the 120 KB of function bodies a profile holds."""
        where = self.functions.get(name)
        if where is None:
            return None
        index, start, length = where
        if not 0 <= index < len(self.files):
            return None
        try:
            with open(self.files[index], "rb") as f:
                f.seek(start)
                data = f.read(min(length, BODY_CAP))
        except OSError:
            return None
        return data.decode("utf-8", "replace")


EMPTY_TABLE = Table()
_TABLES = {}  # the home's path -> its table, read once per process


def snapshot_dir():
    """Where Claude Code writes the snapshots it sources for the Bash tool (SPUD_USER_CLAUDE_DIR moves ~/.claude, for tests)."""
    return os.path.join(str(homeconf.user_claude_dir()), SNAPSHOTS)


def shell_table(home):
    """The shell's table for this home, read once per process: from the home's cache while every snapshot's size and mtime
    are what the cache was built at, else parsed and cached again."""
    key = str(home)
    if key not in _TABLES:
        _TABLES[key] = load_table(key)
    return _TABLES[key]


def load_table(home):
    """The table as shell_table caches it.  A snapshot directory that is missing, or holds no snapshot, is an empty table
    and no gap: that is a machine where Claude Code sources nothing.  One the hook cannot list or read is a gap, which the
    Bash hook spools and `spud doctor` reports, while every line keeps the reading it had without the table."""
    directory = snapshot_dir()
    entries = []
    try:
        with os.scandir(directory) as listing:
            for entry in listing:
                if entry.name.startswith(SNAPSHOT_PREFIX) and entry.name.endswith(".sh"):
                    stat = entry.stat()
                    entries.append((entry.name, stat.st_size, stat.st_mtime_ns))
    except FileNotFoundError:
        return EMPTY_TABLE  # no snapshot directory: this shell defines nothing, and every line keeps the reading it had
    except OSError as e:
        return Table(gap="cannot list %s: %s" % (directory, e))
    if not entries:
        return EMPTY_TABLE
    entries.sort(key=lambda e: (-e[2], e[0]))  # newest first: the newest snapshot that names a name decides it
    files = [os.path.join(directory, name) for name, _, _ in entries]
    cache = os.path.join(str(home), hookio.STATE_DIR, CACHE_NAME)
    with contextlib.suppress(OSError, ValueError, TypeError, KeyError, AttributeError):
        with open(cache, encoding="utf-8") as f:
            stored = json.load(f)
        if stored["fingerprint"] == [list(e) for e in entries] and isinstance(stored["aliases"], dict):
            code, verdicts = stored_verdicts(stored.get("verdicts"))
            return Table(stored["aliases"], {k: tuple(v) for k, v in stored["functions"].items()}, files, fingerprint=entries,
                         verdicts=verdicts, code=code)
    built = build_table(files)
    if built.gap is None:
        built.fingerprint = entries
        write_cache(cache, entries, built)
    return built


def stored_verdicts(section):
    """(the program key, the verdicts) a cached table holds, or (None, {}) where it holds none the hook can read: a table
    cached before SPD-134, or verdicts of any shape but record_verdict's.  A verdict the hook cannot read is no verdict,
    and its body is judged again rather than skipped."""
    if not isinstance(section, dict) or type(section.get("code")) is not int or not isinstance(section.get("bodies"), dict):
        return None, {}
    verdicts = {}
    for name, kept in section["bodies"].items():
        if not (isinstance(kept, list) and len(kept) == 4 and all(type(x) is int for x in kept[:3])):
            return None, {}
        kinds = kept[3]
        if kinds is not None and not (isinstance(kinds, list) and all(isinstance(k, str) for k in kinds)):
            return None, {}
        verdicts[name] = (kept[0], kept[1], kept[2], kinds)
    return section["code"], verdicts


def record_verdict(home, table, name, kinds):
    """Keep what the hook judged of `name`'s body (shell/expansions.inert_kinds) with the table in the home's cache, for
    this program: a verdict another program judged is dropped rather than kept beside it."""
    code = table.current_program()
    if table.code != code:
        table.verdicts, table.code = {}, code
    table.verdicts[name] = tuple(table.functions[name]) + (kinds,)
    if table.fingerprint is not None:
        write_cache(os.path.join(str(home), hookio.STATE_DIR, CACHE_NAME), table.fingerprint, table)


def program_key():
    """A key of the program reading the Bash tool's lines: the size and mtime of every source file under bin/, which a
    merge or an edit of any of them changes.  A verdict on a body holds only for the reading that judged it.  Only
    integers are hashed, whose hash, unlike a string's, is the same in every process."""
    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stats = []
    for directory in [os.path.dirname(package)] + sorted(os.path.join(package, d) for d in os.listdir(package)):
        with contextlib.suppress(OSError):
            with os.scandir(directory) as listing:
                for entry in sorted(listing, key=lambda e: e.name):
                    if entry.name.endswith(".py") and entry.is_file():
                        stat = entry.stat()
                        stats.append((stat.st_size, stat.st_mtime_ns))
    return hash(tuple(stats))


def build_table(files):
    """The union of these snapshots, newest first: the first one that names a name decides what that name is, so a name a
    newer snapshot unaliased is not taken from an older one."""
    aliases, functions, decided = {}, {}, set()
    for index, path in enumerate(files):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            return Table(gap="cannot read %s: %s" % (path, e))
        file_aliases, file_functions, named = read_snapshot(data, index)
        for name in named:
            if name in decided:
                continue
            decided.add(name)
            if name in file_aliases:
                aliases[name] = file_aliases[name]
            elif name in file_functions:
                functions[name] = file_functions[name]
    return Table(aliases, functions, files)


def read_snapshot(data, index):
    """(the aliases this snapshot leaves defined, its functions as (index, the body's first byte, its length), every name
    any of its lines names).  The lines are read in order, so a later `unalias` clears an alias and a later definition
    replaces an earlier one, as the shell reading the same file does."""
    aliases, functions, named = {}, {}, []
    lines = data.split(b"\n")
    offsets, at = [], 0
    for raw in lines:
        offsets.append(at)
        at += len(raw) + 1
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if raw.startswith(b"#") or not raw.strip():
            continue
        line = raw.decode("utf-8", "replace").rstrip("\r")
        if raw.startswith(b"alias"):
            found = alias_definition(line)
            if found is not None:
                name, body = found
                aliases[name] = body
                functions.pop(name, None)
                named.append(name)
            continue
        if raw.startswith(b"unalias"):
            for name in unalias_names(line, aliases):
                aliases.pop(name, None)
                named.append(name)
            continue
        m = FUNCTION_RE.match(line)
        if m is None:
            continue
        start = i
        while i < len(lines) and lines[i].strip() != b"}":
            i += 1
        if i >= len(lines):  # an opener with no closing brace: not a definition the hook can read
            i = start
            continue
        name = m.group("name")
        functions[name] = (index, offsets[start], offsets[i] - offsets[start])
        aliases.pop(name, None)
        named.append(name)
        i += 1
    return aliases, functions, named


def alias_definition(line):
    """(the name, the body as the shell stores it) of a snapshot's `alias` line, or None for a suffix alias, a query, or a
    line that is no definition.  The body is None where the hook cannot take the line's quoting off it."""
    m = ALIAS_RE.match(line)
    if m is None or "-s" in m.group("options").split():
        return None
    spelled, sep, body = m.group("rest").partition("=")
    if not sep:
        return None  # `alias name` prints a definition and makes none
    name = unquote_word(spelled) or spelled
    return (name, unquote_word(body)) if name else None


def unalias_names(line, aliases):
    """The alias names an `unalias` line clears: every name it lists, or every name defined so far for `-a`.  The words
    after a redirection or a list operator belong to the rest of the line (`unalias find 2>/dev/null || true`), and `-m`
    takes patterns, which the hook does not match -- it clears nothing rather than guess."""
    m = UNALIAS_RE.match(line)
    if m is None:
        return []
    options = m.group("options").split()
    if "-a" in options:
        return list(aliases)
    if "-m" in options:
        return []
    names = []
    for word in m.group("rest").split():
        if word in ("||", "&&", ";", "|", "&") or any(c in word for c in "<>|&;()"):
            break
        names.append(unquote_word(word) or word)
    return names


def unquote_word(text):
    """The text the shell keeps once it has taken one level of quoting off this word, or None when the quoting does not
    close.  What comes out is shell text again, which is what the shell parses when it expands the alias: an `awk
    '\\''{print $1}'\\''` in a body reaches the analysis as `awk '{print $1}'`, quotes and all.  `$'...'` is its value
    (SPD-202): zsh prints an alias whose body holds a newline that way (probed: `alias -L` printed `alias nl=$'echo
    a\\necho b'`), and the body is two commands, not `echo anecho b`.  One whose escapes the hook does not decode
    (ansi_c_value; zsh printed a carriage return as `\\C-M`) is quoting it cannot take off, and None."""
    if not text:
        return text
    if "'" not in text and '"' not in text and "\\" not in text:
        return text
    if text.startswith("'") and text.endswith("'") and len(text) > 1 and "'" not in text[1:-1] and "\\" not in text:
        return text[1:-1]
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "$" and text.startswith("$'", i):
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            value = ansi_c_value(text[i + 2 : j]) if j < n else None
            if value is None:
                return None
            out.append(value)
            i = j + 1
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            out.append(text[i + 1 : j])
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '$`"\\':
                    out.append(text[i + 1])
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i >= n:
                return None
            i += 1
        elif c == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def write_cache(cache, entries, built):
    """Store the parsed table under the home's .spud/, atomically and only where that directory already exists: the CLI
    makes it at `spud init`, and a hook never creates a state directory of its own, as the worktree list cache never does."""
    if not os.path.isdir(os.path.dirname(cache)):
        return
    tmp = "%s.%d.tmp" % (cache, os.getpid())
    stored = {"fingerprint": [list(e) for e in entries], "aliases": built.aliases,
              "functions": {k: list(v) for k, v in built.functions.items()}}
    if built.code is not None:
        stored["verdicts"] = {"code": built.code, "bodies": {k: list(v) for k, v in built.verdicts.items()}}
    with contextlib.suppress(OSError):
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stored, f)
        os.replace(tmp, cache)
    with contextlib.suppress(OSError):
        os.unlink(tmp)


def table_report(home):
    """What `spud doctor` says about the shell table: (a problem or None, a note or None)."""
    found = shell_table(home)
    if found.gap is not None:
        return ("the hook cannot read this Mac's shell snapshots, so it reads a member's command word as written and an"
                " alias or function of the shell's runs unread: %s" % found.gap), None
    if not found.files:
        return None, "no shell snapshot in %s: the hook reads a member's command word as written" % snapshot_dir()
    unreadable = sorted(n for n, body in found.aliases.items() if body is None)
    note = "shell snapshot: %d aliases and %d functions from %d file%s" % (
        len(found.aliases), len(found.functions), len(found.files), "" if len(found.files) == 1 else "s")
    if unreadable:
        note += "; %d alias bod%s the hook cannot read (%s)" % (len(unreadable), "y" if len(unreadable) == 1 else "ies",
                                                                ", ".join(unreadable[:5]))
    return None, note
