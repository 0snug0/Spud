"""shell/syntax: Shell and git word tables, sentinels, ShellAnalysis, tokens.  Moved from bin/spud_ledger.py (SPD-065)."""

import re
import shlex


# Shell analysis for PreToolUse(Bash).
# `<>` opens its target read-write and creates it (SPD-040, probed in zsh 5.9, bash 3.2 and sh: `1<>f` overwrote f from its
# start), so it is an output redirection whose operand is checked like any other target.
OUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
IN_REDIRECTS = {"<", "<<", "<<<", "<<-", "<&"}
RESERVED_WORDS = {"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "for", "select", "case", "esac",
                  "in", "function", "!", "{", "}", "coproc"}
# Matched case-folded (SPD-030): macOS PATH lookup is case-insensitive, so ENV runs /usr/bin/env.  noglob and nocorrect are
# zsh's precommand modifiers.
WRAPPERS = {"env", "command", "exec", "builtin", "nohup", "nice", "time", "timeout", "caffeinate", "sudo", "doas",
            "xargs", "stdbuf", "chronic", "ionice", "setsid", "unbuffer", "script", "noglob", "nocorrect"}
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
DURATION_RE = re.compile(r"\d+(?:\.\d+)?[smhd]?|\.\d+[smhd]?")
# The shell's operators, longest first: shlex (punctuation_chars) returns a run of them such as `)>` or `;;&` as one token.
SHELL_OPERATORS = (";;&", "&>>", "<<<", "<<-", ";;", ";&", "&&", "||", "|&", "&>", ">>", ">|", ">&", "<&", "<>", "<<", "<(", ">(",
                   ";", "&", "|", "(", ")", "<", ">")
SHELL_PUNCTUATION = frozenset("();<>|&")
LIST_TERMINATORS = {";", ";;", ";&", ";;&"}
# What may stand between a complete header or condition and the body that follows it with no `do` or `then`: a terminator
# separates the two (SPD-042), and a list operator says the condition is not complete after all, so the `]]` that looked
# like its end was not (SPD-061, probed: `if [[ -n x ]] && [[ -n y ]] echo both` and `if true && [[ -n x ]] echo both` ran
# the body, `if [[ -n x ]] | cat` is a parse error).
BODY_DEFERRING = LIST_TERMINATORS | {"&&", "||", "|", "|&", "&"}
# Words zsh lets stand before a compound command, so `coproc repeat 1 git push` runs the loop (probed; `nocorrect`, `noglob`,
# `command` and `-` do not: zsh reports a parse error or looks for a program named repeat).  SPD-042.
LOOP_PREFIX_WORDS = {"coproc", "time", "!"}
# The compound commands bash 4 and later run in the forked shell of a named coproc, `coproc NAME compound_command` (SPD-060,
# probed in bash 5.2 in the ubuntu:24.04 image: a `{ ... }` group, a `( ... )` subshell, `while`, `for`, `if`, `case` and
# `[[ ... ]]` each ran after the name, and a name that is not a valid identifier ran nothing; `coproc NAME echo x` is a
# simple command named NAME, and zsh, whose coproc takes a command only, is a parse error for every named form).
COPROC_COMPOUND_WORDS = {"{", "(", "[[", "if", "while", "until", "for", "select", "case"}
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
DIRECTORY_COMMANDS = {"cd", "chdir", "pushd", "popd"}  # the builtins, spelled exactly: CD and /usr/bin/cd are programs
SHELL_DECLARATIONS = {"export", "typeset", "declare", "local", "readonly"}
# A redirection or tee target the shell expands is checked as every file it opens, not as its literal spelling (SPD-034).
# neutralize_quoted_globs replaces a quoted or escaped metacharacter with a sentinel so filename generation is read only
# from the unquoted ones; deglob restores the literal character.  The sentinels are private-use characters shlex keeps in
# a word (they are not whitespace, quotes or the operator punctuation).
_GLOB_META = "*?[]{},"
_GLOB_SENTINELS = {c: chr(0xE000 + i) for i, c in enumerate(_GLOB_META)}
_GLOB_UNSENTINEL = {v: k for k, v in _GLOB_SENTINELS.items()}
# zsh's own glob operators (SPD-039): parenthesised alternation `(a|b)` and the numeric range `<n-m>`, which shlex reads as a
# subshell and as an input redirection.  mark_zsh_patterns replaces each character zsh reads as part of such a pattern (the
# parentheses, bars and blanks of a group, the angle brackets of a range) with one of these sentinels, so the pattern stays
# in one word; they are active glob syntax, unlike the quoted sentinels above, and deglob restores both kinds.
_ZSH_PATTERN_CHARS = "(|)<> \t"
_ZSH_SENTINELS = {c: chr(0xE010 + i) for i, c in enumerate(_ZSH_PATTERN_CHARS)}
ZSH_OPEN, ZSH_BAR, ZSH_CLOSE, ZSH_RANGE_OPEN, ZSH_RANGE_CLOSE = (_ZSH_SENTINELS[c] for c in "(|)<>")
_ZSH_UNSENTINEL = {v: k for k, v in _ZSH_SENTINELS.items()}
_LITERAL_EQUALS = chr(0xE020)  # a word's leading `=` that zsh's EQUALS is not to expand again (SPD-041: literalize)
# SPD-043: marks neutralize_quoted_globs leaves beside a `$`, so an expansion is told from a literal dollar once shlex has taken
# the quotes away.  `$` then _LITERAL_DOLLAR: single-quoted or escaped, no expansion.  `$` then _QUOTED_DOLLAR: `$'...'` (ANSI-C
# quoting, both shells) or `$"..."` (bash's locale string), whose text the hook does not decode.  _NAME_END: a quote or an escape
# right after `$name` ends the name (`$X"t"` is $X then t, which shlex joins as $Xt).  _ARRAY_VALUE opens the value ShellWalk
# joins for `name=(a b)`: bash reads `$name` as its first element, zsh as all of them.  deglob removes all four.
_LITERAL_DOLLAR, _QUOTED_DOLLAR, _ARRAY_VALUE, _NAME_END = chr(0xE021), chr(0xE022), chr(0xE023), chr(0xE024)
_SENTINEL_TEXT = dict(_GLOB_UNSENTINEL, **_ZSH_UNSENTINEL, **{_LITERAL_EQUALS: "=", _LITERAL_DOLLAR: "", _QUOTED_DOLLAR: "", _ARRAY_VALUE: "",
                                                              _NAME_END: ""})
_LITERALIZE = str.maketrans(dict(_GLOB_SENTINELS, **_ZSH_UNSENTINEL))
_GLOB_SENTINEL_RE = re.compile("[" + "".join(_SENTINEL_TEXT) + "]")
GLOB_RE = re.compile(r"[*?\[]|\{[^}]*(?:,|\.\.)[^}]*\}|[" + ZSH_OPEN + ZSH_RANGE_OPEN + "]")
ZSH_RANGE_RE = re.compile(r"<(\d*)-(\d*)>")  # zsh's numeric glob, read as one wherever it stands unquoted (probed)
GLOB_MATCH_CAP = 500   # the most files a redirection glob is expanded to before the hook refuses a member (SPD-034)
GLOB_SCAN_CAP = 5000   # the most directory entries scanned expanding one glob, so `**` never walks a large tree unbounded
ARRAY_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=$")
# SPD-059: an `alias` definition word, `name=body`, as it reaches the analysis with its quotes taken (`alias gp='git push'`
# is one word, `gp=git push`).  The shells take almost any name, so the name is everything before the first `=`; a bare word
# is a query, which defines nothing.
ALIAS_WORD_RE = re.compile(r"^([^=\s]+)=(.*)\Z", re.S)
# The key an alias's name is recorded under in `assigned` and `doubt`, so every rule that doubts a variable the line assigned
# doubts the alias too.  No variable name can hold it.
ALIAS_KEY = "\x00alias\x00"
ASSIGNMENT_WORD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)\Z", re.S)
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh"}
PYTHON_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
JS_RUNTIMES = {"node", "nodejs", "bun", "deno"}
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VARREF_RE = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})\Z")  # a bare `$X` or `${X}`, whole
# SPD-043: a `$` that expands (not one neutralize_quoted_globs marked literal, and not the last character of the word).
_EXPANDING_DOLLAR_RE = re.compile("\\$(?!" + _LITERAL_DOLLAR + ")")  # a word-final `$` too: `$((1))` reaches a word as `$` alone
# `${X=v}`, `${X:=v}` and zsh's `${X::=v}` (flags and a subscript allowed) assign X wherever they are expanded.
_ASSIGNING_EXPANSION_RE = re.compile(r"\$\{(?:\([^)]*\))?[#!]?([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?:{0,2}=")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NAME_CHAR_RE = re.compile(r"[A-Za-z0-9_]")
_BARE_NAME_TAIL_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\Z")
_IFS_BLANKS_RE = re.compile(r"[ \t\n]+")
# Builtins that assign a shell variable named by an argument (SPD-043: `read X`, `printf -v X`, `getopts o X`, `unset X`, zsh's
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
GIT_WRITE_VERBS = {"commit", "add", "checkout", "switch", "rebase", "reset", "push", "merge", "cherry-pick", "pull", "am",
                   "apply", "revert", "restore", "rm", "mv", "clean", "notes", "replace", "update-ref", "symbolic-ref",
                   "filter-branch", "gc", "prune", "submodule", "init", "clone", "bisect", "mergetool", "citool", "gui"}
GIT_GLOBAL_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env", "--list-cmds"}
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
# carries a single positional, which the positional count read as a key, so it slipped through Law 7 (SPD-063).
CONFIG_WRITE_SUBCOMMANDS = {"set", "unset", "edit", "rename-section", "remove-section"}
CONFIG_READ_SUBCOMMANDS = {"list", "get"}


# -- shell analysis for PreToolUse(Bash) ---------------------------------------------


class ShellAnalysis:
    """What a command line would run: (kind, detail) findings per simple command, the output redirection targets with the
    directories the shell may be in when each is opened, and whether every simple command is a spud call.

    `cwds` (SPD-030) is the set of absolute directories the shell may be in at this point of the line, every one of them
    checked, or None when the hook cannot know it: where zsh and bash disagree, or a cd may not run or may fail, the hook
    keeps both the old directory and the new one rather than guess.

    `home` (SPD-032) is the ledger root whose launcher a spud call must run for the hook to allow it."""

    def __init__(self, cwd=None, home=None):
        self.home = home
        self.findings = []
        self.kinds = []
        self.redirects = []
        # SPD-063: one entry per git call on the line, (its repository targets, the directories the shell may be in), so
        # bash_reason can read the config in force at each target repository's local and worktree scopes.  Not a finding:
        # every git line has one, and the findings are the refusals a line has earned.
        self.git_calls = []
        self.vars = {}
        self.cwds = frozenset([cwd]) if cwd else None
        self.unparseable = False
        self.loop_depth = 0  # inside a loop or a function body, where a relative cd may repeat
        self.cd_uncertain = False  # the last directory change may not happen (a target that does not exist now)
        self.isolated_done = set()  # (command, depth, starting state) of every body analysed in its own process (SPD-039)
        # SPD-043: `doubt`, the variables whose value in `vars` the shell may not hold when a later word reads it (an assignment that
        # may not run or does not persist, or a builtin that assigns it); `sticky`, those no later assignment settles (a function
        # body's, a loop's, `${X:=v}`); `assigned`, every name assigned so far, in order; `unsure`, the simple command being read may
        # not run in the shell (after && or ||, a pipeline element, a background job); `all_doubt`, after code the hook does not read
        self.doubt, self.sticky, self.assigned = set(), set(), []
        self.unsure = 0
        self.all_doubt = False
        # SPD-059: `aliases`, what `alias NAME=body` defined on the line, name -> the body's text, None for one the hook
        # cannot read and for one `unalias` cleared; `alias_scope`, how many `eval` re-analyses deep the reading is, the only
        # place on one line where a name the line aliased is expanded (a shell expands an alias when it parses the text);
        # `alias_unknown`, the line defined an alias whose name the hook cannot read.  Each name's doubt lives in `doubt`
        # under ALIAS_KEY + name, so a definition in a branch, a subshell, a pipeline or a loop body is doubted as a
        # variable's assignment there is.
        self.aliases, self.alias_scope, self.alias_unknown = {}, 0, False

    @property
    def all_spud(self):
        """Every simple command is a spud call (a `cd` beside it changes nothing that matters)."""
        return "spud" in self.kinds and all(k in ("spud", "cd") for k in self.kinds) and not self.unparseable


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
