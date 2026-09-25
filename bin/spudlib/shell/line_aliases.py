"""shell/line_aliases: the aliases a line defines for eval, and the ones the shell already holds (SPD-284).

The seam SPD-284 took out of shell/expansions, past its 1000-line band once SPD-109 added zsh's global and suffix aliases:
`alias` and `unalias` lines and zsh's aliases, galiases and saliases parameters recorded into ShellAnalysis.aliases
(record_alias_line, clear_alias_line, record_alias_definition, read by analyse), the text eval's command word runs
through them (alias_substitution and suffix_substitution, read by analyse.dispatch_words and stdin_text; alias_requoted,
word_aliases), the global aliases expanded in text eval reads again (global_aliased, read by analyse), and a command word
read against the aliases and functions the shell's snapshot holds (shell_aliased and shell_function, read by held_text).
Past 250 lines as one reading: every function here reads or writes the one alias table, or a command word against the
shell's own."""

from . import expansions, prepare, syntax, unread
from ..hooks import snapshots


# `alias NAME=body` stores shell text the shell runs wherever it next reads NAME in command position (a global or a suffix
# alias elsewhere too, below record_alias).  A shell
# expands an alias when it parses the text, before the line runs, so an alias defined on the line
# reaches only code the line parses again: `eval`'s words, and a substitution inside them.  Probed in bash 3.2, zsh 5.9 -f,
# zsh -f -o nobareglobqual and sh with a fake git first on a scratch PATH: `alias gp='git push'; eval gp` pushed in zsh,
# zsh-nbgq and sh (bash expands no alias non-interactively without `shopt -s expand_aliases`, so it pushed nothing -- noted,
# never relied on), and so did `alias -g GP=...; eval GP`, two definitions on one `alias` line, `eval 'gp; gp'`, `eval 'X=1
# gp'`, `eval '{ gp; }'`, `eval 'if true; then gp; fi'`, `eval 'coproc gp'`, `eval 'time gp'`, `eval '! gp'`, `eval 'eval
# gp'`, `eval 'echo $(gp)'`, an alias reached from another alias, and one that shadows a function of the same name.  `alias
# g=git; g push` ran nothing anywhere (the alias does not exist when the line is parsed), nor did `eval 'command gp'`, `eval
# 'env gp'` or `eval 'sh -c gp'`, and `unalias` cleared.
def record_alias(a, name, body, doubtful=False):
    """Record `alias NAME=body`, or an `unalias` (whose body is None), where the shell reads it.  The name goes into
    `assigned` under a key no variable can have, so every rule that doubts a variable the line assigned -- a branch that may
    not run, a subshell, a pipeline element, a background list, a loop or function body, a reading only one shell makes --
    doubts the alias too, and a certain definition settles an earlier doubt as an assignment does.  The body itself stays out
    of `vars`, which holds the shell's variables alone (vouched_spud_call reads every name there)."""
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


def alias_substitution(name, a):
    """(the text an alias of this line's runs where `eval` dispatches its name, whether the hook cannot be sure of it), for a
    command word inside an `eval`.  (None, False) when the name is no alias of the line's and the line defined none
    the hook could not read; (None, True) when it may be one, or may have been cleared, and the hook cannot say what it runs."""
    if name not in a.aliases:
        return None, a.alias_unknown
    return a.aliases[name], alias_doubted(a, name)


def alias_doubted(a, key):
    """Whether the shell may not hold what the line's alias table says for this key, where eval reads it."""
    key = syntax.ALIAS_KEY + key
    return key in a.doubt or key in a.sticky or a.all_doubt


def suffix_substitution(name, a):
    """(the text a suffix alias of this line's runs where eval dispatches the command word `name`, whether the hook cannot
    be sure of it) -- the body, to which the caller appends the word itself -- or (None, False).  zsh looks the suffix up
    after the text past the word's last dot, a dot that does not open the word (probed: `b.a.txt` and `./d/a.txt` ran
    `alias -s txt=...`, `.txt` and `a.TXT` did not).  The word reaches this with its quotes taken, so a quoted suffix
    (`a.'txt'`, which zsh does not expand) is read as one: that reads more than the shell runs, never less."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return None, False
    key = SUFFIX_ALIAS + name[dot + 1 :]
    if key in a.aliases:
        return a.aliases[key], alias_doubted(a, key)
    return None, SUFFIX_ALIAS in a.aliases and alias_doubted(a, SUFFIX_ALIAS)


def word_aliases(a):
    """Whether the line defines a global or a suffix alias eval may expand, or one whose name it cannot read."""
    return any(k.startswith((GLOBAL_ALIAS, SUFFIX_ALIAS)) and (body is not None or alias_doubted(a, k))
               for k, body in a.aliases.items())


def alias_requoted(word, a):
    """prepare.requoted for a word the line already read where eval's text is read again with an alias body set before it
    (an alias's, analyse.dispatch_words; a snapshot alias's, shell_aliased): a word that spells one of the line's global
    aliases was quoted where eval read it, or it would have been expanded then, so it is escaped, which keeps it from
    being expanded a second time (SPD-109)."""
    text = prepare.requoted(word)
    return "\\" + text if GLOBAL_ALIAS + prepare.deglob(word) in a.aliases else text


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
    """`text`, which eval reads again (or an alias body, or a substitution inside either), with every global alias the line
    defined expanded where zsh expands it: each unquoted word that spells one's name becomes its body, itself read again
    for the line's global aliases but its own (probed: `rc='rc more'` gave `rc more`), then a blank, as zsh adds one before
    a word the body is glued to.  A word that may be one the hook cannot resolve -- a doubtful definition or `unalias`, a
    body the line does not spell -- or any word at all where the line defined a global alias whose name the hook cannot
    read, is refused a member unread (SPD-217); a known body is read all the same, so a refusal it earns comes first."""
    names = {k[len(GLOBAL_ALIAS) :]: body for k, body in a.aliases.items() if k.startswith(GLOBAL_ALIAS)}
    if not names:
        return text
    budget = [GLOBAL_EXPANSIONS]
    out = _expand_global(text, names, a, frozenset(), budget)
    if "" in names and alias_doubted(a, GLOBAL_ALIAS) and any(plain for _, _, plain in alias_words(out)):
        unread.record_unread(a, "alias-word", unread.unread_shown(text))
    return out


def _expand_global(text, names, a, in_use, budget):
    out, at = [], 0
    for start, end, plain in alias_words(text):
        word = text[start:end]
        if not plain or word not in names or word in in_use or not word:
            continue
        body, doubtful = names[word], alias_doubted(a, GLOBAL_ALIAS + word)
        if doubtful:
            unread.record_unread(a, "alias-word", word)
        if body is None:
            continue
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
# status.  hooks/snapshots holds the table; these two read a command word against it.  An alias the line itself defines is
# a different thing and read by the alias table: it reaches only text the line parses again, which is `eval`.
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
