"""shell/syntax: Shell and git word tables, sentinels, ShellAnalysis, tokens."""

import re
import shlex


# Shell analysis for PreToolUse(Bash).
# `<>` opens its target read-write and creates it (probed in zsh 5.9, bash 3.2 and sh: `1<>f` overwrote f from its
# start), so it is an output redirection whose operand is checked like any other target.
OUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
IN_REDIRECTS = {"<", "<<", "<<<", "<<-", "<&"}
RESERVED_WORDS = {"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "for", "select", "case", "esac",
                  "in", "function", "!", "{", "}", "coproc"}
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
# The wrappers zsh still looks a shell function up after, so one shadows the command word behind them: a function
# is looked up in command position, and these do not take it away.  Probed in zsh 5.9 -f, zsh -f -o nobareglobqual, bash 3.2
# and sh with `foo` a name no command has: after `noglob`, `nocorrect` and `exec` a `foo() { echo SH; }` ran (zsh only for
# noglob/nocorrect; `exec foo` ran the function in zsh -- `SH` and no line after it -- but skipped it in bash and sh, which
# the hook refuses for, the Bash tool being zsh), and `time foo` ran it in all four (`time` is a keyword, /usr/bin/time is
# not a wrapper here).  Every other wrapper (`command`, `builtin`, `env`, `nice`, `nohup`, `sudo`, `xargs`, ...) execs or
# resolves its word itself and ran the real lookup, not the function.  zsh's `-` precommand modifier keeps it too, but is
# handled where `-` is read, not as a wrapper.
FUNCTION_KEEP_WRAPPERS = {"exec", "noglob", "nocorrect", "time"}
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
DURATION_RE = re.compile(r"\d+(?:\.\d+)?[smhd]?|\.\d+[smhd]?")
# The shell's operators, longest first: shlex (punctuation_chars) returns a run of them such as `)>` or `;;&` as one token.
SHELL_OPERATORS = (";;&", "&>>", "<<<", "<<-", ";;", ";&", "&&", "||", "|&", "&>", ">>", ">|", ">&", "<&", "<>", "<<", "<(", ">(",
                   ";", "&", "|", "(", ")", "<", ">")
SHELL_PUNCTUATION = frozenset("();<>|&")
LIST_TERMINATORS = {";", ";;", ";&", ";;&"}
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
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
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
# in one word; they are active glob syntax, unlike the quoted sentinels above, and deglob restores both kinds.
_ZSH_PATTERN_CHARS = "(|)<> \t"
_ZSH_SENTINELS = {c: chr(0xE010 + i) for i, c in enumerate(_ZSH_PATTERN_CHARS)}
ZSH_OPEN, ZSH_BAR, ZSH_CLOSE, ZSH_RANGE_OPEN, ZSH_RANGE_CLOSE = (_ZSH_SENTINELS[c] for c in "(|)<>")
_ZSH_UNSENTINEL = {v: k for k, v in _ZSH_SENTINELS.items()}
_LITERAL_EQUALS = chr(0xE020)  # a word's leading `=` that zsh's EQUALS is not to expand again (literalize)
# Marks neutralize_quoted_globs leaves beside a `$`, so an expansion is told from a literal dollar once shlex has taken
# the quotes away.  `$` then _LITERAL_DOLLAR: single-quoted or escaped, no expansion.  `$` then _QUOTED_DOLLAR: `$'...'` (ANSI-C
# quoting, both shells) or `$"..."` (bash's locale string), whose text the hook does not decode.  _NAME_END: a quote or an escape
# right after `$name` ends the name (`$X"t"` is $X then t, which shlex joins as $Xt).  _ARRAY_VALUE opens the value ShellWalk
# joins for `name=(a b)`: bash reads `$name` as its first element, zsh as all of them.  deglob removes all four.
_LITERAL_DOLLAR, _QUOTED_DOLLAR, _ARRAY_VALUE, _NAME_END = chr(0xE021), chr(0xE022), chr(0xE023), chr(0xE024)
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
                      **{_LITERAL_EQUALS: "=", _LITERAL_DOLLAR: "", _QUOTED_DOLLAR: "", _ARRAY_VALUE: "", _NAME_END: ""})
# The operands a line does not spell.  FIND_PATH stands where find's -exec, -execdir, -ok and -okdir put `{}`: a path
# under find's starting points, which shell/find_xargs turns into a whole-subtree write of each starting point.  INPUT_OPERAND
# stands for what xargs reads from its input -- appended after the words the line spells, or where -I or -J put it -- which
# the hook cannot know at all.  Neither is a glob, an expansion or a sentinel deglob removes, so a string a shell reads again
# (`find . -exec sh -c 'rm {}' \;`, `xargs -I% sh -c 'rm %'`) still holds it; every write channel reads a target holding one
# as a target it cannot resolve (unknown_operand), and a reason shows it as `{}` or `{input}` (shown_operands).  ANY_PATH
# stands for the files a command places where the line cannot say -- an archive extracted with -P, unzip's `-:`, a curl
# config file, tar's -T list -- which may lie anywhere at all.
FIND_PATH, INPUT_OPERAND, ANY_PATH = chr(0xE050), chr(0xE051), chr(0xE052)
_OPERAND_TEXT = {FIND_PATH: "{}", INPUT_OPERAND: "{input}", ANY_PATH: "(anywhere)"}
_LITERALIZE = str.maketrans(dict(_GLOB_SENTINELS, **_ZSH_UNSENTINEL))
_GLOB_SENTINEL_RE = re.compile("[" + "".join(_SENTINEL_TEXT) + "]")
GLOB_RE = re.compile(r"[*?\[]|\{[^}]*(?:,|\.\.)[^}]*\}|[" + ZSH_OPEN + ZSH_RANGE_OPEN + "]")
ZSH_RANGE_RE = re.compile(r"<(\d*)-(\d*)>")  # zsh's numeric glob, read as one wherever it stands unquoted (probed)
GLOB_MATCH_CAP = 500   # the most files a redirection glob is expanded to before the hook refuses a member
GLOB_SCAN_CAP = 5000   # the most directory entries scanned expanding one glob, so `**` never walks a large tree unbounded
# An `alias` definition word, `name=body`, as it reaches the analysis with its quotes taken (`alias gp='git push'`
# is one word, `gp=git push`).  The shells take almost any name, so the name is everything before the first `=`; a bare word
# is a query, which defines nothing.
ALIAS_WORD_RE = re.compile(r"^([^=\s]+)=(.*)\Z", re.S)
# The key an alias's name is recorded under in `assigned` and `doubt`, so every rule that doubts a variable the line assigned
# doubts the alias too.  No variable name can hold it.
ALIAS_KEY = "\x00alias\x00"
# The findings that say only that the hook cannot read a word, dropped from text the shell itself holds -- an
# alias's body or a function's, out of Claude Code's snapshot of the user's profile.  The member did not write that text,
# cannot spell it differently and cannot write the file it comes from, and Claude Code's own shadows for find, grep,
# pkill and rg each dispatch through `"$_cc_bin"`, so reading those as refusals would refuse every `grep` a member runs.
# Everything the hook *can* read there -- a git verb, a program git runs, a database call, a spud call, a file the text
# names and writes -- is the finding it would be on the line.
SHELL_TEXT_TOLERATED = frozenset({"var", "var-word", "var-doubt", "glob", "alias"})
# A positional parameter, which is how a function receives the words the member wrote (`mkdir -p $@` in a body is
# the member's own path).  A write target holding one is never pruned from text the shell holds, whatever else is: `$@`,
# `$*`, `$0`..`$9` and every braced form of them (`${@}`, `${@:2}`, `${@:$#}`, `${1:-x}`, `${#@}`, `${1+"$@"}`).  `$HOME`
# and `$_cc_bin` are not matched -- a name never starts with a digit or one of those two characters.
POSITIONAL_RE = re.compile(r"\$(?:[0-9@*]|\{[#!]?[0-9@*][^}]*\})")
# The entry ShellAnalysis.functions and .hashed hold when the line set an element of zsh's `functions` or
# `commands` parameter whose name the hook cannot read (`functions[$k]=`, `functions+=($pairs)`), so every name the hook
# reads may now be one.  No command name can hold it.
UNKNOWN_NAME = "\x00unknown\x00"
ASSIGNMENT_WORD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)\Z", re.S)
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh"}
PYTHON_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
JS_RUNTIMES = {"node", "nodejs", "bun", "deno"}
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VARREF_RE = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})\Z")  # a bare `$X` or `${X}`, whole
# A `$` that expands (not one neutralize_quoted_globs marked literal, and not the last character of the word).
_EXPANDING_DOLLAR_RE = re.compile("\\$(?!" + _LITERAL_DOLLAR + ")")  # a word-final `$` too: `$((1))` reaches a word as `$` alone
# `${X=v}`, `${X:=v}` and zsh's `${X::=v}` (flags and a subscript allowed) assign X wherever they are expanded.
_ASSIGNING_EXPANSION_RE = re.compile(r"\$\{(?:\([^)]*\))?[#!]?([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?:{0,2}=")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NAME_CHAR_RE = re.compile(r"[A-Za-z0-9_]")
_BARE_NAME_TAIL_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\Z")
_IFS_BLANKS_RE = re.compile(r"[ \t\n]+")
# Builtins that assign a shell variable named by an argument (`read X`, `printf -v X`, `getopts o X`, `unset X`, zsh's
# `print -v X`, `vared X`, `zparseopts -A X`, `set -A X` ...): a variable any of their words names may no longer hold what the line
# assigned it.  `trap`, `source` and `.` run code the hook does not read, so after them no variable is certain.
ASSIGNING_COMMANDS = {"read", "getopts", "printf", "print", "mapfile", "readarray", "unset", "let", "wait", "vared", "zparseopts", "zstyle",
                      "zformat", "zregexparse", "strftime", "zstat", "stat", "sysread", "getln", "select", "foreach", "zle", "zcurses",
                      "zsocket", "ztcp", "zpty", "zselect", "zsystem", "private", "integer", "float", "set", "compadd", "compset"}
# Variables the shells change themselves (the last argument, the directory, a match, a reply): never certain.
DYNAMIC_VARIABLES = {"_", "PWD", "OLDPWD", "REPLY", "OPTARG", "OPTIND", "MATCH", "MBEGIN", "MEND", "match", "mbegin", "mend", "BASH_REMATCH",
                     "RANDOM", "SRANDOM", "SECONDS", "EPOCHSECONDS", "EPOCHREALTIME", "LINENO", "BASH_COMMAND", "FUNCNAME", "DIRSTACK",
                     "dirstack", "PIPESTATUS", "pipestatus", "status", "argv", "BASHPID", "COLUMNS", "LINES", "HISTCMD", "psvar", "reply"}
HEREDOC_RE = re.compile(r"<<-?\s*(?:'([^']*)'|\"([^\"]*)\"|(\\?[A-Za-z_][\w.-]*))")
# The verbs Law 7 refuses a member by name, whatever git's own command list says: the ones a member would reach for, so
# the refusal keeps its own reason (`git push` is still "Spud commits" when the hook cannot run git at all).
# Every other name git answers to is refused by GIT_MEMBER_VERBS below; this table is the named half, not the whole set,
# and git_verbs.git_write_option_targets reads it to skip the file target of a verb refused here -- which is why a
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
# under a name the line never spells, which neither rule sees; that gap is left open rather than
# overturn that division here.
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
# target, against every directory the shell may be in; read by git_verbs.git_write_targets.
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
# index there and `GIT_OBJECT_DIRECTORY=<dir> git hash-object -w --stdin` a loose object under it (probed).
GIT_WRITE_PATH_ENV_VARS = ("GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
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
# (`archive --add-file`, `grep -f`, `commit-tree -F`, `ls-files -X`) carries no entry.
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
}
# Per verb, (the subcommand the writing form takes or None, how many of its positional words name a path git writes):
# `git bundle create <file> <rev-list-args>`, `git mailinfo <msg> <patch>` and `git pack-objects <base-name>` each wrote
# what they name (probed; bundle create with `-q` and `--version=2` before the file too).
GIT_VERB_FILE_POSITIONALS = {"bundle": ("create", 1), "mailinfo": (None, 2), "pack-objects": (None, 1)}
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
PERL_RE = re.compile(r"^perl(?:\d+(?:\.\d+)*)?$")
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
        # it.  A shell that runs what it reads there runs that text (shell/stdin_text).  analyse_segment sets it for
        # each command and puts back what it found, so a body read in its own process reads its own input, not this one.
        self.stdin = None
        # Whether the line puts anything on that standard input at all (stdin_text.input_fed), which `stdin`
        # cannot say, being None both for text the line does not spell and for no input at all.  An interpreter with no
        # program of its own runs whatever stands there, and reads a terminal where nothing does (shell/inline_programs).
        self.stdin_fed = False
        self.cwds = frozenset([cwd]) if cwd else None
        self.unparseable = False
        self.loop_depth = 0  # inside a loop or a function body, where a relative cd may repeat
        self.cd_uncertain = False  # the last directory change may not happen (a target that does not exist now)
        self.isolated_done = set()  # (command, depth, starting state) of every body analysed in its own process
        # `doubt`, the variables whose value in `vars` the shell may not hold when a later word reads it (an assignment that
        # may not run or does not persist, or a builtin that assigns it); `sticky`, those no later assignment settles (a function
        # body's, a loop's, `${X:=v}`); `assigned`, every name assigned so far, in order; `unsure`, the simple command being read may
        # not run in the shell (after && or ||, a pipeline element, a background job); `all_doubt`, after code the hook does not read
        self.doubt, self.sticky, self.assigned = set(), set(), []
        self.unsure = 0
        self.all_doubt = False
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
        # does not expand again inside their own body (`alias ls='ls -G'` terminates); `bodies_read`, the function names
        # whose body this line has already read, once each however often the line names them; `shell_reading`, how deep
        # inside such text the reading is, so the outermost of them prunes once, against the member's own words.
        self.shell_expanded, self.expanding, self.bodies_read = [], [], set()
        self.shell_reading = 0
        # The command names a `hash` line put in the shell's own command table, so a later bare call of one of them
        # runs the file the line chose whatever PATH holds.  Never cleared: a `hash` in a branch, a subshell or a loop body
        # still leaves the hook unable to say which program a name finds, and the refusal is the safe answer.  zsh's
        # `commands[name]=<path>` fills the same table, and UNKNOWN_NAME stands for a name it cannot read.
        self.hashed = set()
        # The variables of a `for` or `select` loop whose every listed word starts with something other than `-`,
        # so a word such a variable fills may be read as the operand it is where a command reads options (tree_writes).
        self.dashless_loops = set()
        # The names a `name () { ... }`/`function name` definition earlier on the line bound to a shell function, so
        # a later bare call of one of them (in command position) runs that function, not the program the hook read.  Kept as a
        # set like `hashed`, but scoped: a definition in a branch, a loop or another function's body may exist at the call, so
        # it is kept (refuse on doubt); one in a `( ... )` subshell does not reach a call after it, so ShellWalk restores this
        # set when the subshell frame closes (probed: `(git(){ :; }); git status` ran the real git).  zsh's `functions`
        # parameter binds the same names (`functions[git]=body`, `functions+=(git body)`), and UNKNOWN_NAME stands
        # for one whose name the hook cannot read.
        self.functions = set()

    @property
    def all_spud(self):
        """Every simple command is a spud call (a `cd` beside it changes nothing that matters)."""
        return "spud" in self.kinds and all(k in ("spud", "cd") for k in self.kinds) and not self.unparseable


def unknown_operand(word):
    """True when a word holds an operand the line does not spell: find's `{}`, xargs's input, or a file a command
    places where the line cannot say."""
    return FIND_PATH in word or INPUT_OPERAND in word or ANY_PATH in word


def shown_operands(text):
    """A word or reason with the operand markers shown as the line spells them: `{}` for find's, `{input}` for xargs's."""
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
