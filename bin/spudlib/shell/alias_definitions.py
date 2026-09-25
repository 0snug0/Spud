"""shell/alias_definitions: what a line does to the alias table -- `alias` and `unalias`, zsh's `unhash`, `disable` and
`enable`, and its aliases, galiases and saliases parameters.

Taken out of shell/line_aliases past its 1000-line band (SPD-314): each definition or clear a line makes, recorded into
ShellAnalysis.aliases where the shell reads it (record_alias_line, record_alias_definition, record_alias_unknown and
clear_alias_line, with ALIAS_TABLE_KINDS for zsh's parameters, read by analyse), for the text the shell parses after
it, which reads the table as alias_views.AliasView keeps it and line_aliases reads a word against it."""

from . import alias_spellings, alias_views, expansions, prepare, syntax
from ..hooks import snapshots


# `alias NAME=body` stores shell text the shell runs wherever it next reads NAME in command position (a global or a
# suffix alias elsewhere too, alias_views.GLOBAL_ALIAS).  A shell expands an alias when it parses the text, before the
# line runs, so an alias defined on the line reaches only code the shell parses as the line runs: `eval`'s words, and a
# substitution's body, inside eval or on the line itself, whose `$( )`, backtick and `<( )` bodies zsh parses when it
# runs them (probed in zsh 5.9 -f and -f -o nobareglobqual, SPD-283: `alias gp="echo GP-RAN"; echo $(gp)` printed
# GP-RAN, and so did `echo "$(gs)"`, `` echo `gq` `` and `cat <(gt)`, where `(gr)` ran nothing).
# analyse.analyse_isolated reads a `$( )` or backtick body as eval's words are (ShellAnalysis.alias_scope); the walk
# reads a `<( )` or `>( )` body a level into that scope too, where it opens (ShellWalk.open_process_substitution,
# SPD-287), and expands the global aliases standing there in the body's own words (ShellWalk.expand_globals, SPD-293:
# `alias -g X='; git push'; cat <(echo X)` pushes, as the same line with `$( )` does).  Probed in bash 3.2, zsh 5.9 -f,
# zsh -f -o nobareglobqual and sh with a fake git first on a scratch PATH: `alias gp='git push'; eval gp` pushed in zsh,
# zsh-nbgq and sh (bash expands no alias non-interactively without `shopt -s expand_aliases`, so it pushed nothing --
# noted, never relied on), and so did `alias -g GP=...; eval GP`, two definitions on one `alias` line, `eval 'gp; gp'`,
# `eval 'X=1 gp'`, `eval '{ gp; }'`, `eval 'if true; then gp; fi'`, `eval 'coproc gp'`, `eval 'time gp'`, `eval '! gp'`,
# `eval 'eval gp'`, `eval 'echo $(gp)'`, an alias reached from another alias, and one that shadows a function of the
# same name.  `alias g=git; g push` ran nothing anywhere (the alias does not exist when the line is parsed), nor did
# `eval 'command gp'`, `eval 'env gp'` or `eval 'sh -c gp'`, and `unalias` cleared.
#
# SPD-286: that holds inside such text too.  The shell parses a text it reads as the line runs -- eval's words, a `$( )`
# or backtick body, a trap's action, an (e) flag's value, a `-c` string -- whole before any of it runs, so an alias the
# text itself defines, changes or clears stands only in text parsed after it (a later eval or substitution), never in a
# command of the same text.  Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py: `eval
# 'alias ls="echo ALIASED"; ls -d /'` printed `/`, the real ls, and so did the same text on two lines, `echo $(alias
# ls=...; ls -d /)`, its backtick form, `trap 'alias ls=...; ls -d /' EXIT`, `x='$(alias ls=...; ls -d /)'; echo
# ${(e)x}`, `zsh -f -c` either way, an alias whose body defines ls and runs it, and a function eval defines that does,
# called twice; `eval 'alias -g QQ=GLOBAL; echo hi QQ'` printed `hi QQ` and `eval 'alias -s txt="echo SUFFIX"; a.txt'`
# found no command; after `alias ls=...`, `eval 'unalias ls; ls -d /'` ran the alias, and a global alias the text
# cleared still expanded in an alias body read in it, a suffix alias `unalias -s` cleared still ran.  Text parsed after
# it does see it: `eval 'alias ls=...'; eval 'ls -d /'`, `eval 'alias ls=...; eval "ls -d /"'` and `eval 'alias ls=...;
# echo $(ls -d /)'` each ran the alias.  The Bash tool's own line is such a text, which is why the line's own alias
# never reaches its own commands: Claude Code runs it as `/bin/zsh -c 'source <snapshot> ... && eval <line> < /dev/null
# && pwd -P >| ...'` (a member's own call's `ps -o args= -p $$`, 2026-09-24), where `alias zzq='echo ALIASED-TOP'` and
# `zzq hi` on the next line printed "(eval):2: command not found: zzq".  bash 3.2 (with `shopt -s expand_aliases`) and
# sh read such text a line at a time instead: `eval $'alias ls=...\nls -d /'`, `sh -c` on two lines, a `$( )` body and a
# trap's action on two lines each ran the alias there, and on one line none did -- which alias_views.AliasView.lines
# keeps for line_aliases.line_reading, and, for a new shell's text and a bash Bash tool's line, whose lines the walk
# tells apart, walk.ShellWalk.new_line (SPD-291), which reads eval's words, a trap's action and a substitution's body
# the same way where the shell running them parses them a line at a time (held_text.parsed_lines, SPD-323: `sh -c 'eval
# "alias gq=\"git push\"<newline>unalias gq; gq"'` pushes, the second line parsed before its unalias ran).
def record_alias(a, name, body, doubtful=False):
    """Record `alias NAME=body`, or an `unalias` (whose body is None), where the shell reads it.  The name goes into
    `assigned` under a key no variable can have, so every rule that doubts a variable the line assigned -- a branch that
    may not run, a subshell, a pipeline element, a background list, a loop or function body, a reading only one shell
    makes -- doubts the alias too, and a certain definition settles an earlier doubt as an assignment does.  The body
    itself stays out of `vars`, which holds the shell's variables alone (vouched_spud_call reads every name there).  It
    is recorded in the table as it stands, which text parsed after this reads; the text this runs in reads its own
    alias_views.AliasView (SPD-286)."""
    key = syntax.ALIAS_KEY + name
    a.aliases[name] = body
    a.assigned.append(key)
    if doubtful or a.unsure or a.loop_depth:
        a.doubt.add(key)
        if a.loop_depth:
            a.sticky.add(key)
    elif key not in a.sticky:
        a.doubt.discard(key)


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


ALIAS_TABLE_KINDS = {"aliases": ("plain",), "galiases": ("global",), "saliases": ("suffix",)}  # zsh's parameters (SPD-105)
_ALL_KINDS = ("plain", "global", "suffix")
_KIND_PREFIX = {"global": alias_views.GLOBAL_ALIAS, "suffix": alias_views.SUFFIX_ALIAS}


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
    """An alias of each of these kinds whose name the hook cannot read: every command word eval reads may be a plain
    one, every word a global one and every command word with a dot a suffix one (alias_spellings.alias_words,
    line_aliases.suffix_substitution)."""
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
    if not alias_spellings.alias_word_name(spelled):
        record_alias_unknown(a, [k for k in kinds if k != "plain"])
        return
    readable = value is not None and not expansions.expansion_word(value)
    body = prepare.deglob(value) if readable else None
    if identifier and ("plain" in kinds or "global" in kinds):
        record_alias(a, spelled, body, doubtful=not readable)
    if "global" in kinds:
        record_alias(a, alias_views.GLOBAL_ALIAS + spelled, body, doubtful=not readable)
    elif "plain" in kinds and alias_views.GLOBAL_ALIAS + spelled in a.aliases:
        record_alias(a, alias_views.GLOBAL_ALIAS + spelled, None)
    if "suffix" in kinds:
        record_alias(a, alias_views.SUFFIX_ALIAS + spelled, body, doubtful=not readable)


def clear_alias_line(words, a):
    """`unalias NAME ...` and `unalias -a` clear what the line aliased; an argument the hook cannot read (an expansion, or a
    pattern for zsh's `-m`) clears nothing and doubts every name instead.  `-s` clears suffix aliases alone, and without
    it no suffix alias is cleared (probed: `unalias -a` left `alias -s cfg=...` running).

    SPD-299: the pool holds the snapshot's plain aliases too, where the shell sourced the snapshot
    (alias_views.AliasView's `held`, held_text.snapshot_sourced: never in a new shell's text, and in a snapshot body,
    which runs once they are defined), so `unalias -a`, `-m` and a name the hook cannot read clear or doubt them as they
    do the line's own; a name spelled out is recorded whatever held it.  Each is recorded as cleared in the line's
    table, which text the shell parses after this reads (alias_views.AliasView) and line_aliases.shell_aliased then
    leaves unexpanded there (probed: under a sourced `alias gp='echo SNAP-GP'`, `unalias gp`, `unalias -a`, `unalias -m
    "g*"` and `X=gp; unalias $X` each left `eval "gp x"` and `echo $(gp y)` finding no command gp, while the line's own
    `gp top` still printed SNAP-GP).

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
    pool = [k for k in a.aliases if k.startswith(alias_views.SUFFIX_ALIAS) == suffix]
    if not suffix and (a.alias_view is None or a.alias_view.held):
        pool += [k for k in snapshots.shell_table(a.home).aliases if k not in a.aliases]
    return pool


def _named_keys(a, rest, suffix):
    """The table keys the names `rest` spell: a suffix alias's under its prefix; a plain name's, and its global alias's
    where the line's table holds one, the two sharing zsh's table."""
    if suffix:
        return [alias_views.SUFFIX_ALIAS + prepare.deglob(w) for w in rest]
    names = [prepare.deglob(w) for w in rest]
    return names + [alias_views.GLOBAL_ALIAS + n for n in names if alias_views.GLOBAL_ALIAS + n in a.aliases]


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
