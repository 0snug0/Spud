"""shell/syntax: Shell and git word tables, sentinels, ShellAnalysis, tokens."""

import re
import shlex

from ..core import lazy


# Shell analysis for PreToolUse(Bash).
# `<>` opens its target read-write and creates it (probed in zsh 5.9, bash 3.2 and sh: `1<>f` overwrote f from its
# start), so it is an output redirection whose operand is checked like any other target.
OUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
IN_REDIRECTS = {"<", "<<", "<<<", "<<-", "<&"}
RESERVED_WORDS = {"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "for", "select", "case", "esac",
                  "in", "function", "!", "{", "}", "coproc"}
# zsh's own reserved words, as `enable -r` listed them in zsh 5.9 -f (tests/probes/shell_probe.py, SPD-180).  Its lexer
# reads each as a token of its own wherever it is in command position, as it is after each of a for's or a foreach's names,
# so one there ends the names and opens the loop's body: `set -- p; foreach f if true; then echo x; fi; end`, `foreach f
# [[ -n x ]] && echo x; end`, `foreach f typeset -f > tf; end` and `foreach f export X=1; end` each ran as that body, and
# so did `for f if true; then echo x; fi` and `for f typeset -f > o4` (SPD-182).  `in` is not one: zsh's parser compares
# the word itself there.
ZSH_RESERVED_WORDS = {"!", "[[", "case", "coproc", "declare", "do", "done", "elif", "else", "end", "esac", "export", "fi",
                      "float", "for", "foreach", "function", "if", "integer", "local", "nocorrect", "readonly", "repeat",
                      "select", "then", "time", "typeset", "until", "while", "{", "}"}
# Matched case-folded: macOS PATH lookup is case-insensitive, so ENV runs /usr/bin/env.  noglob and nocorrect are
# zsh's precommand modifiers.
WRAPPERS = {"env", "command", "exec", "builtin", "nohup", "nice", "time", "timeout", "caffeinate", "sudo", "doas",
            "xargs", "stdbuf", "chronic", "ionice", "setsid", "unbuffer", "script", "noglob", "nocorrect"}
# The wrappers that read a NAME=value word as an assignment of their own rather than as the command they run: env,
# whose environment POSIX defines that way, and sudo, whose usage line is `sudo [VAR=value] [-i | -s] [command [arg ...]]`.
# Probed in zsh 5.9 -f, zsh -f -o nobareglobqual as the Bash tool runs it, bash 3.2 and sh, with a program in a directory
# named `x=.`: `env x=./prog status` set x and ran `status`, wherever env stood (`nice env x=./prog status` too), while
# `command`, `exec`, `nohup`, `nice`, `caffeinate`, `script`, `stdbuf`, `xargs` (with input) and zsh's `noglob` each ran
# ./prog -- an external wrapper execs its first non-option word whatever it looks like, and so do the builtins that take a
# command.  So for every wrapper outside this set the first remaining word is its command, assignment-shaped or not, and
# os.path.basename is what the hook dispatches on (`nice x=./git push` runs git push out of a directory named `x=.`).
#
# The shell's own `time` and zsh's `nocorrect` keep the command position instead, so the shell reads the assignment itself --
# but only where they stand in it: those are zsh.ZSH_COMMAND_POSITION_WORDS, which analyse_words already tracks for aliases.
# Probed: `time x=./prog status` set x and ran `status`, while `nice time x=./prog status`, `env time x=./prog status` and
# zsh's `- time x=./prog status` each ran ./prog, /usr/bin/time being an external program that execs its word.
WRAPPER_TAKES_ASSIGNMENTS = {"env", "sudo"}
# The wrappers zsh still looks a shell function up after wherever they stand, so one shadows the command word behind them:
# a function is looked up where the command word is, and these do not take the lookup away.  Probed in zsh 5.9 -f, zsh -f
# -o nobareglobqual, bash 3.2 and sh with `foo` a name no command has: after `noglob` and `exec` (its -c, -l, -a name and --
# too) a `foo() { echo SH; }` ran in zsh (`exec foo` ran the function -- `SH` and no line after it -- but skipped it in bash
# and sh, which the hook reads for too, the Bash tool being zsh), and so it did after `$W` holding either (SPD-262).  zsh's
# `nocorrect` and the `time` keyword keep it only where the command position holds, being reserved words there and a
# program's name anywhere else (`noglob nocorrect foo`: "command not found: nocorrect"; `noglob time foo` ran
# /usr/bin/time), which analyse.dispatch_words reads with zsh.ZSH_COMMAND_POSITION_WORDS; `builtin` looks its word up as a
# builtin alone, so it keeps the lookup only for one of these or zsh's `-` (`builtin noglob foo` ran the function, `builtin
# foo` "no such builtin"), and `-` keeps it, handled where `-` is read.  Every other wrapper (`command`, `env`, `nice`,
# `nohup`, `sudo`, `xargs`, ...) execs or resolves its word itself and ran the real lookup, not the function.  Spelled
# exactly: NOGLOB and Exec are no modifiers ("command not found: NOGLOB").
FUNCTION_KEEP_MODIFIERS = frozenset({"exec", "noglob"})
# Per wrapper, the options whose value is the next word unless attached (macOS and GNU spellings): a value taken for the
# command word hides the command (`timeout -s KILL 5 git push`), a command word taken for a value hides it too.
WRAPPER_VALUE_OPTIONS = {
    "env": {"-u", "-P", "-S", "-C", "-L", "-U", "--unset", "--chdir", "--split-string"},
    "exec": {"-a"},
    "nice": {"-n", "--adjustment"},
    "time": {"-o", "-f", "--output", "--format"},
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "caffeinate": {"-t", "-w"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "-a", "-c", "-R", "--user", "--group", "--close-from",
             "--chdir", "--host", "--prompt", "--role", "--type", "--other-user", "--command-timeout", "--auth-type", "--login-class", "--chroot"},
    "doas": {"-u", "-C"},
    "xargs": {"-E", "-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s", "-a", "-d", "--arg-file", "--delimiter", "--eof", "--max-lines",
              "--max-args", "--max-procs", "--max-chars", "--process-slot-var"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"},
    "script": {"-t", "-T", "-c", "-O", "-I", "-B", "-E", "-o", "-m", "--command", "--log-out", "--log-in", "--log-io", "--log-timing",
               "--echo", "--output-limit", "--logging-format"},
}
# The value options above whose value is the directory the wrapper runs its command in, which strip_wrapper hands
# analyse_words as the command's own: env's `-C` (this Mac's BSD env, probed; `--chdir` is GNU's, and BSD env refuses it
# before it runs anything, so reading it too costs nothing) and sudo's `-D`/`--chdir` (sudo(8)).  sudo's `-C` closes
# descriptors and doas's `-C` names a config file: neither moves anything.
WRAPPER_CHDIR_OPTIONS = {"env": {"-C", "--chdir"}, "sudo": {"-D", "--chdir"}}
DURATION_RE = lazy.LazyPattern(r"\d+(?:\.\d+)?[smhd]?|\.\d+[smhd]?")
# The shell's operators, longest first: shlex (punctuation_chars) returns a run of them such as `)>` or `;;&` as one token.
# `;|` is zsh's: it ends a case arm and goes on testing the patterns after it, and anywhere else it is a parse error in zsh
# (probed in zsh 5.9 -f: `echo a;| cat` failed near `;|`) and in bash, which reads `;` then `|` (SPD-181).
SHELL_OPERATORS = (";;&", "&>>", "<<<", "<<-", ";;", ";&", ";|", "&&", "||", "|&", "&>", ">>", ">|", ">&", "<&", "<>", "<<", "<(",
                   ">(", ";", "&", "|", "(", ")", "<", ">")
SHELL_PUNCTUATION = frozenset("();<>|&")
LIST_TERMINATORS = {";", ";;", ";&", ";|", ";;&"}
# What may stand between a complete header or condition and the body that follows it with no `do` or `then`: a terminator
# separates the two, and a list operator says the condition is not complete after all, so the `]]` that looked
# like its end was not (probed: `if [[ -n x ]] && [[ -n y ]] echo both` and `if true && [[ -n x ]] echo both` ran
# the body, `if [[ -n x ]] | cat` is a parse error).
BODY_DEFERRING = LIST_TERMINATORS | {"&&", "||", "|", "|&", "&"}
# Words zsh lets stand before a compound command, so `coproc repeat 1 git push` runs the loop (probed; `nocorrect`, `noglob`,
# `command` and `-` do not: zsh reports a parse error or looks for a program named repeat).
LOOP_PREFIX_WORDS = {"coproc", "time", "!"}
# The compound commands bash 4 and later run in the forked shell of a named coproc, `coproc NAME compound_command`
# (probed in bash 5.2 in the ubuntu:24.04 image: a `{ ... }` group, a `( ... )` subshell, `while`, `for`, `if`, `case` and
# `[[ ... ]]` each ran after the name, and a name that is not a valid identifier ran nothing; `coproc NAME echo x` is a
# simple command named NAME, and zsh, whose coproc takes a command only, is a parse error for every named form).
COPROC_COMPOUND_WORDS = {"{", "(", "[[", "if", "while", "until", "for", "select", "case"}
IDENTIFIER_RE = lazy.LazyPattern(r"[A-Za-z_][A-Za-z0-9_]*\Z")
LOOP_NAME_RE = lazy.LazyPattern(r"(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)\Z")  # a loop's name to zsh: an identifier or a run of digits
DIRECTORY_COMMANDS = {"cd", "chdir", "pushd", "popd"}  # the builtins, spelled exactly: CD and /usr/bin/cd are programs
SHELL_DECLARATIONS = {"export", "typeset", "declare", "local", "readonly"}
# A redirection or tee target the shell expands is checked as every file it opens, not as its literal spelling.
# neutralize_quoted_globs replaces a quoted or escaped metacharacter with a sentinel so filename generation is read only
# from the unquoted ones; deglob restores the literal character.  The sentinels are private-use characters shlex keeps in
# a word (they are not whitespace, quotes or the operator punctuation).
_GLOB_META = "*?[]{},"
_GLOB_SENTINELS = {c: chr(0xE000 + i) for i, c in enumerate(_GLOB_META)}
_GLOB_UNSENTINEL = {v: k for k, v in _GLOB_SENTINELS.items()}
# zsh's own glob operators: parenthesised alternation `(a|b)` and the numeric range `<n-m>`, which shlex reads as a
# subshell and as an input redirection.  mark_zsh_patterns replaces each character zsh reads as part of such a pattern (the
# parentheses, bars and blanks of a group, the angle brackets of a range) with one of these sentinels, so the pattern stays
# in one word; they are active glob syntax, unlike the quoted sentinels above, and deglob restores both kinds.  A group
# holds a newline as one more character of its pattern (SPD-183), so the newline has one too.
_ZSH_PATTERN_CHARS = "(|)<> \t\n"
_ZSH_SENTINELS = {c: chr(0xE010 + i) for i, c in enumerate(_ZSH_PATTERN_CHARS)}
ZSH_OPEN, ZSH_BAR, ZSH_CLOSE, ZSH_RANGE_OPEN, ZSH_RANGE_CLOSE = (_ZSH_SENTINELS[c] for c in "(|)<>")
_ZSH_UNSENTINEL = {v: k for k, v in _ZSH_SENTINELS.items()}
_LITERAL_EQUALS = chr(0xE020)  # a word's leading `=` that zsh's EQUALS is not to expand again (literalize)
# Marks neutralize_quoted_globs leaves beside a `$`, so an expansion is told from a literal dollar once shlex has taken
# the quotes away.  `$` then _LITERAL_DOLLAR: single-quoted or escaped, no expansion.  `$` then _QUOTED_DOLLAR: a `$'...'`
# (ANSI-C quoting) whose escapes zsh and bash decode apart -- prepare.ansi_c_quotes has written every other one as the literal
# it is (SPD-202) -- or `$"..."` (bash's locale string), whose text the hook does not decode.  _NAME_END: a quote or an escape
# right after `$name` ends the name (`$X"t"` is $X then t, which shlex joins as $Xt).  _ARRAY_VALUE opens the value ShellWalk
# joins for `name=(a b)`: bash reads `$name` as its first element, zsh as all of them.  _QUOTED_NAME follows the name of a
# `$name` that stands in double quotes (`"$X"`, `"git $X"`), whose value neither shell splits, where bash splits the value
# of an unquoted one at its blanks (SPD-167); it ends the name as _NAME_END does.  deglob removes all five.
_LITERAL_DOLLAR, _QUOTED_DOLLAR, _ARRAY_VALUE, _NAME_END = chr(0xE021), chr(0xE022), chr(0xE023), chr(0xE024)
_QUOTED_NAME = chr(0xE025)
# _QUOTED_SUBST follows the placeholder of a `$( )` or backtick body that stands in double quotes, whose output neither
# shell splits or globs (SPD-146: shell/loop_bindings settles a quoted `$(basename ...)` as one name).  deglob removes it.
_QUOTED_SUBST = chr(0xE027)
# What newlines_as_separators writes, between two blanks, for an unquoted newline, where it once wrote `;` (SPD-183): the
# `;` the walk reads everywhere but inside a zsh glob group, where zsh reads the newline as one more character of the
# pattern and a spelled `;` ends the word.  mark_zsh_patterns replaces every one, with `;` or with the group's newline, so
# shlex and the walk never see it and deglob has nothing to restore.
LINE_BREAK = chr(0xE026)
# The characters of an arithmetic command `(( ... ))` and of an arithmetic expansion `$(( ... ))`.  Both shells
# evaluate what stands between the parentheses as arithmetic -- the `>` of `(( n > 2 ))` is a comparison and opens no file,
# `|` is a bitwise or and not a pipeline, `;` separates a `for` header's three expressions and no commands -- so
# mark_zsh_patterns replaces every character there that shlex or the walk would read as an operator with one of these, the
# glob metacharacters with the quoted-glob sentinels above (`*` is multiplication, not filename generation) and a `$` with
# _LITERAL_DOLLAR (arithmetic names no command word, so nothing the hook dispatches on can come out of it).  Unlike the zsh
# sentinels these are inert: GLOB_RE does not read them, so a word that holds one is never expanded either.  deglob restores
# all of them, so a refusal still names the target the line spells.
_ARITH_CHARS = "()<>|&;\n \t"
_ARITH_SENTINELS = {c: chr(0xE030 + i) for i, c in enumerate(_ARITH_CHARS)}
_ARITH_UNSENTINEL = {v: k for k, v in _ARITH_SENTINELS.items()}
# A shell operator character that is quoted or escaped (`\;`, `';'`, `\(`, `"|"`) is an ordinary character of the
# word it stands in, and neutralize_quoted_globs replaces it with one of these so shlex keeps it there.  Before these, shlex
# took the quotes away and handed the walk a bare `;` or `(`, which it read as the operator: `find . -exec rm {} \; -delete`
# ended the find at `\;` and read `-delete` as a command of its own, and `find . \( -name a \) -delete` opened a subshell,
# so no primary past the first escaped operator was ever find's.  deglob restores the character, so a string a shell or
# eval reads again (`sh -c 'a; b'`, `eval echo x \; git push`) still has its operators when it is read the second time.
_PUNCT_CHARS = ";&|<>()"
_PUNCT_SENTINELS = {c: chr(0xE040 + i) for i, c in enumerate(_PUNCT_CHARS)}
_PUNCT_UNSENTINEL = {v: k for k, v in _PUNCT_SENTINELS.items()}
_SENTINEL_TEXT = dict(_GLOB_UNSENTINEL, **_ZSH_UNSENTINEL, **_ARITH_UNSENTINEL, **_PUNCT_UNSENTINEL,
                      **{_LITERAL_EQUALS: "=", _LITERAL_DOLLAR: "", _QUOTED_DOLLAR: "", _ARRAY_VALUE: "", _NAME_END: "",
                         _QUOTED_NAME: "", _QUOTED_SUBST: ""})
# The operands a line does not spell.  FIND_PATH stands where find's -exec, -execdir, -ok and -okdir put `{}`: a path
# under find's starting points, which shell/find_xargs turns into a whole-subtree write of each starting point.  INPUT_OPERAND
# stands for what xargs reads from its input -- appended after the words the line spells, or where -I or -J put it -- which
# the hook cannot know at all.  Neither is a glob, an expansion or a sentinel deglob removes, so a string a shell reads again
# (`find . -exec sh -c 'rm {}' \;`, `xargs -I% sh -c 'rm %'`) still holds it; every write channel reads a target holding one
# as a target it cannot resolve (unknown_operand), and a reason shows it as `{}` or `{input}` (shown_operands).  ANY_PATH
# stands for the files a command places where the line cannot say -- an archive extracted with -P, unzip's `-:`, a curl
# config file, tar's -T list -- which may lie anywhere at all.
FIND_PATH, INPUT_OPERAND, ANY_PATH = chr(0xE050), chr(0xE051), chr(0xE052)
# PROCSUB_MARK follows hookio.SUBST in the word that stands for the file name a `<( list )` hands its command
# (walk.PROCSUB_FILE, SPD-190): every reader of SUBST takes that word for the one it is, a word the line does not spell,
# and ShellWalk.consume, which analyses one lifted `$( )` or backtick body for each SUBST a word holds, pairs it with none.
# A private-use character, as the sentinels are: a suffix of plain text is one a line can spell after a `$( )` in the
# same word (`$(...)FILE__`), whose body would then pair with none.  deglob keeps it, so text eval or a shell reads again
# still pairs it with none, and a reason shows the word as SUBST alone (shown_operands).  0xE053 and 0xE054 are
# hooks/pathrule's.
PROCSUB_MARK = chr(0xE055)
_OPERAND_TEXT = {FIND_PATH: "{}", INPUT_OPERAND: "{input}", ANY_PATH: "(anywhere)", PROCSUB_MARK: ""}
_LITERALIZE = str.maketrans(dict(_GLOB_SENTINELS, **_ZSH_UNSENTINEL))
_GLOB_SENTINEL_RE = lazy.LazyPattern("[" + "".join(_SENTINEL_TEXT) + "]")
GLOB_RE = lazy.LazyPattern(r"[*?\[]|\{[^}]*(?:,|\.\.)[^}]*\}|[" + ZSH_OPEN + ZSH_RANGE_OPEN + "]")
ZSH_RANGE_RE = lazy.LazyPattern(r"<(\d*)-(\d*)>")  # zsh's numeric glob, read as one wherever it stands unquoted (probed)
GLOB_MATCH_CAP = 500   # the most files a redirection glob is expanded to before the hook refuses a member
GLOB_SCAN_CAP = 5000   # the most directory entries scanned expanding one glob, so `**` never walks a large tree unbounded
# An `alias` definition word, `name=body`, as it reaches the analysis with its quotes taken (`alias gp='git push'`
# is one word, `gp=git push`).  The shells take almost any name, so the name is everything before the first `=`; a bare word
# is a query, which defines nothing.
ALIAS_WORD_RE = lazy.LazyPattern(r"^([^=\s]+)=(.*)\Z", re.S)
# The key an alias's name is recorded under in `assigned` and `doubt`, so every rule that doubts a variable the line assigned
# doubts the alias too.  No variable name can hold it.
ALIAS_KEY = "\x00alias\x00"
# ShellAnalysis.body_locals's value for a name `vars` did not hold when a function body declared it local (SPD-246).
UNSET = object()
# The findings that say only that the hook cannot read a word, dropped from text the shell itself holds -- an
# alias's body or a function's, out of Claude Code's snapshot of the user's profile.  The member did not write that text,
# cannot spell it differently and cannot write the file it comes from, and Claude Code's own shadows for find, grep,
# pkill and rg each dispatch through `"$_cc_bin"`, so reading those as refusals would refuse every `grep` a member runs.
# Everything the hook *can* read there -- a git verb, a program git runs, a database call, a spud call, a file the text
# names and writes -- is the finding it would be on the line, and so is one the member's own words there earn: a word
# after the alias's name, or a call's words in a function's body (held_text.analyse_shell_text, SPD-203).  An awk program
# or sed script that is the text's own variables or substitutions ("script-word", SPD-265: `awk "$1"`, `awk "$prog"`) is
# one of these; one that is the command's input (xargs's, find's {}, standard input: "script-input", "script-option") is
# its caller's wherever the text stands, and is not.
SHELL_TEXT_TOLERATED = frozenset({"var", "var-word", "var-doubt", "glob", "alias", "eval-flag", "script-word"})
# A positional parameter, which is how a function receives the words the member wrote (`mkdir -p $@` in a body is
# the member's own path).  A write target holding one is never pruned from text the shell holds, whatever else is: `$@`,
# `$*`, `$0`..`$9` and every braced form of them (`${@}`, `${@:2}`, `${@:$#}`, `${1:-x}`, `${#@}`, `${1+"$@"}`).  `$HOME`
# and `$_cc_bin` are not matched -- a name never starts with a digit or one of those two characters.
POSITIONAL_RE = lazy.LazyPattern(r"\$(?:[0-9@*]|\{[#!]?[0-9@*][^}]*\})")
# The name a `$NAME` reads, in plain text: braced or not, after a length's `#`, bash's `!` indirection, zsh's flags in
# parentheses and its `=`, `~`, `^` and `+` (`${#V}`, `${!V}`, `${(q)V}`, `${=V}`, `${+V}`).  What a finding or a value names
# for the member-filled variables (SPD-205, SPD-258).
READ_NAME_RE = lazy.LazyPattern(r"\$\{?(?:\([^)]*\))*[#!=~^+]*([A-Za-z_][A-Za-z0-9_]*)")
# The entry ShellAnalysis.functions and .hashed hold when the line set an element of zsh's `functions` or
# `commands` parameter whose name the hook cannot read (`functions[$k]=`, `functions+=($pairs)`), so every name the hook
# reads may now be one.  No command name can hold it.
UNKNOWN_NAME = "\x00unknown\x00"
ASSIGNMENT_WORD_RE = lazy.LazyPattern(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)\Z", re.S)
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh"}
PYTHON_RE = lazy.LazyPattern(r"^python(?:\d+(?:\.\d+)?)?$")
JS_RUNTIMES = {"node", "nodejs", "bun", "deno"}
ASSIGNMENT_RE = lazy.LazyPattern(r"^[A-Za-z_][A-Za-z0-9_]*=")
VARREF_RE = lazy.LazyPattern(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})" + _QUOTED_NAME + r"?\Z")  # `$X` or `${X}`, whole
# A `$` that expands (not one neutralize_quoted_globs marked literal, and not the last character of the word).
_EXPANDING_DOLLAR_RE = lazy.LazyPattern("\\$(?!" + _LITERAL_DOLLAR + ")")  # a word-final `$` too: `$((1))` reaches a word as `$` alone
# `${X=v}`, `${X:=v}` and zsh's `${X::=v}` (flags and a subscript allowed) assign X wherever they are expanded.
_ASSIGNING_EXPANSION_RE = lazy.LazyPattern(r"\$\{(?:\([^)]*\))?[#!]?([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?:{0,2}=")
_NAME_RE = lazy.LazyPattern(r"[A-Za-z_][A-Za-z0-9_]*")
_BRACED_NAME_RE = lazy.LazyPattern(r"\{[A-Za-z_][A-Za-z0-9_]*\}")  # the `{X}` of a plain `${X}`, no operator (SPD-141)
_NAME_CHAR_RE = lazy.LazyPattern(r"[A-Za-z0-9_]")
_BARE_NAME_TAIL_RE = lazy.LazyPattern(r"\$[A-Za-z_][A-Za-z0-9_]*\Z")
_IFS_BLANKS_RE = lazy.LazyPattern(r"[ \t\n]+")
# Builtins that assign a shell variable named by an argument (`read X`, `printf -v X`, `getopts o X`, `unset X`, zsh's
# `print -v X`, `vared X`, `zparseopts -A X`, `set -A X` ...), each read by its own grammar (assigning_builtins.builtin_names)
# and every name it assigns recorded as the line's assignment of a value the hook does not know (expansions.
# read_assigning_builtin, SPD-254); `let` assigns by arithmetic (SPD-225).  `trap`, `source` and `.` run code the hook does
# not read, so after them no variable is certain.
ASSIGNING_COMMANDS = {"read", "getopts", "printf", "print", "mapfile", "readarray", "unset", "let", "wait", "vared", "zparseopts", "zstyle",
                      "zformat", "zregexparse", "strftime", "zstat", "stat", "sysread", "getln", "select", "foreach", "zle", "zcurses",
                      "zsocket", "ztcp", "zpty", "zselect", "zsystem", "private", "integer", "float", "set", "compadd", "compset"}
# Variables the shells change themselves (the last argument, the directory, a match, a reply): never certain.
DYNAMIC_VARIABLES = {"_", "PWD", "OLDPWD", "REPLY", "OPTARG", "OPTIND", "MATCH", "MBEGIN", "MEND", "match", "mbegin", "mend", "BASH_REMATCH",
                     "RANDOM", "SRANDOM", "SECONDS", "EPOCHSECONDS", "EPOCHREALTIME", "LINENO", "BASH_COMMAND", "FUNCNAME", "DIRSTACK",
                     "dirstack", "PIPESTATUS", "pipestatus", "status", "argv", "BASHPID", "COLUMNS", "LINES", "HISTCMD", "psvar", "reply"}
# The verbs Law 7 refuses a member by name, whatever git's own command list says: the ones a member would reach for, so
# the refusal keeps its own reason (`git push` is still "Spud commits" when the hook cannot run git at all).
# Every other name git answers to is refused by GIT_MEMBER_VERBS below; this table is the named half, not the whole set,
# and git_writes.git_write_option_targets reads it to skip the file target of a verb refused here -- which is why a
# verb that names a file git writes (read-tree, checkout-index, index-pack, repack, commit-graph ...) is refused by the
# allowlist instead and not added here: its GIT_VERB_FILE_OPTIONS entry stays live, and Law 7 does not bind Spud, whose
# own call is still held to the path rule.  `stage` and `init-db` are git's own spellings of `add` and `init`, which the
# table once missed (probed on git 2.54.0 (Apple Git-157): `git stage -h` prints "usage: git add [<options>]
# [--] <pathspec>..." and `git init-db -h` prints "usage: git init [-q | --quiet] [--bare] ...").
GIT_WRITE_VERBS = {"commit", "add", "stage", "checkout", "switch", "rebase", "reset", "push", "merge", "cherry-pick",
                   "pull", "am", "apply", "revert", "restore", "rm", "mv", "clean", "notes", "replace", "update-ref",
                   "symbolic-ref", "filter-branch", "gc", "prune", "submodule", "init", "init-db", "clone", "bisect",
                   "mergetool", "citool", "gui"}
# Law 7's answer for every other name.  git dispatches about 170 of them (`git --list-cmds=main`: 174 on git
# 2.54.0 (Apple Git-157)), and the table above named 31; the rest were silent for a member -- `git stage .` and
# `git init-db x` did what `git add` and `git init` do, and two dozen plumbing verbs wrote the index, the object
# database and the working tree.  So the reading is inverted: a member runs the verbs named here, git_not_allowed
# refuses every other name git knows, and a verb a later git adds is refused until someone reads it.  A name git does
# not know at all is not refused here but by the unknown-verb refusal, whose reason names the alias or external
# `git-<verb>` it must be.
#
# The sweep: every name of `git --list-cmds=main` read with `git <name> -h` and with its line in `git help -a`, whose
# groups are git's own reading of the same question ("Low-level Commands / Manipulators" against "/ Interrogators").
# A name is here when no form of it changes the repository -- the object database, the refs, the index, the config, the
# hooks -- or the working tree, and it writes no file the line does not name.  Everything else is refused, including
# every name whose read and write forms are told apart only by a subcommand or a flag (bundle, commit-graph,
# multi-pack-index, refs, rerere, sparse-checkout, hash-object, credential-store, fsck, interpret-trailers): the verb is
# what Law 7 reads, and `git stash`, `git worktree`, `git remote`, `git reflog`, `git branch`, `git tag` and
# `git config` are the seven exceptions git_refused reads by subcommand, so they are here for that reading to be
# reached.  Two names git itself groups as interrogators are refused anyway: `git for-each-repo --config=<key> --
# <arguments>` runs a git command of its own in every repository the key names, and `git unpack-file <blob>` "Creates a
# temporary file with a blob's contents" in the current directory, which no option names and the path rule cannot see.
#
# `fetch` writes -- FETCH_HEAD, the remote-tracking refs and the objects it downloads -- and is here because the ledger
# lets a member fetch, not because it only reads.  `archive`, `bugreport`, `diagnose`, `fast-export`, `format-patch`,
# `mailinfo` and `mailsplit` leave the repository alone and write only what the line names them to write, which the git
# file-write tables hold to the path rule for every caller, member and Spud alike; that is the division of labour this
# table keeps.  `git format-patch -1`, `git bugreport` and `git diagnose` with no `-o` write into the current directory
# under a name the line never spells, which GIT_VERB_CWD_WRITES reads as a file of git's naming there (SPD-093).
GIT_MEMBER_VERBS = frozenset({
    # git's read verbs.  Probed with `-h`: each takes input, revisions, pathspecs and formatting alone, and the only
    # file any of them names git to write is the diff `--output`, which GIT_FILE_OPTIONS checks on every verb.
    "status", "log", "show", "diff", "diff-files", "diff-index", "diff-tree", "diff-pairs", "grep", "blame", "annotate",
    "pickaxe", "shortlog", "describe", "range-diff", "whatchanged", "last-modified", "cherry", "count-objects",
    "merge-base", "name-rev", "rev-list", "rev-parse", "repo", "request-pull", "help", "version", "var",
    "cat-file", "ls-files", "ls-remote", "ls-tree", "for-each-ref", "show-branch", "show-index", "show-ref",
    "check-attr", "check-ignore", "check-mailmap", "check-ref-format", "column", "fmt-merge-msg",
    "get-tar-commit-id", "pack-redundant", "patch-id", "stripspace", "verify-commit", "verify-pack", "verify-tag",
    # `git annotate -h` and `git pickaxe -h` both print "usage: git blame ...": git's own spellings of blame.
    "fetch",  # writes; allowed on purpose (above)
    # Writers of named files: the repository is untouched and the file each writes is one the line names, checked by
    # the path rule.
    "archive", "bugreport", "diagnose", "fast-export", "format-patch", "mailinfo", "mailsplit",
    # The seven git_refused reads by subcommand, above.
    "branch", "config", "reflog", "remote", "stash", "tag", "worktree",
})
GIT_GLOBAL_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env", "--list-cmds"}
# Options that name a program on a verb git_refused otherwise allows (submodule, bisect and the other write verbs are already
# refused whole, so they need no entry): verb -> (long options, short-option letters).  git's parse-options accepts any
# unambiguous prefix of a long option (`--upload`, `--open`, `--to-cm`, probed) and lets short options cluster with the value
# attached (`-nO<cmd>`, probed), so a `--`-prefix of one of these long options, and any short cluster containing one of the
# letters, is refused whether or not the value is present (fail closed; an occasional refused pattern value, and an exact
# option that is also the prefix of a program-naming one -- `--to`, `--cc` -- is acceptable).  Read by
# git_verb_names_program; sampled by GLOB_SAMPLES, which is why the table lives here with the other word tables.
#
# Probed on git 2.54.0 (Apple Git-157): ls-remote/fetch --upload-pack, grep -O/--open-files-in-pager, difftool -x/--extcmd
# and archive --exec each ran the named program; `git send-email --dry-run --to-cmd=<prog>` and `--cc-cmd=<prog>`
# ran <prog>, and `git send-email -h` lists --sendmail-cmd ("Command to run to send email") and --smtp-server, which
# git-send-email(1) takes as a sendmail-like program when it is a path; `git web--browse` accepted --browser, --tool and
# --config and their spaced short forms -b, -t and -c, each naming the browser or the config key whose value git runs.
# git instaweb is not installed on this Mac (it is absent from `git --list-cmds=main`, so an unknown verb refuses it here);
# its --httpd/-d and --browser/-b come from git-instaweb(1), for a machine that has it.  `git help` names no program on the
# line -- -m/-w/-i pick man, a browser or info, whose program comes from config the allowlist already refuses, and from
# GIT_MAN_VIEWER, which GIT_PROGRAM_ENV_VARS now holds -- so it has no entry.
GIT_VERB_PROGRAM_OPTIONS = {
    "ls-remote": (("--upload-pack",), ""),
    "fetch": (("--upload-pack",), ""),
    "grep": (("--open-files-in-pager",), "O"),
    "difftool": (("--extcmd",), "x"),
    "archive": (("--exec",), ""),
    "send-email": (("--sendmail-cmd", "--smtp-server", "--to-cmd", "--cc-cmd"), ""),
    "instaweb": (("--httpd", "--browser"), "db"),
    "web--browse": (("--browser", "--tool", "--config"), "btc"),
}
# What a git call writes beside the repository -- the environment and the options that name a file or directory
# git creates or appends to, under a verb Law 7's table allows.  Each is checked with the path rule, like a redirection
# target, against every directory the shell may be in; read by git_writes.git_write_targets.
#
# The environment, probed on git 2.54.0 (Apple Git-157): every GIT_TRACE* variable set to an absolute path appended its
# trace to that file (GIT_TRACE, GIT_TRACE_PERFORMANCE, GIT_TRACE_SETUP, GIT_TRACE_PACK_ACCESS, GIT_TRACE_REFS,
# GIT_TRACE2, GIT_TRACE2_EVENT and GIT_TRACE2_PERF under a bare `git status`; the transport and packfile ones under
# fetch, ls-remote and their kin), and GIT_TRACE2 pointed at a directory wrote one file per process inside it.  The
# family is matched by its prefix, not by a list, so a sibling git adds is covered.  `0`, `1`, `2`, a small integer,
# `true`, `false` and an empty value are a descriptor or off, and a relative value only warns ("unknown trace value for
# 'GIT_TRACE'") and writes nothing, so only an absolute value (or a `~` the shell expanded before git saw it) is a file.
GIT_TRACE_VAR_PREFIX = "GIT_TRACE"
# The variables that name a path git writes whatever its shape: `GIT_INDEX_FILE=<file> git read-tree HEAD` wrote a 44 KB
# index there and `GIT_OBJECT_DIRECTORY=<dir> git hash-object -w --stdin` a loose object under it (probed), each read in
# the shape and from the base GIT_FILE_FORMS names (below): a relative GIT_INDEX_FILE landed at the work tree's top from
# a subdirectory and under -C (SPD-227, probed on git 2.54.0), and an object directory is written two levels deep.
GIT_WRITE_PATH_ENV_VARS = {"GIT_INDEX_FILE": ("file", "top"), "GIT_OBJECT_DIRECTORY": ("tree", "top")}
# `--output=<file>` is a diff option, so it is read on every verb rather than a list of them: probed opening its file
# under diff, log, show, whatchanged, format-patch, range-diff, diff-tree, diff-index, diff-files and blame.  A verb that
# does not take it errors ("unknown option") instead of writing, so reading it everywhere costs a refused path at worst.
GIT_FILE_OPTIONS = ("--output",)
# Per verb, the options that name a path git writes: (long options, short-option letters).  git's parse-options accepts
# any unambiguous prefix of a long option and lets a short option carry its value attached or clustered (`-oF`, `-so D`,
# probed), so a `--`-prefix of one of these and any cluster holding one of the letters is read as it, like a
# program-naming option -- fail closed, at the price of an occasional cluster whose letter belonged to another option's
# value.  `git init` and `git clone` make a directory wherever they are pointed too, but GIT_WRITE_VERBS refuses both to
# a member whole and Spud's own `git init` is his, so they carry no entry.
#
# The list comes from `git <verb> -h` for every verb outside GIT_WRITE_VERBS, read for an option whose own help says git
# writes what it names: archive and format-patch `-o`/`--output`/`--output-directory`, bugreport and diagnose `-o`
# ("output-directory <path>"), checkout-index `--prefix` (probed: it wrote the whole tree there), mailsplit `-o<dir>`
# ("directory in which to place the split mbox"), index-pack `-o <index-file>`, read-tree `--index-output <file>`
# ("write resulting index to <file>"), fast-export `--export-marks <file>`, commit-graph and multi-pack-index
# `--object-dir <dir>` (where git writes the graph and the index), repack `--expire-to`/`--filter-to <dir>` (packs) and
# credential-store `--file <path>` ("fetch and store credentials in <path>").  An option that only reads a file it names
# (`archive --add-file`, `grep -f`, `commit-tree -F`, `ls-files -X`) carries no entry.  Where each lands -- a file, a
# directory holding files of git's naming, a pack base name, from the directory -C names or from the work tree's top --
# is GIT_FILE_FORMS' (SPD-227; repack's two turned out to be base names, not the directories the help calls them).
# `git config --file` (SPD-227's adjacent gap: a verb the survey passed over, since its read forms name the file too) is
# read only where git_refused reads the call as a write: probed on git 2.54.0, `config -f c1 a.b c`, `--file=c2`, `config
# set --file c3`, `-fc4` and `--fil=c8` each wrote their file (under -C sub, sub/c6), while `-f c5 --get a.b` wrote none.
GIT_VERB_FILE_OPTIONS = {
    "archive": (("--output",), "o"),
    "format-patch": (("--output", "--output-directory"), "o"),
    "bugreport": (("--output-directory",), "o"),
    "diagnose": (("--output-directory",), "o"),
    "checkout-index": (("--prefix",), ""),
    "mailsplit": ((), "o"),
    "index-pack": ((), "o"),
    "read-tree": (("--index-output",), ""),
    "fast-export": (("--export-marks",), ""),
    "commit-graph": (("--object-dir",), ""),
    "multi-pack-index": (("--object-dir",), ""),
    "repack": (("--expire-to", "--filter-to"), ""),
    "credential-store": (("--file",), ""),
    "config": (("--file",), "f"),
}
# Per verb, (the subcommand the writing form takes or None, how many of its positional words name a path git writes):
# `git bundle create <file> <rev-list-args>`, `git mailinfo <msg> <patch>` and `git pack-objects <base-name>` each wrote
# what they name (probed; bundle create with `-q` and `--version=2` before the file too).
GIT_VERB_FILE_POSITIONALS = {"bundle": ("create", 1), "mailinfo": (None, 2), "pack-objects": (None, 1)}
# How git places what each of those names (SPD-227): (verb, the option's long name or short letter, or "" for the
# positional form, mailsplit's older one included) -> (shape, base), and ("file", "cwd") for every one not listed.
# Probed on git 2.54.0 (Apple Git-157) in a scratch repository in the scratchpad, from its top, from a subdirectory,
# under -C and under --git-dir.  Shapes, each read as the paths git_writes.shaped_paths makes of the value, a name git
# picks standing as hooks/pathrule.NAME_CHAR and NAME_MORE:
#   "file"    the path itself;
#   "dir"     a directory git writes files of its own naming directly into: format-patch, bugreport and diagnose -o a/b/c
#             made a/b/c and wrote 0001-second.patch, git-bugreport-<date>.txt and git-diagnostics-<date>.zip in it,
#             mailsplit -oD wrote D/0001 and D/0002, and so did its older form `mailsplit <mbox> D` (or `mailsplit D`
#             with the mailbox on standard input: GIT_VERB_CWD_WRITES' survey read "the last word of its older form");
#   "tree"    a directory git writes into two or three levels down: commit-graph --object-dir D wrote
#             D/info/commit-graph (--split: D/info/commit-graphs/...), multi-pack-index D/pack/multi-pack-index;
#   "base"    a pack base name git adds -<hash>.pack, .idx, .rev and .mtimes to: `pack-objects o1pack`, `repack
#             --filter-to=zz/pk` and `--expire-to=xx/pk` wrote zz/pk-<hash>.pack and its siblings, in no directory named so;
#   "prefix"  text put before every path of the index: checkout-index --prefix=o1/ wrote o1/f and o1/sub/g, and
#             --prefix=o1- wrote o1-f and o1-sub/g;
#   "idx"     a pack index and the reverse index beside it: index-pack -o o1.idx wrote o1.idx and o1.rev.
# Bases: "cwd", the directory git runs in -- the shell's, or the one the composed -C chain names, which every value was
# relative to in the probe but these -- and "top", the top of the work tree git finds from there, which git chdirs to and
# does not rebase these values on: from a subdirectory and under -C, fast-export --export-marks, read-tree
# --index-output, pack-objects, repack's two, checkout-index --prefix and --object-dir each wrote at the top, while under
# --git-dir with no work tree, or run inside .git, they wrote in the directory git ran in, and under --work-tree=W at W
# when git ran inside W.  So a "top" value is read at every one of those places (git_writes.top_readings).
GIT_FILE_FORMS = {
    ("format-patch", "--output-directory"): ("dir", "cwd"), ("format-patch", "o"): ("dir", "cwd"),
    ("bugreport", "--output-directory"): ("dir", "cwd"), ("bugreport", "o"): ("dir", "cwd"),
    ("diagnose", "--output-directory"): ("dir", "cwd"), ("diagnose", "o"): ("dir", "cwd"),
    ("mailsplit", "o"): ("dir", "cwd"), ("mailsplit", ""): ("dir", "cwd"),
    ("index-pack", "o"): ("idx", "cwd"),
    ("checkout-index", "--prefix"): ("prefix", "top"),
    ("read-tree", "--index-output"): ("file", "top"),
    ("fast-export", "--export-marks"): ("file", "top"),
    ("commit-graph", "--object-dir"): ("tree", "top"), ("multi-pack-index", "--object-dir"): ("tree", "top"),
    ("repack", "--expire-to"): ("base", "top"), ("repack", "--filter-to"): ("base", "top"),
    ("pack-objects", ""): ("base", "top"),
}
# The verbs git_refused reads by their operands -- a name, a key, a subcommand -- which write whatever stands before
# them, `--` included: an operand the line does not spell there is refused whole (SPD-230).  Probed on git 2.54.0 (Apple
# Git-157) in a scratch repository: `branch -- b1` and `branch --list -- b2` made b1 (and exited 0), `tag -- t1` made
# t1, `config -- a.b c` set a.b and `reflog -- expire --all` expired, while `log --oneline -- --output=zz` and `archive
# HEAD -- -o zz.tar` read what followed the `--` as paths and wrote nothing.
GIT_OPERAND_VERBS = frozenset({"branch", "tag", "config", "stash", "worktree", "remote", "reflog"})
# The verbs whose default form writes a file of git's own naming into the directory git runs in, a form the line never
# spells (SPD-093): `git format-patch -1` writes 0001-<subject>.patch there, `git bugreport` git-bugreport-<date>.txt
# (and with --diagnose git-diagnostics-<date>.zip) and `git diagnose` git-diagnostics-<date>.zip -- the shell's
# directory, or the one -C names (a repeated -C composes, an empty one changes nothing).  Their man pages say so:
# format-patch's files "are created in the current working directory" without -o, and bugreport's and diagnose's -o
# writes "instead of the current directory".  git_writes.git_cwd_write_targets reads such a call as a write of a new
# file directly in that directory, held to the path rule like a redirection target, for every caller; a directory
# the hook cannot follow refuses everyone, as any unfollowable relative write does.  -o's own directory is
# GIT_VERB_FILE_OPTIONS' reading (SPD-049) and stays so.
#
# The name is read as picked whole (hooks/pathrule.NAME_CHAR and NAME_MORE), so a caller may run the default form only
# where one of its globs covers every file directly in that directory (`out/*`, `tests/**`), never where only a narrower
# one does (`out/*.patch`).  format-patch's name comes from the commit's subject, --numbered-files, --cover-letter and a
# format.suffix set in a config file the hook does not read, so a glob matching some of its shapes would be a guess;
# bugreport's and diagnose's are settled by the line, but one reading for the three keeps the refusal one sentence long,
# and `-o <dir>` names a directory inside the deliverables for whoever needs anything narrower.  No name git picks is
# one the path rule refuses by its spelling (.git, a config file, the ledger's roots): each starts with a digit, `v`,
# `git-bugreport` or `git-diagnostics`.
#
# A suffix adds a reading of its own, in the directory git writes the files in -- -o's too -- since its value can carry
# a `/` git writes through (probed on git 2.54.0 (Apple Git-157)): bugreport and diagnose make the leading directories
# of git-bugreport-<-s>.txt and git-diagnostics-<-s>.zip, so `-s /../x` wrote ./x.txt and `-o D -s /../../x` wrote
# beside D, and strftime printed %D, %x, %Ex, %OD, %_D, %0x and %-D with two slashes (see STRFTIME_FLAT); format-patch
# appends --suffix, or format.suffix, raw after the subject, and `--suffix=/../b/x` wrote b/x through a directory named
# for the patch.  -v's reroll count is no suffix: git sanitises it (`-v ../x/y` wrote v.-x-y-0001-...).
#
# Which words are options is read as git's parse-options reads them, since a word git takes as another option's value
# sends nothing away: `git format-patch --subject-prefix --stdout -1` and `--to --stdout -1` wrote 0001-...patch here
# (probed).  format-patch parses its own options first, abbreviating none of them (`--std`, `--output-dir=D`,
# `--outp=F` and `--no-std` were each "unrecognized argument"), and hands every word it does not know, in order, to
# the revision and diff options, where `--output=F` sends every patch to F unless one of those takes it as its value
# (`--src-prefix --output=F -1` wrote 0001-...patch here and no F); bugreport and diagnose take any unambiguous
# `--` prefix of their own (`--out=D` was -o, `--no-o` set it back) and refuse any other word.  `-h` or `--help` that
# no option takes as its value printed usage and wrote nothing, wherever it stood (exit 129).  A word the hook cannot
# read, a glob or an expansion that may become an option, leaves the default reading in place wherever it stands
# before `--`.
#
# Per verb: (whether git takes a `--` prefix of the verb's own long options; its own options, each (long, short letter
# or "", kind), from `git <verb> --help-all` -- "flag" takes no separate value (an optional one is attached, `--rfc=x`),
# "value" takes the next word when it carries none, "stdout" sends every file to standard output, "dir" names the
# directory the files go in (`-o ''` and `--output-directory=` wrote here, probed), "suffix" shapes every name -- where
# `--no-<long>` sets a "stdout" or "dir" option back; the options of the later parse that send the files to one file,
# read only where the word before them cannot take them as its value; the config keys `-c` on the line may set, the
# directory (which a "dir" or a "stdout" option overrides) and the suffix, or None; the names a suffix shapes, `{pick}`
# a run git picks and `{value}` the suffix; and whether a suffix is a strftime format).
#
# Surveyed on git 2.54.0 (Apple Git-157): the man page of every verb of GIT_MEMBER_VERBS, read for "current (working)
# directory" and "temporary file".  These three write there by default and no other does: archive, fast-export,
# request-pull and shortlog write to standard output, mailinfo, bundle create and pack-objects name what they write,
# and mailsplit writes into a directory it is given (`-o<dir>`, or the last word of its older form) and into none
# without one.  Outside GIT_MEMBER_VERBS, which Law 7 refuses a member whole: clone and init make a directory here
# when they name none, and unpack-file, checkout-index --temp, mergetool and difftool create temporary files no
# option names; Spud's own calls to them are his.
GIT_VERB_CWD_WRITES = {
    "format-patch": (False, (
        ("--numbered", "n", "flag"), ("--no-numbered", "N", "flag"), ("--signoff", "s", "flag"), ("--stdout", "", "stdout"),
        ("--cover-letter", "", "flag"), ("--commit-list-format", "", "value"), ("--numbered-files", "", "flag"),
        ("--suffix", "", "suffix"), ("--start-number", "", "value"), ("--reroll-count", "v", "value"),
        ("--filename-max-length", "", "value"), ("--rfc", "", "flag"), ("--cover-from-description", "", "value"),
        ("--description-file", "", "value"), ("--subject-prefix", "", "value"), ("--output-directory", "o", "dir"),
        ("--keep-subject", "k", "flag"), ("--no-binary", "", "flag"), ("--binary", "", "flag"), ("--zero-commit", "", "flag"),
        ("--ignore-if-in-upstream", "", "flag"), ("--no-stat", "p", "flag"), ("--add-header", "", "value"),
        ("--to", "", "value"), ("--cc", "", "value"), ("--from", "", "flag"), ("--in-reply-to", "", "value"),
        ("--attach", "", "flag"), ("--inline", "", "flag"), ("--thread", "", "flag"), ("--signature", "", "value"),
        ("--base", "", "value"), ("--signature-file", "", "value"), ("--quiet", "q", "flag"), ("--progress", "", "flag"),
        ("--interdiff", "", "value"), ("--range-diff", "", "value"), ("--creation-factor", "", "value"),
        ("--force-in-body-from", "", "flag"),
    ), GIT_FILE_OPTIONS, ("format.outputdirectory", "format.suffix"), ("{pick}{value}",), False),
    "bugreport": (True, (
        ("--diagnose", "", "flag"), ("--output-directory", "o", "dir"), ("--suffix", "s", "suffix"),
    ), (), None, ("git-bugreport-{value}.txt", "git-diagnostics-{value}.zip"), True),
    "diagnose": (True, (
        ("--output-directory", "o", "dir"), ("--suffix", "s", "suffix"), ("--mode", "", "value"),
    ), (), None, ("git-diagnostics-{value}.zip",), True),
}
# The strftime conversions whose output holds no `/`, each read as one run git picks: digits, names, `-`, `:` and
# blanks.  Every other one -- %D and %x (mm/dd/yy), %c and %+, an E or O modifier, a flag or a width, a letter strftime
# does not know -- is read as a run two slashes deep, fail closed.  Probed through `git bugreport -s`: git formats in
# the C locale whatever LC_ALL says (`%c` under fr_FR.UTF-8 and ja_JP.UTF-8 printed `Wed Sep 23 21:59:28 2026`).
STRFTIME_FLAT = frozenset("aAbBCdeFgGhHIjklmMnpPrRsStTuUvVwWyYzZ")
# The commands that write the files they name as operands, which shell/arg_writes reads for bash_reason to hold
# to the path rule as it holds a redirection target.  Per command, (its shape, the short options that take a value --
# attached, or the next word -- and the GNU long options that take the next word unless `=` attaches it).  Read on this
# Mac's BSD grammar (/bin/cp, /bin/mv, /bin/ln, /bin/mkdir, /bin/rm, /bin/rmdir, /bin/chmod, /usr/bin/install,
# /usr/bin/touch, /usr/bin/truncate, /usr/sbin/chown, /usr/bin/chgrp, /usr/bin/chflags, /usr/bin/sed: their man pages,
# and probed in a scratch directory), so where BSD and GNU disagree BSD wins: cp's -S and sed's -l are flags, install's
# -T takes its mtree tags and -D its DESTDIR.  GNU's -t/--target-directory and -T/--no-target-directory are read too, where
# BSD has no such option (a BSD command refuses an option it does not know and writes nothing).  `link` is ln's and
# `unlink` rm's two-argument forms (their man pages).  Shapes: "dest" writes its last operand (or -t's directory) and, when
# that is a directory, each source inside it; "move" also removes each source; "link" writes its link name, `./<name>` for
# a single operand; "each" writes every operand; "mode" every operand after the mode, owner or flags; "sed" every file
# after the script, only in place.  mkfifo (mkfifo(1): `mkfifo [-m mode] fifo_name ...`, getopt's) makes every
# operand, "each" as mkdir's.
ARG_WRITE_COMMANDS = {
    "cp": ("dest", "t", ("--target-directory", "--suffix")),
    "install": ("dest", "BDfghlMmoTt", ("--target-directory", "--suffix", "--mode", "--owner", "--group", "--strip-program")),
    "mv": ("move", "t", ("--target-directory", "--suffix")),
    "ln": ("link", "t", ("--target-directory", "--suffix")),
    "link": ("link", "", ()),
    "mkdir": ("each", "m", ("--mode",)),
    "touch": ("each", "Adrt", ("--date", "--reference", "--time")),
    "rm": ("each", "", ()),
    "unlink": ("each", "", ()),
    "rmdir": ("each", "", ()),
    "truncate": ("each", "rs", ("--reference", "--size")),
    "chmod": ("mode", "", ("--reference",)),
    "chown": ("mode", "", ("--from", "--reference")),
    "chgrp": ("mode", "", ("--from", "--reference")),
    "chflags": ("mode", "", ()),
    "sed": ("sed", "efiI", ("--expression", "--file", "--line-length")),
    "mkfifo": ("each", "m", ("--mode",)),
}
# The options of those commands that change which files they write, so a glob word the shell may expand to one of them is
# read as it (GLOB_SAMPLES): the destination directory, install's -d (every operand a directory it makes) and -M
# (its metalog), and sed's in-place forms.
ARG_WRITE_OPTIONS = frozenset({"-t", "-T", "--target-directory", "--no-target-directory", "-d", "-M", "-i", "-I", "--in-place"})
# The commands whose files the line does not spell, which the analysis hands to shell/find_xargs (find: what it
# deletes, the files -fprint names, the command -exec runs), shell/downloads (a download named by the URL or the server:
# curl -O and -J, wget without -O) and shell/tree_writes (the rest): an archive extracted (bsdtar, this Mac's tar; unzip;
# ditto -x), a patch applied, a tree synced or copied (rsync, which is openrsync here; ditto).  Each is a whole-subtree
# write of the directory the files land in, beside the files the same command names.
TREE_WRITE_COMMANDS = frozenset({"find", "tar", "bsdtar", "unzip", "patch", "curl", "wget", "rsync", "ditto"})
# The two downloaders, which shell/downloads reads whole: the files curl and wget are told to write by name (curl's
# -o, -D, -c and kin; wget's -O, -o, -a and kin) and the ones the URL or the server names under a directory (curl -O, wget
# without -O).  wget is not installed on this Mac; its reading rests on GNU wget's manual alone.
DOWNLOAD_COMMANDS = frozenset({"curl", "wget"})
# The commands that write a file the line names past ARG_WRITE_COMMANDS, whose grammar is no shape of it, read by
# shell/spelled_writes: dd's of= operand, sort's -o and -T, mktemp's templates and split's prefix (names the command picks,
# read with hooks/pathrule.NAME_CHAR), and perl's -i, each on this Mac's man page (perl: perlrun).  perl is matched by
# PERL_RE, which takes its versioned names too.
SPELLED_WRITE_COMMANDS = frozenset({"dd", "sort", "mktemp", "split"})
PERL_RE = lazy.LazyPattern(r"^perl(?:\d+(?:\.\d+)*)?$")
# The two commands whose script is one word of the line and names files and commands of its own, which
# shell/script_text reads -- sed's `w` command and `s///w` flag, awk's `print`/`printf` redirections and pipes, its
# `system(...)` and its `"cmd" | getline`.  sed is also in ARG_WRITE_COMMANDS, which reads its -i; awk writes nothing by
# argument.  The g-prefixed GNU names are not read yet, and no interpreter is read at all.
SCRIPT_COMMANDS = frozenset({"sed", "awk"})
BRANCH_READ_FLAGS = {"-a", "-r", "-v", "-vv", "--list", "-l", "--show-current", "--all", "--remotes", "--verbose", "--color",
                     "--no-color", "--column", "--no-column", "-i", "--ignore-case", "--no-abbrev"}
BRANCH_READ_VALUE_FLAGS = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format", "--abbrev"}
TAG_READ_FLAGS = {"-l", "--list", "-n", "--column", "--no-column", "-i", "--ignore-case", "--color", "--no-color"}
TAG_READ_VALUE_FLAGS = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format"}
CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l",
                     "--show-origin", "--show-scope", "--bool", "--int", "--bool-or-int", "--path", "--expiry-date", "--null",
                     "-z", "--name-only", "--includes", "--no-includes", "--global", "--local", "--system", "--worktree"}
CONFIG_VALUE_FLAGS = {"--file", "-f", "--blob", "--type", "--default", "--comment"}
CONFIG_WRITE_FLAGS = {"--add", "--append", "--unset", "--unset-all", "--replace-all", "--rename-section", "--remove-section",
                      "--edit", "-e"}
# The flags whose positional words are their own arguments, so a second positional is not a value being written
# (`--get-urlmatch <name> <url>`, `--get-color <name> [<default>]`, `--get <name> [<value-pattern>]`).
CONFIG_READ_SELECTORS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l"}
# git 2.46 gave `git config` subcommands beside the flag syntax (git-config(1) synopsis, git 2.54.0): `git config edit`
# carries a single positional, which the positional count read as a key, so it slipped through Law 7.
CONFIG_WRITE_SUBCOMMANDS = {"set", "unset", "edit", "rename-section", "remove-section"}
CONFIG_READ_SUBCOMMANDS = {"list", "get"}


# bash's `set -P` (and +P) turns its physical option on (off), read as a line's own option builtin is (ShellAnalysis.chase)
PHYSICAL_FLAG_RE = lazy.LazyPattern(r"[-+][A-Za-z]*P[A-Za-z]*\Z")


# -- shell analysis for PreToolUse(Bash) ---------------------------------------------


class ShellAnalysis:
    """What a command line would run: (kind, detail) findings per simple command, the output redirection targets with the
    directories the shell may be in when each is opened, and whether every simple command is a spud call.

    `cwds` is the set of absolute directories the shell may be in at this point of the line, every one of them
    checked, or None when the hook cannot know it: where zsh and bash disagree, or a cd may not run or may fail, the hook
    keeps both the old directory and the new one rather than guess.

    `launcher` is the running tool's bin/spud, the file a spud call must run for the hook to allow it; `home` is
    what a SPUD_HOME assignment on the line must name."""

    def __init__(self, cwd=None, home=None, launcher=None):
        self.home = home
        self.launcher = launcher
        self.findings = []
        self.kinds = []
        # (the target word, the directories the shell may be in when it opens) per output redirection and tee operand, the
        # word with every `$NAME` the line settled resolved as arg_writes.resolved resolves one; an assignment-only
        # command whose own redirection the shells read differently records both readings.
        self.redirects = []
        # One entry per git call on the line, (its repository targets, the directories the shell may be in), so
        # bash_reason can read the config in force at each target repository's local and worktree scopes.  Not a finding:
        # every git line has one, and the findings are the refusals a line has earned.
        self.git_calls = []
        # One entry per file or directory a git call writes through an option or the environment, (the spelling
        # a reason names it by, the target word as the shell passes it to git -- a `$NAME` the line settled resolved
        # -- the directories the shell may be in when git opens it).  Checked in bash_reason with the path
        # rule, like a redirection target, for every caller.
        self.git_writes = []
        # One entry per file a command names as an operand and writes (cp, mv, ln, install, mkdir, touch, rm,
        # rmdir, truncate, chmod and its kin, sed in place), as shell/arg_writes reads it: (the command as spelled, the
        # operand word, the directories the shell may be in, the source words a destination directory takes, how the
        # word is written, a backup suffix or None, and the directory kind -- "make" for mkdir's operands and
        # install -d's without -m, -o or -g, "remove" for rmdir's and rm -d's, None for every write of a file).  bash_reason turns each into
        # the files it names and holds them to the path rule like a redirection target, for every caller.  The
        # kind is also "tree" (anything under the directory), "rm-tree" (rm -r, find -delete), "file-tree" (chmod -R, mv's
        # source) or "find-tree" (under find's starting point), which bash_rule.path_directories reads per path; how is
        # also "walk" (find's starting point, walked for a git directory only) or "itself" (GNU -T); and a destination's
        # kind is (arg_writes.RECURSIVE, rsync's excludes, whether `src/` lands as its contents) when what lands there may be
        # a whole tree.  Records come from shell/arg_writes, shell/find_xargs, shell/tree_writes, shell/downloads and
        # shell/spelled_writes, the last with hooks/pathrule.NAME_CHAR standing for each character of a name the command
        # picks (mktemp's X, split's suffix).
        self.arg_writes = []
        self.vars = {}
        # The text the line feeds the simple command being read on its standard input -- a here-string, a
        # here-document body, or what the pipeline element before it printed -- and None where the line does not spell
        # it; a stdin_text.MultiosText where zsh, which reads every input in turn, and bash read it apart (SPD-209).  A
        # shell that runs what it reads there runs that text (shell/stdin_text).  analyse_segment sets it for
        # each command and puts back what it found, so a body read in its own process reads its own input, not this one.
        self.stdin = None
        # Whether the line puts anything on that standard input at all (stdin_text.input_fed), which `stdin`
        # cannot say, being None both for text the line does not spell and for no input at all.  An interpreter with no
        # program of its own runs whatever stands there, and reads a terminal where nothing does (shell/inline_programs).
        self.stdin_fed = False
        # SPD-144: the file the simple command being read has on standard input when the line names exactly one and
        # nothing else feeds it -- its own `<` or `<>` on descriptor 0, the word as spelled -- else None
        # (analyse.stdin_file_word); an archive or a patch read from standard input is read from it (shell/tree_writes).
        self.stdin_file = None
        self.cwds = frozenset([cwd]) if cwd else None
        # What stopped the hook tokenizing text it reads for this line, or None (SPD-191): (what -- the quote character
        # that never closes, or a backslash that ends the text with nothing to escape; the text from that quote on, or up
        # to that backslash, as untokenized gives it; where -- "line" for the line itself, "nested" for a body the line
        # hands another reading, "shell" for text the Bash tool's shell already holds).  The first one found is kept, and
        # bash_rule refuses every caller a line that holds one.
        self.unparseable = None
        self.loop_depth = 0  # inside a loop or a function body, where a relative cd may repeat
        self.cd_uncertain = False  # the last directory change may not happen (a target that does not exist now)
        # how many times the reading has changed the directory of the shell it reads -- a cd, pushd or popd, a sourced
        # file, a function body's move given again, text dropped past the depth bound -- which expansions.analyse_trap
        # compares across a trap's action, to know whether the action moves the line where it runs inside it (SPD-252);
        # `moving_traps`, the action texts whose reading moved it, for a reading of one that analyse_isolated skips
        self.dir_moves, self.moving_traps = 0, set()
        self.isolated_done = set()  # (command, depth, starting state) of every body analysed in its own process
        # `doubt`, the variables whose value in `vars` the shell may not hold when a later word reads it (an assignment that
        # may not run or does not persist, or a builtin that assigns it); `sticky`, those no later assignment settles (a function
        # body's, a loop's, `${X:=v}`); `assigned`, every name assigned so far, in order; `unsure`, the simple command being read may
        # not run in the shell (after && or ||, a pipeline element, a background job); `all_doubt`, after code the hook does not read
        self.doubt, self.sticky, self.assigned = set(), set(), []
        self.unsure = 0
        self.all_doubt = False
        # SPD-225: `typed`, the names a declaration on the line gave the integer or float attribute, whose assignments the
        # shells evaluate as arithmetic and format their own way (assignment_words.typed_names); `arith_opaque`, the line ran
        # an option builtin (setopt, unsetopt, emulate, `set -o`), which may change how arithmetic reads a number (zsh's
        # FORCE_FLOAT turned `(( X = 5 ))` into 5.000000000e+00, probed), so no arithmetic literal is taken after it.
        self.typed, self.arith_opaque = set(), False
        # SPD-252: `cdable`, the line (or a function body it called, whose options stay set) ran an option builtin --
        # setopt, unsetopt, emulate, `set -o`, bash's shopt -- which may turn on zsh's CDABLE_VARS or bash's cdable_vars, so
        # a later cd into a relative name that is no directory may go where a variable of that name points
        # (directories.directory_change reads it as a directory it cannot follow).  SPD-263: a profile whose snapshot sets
        # CDABLE_VARS starts the line with it (held_options.line_options).
        self.cdable = False
        # SPD-263: `chase`, how a cd with neither -L nor -P reads a symbolic link: False, as the path spells it (zsh's and
        # bash's default); True, resolved first, the profile's snapshot setting CHASE_LINKS or CHASE_DOTS (probed: `cd lnk/..`
        # went to the parent of where lnk points); "either", after an option builtin on the line, which may have set one
        # (directories.cd_target)
        self.chase = False
        # `aliases`, what `alias NAME=body` defined on the line, name -> the body's text, None for one the hook
        # cannot read and for one `unalias` cleared; `alias_scope`, how many `eval` re-analyses deep the reading is, the only
        # place on one line where a name the line aliased is expanded (a shell expands an alias when it parses the text);
        # `alias_unknown`, the line defined an alias whose name the hook cannot read.  Each name's doubt lives in `doubt`
        # under ALIAS_KEY + name, so a definition in a branch, a subshell, a pipeline or a loop body is doubted as a
        # variable's assignment there is.
        self.aliases, self.alias_scope, self.alias_unknown = {}, 0, False
        # The shell the Bash tool starts sources Claude Code's snapshot of the user's interactive shell, so a
        # command word may already be one of that profile's aliases or functions before anything on the line runs.
        # `shell_expanded`, (the name, what the shell runs for it) per expansion on this line, in order, so a reason can
        # say what the word it names actually was; `expanding`, the alias names whose expansion is in flight, which zsh
        # does not expand again inside their own body (`alias ls='ls -G'` terminates); `bodies_read`, each function name ->
        # the (text, call's words, (standard input, starting state)) its body was read as on this line, once each however
        # often the line calls it so (held_text.read_shell_name) -- and each walk.LineBody the line defines -> the (name,
        # standard input, starting state, writes so far) it was read at (held_text.read_function, SPD-277);
        # `shell_reading`, how deep inside such text the reading is,
        # so the outermost of them prunes once, against the member's own words; `shell_words`, those words, while the
        # outermost reading is under way; `shell_kept`, the indices of the findings a function's body earned that the
        # member's words reach where the hook cannot follow them (SPD-203), which that prune keeps.
        self.shell_expanded, self.expanding, self.bodies_read = [], [], {}
        # `body_dirs`, (a function name, one of its bodies_read) -> the directories that reading left the line's shell in
        # (SPD-252), which a call reading exactly the same again is given; while the reading is under way, a mark that
        # held_text.read_shell_name reads for a call inside it.
        self.body_dirs = {}
        self.shell_reading, self.shell_words, self.shell_kept = 0, [], set()
        # `shell_own`, the ids of the entries a function body the line defines earned where the shell's own text called it
        # (held_text.read_function, SPD-277): the member's text, which that prune keeps; emptied with shell_kept.
        self.shell_own = set()
        # The command names a `hash` line put in the shell's own command table, so a later bare call of one of them
        # runs the file the line chose whatever PATH holds.  Never cleared: a `hash` in a branch, a subshell or a loop body
        # still leaves the hook unable to say which program a name finds, and the refusal is the safe answer.  zsh's
        # `commands[name]=<path>` fills the same table, and UNKNOWN_NAME stands for a name it cannot read.
        self.hashed = set()
        # The variables of a `for` or `select` loop whose every listed word starts with something other than `-`,
        # so a word such a variable fills may be read as the operand it is where a command reads options (tree_writes).
        self.dashless_loops = set()
        # The variables the call's positional parameters fill inside a shell function's body: a `for`/`select` loop over
        # a list holding `$@`/`$1`.., and a variable a value holding a positional assigns.  A finding naming one of them
        # is the member's own, so analyse_shell_text's prune keeps it rather than drop it as the body's (SPD-205).  Since
        # SPD-258 a value fills one whenever it can carry the call's words (expansions.fill_from): a substitution whose text
        # shell/positional set them in (`filled_texts`), an arithmetic expression that names one, or another variable they
        # fill.  `line_filled`, the body variables a value naming one of the line's own variables fills (SPD-253: the
        # harness's `_cc_bin="${CLAUDE_CODE_EXECPATH:-}"` after `CLAUDE_CODE_EXECPATH=<file>` on the line), which the prune
        # keeps as it keeps the line's variables themselves; `shell_line_vars`, those variables while the outermost
        # reading of the shell's text is under way.
        self.member_vars = set()
        self.filled_texts, self.line_filled, self.shell_line_vars = set(), set(), frozenset()
        # SPD-246: a function body the shell already holds (a snapshot's) runs in the line's shell, so a name it assigns is
        # still set after the call and a later text reads it -- unless the body declared it local, which is gone when it
        # returns.  `line_assigned`, every name assigned where the assignment reaches the line's shell: everything the
        # line's own text assigns (a for or select loop's variable too), and what such a body assigns to a name no body
        # open around the assignment declared local; analyse_shell_text reads it as the line's variables (SPD-205).
        # `body_locals`, None outside such a body, else one dict per body being read, innermost last: each name the body
        # surely declared local (assignment_words.local_names) -> the value `vars` held for it before, UNSET for none,
        # which held_text.read_body puts back when the body returns.  `line_members`, the member_vars no open body declared
        # local, which outlive the reading that filled them, where a body's local does not.
        self.line_assigned, self.body_locals, self.line_members = set(), None, set()
        # The names a `name () { ... }`/`function name` definition earlier on the line bound to a shell function, so
        # a later bare call of one of them (in command position) runs that function, not the program the hook read.  Kept as a
        # set like `hashed`, but scoped: a definition in a branch, a loop or another function's body may exist at the call, so
        # it is kept (refuse on doubt); one in a `( ... )` subshell does not reach a call after it, so ShellWalk restores this
        # set when the subshell frame closes (probed: `(git(){ :; }); git status` ran the real git).  zsh's `functions`
        # parameter binds the same names (`functions[git]=body`, `functions+=(git body)`), and UNKNOWN_NAME stands
        # for one whose name the hook cannot read.
        self.functions = set()
        # The bodies of those definitions, which each call reads from its own state and on its own standard input
        # (SPD-212, SPD-277: walk.read_call): `function_bodies`, each name -> the walk.LineBody objects a call of it may
        # run, every definition kept and scoped as `functions` is (zsh's `functions[name]=body` too, SPD-278);
        # `walking`, the readings of a line under way (walk_line), a body defined in one of which a call reads with its
        # input, and `walks`, the ShellWalk objects under way, innermost last; `body_walks`, the readings of a body on a
        # call's input the whole analysis has made, which positional.READINGS_PER_NAME bounds (SPD-212); `body_serial`,
        # how many bodies the analysis has numbered, the order a call reads several bodies of one name in.
        self.function_bodies, self.walking, self.walks, self.body_walks, self.body_serial = {}, set(), [], 0, 0
        # SPD-276: `hook_names`, each name a zshexit_functions, chpwd_functions or zsh_directory_name_functions array on
        # the line lists, or an action a shell runs later calls -> whether zsh may run it inside the line (UNKNOWN_NAME for
        # an element the hook cannot read), whose body is read as a trap's action is (expansions.read_deferred_body);
        # `deferring`, one entry per such action being read, whether it may run inside the line (expansions.read_action);
        # `stood`, the directories each command the reading met ran in, where a signal's action may run however briefly
        # the line stood there (expansions.TrapDirs).
        self.hook_names, self.deferring, self.stood = {}, [], set()
        # SPD-146 (shell/loop_bindings): `loop_words`, each for loop's variable whose words the line settles -> (its
        # values, the function bodies open where the loop is), until the loop closes or something assigns the name;
        # `func_depth`, how many function bodies are open; `subst_words`, each word of the simple command being read that
        # holds a lifted substitution -> its bodies in order (None where one word stands twice with other bodies);
        # `derived`, a name a certain `NAME=$(basename ...)` assigned -> (the value, what it printed); `binding`, the one reading of
        # those a write channel is resolving its words under (loop_bindings.per_reading), None everywhere else.
        self.loop_words, self.func_depth, self.subst_words, self.derived, self.binding = {}, 0, {}, {}, None
        # SPD-221 (shell/loop_bindings): `loop_derived`, a name a `NAME=$(basename ...)` certain in every pass of a for
        # loop's body assigned -> (the value, that loop's frame, the function bodies open there, the loop names it read,
        # ((their values, what it printed) per pass)); `body_loop`, the settled for loop whose body the simple command being
        # read stands in directly and runs in every pass of, None elsewhere; `unseen_assigned`, the names something
        # assigns where the walk does not read it then -- `${X:=v}`, and a function body, whenever it is called.
        self.loop_derived, self.body_loop, self.unseen_assigned = {}, None, set()

    @property
    def all_spud(self):
        """Every simple command is a spud call (a `cd` beside it changes nothing that matters)."""
        return "spud" in self.kinds and all(k in ("spud", "cd") for k in self.kinds) and not self.unparseable

    def reaching(self, names):
        """The names whose assignment here reaches the line's shell: none an open function body declared local (SPD-246)."""
        scopes = self.body_locals
        return names if scopes is None else [n for n in names if not any(n in scope for scope in scopes)]

    def fill_members(self, names):
        """Names the call's words fill inside a function's body (member_vars), each one that reaches the line's shell kept
        in line_members for a later reading (SPD-246)."""
        self.member_vars.update(names)
        self.line_members.update(self.reaching(names))

    def reading_state(self):
        """The state a reading of text starts from that decides what it finds, beside the text and its standard input: the
        directories the shell may be in, the loop and function depth a relative cd repeats in, the line's variables with
        their doubt, the aliases' scope, what shell/loop_bindings settled (SPD-146, SPD-221), and whether an option
        builtin ran (arith_opaque, cdable, chase).  analyse.analyse_isolated reads a body in its own process once per such
        state, and held_text.read_shell_name a function's body once per call from one (SPD-252); a field that changes what a
        reading finds belongs here."""
        return (self.cwds, self.loop_depth, tuple(sorted(self.vars.items())), frozenset(self.doubt), frozenset(self.sticky),
                self.all_doubt, self.alias_scope, tuple(sorted(self.loop_words.items())), tuple(sorted(self.derived.items())),
                self.func_depth, tuple(sorted(self.loop_derived.items())), self.arith_opaque, self.cdable, self.chase)


def loop_name(word, first=False):
    """Whether zsh reads this word, in a for, select or foreach header, as one of the loop's names (its parser, par_for;
    SPD-180, SPD-182): an identifier or a run of digits, which its isident takes as well (probed in zsh 5.9: `for f 1 (a b)
    echo $f$1` printed ab, and so did foreach).  The `first` word after the reserved word is read with command position
    off, so `in` and a reserved word are names there too (`for in (a b)`, `for do (a b)` and `select in (a)` ran their
    bodies); after it zsh reads each word in command position, where `in` starts a word list and a reserved word is a token
    of its own (ZSH_RESERVED_WORDS), and either ends the names, as any other word does (walk.ShellWalk.names_end)."""
    if LOOP_NAME_RE.match(word) is None:
        return False
    return first or word != "in" and word not in ZSH_RESERVED_WORDS


def unknown_operand(word):
    """True when a word holds an operand the line does not spell: find's `{}`, xargs's input, or a file a command
    places where the line cannot say."""
    return FIND_PATH in word or INPUT_OPERAND in word or ANY_PATH in word


def shown_operands(text):
    """A word or reason with the operand markers shown as the line spells them: `{}` for find's, `{input}` for xargs's;
    PROCSUB_MARK as nothing, so the word for a process substitution's file name reads as a substitution's."""
    for marker, shown in _OPERAND_TEXT.items():
        text = text.replace(marker, shown)
    return text


def shell_tokens(text):
    lx = shlex.shlex(text, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    lx.commenters = ""  # newlines_as_separators has read the comments; shlex would eat the rest of the line
    try:
        return list(lx)
    except ValueError:
        return None


UNTOKENIZED_KEPT = 160  # the most of the text untokenized keeps, from the quote on or up to the backslash


def untokenized(text):
    """What stops shell_tokens splitting this text, by shlex's own posix rules: (the quote character that never closes,
    and the text from it on), or ("\\\\", the text up to and with a backslash that ends it with nothing to escape).  A
    backslash escapes the next character outside single quotes, and inside double quotes too, where shlex keeps it; a
    backslash that ends the text inside an open quote is the quote's.  ("", the text) when neither is the cause, which
    only text shlex splits reaches."""
    i, n, quote, start = 0, len(text), None, 0
    while i < n:
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = None
        elif c == "\\":
            if i + 1 == n and quote is None:
                return "\\", text[max(0, i + 1 - UNTOKENIZED_KEPT):]
            i += 1
        elif quote == '"':
            if c == '"':
                quote = None
        elif c in "'\"":
            quote, start = c, i
        i += 1
    if quote is not None:
        return quote, text[start:start + UNTOKENIZED_KEPT]
    return "", text[:UNTOKENIZED_KEPT]


def operator_parts(token):
    """A run of shell punctuation split into the operators it holds (`)>` is `)` then `>`; `;;&` stays one)."""
    if not token or any(c not in SHELL_PUNCTUATION for c in token):
        return [token]
    parts, i = [], 0
    while i < len(token):
        op = next(o for o in SHELL_OPERATORS if token.startswith(o, i))
        parts.append(op)
        i += len(op)
    return parts


_CURRENT = object()  # analyse_segment: redirections open in the directories in force
