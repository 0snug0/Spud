"""shell/held_options: The options the shell holds, read into the state a line starts from.

A module of its own since SPD-267: line_options and option_effect taken out of shell/held_text, and the option tables they
read out of shell/syntax, which nothing else reads.  analyse.analyse_command calls line_options once, at a line's first
reading.  A leaf the shell reading's import cycle reaches: it imports hooks/snapshots and nothing of the cycle."""

import os

from ..hooks import snapshots


def line_options(a):
    """Start a line's reading from the options the shell holds (SPD-263): the snapshot's option lines run before every
    line, so CDABLE_VARS there is ShellAnalysis.cdable from the line's first word, CHASE_LINKS or CHASE_DOTS
    ShellAnalysis.chase, an option that changes arithmetic ShellAnalysis.arith_opaque; and an option the reader does not
    model, which may change how the shell reads the line's words (the option tables below), is an "unread" finding that
    names the profile's line, refused a member.  Every snapshot's options count, any of them may be the one sourced."""
    table = snapshots.shell_table(a.home)
    for kind, name, on, line, index in table.options:
        effect = option_effect(kind, name, on)
        if effect == "cdable":
            a.cdable = True
        elif effect == "chase":
            a.chase = True
        elif effect == "arith":
            a.arith_opaque = True
        elif effect == "unread":
            where = os.path.basename(table.files[index]) if 0 <= index < len(table.files) else "a shell snapshot"
            a.findings.append(("unread", ("option", "`%s` (%s)" % (line.strip(), where))))


def option_effect(kind, name, on):
    """What one option line of the snapshot does to the line the shell reads next: None, "cdable", "chase", "arith" or
    "unread", as the option tables below say (-- the options a shell snapshot sets --).  `kind` is the builtin that set it,
    "setopt" (zsh, `unsetopt` turning it off), "shopt" (bash) or "set" (`set -o`, zsh's names); a name None is a line the
    snapshot reader could not take apart."""
    if name is None:
        return "unread"
    if kind == "shopt":
        name = name.lower()
        if name == "cdable_vars":
            return "cdable" if on else None
        if name in BASH_SHOPT_INERT or on == (name in BASH_SHOPT_ON):
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
