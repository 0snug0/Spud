"""shell/alias_views: the alias table the text being read was parsed with, whether the snapshot's aliases stand there,
and how sure the reading is of each entry.

Taken out of shell/line_aliases past its 1000-line band (SPD-314), the part every reading of a word against the alias
table starts from: the table's keys for zsh's global and suffix aliases (GLOBAL_ALIAS, SUFFIX_ALIAS, SPD-109); the table
as it stood where the shell parsed a text it reads as the line runs (AliasView, SPD-286; PARTLY, TOOL_SHELL), which
every word of that text reads (parsed_view, parsed_table, parsed_doubted; alias_doubted, the table as it stands);
whether the aliases the shell's snapshot defines stand in that text (held_standing, held_partly: SPD-290, SPD-301);
whether a word an alias stands in runs as it is written too, where the shell parsing it may be a bash (spelled_too,
global_spelled: SPD-322, SPD-327, SPD-328); and the two notes a reading makes of an alias, a doubt recorded once
(note_alias_doubt) and what an alias of the shell's ran (held_shown).  Read by line_aliases, alias_chains,
alias_definitions, analyse, held_text, line_functions, loop_bindings, stdin_text and walk; it imports nothing of the
shell's reading but held_options and syntax, so the import cycle's modules reach it and it reaches none of them."""

from . import held_options, syntax


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


class AliasView:
    """The alias table a text the shell parses as the line runs was parsed with (SPD-286, probed above
    alias_definitions.record_alias): ShellAnalysis.aliases, the doubt of each name in it and alias_unknown, as they
    stood where the reading of that text began -- eval's (analyse.dispatch_words) and a body read in its own process
    (analyse.analyse_isolated), held in ShellAnalysis.alias_view while it is read.  Every word of that text reads it
    (line_aliases.parsed_alias, line_aliases.suffix_substitution, line_aliases.global_aliased,
    alias_chains.alias_requoted), an alias body read in it too, being part of the same parse; what the text itself
    defines or clears goes into ShellAnalysis.aliases alone, for text parsed after it.  `lines`: the text holds a
    newline, where bash and sh, reading it a line at a time, may expand what a line before defined
    (line_aliases.line_reading).  Equal by content, as ShellAnalysis.reading_state compares it.

    SPD-290: two more marks say whether the aliases the shell's snapshot defines stand in the text (held_standing).
    `held`: the shell that parses it sourced the snapshot -- False in a new shell's text (analyse.analyse_new_shell) and
    in every text parsed inside it, which inherits it (`outer`); `early`: the text is a function body the snapshot defines,
    parsed before the snapshot's aliases (held_text.read_body), which no text the body parses as it runs inherits.

    SPD-301: `held` is PARTLY in a new zsh that sources part of the startup files the snapshot came from -- started
    interactive or login but not both, or plain where a .zshenv exists (held_text.shell_start) -- where the hook cannot
    tell which of the snapshot's names those files define: its plain aliases and functions are read both ways, the body
    and the command as spelled (held_partly), and its global and suffix aliases doubted (line_aliases.held_aliases,
    line_aliases.held_names).

    SPD-322: `bare`, the shell that parses the text may expand no alias in it at all -- a bash, whose expand_aliases is
    off where it is not interactive (held_text.expands_no_alias), set where analyse.analyse_new_shell reads its text and
    inherited, as `held` is, by every text parsed inside it: there a command word a plain or suffix alias of the view
    stands in is read both ways, the alias and the word as written, and a word a global alias stands in, which bash has
    none of, is refused a member unread (global_spelled).  The Bash tool's own texts (`shell` TOOL_SHELL) are marked
    False here and asked in spelled_too instead, where a bash snapshot may leave expand_aliases off (SPD-327), and in
    global_spelled wherever a snapshot may be bash's (SPD-328).

    SPD-323: `shell`, the name of the shell that parses the text -- TOOL_SHELL for the Bash tool's own, a new shell's
    base name where analyse.analyse_new_shell reads its text, "" for one the hook cannot name -- inherited, as `held` is,
    by every text parsed inside it: how that shell reads eval's words, a substitution's body and a trap's action, whole
    or a line at a time (held_text.parsed_lines)."""

    __slots__ = ("table", "doubted", "unknown", "lines", "held", "early", "bare", "shell", "key")

    def __init__(self, a, lines, held=None, early=False, bare=None, shell=None):
        self.table = dict(a.aliases)
        self.doubted = frozenset(k for k in self.table if alias_doubted(a, k))
        self.unknown, self.lines = a.alias_unknown, lines
        self.held = (a.alias_view is None or a.alias_view.held) if held is None else held
        self.early = early
        self.bare = (a.alias_view is not None and a.alias_view.bare) if bare is None else bare
        self.shell = (TOOL_SHELL if a.alias_view is None else a.alias_view.shell) if shell is None else shell
        self.key = (frozenset(self.table.items()), self.doubted, self.unknown, lines, self.held, self.bare, self.shell,
                    early)

    def parsed_late(self):
        """This view, but for text parsed with the snapshot's aliases in force: a function body the line defines, read
        where a call inside a snapshot function's body runs it (held_text.read_body), was parsed with the line."""
        if not self.early:
            return self
        view = object.__new__(AliasView)
        for field in ("table", "doubted", "unknown", "lines", "held", "bare", "shell"):
            setattr(view, field, getattr(self, field))
        view.early, view.key = False, self.key[:-1] + (False,)
        return view

    def whole(self):
        """This view for a text the shell parsed with the text it stands for, whole: a dash's `$( )` or backtick body,
        parsed with the line around it (held_text.substitution_view, SPD-326)."""
        if not self.lines:
            return self
        view = object.__new__(AliasView)
        for field in ("table", "doubted", "unknown", "held", "early", "bare", "shell"):
            setattr(view, field, getattr(self, field))
        view.lines, view.key = False, self.key[:3] + (False,) + self.key[4:]
        return view

    def __eq__(self, other):
        return isinstance(other, AliasView) and self.key == other.key

    def __hash__(self):
        return hash(self.key)



# SPD-301: AliasView.held for a new zsh that sources part of the files Claude Code's snapshot came from (AliasView)
PARTLY = "partly"
# SPD-323: AliasView.shell for the Bash tool's own shell, zsh or bash as held_text.tool_lines reads it: no shell's name
TOOL_SHELL = "the Bash tool's shell"


def held_partly(a):
    """Whether the text being read runs in a new zsh that sources part of the snapshot's startup files (AliasView.held
    PARTLY, SPD-301), where a snapshot name may stand or not: read both ways, or doubted where it cannot be."""
    view = a.alias_view
    return view is not None and view.held == PARTLY


def held_standing(a):
    """Whether the aliases the shell's snapshot defines stand in the text being read (SPD-290; SPD-283 for the global
    and suffix ones, line_aliases.held_names): in the line's own text, an alias body read in it, and text the Bash
    tool's shell parses as the line runs -- eval's, a substitution's, on the line or inside a snapshot function's body,
    which run with the aliases defined -- and a function body the line defines, parsed with the line; never in a
    function body the snapshot defines, which it writes before its aliases (so zsh parsed it with none), nor anywhere in
    a new shell's text, which never sources the snapshot (AliasView's `held` and `early`) -- unless it is a zsh that
    sources the user's startup files, where they stand, in part of them only as held_partly reads them (SPD-301,
    held_text.shell_start).  The harness's shadows, which the snapshot defines after its aliases, are read as the line's
    own text (held_text.read_body)."""
    view = a.alias_view
    return view is None or bool(view.held) and not view.early


def parsed_view(a):
    """The AliasView the words being read expand with: the innermost text parsed as the line runs, where alias_scope says
    one is being read; None elsewhere -- the line's own text, and a new shell's, which read the table as it stands, and
    only their eval and substitutions read a line's alias (analyse.analyse_command, line_aliases.held_names)."""
    return a.alias_view if a.alias_scope else None


def parsed_table(a):
    """The alias table the text being read was parsed with: its AliasView's, else the table as it stands."""
    view = parsed_view(a)
    return a.aliases if view is None else view.table


def parsed_doubted(a, key):
    """alias_doubted, as the text being read was parsed: its AliasView's doubt, else the doubt as it stands."""
    view = parsed_view(a)
    return alias_doubted(a, key) if view is None else key in view.doubted


def alias_doubted(a, key):
    """Whether the shell may not hold what the line's alias table says for this key, as the table stands; parsed_doubted
    reads it as the text being read was parsed."""
    key = syntax.ALIAS_KEY + key
    return key in a.doubt or key in a.sticky or a.all_doubt


# SPD-322: bash expands no alias at all where it is not interactive -- in eval's words and a substitution's body as in
# its own text -- so an alias its text defines, which the hook reads in the text parsed as that text runs (the view
# above), may stand there or not.  Probed through tests/probes/shell_probe.py (2026-09-25), zsh 5.9 -f -o
# nobareglobqual, zsh 5.9 -f and bash 3.2.57 each driving /bin/bash 3.2.57 after `alias ls="echo ALIASED"`: `bash -c`
# with `eval ls -d /` printed `/`, and so did `echo "[$(ls -d /)]"`, its backtick form, `cat <(ls -d /)`, `trap "ls -d
# /" EXIT`, `eval "eval ls -d /"`, a `$( )` inside a `$( )`, a function's `eval ls -d /`, the eval on the text's next
# line, `bash -l -c`, and the text fed to bash by a pipe, with `-s` and without; `shopt -s expand_aliases` before it,
# `-O expand_aliases`, `-i` and `--posix` printed `ALIASED -d /`, as `/bin/sh -c` did.  Such a command word is read both
# ways, the alias and on as it is written (analyse.dispatch_words), as one the text spells quoted and not is
# (alias_spellings.quoted_too) -- a suffix alias's too.  bash has no global or suffix alias at all (`alias -g X=...`
# printed `alias: -g: invalid option`, and `alias -s txt=...` the same of -s, each status 2, defining nothing: a later
# `eval X` found no command X), so a word a global alias stands in runs as written, which the hook, reading the alias's
# words in its place, refuses a member unread (line_aliases.global_aliased, walk.ShellWalk.expand_globals).
#
# SPD-327: the Bash tool's own shell is such a bash where a snapshot it may source is bash's and leaves expand_aliases
# off (held_options.tool_expands_no_alias): there `alias git=echo; eval git push` on the member's own line pushed with
# no finding, the whole reading held_text.tool_lines gives the line standing for no alias in the line's own words alone.
def spelled_too(a):
    """Whether a command word of the text being read runs as it is written as well as the alias the text was parsed with
    (SPD-322, above): the text is parsed as the line runs by a shell that may expand no alias at all -- a bash's text
    (AliasView.bare), or the Bash tool's own where its snapshot may leave expand_aliases off (SPD-327), asked only here,
    where an alias stands in a word, so no other line reads a snapshot for it."""
    view = parsed_view(a)
    if view is None:
        return False
    return view.bare or view.shell == TOOL_SHELL and held_options.tool_expands_no_alias(a)


# SPD-328: bash has no global alias whatever expand_aliases says (SPD-322's probe above), so where the Bash tool's shell
# may be a bash -- a snapshot any session may source is bash's -- a word a global alias stands in runs as written there
# even under Claude Code's own snapshot, which turns expand_aliases on: `alias -g git=echo; eval git push` on the member's
# own line read as `echo push` and pushed with no finding.  spelled_too asks the snapshot's options, which decide only
# the plain and suffix aliases.
def global_spelled(a):
    """Whether a word a global alias stands in, in the text being read, runs as it is written (SPD-322, SPD-328, above):
    the text is parsed as the line runs by a shell that may be a bash, which has no global alias -- a bash's text
    (AliasView.bare), or the Bash tool's own where a snapshot it may source is bash's (held_options.tool_may_be_bash),
    asked only where a global alias stood in a word."""
    view = parsed_view(a)
    if view is None:
        return False
    return view.bare or view.shell == TOOL_SHELL and held_options.tool_may_be_bash(a)


def note_alias_doubt(a, finding):
    """Record line_aliases.alias_doubt's finding once, as unread.record_unread records one: the first of a kind the line
    earns stands."""
    if finding not in a.findings:
        a.findings.append(finding)


def held_shown(kind, body):
    """What ShellAnalysis.shell_expanded says an alias of the shell's runs, for a reason's note: `kind` and its body, cut
    as line_aliases.shell_aliased cuts a plain alias's."""
    spelled = body.strip()
    return "%s for `%s`" % (kind, spelled if len(spelled) <= 120 else spelled[:117] + "...")
