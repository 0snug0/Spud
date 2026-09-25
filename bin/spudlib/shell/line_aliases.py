"""shell/line_aliases: a word read against the aliases a line defines, and the ones the shell already holds (SPD-284).

The seam SPD-284 took out of shell/expansions, past its 1000-line band once SPD-109 added zsh's global and suffix
aliases; SPD-314 took four more out of this one past its own: what a line does to the alias table
(shell/alias_definitions), the table as the shell parsed the text being read and whether the snapshot's aliases stand
there (shell/alias_views, AliasView, SPD-286), how that text writes each word (shell/alias_spellings, SPD-295,
SPD-308), and the text an alias body and the words after it stand for (shell/alias_chains, SPD-310, SPD-313, SPD-317).
What stays is the reading of a word against the table: the text a command word runs through it (alias_substitution,
parsed_alias, line_reading and suffix_substitution, read by analyse.dispatch_words, walk and stdin_text; body_spells and
word_aliases, read by analyse), the global aliases expanded in the words of text the shell parses (global_aliased, read
by analyse and bash_rule; global_names, global_word and unknown_global, read by shell/walk for a `<( )` body, SPD-293),
the global and suffix aliases the shell's snapshot holds, put in the line's table where a line starts (held_aliases,
SPD-283) and read where the shell parsed the text with them (held_names, alias_doubt), and a command word read against
the plain aliases and the functions the snapshot holds (shell_aliased and shell_function, read by held_text), each kind
only where the snapshot's aliases stand (alias_views.held_standing, SPD-290).  Past 250 lines as one reading: every
function here reads a word against the one alias table, or against the shell's own.

Where each alias stands, as the reading now has it (the comments beside each reading hold the probes):

- the line's own alias -- plain, global or suffix -- in text the shell parses as the line runs, a level into
  ShellAnalysis.alias_scope with the table as it stood where that text was parsed (alias_views.AliasView, SPD-286):
  eval's words (analyse.dispatch_words); a `$( )` or backtick body, a trap's action and an (e) flag's value
  (analyse.analyse_isolated, SPD-283); a `<( )` or `>( )` body, its command words (ShellWalk.open_process_substitution,
  SPD-287) and its global aliases in every unquoted word (ShellWalk.expand_globals, SPD-293), which the line's own text
  leaves to it (alias_spellings.alias_words); a glob qualifier's `e` or `+` code (ShellWalk.read_qualifier_code,
  SPD-292); and each further pass of a loop whose body changed the table (ShellWalk.read_loop_again, SPD-294).  Never in
  the line's own text, parsed before any of it runs, nor in a word of the same text that defined it, nor in a new
  shell's text (analyse.analyse_new_shell) -- but for a later line of text a shell reads a line at a time, each parsed
  once the lines before it ran: a script fed to a shell, sh's, dash's and ksh's `-c` string, bash's both ways, and the
  Bash tool's own line where its shell may be bash (ShellWalk.new_line, held_text.text_lines and tool_lines, SPD-291),
  and eval's words, a trap's action and a `$( )`, backtick or `<( )` body where the shell running them parses them so,
  as sh and bash do (held_text.parsed_lines, alias_views.AliasView.shell, ShellWalk.new_body_line, SPD-323); and in a
  bash's text, which may expand no alias at all, both ways wherever it stands: the alias and the word as written
  (alias_views.spelled_too, AliasView.bare, SPD-322), and a global alias, which bash has none of, refused where the
  shell may be a bash (alias_views.global_spelled, SPD-328);
- the snapshot's global and suffix aliases in the line's own text as the snapshot holds them, and in text parsed as the
  line runs as the line left them (held_aliases, held_names, SPD-283); its plain aliases on a command word
  (shell_aliased); none of them in a function body the snapshot defines, parsed before its aliases, except in the text
  that body parses as it runs, nor anywhere in a new shell's text (alias_views.held_standing, SPD-290, SPD-300) but a
  zsh's that sources the user's startup files: all of them read as the line's own text, part of them both ways
  (SPD-301);
- a quoted or escaped word: a global alias never expands it (alias_spellings.alias_words; a `<( )` body's token, only
  where the text writes it unquoted: alias_spellings.plain_words), nor a plain alias, the line's or the snapshot's, nor
  a suffix alias where the text after its last dot is quoted, each read by how the text being read writes the word
  (alias_spellings.spellings, SPD-295), and one it writes both ways read both ways, the alias and on as a quoted word
  runs (SPD-308): more than runs, never less;
- `unalias` clears what the line and the snapshot -- its plain, global and suffix aliases -- hold for text parsed after
  it (alias_definitions.clear_alias_line; shell_aliased for the plain ones, SPD-299), and doubts every name where the
  hook cannot read what it clears or it may not have run."""

from . import alias_chains, alias_spellings, alias_views, prepare, syntax, unread
from ..hooks import snapshots


GLOBAL_EXPANSIONS = 256  # the most global aliases one reading expands, past which the text is refused a member unread


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
    starts it from the options: each goes into the line's own alias table under its kind's prefix, a global alias with
    its plain entry too, as the line's own `alias -g` and `alias -s` put them there
    (alias_definitions.record_alias_definition), so what the line does to one where it runs -- `alias -g`, `alias -s`, a
    plain `alias` of the name, `unalias` -- changes it for the text the shell parses after that, and every rule that
    reads, doubts, copies or joins the table reads them.  A body the hook cannot read is recorded doubtful, never as
    cleared.  A global alias's name a word cannot spell unquoted (alias_spellings.alias_word_name) is never expanded,
    and is left out.  Called once, where analyse.analyse_command reads the line, and where analyse.analyse_new_shell
    reads the text of a new zsh that sources the user's startup files (SPD-301): one that sources part of them
    (alias_views.held_partly) holds each doubtful, since the hook cannot tell whether those files define it."""
    held, partly = snapshots.shell_table(a.home), alias_views.held_partly(a)
    entries = [(alias_views.GLOBAL_ALIAS + name, body) for name, body in held.galiases.items()
               if alias_spellings.alias_word_name(name)]
    entries += [(name, body) for name, body in held.galiases.items() if syntax.IDENTIFIER_RE.match(name)]
    entries += [(alias_views.SUFFIX_ALIAS + suffix, body) for suffix, body in held.saliases.items()]
    for key, body in entries:
        a.aliases[key] = body
        if body is None or partly:
            a.doubt.add(syntax.ALIAS_KEY + key)


def held_names(a, prefix):
    """The shell's own global aliases (prefix alias_views.GLOBAL_ALIAS) or suffix aliases (alias_views.SUFFIX_ALIAS)
    that stand in the text being read at the line's own parse, each name -> its body as the snapshot holds it, None for
    one the hook cannot read: in the line's own text, which the shell parses before any of it runs, and an alias body
    read in it, whatever the line did to them since (above).  None stand where alias_views.held_standing says the
    snapshot's aliases do not (SPD-290): a function body the snapshot defines before its aliases, and a new shell's
    text, whose table analyse.analyse_new_shell empties besides, so held_aliases never put the name there.  Text the
    shell parses as the line runs reads the line's table instead (ShellAnalysis.alias_scope), as it stood where that
    text was parsed (alias_views.AliasView, SPD-286) -- inside a snapshot body too, where a substitution read at scope
    0, since the line's table holds none, now reads them as its shell parses it then.  In a new zsh that sources part of
    the snapshot's startup files (alias_views.held_partly, SPD-301) each is one the hook cannot read, None: it may stand
    there or not, and a word spelling its name is refused a member (alias_doubt)."""
    if not alias_views.held_standing(a):
        return {}
    held, partly = snapshots.shell_table(a.home), alias_views.held_partly(a)
    table = held.galiases if prefix == alias_views.GLOBAL_ALIAS else held.saliases
    return {name: None if partly else body for name, body in table.items() if prefix + name in a.aliases}


def alias_doubt(a, prefix, name, shown):
    """The finding a word earns where a global (prefix alias_views.GLOBAL_ALIAS) or suffix alias
    (alias_views.SUFFIX_ALIAS) called `name` may run in its place but the hook cannot read what it runs: "shell-alias",
    naming `shown`, for one the shell's snapshot holds with a body the hook cannot read -- its quoting, not the line, is
    what the member can do nothing about -- and else an "unread" alias-word finding, for one the line defined that the
    hook cannot resolve (SPD-109)."""
    held = snapshots.shell_table(a.home)
    if (held.galiases if prefix == alias_views.GLOBAL_ALIAS else held.saliases).get(name, "") is None:
        return ("shell-alias", shown)
    return ("unread", ("alias-word", shown))


def alias_substitution(name, a):
    """(the text an alias of this line's runs where `eval` dispatches its name, whether the hook cannot be sure of it), for a
    command word inside an `eval`, read as parsed_alias reads it.  (None, False) when the name is no alias of the line's and
    the line defined none the hook could not read; (None, True) when it may be one, or may have been cleared, and the hook
    cannot say what it runs -- and where bash and sh, reading the text a line at a time, may hold another (line_reading)."""
    body, doubtful = parsed_alias(name, a)
    return body, doubtful or line_reading(name, a) is not None


def parsed_alias(name, a):
    """(the body, whether it is doubtful) of the alias the table the text being read was parsed with
    (alias_views.parsed_table, SPD-286) holds for a command word: an alias the same text defines or clears changes
    neither.  None stands for a name the text spells only quoted, which zsh expands no alias of, whatever its name
    (alias_spellings.quoted_name, SPD-295: `alias ls=...; eval "'ls' -d /"` ran the real ls); for one it spells both
    ways the alias stands, and the caller reads the word on as a quoted one runs too (alias_spellings.quoted_too,
    SPD-308)."""
    table = alias_views.parsed_table(a)
    if name not in table:
        view = alias_views.parsed_view(a)
        unknown = a.alias_unknown if view is None else view.unknown
        return None, unknown and not alias_spellings.quoted_name(a, name)
    if alias_spellings.quoted_name(a, name):
        return None, False
    return table[name], alias_views.parsed_doubted(a, name)


def line_reading(name, a):
    """(the body, whether it is doubtful) of the alias bash and sh may expand for a command word of a text of several
    lines they read a line at a time, where a line before this one defined, changed or cleared it
    (alias_views.AliasView.lines, SPD-286): the table as it stands, where it differs from the one zsh parsed the text
    with; None where it does not, and in a text of one line, which every shell parses whole.  The table as it stands
    holds what this command's own line did too, which neither shell expands there: a reading of more than runs, never
    less.

    SPD-323: and never the table a later line was parsed with, where this command's own line cleared or changed what a
    line before defined, which is why the walk reads each line of such a text with the table the lines before it left
    where the shell running it parses it a line at a time and the text, or an alias body it may run, spells a word that
    changes the table (held_text.parsed_lines, body_spells, walk.ShellWalk.new_line and new_body_line); this stays for
    the rest -- the text's first line, zsh's reading of it, and a text whose table only a function changes (whose
    `alias` is doubted, alias_definitions.record_alias) or that the walk reads with no newline told apart (a `<( )` body
    in text read whole)."""
    view = alias_views.parsed_view(a)
    if view is None or not view.lines or alias_spellings.quoted_name(a, name):
        return None  # bash and sh expand no alias of a quoted word either
    now =(a.aliases[name], alias_views.alias_doubted(a, name)) if name in a.aliases else (None, a.alias_unknown)
    return None if now == parsed_alias(name, a) else now


def body_spells(a, words):
    """Whether an alias that may run in the text being read has a body that spells one of `words` -- the table's as it
    stands, and the snapshot's plain aliases where they stand (alias_views.held_standing): analyse.analyse_command reads
    a text of several lines a line at a time where a line may change the table, and one may through an alias whose body
    does (SPD-323, probed through tests/probes/shell_probe.py with GNU bash 3.2.57, zsh 5.9 -f and -f -o nobareglobqual
    driving each shell: after `alias f='alias ls="echo ALIASED"' g='unalias ls'`, eval's `f` then `g; ls -d /` on the
    next line printed `ALIASED -d /` in /bin/sh, /bin/dash and /bin/bash -O expand_aliases, `/` in /bin/zsh -f)."""
    bodies = [body for body in a.aliases.values() if body]
    if alias_views.held_standing(a):
        bodies += [body for body in snapshots.shell_table(a.home).aliases.values() if body]
    return any(word in body for body in bodies for word in words)


def suffix_substitution(name, a, fresh=0, plain=False):
    """(the text a suffix alias runs where the shell dispatches the command word `name` -- the body, to which the caller
    appends the word itself -- or None; the finding the word earns where the hook cannot be sure of it, or None; whether
    the word may run something else instead, which the caller reads on for): in text parsed as the line runs
    (ShellAnalysis.alias_scope), one the line's table holds, the line's own or the shell's as the line left it; in the
    line's own text, one the shell's snapshot holds as it holds it (held_names, SPD-283), never for a word an expansion
    gave (`fresh`, analyse.dispatch_words: probed, `F=a.txt; $F` found no command a.txt).  zsh looks the suffix up after
    the text past the word's last dot, a dot that does not open the word (probed: `b.a.txt` and `./d/a.txt` ran `alias
    -s txt=...`, `.txt` and `a.TXT` did not), and only once no plain alias of the whole word stands (probed: `alias
    a.txt=...` ran in place of `alias -s txt=...`), the shell's too, which shell_aliased then expands -- where it stands
    (alias_views.held_standing, SPD-290: `zsh -c 'alias -s txt=...; eval a.txt'` ran the suffix alias, the new shell
    holding no plain a.txt).  A glob is read as one, whose text zsh looks the suffix up in before it globs (probed:
    `a?.txt` and `*.txt` ran the suffix alias with the file they matched).  The line's table is read as the text was
    parsed (alias_views.parsed_table, SPD-286): `eval 'unalias -s txt; a.txt'` ran the suffix alias (probed).

    SPD-295: zsh looks up only the text after the last dot as the word is written, so a quote or an escape there keeps
    the suffix alias from running, and one before the dot does not (probed in zsh 5.9 -f and -f -o nobareglobqual:
    `'a.cfg'`, `a.'cfg'`, `a.cf\\g` and `a.cfg''` ran nothing, `a\\.cfg`, `"a".cfg` and `'a'.cfg` ran `alias -s cfg`).
    The word reaches this with its quotes taken, so it is read by how the text being read writes it
    (alias_spellings.spellings): with its suffix quoted it runs no suffix alias, and a plain alias of the whole word,
    which zsh expands in the suffix alias's place, does not stand where it is quoted at all, so `a\\.cfg` runs the
    suffix alias whatever plain `a.cfg` there is.

    SPD-308: by the word's own spellings, not every word's with that suffix (`'a.cfg'; b.cfg` runs one suffix alias, for
    b.cfg), and where the text writes the word more than one way, the ways it may run beside this are read on for: an
    unquoted one's plain alias of the whole word -- the shell's, which the caller then reads (held_text.read_shell_name);
    the line's, which it read before this (`plain`) -- and one quoted after its last dot, the command it names."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return None, None, True
    suffix, held, table = name[dot + 1 :], snapshots.shell_table(a.home), alias_views.parsed_table(a)
    key = alias_views.SUFFIX_ALIAS + suffix
    if not a.alias_scope:
        names = {} if fresh else held_names(a, alias_views.SUFFIX_ALIAS)
        if suffix not in names:
            return None, None, True
        body, finding = names[suffix], (alias_doubt(a, alias_views.SUFFIX_ALIAS, suffix, name)
                                        if names[suffix] is None else None)
    elif key in table:
        body, finding = table[key], (alias_doubt(a, alias_views.SUFFIX_ALIAS, suffix, name)
                                     if alias_views.parsed_doubted(a, key) else None)
    elif alias_views.SUFFIX_ALIAS in table and alias_views.parsed_doubted(a, alias_views.SUFFIX_ALIAS):
        body, finding = None, ("unread", ("alias-word", name))
    else:
        return None, None, True
    # ... one may run: by how the text writes the word, read once one stands, so a text no suffix alias reaches is not scanned
    written = alias_spellings.spellings(a, name)
    shell_plain = name in held.aliases and alias_views.held_standing(a) and (not a.alias_scope or name not in table)
    if not (written & alias_spellings.QUOTED_STEM or written & alias_spellings.UNQUOTED and not (plain or shell_plain)):
        return None, None, True  # no way the word is written runs its suffix alias
    return body, finding, bool(written & alias_spellings.QUOTED_SUFFIX
                               or written & alias_spellings.UNQUOTED and shell_plain)


def word_aliases(a):
    """Whether the line's table holds a global or a suffix alias eval may expand -- one the line defines, or the shell's
    own (held_aliases) -- or one whose name it cannot read: the table as it stands where eval runs, which is the one it
    parses its words with (SPD-286)."""
    return any(k.startswith((alias_views.GLOBAL_ALIAS, alias_views.SUFFIX_ALIAS))
               and (body is not None or alias_views.alias_doubted(a, k))
               for k, body in a.aliases.items())


def global_aliased(text, a):
    """`text`, which the shell parses, with every global alias that stands there expanded where zsh expands it: in text
    parsed as the line runs (eval's, a substitution's, an alias body or a substitution inside them: ShellAnalysis
    .alias_scope), each the line's table held where the shell parsed that text (alias_views.parsed_table, SPD-286) --
    the line's own, and the shell's as the line left them (held_aliases), never one the same text defines or clears; in
    the line's own text and an alias body read in it, the shell's as its snapshot holds them (held_names, SPD-283).
    Each unquoted word that spells one's name becomes its body, itself read again for the global aliases but its own
    (probed: `rc='rc more'` gave `rc more`), then a blank, as zsh adds one before a word the body is glued to.  A word
    that may be one the hook cannot resolve -- a doubtful definition or `unalias`, a body the line does not spell, a
    body of the shell's it cannot unquote (alias_doubt) -- or any word at all where the line defined a global alias
    whose name the hook cannot read, is refused a member (SPD-217); a known body is read all the same, so a refusal it
    earns comes first.  A `<( )` or `>( )` body's words are left to the walk, which expands them as zsh parses them
    (alias_spellings.alias_words, global_word, SPD-293)."""
    names = global_names(a)
    if not names:
        return text
    budget = [GLOBAL_EXPANSIONS]
    out = _expand_global(text, names, a, frozenset(), budget)
    if unknown_global(a, names) and any(plain for _, _, plain in alias_spellings.alias_words(out)):
        unread.record_unread(a, "alias-word", unread.unread_shown(text))
    if out != text and alias_views.global_spelled(a):  # a bash runs the words as written, which this reading does not
        unread.record_unread(a, "bash-alias", unread.unread_shown(text))  # (SPD-322)
    return out


def global_names(a):
    """{name: body} for each global alias that stands in the text being read (global_aliased): in text parsed as the line
    runs, the table it was parsed with (alias_views.parsed_table); in the line's own text, the shell's own (held_names)."""
    if a.alias_scope:
        return {k[len(alias_views.GLOBAL_ALIAS) :]: body for k, body in alias_views.parsed_table(a).items()
                if k.startswith(alias_views.GLOBAL_ALIAS)}
    return held_names(a, alias_views.GLOBAL_ALIAS)


def unknown_global(a, names):
    """Whether a global alias whose name the hook cannot read may stand among `names` (global_names): then any plain word
    may be one, and is refused a member unread."""
    return "" in names and alias_views.parsed_doubted(a, alias_views.GLOBAL_ALIAS)


def global_word(word, names, a, budget):
    """The text zsh parses in place of one word of a `<( )` or `>( )` body the walk reads (shell/walk, SPD-293) where
    `names` (global_names, with the body's alias_views.AliasView in force) makes it a global alias -- its body with the
    global aliases it holds expanded, then a blank, as global_aliased writes it -- or None where it stays as it is: no
    such alias, or one whose body the hook cannot read, whose finding _expand_global records.  `budget`, the one
    reading's count of expansions left (GLOBAL_EXPANSIONS)."""
    out = _expand_global(word, names, a, frozenset(), budget)
    return None if out == word else out


def _expand_global(text, names, a, in_use, budget):
    out, at = [], 0
    for start, end, plain in alias_spellings.alias_words(text):
        word = text[start:end]
        if not plain or word not in names or word in in_use or not word:
            continue
        body = names[word]
        if alias_views.parsed_doubted(a, alias_views.GLOBAL_ALIAS + word) if a.alias_scope else body is None:
            alias_views.note_alias_doubt(a, alias_doubt(a, alias_views.GLOBAL_ALIAS, word, word))
        if body is None:
            continue
        if not a.alias_scope:  # the shell's own, named where a reason says what the word ran (bash_rule)
            a.shell_expanded.append((word, alias_views.held_shown("a global alias", body)))
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
# and a substitution's, once it has run (alias_views.AliasView, SPD-286).
def shell_aliased(words, a):
    """([(a text the shell's own aliases may put in place of `words`, the member's own words that follow that expansion,
    as the line's reading tokenized them, the names in flight at its first word)], [(the name, what it runs)] for each
    snapshot alias expanded, the first name whose body the hook cannot read or None, whether the command word may be one
    no alias expands too), or ([], [], None, False) when the command word is none of them.  The own words are kept apart
    because a write a body makes into one of them, and a finding that spells one the hook cannot read, is the member's,
    which analyse_shell_text never prunes.

    A shell expands an alias where it parses the command word, textually and before any rule reads it, so the body and
    the words after it are analysed as the text the shell would have parsed -- `gc -m x` is `git commit --verbose -m x`
    and earns Law 7's own refusal -- each of those words quoted again as the line spelled it (prepare.requoted,
    SPD-201): `gc -m "don't"` is one message, not a quote that never closes.  The body's first word is looked up too,
    and where the body so read ends in a blank the next word (zsh's chaining rule, `_='sudo '`), on down the chain in
    the line's table and the snapshot's (alias_chains._chain_at, SPD-313: under `ws=s` and `s='sudo '`, `ws ws gp` is
    `sudo sudo git push`).  A name is not expanded again while its own expansion is in flight, which is what stops
    `alias ls='ls -G'`: each text is read with the names in flight at its first word in ShellAnalysis.expanding
    (held_text.read_shell_alias), and no other, the member's words after the body being read once it is over; the body's
    own words that spell one are written quoted (alias_chains.alias_texts, SPD-317), as zsh looks none of them up.

    SPD-295: zsh expands no alias of a word quoted or escaped in any way (`\\gp`, `'gp'`, `g''p`, `"gp"`, `gp''`,
    `$'gp'`), which reaches this with its quotes already taken, so a name the text being read spells only quoted stands
    for no alias here (alias_spellings.quoted_name) and the word is the command it names: under `alias git=hub`, `'git'
    push` is git's push.  The mark is on the text's names, not inside the word, which every reading by name would then
    have to strip.

    SPD-308: a command word the text spells both ways is read both ways (alias_spellings.quoted_too), since the hook
    cannot tell which this is: expanded, and as spelled, which the caller reads on for (`'git' push; git status` pushes
    under `alias git=hub`); a word along the chain as alias_chains.alias_texts reads it (under `alias v='V=1 '`, `v
    'git' push` is git's push).

    SPD-290: only where the snapshot's aliases stand (alias_views.held_standing), as SPD-283 made its global and suffix
    ones.  zsh expands none in a function body the snapshot defines, written before its aliases, nor in a new shell's
    text, which never sources it (probed in zsh 5.9 -f, a `zsh -f -c` sourcing `unalias -a`, functions, then `alias
    gp='echo GP-RAN'`: a body's `gp`, `runit gp` through a body's `"$@"`, `zsh -f -c gp` and `zsh -f -c "eval gp"` found
    no command gp, where a body's `eval gp` and `$(gp)` and a line function's `gp` ran the alias).  Reading the body
    there reads other text, not more: under `alias git=hub`, a body's `git push` and `sh -c 'git push'` push.

    SPD-299: nor in text the shell parses as the line runs where the line had cleared it by then -- the table that text
    was parsed with (alias_views.parsed_view, alias_views.AliasView) holds the name, which
    alias_definitions.clear_alias_line records for the snapshot's aliases too: zsh parses eval's words and a
    substitution's body with the aliases as they stand, so after `unalias git`, `unalias -a` or `unalias -m 'g*'` the
    word runs the command it names (probed: under a sourced `alias gp='echo SNAP-GP'`, `unalias gp; eval "gp x"`, `echo
    $(gp y)` and `cat <(gp ps)` found no command gp).  A name the line's table holds with a body is the line's own
    alias, which analyse.dispatch_words expanded in its place; one it doubts -- an unalias that may not have run, or
    whose names the hook cannot read -- may stand or not, and is refused a member as the line's doubtful alias is (the
    "alias" finding), where global_aliased doubts the snapshot's global ones.  The line's own text, parsed before any of
    it runs, and a text that holds the unalias itself, still expand it."""
    found = snapshots.shell_table(a.home)
    if not found.aliases or not alias_views.held_standing(a):
        return [], [], None, False  # no snapshot (every scratch home the suite builds), or none stands here
    name = prepare.deglob(words[0])
    if name in a.expanding or name not in found.aliases:
        return [], [], None, False
    written, view = alias_spellings.spellings(a, name), alias_views.parsed_view(a)
    if not written & alias_spellings.UNQUOTED:
        return [], [], None, False
    if view is not None and name in view.table:  # the line cleared, redefined or doubted it before this was parsed
        if name in view.doubted:
            alias_views.note_alias_doubt(a, ("alias", name))
        return [], [], None, False
    # SPD-308: it may be a quoted word, read on as spelled; SPD-301: or run in a new zsh whose startup files may not define it
    as_spelled, body = written != alias_spellings.UNQUOTED or alias_views.held_partly(a), found.aliases[name]
    if body is None:
        return [], [], name, as_spelled
    # SPD-313: the body's own first word and, where the body so read ends in a blank, the words after it, down the chain in
    # the line's table and the snapshot's; each text read with the names in flight at its first word, and those alone,
    # the body's own words that spell one of them written quoted (SPD-317)
    expanded = [(name, alias_views.held_shown("an alias", body))]
    readings = [(text, list(words[1:]), list(names))
                for text, names in alias_chains.alias_texts(name, body, words[1:], a, expanded)]
    return readings, expanded, None, as_spelled


def shell_function(name, a):
    """The shell text a function the shell already defines runs for this command word, or None; an alias of the same name
    is expanded first, as the shell does it (shell_aliased runs before this).  held_text.read_shell_name reads it once per
    call's words and starting state, so a body that calls itself with them from where it started reads it no further."""
    found = snapshots.shell_table(a.home)
    return found.body(name) if name in found.functions else None
