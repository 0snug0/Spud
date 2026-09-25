"""shell/held_options: The options the shell holds, read into the state a line starts from.

A module of its own since SPD-267: line_options and option_effect taken out of shell/held_text, and the option tables they
read out of shell/syntax, which nothing else reads.  analyse.analyse_command calls line_options once, at a line's first
reading.  A leaf the shell reading's import cycle reaches: it imports hooks/snapshots and nothing of the cycle.
tool_expands_no_alias (SPD-327) reads whether a bash snapshot turns expand_aliases on, for alias_views.spelled_too;
tool_may_be_bash (SPD-328) whether any snapshot is bash's, for held_text.tool_lines and alias_views.global_spelled.  A
bash snapshot's `set -o` lines are read against bash's own names (BASH_SET_ON, SPD-329), a zsh's against zsh's."""

import os

from ..hooks import snapshots


def line_options(a):
    """Start a line's reading from the options the shell holds (SPD-263): the snapshot's option lines run before every
    line, so CDABLE_VARS there is ShellAnalysis.cdable from the line's first word, CHASE_LINKS or CHASE_DOTS
    ShellAnalysis.chase, an option that changes arithmetic ShellAnalysis.arith_opaque; and an option the reader does not
    model, which may change how the shell reads the line's words (the option tables below), is an "unread" finding that
    names the profile's line, refused a member.  Every snapshot's options count, any of them may be the one sourced, and a
    `set -o` line in a bash's is read against bash's names (SPD-329)."""
    table = snapshots.shell_table(a.home)
    for kind, name, on, line, index in table.options:
        bash = kind == "set" and 0 <= index < len(table.files) and snapshots.is_bash(table.files[index])
        effect = option_effect(kind, name, on, bash)
        if effect == "cdable":
            a.cdable = True
        elif effect == "chase":
            a.chase = True
        elif effect == "arith":
            a.arith_opaque = True
        elif effect == "unread":
            where = os.path.basename(table.files[index]) if 0 <= index < len(table.files) else "a shell snapshot"
            a.findings.append(("unread", ("option", "`%s` (%s)" % (line.strip(), where))))


# SPD-327: bash expands no alias at all where it is not interactive -- in eval's words and a substitution's body as in its
# own text (alias_views.spelled_too has the probe) -- until `shopt -s expand_aliases` turns it on, and the snapshot's
# option lines are what turn it on in the Bash tool's shell.  Claude Code writes that line into every bash snapshot it
# makes, after the `shopt -p` lines of the shell that made it (read 2026-09-25 in the snapshot script of Claude Code
# 2.1.282: `echo "shopt -s expand_aliases" >> "$SNAPSHOT_FILE"`, with or without a startup file to source), so a bash
# snapshot without it -- another writer's, an older one's -- leaves the line's eval and substitution words to run as they
# are written as well.  Which snapshot a session sources is not in the hook's input, so each bash snapshot is read on its
# own, in line order, the last `shopt` line naming the option deciding it (a snapshot's `shopt -p` may print it off before
# the harness's line turns it on; hooks/snapshots.last_option_lines, SPD-329).  Read only where a word an alias stands in
# asks, once per process: each path's answer.
_EXPANDS_ALIASES = {}


def tool_may_be_bash(a):
    """Whether the Bash tool's own shell may be a bash: a snapshot any session may source is bash's, by its name
    (`snapshot-<shell>-<stamp>-<id>.sh`, hooks/snapshots) -- held_text.tool_lines's question (SPD-291), and
    alias_views.global_spelled's, bash having no global alias whatever its options (SPD-328)."""
    for path in snapshots.shell_table(a.home).files:
        if snapshots.is_bash(path):
            return True
    return False


def tool_expands_no_alias(a):
    """Whether the Bash tool's own shell may expand no alias in text it parses as the line runs -- eval's words, a `$( )`,
    backtick or `<( )` body, a trap's action (SPD-327, above): a bash snapshot any session may source leaves
    expand_aliases off, or cannot be read.  zsh expands one there whatever its options; with no bash snapshot, False."""
    for path in snapshots.shell_table(a.home).files:
        if snapshots.is_bash(path) and not snapshot_expands_aliases(path):
            return True
    return False


def snapshot_expands_aliases(path):
    """Whether the bash snapshot at `path` leaves expand_aliases on: its last `shopt -s|-u` line naming the option, read
    as hooks/snapshots reads a snapshot's option lines; False where none does or the file cannot be read."""
    if path not in _EXPANDS_ALIASES:
        on = False
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            data = b""
        for kind, name, state, _, _ in snapshots.read_snapshot(data, 0, bash=True)[3]:
            if kind == "shopt" and name is not None and name.lower() == "expand_aliases":
                on = state
        _EXPANDS_ALIASES[path] = on
    return _EXPANDS_ALIASES[path]


def option_effect(kind, name, on, bash=False):
    """What one option line of the snapshot does to the line the shell reads next: None, "cdable", "chase", "arith" or
    "unread", as the option tables below say (-- the options a shell snapshot sets --).  `kind` is the builtin that set it,
    "setopt" (zsh, `unsetopt` turning it off), "shopt" (bash) or "set" (`set -o`, zsh's names, or bash's where `bash`: the
    line is a bash snapshot's, SPD-329); a name None is a line the snapshot reader could not take apart."""
    if name is None:
        return "unread"
    if kind == "shopt":
        name = name.lower()
        if name == "cdable_vars":
            return "cdable" if on else None
        if name in BASH_SHOPT_INERT or on == (name in BASH_SHOPT_ON):
            return None
        return "unread"
    if kind == "set" and bash:  # bash's names, spelled only as bash spells them (BASH_SET_ON)
        if name == "physical":
            return "chase" if on else None
        if name in BASH_SET_INERT or on == (name in BASH_SET_ON):
            return None
        return "unread"
    name = name.lower().replace("_", "")
    if name == "physical":  # zsh's other name for CHASE_LINKS, and bash's `set -o physical`
        name = "chaselinks"
    if name not in ZSH_OPTIONS_ON and name not in ZSH_OPTIONS_OFF and name.startswith("no"):
        name, on = name[2:], not on  # `nohashdirs`, `NO_CDABLE_VARS`: the option's name after a `no` (nomatch, notify are names)
    if name not in ZSH_OPTIONS_ON and name not in ZSH_OPTIONS_OFF:
        return "unread"
    if on == (name in ZSH_OPTIONS_ON) or name in ZSH_OPTIONS_INERT:
        return None  # its default state, or a state that changes nothing the hook reads
    if name == "cdablevars":
        return "cdable"
    if name in ZSH_OPTIONS_CHASE:
        return "chase"
    if name in ZSH_OPTIONS_ARITH:
        return "arith"
    return "unread"


# -- the options a shell snapshot sets (SPD-263, line_options) -------------------------
# Claude Code's snapshot of the user's shell holds the options the profile sets, which the Bash tool's shell sources before
# every line: zsh's `setopt | sed 's/^/setopt /'` (one `setopt <name>` per option in a state other than zsh's default, `no`
# before the name of one turned off), bash's `shopt -p` (a `shopt -s` or `shopt -u` line for every option).  Each is read as
# (the option, on or off) and has one of these effects on the line the shell then reads:
#
# - none, where it is zsh's or bash's default state, or where the state changes nothing the hook reads (the *_INERT names);
# - "cdable", zsh's CDABLE_VARS or bash's cdable_vars on (ShellAnalysis.cdable);
# - "chase", zsh's CHASE_LINKS or CHASE_DOTS on (ShellAnalysis.chase);
# - "arith", an option that changes the number an arithmetic expansion gives (ShellAnalysis.arith_opaque, SPD-225);
# - "unread", any other state: it may change how the shell reads a line's words or runs its commands in a way the hook does
#   not model (probed: SH_WORD_SPLIT split `$X` holding `a b` in two, GLOB_SUBST globbed `$X` holding `g*`, KSH_ARRAYS made
#   `$A` its first element, EXTENDED_GLOB made `^keep` a glob, RC_QUOTES made `'it''s'` it's), so a member's line is
#   refused, naming the profile's line, and an option neither table knows is read the same way.
#
# zsh's options and their defaults, as `set -o` listed them in zsh 5.9 -f (tests/probes/shell_probe.py, 2026-09-24): the
# ones on by default, then every other one, off.  A name is read as zsh reads one, case and underscores ignored, and a `no`
# before a name that is not an option's own (nomatch and notify are) turns it off.
ZSH_OPTIONS_ON = frozenset({
    "aliases", "alwayslastprompt", "appendhistory", "autolist", "automenu", "autoparamkeys", "autoparamslash",
    "autoremoveslash", "badpattern", "banghist", "bareglobqual", "beep", "bgnice", "caseglob", "casematch", "checkjobs",
    "checkrunningjobs", "clobber", "debugbeforecmd", "equals", "evallineno", "exec", "flowcontrol", "functionargzero", "glob",
    "globalexport", "globalrcs", "hashcmds", "hashdirs", "hashlistall", "histbeep", "histsavebycopy", "hup", "listambiguous",
    "listbeep", "listtypes", "multibyte", "multifuncdef", "multios", "nomatch", "notify", "promptcr", "promptpercent",
    "promptsp", "rcs", "shortloops", "unset"})
ZSH_OPTIONS_OFF = frozenset({
    "aliasfuncdef", "allexport", "alwaystoend", "appendcreate", "autocd", "autocontinue", "autonamedirs", "autopushd",
    "autoresume", "bashautolist", "bashrematch", "braceccl", "bsdecho", "casepaths", "cbases", "cdablevars", "cdsilent",
    "chasedots", "chaselinks", "clobberempty", "combiningchars", "completealiases", "completeinword", "continueonerror",
    "correct", "correctall", "cprecedences", "cshjunkiehistory", "cshjunkieloops", "cshjunkiequotes", "cshnullcmd",
    "cshnullglob", "dvorak", "emacs", "errexit", "errreturn", "extendedglob", "extendedhistory", "forcefloat", "globassign",
    "globcomplete", "globdots", "globstarshort", "globsubst", "hashexecutablesonly", "histallowclobber",
    "histexpiredupsfirst", "histfcntllock", "histfindnodups", "histignorealldups", "histignoredups", "histignorespace",
    "histlexwords", "histnofunctions", "histnostore", "histreduceblanks", "histsavenodups", "histsubstpattern", "histverify",
    "ignorebraces", "ignoreclosebraces", "ignoreeof", "incappendhistory", "incappendhistorytime", "interactive",
    "interactivecomments", "ksharrays", "kshautoload", "kshglob", "kshoptionprint", "kshtypeset", "kshzerosubscript",
    "listpacked", "listrowsfirst", "localloops", "localoptions", "localpatterns", "localtraps", "login", "longlistjobs",
    "magicequalsubst", "mailwarning", "markdirs", "menucomplete", "monitor", "nullglob", "numericglobsort", "octalzeroes",
    "overstrike", "pathdirs", "pathscript", "pipefail", "posixaliases", "posixargzero", "posixbuiltins", "posixcd",
    "posixidentifiers", "posixjobs", "posixstrings", "posixtraps", "printeightbit", "printexitvalue", "privileged",
    "promptbang", "promptsubst", "pushdignoredups", "pushdminus", "pushdsilent", "pushdtohome", "rcexpandparam", "rcquotes",
    "recexact", "rematchpcre", "restricted", "rmstarsilent", "rmstarwait", "sharehistory", "shfileexpansion", "shglob",
    "shinstdin", "shnullcmd", "shoptionletters", "shortrepeat", "shwordsplit", "singlecommand", "singlelinezle",
    "sourcetrace", "sunkeyboardhack", "transientrprompt", "trapsasync", "typesetsilent", "typesettounset", "verbose", "vi",
    "warncreateglobal", "warnnestedvar", "xtrace", "zle"})
# The zsh options whose state, either way, changes nothing the hook reads in the Bash tool's shell, which reads its line
# with `eval` in `zsh -c`, not interactively and not from standard input: completion, history, the prompt and the line
# editor, job control, hashing and startup, which act only in an interactive shell or on what it prints (CORRECT and
# CORRECT_ALL corrected nothing in a script, probed: "command not found: lss"; INTERACTIVE_COMMENTS off still took `#` as
# a comment, and `setopt login` set nothing); AUTO_CD, which needs SHINSTDIN (tests/test_hooks_snapshots.py
# FunctionDirectoryTest), and the pushd and cd options, which change only the directory stack, a pushd with no directory,
# a `cd +N`/`-N` or a ~name, each of which the hook already reads as a directory it cannot follow, or what cd prints
# (POSIX_CD searches CDPATH before the directory, and cd_target reads both); the options under which a line runs less of
# itself than the hook reads (ERR_EXIT, NO_EXEC, RESTRICTED, NO_SHORT_LOOPS and NO_MULTI_FUNC_DEF, which make a form a parse
# error), or writes no more than it reads (NO_CLOBBER, APPEND_CREATE), or only reports (XTRACE, VERBOSE, SOURCE_TRACE,
# WARN_*); and BARE_GLOB_QUAL, which the hook's reader reads both ways already (tests/probes/shell_probe.py runs zsh -f and
# -o nobareglobqual).
ZSH_OPTIONS_INERT = frozenset({
    "alwayslastprompt", "alwaystoend", "appendhistory", "autolist", "automenu", "autoparamkeys", "autoparamslash",
    "autoremoveslash", "bashautolist", "completealiases", "completeinword", "globcomplete", "hashlistall", "listambiguous",
    "listbeep", "listpacked", "listrowsfirst", "listtypes", "menucomplete", "recexact",
    "banghist", "cshjunkiehistory", "extendedhistory", "histallowclobber", "histbeep", "histexpiredupsfirst", "histfcntllock",
    "histfindnodups", "histignorealldups", "histignoredups", "histignorespace", "histlexwords", "histnofunctions",
    "histnostore", "histreduceblanks", "histsavebycopy", "histsavenodups", "histverify", "incappendhistory",
    "incappendhistorytime", "sharehistory",
    "promptbang", "promptcr", "promptpercent", "promptsp", "promptsubst", "transientrprompt", "beep", "combiningchars",
    "dvorak", "emacs", "flowcontrol", "ignoreeof", "overstrike", "singlelinezle", "sunkeyboardhack", "vi", "zle",
    "mailwarning", "printeightbit", "printexitvalue", "rmstarsilent", "rmstarwait", "correct", "correctall",
    "autocontinue", "autoresume", "bgnice", "checkjobs", "checkrunningjobs", "hup", "longlistjobs", "monitor", "notify",
    "posixjobs",
    "autocd", "autopushd", "pushdignoredups", "pushdminus", "pushdsilent", "pushdtohome", "cdsilent", "autonamedirs",
    "posixcd",
    "hashcmds", "hashdirs", "hashexecutablesonly", "interactivecomments", "login", "interactive", "shinstdin", "privileged",
    "globalrcs", "rcs",
    "appendcreate", "clobber", "clobberempty", "errexit", "errreturn", "exec", "restricted", "singlecommand", "shortloops",
    "multifuncdef", "continueonerror", "unset", "xtrace", "verbose", "sourcetrace", "warncreateglobal", "warnnestedvar",
    "evallineno", "debugbeforecmd", "trapsasync", "pipefail", "localoptions", "localtraps", "localloops", "localpatterns",
    "typesetsilent", "kshoptionprint", "bashrematch", "rematchpcre", "bareglobqual"})
ZSH_OPTIONS_ARITH = frozenset({"cbases", "cprecedences", "octalzeroes", "forcefloat"})
ZSH_OPTIONS_CHASE = frozenset({"chaselinks", "chasedots"})
# bash's shopt names (3.2's as `shopt -p` listed them in /bin/bash 3.2.57, probed, and 4.x and 5.x's): the ones on by
# default, whose off state the hook does not model, and the ones whose state, either way, changes nothing it reads
# (interactive editing, history, completion, messages, and execfail and inherit_errexit, under which a line runs no more of
# itself).  expand_aliases is on in the shell whose snapshot this is, and on is what the hook reads the snapshot's aliases
# as: off, bash would run a word the hook reads as an alias's body as the command it spells.  Every other name is off by
# default and read "unread" when on, an unknown one among them.
BASH_SHOPT_ON = frozenset({"cmdhist", "expand_aliases", "extquote", "force_fignore", "hostcomplete", "interactive_comments",
                           "progcomp", "promptvars", "sourcepath", "checkwinsize", "complete_fullquote", "globasciiranges",
                           "globskipdots", "patsub_replacement"})
BASH_SHOPT_INERT = frozenset({
    "autocd", "cdspell", "checkhash", "checkjobs", "checkwinsize", "cmdhist", "complete_fullquote", "direxpand", "dirspell",
    "execfail", "force_fignore", "gnu_errfmt", "histappend", "histreedit", "histverify", "hostcomplete", "huponexit",
    "inherit_errexit", "interactive_comments", "lithist", "login_shell", "mailwarn", "no_empty_cmd_completion",
    "noexpand_translation", "progcomp", "progcomp_alias", "promptvars", "restricted_shell", "shift_verbose", "sourcepath",
    "varredir_close"})
# bash's `set -o` names (SPD-329), which Claude Code writes into a bash snapshot as `set -o NAME` for each line of `set -o`
# its `grep "on"` matches -- onecmd and monitor too, whose `off` holds "on" -- and which a bash snapshot's `set` lines are
# read against, never zsh's.  /bin/bash 3.2.57 listed these 27 in `bash --norc --noprofile -c 'set -o'`
# (tests/probes/shell_probe.py, 2026-09-25): allexport, braceexpand, emacs, errexit, errtrace, functrace, hashall,
# histexpand, history, ignoreeof, interactive-comments, keyword, monitor, noclobber, noexec, noglob, nolog, notify, nounset,
# onecmd, physical, pipefail, posix, privileged, verbose, vi, xtrace; on: braceexpand, hashall and interactive-comments.
# A name a later bash adds is read as one the table does not know, below.  bash takes a name only as spelled -- `set -o` refused
# `interactive_comments` and `INTERACTIVE-COMMENTS` as invalid option names -- so a name is matched as it is written.
# physical is "chase", as zsh's CHASE_LINKS.  The inert ones change nothing the hook reads in the Bash tool's shell, which
# runs its line through `eval` in `bash -c` (probed in that shape: with history, histexpand, onecmd and monitor on and
# interactive-comments off, eval's `echo "two!!" # comment` printed `two!!` and the line after it ran): line editing,
# history, job control, hashing and comments, which act in an interactive shell; errexit, errtrace, functrace, noexec,
# onecmd, pipefail, ignoreeof, under which a line runs no more of itself; noclobber, which writes no more than it reads;
# xtrace, verbose and nolog, which only report; privileged, which reads no startup file once the shell runs.  The rest
# change how the line's words are read or what its commands are handed -- braceexpand off left `{a,b}` as written, and
# allexport, keyword, noglob, nounset and posix on are read as zsh's kin are -- so they are "unread" off their default,
# and so is a name bash does not know set on (bash refuses it, but the hook does not guess why a profile wrote it).
BASH_SET_ON = frozenset({"braceexpand", "hashall", "interactive-comments"})
BASH_SET_INERT = frozenset({
    "emacs", "vi", "history", "histexpand", "monitor", "notify", "hashall", "interactive-comments", "errexit", "errtrace",
    "functrace", "noexec", "onecmd", "pipefail", "ignoreeof", "noclobber", "xtrace", "verbose", "nolog", "privileged"})
