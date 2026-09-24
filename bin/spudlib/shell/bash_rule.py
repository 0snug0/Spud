"""shell/bash_rule: bash_reason: the Bash hook's rule over a line."""

import os

from . import (analyse, arg_writes, expansions, git_config, git_verbs, prepare, redirect_globs, runner_files, script_files, spud_calls,
               syntax, tree_writes)
from ..hooks import hookio, pathrule, worktrees
from ..state import lookup


# What a refused write is called in each channel's reasons: the path rule's own reason wrapped for the write ("into"),
# a target holding an expansion the line does not settle ("variable", every caller since SPD-091), a
# target relative to a directory the hook cannot follow ("unfollowable", every caller since SPD-035), and the two a glob
# earns a member ("capped", "nomatch").  A target holding an operand the line does not spell (syntax.unknown_operand)
# earns a member "input" -- what xargs reads from its input, what find hands its command -- or "anywhere", files a command
# places where the line cannot say.
_UNFOLLOWABLE = ("a cd into a variable the line does not settle, `cd -`, popd, a directory stack entry or ~name, an option"
                 " or a CDPATH it cannot read, a relative cd in a loop, a sourced file, a cd a DEBUG, ERR or RETURN trap"
                 " makes, one into a name after setopt or shopt, text nested past the hook's depth -- on the line, or in a"
                 " shell function it calls)")
_INPUT = (" names a file xargs reads from its input (or find hands its command as {}), which the hook cannot know; spell the"
          " paths out, or give find the files to change as its own starting points")
_ANYWHERE = (" places files where the line cannot say -- an archive tar extracts with -P (it keeps absolute paths and `..`) or"
             " from a -T list, unzip's -: (it keeps `../`), a config file curl reads (-K, or a CURL_HOME, XDG_CONFIG_HOME or"
             " HOME the line sets), a -w format curl reads from a file, a {{variable}} an --expand- option of curl's holds,"
             " an -o name holding #N under curl's URL globbing, a wgetrc wget reads or runs (-e, --config, or a WGETRC or HOME"
             " the line sets), rsync's daemon, a file or a command a sed script or an awk program names as anything but one"
             " string literal standing alone (a variable, a parenthesised expression, a concatenation) or a script this Mac's"
             " sed or awk would itself refuse, or options a substitution or a variable the line cannot settle may hold (sort's"
             " -o, perl's -i and kin among them) -- so they may lie anywhere; name the files, spell a script's own file and"
             " command out as one literal, and extract or download into a directory the line names")
REDIRECT_MESSAGES = {
    "input": "the redirection target %s" + _INPUT,
    "anywhere": "the redirection target %s" + _ANYWHERE,
    "into": "a redirection or tee into %s: %s",
    "variable": "the redirection target %s holds a variable or substitution the hook cannot resolve; spell the path out",
    "unfollowable": "the redirection target %s is relative to a directory the hook cannot follow (" + _UNFOLLOWABLE + "; use an absolute path",
    "capped": ("the redirection or tee target %s is a glob whose expansion reaches the hook's match budget of %d files;"
               " write to explicit paths instead"),
    "nomatch": ("the redirection or tee target %s is a glob that matches no file now, so the hook cannot know what the shell"
                " would open (a matching file may appear before the command runs, or the shell may write the name literally);"
                " write to an explicit path"),
}
GIT_WRITE_MESSAGES = {
    "into": ("a file this git call writes (%s): %s. A git option or a GIT_TRACE* variable can name a file git creates or"
             " appends to anywhere -- `--output`, archive and format-patch `-o`, `bundle create`, GIT_TRACE2_EVENT and"
             " their kin -- under a verb Law 7's table allows; a directory an option names (format-patch, bugreport,"
             " diagnose and mailsplit `-o`, mailsplit's last word) holds files of git's own naming, and format-patch,"
             " bugreport and diagnose with no -o write such files into the directory git runs in, which a glob covers"
             " only when it covers every file directly there (name a directory with -o, or use --stdout); a relative"
             " path is read where git reads it, from the directory -C leaves it in; each goes through the path rule as a"
             " redirection does"),
    "variable": ("the file this git call writes (%s) holds a variable or substitution the hook cannot resolve, so it cannot"
                 " tell where git would write; spell the path out"),
    "unfollowable": "the file this git call writes (%s) is relative to a directory the hook cannot follow (" + _UNFOLLOWABLE + "; use an absolute path",
    "capped": ("the file this git call writes (%s) is a glob whose expansion reaches the hook's match budget of %d files;"
               " name the file explicitly"),
    "nomatch": ("the file this git call writes (%s) is a glob that matches no file now, so the hook cannot know what git"
                " would open; name the file explicitly"),
    "input": "the file this git call writes (%s)" + _INPUT,
    "anywhere": "the file this git call writes (%s)" + _ANYWHERE,
}
# a file a command names as an operand and writes (cp, mv, ln, install, mkdir, touch, rm, rmdir, truncate, chmod
# and its kin, sed in place), `%s` naming the command and the file as the line spells them.
ARG_WRITE_MESSAGES = {
    "into": "a write by argument (%s): %s",
    "variable": ("a write by argument (%s) names its file through a variable or substitution the hook cannot resolve;"
                 " spell the path out"),
    "unfollowable": "a write by argument (%s) names a file relative to a directory the hook cannot follow (" + _UNFOLLOWABLE + "; use an absolute path",
    "capped": ("a write by argument (%s) names a glob whose expansion reaches the hook's match budget of %d files;"
               " name the files explicitly"),
    "nomatch": ("a write by argument (%s) names a glob that matches no file now, so the hook cannot know what the command"
                " would write (a matching file may appear before it runs, or the shell may pass the name literally);"
                " name the files explicitly"),
    "input": "a write by argument (%s)" + _INPUT,
    "anywhere": "a write by argument (%s)" + _ANYWHERE,
    # a tree a recursive copy lands, or find hands its command, that the walk for a git directory could not read whole
    "unwalked": ("a write by argument (%s) lands a directory tree the hook could not read whole (more than %d entries, or a"
                 " directory it cannot list), so it cannot tell whether a git directory or config file lands with it, which"
                 " no spudagent writes; copy or search a smaller tree, or exclude .git"),
}
# an interpreter run whose program the line spells rather than reads from a file (shell/inline_programs), which
# is how a member in another project wrote a script past its deliverable globs: `python3.14 - <<'PY' ... p.write_text(s)`.
# Since SPD-175 only a program whose text shows a write marker (shell/program_writes), whose text the line does not
# spell, or whose family no marker is tabled for is refused.  The rule it holds a member to is no law of Spud's: it is
# the spudagent definition's (share/agents/spudagent.md), "Write only to the deliverable paths your parent planned",
# which every other channel is held to.  The way out is a channel the hook checks -- the Edit or Write tool, or the
# program's output redirected -- and no longer a program from a file, which the hook reads no better.
INLINE_PROGRAM_REASON = (
    "`%s` runs a program %s, and %s. The spudagent definition has you write only to the deliverable paths your parent"
    " planned, and every other channel is held to them (the Edit and Write hook, a redirection, a write by argument, a"
    " sed or awk script). Edit a deliverable with the Edit or Write tool, which the edit hook checks against your globs,"
    " or have the program print what it makes and redirect that (`> file`), which the path rule checks; a program the"
    " line spells whose text shows no write runs as it is; and file a proposal if the work really needs a program that"
    " writes")
INLINE_PROGRAM_STDIN = ("it reads on standard input (a here-document, a here-string, a pipe or a `<` file), having none"
                        " of its own")
INLINE_PROGRAM_WHY = {
    "writes": "its text writes (`%s`), a write the hook does not follow to a path it can check",
    "unspelled": ("the line does not spell its text (a pipe from a file or a program, a `<` file, a word the line cannot"
                  " settle), so the hook cannot read whether it writes"),
    "untabled": "the hook tables no write markers for `%s`'s language, so it cannot read whether that program writes",
}
# The reason for a word the hook cannot resolve where a command is read by name, which is read in two places:
# where the word stands on the line ("var-word", among the findings as spelled) and where an xargs reads it from an input
# the line does not spell ("inline-word", read last with the inline program it may carry, shell/interpreter_words).
VAR_WORD_REASON = ("the word %s holds a parameter expansion, arithmetic or a substitution the hook cannot resolve, or is an operand the"
                   " line does not spell at all, where the command is read by name (a wrapper's options, git's options, verb and the"
                   " arguments it checks, a shell's or an interpreter's options and program, a spud call's words, the options of a command"
                   " that writes by argument); spell the words out")
# ... and what it adds on a line that runs git, whose options a word starting with an expansion may become (SPD-089,
# SPD-171): the respellings git itself takes.  Probed on git 2.54.0: after `--` ls-remote and fetch read a word as their
# repository or a refspec and never an option (git_programs.GIT_OPTIONS_STOP_VERBS' note).
GIT_VAR_WORD_NOTE = (" (where git still reads its options, spell the option itself, or end git's options with `--` before"
                     " the word, so git reads it as a repository, a refspec or a path: `git fetch origin -- \"$B\"`,"
                     " `git ls-remote -- \"$URL\"`)")
# The reason for an awk program or a sed script the line does not settle at all (shell/script_text's "script-word" and
# "script-input", SPD-260, SPD-265), read where "inline-word" is, last, so a write out of the same input keeps its own
# reason.  A member alone.  The detail is one string, the command word and the script word as spelled.
SCRIPT_WORD_REASON = (
    "`%s` runs a program or script the line does not spell: what xargs reads from its input, after the words the"
    " line spells or where -I or -J puts it, a path find hands it as {}, a substitution or a variable the line fills from"
    " one, a file or a loop and does not settle, or standard input as its -f file -- so the hook cannot read the files it"
    " writes or the commands it runs (awk's `system()`, `print >` and `|`, sed's `w`), and a git write (Law 7), a spud"
    " call (Law 6) or a write outside your deliverables (Law 5) could hide there. Spell the program on the line as one"
    " quoted word (`awk '{print $1}' f`, `sed -n 's/a/b/p' f`), or write it to a file and name that file with -f (`awk -f"
    " prog.awk f`, `sed -f prog.sed f`), which the hook reads; hand xargs only the files")
# The reason for what xargs hands awk where it still reads its options after a -f file (shell/script_text's
# "script-option", SPD-266): one more `-f` there is a program the hook never reads.  Read with the two above; a member
# alone.  The respelling is the `--` that makes every word xargs adds a file awk reads, as GIT_INPUT_REASON's is for git.
SCRIPT_OPTION_REASON = (
    "`%s` takes words the line does not spell where awk still reads its options -- what xargs reads from its input, after"
    " the words the line spells or where -I or -J puts it -- and one more `-f other.awk` among them is a program the hook"
    " never reads, so it cannot read the files that program writes or the commands it runs, and a git write (Law 7), a"
    " spud call (Law 6) or a write outside your deliverables (Law 5) could hide there. End awk's options with `--` after"
    " its program file (`xargs awk -f prog.awk --`), so every word xargs adds is a file awk reads, or put the input after"
    " a file the line names (`xargs -I{} awk -f prog.awk f {}`)")


# The reason for a git call an xargs extends with words the line does not spell, where git reads its verb or an option
# (git_verbs.git_unspelled_word, SPD-230); a member alone, as every Law 7 fence.  The respelling is the `--` that makes
# every word xargs adds a path, in each call it makes however it splits its input.
GIT_INPUT_REASON = (
    "Law 7: the git call `%s` takes words the line does not spell -- what xargs reads from its input, after the words the"
    " line spells or where -I or -J puts it, or the path find hands its command as {} -- where git reads its verb or an"
    " option, so the hook cannot tell what git runs or writes: the verb itself, a file it writes (`-o`, `--output`,"
    " format-patch's `--no-stdout`), a program it runs (`--upload-pack`, grep's `-O`), config (`-c`) or another"
    " repository (`-C`); a name or a subcommand of branch, tag, config, stash, worktree, remote or reflog writes wherever"
    " it stands. Spell the verb, then end git's own words with `--` so every word xargs adds is a path (`xargs git log"
    " --`), give git the revisions on its standard input where it reads them there (`git log --stdin`, `git cat-file"
    " --batch`), or spell the words out; Spud commits, after the outcome is recorded")


# A git call inside a trap's action (expansions.TrapDirs, SPD-122) -- or in a function zsh runs by itself, SPD-276 --
# refused a member after every reason the words as spelled earn, so Law 7's verb check inside the action keeps its own
# reason.
TRAP_GIT_REASON = (
    "Law 7: this line runs git inside a trap's action (`trap '...' <signal>`, or a function zsh runs by itself: a"
    " TRAPxxx function, zshexit, chpwd, command_not_found_handler, or one a zshexit_functions or chpwd_functions array"
    " names), which the shell runs later -- on exit, on a signal, around a command under DEBUG, ERR, ZERR or RETURN, after"
    " a cd, as a subshell or a function ends -- in whichever directory it stands in then, so the hook cannot tell which"
    " repository that git reads, nor whose hooks and config it runs (post-index-change under `git status`). Nobody needs"
    " git in a trap: run git as its own command on the line, where the hook reads the directory it runs in; Spud commits,"
    " after the outcome is recorded")


# The reason for an (e) expansion whose text the hook cannot read (shell/reevaluation, SPD-189).
EVAL_FLAG_REASON = ("the word %s expands a value with zsh's (e) flag, which runs the command substitutions, the arithmetic and"
                    " the parameter expansions in it, and the hook cannot read the text it evaluates: a value the line does"
                    " not spell (a substitution's output, a variable it did not assign, a `$'...'` whose escapes zsh and bash"
                    " decode apart), one it may not hold there"
                    " (an assignment that may not run, a loop or function body, a builtin or a word of the same command that"
                    " assigns it, an array), or one another flag, a modifier or a subscript changes first; spell the commands out")


# SPD-217, the one fail-closed rule: text the reader did not read is refused a member rather than modelled or let pass,
# with the form named and a readable respelling.  A member alone is refused (the findings loop skips these for Spud, whom
# the laws bind where the hook cannot see, as with every Law 7 fence); each form, an "unread" finding whose detail is
# (form, shown), names why the hook could not read the text and how to spell the line so that it can.  The forms:
# "depth"    a command substitution, `eval` or here-document nested past the hook's reading bound (SPD-195), whose
#            innermost text is dropped unread.
# "braces"   a `${ }` parameter expansion nested past the bound (SPD-103), read no further.
# "subst-end" a `$( )` whose closing `)` the hook cannot place (a quoted `)`, a case pattern's, or one in a
#             here-document body inside it), so the text past its guessed end is misread (SPD-194).
# "placeholder" a word holds the hook's own substitution placeholder or an operand marker that the line did not
#             produce (SPD-199), which pairs with a lifted body that is not its own.
# "escaped-subst" a `-c` string or `eval` text holds a backslash-escaped `$( )` or backtick the shell unescapes and runs
#             but the reader read as literal (SPD-196).
# "evaluated" a value a shell evaluates as code in a form the reader does not model: a `${(P)name}` subscript, a
#             glob qualifier under GLOB_SUBST, a prompt/PROMPT/PS4 expansion, or bash arithmetic holding a substitution (SPD-197);
#             and code an assigning builtin is handed to run, `mapfile -C`'s callback or a `zstyle -e` value (SPD-254).
# "procsub-list" a process substitution in a `for`/`foreach` list or an array value, whose command the walk joins into
#             one word without reading (SPD-198).
# "assigned" an assignment whose name the reader cannot read -- an arithmetic evaluation's (`(( $N = 5 ))`) or an assigning
#             builtin's (`read $N`, `printf -v "$N"`, zsh's `unset -m`) -- so it cannot tell which of the line's variables
#             it changes (SPD-225, SPD-254); shown is (what assigns, the text).
# "option"   an option Claude Code's shell snapshot sets that the reader does not model (held_options.line_options, SPD-263),
#             which may change how the shell reads every line; shown is the snapshot's line and its file.  No spelling of
#             the line gets past it, so the respelling is the profile's.
# "function-body" a function body zsh's `functions` parameter is handed that the line does not spell -- a variable, a
#             substitution, text appended to a body (line_functions.assign_function, SPD-278); shown is the assignment.
UNREAD_REASON = (
    "the hook cannot read part of what this line runs: %s. The hook refuses a member a form it cannot read rather than"
    " guess over it, so a git write (Law 7), a spud call (Law 6) or a write outside your deliverables (Law 5) cannot hide"
    " in text it did not read; %s. Spud is not refused: the laws bind him where the hook cannot see")
UNREAD_MESSAGES = {
    "depth": ("a command substitution, `eval` or here-document nested past the %d levels the hook reads (`%s`), so the"
              " command at the bottom is text it never read",
              "run the innermost command on its own line, or store its output in a variable first (`x=$(...); ... $x`)"),
    "braces": ("a `${ }` parameter expansion nested deeper than the %d the hook reads (`%s`), read no further",
               "expand one level at a time through a variable of your own (`x=${...}; ... ${x...}`)"),
    "subst-end": ("a `$( )` whose closing `)` the hook cannot place -- a `)` in quotes, a `case` pattern or a"
                  " here-document body inside it makes its extent ambiguous (`%s`) -- so the text after it is misread",
                  "put the substitution's command in a variable on its own line (`x=$(...); ... $x`), or keep no quoted"
                  " `)`, `case` or here-document inside the `$( )`"),
    "placeholder": ("the word `%s` holds a marker the hook uses for a lifted substitution or an operand it cannot spell,"
                    " which the line did not produce, so it pairs with a substitution body that is not its own",
                    "spell the word without the marker text"),
    "escaped-subst": ("a `%s` the shell unescapes and runs, in a `-c` string or `eval` text where the hook read its"
                      " backslash as escaping it, so the substitution the shell runs is text the hook did not read",
                      "spell the command out, on the line where the hook reads it (`sh -c '...'`) or in a here-document"
                      " fed to the shell (`sh <<'EOF'` ... `EOF`)"),
    "evaluated": ("a value the shell evaluates as code in a form the hook does not read (%s): a `${(P)name}` whose"
                  " subscript runs, a glob qualifier under GLOB_SUBST, a prompt, PROMPT or PS4 expansion, bash"
                  " arithmetic holding a substitution, a `mapfile -C` callback or a `zstyle -e` value",
                  "spell the commands out on the line, and set none of these from a value the hook cannot read"),
    "assigned": ("%s assigns a variable whose name the hook cannot read (`%s`), so it cannot tell which of the line's"
                 " variables that changes, and a later word, a write target or a function the line calls may read one of"
                 " them as the value the line gave it",
                 "spell the variable's name out where it is assigned, or set the name on the line first (`N=name; read -r"
                 " $N` is read as `read -r name`)"),
    "procsub-list": ("a process substitution -- `<( )`, `>( )` or zsh's `=( )` -- in a `for` or `foreach` list or a"
                     " `name=( )` array value (`%s`), whose command the hook does not read where it joins the list into"
                     " one word",
                     "list the words without a process substitution, or read the list with `for name in <( ... )`, which"
                     " the hook reads"),
    "option": ("the shell this line runs in first runs %s, an option line in Claude Code's snapshot of your interactive"
               " shell (~/.claude/shell-snapshots/, which it sources before every Bash call), and that option may change"
               " how the shell reads a line's words or runs its commands in a way the hook does not model",
               "no spelling of the line gets past a profile's option: ask Spud to have it taken out of the shell profile;"
               " the hook reads every snapshot in that directory, so the option counts until no snapshot there sets it"),
    "function-body": ("zsh's `functions` parameter is handed a function body the line does not spell (`%s`): a variable,"
                      " a substitution, or text appended to a body, which runs when the function is called, or by itself"
                      " for a TRAPxxx, zshexit or chpwd name",
                      "define the function with name() { ... } on the line, where the hook reads its body"),
}


def unread_reason(detail):
    """The reason an "unread" finding earns a member (SPD-217): detail is (form, shown), shown a (bound, text) pair for
    "depth" and "braces" and the readable text of the form elsewhere, filled into the form's own message."""
    form, shown = detail
    what, respell = UNREAD_MESSAGES[form]
    return UNREAD_REASON % (what % shown, respell)


# The reason a line earns when the hook cannot tokenize text it reads for it (ShellAnalysis.unparseable, SPD-191): what
# stopped the reading and where it stands, then how to spell the line so that the hook can read it.
UNREADABLE_REASON = ("the hook cannot read this line: %s%s, so it cannot tell which of its words are commands, operators or"
                     " quoted text, and a line it cannot read is refused whatever it holds. zsh, which runs the Bash tool's"
                     " line, runs none of a line it cannot parse, but a line the hook misreads may be whole to zsh, and a"
                     " shell runs every complete line before an unbalanced one in `sh -c` or `bash -c` text, in bash's eval,"
                     " and in a script it reads from a here-document or a file, zsh too. Close every quote -- an apostrophe"
                     " inside single quotes is '\\'' (or put the text in double quotes), and the pid before a quote is"
                     " `${$}` -- end no line with a lone backslash, and give a long message a file of its own or a quoted"
                     " here-document (<<'EOF')")
UNREADABLE_WHERE = {
    "line": "",
    "nested": (" in text the line hands another reading (a `$( )` or backtick body, eval's words, a `-c` string, a"
               " here-document or here-string a shell reads)"),
    "shell": (" in the text an alias or function of your shell runs, read with the words after it (Claude Code's snapshot"
              " of your interactive shell, ~/.claude/shell-snapshots/); spell the command out instead of its alias"),
}
UNREADABLE_SHOWN = 40  # the most of the text from the quote, or before the backslash, a reason shows


def unreadable_reason(cause):
    """The reason for ShellAnalysis.unparseable: the quote that never closes and the text from it (an ANSI-C string's
    `$'` among them), a `$$` a quote follows that zsh and bash end apart and the text from it (SPD-202), or the backslash
    with nothing to escape and the text before it, shown as the line spells it (the hook's own marks taken off, a lifted
    body as `$(...)`, every run of blanks and newlines as one space) and cut to UNREADABLE_SHOWN characters."""
    what, text, where = cause
    shown = " ".join(shown_word(text).split())
    if what == "\\":
        shown = shown if len(shown) <= UNREADABLE_SHOWN else "..." + shown[-UNREADABLE_SHOWN:]
        stop = "the backslash that ends `%s` has nothing to escape" % shown
    elif what == "$$'":
        shown = shown if len(shown) <= UNREADABLE_SHOWN else shown[:UNREADABLE_SHOWN] + "..."
        stop = ("zsh reads the quote after the `$$` of `%s` as ANSI-C quoting (`$'...'`, where \\' ends nothing) and"
                " bash as a single quote, and the two end it apart" % shown)
    else:
        shown = shown if len(shown) <= UNREADABLE_SHOWN else shown[:UNREADABLE_SHOWN] + "..."
        stop = ("the `%s` that opens `%s` never closes" % (what, shown)) if what else "shlex cannot split `%s`" % shown
    return UNREADABLE_REASON % (stop, UNREADABLE_WHERE.get(where, ""))


def inline_program_reason(detail):
    """The refusal an interpreter run earns a member for a program the line spells: (the command word as spelled, the
    option that carries the program or None where the interpreter reads it on standard input, why, the write marker),
    as shell/inline_programs.read_inline records it."""
    cmd, option, why, marker = detail
    because = INLINE_PROGRAM_WHY[why]
    if "%s" in because:
        because %= marker if why == "writes" else cmd
    return INLINE_PROGRAM_REASON % (cmd, ("`%s` carries" % option) if option else INLINE_PROGRAM_STDIN, because)


def path_directories(kind, path):
    """The kinds the path rule reads a write of `path` with, every one of which must let it in.  A whole-subtree
    write, "tree", never loosens what the line earned before it was read as one:
    - "rm-tree" (rm -r, find -delete) is "tree" -- narrower than "remove", which rm -r was -- unless the path
      exists now as something other than a directory (a file, or a symlink, which rm removes as itself): "remove" then.
    - "file-tree" (chmod -R and its kin, mv's source) is the file the path is, and "tree" too while it is a directory now.
    - "find-tree" (a write under find's starting point) is "tree" unless the path exists as something other than a
      directory, which is then the one file find hands its command."""
    if kind not in ("rm-tree", "file-tree", "find-tree"):
        return (kind,)
    p = os.path.expanduser(path)
    directory = os.path.isdir(p) and not os.path.islink(p)
    if kind == "file-tree":
        return (None, "tree") if directory else (None,)
    if directory or not os.path.lexists(p):
        return ("tree",)
    return ("remove",) if kind == "rm-tree" else (None,)


def shown_word(detail):
    """A word the reader holds, or a finding's detail of them, as a reason shows it (SPD-200): the sentinels restored, the
    operand markers as syntax.shown_operands spells them, and a lifted `$( )` or backtick body's placeholder as `$(...)`
    -- never the reader's own marks.  The file name a `<( )` hands its command (walk.PROCSUB_FILE) shows as `$(...)` too,
    since shown_operands takes its mark off: its reason reads as the same line's with a `$( )` there does (SPD-190,
    test_hooks_words.ProcessSubstitutionFileTest).  A tuple is shown word by word; anything but text (None, a flag)
    stands as it is."""
    if isinstance(detail, tuple):
        return tuple(shown_word(d) for d in detail)
    if not isinstance(detail, str):
        return detail
    return syntax.shown_operands(prepare.deglob(detail)).replace(hookio.SUBST, "$(...)")


def shown_picked(text):
    """A reason with the characters a command picks for a name (hooks/pathrule.NAME_CHAR, NAME_MORE) shown as a glob
    shows them: `?` one, `*` any number more."""
    return text.replace(pathrule.NAME_CHAR, "?").replace(pathrule.NAME_MORE, "*")


def redirection_paths(target, cwds):
    """The paths a redirection or tee target may name, one per directory the shell may be in; None when it is relative to a
    directory the hook cannot know (`~+` is that directory; `~-` and `~name` are OLDPWD, a user or a zsh named directory)."""
    if target.startswith("~"):
        head, _, tail = target.partition("/")
        if head == "~":
            return [target]
        if head == "~+" and cwds is not None:
            return [os.path.join(c, tail) for c in sorted(cwds)]
        return None
    if os.path.isabs(target):
        return [target]
    if cwds is None:
        return None
    return [os.path.join(c, target) for c in sorted(cwds)]


def written_targets(analysis, written):
    """Every absolute path the line writes -- a redirection or tee target, a git call's own write, a write by argument
    (`written`, arg_writes.written_paths' entries) -- in its lexical and its real reading, a directory standing for all
    it holds: what shell/script_files holds an allow-listed script against, so a line that writes one never runs it.  A
    target the hook cannot resolve never reaches this for a member, whose line it has already refused."""
    entries = ([(t, c) for t, c in analysis.redirects] + [(t, c) for _n, t, c in analysis.git_writes]
               + [(t, c) for _n, t, c, _d in written])
    out = set()
    for target, cwds in entries:
        spelled = prepare.deglob(target)
        if target_has_active_glob(target):
            expansion = redirect_globs.expand_redirect_target(target, cwds)
            paths = list(expansion[0]) if expansion else []
        else:
            paths = redirection_paths(spelled, cwds) or []
        for path in paths:
            p = os.path.expanduser(path)
            out.update((os.path.normpath(p), os.path.realpath(p)))
    return sorted(out)


def writes_but(analysis, own):
    """written_targets over every write of the line but the writes by argument `own` holds, by identity: what a file a
    command reads is held against where its own writes come after it opens the file (shell/tree_writes.rewritable)."""
    mine = {id(e) for e in own}
    return written_targets(analysis, arg_writes.written_paths([e for e in analysis.arg_writes if id(e) not in mine])[0])


def target_has_active_glob(target):
    """True when a masked redirection or tee target holds an unquoted glob metacharacter the shell would expand (a quoted
    one is a sentinel, so GLOB_RE, which looks for bare `* ? [` or a brace list, does not see it)."""
    return syntax.GLOB_RE.search(target) is not None


def git_target_dirs(target, cwds):
    """(the directories a git call's repository target may name, whether the hook cannot resolve it): a path relative to a
    directory it cannot follow, `~-`/`~+`/`~name`, or a value holding an expansion is unresolvable."""
    path = prepare.deglob(target)
    if spud_calls.unresolvable_word(path):
        return [], True
    if path.startswith("~"):
        if path != "~" and not path.startswith("~/"):
            return [], True  # ~- and ~+ are OLDPWD and the current directory, ~name another user or a zsh named directory
        return [os.path.expanduser(path)], False
    if os.path.isabs(path):
        return [path], False
    if cwds is None:
        return [], True
    return [os.path.join(c, path) for c in sorted(cwds)], False


def git_repo_outside(ctx, con, target, cwds):
    """Where a git call's repository target lands: (the first directory it may name that lies outside every checkout
    the ledger knows, False), (None, True) when the hook cannot resolve it, else (None, None).  Inside is a registered
    project's root or one of the worktrees git names for it, read as the path rule reads a file's path."""
    candidates, unresolved = git_target_dirs(target, cwds)
    if unresolved:
        return None, True
    for candidate in candidates:
        if not worktrees.project_paths(ctx, con, candidate, None):
            return os.path.normpath(candidate), False
    return None, None


def bash_reason(ctx, con, caller_agent_id, caller_member, command, cwd, mode="spud"):
    """(reason or None, analysis) for a Bash command line, with what the shell expanded named after it: the
    refusal is the one the words the shell actually runs earn, and a member that typed `gc -m x` reads Law 7's words
    about `git commit` beside the alias that spelled it."""
    reason, analysis = bash_refusal(ctx, con, caller_agent_id, caller_member, command, cwd, mode)
    if reason and analysis is not None and analysis.shell_expanded:
        reason += shell_expansion_note(analysis)
    return reason, analysis


def shell_expansion_note(analysis):
    """What a reason adds when the shell this line runs in already defined one of its command words.  Claude
    Code sources its snapshot of the user's interactive shell in the shell it starts for every Bash call, so the word the
    member wrote is not the command that runs; the reason says which names it read and what each one is."""
    named, seen = [], set()
    for name, what in analysis.shell_expanded:
        if name not in seen:
            seen.add(name)
            named.append("`%s` as %s" % (name, what))
    return ("  (The shell this command runs in already defines %s, from Claude Code's snapshot of your interactive shell"
            " in ~/.claude/shell-snapshots/, which it sources for every Bash call; the hook reads what the command word"
            " runs, not what it spells.)" % ", ".join(named))


def bash_refusal(ctx, con, caller_agent_id, caller_member, command, cwd, mode="spud"):
    """(reason or None, analysis) for a Bash command line.  In a session that is not Spud (`mode` plain) a caller
    with no agent_id, or an unbound one (Eric's own subagents), keeps the database, `spud hook`, `--as spud` and member-own
    refusals and the path rule, and gets no Law 7 refusal; a bound member gets every refusal, in any session."""
    db_reason = hookio.DB_REASON
    if hookio.DB_PATH_RE.search(command):
        return db_reason % "the command names ledger.db or .spud/", None
    analysis = analyse.analyse_command(command, syntax.ShellAnalysis(cwd=cwd, home=str(ctx.home), launcher=str(ctx.launcher)))
    plain = mode == "plain"
    strict = bool(caller_agent_id) and not (plain and caller_member is None)
    who = ("%s (agent_id %s)" % (lookup.member_ref(con, caller_member["id"]), caller_agent_id)) if caller_member else ("agent_id %s" % caller_agent_id if caller_agent_id else "Spud")
    # An expansion the hook cannot resolve in a word it reads by name refuses a member last, and a verb outside git's
    # own commands or a repository it cannot read second to last, so a refusal the words as spelled already earn
    # (Law 7's verb, a program config key, an ambiguous glob, Law 6, an actor) keeps its own reason.
    for kind, detail in sorted(analysis.findings, key=lambda f: spud_calls.FINDING_LAST.get(f[0], 0)):
        if kind == "db":
            return db_reason % ("`%s`" % detail), analysis
        if kind == "spud" and detail["command"] == "hook":
            return "`spud hook` is the harness's: hooks run from .claude/settings.json (spud settings sync), never from Bash", analysis
        if not strict:
            if kind == "spud" and detail["actor"] not in (None, "spud") and (detail["command"], detail["subcommand"]) in hookio.MEMBER_OWN_COMMANDS:
                return ("Law 5: `--as %s` from Spud's own session would write a member's own sections (log, result, block, proposals) in its name;"
                        " members record themselves (the SubagentStop hold sees to it), Spud records verdicts with `spud --as spud member finish`" % shown_word(detail["actor"])), analysis
            if plain and kind == "spud" and detail["actor"] == "spud" and spud_calls.spud_call_writes(detail) and (detail["command"], detail["subcommand"]) != ("session", "claim"):
                what = " ".join(w for w in (detail["command"], detail["subcommand"]) if w)
                return ("Law 6: this session is not Spud; /spud claims it. `spud --as spud %s` writes the ledger, and outside Spud's home a session"
                        " is Spud only after `spud --as spud session claim` (the /spud skill runs it)" % what), analysis
            continue
        if kind == "git":
            verb, refused = detail
            if refused:
                return ("Law 7: spudagents never run `git %s`. A member runs git's read verbs (status, log, diff, show, blame, rev-parse,"
                        " ls-files, grep, fetch, `stash list`, `worktree list`, a `branch`/`tag` listing, `config get` ...); every other name"
                        " git answers to is Spud's -- the verbs that write the repository, the index, the object database or the working tree,"
                        " and git's own spellings and plumbing for them (commit, add, stage, checkout, switch, rebase, reset, push, merge,"
                        " cherry-pick, pull, init, init-db, read-tree, update-index, write-tree, checkout-index, hash-object, repack,"
                        " pack-refs ...); Spud commits, after the outcome is recorded" % shown_word(verb)), analysis
        elif kind == "git-clone":  # a member's clone, allowed into scratch alone (SPD-095)
            reason = git_verbs.clone_reason(ctx, con, detail)
            if reason:
                return reason, analysis
        elif kind == "git-config":
            return ("Law 7: this git call takes config the hook cannot read (%s): an alias or include defined on the line, or a variable"
                    " that injects config or points git at a config file of its own (HOME and XDG_CONFIG_HOME move git's global config to"
                    " <dir>/.gitconfig or <dir>/git/config). git expands an alias into whatever command it names before it dispatches, and"
                    " such a file can also name a program git runs, so a write can run under a verb Law 7's table does not list. Run git"
                    " with no `-c`/`--config-env` alias or include and no GIT_CONFIG_*, HOME or XDG_CONFIG_HOME variable; Spud commits,"
                    " after the outcome is recorded" % shown_word(detail)), analysis
        elif kind == "git-verb":
            how, verb = shown_word(detail)
            if how == "unreadable":
                return ("Law 7: the hook cannot read git's own command list (`git --list-cmds=main`), so it cannot tell whether `git %s` is"
                        " one of git's commands or an alias from a config file it cannot read, which git would expand into whatever command"
                        " it names before it dispatches; the hook fails closed. Spud commits, after the outcome is recorded" % verb), analysis
            return ("Law 7: `git %s` is not one of git's own commands (`git --list-cmds=main`), so it is an alias from a config file the hook"
                    " cannot read (another repository's .git/config, ~/.gitconfig, $XDG_CONFIG_HOME/git/config, the system config, an include)"
                    " or an external `git-%s` program on PATH; git expands an alias into whatever command it names before it dispatches, so a"
                    " write can run under a verb Law 7's table does not list. A member runs only git's own read verbs (status, log, diff, show,"
                    " rev-parse, ls-files ...); Spud commits, after the outcome is recorded" % (verb, verb)), analysis
        elif kind == "git-repo":
            spelled, target, target_cwds = detail
            outside, unresolved = git_repo_outside(ctx, con, target, target_cwds)
            if unresolved:
                return ("Law 7: this git call points git at a repository the hook cannot resolve (%s): a path relative to a directory it"
                        " cannot follow, or a value it cannot read. git reads that repository's .git/config before it dispatches -- aliases"
                        " and the keys that name a program it runs -- so a write can run under a verb Law 7's table does not list. Use an"
                        " absolute path inside the session's own checkout; Spud commits, after the outcome is recorded" % shown_word(spelled)), analysis
            if outside is not None:
                return ("Law 7: this git call points git at %s (%s), outside every checkout the ledger knows (a registered project's root or"
                        " one of its worktrees). git reads that repository's .git/config before it dispatches -- aliases and the keys that"
                        " name a program it runs -- and a member can write such a file under its own deliverables, so a write can run under a"
                        " verb Law 7's table does not list. Run git in the session's own checkout; Spud commits, after the outcome is"
                        " recorded" % (outside, shown_word(spelled))), analysis
        elif kind == "git-program":
            return ("Law 7: this git call runs a program git never checks (%s); git config, the environment and some options can name or"
                    " enable a program git runs -- a pager, editor, ssh or proxy command, diff or merge driver, hooks or exec path, credential"
                    " or askpass helper, the ext:: transport, --exec-path, or a verb option like --upload-pack -- under a verb Law 7's table"
                    " allows. A member may set only inert `-c`/`--config-env` keys (color.*, advice.*, i18n.*, core.quotepath, log.date,"
                    " safe.directory, and core.pager/pager.<cmd>=cat), no program-naming environment variable, and no such option; Spud"
                    " commits, after the outcome is recorded" % shown_word(detail)), analysis
        elif kind == "path":
            var, name = shown_word(detail)
            return ("Law 7: this line assigns %s and then runs `%s` by name, so the shell looks for it in a directory the"
                    " line chose and the program the hook checked is not the one that would run: a `%s` of the member's own"
                    " can do everything the name it borrows is allowed to do (zsh's `path` array is PATH under another"
                    " name). Leave PATH as the session has it, or spell the program's path out (/usr/bin/git, bin/spud),"
                    " which the shell does not search PATH for; Spud commits, after the outcome is recorded" % (var, name, name)), analysis
        elif kind == "hashed":
            return ("Law 7: this line hashes `%s` into the shell's own command table (`hash -p <path> <name>` in bash,"
                    " `hash <name>=<path>` or an element of the `commands` parameter in zsh, whose name the hook may not"
                    " be able to read), so a later bare `%s` runs that file whatever PATH holds and the program the hook"
                    " checked is not the one that would run. Hash none of the names the hook reads (git, spud, python3.14,"
                    " sqlite3, tee, a shell, a wrapper), or spell the program's path out; Spud commits, after the outcome"
                    " is recorded" % ((shown_word(detail),) * 2)), analysis
        elif kind == "function":
            return ("Law 7: this line defines a shell function `%s` (a definition, or an element of zsh's `functions`"
                    " parameter, whose name the hook may not be able to read), so a later bare `%s` runs that function and"
                    " not the program the hook checked (a shell function shadows a command of the same name in command"
                    " position). Define no function named for one of the names the hook reads (git, spud, python3.14,"
                    " sqlite3, tee, a shell, a wrapper), or reach the program past the function (`command %s`, an absolute"
                    " path); Spud commits, after the outcome is recorded" % ((shown_word(detail),) * 3)), analysis
        elif kind == "env-function":
            return ("Law 7: this line puts `%s` in a program's environment (env, sudo, export, a prefix assignment, or"
                    " bash's `export -f`), and every bash or sh started under it, however far down, imports a shell"
                    " function from a BASH_FUNC_<name>%%%% variable that runs in place of the program the hook checked --"
                    " a child the hook never sees. A member has no reason to hand a program a function: set no BASH_FUNC_*"
                    " variable and export no function; Spud commits, after the outcome is recorded" % shown_word(detail)), analysis
        elif kind == "var":
            return "the command word %s comes from a variable or a substitution the hook cannot resolve; spell the command out" % shown_word(detail), analysis
        elif kind == "var-word":
            return VAR_WORD_REASON % shown_word(detail) + (GIT_VAR_WORD_NOTE if analysis.git_calls else ""), analysis
        elif kind == "git-input":
            return GIT_INPUT_REASON % shown_word(detail), analysis
        elif kind == "eval-flag":
            return EVAL_FLAG_REASON % shown_word(detail), analysis
        elif kind == "unread":
            return unread_reason(detail), analysis
        elif kind == "var-doubt":
            return ("the variable %s may not hold the value this line assigned it (the assignment may not run or does not persist: a"
                    " condition, a compound command, a loop or function body, a pipeline, a background job, a subshell or substitution,"
                    " a command's prefix, or a builtin that assigns it), so the hook cannot resolve the words it becomes; spell them out"
                    % shown_word(detail)), analysis
        elif kind == "alias":
            return ("`eval` runs the command word %s, which this line defines as an alias the hook cannot resolve (an `alias`"
                    " line, or an element of zsh's `aliases` parameter): its name or its body holds an expansion or a"
                    " substitution, or the definition or an `unalias` may not have run (a branch, a subshell, a"
                    " pipeline, a background list, a loop or function body, a reading only one shell makes). A shell expands an"
                    " alias when it parses the text, so the command that runs is not the one written; spell the command out, or"
                    " define no alias on the line" % shown_word(detail)), analysis
        elif kind == "shell-alias":
            return ("the command word %s is an alias your shell already defines whose body the hook cannot read (its quoting"
                    " does not close, or holds a `$'...'` escape zsh and bash decode apart, in Claude Code's snapshot of your"
                    " interactive shell, ~/.claude/shell-snapshots/). A shell"
                    " expands an alias when it parses the line, so the command that runs is not the one written; spell the"
                    " command out" % shown_word(detail)), analysis
        elif kind == "glob":
            return ("the word %s is a glob the shell expands before it runs the command, and it can become more than one command,"
                    " option or verb the hook checks at once, or more than the hook reads; spell the words out" % shown_word(detail)), analysis
        elif kind == "spud":
            call = detail
            if call["actor"] == "spud":
                return "Law 6: `--as spud` is Spud's; a spudagent acts as itself (--as %s)" % (caller_agent_id,), analysis
            if call["command"] in hookio.SPUD_ONLY_COMMANDS or (call["command"], call["subcommand"]) in hookio.SPUD_ONLY_SUBCOMMANDS:
                what = call["command"] + ((" " + call["subcommand"]) if call["subcommand"] and (call["command"], call["subcommand"]) in hookio.SPUD_ONLY_SUBCOMMANDS else "")
                return ("Law 6: `spud %s` is Spud's (tickets are created, moved and edited by Spud alone; init, migrate, import, render, backup,"
                        " settings sync, config sync and member resum are ledger-wide, schedule installs the ledger's daily backup and its render"
                        " watcher on this Mac, and home move moves the home);"
                        " file a proposal instead: spud proposal file --as %s" % (what, caller_agent_id)), analysis
            if call["actor"] is not None:
                if caller_member is None:
                    return ("your agent_id %s is not bound to a member yet, so `--as %s` cannot be verified (a background spawn is bound right"
                            " after launch by PostToolUse(Agent); a foreground spawn only at its stop)" % (caller_agent_id, shown_word(call["actor"]))), analysis
                if not spud_calls.actor_is_self(con, call["actor"], caller_member, caller_agent_id):
                    return ("`--as %s` does not resolve to the caller's own member %s; use `--as %s`"
                            % (shown_word(call["actor"]), who, caller_agent_id)), analysis
    if strict:
        # The repository each git call reads must be a known checkout's own, and the config it sets for
        # itself, which git reads with nothing on the line, is held to the program allowlist; and it holds no hook
        # a member planted.  After the findings, so a refusal the words as spelled already earn (a write verb, a program
        # key, an unknown verb, a repository outside every known checkout) keeps its own reason.
        # A git call inside a trap's action runs later, in whichever directory the line stands in then (SPD-122): refused
        # outright, since nobody needs git in a trap.
        if any(isinstance(target_cwds, expansions.TrapDirs) for _targets, target_cwds in analysis.git_calls):
            return TRAP_GIT_REASON, analysis
        for targets, target_cwds in analysis.git_calls:
            reason = git_config.git_repository_reason(ctx, con, targets, target_cwds)
            if reason:
                return reason, analysis
    elif not caller_agent_id and not plain:
        # Spud's own git call, which runs as Eric's, reads the same repository for what a member could have
        # planted there through a program the hook cannot read.  A plain session's calls and its own subagents never reach
        # this: they keep the answers they had.  One inside a trap's action is read in every directory it may run in.
        for targets, target_cwds in analysis.git_calls:
            if isinstance(target_cwds, expansions.TrapDirs):
                target_cwds = target_cwds.dirs()
            reason = git_config.git_spud_repository_reason(ctx, con, targets, target_cwds)
            if reason:
                return reason, analysis

    def target_reason(messages, spelled, path, directory=None):
        """edit_reason for one concrete file a redirection, a tee, a git call or a write by argument may open, phrased for
        the write.  `directory` is the kind only a write by argument carries: the making or removal of a directory,
        the whole-subtree writes, each read with the kinds path_directories gives for this path."""
        for kind in path_directories(directory, path):
            reason, rel = pathrule.edit_reason(ctx, con, caller_agent_id, caller_member, path, cwd, mode, kind)
            if reason:
                break
        if not reason:
            return None
        # the state directory is refused in the database's words, not Law 1's; a session that is not Spud is not held to Law 1
        law_1 = not caller_agent_id and not plain and not (rel is not None and pathrule.in_state_dir(rel))
        return shown_picked(("Law 1: " if law_1 else "") + messages["into"] % (spelled, reason))

    def targets_reason(entries, messages):
        """The reason one of these writes is refused, or None.  An entry is (the spelling the reason names it by, or None
        for the target's own, the target word as the shell passes it -- a `$NAME` the line settled already resolved where
        the analysis recorded it, everything else as the line spells it -- the directories the shell may be in
        when it opens, and the directory kind, None for every write that is not a directory's making or removal)."""
        for named, target, target_cwds, directory in entries:
            spelled = shown_picked(shown_word(named if named else target))
            if syntax.unknown_operand(target):  # an operand the line does not spell (xargs's input, find's {}, anywhere)
                if strict:
                    return messages["anywhere" if syntax.ANY_PATH in target else "input"] % spelled
                continue
            if "$" in target or "`" in target or hookio.SUBST in target:  # an expansion the line does not settle: refused for Spud too
                return messages["variable"] % spelled
            if target_has_active_glob(target):
                # The shell expands the target before opening it: check every file it opens from every candidate
                # directory, not the literal spelling that maps under no root.
                expansion = redirect_globs.expand_redirect_target(target, target_cwds)
                if expansion is None:  # a directory the hook cannot follow: refused for Spud too
                    return messages["unfollowable"] % spelled
                matches, capped = expansion
                for path in matches:
                    reason = target_reason(messages, spelled, path, directory)
                    if reason:
                        return reason
                if strict:  # a member: the hook cannot know what the glob opens beyond what it matches now
                    if capped:
                        return messages["capped"] % (spelled, syntax.GLOB_MATCH_CAP)
                    if not matches:
                        return messages["nomatch"] % spelled
                else:  # Spud: also the literal name a shell writes when a glob matches nothing
                    for path in redirection_paths(prepare.deglob(target), target_cwds) or []:
                        reason = target_reason(messages, spelled, path, directory)
                        if reason:
                            return reason
                continue
            paths = redirection_paths(prepare.deglob(target), target_cwds)
            if paths is None:  # a directory the hook cannot follow: refused for Spud too
                return messages["unfollowable"] % spelled
            for path in paths:  # every directory the shell may be in
                reason = target_reason(messages, spelled, path, directory)
                if reason:
                    return reason
        return None

    # The redirections first, so a line that already earned a redirection's reason keeps it; then the files a git call
    # writes through its own options or the environment, and the files a command names as operands and writes
    # by argument, which are held to the same rule.
    # For a caller the path rule holds, each tree a recursive copy lands or find hands its command is walked too.
    written, capped, unwalked = arg_writes.written_paths(analysis.arg_writes, walk_trees=strict)
    for entries, messages in (([(None, t, c, None) for t, c in analysis.redirects], REDIRECT_MESSAGES),
                              ([(n, t, c, None) for n, t, c in analysis.git_writes], GIT_WRITE_MESSAGES),
                              (written, ARG_WRITE_MESSAGES)):
        reason = targets_reason(entries, messages)
        if reason:
            return reason, analysis
    if strict and capped:  # a source glob whose files, each written into a directory, reach the budget
        return ARG_WRITE_MESSAGES["capped"] % (capped, syntax.GLOB_MATCH_CAP), analysis
    if strict and unwalked:  # a tree whose walk for a git directory stopped short
        return ARG_WRITE_MESSAGES["unwalked"] % (unwalked, syntax.GLOB_SCAN_CAP), analysis
    if strict:
        # last of all, where the findings FINDING_LAST holds back stand: an inline program says only that its text
        # writes somewhere the hook does not follow, or that the hook cannot read it, so every refusal the line has already earned keeps its own reason -- a git verb, a
        # database call, a spud call, a program git would run, and each write the path rule refuses above, which is
        # what the readings of perl and sed as writers still answer with.
        # A shell whose commands come from a file (shell/script_files) is read here too, for the same reason; an
        # allow-listed repository script is let through only where the line writes none of it first.
        # A script runner (shell/script_runners, shell/runner_files) the same way: a name its project allows, from a file
        # the line writes none of, and no shell of the line's own.
        allow, line_writes, runner_cache = [], None, {}
        for kind, detail in analysis.findings:
            if kind == "script":
                if line_writes is None:
                    line_writes = written_targets(analysis, written)
                reason = script_files.script_reason(ctx, con, caller_agent_id, caller_member, cwd, mode, detail, line_writes, allow)
                if reason:
                    return reason, analysis
                continue
            if kind == "runner":
                if line_writes is None:
                    line_writes = written_targets(analysis, written)
                reason = runner_files.runner_reason(ctx, con, caller_agent_id, caller_member, cwd, mode, detail, line_writes, runner_cache)
                if reason:
                    return reason, analysis
                continue
            if kind == "archive":
                # an archive or a patch whose names the hook cannot list, or one a write of the line may reach before the
                # command reads it (shell/tree_writes, SPD-144, SPD-275): after every write the path rule refuses above,
                # so the directory it extracts into, and each name it did list, keep their own reasons
                reason = tree_writes.archive_reason(detail, writes_but(analysis, detail[4]) if detail[0] == "rewritable" else ())
                if reason:
                    return reason, analysis
                continue
            if kind == "inline":
                return inline_program_reason(shown_word(detail)), analysis
            if kind == "inline-word":
                # an interpreter's option position the hook cannot read at all, because an xargs reads it from an
                # input the line does not spell (`cat f | xargs node`, where an `-e` may stand).  Read here rather than
                # among the findings as spelled, for the same reason an inline program is: what the same input writes
                # (`xargs perl -pi -e s/a/b/ < list`) keeps its own reason.
                return VAR_WORD_REASON % shown_word(detail), analysis
            if kind in ("script-word", "script-input"):
                # an awk program or a sed script the line does not settle at all (shell/script_text, SPD-260), read here
                # for the same reason: what the same input writes (`xargs sed -n < list`, whose input may be -i) keeps
                # its own reason.
                return SCRIPT_WORD_REASON % shown_word(detail), analysis
            if kind == "script-option":
                # what xargs hands awk where it still reads its options after a -f file (SPD-266), the same way: a write
                # the file itself makes keeps its own reason.
                return SCRIPT_OPTION_REASON % shown_word(detail), analysis
    if analysis.unparseable:
        # Last of all, so a refusal the words the hook did read already earn keeps its own reason (a Law 7 verb before a
        # stray quote in an eval string), and then for every caller: a bound member, Spud, a plain session and its
        # subagents (SPD-191).  Before, this line passed every law.  Probed through tests/probes/shell_probe.py (zsh 5.9, bash
        # 3.2.57; UnreadableLineTest): the Bash tool runs the line as `zsh -c '... && eval <line>'`, and zsh's eval parses
        # all of its text before it runs any, so a line whose quote truly never closes runs nothing and refusing it costs
        # nothing; but a line the hook misreads may be whole to zsh (`$'\''` before SPD-202, a quoted `)` in a `$( )`, a
        # lone backslash at the end all wrote files), and a shell runs every complete line before an unbalanced one in
        # `sh -c` and `bash -c` text, in bash's eval, and in a script it reads from a here-document or a file, zsh
        # too -- only zsh's eval and `zsh -c` parse their whole text first.  Spud is refused always, not only where
        # the text holds something his laws cover: which words of it are commands, targets or quoted text is exactly
        # what the hook could not read, so a scan for a write, a tee, a spud or sqlite3 call, a git call or a generated
        # path would guess over the same text and match nearly every line he runs anyway.
        return unreadable_reason(analysis.unparseable), analysis
    return None, analysis
