"""hooks/snapshots: the aliases, functions and options the Bash tool's shell already holds, read from Claude Code's shell snapshot.

Past 250 lines as one reading: the snapshot file's grammar -- its alias, unalias, option and function lines and the
quoting on them (unquote_word, ansi_c_value), the harness's own shadows it appends -- and the table and cache built from
it, which every reader of the shell's own text asks (shell/line_aliases, shell/held_options, shell/held_shadows)."""

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
# A snapshot is machine-written and its shape is fixed: `unalias -a`, one `name () { ... }` per function, `setopt` lines
# (bash's `shopt -p` lines, before the functions), one `alias -- name=body` per alias, then whatever the harness appends -- on this Mac an `unalias` and a `function name {
# ... }` for each command Claude Code shadows (find, grep, pkill, and rg where it is not on PATH), which is why a snapshot
# is read in line order: a later `unalias` clears an alias and a later definition wins, as the shell's own reading does.
#
# Nothing here writes into ~/.claude: the snapshots are the user's files, read and never touched.
SNAPSHOTS = "shell-snapshots"
SNAPSHOT_PREFIX = "snapshot-"
BASH_SNAPSHOT_PREFIX = SNAPSHOT_PREFIX + "bash-"  # `snapshot-<shell>-<stamp>-<id>.sh`: a bash's, read as bash reads it
CACHE_NAME = "shell-snapshot.json"  # the parsed table, under the home's .spud/, keyed by every snapshot's size and mtime
# What a cache holds, which changes whenever the table does: one of another format, or of none (written before SPD-263
# added the options), is built again, never read, since a table read from it would lack what this one reads -- format 2's
# held a global alias among the plain ones and no suffix alias at all (SPD-283), format 3 a bash snapshot's `shopt` line a
# later one overrides and its hyphenated `set -o` name as an option line it could not take apart (SPD-329).
CACHE_FORMAT = 4
# A snapshot's option lines, which the shell runs before every Bash call (SPD-263): zsh's `setopt <name>` (and unsetopt),
# bash's `shopt -s|-u <name>`, and `set -o|+o <name>`, each at the start of a line of its own (a function's body, which
# runs only when called, is skipped as a whole).  Read as (kind, the option as spelled, on or off), with the line itself
# for a reason to show; shell/held_options.line_options reads what each means for the line.  A line of any other shape --
# an option builtin with a flag of its own, a redirection, an operator, quoting -- is kept with no option (None), which
# the reader takes as one it cannot model.  A name may hold a hyphen after its first character, as bash's
# interactive-comments does (SPD-329: Claude Code writes `set -o interactive-comments` into a bash snapshot).
OPTION_RE = re.compile(r"^(?P<builtin>setopt|unsetopt|shopt|set)(?P<rest>(?:\s.*)?)$", re.S)
OPTION_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")
BODY_CAP = 64 * 1024  # the most of one function body the hook reads; nothing a profile defines comes near it
# A snapshot's `alias` line: `alias -- name=body`, any options before the `--`.  Claude Code writes the alias block from
# zsh's plain `alias` listing, one `alias -- ` line per entry, and that listing prints a global alias with no flag and a
# suffix alias not at all (probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py: after
# `alias -g GL='| cat'` and `alias -s txt=...`, `alias` printed `GL='| cat'` and no txt, and a file of its lines sourced
# back made GL plain).  So a profile's global alias is a plain one in the shell the Bash tool starts, and no snapshot
# today holds either form: this Mac's oh-my-zsh defines `alias -g ...='../..'` (lib/directories.zsh), its snapshots write
# `alias -- ...=../..`, and in a member's own Bash call on 2026-09-24 `echo ... ....` printed `... ....`, `alias -g` and
# `alias -s` nothing.  A line that names `-g` or `-s` is read as the kind it names all the same (SPD-283), for a writer
# that keeps the flag, as `alias -L` does (it printed `alias -g GL='| cat'`, and `alias -L -s` `alias -s txt=...`): a
# global alias, which zsh expands in every word it reads unquoted, and a suffix alias, which runs its body before a
# command word ending in its suffix, each in a table of its own (Table.galiases, Table.saliases), read by
# shell/line_aliases.held_aliases.  zsh keeps a plain and a global alias in one table, so one of a name replaces the
# other, and a suffix alias in another; a line naming both flags defines nothing ("illegal combination of options").
ALIAS_RE = re.compile(r"^alias(?P<options>(?: +-[A-Za-z]+)*)(?: +--)? +(?P<rest>\S.*)$", re.S)
ALIAS_KINDS = ("plain", "global", "suffix")  # read_snapshot's tables of aliases, one per kind (alias_definition)
UNALIAS_RE = re.compile(r"^unalias(?P<options>(?: +-[A-Za-z]+)*)(?: +(?P<rest>\S.*))?$", re.S)  # `unalias -a` alone too
# `name () {` as zsh prints a function, and `function name {` as Claude Code writes the commands it shadows; the body runs
# to the first line that is `}` alone.  A definition may be indented (the harness writes its `rg` shadow inside an `if`),
# and the closing brace of an indented one is at column 0 there, so the closer is read stripped.
FUNCTION_RE = re.compile(r"^\s*(?:function\s+)?(?P<name>[^\s(){}=#|&;<>'\"]+)\s*(?:\(\s*\))?\s*\{$")

# Claude Code's own shadows (SPD-247): the body of each function the harness writes after the profile, as Table.body
# returns it, byte for byte as every snapshot on this Mac held it on 2026-09-24, with CLAUDE_BIN where it writes the claude
# binary it installed (this Mac's /Users/<user>/.local/bin/claude).  find, grep and rg run that binary as bfs, ugrep and rg
# through `"$_cc_bin"`, which ${CLAUDE_CODE_EXECPATH:-} may name instead, and fall back to `command <name>`; pkill refuses a
# pattern matching the CLI's own process, then runs `command pkill`.  shell/held_shadows.read_shadow reads a call of one as what
# the full reading of its body records; any other text -- another harness version, a profile's own function of the name, one
# byte changed -- is read in full (tests/test_hooks_snapshots.py HarnessShadowReadingTest).
CLAUDE_BIN = "\x00"
_CLAUDE_BIN_RE = re.compile(r"/(?:[A-Za-z0-9_.+-]+/)*claude\Z")  # an absolute path, no quoting, to a file named claude
_RUNS_CLAUDE = ('  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"\n  [[ -x $_cc_bin ]] || _cc_bin=' + CLAUDE_BIN + '\n'
                '  if [[ ! -x $_cc_bin ]]; then command %(name)s ${1+"$@"}; return; fi\n'
                '  if [[ -n ${ZSH_VERSION:-} ]]; then\n    ARGV0=%(runs)s "$_cc_bin" %(options)s${1+"$@"}\n'
                '  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then\n'
                '    ARGV0=%(runs)s "$_cc_bin" %(options)s${1+"$@"}\n'
                '  else\n    (exec -a %(runs)s "$_cc_bin" %(options)s${1+"$@"})\n  fi\n')
HARNESS_SHADOWS = {
    "find": _RUNS_CLAUDE % {"name": "find", "runs": "bfs", "options": "-S dfs -regextype findutils-default "},
    "grep": ('  local _cc_a\n  for _cc_a in ${1+"$@"}; do\n'
             '    case "$_cc_a" in -*-filter*|-*-pager*|-*-view*|-*-format-open*|-*-config*|---*|-@*|-*-save-config*|'
             '-[Zz]*|-[!-]*[Zz]*|--null|--null-data) command grep ${1+"$@"}; return ;; esac\n'
             '  done\n' + _RUNS_CLAUDE % {"name": "grep", "runs": "ugrep", "options": "-G --ignore-files --hidden -I "
                                          "--exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr "
                                          "--exclude-dir=.jj --exclude-dir=.sl "}),
    "rg": _RUNS_CLAUDE % {"name": "rg", "runs": "rg", "options": ""},
    "pkill": ('  if [ -n "${CLAUDE_PID:-}" ] && [ -r "/proc/${CLAUDE_PID}/comm" ]; then\n    local _cc_skip="" _cc_a\n'
              '    local -a _cc_probe=()\n    for _cc_a in ${1+"$@"}; do\n      if [ -n "$_cc_skip" ]; then _cc_skip=""; continue; fi\n'
              '      case "$_cc_a" in\n        --signal) _cc_skip=1 ;;\n        --signal=*|-e|--echo) ;;\n        -[0-9]*) ;;\n'
              '        -[PUGOF]?*) _cc_probe+=("$_cc_a") ;;\n        -[ABCDEFGHIJKLMNOPQRSTUVWXYZ][ABCDEFGHIJKLMNOPQRSTUVWXYZ0-9]*) ;;\n'
              '        *) _cc_probe+=("$_cc_a") ;;\n      esac\n    done\n'
              '    if command pgrep ${_cc_probe[@]+"${_cc_probe[@]}"} 2>/dev/null | command grep -qx "${CLAUDE_PID}"; then\n'
              "      printf 'pkill: refusing to run — this pattern matches the Claude CLI process (PID %s). Narrow the "
              "pattern, or target your own children with `pkill -P $$ ...`.\\n' \"${CLAUDE_PID}\" >&2\n      return 1\n"
              "    fi\n  fi\n  command pkill ${1+\"$@\"}\n"),
}
_SHADOW_ENDS = {name: text.partition(CLAUDE_BIN)[::2] for name, text in HARNESS_SHADOWS.items()}  # (before, after) the hole


def harness_shadow(name, body):
    """The claude binary the body of the function `name` runs when the body is byte for byte the harness's own shadow
    (HARNESS_SHADOWS) -- "" for pkill's, which names none -- or None for any other text.  The binary is the one word of the
    text that differs between machines, taken only as the harness spells it: an absolute path to a file named claude."""
    ends = _SHADOW_ENDS.get(name)
    if ends is None or body is None:
        return None
    before, after = ends
    if CLAUDE_BIN not in HARNESS_SHADOWS[name]:
        return "" if body == before else None
    if len(body) <= len(before) + len(after) or not body.startswith(before) or not body.endswith(after):
        return None
    claude = body[len(before) : len(body) - len(after)]
    return claude if _CLAUDE_BIN_RE.match(claude) else None


class Table:
    """What the shell the Bash tool starts already defines: `aliases`, each plain alias's name -> its body as the shell
    stores it (None for a body the hook cannot read); `galiases` and `saliases`, the same for each global alias and each
    suffix alias, keyed by its suffix (SPD-283, ALIAS_RE); `functions`, each name's body as (which snapshot, its first byte,
    its length); `options`, every option line any snapshot runs, as (kind, the option or None, on, the line, which snapshot)
    -- kind "setopt", "shopt" or "set" -- each once, from the newest snapshot that has it (SPD-263: which snapshot a session
    sources is not in the hook's input, so an option any of them sets may be in force); `gap`, what stopped the table being
    read, or None.  An empty table is the answer on a machine with no snapshots, and it keeps every line the reading it had
    without one."""

    def __init__(self, aliases=None, functions=None, files=(), gap=None, options=(), galiases=None, saliases=None):
        self.aliases = aliases if aliases is not None else {}
        self.galiases = galiases if galiases is not None else {}
        self.saliases = saliases if saliases is not None else {}
        self.functions = functions if functions is not None else {}
        self.files = list(files)
        self.gap = gap
        self.options = tuple(options)

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
        if (stored["format"] == CACHE_FORMAT and stored["fingerprint"] == [list(e) for e in entries]
                and all(isinstance(stored[k], dict) for k in ("aliases", "galiases", "saliases"))):
            return Table(stored["aliases"], {k: tuple(v) for k, v in stored["functions"].items()}, files,
                         options=(tuple(o) for o in stored["options"]), galiases=stored["galiases"],
                         saliases=stored["saliases"])
    built = build_table(files)
    if built.gap is None:
        write_cache(cache, entries, built)
    return built


def build_table(files):
    """The union of these snapshots, newest first: the first one that names a name decides what that name is, so a name a
    newer snapshot unaliased is not taken from an older one.  A suffix alias's suffix is a name of its own (("suffix",
    name) in read_snapshot's list), as zsh keeps those apart from every other alias and from functions."""
    aliases, functions, decided, options = {kind: {} for kind in ALIAS_KINDS}, {}, set(), {}
    for index, path in enumerate(files):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            return Table(gap="cannot read %s: %s" % (path, e))
        bash = is_bash(path)
        file_aliases, file_functions, named, file_options = read_snapshot(data, index, bash)
        for name in named:
            if name in decided:
                continue
            decided.add(name)
            if isinstance(name, tuple):
                if name[1] in file_aliases["suffix"]:
                    aliases["suffix"][name[1]] = file_aliases["suffix"][name[1]]
            elif name in file_aliases["plain"]:
                aliases["plain"][name] = file_aliases["plain"][name]
            elif name in file_aliases["global"]:
                aliases["global"][name] = file_aliases["global"][name]
            elif name in file_functions:
                functions[name] = file_functions[name]
        for option in file_options:  # every snapshot's, since any may be the one a session sourced (SPD-263)
            # a bash's line and a zsh's alike are two options, each read as its own shell reads it (SPD-329)
            options.setdefault((option[:3] if option[1] is not None else option[3], bash), option)
    return Table(aliases["plain"], functions, files, options=options.values(), galiases=aliases["global"],
                 saliases=aliases["suffix"])


def is_bash(path):
    """Whether the snapshot at `path` is a bash's, by its name (BASH_SNAPSHOT_PREFIX)."""
    return os.path.basename(path).startswith(BASH_SNAPSHOT_PREFIX)


def read_snapshot(data, index, bash=False):
    """(the aliases this snapshot leaves defined, a table per kind -- ALIAS_KINDS -- of each name's body, its functions as
    (index, the body's first byte, its length), every name any of its lines names -- a suffix alias's as ("suffix", its
    suffix) -- its option lines as Table.options holds them).  The lines are read in order, so a later `unalias` clears an
    alias and a later definition replaces an earlier one, as the shell reading the same file does: a plain and a global
    alias share zsh's one table and a function the name, a suffix alias has a table of its own (ALIAS_RE).  In a bash's
    snapshot (`bash`) an option's last `shopt` or `set` line decides it (last_option_lines); a zsh's keeps every option
    line, each read on its own."""
    aliases, functions, named, options = {kind: {} for kind in ALIAS_KINDS}, {}, [], []
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
                kind, name, body = found
                aliases[kind][name] = body
                if kind == "suffix":
                    named.append(("suffix", name))
                else:
                    aliases["global" if kind == "plain" else "plain"].pop(name, None)
                    functions.pop(name, None)
                    named.append(name)
            continue
        if raw.startswith(b"unalias"):
            suffix, names = unalias_names(line, aliases)
            for name in names:
                for kind in (("suffix",) if suffix else ("plain", "global")):
                    aliases[kind].pop(name, None)
                named.append(("suffix", name) if suffix else name)
            continue
        if raw.startswith((b"setopt", b"unsetopt", b"shopt", b"set")):
            found = option_line(line, index)
            if found is not None:
                options.extend(found)
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
        aliases["plain"].pop(name, None)
        aliases["global"].pop(name, None)
        named.append(name)
        i += 1
    return aliases, functions, named, last_option_lines(options) if bash else options


def last_option_lines(options):
    """A bash snapshot's option lines with each option's last line alone, in line order (SPD-329): Claude Code writes the
    `shopt -p` of the shell that made the snapshot and then its own `shopt -s expand_aliases`, and a shell that is not
    interactive prints `shopt -u expand_aliases` among the first, which the harness's line overrides as bash reading the
    file does.  An option is its builtin and its name as spelled -- bash takes a `set -o` name in no other case or
    spelling (probed, SPD-329's BashSnapshotOptionsTest) -- and a line the reader could not take apart (no name) is kept."""
    last = {}
    for at, option in enumerate(options):
        if option[1] is not None:
            last[option[0], option[1]] = at
    return [option for at, option in enumerate(options) if option[1] is None or last[option[0], option[1]] == at]


def option_line(line, index):
    """The options one of a snapshot's lines sets, as Table.options holds them, or None for a line that is no option
    builtin (a `set` alone, or a word that only starts like one: `settle`).  `setopt a b`, `shopt -s a b` and `set -o a -o b`
    set each name they list; `setopt` and `shopt -p` with no name set nothing, and a line of any other shape is one entry
    with no option, which the reader cannot model."""
    m = OPTION_RE.match(line.strip())
    if m is None or FUNCTION_RE.match(line):  # `set () {`: a function the profile names so, read as one
        return None
    builtin, words = m.group("builtin"), m.group("rest").split()
    kind = {"unsetopt": "setopt"}.get(builtin, builtin)
    on, names = builtin != "unsetopt", []
    if builtin == "set":
        if not words:
            return None
        while words and words[0] in ("-o", "+o") and len(words) > 1:
            on = words[0] == "-o"
            names.append((words[1], on))
            words = words[2:]
    elif builtin == "shopt":
        if words and words[0] in ("-s", "-u"):
            on = words[0] == "-s"
            names, words = [(w, on) for w in words[1:]], []
        elif words == ["-p"] or not words:
            return []
    else:
        names, words = [(w, on) for w in words], []
    if words or not all(OPTION_WORD_RE.match(name) for name, _ in names):
        return [(kind, None, on, line, index)]
    return [(kind, name, on, line, index) for name, on in names]


def alias_definition(line):
    """(its kind -- "plain", "global" for `-g` among the options, "suffix" for `-s` -- the name, the body as the shell
    stores it) of a snapshot's `alias` line, or None for a query, a line naming both kinds (which zsh refuses), or a line
    that is no definition.  The body is None where the hook cannot take the line's quoting off it."""
    m = ALIAS_RE.match(line)
    if m is None:
        return None
    flags = "".join(option[1:] for option in m.group("options").split())
    if "g" in flags and "s" in flags:
        return None
    spelled, sep, body = m.group("rest").partition("=")
    if not sep:
        return None  # `alias name` prints a definition and makes none
    name = unquote_word(spelled) or spelled
    kind = "global" if "g" in flags else "suffix" if "s" in flags else "plain"
    return (kind, name, unquote_word(body)) if name else None


def unalias_names(line, aliases):
    """(whether it clears suffix aliases, the names it clears) for an `unalias` line: every name it lists, or for `-a`
    every name defined so far -- the suffix aliases' alone with `-s`, which clears no other kind, and without it every plain
    and global one and no suffix alias (probed: `unalias -a` left `alias -s txt=...` running).  `aliases` is read_snapshot's
    tables.  The words after a redirection or a list operator belong to the rest of the line (`unalias find 2>/dev/null ||
    true`), and `-m` takes patterns, which the hook does not match -- it clears nothing rather than guess."""
    m = UNALIAS_RE.match(line)
    if m is None:
        return False, []
    flags = "".join(option[1:] for option in m.group("options").split())
    suffix = "s" in flags
    if "a" in flags:
        return suffix, list(aliases["suffix"]) if suffix else list(aliases["plain"]) + list(aliases["global"])
    if "m" in flags:
        return suffix, []
    names = []
    for word in (m.group("rest") or "").split():
        if word in ("||", "&&", ";", "|", "&") or any(c in word for c in "<>|&;()"):
            break
        names.append(unquote_word(word) or word)
    return suffix, names


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
    with contextlib.suppress(OSError):
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": CACHE_FORMAT, "fingerprint": [list(e) for e in entries], "aliases": built.aliases,
                       "galiases": built.galiases, "saliases": built.saliases,
                       "functions": {k: list(v) for k, v in built.functions.items()},
                       "options": [list(o) for o in built.options]}, f)
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
    kinds = (found.aliases, found.galiases, found.saliases)
    unreadable = sorted(n for table in kinds for n, body in table.items() if body is None)
    note = "shell snapshot: %d aliases and %d functions from %d file%s" % (
        sum(len(table) for table in kinds), len(found.functions), len(found.files), "" if len(found.files) == 1 else "s")
    if found.galiases or found.saliases:
        note += " (%d global and %d suffix alias%s among them)" % (len(found.galiases), len(found.saliases),
                                                                   "" if len(found.saliases) == 1 else "es")
    if unreadable:
        note += "; %d alias bod%s the hook cannot read (%s)" % (len(unreadable), "y" if len(unreadable) == 1 else "ies",
                                                                ", ".join(unreadable[:5]))
    return None, note
