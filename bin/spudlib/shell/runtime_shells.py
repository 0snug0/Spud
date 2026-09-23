"""shell/runtime_shells: the shell command a runtime's or a package manager's subcommand runs, read as an `sh -c` string is.

`bun exec "git push"` hands bun's own shell a command, and the Bash rule read the line as far as `bun`: the JS row of
shell/inline_programs took `exec` for the name of the program's file and recorded nothing, so a member could push
through it while `bun -e` has been refused since SPD-150.  It is SPD-143's reading -- the commands a line feeds a
shell -- in another shape, and each shape below runs the same class of text.  A run the table names is read here for
the shell text it hands a shell, which analyse reads with analyse_new_shell exactly where it reads an `sh -c` string
(an unsettled variable or a substitution in it is a command the hook cannot resolve, refused for a member as there),
or for the command words it runs, which analyse reads as it reads the words a wrapper runs.

The survey, each row its own evidence (2026-09-22; this Mac has bun 1.3.14, npm 11.19.1 and deno 2.9.5, and neither
pnpm nor yarn, whose rows rest on their documentation alone as shell/syntax's wget row does):

- **bun exec SCRIPT.**  `bun --help`: "exec  Run a shell script directly with Bun".  Its source
  (src/runtime/cli/exec_command.rs) runs `ctx.positionals[1]`, the first operand after `exec`, through Bun's shell
  interpreter as source text; later operands are not read.
- **npm exec / npm x / npx with -c or --call.**  npm-exec(1) and npx(1): `npm exec -c '<cmd> [args...]'`, alias `x`.
  lib/commands/exec.js hands the string to libnpmexec, whose run-script.js makes it the script `sh` runs.  npm's option
  parser (nopt) reads its options anywhere before `--` and takes an unambiguous abbreviation of a long one (`--cal`),
  and npm resolves an abbreviated command the same way (`npm exe`; lib/utils/cmd-list.js).
- **npm exec / npx with the command as words.**  With no `-c` the positional words are the command: run-script.js
  single-quotes the first and @npmcli/run-script escapes the rest, so `sh` runs exactly those words (`npm exec
  --package=x -- git push` pushes).  bin/npx-cli.js ends npx's own options at the first positional word.
- **npm explore PKG -- COMMAND.**  npm-explore(1), whose own example is `npm explore some-dependency -- git pull origin
  master`: lib/commands/explore.js joins the words after the package with spaces, unescaped, into the script a shell
  runs in the package's directory.
- **deno task --eval STRING.**  `deno task --help`: "Evaluate the passed value as if it was a task in a configuration
  file" (`deno task --eval "echo $(pwd)"`); the words after it are appended to the task as a task's arguments are.
- **pnpm exec, pnpm dlx -c, and pnpm COMMAND.**  pnpm.io/cli/exec: "Execute a shell command in scope of a project",
  `-c`/`--shell-mode` "Runs the command inside of a shell", and "the `exec` part is actually optional when the command
  is not in conflict with a builtin pnpm command".  pnpm.io/cli/dlx gives dlx the same `-c`.  In shell mode the words
  are joined with spaces into the shell's text; without it they are the command.
- **yarn exec SCRIPT [args].**  yarnpkg.com/cli/exec: "executes a shell script ... using the portable shell"; the
  berry source (plugin-essentials exec.ts, core scriptUtils.executePackageShellcode) runs its first operand as the
  script and the rest as that script's arguments.

Not shells, and not read here: `bun x`/`bunx`, `pnpm dlx` without `-c` and `yarn dlx`, which run a package's own
binary, and `yarn <name>`, which runs a script or a dependency's binary.  A command a file holds -- `npm run <name>`,
`deno task <name>`, `bun run <script>`, `pnpm run`, `yarn <script>` -- is a script runner, read by shell/script_runners
(SPD-168) against its project's allow-list of names.

Each tool's option grammar is tabled as far as the reading needs it.  An option the table does not know may or may not
take the next word as its value (nopt and npx-cli.js take it unless it starts with `-`), so both readings are made and
both analysed: a word is never skipped as a value the tool would have read as the command.

Past 250 lines (the package's look-again point): it is one body of data read as one -- a row per tool, each its own
evidence -- with the one scan that walks a run's words against it, which runner_readings reads for the text and
runner_read_index for the words read by name, so the two never disagree about where an option ends.
"""

from . import expansions, prepare, syntax

# The most readings one run is analysed under, each option the table does not know doubling them; past it the run is a
# command the hook cannot read, refused for a member as an unresolvable command word is.
MAX_READINGS = 32


class Runner:
    """One tool's options as far as reading its shell text needs them.

    `value`: the options whose value is the next word, or the rest of their own word (`--name=value`, `-Cdir`).
    `flags`: the options that take no value.  `strings`: the options whose value is the shell text (npm's `-c`).
    `switches`: the flags that make the operands shell text (deno task's `--eval`, pnpm's `-c`).
    `stop`: how many operands the tool reads before every later word is the command's own (None: options anywhere
    before `--`, as nopt reads npm's).  `abbrev`: whether a long option may be an abbreviation (nopt)."""

    __slots__ = ("value", "flags", "strings", "switches", "stop", "abbrev")

    def __init__(self, value=(), flags=(), strings=(), switches=(), stop=None, abbrev=False):
        self.value, self.flags, self.strings = frozenset(value), frozenset(flags), frozenset(strings)
        self.switches, self.stop, self.abbrev = frozenset(switches), stop, abbrev


# npm's own configuration (npm help config, @npmcli/config's definitions and shorthands): the value options and the
# booleans a line is likely to spell; nopt reads every `--no-<name>` as a boolean too.
_NPM_VALUES = ("--package", "-w", "--workspace", "--prefix", "-C", "--cache", "--userconfig", "--registry", "--reg",
               "--script-shell", "--shell", "--loglevel", "--before", "--tag")
_NPM_FLAGS = ("-y", "--yes", "--no", "-n", "--ws", "--workspaces", "--iwr", "--include-workspace-root", "-q", "--quiet",
              "-s", "--silent", "-d", "-dd", "-ddd", "--verbose", "-g", "--global", "--offline", "--prefer-online",
              "--prefer-offline", "--foreground-scripts", "--ignore-scripts", "-f", "--force", "-h", "--help", "--json",
              "--parseable", "--porcelain", "--local")
NPM_RUNNER = Runner(value=_NPM_VALUES, flags=_NPM_FLAGS, strings=("-c", "--call"), abbrev=True)
# npx rewrites `-p` to `--package` and ends its options at the first operand (bin/npx-cli.js).
NPX_RUNNER = Runner(value=_NPM_VALUES + ("-p",), flags=_NPM_FLAGS, strings=("-c", "--call"), stop=0, abbrev=True)
# `bun --help`: every option it lists with `=<val>`, and node's `-r`/`--require` and `-e`/`-p`, whose values bun reads.
BUN_RUNNER = Runner(
    value=("-r", "--preload", "--require", "--import", "--cwd", "-c", "--config", "-F", "--filter", "--shell",
           "--env-file", "--port", "--conditions", "--install", "--title", "--elide-lines", "--console-depth",
           "--user-agent", "--unhandled-rejections", "--dns-result-order", "--max-http-header-size",
           "--fetch-preconnect", "--cron-title", "--cron-period", "--cpu-prof-name", "--cpu-prof-dir",
           "--cpu-prof-interval", "--heap-prof-name", "--heap-prof-dir", "-e", "--eval", "-p", "--print"),
    flags=("--watch", "--hot", "--no-clear-screen", "--smol", "--if-present", "--no-install", "-i", "--silent",
           "-b", "--bun", "--workspaces", "--parallel", "--sequential", "--no-exit-on-error", "--no-env-file",
           "--no-orphans", "-h", "--help", "-v", "--version", "--revision"))
# `deno task --help`, and deno's global `-L`/`--log-level` and `-q`: `--lock [<FILE>]` may or may not take the next
# word, and so is left to the reading of an option the table does not know.  Every word after the task is its own.
DENO_RUNNER = Runner(
    value=("-c", "--config", "--cwd", "-f", "--filter", "-j", "--jobs", "--concurrency", "-L", "--log-level"),
    flags=("-q", "--quiet", "-r", "--recursive", "--if-present", "--members", "--no-prefix", "--no-lock", "--unstable",
           "-h", "--help"),
    switches=("--eval",), stop=1)
# pnpm.io/cli/exec and /cli/dlx, with pnpm's own `-C`/`--dir` and `--filter`; the command's words start at its name.
PNPM_RUNNER = Runner(
    value=("-C", "--dir", "--filter", "-F", "--resume-from", "--package", "--allow-build", "--reporter", "--loglevel",
           "--workspace-concurrency", "--test-pattern", "--changed-files-ignore-pattern"),
    flags=("-r", "--recursive", "--parallel", "-s", "--silent", "-w", "--workspace-root", "--bail", "--no-bail",
           "--stream", "--no-reporter-hide-prefix", "--report-summary", "--if-present", "-h", "--help"),
    switches=("-c", "--shell-mode"), stop=1)
# yarnpkg.com/cli/exec: no options of its own; yarn's `--cwd` before the command.
YARN_RUNNER = Runner(value=("--cwd",), flags=("-h", "--help"), stop=1)
RUNNERS = {"npm": NPM_RUNNER, "npx": NPX_RUNNER, "bun": BUN_RUNNER, "deno": DENO_RUNNER, "pnpm": PNPM_RUNNER,
           "yarn": YARN_RUNNER}

# pnpm's builtin commands (pnpm.io/cli), which the implicit `pnpm <command>` never names.  One missing here is read as
# the command it names, which only fails closed.
PNPM_BUILTINS = frozenset((
    "add", "install", "i", "update", "up", "upgrade", "remove", "rm", "uninstall", "un", "link", "ln", "unlink", "import",
    "rebuild", "rb", "prune", "fetch", "install-test", "it", "dedupe", "patch", "patch-commit", "patch-remove", "audit",
    "list", "ls", "ll", "la", "outdated", "why", "licenses", "run", "run-script", "test", "t", "tst", "exec", "dlx",
    "create", "start", "stop", "restart", "publish", "pack", "recursive", "multi", "m", "server", "store", "root", "bin",
    "setup", "init", "deploy", "doctor", "config", "c", "get", "set", "env", "self-update", "approve-builds",
    "ignored-builds", "cat-file", "cat-index", "find-hash", "help", "version", "login", "logout", "whoami", "owner",
    "dist-tag", "team", "unpublish", "deprecate", "undeprecate", "search", "view", "info", "show", "docs", "home", "bugs",
    "repo", "ping", "profile", "token", "access", "star", "stars", "unstar", "completion", "cache", "sbom",
    "update-local", "catalog", "node", "runtime",
))


def _npm_command(word):
    """npm's command for the word, resolving its alias and abbreviation for the two that run a shell (cmd-list.js):
    `x` and any prefix of `exec` from `exe`, any prefix of `explore` from `explo`."""
    if word is None:
        return None  # `npm --version`, `npm`: no command at all (a run with no operand crashed the hook before SPD-168)
    if word == "x" or (len(word) >= 3 and "exec".startswith(word)):
        return "exec"
    if len(word) >= 5 and "explore".startswith(word):
        return "explore"
    return word


def runner_scans(base, words):
    """Every reading of this run's words the tool's option grammar allows: each a list of (index, role, text), role one
    of "opt" (an option, `text` as spelled), "switch" (a flag in `switches`), "value" (an option's value), "string" (shell
    text an option carries, `text` it deglobbed), "end" (`--`) or "pos" (an operand).  None past MAX_READINGS."""
    runner = RUNNERS[base]
    done, pending = [], [(1, [], True, 0)]
    while pending:
        i, got, options, operands = pending.pop()
        while i < len(words):
            w = prepare.deglob(words[i])
            nxt = prepare.deglob(words[i + 1]) if i + 1 < len(words) else None
            if not options or w == "-" or not w.startswith("-"):
                got.append((i, "pos", w))
                operands += 1
                if runner.stop is not None and operands > runner.stop:
                    options = False
                i += 1
                continue
            if w == "--":
                got.append((i, "end", w))
                options, i = False, i + 1
                continue
            name, eq, attached = w.partition("=") if w.startswith("--") else (w, "", "")
            if name.startswith("--") and runner.abbrev and len(name) >= 5 and name not in runner.value | runner.flags:
                name = next((s for s in runner.strings if s.startswith(name)), name)  # nopt's abbreviation: --cal
            if name in runner.strings:
                if eq:
                    got.append((i, "string", attached))
                    i += 1
                elif nxt is not None:
                    got += [(i, "opt", w), (i + 1, "string", nxt)]
                    i += 2
                else:
                    got.append((i, "opt", w))
                    i += 1
                continue
            short = not w.startswith("--") and len(w) > 2
            if short and w[:2] in runner.strings:
                got.append((i, "string", w[2:]))  # the rest of its word: `-c'git push'`
                i += 1
                continue
            if name in runner.switches:
                got.append((i, "switch", name))
                i += 1
                continue
            got.append((i, "opt", w))
            if eq or name in runner.flags or (base == "npm" and name.startswith("--no-")) or (short and w[:2] in runner.value):
                i += 1
                continue
            if name in runner.value:
                if nxt is not None:
                    got.append((i + 1, "value", nxt))
                i += 2
                continue
            if nxt is not None and not nxt.startswith("-"):
                # an option the table does not know: its value, or the next operand -- both are read
                if len(done) + len(pending) >= MAX_READINGS:
                    return None
                pending.append((i + 2, got + [(i + 1, "value", nxt)], options, operands))
            i += 1
        done.append(got)
        if len(done) > MAX_READINGS:
            return None
    return done


def runner_read_index(base, words, start=1):
    """The index, from `start`, of the first word this run's option scan reads by name that the shell expands first --
    a glob or an expansion -- or None: its options, and the operand that is its command (npm's `exec`, bun's, deno's
    `task`, pnpm's and yarn's), so `npm $SUB -c 'git push'` is read as each command `$SUB` can be.  analyse reads it
    with read_points, as it reads an interpreter's options.

    bun and deno are interpreters too, whose own branch reads their options and the word where a program stands
    (interpreter_words.read_point), which is where the subcommand stands: only the words after it are read here, so a
    word is never recorded twice."""
    found = runner_scans(base, words)
    if found is None:
        return next((j for j in range(start, len(words)) if expansions.active_read_word(words[j])), None)
    named = set()
    for got in found:
        operands = [i for i, role, _t in got if role == "pos"]
        if base in ("bun", "deno") and not operands:
            continue
        after = operands[0] if base in ("bun", "deno") else 0
        named.update(i for i, role, _t in got if role in ("opt", "switch", "end") and i > after)
        if operands and base not in ("npx", "bun", "deno"):
            named.add(operands[0])
    return next((j for j in sorted(named) if j >= start and expansions.active_read_word(words[j])), None)


def runner_readings(base, words):
    """What this run hands a shell or runs as a command, under every reading scans makes: a list of ("text", the shell
    text) for analyse_new_shell, ("words", the command's words as the line spells them) for analyse_words, or
    ("unread", the command as shown) for a run the hook cannot read at all.  Empty for a run that runs no shell here."""
    found = runner_scans(base, words)
    if found is None:
        return [("unread", runner_shown(words))]
    out = []
    for got in found:
        for reading in _runner_reading(base, words, got):
            if reading not in out:
                out.append(reading)
    return out


def _runner_reading(base, words, got):
    operands = [i for i, role, _t in got if role == "pos"]
    strings = [t for _i, role, t in got if role == "string"]
    switches = {t for _i, role, t in got if role == "switch"}
    sub = prepare.deglob(words[operands[0]]) if operands else None
    if base == "npx" or (base == "npm" and _npm_command(sub) == "exec"):
        if strings:
            return [("text", s) for s in strings]
        rest = operands if base == "npx" else operands[1:]
        return [("words", [words[i] for i in rest])] if rest else []
    if base == "npm" and _npm_command(sub) == "explore" and len(operands) > 2:
        return [("text", " ".join(prepare.deglob(words[i]) for i in operands[2:]))]
    if base == "bun" and sub == "exec" and len(operands) > 1:
        return [("text", prepare.deglob(words[operands[1]]))]
    if base == "deno" and sub == "task" and "--eval" in switches and len(operands) > 1:
        return [("text", _appended_text(words, operands[1:]))]
    if base == "yarn" and sub == "exec" and len(operands) > 1:
        return [("text", _appended_text(words, operands[1:]))]
    if base == "pnpm" and sub is not None:
        if sub == "exec" or sub == "dlx":
            rest = operands[1:]
        elif sub not in PNPM_BUILTINS:
            rest = operands  # the implicit `pnpm exec`
        else:
            return []
        if not rest:
            return []
        if switches & PNPM_RUNNER.switches:
            return [("text", " ".join(prepare.deglob(words[i]) for i in rest))]
        return [] if sub == "dlx" else [("words", [words[i] for i in rest])]
    return []


def _appended_text(words, operands):
    """The first operand as shell text with the others after it as the literal arguments the tool appends -- double
    quoted, so a `$NAME` the line settled reads as its value and one it did not reads as the expansion it is."""
    first, rest = operands[0], operands[1:]
    quoted = ['"%s"' % prepare.deglob(words[i]).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`") for i in rest]
    return " ".join([prepare.deglob(words[first])] + quoted)


def runner_shown(words):
    """The run as the reason names it, its operand markers shown as the line spells them."""
    return syntax.shown_operands(" ".join(prepare.deglob(w) for w in words[:2]))
