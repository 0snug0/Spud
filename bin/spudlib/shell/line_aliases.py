"""shell/line_aliases: the aliases a line defines for eval, and the ones the shell already holds (SPD-284).

The seam SPD-284 took out of shell/expansions, past its 1000-line band once SPD-109 added zsh's global and suffix aliases:
`alias` and `unalias` lines and zsh's aliases, galiases and saliases parameters recorded into ShellAnalysis.aliases
(record_alias_line, clear_alias_line, record_alias_definition, read by analyse), the table as it stood where the shell
parsed the text being read (AliasView, SPD-286), the text eval's command word runs through it (alias_substitution,
parsed_alias, line_reading and suffix_substitution, read by analyse.dispatch_words and stdin_text; alias_requoted,
word_aliases), the global aliases expanded in text eval reads again (global_aliased, read by analyse), the global and
suffix aliases the shell's snapshot holds, put in that table where a line starts (held_aliases, SPD-283) and read where
the shell parsed the text with them (held_names), and a command word read against the plain aliases and the functions
the snapshot holds (shell_aliased and shell_function, read by held_text).  Past 250 lines as one reading: every function
here reads or writes the one alias table, or a word against the shell's own."""

from . import expansions, prepare, syntax, unread
from ..hooks import snapshots


# `alias NAME=body` stores shell text the shell runs wherever it next reads NAME in command position (a global or a suffix
# alias elsewhere too, below record_alias).  A shell expands an alias when it parses the text, before the line runs, so an
# alias defined on the line reaches only code the shell parses as the line runs: `eval`'s words, and a substitution's body,
# inside eval or on the line itself, whose `$( )`, backtick and `<( )` bodies zsh parses when it runs them (probed in zsh
# 5.9 -f and -f -o nobareglobqual, SPD-283: `alias gp="echo GP-RAN"; echo $(gp)` printed GP-RAN, and so did `echo
# "$(gs)"`, `` echo `gq` `` and `cat <(gt)`, where `(gr)` ran nothing).  analyse.analyse_isolated reads a `$( )` or
# backtick body as eval's words are (ShellAnalysis.alias_scope); the walk reads a `<( )` body with the line's own words,
# where the line's alias does not stand -- a reading of less than zsh runs, left for shell/walk.  Probed in bash 3.2, zsh 5.9 -f,
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
    content, as ShellAnalysis.reading_state compares it."""

    __slots__ = ("table", "doubted", "unknown", "lines", "key")

    def __init__(self, a, lines):
        self.table = dict(a.aliases)
        self.doubted = frozenset(k for k in self.table if alias_doubted(a, k))
        self.unknown, self.lines = a.alias_unknown, lines
        self.key = (frozenset(self.table.items()), self.doubted, self.unknown, lines)

    def __eq__(self, other):
        return isinstance(other, AliasView) and self.key == other.key

    def __hash__(self):
        return hash(self.key)


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
    it no suffix alias is cleared (probed: `unalias -a` left `alias -s cfg=...` running)."""
    rest = alias_arguments(words)
    options = words[1 : len(words) - len(rest)]
    flags = "".join(prepare.deglob(w)[1:] for w in options)
    suffix = "s" in flags
    pool = [k for k in a.aliases if k.startswith(SUFFIX_ALIAS) == suffix]
    if "a" in flags:
        names, doubtful = pool, False
    elif "m" in flags or any(expansions.active_read_word(w) for w in rest):
        names, doubtful = pool, True
    elif suffix:
        names, doubtful = [SUFFIX_ALIAS + prepare.deglob(w) for w in rest], False
    else:
        names, doubtful = [prepare.deglob(w) for w in rest], False
        names += [GLOBAL_ALIAS + n for n in names if GLOBAL_ALIAS + n in a.aliases]
    for name in names:
        record_alias(a, name, None, doubtful=doubtful)


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
    to them since (above).  None stand in a function body the shell holds, nor in text read inside one: the snapshot defines
    its functions before its aliases, and the harness's shadows after them hold no word one could be (hooks/snapshots
    HARNESS_SHADOWS); nor in a new shell's text, whose table analyse.analyse_new_shell empties, so held_aliases never put
    the name there.  Text the shell parses as the line runs reads the line's table instead (ShellAnalysis.alias_scope), as
    it stood where that text was parsed (AliasView, SPD-286)."""
    if a.shell_reading and a.body_locals is not None:
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
    holds for a command word: an alias the same text defines or clears changes neither."""
    table = parsed_table(a)
    if name not in table:
        view = parsed_view(a)
        return None, a.alias_unknown if view is None else view.unknown
    return table[name], parsed_doubted(a, name)


def line_reading(name, a):
    """(the body, whether it is doubtful) of the alias bash and sh may expand for a command word of a text of several lines
    they read a line at a time, where a line before this one defined, changed or cleared it (AliasView.lines, SPD-286):
    the table as it stands, where it differs from the one zsh parsed the text with; None where it does not, and in a text
    of one line, which every shell parses whole.  The table as it stands holds what this command's own line did too, which
    neither shell expands there: a reading of more than runs, never less."""
    view = parsed_view(a)
    if view is None or not view.lines:
        return None
    now = (a.aliases[name], alias_doubted(a, name)) if name in a.aliases else (None, a.alias_unknown)
    return None if now == parsed_alias(name, a) else now


def alias_doubted(a, key):
    """Whether the shell may not hold what the line's alias table says for this key, as the table stands; parsed_doubted
    reads it as the text being read was parsed."""
    key = syntax.ALIAS_KEY + key
    return key in a.doubt or key in a.sticky or a.all_doubt


def suffix_substitution(name, a, fresh=0):
    """(the text a suffix alias runs where the shell dispatches the command word `name` -- the body, to which the caller
    appends the word itself -- or None; the finding the word earns where the hook cannot be sure of it, or None): in text
    parsed as the line runs (ShellAnalysis.alias_scope), one the line's table holds, the line's own or the shell's as the
    line left it; in the line's own text, one the shell's snapshot holds as it holds it (held_names, SPD-283), never for a
    word an expansion gave (`fresh`, analyse.dispatch_words: probed, `F=a.txt; $F` found no command a.txt).  zsh looks the
    suffix up after the text past the word's last dot, a dot that does not open the word (probed: `b.a.txt` and
    `./d/a.txt` ran `alias -s txt=...`, `.txt` and `a.TXT` did not), and only once no plain alias of the whole word stands
    (probed: `alias a.txt=...` ran in place of `alias -s txt=...`), the shell's too, which shell_aliased then expands.
    The word reaches this with its quotes taken, so a quoted suffix (`a.'txt'`, which zsh does not expand) is read as one:
    that reads more than the shell runs, never less; so is a glob, whose text zsh looks the suffix up in before it globs
    (probed: `a?.txt` and `*.txt` ran the suffix alias with the file they matched).  The line's table is read as the text
    was parsed (parsed_table, SPD-286): `eval 'unalias -s txt; a.txt'` ran the suffix alias (probed)."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return None, None
    suffix, held, table = name[dot + 1 :], snapshots.shell_table(a.home), parsed_table(a)
    if name in held.aliases and (not a.alias_scope or name not in table):
        return None, None
    if not a.alias_scope:
        names = {} if fresh else held_names(a, SUFFIX_ALIAS)
        if suffix not in names:
            return None, None
        return names[suffix], (alias_doubt(a, SUFFIX_ALIAS, suffix, name) if names[suffix] is None else None)
    key = SUFFIX_ALIAS + suffix
    if key in table:
        return table[key], (alias_doubt(a, SUFFIX_ALIAS, suffix, name) if parsed_doubted(a, key) else None)
    if SUFFIX_ALIAS in table and parsed_doubted(a, SUFFIX_ALIAS):
        return None, ("unread", ("alias-word", name))
    return None, None


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


def alias_words(text):
    """[(start, end, plain)] for each word of `text` where zsh reads it unquoted, `plain` when it holds no quote, escape,
    expansion or substitution, which a global alias's name alone can be.  A comment is no word, and neither is arithmetic
    `(( ))` or the text of a `$( )`, backticks, `${ }` or `$'...'`, which a word holding it is not plain for; a substitution's
    body is read again on its own, where its words are expanded (probed: `echo $(echo gp)` and `` echo `echo gp` `` ran the
    alias, `(( gp == 5 ))` read the variable).  A case pattern is read as a word, which zsh does not expand there: a
    refusal of more than runs, never less."""
    spans, i, n, start, plain = [], 0, len(text), None, True
    while i < n:
        c = text[i]
        if c in _WORD_ENDS:
            if start is not None:
                spans.append((start, i, plain))
                start = None
            if text.startswith("((", i):
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
    read, is refused a member (SPD-217); a known body is read all the same, so a refusal it earns comes first."""
    if a.alias_scope:
        names = {k[len(GLOBAL_ALIAS) :]: body for k, body in parsed_table(a).items() if k.startswith(GLOBAL_ALIAS)}
    else:
        names = held_names(a, GLOBAL_ALIAS)
    if not names:
        return text
    budget = [GLOBAL_EXPANSIONS]
    out = _expand_global(text, names, a, frozenset(), budget)
    if "" in names and parsed_doubted(a, GLOBAL_ALIAS) and any(plain for _, _, plain in alias_words(out)):
        unread.record_unread(a, "alias-word", unread.unread_shown(text))
    return out


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
    """(the text the shell's own aliases put in place of `words`, the member's own words that follow that expansion, as
    the line's reading tokenized them, [(the name, what it runs)] for each one expanded, the first name whose body the hook
    cannot read), or (None, [], [], None) when the command word is none of them.  The own words are kept apart because a
    write a body makes into one of them, and a finding that spells one the hook cannot read, is the member's, which
    analyse_shell_text never prunes.

    A shell expands an alias where it parses the command word, textually and before any rule reads it, so the body and
    the words after it are analysed as the text the shell would have parsed -- `gc -m x` is `git commit --verbose -m x`
    and earns Law 7's own refusal -- each of those words quoted again as the line spelled it (prepare.requoted, SPD-201):
    `gc -m "don't"` is one message, not a quote that never closes.  When the body ends in a blank the next word is
    expanded too (zsh's chaining rule, `_='sudo '`), and a name is not expanded again while its own expansion is in
    flight, which is what stops `alias ls='ls -G'`.  A word the line quoted or escaped (`\\gp`, `'gp'`) reaches this
    with its quotes already taken and is expanded all the same: that is fail-closed -- the name it spells is no program --
    and telling the two apart would need a mark inside the command word that every reading by name would then have to
    strip (the expansion check makes the same choice for `'$X' push`)."""
    found = snapshots.shell_table(a.home)
    if not found.aliases:  # a machine with no snapshot, and every scratch home the suite builds: nothing to read
        return None, [], [], None
    out, expanded, i, at_command = [], [], 0, True
    while i < len(words) and at_command:
        name = prepare.deglob(words[i])
        if name in a.expanding or name not in found.aliases:
            break
        body = found.aliases[name]
        if body is None:
            return None, [], expanded, name
        spelled = body.strip()
        expanded.append((name, "an alias for `%s`" % (spelled if len(spelled) <= 120 else spelled[:117] + "...")))
        out.append(body)
        at_command = body.endswith((" ", "\t"))
        i += 1
    if not expanded:
        return None, [], [], None
    text = " ".join(out + [alias_requoted(w, a) for w in words[i:]])
    return text, list(words[i:]), expanded, None


def shell_function(name, a):
    """The shell text a function the shell already defines runs for this command word, or None; an alias of the same name
    is expanded first, as the shell does it (shell_aliased runs before this).  held_text.read_shell_name reads it once per
    call's words and starting state, so a body that calls itself with them from where it started reads it no further."""
    found = snapshots.shell_table(a.home)
    return found.body(name) if name in found.functions else None
