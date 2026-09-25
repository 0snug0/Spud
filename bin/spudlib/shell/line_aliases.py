"""shell/line_aliases: the aliases a line defines, and the ones the shell already holds (SPD-284).

The seam SPD-284 took out of shell/expansions, past its 1000-line band once SPD-109 added zsh's global and suffix aliases:
`alias` and `unalias` lines and zsh's aliases, galiases and saliases parameters recorded into ShellAnalysis.aliases
(record_alias_line, clear_alias_line, record_alias_definition, read by analyse), the table as it stood where the shell
parsed the text being read (AliasView, SPD-286), the text a command word runs through it (alias_substitution,
parsed_alias, line_reading and suffix_substitution, read by analyse.dispatch_words and stdin_text; alias_requoted,
alias_rest, alias_texts for an alias body, its names in flight marked (SPD-317), and the words after it, chained after a
body ending in a blank (SPD-310) in the line's table and the snapshot's (SPD-313), word_aliases), how the text being
read writes a word, quoted or not (spellings, SPD-295, SPD-308), the global aliases expanded in the words of text the shell parses (global_aliased, read by analyse; global_names,
global_word and plain_words, read by shell/walk for a `<( )` body, SPD-293), the global and suffix aliases
the shell's snapshot holds, put in that table where a line starts (held_aliases, SPD-283) and read where the shell parsed
the text with them (held_names), and a command word read against the plain aliases and the functions the snapshot holds
(shell_aliased and shell_function, read by held_text), each kind only where the snapshot's aliases stand (held_standing,
SPD-290).  Past 250 lines as one reading: every function here reads or writes the one alias table, or a word against the
shell's own.

Where each alias stands, as the reading now has it (the comments below hold the probes):

- the line's own alias -- plain, global or suffix -- in text the shell parses as the line runs, a level into
  ShellAnalysis.alias_scope with the table as it stood where that text was parsed (AliasView, SPD-286): eval's words
  (analyse.dispatch_words); a `$( )` or backtick body, a trap's action and an (e) flag's value (analyse.analyse_isolated,
  SPD-283); a `<( )` or `>( )` body, its command words (ShellWalk.open_process_substitution, SPD-287) and its global
  aliases in every unquoted word (ShellWalk.expand_globals, SPD-293), which the line's own text leaves to it
  (alias_words); a glob qualifier's `e` or `+` code (ShellWalk.read_qualifier_code, SPD-292); and each further pass of a
  loop whose body changed the table (ShellWalk.read_loop_again, SPD-294).  Never in the line's own text, parsed before
  any of it runs, nor in a word of the same text that defined it, nor in a new shell's text (analyse.analyse_new_shell);
- the snapshot's global and suffix aliases in the line's own text as the snapshot holds them, and in text parsed as the
  line runs as the line left them (held_aliases, held_names, SPD-283); its plain aliases on a command word
  (shell_aliased); none of them in a function body the snapshot defines, parsed before its aliases, except in the text
  that body parses as it runs, nor anywhere in a new shell's text (held_standing, SPD-290, SPD-300);
- a quoted or escaped word: a global alias never expands it (alias_words; a `<( )` body's token, only where the text
  writes it unquoted: plain_words), nor a plain alias, the line's or the snapshot's, nor a suffix alias where the text
  after its last dot is quoted, each read by how the text being read writes the word (spellings, SPD-295), and one it
  writes both ways read both ways, the alias and on as a quoted word runs (SPD-308): more than runs, never less;
- `unalias` clears what the line and the snapshot -- its plain, global and suffix aliases -- hold for text parsed after
  it (clear_alias_line; shell_aliased for the plain ones, SPD-299), and doubts every name where the hook cannot read what
  it clears or it may not have run."""

import re

from . import expansions, prepare, syntax, unread
from ..hooks import snapshots


# `alias NAME=body` stores shell text the shell runs wherever it next reads NAME in command position (a global or a suffix
# alias elsewhere too, below record_alias).  A shell expands an alias when it parses the text, before the line runs, so an
# alias defined on the line reaches only code the shell parses as the line runs: `eval`'s words, and a substitution's body,
# inside eval or on the line itself, whose `$( )`, backtick and `<( )` bodies zsh parses when it runs them (probed in zsh
# 5.9 -f and -f -o nobareglobqual, SPD-283: `alias gp="echo GP-RAN"; echo $(gp)` printed GP-RAN, and so did `echo
# "$(gs)"`, `` echo `gq` `` and `cat <(gt)`, where `(gr)` ran nothing).  analyse.analyse_isolated reads a `$( )` or
# backtick body as eval's words are (ShellAnalysis.alias_scope); the walk reads a `<( )` or `>( )` body a level into that
# scope too, where it opens (ShellWalk.open_process_substitution, SPD-287), and expands the global aliases standing there
# in the body's own words (ShellWalk.expand_globals, SPD-293: `alias -g X='; git push'; cat <(echo X)` pushes, as the
# same line with `$( )` does).  Probed in bash 3.2, zsh 5.9 -f,
# zsh -f -o nobareglobqual and sh with a fake git first on a scratch PATH: `alias gp='git push'; eval gp` pushed in zsh,
# zsh-nbgq and sh (bash expands no alias non-interactively without `shopt -s expand_aliases`, so it pushed nothing -- noted,
# never relied on), and so did `alias -g GP=...; eval GP`, two definitions on one `alias` line, `eval 'gp; gp'`, `eval 'X=1
# gp'`, `eval '{ gp; }'`, `eval 'if true; then gp; fi'`, `eval 'coproc gp'`, `eval 'time gp'`, `eval '! gp'`, `eval 'eval
# gp'`, `eval 'echo $(gp)'`, an alias reached from another alias, and one that shadows a function of the same name.  `alias
# g=git; g push` ran nothing anywhere (the alias does not exist when the line is parsed), nor did `eval 'command gp'`, `eval
# 'env gp'` or `eval 'sh -c gp'`, and `unalias` cleared.
#
# SPD-286: that holds inside such text too.  The shell parses a text it reads as the line runs -- eval's words, a `$( )` or
# backtick body, a trap's action, an (e) flag's value, a `-c` string -- whole before any of it runs, so an alias the text
# itself defines, changes or clears stands only in text parsed after it (a later eval or substitution), never in a command
# of the same text.  Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py: `eval 'alias
# ls="echo ALIASED"; ls -d /'` printed `/`, the real ls, and so did the same text on two lines, `echo $(alias ls=...; ls -d
# /)`, its backtick form, `trap 'alias ls=...; ls -d /' EXIT`, `x='$(alias ls=...; ls -d /)'; echo ${(e)x}`, `zsh -f -c`
# either way, an alias whose body defines ls and runs it, and a function eval defines that does, called twice; `eval 'alias
# -g QQ=GLOBAL; echo hi QQ'` printed `hi QQ` and `eval 'alias -s txt="echo SUFFIX"; a.txt'` found no command; after `alias
# ls=...`, `eval 'unalias ls; ls -d /'` ran the alias, and a global alias the text cleared still expanded in an alias body
# read in it, a suffix alias `unalias -s` cleared still ran.  Text parsed after it does see it: `eval 'alias ls=...'; eval
# 'ls -d /'`, `eval 'alias ls=...; eval "ls -d /"'` and `eval 'alias ls=...; echo $(ls -d /)'` each ran the alias.  The Bash
# tool's own line is such a text, which is why the line's own alias never reaches its own commands: Claude Code runs it as
# `/bin/zsh -c 'source <snapshot> ... && eval <line> < /dev/null && pwd -P >| ...'` (a member's own call's `ps -o args= -p
# $$`, 2026-09-24), where `alias zzq='echo ALIASED-TOP'` and `zzq hi` on the next line printed "(eval):2: command not
# found: zzq".  bash 3.2 (with `shopt -s expand_aliases`) and sh read such text a line at a time instead: `eval $'alias
# ls=...\nls -d /'`, `sh -c` on two lines, a `$( )` body and a trap's action on two lines each ran the alias there, and on
# one line none did -- which AliasView.lines keeps for line_reading.
def record_alias(a, name, body, doubtful=False):
    """Record `alias NAME=body`, or an `unalias` (whose body is None), where the shell reads it.  The name goes into
    `assigned` under a key no variable can have, so every rule that doubts a variable the line assigned -- a branch that may
    not run, a subshell, a pipeline element, a background list, a loop or function body, a reading only one shell makes --
    doubts the alias too, and a certain definition settles an earlier doubt as an assignment does.  The body itself stays out
    of `vars`, which holds the shell's variables alone (vouched_spud_call reads every name there).  It is recorded in the
    table as it stands, which text parsed after this reads; the text this runs in reads its own AliasView (SPD-286)."""
    key = syntax.ALIAS_KEY + name
    a.aliases[name] = body
    a.assigned.append(key)
    if doubtful or a.unsure or a.loop_depth:
        a.doubt.add(key)
        if a.loop_depth:
            a.sticky.add(key)
    elif key not in a.sticky:
        a.doubt.discard(key)


class AliasView:
    """The alias table a text the shell parses as the line runs was parsed with (SPD-286, above): ShellAnalysis.aliases,
    the doubt of each name in it and alias_unknown, as they stood where the reading of that text began -- eval's
    (analyse.dispatch_words) and a body read in its own process (analyse.analyse_isolated), held in
    ShellAnalysis.alias_view while it is read.  Every word of that text reads it (parsed_alias, suffix_substitution,
    global_aliased, alias_requoted), an alias body read in it too, being part of the same parse; what the text itself
    defines or clears goes into ShellAnalysis.aliases alone, for text parsed after it.  `lines`: the text holds a newline,
    where bash and sh, reading it a line at a time, may expand what a line before defined (line_reading).  Equal by
    content, as ShellAnalysis.reading_state compares it.

    SPD-290: two more marks say whether the aliases the shell's snapshot defines stand in the text (held_standing).
    `held`: the shell that parses it sourced the snapshot -- False in a new shell's text (analyse.analyse_new_shell) and
    in every text parsed inside it, which inherits it (`outer`); `early`: the text is a function body the snapshot defines,
    parsed before the snapshot's aliases (held_text.read_body), which no text the body parses as it runs inherits."""

    __slots__ = ("table", "doubted", "unknown", "lines", "held", "early", "key")

    def __init__(self, a, lines, held=None, early=False):
        self.table = dict(a.aliases)
        self.doubted = frozenset(k for k in self.table if alias_doubted(a, k))
        self.unknown, self.lines = a.alias_unknown, lines
        self.held = (a.alias_view is None or a.alias_view.held) if held is None else held
        self.early = early
        self.key = (frozenset(self.table.items()), self.doubted, self.unknown, lines, self.held, early)

    def parsed_late(self):
        """This view, but for text parsed with the snapshot's aliases in force: a function body the line defines, read
        where a call inside a snapshot function's body runs it (held_text.read_body), was parsed with the line."""
        if not self.early:
            return self
        view = object.__new__(AliasView)
        for field in ("table", "doubted", "unknown", "lines", "held"):
            setattr(view, field, getattr(self, field))
        view.early, view.key = False, self.key[:-1] + (False,)
        return view

    def __eq__(self, other):
        return isinstance(other, AliasView) and self.key == other.key

    def __hash__(self):
        return hash(self.key)


def held_standing(a):
    """Whether the aliases the shell's snapshot defines stand in the text being read (SPD-290; SPD-283 for the global and
    suffix ones, held_names): in the line's own text, an alias body read in it, and text the Bash tool's shell parses as
    the line runs -- eval's, a substitution's, on the line or inside a snapshot function's body, which run with the
    aliases defined -- and a function body the line defines, parsed with the line; never in a function body the snapshot
    defines, which it writes before its aliases (so zsh parsed it with none), nor anywhere in a new shell's text, which
    never sources the snapshot (AliasView's `held` and `early`).  The harness's shadows, which the snapshot defines after
    its aliases, are read as the line's own text (held_text.read_body)."""
    view = a.alias_view
    return view is None or view.held and not view.early


def parsed_view(a):
    """The AliasView the words being read expand with: the innermost text parsed as the line runs, where alias_scope says
    one is being read; None elsewhere -- the line's own text, and a new shell's, which read the table as it stands, and
    only their eval and substitutions read a line's alias (analyse.analyse_command, held_names)."""
    return a.alias_view if a.alias_scope else None


def parsed_table(a):
    """The alias table the text being read was parsed with: its AliasView's, else the table as it stands."""
    view = parsed_view(a)
    return a.aliases if view is None else view.table


def parsed_doubted(a, key):
    """alias_doubted, as the text being read was parsed: its AliasView's doubt, else the doubt as it stands."""
    view = parsed_view(a)
    return alias_doubted(a, key) if view is None else key in view.doubted


def alias_arguments(words):
    """An `alias` or `unalias` line's words past its options (`alias -g X=y`, `alias +g X=y`, `alias -- a=b c=d`,
    `unalias -a`): zsh reads a word opening with `-` or `+` as options, and `+g` defines a global alias as `-g` does
    (probed, SPD-109)."""
    i = 1
    while i < len(words) and words[i].startswith(("-", "+")) and len(words[i]) > 1:
        i += 1
        if words[i - 1] == "--":
            break
    return words[i:]


# SPD-109: zsh's two other kinds of alias, which a line defines with `alias -g` or `+g` (or the `galiases` parameter) and
# `alias -s` (`saliases`).  A global alias is expanded in every word zsh reads unquoted where it parses text, not only in
# command position, and a suffix alias runs its body before a command word whose text after its last dot is the suffix:
# `alias -g gp='; git push'; eval 'echo hi gp'` and `alias -s txt='git push'; eval a.txt` both pushed (probed in zsh 5.9
# -f and -f -o nobareglobqual through tests/probes/shell_probe.py; bash 3.2 has neither, `alias -g` being an invalid
# option there).  They live in the analysis's one alias table (ShellAnalysis.aliases) under these prefixes, a name no word
# can spell, so every rule that reads, doubts, copies or joins that table -- a reading's branch, both readings of a line,
# a new shell's empty table -- reads them too; a global alias keeps its plain entry as well, which eval's command word
# reaches through a variable (`X=gp; eval $X`).  The prefix alone stands for an alias of that kind whose name the hook
# cannot read, recorded doubtful.
GLOBAL_ALIAS, SUFFIX_ALIAS = "\x00global\x00", "\x00suffix\x00"
ALIAS_TABLE_KINDS = {"aliases": ("plain",), "galiases": ("global",), "saliases": ("suffix",)}  # zsh's parameters (SPD-105)
_ALL_KINDS = ("plain", "global", "suffix")
_KIND_PREFIX = {"global": GLOBAL_ALIAS, "suffix": SUFFIX_ALIAS}
# The characters that end a word where zsh reads text unquoted, and those that keep a word from being a global alias's
# name: a quote, an escape, an expansion or a substitution anywhere in it (`'gp'`, `g\\p`, `g'p'` and `${gp}` were each
# left as written, probed).
_WORD_ENDS = frozenset(" \t\n;&|<>()")
GLOBAL_EXPANSIONS = 256  # the most global aliases one reading expands, past which the text is refused a member unread


def alias_kinds(words):
    """The kinds of alias an `alias` line's definitions are: global for a `g` among its options, suffix for an `s`, plain
    otherwise; every kind where an option word holds an expansion the hook cannot read (`alias $o gp=...`), since zsh reads
    options from what it expands to.  A line zsh refuses (`-gs`, `-gr`: "illegal combination of options", nothing
    defined) is read as the kind its first letter names, which reads more than the shell runs."""
    rest = alias_arguments(words)
    options = words[1 : len(words) - len(rest)]
    if any(expansions.expansion_word(w) for w in options):
        return _ALL_KINDS
    flags = "".join(prepare.deglob(w)[1:] for w in options)
    return ("global",) if "g" in flags else ("suffix",) if "s" in flags else ("plain",)


def record_alias_line(words, a):
    """Read an `alias` line's definitions into the analysis's table.  A word with no `=` is a query and defines
    nothing.  A body the hook cannot read (it holds an expansion or a substitution, whose value is not on the line) is
    recorded with no body and doubted, so the name refuses a member where `eval` dispatches it; a name it cannot read leaves
    every name of the line's in doubt, since the hook cannot tell which one this defines -- and so does a word that holds an
    expansion with no `=` (`alias $X`), which may be a definition of any kind, or options that make the ones after it any
    kind."""
    kinds = alias_kinds(words)
    for w in alias_arguments(words):
        m = syntax.ALIAS_WORD_RE.match(w)
        if m is not None:
            record_alias_definition(a, m.group(1), m.group(2), kinds)
        elif expansions.expansion_word(w):
            record_alias_unknown(a, _ALL_KINDS)
            kinds = _ALL_KINDS


def record_alias_unknown(a, kinds):
    """An alias of each of these kinds whose name the hook cannot read: every command word eval reads may be a plain one,
    every word a global one and every command word with a dot a suffix one (alias_words, suffix_substitution)."""
    for kind in kinds:
        if kind == "plain":
            a.alias_unknown = True
        else:
            record_alias(a, _KIND_PREFIX[kind], None, doubtful=True)


def record_alias_definition(a, name, value, kinds=("plain",)):
    """One definition as the line spells it, `name` and `value` masked words: an `alias` line's `name=body`, or an element
    of zsh's `aliases`, `galiases` or `saliases` parameter, whose value is None where the hook cannot know it
    (`aliases[gp]+=...`); `kinds`, what it defines (alias_kinds, ALIAS_TABLE_KINDS).  A plain definition makes a global
    alias of the same name plain again (probed: `alias -g q=G; alias q=P; eval 'echo q'` printed q), since the two share
    zsh's table; a suffix alias has a table of its own."""
    spelled = prepare.deglob(name)
    if expansions.expansion_word(name):
        record_alias_unknown(a, kinds)
        return
    identifier = syntax.IDENTIFIER_RE.match(spelled) is not None
    if not identifier and "plain" in kinds:  # the table reads a plain alias's name only as an identifier
        a.alias_unknown = True
    if not alias_word_name(spelled):
        record_alias_unknown(a, [k for k in kinds if k != "plain"])
        return
    readable = value is not None and not expansions.expansion_word(value)
    body = prepare.deglob(value) if readable else None
    if identifier and ("plain" in kinds or "global" in kinds):
        record_alias(a, spelled, body, doubtful=not readable)
    if "global" in kinds:
        record_alias(a, GLOBAL_ALIAS + spelled, body, doubtful=not readable)
    elif "plain" in kinds and GLOBAL_ALIAS + spelled in a.aliases:
        record_alias(a, GLOBAL_ALIAS + spelled, None)
    if "suffix" in kinds:
        record_alias(a, SUFFIX_ALIAS + spelled, body, doubtful=not readable)


def alias_word_name(name):
    """Whether a global alias's name is one a word can spell unquoted (`...`, `G`), which zsh then expands; one holding a
    blank, an operator or a quote never matches a word (probed: `alias -g 'a b'=x; eval 'echo a b'` printed `a b`)."""
    return bool(name) and not any(c in _WORD_ENDS or c in "'\"\\$`=" for c in name)


def clear_alias_line(words, a):
    """`unalias NAME ...` and `unalias -a` clear what the line aliased; an argument the hook cannot read (an expansion, or a
    pattern for zsh's `-m`) clears nothing and doubts every name instead.  `-s` clears suffix aliases alone, and without
    it no suffix alias is cleared (probed: `unalias -a` left `alias -s cfg=...` running).

    SPD-299: the pool holds the snapshot's plain aliases too, where the shell sourced the snapshot (AliasView's `held`,
    held_text.snapshot_sourced: never in a new shell's text, and in a snapshot body, which runs once they are defined), so
    `unalias -a`, `-m` and a name the hook cannot read clear or doubt them as they do the line's own; a name spelled out
    is recorded whatever held it.  Each is recorded as cleared in the line's table, which text the shell parses after this
    reads (AliasView) and shell_aliased then leaves unexpanded there (probed: under a sourced `alias gp='echo SNAP-GP'`,
    `unalias gp`, `unalias -a`, `unalias -m "g*"` and `X=gp; unalias $X` each left `eval "gp x"` and `echo $(gp y)`
    finding no command gp, while the line's own `gp top` still printed SNAP-GP).

    SPD-306: zsh's `unhash` and `disable` clear aliases too, and `enable` brings back what `disable` cleared, each with
    flags of its own (_clear_hash_table_line, below)."""
    cmd = prepare.deglob(words[0])
    if cmd != "unalias":
        _clear_hash_table_line(cmd, words, a)
        return
    rest = alias_arguments(words)
    options = words[1 : len(words) - len(rest)]
    flags = "".join(prepare.deglob(w)[1:] for w in options)
    suffix = "s" in flags
    pool = _clear_pool(a, suffix)
    if "a" in flags:
        names, doubtful = pool, False
    elif "m" in flags or any(expansions.active_read_word(w) for w in rest):
        names, doubtful = pool, True
    else:
        names, doubtful = _named_keys(a, rest, suffix), False
    for name in names:
        record_alias(a, name, None, doubtful=doubtful)


def _clear_pool(a, suffix):
    """Every alias of one table (the suffix aliases, or the plain and global ones) a clear of it may reach: the line's
    table's, and the snapshot's plain ones where the text being read sourced it (SPD-299, clear_alias_line)."""
    pool = [k for k in a.aliases if k.startswith(SUFFIX_ALIAS) == suffix]
    if not suffix and (a.alias_view is None or a.alias_view.held):
        pool += [k for k in snapshots.shell_table(a.home).aliases if k not in a.aliases]
    return pool


def _named_keys(a, rest, suffix):
    """The table keys the names `rest` spell: a suffix alias's under its prefix; a plain name's, and its global alias's
    where the line's table holds one, the two sharing zsh's table."""
    if suffix:
        return [SUFFIX_ALIAS + prepare.deglob(w) for w in rest]
    names = [prepare.deglob(w) for w in rest]
    return names + [GLOBAL_ALIAS + n for n in names if GLOBAL_ALIAS + n in a.aliases]


# SPD-306: zsh's `unhash` and `disable` take an alias out of the table too, as `unalias` does, and `enable` puts back
# one `disable` took out.  Each builtin picks its table by its flags, the first of these that it holds winning -- one
# of its other tables, else `s` the suffix aliases, else `a` the plain and global ones (`a` is never "all" here), else its
# own default table (the command hash table, the builtins) -- and `m` makes its words patterns.  Probed in zsh 5.9 -f and
# -f -o nobareglobqual through tests/probes/shell_probe.py, a `zsh -f -c` sourcing `alias gp='echo SNAP-GP'`, `alias
# go=...` and `alias -s txt='echo SUFFIX'`, then evaluating the line (2026-09-24): `unhash -a gp`, `unhash -a -- gp`,
# `unhash -am "g*"`, `unhash -m -a "g*"`, `disable -a gp`, `disable -am "g*"` each left `eval "gp x"` finding no command
# gp; `unhash -s txt`, `-as`, `-sa`, `-a -s`, `disable -s txt` and `-as` left `eval "a.txt z"` finding none, where `-a
# txt` did not; `unhash -af`, `-ad`, `-ds`, `-fs`, `disable -af`, `-ar`, `-rs`, `-fs` touched no alias, `disable -ap`
# and `-sp` read a pattern table ("invalid pattern"); `+a gp` is a name ("no such hash table element: +a"); bare `unhash
# -a` fails "not enough arguments" and bare `disable -a` and `enable -a` list, clearing nothing.  `disable -a gp; enable
# -a gp` (and `-a -- gp`, and one in an eval) ran SNAP-GP again in a later eval, where after `unhash -a gp` or `unalias
# gp` it found "no such hash table element", and `disable -a gp; alias gp=...` defined gp anew, enabled.
_OTHER_TABLES = {"unhash": "df", "disable": "frp", "enable": "frp"}


def hash_table_kind(cmd, flags):
    """The alias table `unhash`, `disable` or `enable` with these option letters works on: "suffix", "plain" (the plain
    and global aliases), or None for another of its tables (above)."""
    if any(c in flags for c in _OTHER_TABLES[cmd]):
        return None
    return "suffix" if "s" in flags else "plain" if "a" in flags else None


def _clear_hash_table_line(cmd, words, a):
    """SPD-306: `unhash` and `disable` with a flag naming an alias table clear its names as `unalias` does, for text the
    shell parses after it (the line's aliases and the snapshot's, SPD-299); `enable` doubts a name the line cleared,
    which it may bring back with the body it had -- a body the hook does not keep, so the name reads as one whose
    definition may or may not stand: refused a member where eval or a substitution runs it, never read as other text.
    Only `-` opens options (`+a` is a name), `--` ends them.  An option or the first word past them the hook cannot
    read may name either table and more options, so it doubts both; `m`, or a name the hook cannot read, doubts the
    table's every name; no names at all clears nothing."""
    i = 1
    while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
        i += 1
        if words[i - 1] == "--":
            break
    options, rest = words[1:i], words[i:]
    if any(expansions.expansion_word(w) for w in options) or rest and expansions.active_read_word(rest[0]):
        tables, flags = (False, True), "m"
    else:
        kind = hash_table_kind(cmd, "".join(prepare.deglob(w)[1:] for w in options))
        if kind is None or not rest:
            return
        tables, flags = (kind == "suffix",), "".join(prepare.deglob(w)[1:] for w in options)
    for suffix in tables:
        if "m" in flags or any(expansions.active_read_word(w) for w in rest):
            keys, doubtful = _clear_pool(a, suffix), True
        else:
            keys, doubtful = _named_keys(a, rest, suffix), False
        if cmd == "enable":
            keys, doubtful = [k for k in keys if k in a.aliases and a.aliases[k] is None], True
        for key in keys:
            record_alias(a, key, None, doubtful=doubtful)


# SPD-283: the global and suffix aliases the shell already holds, from its snapshot (hooks/snapshots.Table.galiases and
# .saliases, which no snapshot Claude Code writes today holds: see hooks/snapshots.ALIAS_RE).  The shell the Bash tool
# starts sources the snapshot and then parses the member's line whole, before any of it runs, so where the line's own
# text holds one it is expanded as the snapshot left it, whatever the line does to it; text the shell parses as the line
# runs -- eval's words, a substitution's body -- expands it as it stands then, after what the line did.  Probed in zsh 5.9
# -f and -f -o nobareglobqual, a fresh `zsh -f -c` sourcing a snapshot (`unalias -a`, functions, then `alias -g
# X=snapshot`, `alias -s txt='echo SUFFIX'`) and evaluating the line, as the Bash tool's shell does:
#
# - the line's own words: `echo hi X` printed `hi snapshot`, `a.txt top` ran `echo SUFFIX a.txt top`; after `alias -g
#   X=line`, `alias -s txt=...` or `unalias -s txt` on the same line, `echo top X` and `a.txt top` still ran the
#   snapshot's, and `unalias X` had its own X expanded ("no such hash table element: snapshot", then `eval "echo hi X"`
#   printed `hi snapshot`), where `unalias 'X'` cleared it (`hi X`);
# - an alias body read in the line: `unalias 'X'; pa` and `alias -g X=changed; pa` (pa='echo pa X') printed `pa snapshot`;
# - a substitution or eval's text after the line's own change: `alias -g X=line; echo sub $(echo X)` printed `sub line`,
#   `alias -s txt="echo LINE"; echo $(a.txt s)` ran LINE, `unalias -s txt; echo $(a.txt s2)` found no command, and
#   `alias -g X=line; eval "echo hi X"` printed `hi line`, a plain `alias X=plain` `hi X`, and `unalias -a` cleared X and
#   left txt;
# - a function the snapshot defines, which it defines before its aliases, expanded none of them: `f() { echo in-f X; }`
#   printed `in-f X` and `g() { a.txt; }` found no command a.txt, where a function defined after them expanded X;
# - a new shell: `zsh -f -c "echo new X; a.txt new"` expanded neither.
def held_aliases(a):
    """Start a line's reading from the global and suffix aliases the shell already holds, as held_options.line_options
    starts it from the options: each goes into the line's own alias table under its kind's prefix, a global alias with its
    plain entry too, as the line's own `alias -g` and `alias -s` put them there (record_alias_definition), so what the
    line does to one where it runs -- `alias -g`, `alias -s`, a plain `alias` of the name, `unalias` -- changes it for the
    text the shell parses after that, and every rule that reads, doubts, copies or joins the table reads them.  A body the
    hook cannot read is recorded doubtful, never as cleared.  A global alias's name a word cannot spell unquoted
    (alias_word_name) is never expanded, and is left out.  Called once, where analyse.analyse_command reads the line."""
    held = snapshots.shell_table(a.home)
    entries = [(GLOBAL_ALIAS + name, body) for name, body in held.galiases.items() if alias_word_name(name)]
    entries += [(name, body) for name, body in held.galiases.items() if syntax.IDENTIFIER_RE.match(name)]
    entries += [(SUFFIX_ALIAS + suffix, body) for suffix, body in held.saliases.items()]
    for key, body in entries:
        a.aliases[key] = body
        if body is None:
            a.doubt.add(syntax.ALIAS_KEY + key)


def held_names(a, prefix):
    """The shell's own global aliases (prefix GLOBAL_ALIAS) or suffix aliases (SUFFIX_ALIAS) that stand in the text being
    read at the line's own parse, each name -> its body as the snapshot holds it, None for one the hook cannot read: in
    the line's own text, which the shell parses before any of it runs, and an alias body read in it, whatever the line did
    to them since (above).  None stand where held_standing says the snapshot's aliases do not (SPD-290): a function body
    the snapshot defines before its aliases, and a new shell's text, whose table analyse.analyse_new_shell empties besides,
    so held_aliases never put the name there.  Text the shell parses as the line runs reads the line's table instead
    (ShellAnalysis.alias_scope), as it stood where that text was parsed (AliasView, SPD-286) -- inside a snapshot body too,
    where a substitution read at scope 0, since the line's table holds none, now reads them as its shell parses it then."""
    if not held_standing(a):
        return {}
    held = snapshots.shell_table(a.home)
    table = held.galiases if prefix == GLOBAL_ALIAS else held.saliases
    return {name: body for name, body in table.items() if prefix + name in a.aliases}


def alias_doubt(a, prefix, name, shown):
    """The finding a word earns where a global (prefix GLOBAL_ALIAS) or suffix alias (SUFFIX_ALIAS) called `name` may run
    in its place but the hook cannot read what it runs: "shell-alias", naming `shown`, for one the shell's snapshot holds
    with a body the hook cannot read -- its quoting, not the line, is what the member can do nothing about -- and else an
    "unread" alias-word finding, for one the line defined that the hook cannot resolve (SPD-109)."""
    held = snapshots.shell_table(a.home)
    if (held.galiases if prefix == GLOBAL_ALIAS else held.saliases).get(name, "") is None:
        return ("shell-alias", shown)
    return ("unread", ("alias-word", shown))


def note_alias_doubt(a, finding):
    """Record alias_doubt's finding once, as unread.record_unread records one: the first of a kind the line earns stands."""
    if finding not in a.findings:
        a.findings.append(finding)


def held_shown(kind, body):
    """What ShellAnalysis.shell_expanded says an alias of the shell's runs, for a reason's note: `kind` and its body, cut
    as shell_aliased cuts a plain alias's."""
    spelled = body.strip()
    return "%s for `%s`" % (kind, spelled if len(spelled) <= 120 else spelled[:117] + "...")


def alias_substitution(name, a):
    """(the text an alias of this line's runs where `eval` dispatches its name, whether the hook cannot be sure of it), for a
    command word inside an `eval`, read as parsed_alias reads it.  (None, False) when the name is no alias of the line's and
    the line defined none the hook could not read; (None, True) when it may be one, or may have been cleared, and the hook
    cannot say what it runs -- and where bash and sh, reading the text a line at a time, may hold another (line_reading)."""
    body, doubtful = parsed_alias(name, a)
    return body, doubtful or line_reading(name, a) is not None


def parsed_alias(name, a):
    """(the body, whether it is doubtful) of the alias the table the text being read was parsed with (parsed_table, SPD-286)
    holds for a command word: an alias the same text defines or clears changes neither.  None stands for a name the text
    spells only quoted, which zsh expands no alias of, whatever its name (quoted_name, SPD-295: `alias ls=...; eval
    "'ls' -d /"` ran the real ls); for one it spells both ways the alias stands, and the caller reads the word on as a
    quoted one runs too (quoted_too, SPD-308)."""
    table = parsed_table(a)
    if name not in table:
        view = parsed_view(a)
        unknown = a.alias_unknown if view is None else view.unknown
        return None, unknown and not quoted_name(a, name)
    if quoted_name(a, name):
        return None, False
    return table[name], parsed_doubted(a, name)


def line_reading(name, a):
    """(the body, whether it is doubtful) of the alias bash and sh may expand for a command word of a text of several lines
    they read a line at a time, where a line before this one defined, changed or cleared it (AliasView.lines, SPD-286):
    the table as it stands, where it differs from the one zsh parsed the text with; None where it does not, and in a text
    of one line, which every shell parses whole.  The table as it stands holds what this command's own line did too, which
    neither shell expands there: a reading of more than runs, never less."""
    view = parsed_view(a)
    if view is None or not view.lines or quoted_name(a, name):  # bash and sh expand no alias of a quoted word either
        return None
    now =(a.aliases[name], alias_doubted(a, name)) if name in a.aliases else (None, a.alias_unknown)
    return None if now == parsed_alias(name, a) else now


def alias_doubted(a, key):
    """Whether the shell may not hold what the line's alias table says for this key, as the table stands; parsed_doubted
    reads it as the text being read was parsed."""
    key = syntax.ALIAS_KEY + key
    return key in a.doubt or key in a.sticky or a.all_doubt


def suffix_substitution(name, a, fresh=0, plain=False):
    """(the text a suffix alias runs where the shell dispatches the command word `name` -- the body, to which the caller
    appends the word itself -- or None; the finding the word earns where the hook cannot be sure of it, or None; whether
    the word may run something else instead, which the caller reads on for): in text
    parsed as the line runs (ShellAnalysis.alias_scope), one the line's table holds, the line's own or the shell's as the
    line left it; in the line's own text, one the shell's snapshot holds as it holds it (held_names, SPD-283), never for a
    word an expansion gave (`fresh`, analyse.dispatch_words: probed, `F=a.txt; $F` found no command a.txt).  zsh looks the
    suffix up after the text past the word's last dot, a dot that does not open the word (probed: `b.a.txt` and
    `./d/a.txt` ran `alias -s txt=...`, `.txt` and `a.TXT` did not), and only once no plain alias of the whole word stands
    (probed: `alias a.txt=...` ran in place of `alias -s txt=...`), the shell's too, which shell_aliased then expands --
    where it stands (held_standing, SPD-290: `zsh -c 'alias -s txt=...; eval a.txt'` ran the suffix alias, the new shell
    holding no plain a.txt).
    A glob is read as one, whose text zsh looks the suffix up in before it globs (probed: `a?.txt` and `*.txt` ran the
    suffix alias with the file they matched).  The line's table is read as the text was parsed (parsed_table, SPD-286):
    `eval 'unalias -s txt; a.txt'` ran the suffix alias (probed).

    SPD-295: zsh looks up only the text after the last dot as the word is written, so a quote or an escape there keeps
    the suffix alias from running, and one before the dot does not (probed in zsh 5.9 -f and -f -o nobareglobqual:
    `'a.cfg'`, `a.'cfg'`, `a.cf\\g` and `a.cfg''` ran nothing, `a\\.cfg`, `"a".cfg` and `'a'.cfg` ran `alias -s cfg`).
    The word reaches this with its quotes taken, so it is read by how the text being read writes it (spellings): with
    its suffix quoted it runs no suffix alias, and a plain alias of the whole word, which zsh expands in the suffix
    alias's place, does not stand where it is quoted at all, so `a\\.cfg` runs the suffix alias whatever plain `a.cfg`
    there is.

    SPD-308: by the word's own spellings, not every word's with that suffix (`'a.cfg'; b.cfg` runs one suffix alias, for
    b.cfg), and where the text writes the word more than one way, the ways it may run beside this are read on for: an
    unquoted one's plain alias of the whole word -- the shell's, which the caller then reads (held_text.read_shell_name);
    the line's, which it read before this (`plain`) -- and one quoted after its last dot, the command it names."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return None, None, True
    suffix, held, table = name[dot + 1 :], snapshots.shell_table(a.home), parsed_table(a)
    key = SUFFIX_ALIAS + suffix
    if not a.alias_scope:
        names = {} if fresh else held_names(a, SUFFIX_ALIAS)
        if suffix not in names:
            return None, None, True
        body, finding = names[suffix], (alias_doubt(a, SUFFIX_ALIAS, suffix, name) if names[suffix] is None else None)
    elif key in table:
        body, finding = table[key], (alias_doubt(a, SUFFIX_ALIAS, suffix, name) if parsed_doubted(a, key) else None)
    elif SUFFIX_ALIAS in table and parsed_doubted(a, SUFFIX_ALIAS):
        body, finding = None, ("unread", ("alias-word", name))
    else:
        return None, None, True
    # ... one may run: by how the text writes the word, read once one stands, so a text no suffix alias reaches is not scanned
    written = spellings(a, name)
    shell_plain = name in held.aliases and held_standing(a) and (not a.alias_scope or name not in table)
    if not (written & QUOTED_STEM or written & UNQUOTED and not (plain or shell_plain)):
        return None, None, True  # no way the word is written runs its suffix alias
    return body, finding, bool(written & QUOTED_SUFFIX or written & UNQUOTED and shell_plain)


# SPD-295: zsh expands no plain alias of a command word quoted in any way -- a quote or a backslash anywhere in it -- and
# no suffix alias of one quoted or escaped anywhere after its last dot, the line's own aliases as the snapshot's (probed
# in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py, the snapshot sourced and the line eval'd
# as the Bash tool's shell runs it: `\ls`, `l''s`, `"ls"`, `'ls'`, `ls''` and `$'ls'` ran the real ls, in eval and a
# `$( )` too, and `eval "'ls'"` did under the line's own `alias ls=...`; `'a.cfg'`, `a.'cfg'`, `a.cf\g` and `a.cfg''` ran
# no `alias -s cfg`, `a\.cfg`, `"a".cfg`, `\a.cfg` and `'a'.cfg` ran it).  The tokens the walk reads have their quotes
# taken (`'ls'`, `\ls` and `ls` are one token), so the mark is kept on the text instead: how the text being read spells
# each word, found by a scan of it (spellings).  The text is the one analyse_command tokenizes
# (ShellAnalysis.quoted_text), its comments' quote characters blanked, its newlines LINE_BREAK and its `$( )` and
# backtick bodies lifted out, each read as a text of its own; a `<( )` body's words are the line's own tokens, and read
# here with the rest.  A brace ends a word here, where zsh may split one off (`{ls}`), so a word glued to one counts
# unquoted too.
#
# SPD-308: zsh reads each word's own quotes where it parses it, so a text that spells a name both ways runs the alias
# where it is unquoted and the command the name spells where it is quoted (probed as above: under `alias ls='echo
# ALIASED'`, `'ls' -d /; ls -d /` printed `/`, then `ALIASED -d /`, and the other order the other way; with `function
# a.txt` and `alias -s txt=...`, `'a.txt' q1; a.txt q2` ran the function, then the suffix alias).  SPD-295 read every
# token of such a name as the alias, which let `'git' push; git status` through under a profile's `alias git=hub`
# while zsh pushed.  A token does not say which spelling it came from, so where the text has more than one for its word
# the word is read as each of them runs: the alias an unquoted one runs, then on as a quoted one runs, its suffix alias
# where one written with that suffix unquoted may run it, and the command it names (analyse.dispatch_words,
# held_text.read_shell_name).  That reads more than runs, never less.  A token carrying its own mark instead -- a
# sentinel written into each quoted word before shlex splits the text, or a str subclass -- was weighed and left: a
# sentinel inside the word reaches every reading by name that does not pass through prepare.deglob (the wrapper and
# reserved-word tables, an assignment word, a redirection operator), and a subclass's mark is gone from every token the
# walk rebuilds (a brace split off, `{'git' push}`; a closer glued on), where the reading of both ways is needed still.
UNQUOTED, QUOTED_STEM, QUOTED_SUFFIX = 1, 2, 4  # a word as written: unquoted; quoted, but not after its last dot; after it
_ANY_SPELLING = UNQUOTED | QUOTED_STEM | QUOTED_SUFFIX
_SCAN_ENDS = _WORD_ENDS | {"{", "}", syntax.LINE_BREAK}


def spellings(a, name):
    """How the text being read writes the word `name` wherever it stands, the ways zsh then reads it (SPD-295, SPD-308):
    UNQUOTED, QUOTED_STEM (quoted, but not after its last dot, or it has none) and QUOTED_SUFFIX (quoted after its last
    dot), or'd -- UNQUOTED alone for a word it never writes quoted, and every one where the hook cannot see the quotes
    of the words it reads (quoted_words)."""
    written = quoted_words(a)
    return _ANY_SPELLING if written is None else written.get(prepare.deglob(name), UNQUOTED)


def quoted_name(a, name):
    """Whether the text being read spells the command word `name` only quoted (spellings), so no plain alias of it stands
    there (SPD-295)."""
    return not spellings(a, name) & UNQUOTED


def quoted_too(a, name):
    """Whether the text being read spells the command word `name` quoted as well as unquoted (spellings), so the word the
    reading has may be either, and is read as each runs: the alias, and on as a quoted word (SPD-308)."""
    written = spellings(a, name)
    return written != UNQUOTED and bool(written & UNQUOTED)


def quoted_words(a):
    """{each word the text being read writes quoted at least once: how it writes it (spellings)} -- SPD-295, SPD-308,
    above -- found once per text, at the first lookup that asks (ShellAnalysis.quoted_sets); none where no word of it is
    quoted.  None -- every word may be written any way -- where the reading has no text (a function body the line
    defines, where the walk that read it is not reading its text still: analyse.analyse_command), and in a `<( )` or `>(
    )` body where a global alias set words the text does not spell (under `alias -g QP="'git' push"`, `cat <(QP)` runs
    git's push), which the walk reads with no text until the body closes (ShellWalk.open_process_substitution, SPD-315:
    every other body is read by the text's quotes, as a `$( )` body is by its own)."""
    text = a.quoted_text
    if text is None:
        return None
    if a.quoted_sets is None:
        a.quoted_sets = _quoted_words(text) if any(q in text for q in "'\"\\") else {}
    return a.quoted_sets


def _quoted_words(text):
    """quoted_words' scan of `text`: each word as the shell splits it, taken apart into its value and whether any of it was
    quoted, and, for one that was, whether the text after its last dot was."""
    written = {}
    i, n = 0, len(text)
    while i < n:
        if text[i] in _SCAN_ENDS:
            i += 1
            continue
        start, value, quoted = i, [], False
        while i < n and text[i] not in _SCAN_ENDS:
            c = text[i]
            if c == "'":
                end = text.find("'", i + 1)
                end = n if end < 0 else end
                value.append(text[i + 1 : end])
                i, quoted = end + 1, True
            elif c == '"':
                i, quoted = _double_quoted(text, i + 1, value), True
            elif c == "\\":
                value.append(text[i + 1 : i + 2])
                i, quoted = i + 2, True
            else:
                value.append(c)
                i += 1
        word, spelled = "".join(value), UNQUOTED
        if quoted:
            raw = text[start:i]
            dot = raw.rfind(".")  # the same dot is the last of the word's value, a quote adding or taking none
            spelled = QUOTED_SUFFIX if dot >= 0 and any(q in raw[dot + 1 :] for q in "'\"\\") else QUOTED_STEM
        written[word] = written.get(word, 0) | spelled
    return {word: spelled for word, spelled in written.items() if spelled != UNQUOTED}


def _double_quoted(text, i, value):
    """The index past the `"` that closes a double-quoted string whose text starts at text[i], its value appended to
    `value`: a backslash escapes only `"`, `\\`, `$` and a backtick there."""
    n = len(text)
    while i < n and text[i] != '"':
        if text[i] == "\\" and text[i + 1 : i + 2] in ('"', "\\", "$", "`"):
            value.append(text[i + 1])
            i += 2
        else:
            value.append(text[i])
            i += 1
    return i + 1


def alias_rest(words, a):
    """The text the command's words after an alias's expansion stand for where the reading parses them again
    (alias_requoted, SPD-201), joined, where zsh looks none of them up as an alias; alias_texts after a body ending in
    a blank, where it does."""
    return " ".join(alias_requoted(w, a) for w in words)


# SPD-310: zsh looks the word after an alias whose body ends in a blank up as an alias too, wherever that word stands,
# so a wrapper at the head of the body, which takes the command position away from the words after it, does not end the
# chain (probed in zsh 5.9 -f and -f -o nobareglobqual, tests/probes/shell_probe.py, with `alias n='nice '`, `alias
# e='echo '` and `alias g2='echo G2-RAN'`: `eval 'n g2'` ran G2-RAN, and so did `n n g2`, `n m g2` with m another `nice
# `, and `e y g2` with y='e', the chain going on from the last alias the body expanded).  zsh reads the body as it reads
# text, so the body's own first word stands where the word did and is looked up too, and the chain runs on inside the
# body (`e z g2` with z='e g2' printed `echo G2-RAN g2`: z's g2 ran its alias, the g2 after z, behind a body with no
# blank at its end, did not); a name is not looked up inside its own expansion (`e k g2` with k='e k' printed `k g2`),
# and is again once its body is read (`n n g2`).  A quoted word, one holding an expansion, or one that is no alias ends
# the chain, and a suffix alias is never looked up there (`n a.txt` found no file a.txt).  The hook read such a body and
# the words after it as one text, whose wrapper left the next word a plain command (`alias s='sudo '; alias gp='git
# push'; eval 's gp'` pushed unread); the chain is now spelled into the text before it is read, as zsh parses it.
#
# SPD-313: zsh looks each of them up in the one table it holds, the snapshot's aliases and the line's (probed as above,
# the snapshot sourced: with its `s='nice '`, `ws='s'` and `u='gp'`, `alias lg=...; eval 's lg'` ran the line's lg, `alias
# ln2='nice '; eval 'ln2 gp'`, `s u` and `ws ws gp` its gp, and `alias gp=...; eval 's gp'` the line's gp where the
# line's own `s gp` ran the snapshot's: tests/test_hooks_snapshots.py MixedAliasChainTest).  The hook chained the line's
# aliases into the line's and the snapshot's into the snapshot's, never looking a chained body's first word up, so `s u`
# was read as `sudo gp`, a program; one walk now reads every word of a chain in the table zsh holds (_chain_bodies).
_CHAIN_WORD_RE = re.compile(r"([ \t]*)([^\s;&|<>()'\"\\$`\x00]+)(?=[\s;&|<>()]|\Z)")
CHAIN_STEPS = 64  # the most aliases one chain expands, past which the word is refused a member as one the hook cannot read
CHAIN_READINGS = 16  # ... and the most texts one word's chain is read as, bash's and sh's readings among them
_BLANK_END = (" ", "\t")


def alias_texts(name, body, words, a, notes):
    """[(a text zsh parses for a command word `name` an alias puts `body` in place of, the names in flight at its first
    word)] -- a line's alias (analyse.read_alias_body, SPD-317) or the snapshot's (shell_aliased, SPD-313): the body with
    the names in flight written quoted where it spells them (_held_in_flight), its own first word looked up too, then,
    where the body so read ends in a blank, the member's `words` after it with the chain spelled in (above), else those
    words as the reading parses them again (alias_rest).  Each word the chain reaches, where the text being read writes it
    unquoted, is replaced by what the table holds for it (_chain_at), and where it writes it quoted too, left quoted,
    which ends the chain (spellings, SPD-308, probed in zsh 5.9: after `alias v='V=1 '`, `v 'ls' -d /` ran the real ls),
    an empty quoted string before it keeping the reading from expanding it.  No name is in flight at the member's words
    but those ShellAnalysis.expanding holds: zsh looks them up once the body is read (SPD-317, _held_in_flight).  Each
    snapshot alias the chain expands is appended to `notes`, as ShellAnalysis.shell_expanded names it."""
    flight, steps, tails, out = tuple(a.expanding) + (name,), [CHAIN_STEPS], None, []
    for text, heads in _chain_at(_held_in_flight(body, flight), a, flight, notes, steps):
        chained = text.endswith(_BLANK_END)
        if chained and tails is None:
            tails = _chain_words(words, a, notes, steps)
        for rest in tails if chained else [alias_rest(words, a)]:
            out.append((text + (" " + rest if rest else ""), (name, *heads)))
    return _capped(out, name, a)


# SPD-317: zsh looks no name up as an alias while the text of its own expansion is read -- the body's first word and
# every other word of it -- and looks it up again once that text is read, so the words after the body are aliases again.
# Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py: `alias ls='ls -d'; eval 'ls /'`
# printed `/`; `alias ls='ls -d; ls -d /'; eval 'ls /tmp'` printed `.`, `/`, `/tmp`, and so did `&&` for the `;`; with
# `function m` and `alias m='echo M; m'`, `eval 'm q'` ran the function; with `function a`, `alias a=b b='a x'` ran it for
# `a y`, a name staying in flight while a body its own reached is read; `alias e='echo E;'; eval 'e e'` printed E twice.
# The hook read a line alias's body again as text with no name in flight, so `alias ls='ls -G'; eval ls` expanded ls at
# every level to the reading depth, refused a member.  A name held in ShellAnalysis.expanding would stand for the whole
# text read, the member's words after the body too (SPD-316's shape); the body's own words are marked instead, the flight
# thus scoped to exactly them.  An empty quoted string before a word changes no value it has in any shell, and every
# reading takes the word so written for a quoted one, which no plain or global alias expands (spellings, SPD-295), while
# a function or the command it names still runs.  A reserved word so written would no longer be one, and a here-document
# the body holds would have its text changed: there nothing is marked, and a name the body spells again is expanded again
# to the depth, refused a member, more than runs and never less.  So is a word inside a `$( )` or a `<( )` of the body,
# which zsh parses when it runs it, the alias no longer in flight (alias_words skips them).
def _held_in_flight(body, flight):
    """`body` with each word zsh reads unquoted and plain (alias_words) that names an alias in `flight` written after an
    empty quoted string, which keeps every later reading from expanding it (above)."""
    if "<<" in body or not any(name in body for name in flight):
        return body
    out, at = [], 0
    for start, end, plain in alias_words(body):
        word = body[start:end]
        if plain and word in flight and word not in syntax.RESERVED_WORDS and word not in syntax.ZSH_RESERVED_WORDS:
            out.append(body[at:start] + "''")
            at = start
    return "".join(out) + body[at:]


def _chain_words(words, a, notes, steps):
    """The texts zsh parses for the member's `words` after an alias body ending in a blank (alias_texts), from words[0]
    on."""
    rest = [alias_requoted(w, a) for w in words]
    if not rest:
        return [""]
    written, tails, out = spellings(a, words[0]), None, []
    if written & UNQUOTED:
        for head, _ in _chain_at(rest[0], a, tuple(a.expanding), notes, steps):
            if head.endswith(_BLANK_END):  # a body ending in a blank: the next word is looked up too
                tails = _chain_words(words[1:], a, notes, steps) if tails is None else tails
                out.extend(head + tail for tail in tails)
            else:
                out.append(" ".join([head] + rest[1:]))
    if written != UNQUOTED:
        out.append(" ".join(["''" + rest[0]] + rest[1:]))
    return _capped(out, prepare.deglob(words[0]), a)


def _chain_at(text, a, flight, notes, steps):
    """[(a text zsh parses for `text`, the names in flight at its first word past `flight`)], `text`'s first word standing
    where an alias is looked up, with the chain spelled in: that word, where it is one plain word, replaced by each body
    the table holds for it (_chain_bodies), that body read the same way with the name in flight, which zsh does not look
    up again (`flight`) there or anywhere else in the body (_held_in_flight, SPD-317), and, where the body so read ends in
    a blank, the text after the word too; `steps`, the expansions left (CHAIN_STEPS), past which the word is refused a
    member and read as it is written."""
    m = _CHAIN_WORD_RE.match(text)
    name = m.group(2) if m else None
    if name is None or name.startswith("#") or name in flight:
        return [(text, ())]
    bodies = _chain_bodies(name, a, notes)
    steps[0] -= sum(b is not None for b in bodies)
    if steps[0] < 0:
        note_alias_doubt(a, ("alias", name))
        return [(text, ())]
    lead, after, tails, out = m.group(1), text[m.end() :], None, []
    for body in bodies:
        if body is None:
            out.append((text, ()))
            continue
        for expanded, heads in _chain_at(_held_in_flight(body, flight + (name,)), a, flight + (name,), notes, steps):
            if expanded.endswith(_BLANK_END):
                tails = [t for t, _ in _chain_at(after, a, flight, notes, steps)] if tails is None else tails
                out.extend((lead + expanded + tail, (name,) + heads) for tail in tails)
            else:
                out.append((lead + expanded + after, (name,) + heads))
    return _capped(out, name, a)


def _capped(out, name, a):
    """At most CHAIN_READINGS of one word's readings -- several lines read a line at a time, each word written both ways,
    multiplying -- past which the word is refused a member as one the hook cannot read."""
    if len(out) > CHAIN_READINGS:
        note_alias_doubt(a, ("alias", name))
        del out[CHAIN_READINGS:]
    return out


def _chain_bodies(name, a, notes):
    """The bodies zsh's table puts in place of `name` along a chain (SPD-313), None for the word as written: in text parsed
    as the line runs, the line's own entry as that text was parsed (parsed_view: one it defined or cleared, the
    snapshot's among them, SPD-299), else the snapshot's where its aliases stand (_held_body); where bash and sh read the
    text a line at a time (line_reading), the table as it stands too.  A name the line's table doubts, or may hold under
    a name the hook cannot read, is refused a member (the "alias" finding) and read all the same."""
    view = parsed_view(a)
    tables = [] if view is None else [(view.table, name in view.doubted, view.unknown)]
    if view is not None and view.lines:
        tables.append((a.aliases, alias_doubted(a, name), a.alias_unknown))
    held = _held_body(name, a, notes) if not tables or any(name not in t for t, _, _ in tables) else None
    bodies, doubtful = ([held] if not tables else []), False
    for table, doubted, unknown in tables:
        body = table[name] if name in table else held
        doubtful = doubtful or (doubted if name in table else unknown)
        if body not in bodies:
            bodies.append(body)
    if doubtful:
        note_alias_doubt(a, ("alias", name))
    return bodies


def _held_body(name, a, notes):
    """The snapshot's plain alias body for `name` where its aliases stand (held_standing, SPD-290), named in `notes`; None
    where it holds none, or one the hook cannot read, refused a member as a command word's is ("shell-alias")."""
    held = snapshots.shell_table(a.home).aliases
    if name not in held or not held_standing(a):
        return None
    body = held[name]
    if body is None:
        note_alias_doubt(a, ("shell-alias", name))
    else:
        notes.append((name, held_shown("an alias", body)))
    return body


def word_aliases(a):
    """Whether the line's table holds a global or a suffix alias eval may expand -- one the line defines, or the shell's
    own (held_aliases) -- or one whose name it cannot read: the table as it stands where eval runs, which is the one it
    parses its words with (SPD-286)."""
    return any(k.startswith((GLOBAL_ALIAS, SUFFIX_ALIAS)) and (body is not None or alias_doubted(a, k))
               for k, body in a.aliases.items())


def alias_requoted(word, a):
    """prepare.requoted for a word the line already read where eval's text is read again with an alias body set before it
    (an alias's, analyse.dispatch_words; a snapshot alias's, shell_aliased): a word that spells a global alias of the
    table the text was parsed with (parsed_table, SPD-286) -- the line's own or the shell's (held_aliases) -- was quoted
    where it was read, or it would have been expanded then, so it is escaped, which keeps it from being expanded a second
    time (SPD-109, SPD-283)."""
    text = prepare.requoted(word)
    return "\\" + text if GLOBAL_ALIAS + prepare.deglob(word) in parsed_table(a) else text


def alias_words(text, bodies=None):
    """[(start, end, plain)] for each word of `text` where zsh reads it unquoted, `plain` when it holds no quote, escape,
    expansion or substitution, which a global alias's name alone can be.  A comment is no word, and neither is arithmetic
    `(( ))` or the text of a `$( )`, backticks, `${ }` or `$'...'`, which a word holding it is not plain for; a substitution's
    body is read again on its own, where its words are expanded (probed: `echo $(echo gp)` and `` echo `echo gp` `` ran the
    alias, `(( gp == 5 ))` read the variable).  A case pattern is read as a word, which zsh does not expand there: a
    refusal of more than runs, never less.

    SPD-293: nor is the body of a `<( )` or `>( )`, which zsh parses when it runs it, with the table as it stands then
    (probed: with `alias -g X=snapshot` held, `alias -g X=line; cat <(echo p1 X); echo top X` printed `p1 line`, then `top
    snapshot`), so the walk expands its words where it opens it (shell/walk ShellWalk.open_process_substitution) and the
    text around it leaves them be.  Its closing `)` is found as a `$( )`'s is (prepare.substitution_end).  `bodies`, a list:
    the words inside such bodies are read and not skipped, and each body's (start, end) is appended to it (plain_words)."""
    spans, i, n, start, plain = [], 0, len(text), None, True
    while i < n:
        c = text[i]
        if c in _WORD_ENDS:
            if start is not None:
                spans.append((start, i, plain))
                start = None
            if c in "<>" and text.startswith("(", i + 1):
                end = prepare.substitution_end(text, i)
                if bodies is None:
                    i = end + 1
                else:
                    bodies.append((i + 2, end))
                    i += 2
            elif text.startswith("((", i):
                end = text.find("))", i + 2)
                i = n if end < 0 else end + 2
            else:
                i += 1
            continue
        if start is None:
            if c == "#":
                end = text.find("\n", i)
                i = n if end < 0 else end
                continue
            start, plain = i, True
        if c == "'":
            end = text.find("'", i + 1)
            i, plain = (n if end < 0 else end + 1), False
        elif c == '"':
            i, plain = _double_quote_end(text, i + 1), False
        elif c == "\\":
            i, plain = i + 2, False
        elif c == "`":
            i, plain = prepare.backtick_end(text, i) + 1, False
        elif c == "$":
            i, plain = _dollar_end(text, i), False
        else:
            i += 1
    if start is not None:
        spans.append((start, n, plain))
    return spans


def _double_quote_end(text, i):
    """The index past the `"` that closes a double-quoted string whose text starts at text[i]."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            return i + 1
        if c == "\\":
            i += 2
        elif c == "`":
            i = prepare.backtick_end(text, i) + 1
        elif text.startswith("$(", i):
            i = prepare.substitution_end(text, i) + 1
        else:
            i += 1
    return n


def _dollar_end(text, i):
    """The index past what the `$` at text[i] opens: a `$( )` or `$(( ))`, a `${ }` (its braces counted), a `$'...'`."""
    if text.startswith("$(", i):
        return prepare.substitution_end(text, i) + 1
    if text.startswith("${", i):
        depth, j, n = 0, i + 1, len(text)
        while j < n:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
            if depth == 0:
                break
        return j
    if text.startswith("$'", i):
        return prepare.ansi_c_end(text, i + 2) + 1
    return i + 1


def global_aliased(text, a):
    """`text`, which the shell parses, with every global alias that stands there expanded where zsh expands it: in text
    parsed as the line runs (eval's, a substitution's, an alias body or a substitution inside them: ShellAnalysis
    .alias_scope), each the line's table held where the shell parsed that text (parsed_table, SPD-286) -- the line's own,
    and the shell's as the line left them (held_aliases), never one the same text defines or clears; in the line's own
    text and an alias body read in it, the shell's as its snapshot holds them (held_names, SPD-283).  Each
    unquoted word that spells one's name becomes its body, itself read again for the global aliases but its own (probed:
    `rc='rc more'` gave `rc more`), then a blank, as zsh adds one before a word the body is glued to.  A word that may be
    one the hook cannot resolve -- a doubtful definition or `unalias`, a body the line does not spell, a body of the shell's
    it cannot unquote (alias_doubt) -- or any word at all where the line defined a global alias whose name the hook cannot
    read, is refused a member (SPD-217); a known body is read all the same, so a refusal it earns comes first.  A `<( )` or
    `>( )` body's words are left to the walk, which expands them as zsh parses them (alias_words, global_word, SPD-293)."""
    names = global_names(a)
    if not names:
        return text
    budget = [GLOBAL_EXPANSIONS]
    out = _expand_global(text, names, a, frozenset(), budget)
    if unknown_global(a, names) and any(plain for _, _, plain in alias_words(out)):
        unread.record_unread(a, "alias-word", unread.unread_shown(text))
    return out


def global_names(a):
    """{name: body} for each global alias that stands in the text being read (global_aliased): in text parsed as the line
    runs, the table it was parsed with (parsed_table); in the line's own text, the shell's own (held_names)."""
    if a.alias_scope:
        return {k[len(GLOBAL_ALIAS) :]: body for k, body in parsed_table(a).items() if k.startswith(GLOBAL_ALIAS)}
    return held_names(a, GLOBAL_ALIAS)


def unknown_global(a, names):
    """Whether a global alias whose name the hook cannot read may stand among `names` (global_names): then any plain word
    may be one, and is refused a member unread."""
    return "" in names and parsed_doubted(a, GLOBAL_ALIAS)


def global_word(word, names, a, budget):
    """The text zsh parses in place of one word of a `<( )` or `>( )` body the walk reads (shell/walk, SPD-293) where
    `names` (global_names, with the body's AliasView in force) makes it a global alias -- its body with the global aliases
    it holds expanded, then a blank, as global_aliased writes it -- or None where it stays as it is: no such alias, or
    one whose body the hook cannot read, whose finding _expand_global records.  `budget`, the one reading's count of
    expansions left (GLOBAL_EXPANSIONS)."""
    out = _expand_global(word, names, a, frozenset(), budget)
    return None if out == word else out


def plain_words(text):
    """The words `text` writes unquoted (alias_words), those inside a `<( )` or `>( )` body among them: the ones the walk
    may take for a global alias's name in such a body, since its tokens no longer show which word the line quoted (`'X'`
    and `X` are one token).  Every such word of the text, not only a body's, since a case pattern's `)` in a body ends it
    where a count of parentheses would place it (alias_words): a word written quoted in a body and unquoted elsewhere is
    expanded in the body too, more than zsh runs, never less."""
    return {text[s:e] for s, e, plain in alias_words(text, []) if plain}


def _expand_global(text, names, a, in_use, budget):
    out, at = [], 0
    for start, end, plain in alias_words(text):
        word = text[start:end]
        if not plain or word not in names or word in in_use or not word:
            continue
        body = names[word]
        if parsed_doubted(a, GLOBAL_ALIAS + word) if a.alias_scope else body is None:
            note_alias_doubt(a, alias_doubt(a, GLOBAL_ALIAS, word, word))
        if body is None:
            continue
        if not a.alias_scope:  # the shell's own, named where a reason says what the word ran (bash_rule)
            a.shell_expanded.append((word, held_shown("a global alias", body)))
        budget[0] -= 1
        if budget[0] < 0:
            unread.record_unread(a, "alias-word", "past %d global alias expansions" % GLOBAL_EXPANSIONS)
            break
        out.extend((text[at:start], _expand_global(body, names, a, in_use | {word}, budget), " "))
        at = end
    out.append(text[at:])
    return "".join(out)


# The aliases and functions the shell already holds.  Claude Code starts a shell for every Bash call and sources
# its snapshot of the user's interactive shell in it (~/.claude/shell-snapshots/snapshot-zsh-*.sh), so a member's command
# word is expanded by that profile's aliases and run by its functions before any program does: on this Mac `gp` pushed,
# `gc -m x` committed, `g commit -m x` committed and `ggp` pushed, each reaching the hook as an unknown command with no
# finding at all.  Confirmed from a member's own Bash call: `type gp` printed "gp is an alias for git push",
# `type ggp` "ggp is a shell function from <that snapshot>", and `gst --short --branch` ran git and printed the worktree's
# status.  hooks/snapshots holds the table; these two read a command word against its plain aliases and its functions,
# and held_aliases puts its global and suffix aliases in the line's own alias table (SPD-283).  An alias the line itself
# defines is a different thing and read by the alias table: it reaches only text the shell parses as the line runs, eval's
# and a substitution's, once it has run (AliasView, SPD-286).
def shell_aliased(words, a):
    """([(a text the shell's own aliases may put in place of `words`, the member's own words that follow that expansion,
    as the line's reading tokenized them, the names in flight at its first word)], [(the name, what it runs)] for each
    snapshot alias expanded, the first name whose body the hook cannot read or None, whether the command word may be one
    no alias expands too), or ([], [], None, False) when the command word is none of them.  The own words are kept apart
    because a write a body makes into one of them, and a finding that spells one the hook cannot read, is the member's,
    which analyse_shell_text never prunes.

    A shell expands an alias where it parses the command word, textually and before any rule reads it, so the body and
    the words after it are analysed as the text the shell would have parsed -- `gc -m x` is `git commit --verbose -m x`
    and earns Law 7's own refusal -- each of those words quoted again as the line spelled it (prepare.requoted, SPD-201):
    `gc -m "don't"` is one message, not a quote that never closes.  The body's first word is looked up too, and where
    the body so read ends in a blank the next word (zsh's chaining rule, `_='sudo '`), on down the chain in the line's
    table and the snapshot's (_chain_at, SPD-313: under `ws=s` and `s='sudo '`, `ws ws gp` is `sudo sudo git push`).  A
    name is not expanded again while its own expansion is in flight, which is what stops `alias ls='ls -G'`: each text is
    read with the names in flight at its first word in ShellAnalysis.expanding (held_text.read_shell_alias), and no
    other, the member's words after the body being read once it is over; the body's own words that spell one are written
    quoted (alias_texts, SPD-317), as zsh looks none of them up.

    SPD-295: zsh expands no alias of a word quoted or escaped in any way (`\\gp`, `'gp'`, `g''p`, `"gp"`, `gp''`,
    `$'gp'`), which reaches this with its quotes already taken, so a name the text being read spells only quoted stands
    for no alias here (quoted_name) and the word is the command it names: under `alias git=hub`, `'git' push` is git's
    push.  The mark is on the text's names, not inside the word, which every reading by name would then have to strip.

    SPD-308: a command word the text spells both ways is read both ways (quoted_too), since the hook cannot tell which
    this is: expanded, and as spelled, which the caller reads on for (`'git' push; git status` pushes under `alias
    git=hub`); a word along the chain as alias_texts reads it (under `alias v='V=1 '`, `v 'git' push` is git's push).

    SPD-290: only where the snapshot's aliases stand (held_standing), as SPD-283 made its global and suffix ones.  zsh
    expands none in a function body the snapshot defines, written before its aliases, nor in a new shell's text, which
    never sources it (probed in zsh 5.9 -f, a `zsh -f -c` sourcing `unalias -a`, functions, then `alias gp='echo
    GP-RAN'`: a body's `gp`, `runit gp` through a body's `"$@"`, `zsh -f -c gp` and `zsh -f -c "eval gp"` found no
    command gp, where a body's `eval gp` and `$(gp)` and a line function's `gp` ran the alias).  Reading the body there
    reads other text, not more: under `alias git=hub`, a body's `git push` and `sh -c 'git push'` push.

    SPD-299: nor in text the shell parses as the line runs where the line had cleared it by then -- the table that text
    was parsed with (parsed_view, AliasView) holds the name, which clear_alias_line records for the snapshot's aliases
    too: zsh parses eval's words and a substitution's body with the aliases as they stand, so after `unalias git`,
    `unalias -a` or `unalias -m 'g*'` the word runs the command it names (probed: under a sourced `alias gp='echo
    SNAP-GP'`, `unalias gp; eval "gp x"`, `echo $(gp y)` and `cat <(gp ps)` found no command gp).  A name the line's
    table holds with a body is the line's own alias, which analyse.dispatch_words expanded in its place; one it doubts --
    an unalias that may not have run, or whose names the hook cannot read -- may stand or not, and is refused a member as
    the line's doubtful alias is (the "alias" finding), where global_aliased doubts the snapshot's global ones.  The
    line's own text, parsed before any of it runs, and a text that holds the unalias itself, still expand it."""
    found = snapshots.shell_table(a.home)
    if not found.aliases or not held_standing(a):  # no snapshot (every scratch home the suite builds), or none stands here
        return [], [], None, False
    name = prepare.deglob(words[0])
    if name in a.expanding or name not in found.aliases:
        return [], [], None, False
    written, view = spellings(a, name), parsed_view(a)
    if not written & UNQUOTED:
        return [], [], None, False
    if view is not None and name in view.table:  # the line cleared, redefined or doubted it before this was parsed
        if name in view.doubted:
            note_alias_doubt(a, ("alias", name))
        return [], [], None, False
    as_spelled, body = written != UNQUOTED, found.aliases[name]  # SPD-308: it may be a quoted word, read on as spelled
    if body is None:
        return [], [], name, as_spelled
    # SPD-313: the body's own first word and, where the body so read ends in a blank, the words after it, down the chain in
    # the line's table and the snapshot's; each text read with the names in flight at its first word, and those alone,
    # the body's own words that spell one of them written quoted (SPD-317)
    expanded = [(name, held_shown("an alias", body))]
    readings = [(text, list(words[1:]), list(names)) for text, names in alias_texts(name, body, words[1:], a, expanded)]
    return readings, expanded, None, as_spelled


def shell_function(name, a):
    """The shell text a function the shell already defines runs for this command word, or None; an alias of the same name
    is expanded first, as the shell does it (shell_aliased runs before this).  held_text.read_shell_name reads it once per
    call's words and starting state, so a body that calls itself with them from where it started reads it no further."""
    found = snapshots.shell_table(a.home)
    return found.body(name) if name in found.functions else None
