"""shell/script_runners: a script runner -- `npm run`, `deno task`, `make` -- and the project allow-list of runner names.

SPD-168, SPD-145's analogue.  A script runner runs a command a project file holds: `npm run <name>`, `npm test` and npm's
other lifecycle verbs, `pnpm run`, `yarn <script>`, `bun run`, `node --run` (package.json's `scripts`), `deno task <name>`
(deno.json's or deno.jsonc's `tasks`, and package.json's scripts), `make <target>` (the makefile's recipes).  The Bash rule
read such a line no more than it read `sh x.sh`: a member that could edit package.json could put `git push` in a script
and run `npm run x`, past Law 7, and the same for a spud call (Law 6) or a write outside its deliverables (Law 5).

Eric's call (2026-09-22): the same shape as SPD-145.  For a caller the Bash rule holds (a member) each run is recorded
here as a "runner" finding and refused in bash_rule, last of all, unless

- every name it runs is on its project's allow-list, `projects.runners` (migration 0008_project_runners), which Spud sets
  with `spud --as spud project edit <key> --allow-runner <name>` and `project show` lists.  The names a run runs are the
  ones it asks for (`npm run build`: build; `make test lint`: test and lint) and those the file then runs by name:
  `pre<name>` and `post<name>` wherever the file defines them (npm, and bun and deno too: probed 2026-09-22 with npm
  11.19.1, bun 1.3.14 and deno 2.9.5, each ran `prex`, `x` and `postx` for `x`), a deno task's `dependencies` (deno ran
  a package.json script a deno.json task named as one), and the lifecycle scripts a verb runs (`npm install` runs
  `preinstall`, `install`, `postinstall`, `prepare` and their kin; `npm pack`, `publish`, `version`, `restart` theirs);
- every file the runner reads its commands or its configuration from lies outside the member's reach: in each directory
  from where the runner starts up to the one holding the file it reads (package.json; deno.json or deno.jsonc; a
  makefile), each name the runner would read there (package.json, .npmrc, .yarnrc, .yarnrc.yml, bunfig.toml,
  pnpm-workspace.yaml, .pnpmfile.cjs; deno.json, deno.jsonc; GNUmakefile, makefile, Makefile and the files an `include`
  line names) is one the path rule refuses the member (hooks/pathrule.edit_reason: outside its deliverable globs, and
  not in the scratchpad or the temp roots), whether or not it exists yet, since a nearer one the member wrote would be
  read first; the line writes none of them; and the file that defines the names lies in the member's ticket's project,
  its main checkout or the ticket's bound worktree, whose allow-list is the one read;
- and the line sets no shell or configuration of the runner's own: `--script-shell` (npm, pnpm, yarn), bun's `--shell`,
  a configuration file named on the line (`--userconfig`, bun's `-c`, pnpm's `--config.*`), a variable a runner reads its
  configuration from (npm_config_*, yarn_*, pnpm_config_*, bun_config_*, MAKEFLAGS, MAKEFILES) assigned anywhere on the
  line, a make variable given on the line (`make test SHELL=/tmp/x`), make's `-e`, `--eval` and `-I`.

A run the hook cannot settle is refused a member the same way: a name, a directory or a file the line does not spell (a
variable, a glob, words xargs reads from its input), an option that runs other packages' scripts (npm's `-w`/`--workspaces`,
pnpm's `-r`/`--filter`, bun's `--filter`/`--workspaces`, deno's `-r`/`--filter`, `yarn workspace`), and a `make` with no
target, whose default goal the hook does not name.  Spud and Eric's own sessions are unaffected, as with SPD-145.

What stays open: what an allowed name's command does is Spud's to judge when he allows it, from a file the member cannot
change -- the arguments the line appends to it (`npm test -- x`), a variable it expands that the line may set, the
scripts it runs by name itself (`"web": "npm run web:seed && ..."`), a make target's prerequisites, a node program it runs
from a file the member wrote (`node --test`), which is SPD-150's open question for every caller.  A runner the table does
not name records nothing (`just`, `task`, `rake`, `cargo`, `corepack pnpm`).

This module reads a run's words into plans, for analyse; shell/runner_files holds a plan against the files the runner
would read, for bash_rule -- the seam between the half that reads words and the half that reads files, each with its own
caller.  Past 250 lines (the package's look-again point) this half stays whole: it is one body of data read as one, a
row per runner and verb, each its own evidence, with the grammars that read a run's words against them, as
shell/runtime_shells is.
"""

from . import prepare, runtime_shells, stdin_text, syntax

RUNNER_BASES = frozenset({"npm", "pnpm", "yarn", "bun", "deno", "node", "nodejs", "make", "gmake"})

# The lifecycle scripts a verb runs by name (npm help scripts, "Life Cycle Operation Order"), each only where the file
# defines it.  A reify -- install and every verb that installs -- runs the root package's install scripts.
LIFECYCLE_INSTALL = ("preinstall", "install", "postinstall", "prepublish", "preprepare", "prepare", "postprepare")
LIFECYCLE_PACK = ("prepack", "prepare", "postpack")
LIFECYCLE_PUBLISH = ("prepublishOnly", "prepack", "prepare", "postpack", "prepublish", "publish", "postpublish")
LIFECYCLE_VERSION = ("preversion", "version", "postversion")

# npm's commands and aliases (`npm help`, lib/utils/cmd-list.js in npm 11.19.1): npm reads a unique prefix of any of
# them as that command (`npm tes` ran pretest and test, probed 2026-09-22).
NPM_COMMANDS = (
    "access", "adduser", "approve-scripts", "audit", "bugs", "cache", "ci", "completion", "config", "dedupe", "deny-scripts",
    "deprecate", "diff", "dist-tag", "docs", "doctor", "edit", "exec", "explain", "explore", "find-dupes", "fund", "get",
    "help", "help-search", "init", "install", "install-ci-test", "install-scripts", "install-test", "link", "ll", "login",
    "logout", "ls", "org", "outdated", "owner", "pack", "ping", "pkg", "prefix", "profile", "prune", "publish", "query",
    "rebuild", "repo", "restart", "root", "run", "sbom", "search", "set", "shrinkwrap", "stage", "star", "stars", "start",
    "stop", "team", "test", "token", "trust", "undeprecate", "uninstall", "unpublish", "unstar", "update", "version", "view",
    "whoami")
NPM_ALIASES = {
    "author": "owner", "home": "docs", "issues": "bugs", "info": "view", "show": "view", "find": "search", "add": "install",
    "unlink": "uninstall", "remove": "uninstall", "rm": "uninstall", "r": "uninstall", "un": "uninstall", "rb": "rebuild",
    "list": "ls", "ln": "link", "create": "init", "i": "install", "it": "install-test", "cit": "install-ci-test",
    "u": "update", "up": "update", "c": "config", "s": "search", "se": "search", "tst": "test", "t": "test", "ddp": "dedupe",
    "v": "view", "run-script": "run", "clean-install": "ci", "clean-install-test": "install-ci-test", "x": "exec",
    "why": "explain", "la": "ll", "verison": "version", "ic": "ci", "innit": "init", "in": "install", "ins": "install",
    "inst": "install", "insta": "install", "instal": "install", "isnt": "install", "isnta": "install", "isntal": "install",
    "isntall": "install", "install-clean": "ci", "isntall-clean": "ci", "hlep": "help", "dist-tags": "dist-tag",
    "upgrade": "update", "udpate": "update", "rum": "run", "sit": "install-ci-test", "urn": "run", "ogr": "org",
    "add-user": "adduser"}


def _names(names, prepost=False, always=False):
    return tuple((n, prepost, always) for n in names)


# What each verb runs: "script" where its next operand is the name, else the requests it makes -- (name, whether the
# name's pre and post scripts run with it, whether it is asked for whatever the file defines).
_RUNS_TEST, _RUNS_START, _RUNS_STOP = _names(("test",), True), _names(("start",), True), _names(("stop",), True)
_RUNS_RESTART = _names(("restart", "stop", "start"), True)  # npm-restart(1): restart, else stop and start, each with its hooks
NPM_VERBS = {
    "run": "script", "test": _RUNS_TEST, "start": _RUNS_START, "stop": _RUNS_STOP, "restart": _RUNS_RESTART, "pack": _names(LIFECYCLE_PACK),
    "publish": _names(LIFECYCLE_PUBLISH), "version": _names(LIFECYCLE_VERSION), "install-test": _names(LIFECYCLE_INSTALL) + _RUNS_TEST,
    "install-ci-test": _names(LIFECYCLE_INSTALL) + _RUNS_TEST,
    **{v: _names(LIFECYCLE_INSTALL) for v in ("install", "ci", "uninstall", "update", "dedupe", "link", "rebuild", "prune", "install-scripts")},
}
# pnpm.io/cli: run, the test/start/stop/restart shorthands, and the verbs that install; a word that is no builtin runs the
# script it names, where one is defined (`pnpm <script>`), which runtime_shells reads as the implicit exec otherwise.
PNPM_VERBS = {
    "run": "script", "run-script": "script", "test": _RUNS_TEST, "t": _RUNS_TEST, "tst": _RUNS_TEST, "start": _RUNS_START, "stop": _RUNS_STOP,
    "restart": _RUNS_RESTART, "pack": _names(LIFECYCLE_PACK), "publish": _names(LIFECYCLE_PUBLISH), "install-test": _names(LIFECYCLE_INSTALL) + _RUNS_TEST,
    "it": _names(LIFECYCLE_INSTALL) + _RUNS_TEST,
    **{v: _names(LIFECYCLE_INSTALL) for v in ("install", "i", "add", "update", "up", "upgrade", "remove", "rm", "uninstall", "un",
                                    "link", "ln", "rebuild", "rb", "prune", "dedupe", "import")},
}
# yarnpkg.com/cli and yarn 1's: `yarn` alone installs; a word that is no builtin runs the script it names, where one is
# defined, and a package's binary otherwise.
YARN_VERBS = {
    "run": "script", "pack": _names(LIFECYCLE_PACK), "publish": _names(LIFECYCLE_PUBLISH), "version": _names(LIFECYCLE_VERSION),
    **{v: _names(LIFECYCLE_INSTALL) for v in ("install", "add", "remove", "up", "upgrade", "link", "unlink", "rebuild", "dedupe")},
}
YARN_BUILTINS = frozenset((
    "add", "audit", "autoclean", "bin", "cache", "check", "config", "constraints", "create", "dedupe", "dlx", "exec", "explain",
    "generate-lock-entry", "global", "help", "import", "info", "init", "install", "licenses", "link", "list", "login", "logout",
    "node", "npm", "outdated", "owner", "pack", "patch", "patch-commit", "plugin", "policies", "publish", "rebuild", "remove",
    "run", "search", "set", "stage", "tag", "team", "unlink", "unplug", "up", "upgrade", "upgrade-interactive", "version",
    "versions", "why", "workspace", "workspaces"))
# `bun --help`: run, and the verbs that install; a word that is no builtin runs the script it names where one is defined,
# and a file or a binary otherwise (so does `bun run <word>`).
BUN_VERBS = {"run": "script", "publish": _names(LIFECYCLE_PUBLISH),
             **{v: _names(LIFECYCLE_INSTALL) for v in ("install", "i", "add", "a", "remove", "rm", "update", "link")}}
BUN_BUILTINS = frozenset((
    "run", "test", "x", "repl", "exec", "install", "i", "add", "a", "remove", "rm", "update", "outdated", "link", "unlink", "pm",
    "build", "init", "create", "c", "upgrade", "publish", "audit", "info", "patch", "patch-commit", "why", "feedback",
    "completions", "getcompletes", "discord", "help"))

# The options that make a run the hook cannot read: each family's own, as runtime_shells' tables spell them.
_WHY_SHELL = "`%s` sets the shell the scripts run in"
_WHY_OTHERS = "`%s` runs scripts of other packages than the one the hook reads"
_WHY_GLOBAL = "`%s` runs against the global prefix rather than the project"
_WHY_CONFIG = "`%s` names a configuration file the runner reads, which may set the shell the scripts run in"
REFUSED_OPTIONS = {
    "npm": {"--script-shell": _WHY_SHELL, "--shell": _WHY_SHELL, "--workspace": _WHY_OTHERS, "-w": _WHY_OTHERS, "--workspaces": _WHY_OTHERS,
            "--ws": _WHY_OTHERS, "--include-workspace-root": _WHY_OTHERS, "--iwr": _WHY_OTHERS, "--global": _WHY_GLOBAL, "-g": _WHY_GLOBAL,
            "--location": _WHY_GLOBAL, "--userconfig": _WHY_CONFIG, "--globalconfig": _WHY_CONFIG},
    "pnpm": {"--script-shell": _WHY_SHELL, "--shell-emulator": _WHY_SHELL, "-r": _WHY_OTHERS, "--recursive": _WHY_OTHERS, "--filter": _WHY_OTHERS,
             "-F": _WHY_OTHERS, "--filter-prod": _WHY_OTHERS, "-w": _WHY_OTHERS, "--workspace-root": _WHY_OTHERS, "--global": _WHY_GLOBAL,
             "-g": _WHY_GLOBAL},
    "yarn": {"--script-shell": _WHY_SHELL, "--use-yarnrc": _WHY_CONFIG, "--global-folder": _WHY_GLOBAL},
    "bun": {"--shell": _WHY_SHELL, "--filter": _WHY_OTHERS, "-F": _WHY_OTHERS, "--workspaces": _WHY_OTHERS, "-c": _WHY_CONFIG, "--config": _WHY_CONFIG},
    "deno": {"--filter": _WHY_OTHERS, "-f": _WHY_OTHERS, "--recursive": _WHY_OTHERS, "-r": _WHY_OTHERS},
}
DIR_OPTIONS = {"npm": ("--prefix", "-C"), "pnpm": ("-C", "--dir"), "yarn": ("--cwd",), "bun": ("--cwd",), "deno": ("--cwd",)}
CONFIG_OPTIONS = {"deno": ("-c", "--config")}

# GNU make's options (make(1), `make --help`): the flags, the options whose value is the rest of the word or the next one,
# the two whose number is optional, and the ones a member is refused -- -e lets the environment override the makefile,
# --eval and -E read makefile text from the line, -I searches directories for an `include`, -t writes the targets' files.
MAKE_FLAGS = frozenset("bmBdhikLnpqrRsSvw")
MAKE_VALUES = frozenset("CfoW")
MAKE_NUMBERS = frozenset("jl")
MAKE_REFUSED = {"e": "`-e` lets the environment override the makefile's variables, its SHELL among them",
                "E": "`-E` reads makefile text from the line", "I": "`-I` searches directories the hook does not read for an include",
                "t": "`-t` writes the targets' files rather than running their recipes"}
MAKE_LONG_FLAGS = frozenset((
    "--always-make", "--debug", "--ignore-errors", "--keep-going", "--just-print", "--dry-run", "--recon", "--question",
    "--no-builtin-rules", "--no-builtin-variables", "--silent", "--quiet", "--no-silent", "--stop", "--no-keep-going",
    "--print-directory", "--no-print-directory", "--version", "--help", "--warn-undefined-variables", "--trace",
    "--check-symlink-times", "--print-data-base", "--output-sync", "--shuffle", "--jobs", "--load-average", "--max-load"))
MAKE_LONG_VALUES = frozenset(("--directory", "--file", "--makefile", "--old-file", "--assume-old", "--what-if", "--new-file",
                              "--assume-new", "--jobserver-auth", "--jobserver-fds", "--jobserver-style"))
MAKE_LONG_REFUSED = {"--environment-overrides": MAKE_REFUSED["e"], "--eval": "`--eval` reads makefile text from the line",
                     "--include-dir": MAKE_REFUSED["I"], "--touch": MAKE_REFUSED["t"]}

# The variables a runner reads its configuration from (npm reads npm_config_<key> in any case, pnpm pnpm_config_ too,
# yarn yarn_<key>, bun bun_config_; GNU make reads MAKEFLAGS, GNUMAKEFLAGS and MFLAGS as options and MAKEFILES as
# makefiles to read first): refused a member wherever the line assigns one.
ENV_PREFIXES = ("npm_config_", "pnpm_config_", "yarn_", "bun_config_")
ENV_NAMES = frozenset(("makeflags", "gnumakeflags", "mflags", "makefiles"))

# ----------------------------------------------------------------------------
# The analysis: what analyse's dispatch records
# ----------------------------------------------------------------------------

def read_runner_assignment(a, name):
    """An assignment the line makes: a variable a runner reads its configuration from is recorded, whatever runs."""
    folded = name.casefold()
    if folded.startswith(ENV_PREFIXES) or folded in ENV_NAMES:
        family = "make" if folded in ENV_NAMES else folded.partition("_")[0]
        plan = ("refuse", family, name + "=...", "`%s` sets a runner's configuration, the shell its scripts run in among it" % name)
        a.findings.append(("runner", ((plan,), a.cwds)))


def read_runner(base, words, a):
    """A runner's words as analyse's dispatch read them (`words` with what an xargs appends): one "runner" finding holding
    a plan for every reading the tool's option grammar allows, or nothing where no reading runs a script."""
    family = "node" if base == "nodejs" else ("make" if base == "gmake" else base)
    if family == "make":
        plans = [_make_plan(words, a)]
    elif family == "node":
        plans = [_node_plan(words, a)]
    else:
        found = runtime_shells.runner_scans(base, words)
        if found is None:
            plans = [("refuse", family, _shown(words), "the hook cannot say which of its options take a value")]
        else:
            plans = []
            for got in found:
                plan = _package_plan(family, words, got, a)
                plans.extend(plan if isinstance(plan, list) else [plan])
    plans = tuple(dict.fromkeys(p for p in plans if p is not None))
    if plans:
        a.findings.append(("runner", (plans, a.cwds)))


def _shown(words, upto=None):
    return syntax.shown_operands(" ".join(prepare.deglob(w) for w in words[: upto if upto is not None else 4]))


def _text(word, a):
    """The word as the line settles it, or None: a variable, a substitution, a glob, or what xargs reads."""
    return stdin_text.word_text(word, a)


def _package_plan(family, words, got, a):
    """The plan for one reading of an npm, pnpm, yarn, bun or deno run: ("check", ...) or ("refuse", ...), or None where
    the reading runs no script of a project's."""
    operands = [(i, t) for i, role, t in got if role == "pos"]
    if operands and syntax.unknown_operand(words[operands[0][0]]):
        if family in ("bun", "deno"):
            return None  # where bun's or deno's program stands: the interpreter's reading refuses it (interpreter_words)
        return ("refuse", family, _shown(words), "its words come from what xargs reads from its input")
    dirs, configs, ignore = [], [], False
    for i, role, t in got:
        if role not in ("opt", "switch"):
            continue
        if t == "--eval" and family == "deno":
            return None  # `deno task --eval`: shell text, which runtime_shells reads
        name, eq, _v = t.partition("=") if t.startswith("--") else (t, "", "")
        why = REFUSED_OPTIONS[family].get(name)
        if why is None and family == "pnpm" and name.startswith("--config."):
            why = _WHY_CONFIG
        if why is None and family == "npm" and name.startswith("--") and len(name) >= 4 and name not in runtime_shells.NPM_RUNNER.flags | runtime_shells.NPM_RUNNER.value:
            if any(o.startswith(name) for o in list(REFUSED_OPTIONS["npm"]) + list(DIR_OPTIONS["npm"]) if o.startswith("--")):
                why = "`%s` is an abbreviation npm may read as an option that moves or reconfigures the run"
        if why is not None:
            return ("refuse", family, _shown(words), why % name)
        if name == "--ignore-scripts":
            ignore = True
        short = [o for o in DIR_OPTIONS.get(family, ()) + CONFIG_OPTIONS.get(family, ()) if not o.startswith("--")]
        attached = next((o for o in short if name != o and name.startswith(o)), None)  # `-Cdir`
        if name in DIR_OPTIONS.get(family, ()) or name in CONFIG_OPTIONS.get(family, ()) or attached:
            option = attached or name
            if attached or eq:
                value = _text(words[i], a)
                value = None if value is None else (value[len(attached):] if attached else value.partition("=")[2])
            else:
                value = _text(words[i + 1], a) if i + 1 < len(words) else None
            if value is None:
                return ("refuse", family, _shown(words), "`%s` names a directory or file the line does not settle" % option)
            (configs if option in CONFIG_OPTIONS.get(family, ()) else dirs).append(value)
    requests, name_at = _requests(family, words, operands, a)
    if requests is None:
        return ("refuse", family, _shown(words), name_at)
    if ignore:
        requests = tuple((n, False, always) for n, _pp, always in requests if always)  # the named script, with no hooks
    if not requests:
        return None
    shown = _shown(words, (name_at + 1) if name_at is not None else None)
    plan = ("check", family, shown, requests, tuple(dirs), tuple(configs))
    if family == "deno" and dirs:
        # `deno task --cwd <dir>` runs the task in that directory ("Specify the directory to run the task in", `deno task
        # --help`), and the hook reads the configuration found from either, whichever deno reads
        return [plan, ("check", family, shown, requests, (), tuple(configs))]
    return plan


def _requests(family, words, operands, a):
    """(the names this reading runs, the index of the word that names the script or None), or (None, why) for one the
    hook cannot settle.  A verb that runs no script gives ((), None)."""
    if family == "yarn" and not operands:
        return _names(LIFECYCLE_INSTALL), None  # `yarn` alone installs
    if not operands:
        return (), None
    i, _t = operands[0]
    verb = _text(words[i], a)
    if verb is None:
        return None, "the command it runs is a word the line does not settle"
    implicit = False
    if family == "npm":
        what = NPM_VERBS.get(_npm_deref(verb))
    elif family == "pnpm":
        what = PNPM_VERBS.get(verb)
        implicit = what is None and verb not in runtime_shells.PNPM_BUILTINS
    elif family == "yarn":
        if verb in ("workspace", "workspaces"):
            return None, "`yarn %s` runs scripts of other packages than the one the hook reads" % verb
        what = YARN_VERBS.get(verb)
        implicit = what is None and verb not in YARN_BUILTINS
    elif family == "bun":
        what = BUN_VERBS.get(verb)
        implicit = what is None and verb not in BUN_BUILTINS
    else:  # deno: only `deno task <name>` runs a task
        what = "script" if verb == "task" else None
    if implicit:
        return ((verb, True, False),), i  # a script where the file defines one, else a binary or a file
    if what != "script":
        return (what or ()), None
    if len(operands) < 2:
        return (), None  # `npm run`, `deno task`: the list of names, nothing run
    k, _t = operands[1]
    name = _text(words[k], a)
    if syntax.unknown_operand(words[k]):
        return None, "its words come from what xargs reads from its input"
    if name is None:
        return None, "the name it runs is a word the line does not settle"
    if family == "deno" and any(c in name for c in "*?["):
        return None, "`%s` names every task it matches, which the hook does not expand" % name
    # bun run falls back to a file or a binary where no script has the name
    return ((name, True, family != "bun"),), k


def _npm_deref(word):
    """npm's command for a word (cmd-list.js's deref): itself, its alias's, or the one command a unique prefix names."""
    if word in NPM_ALIASES:
        return NPM_ALIASES[word]
    if word in NPM_COMMANDS:
        return word
    matches = [k for k in NPM_COMMANDS + tuple(NPM_ALIASES) if k.startswith(word)]
    return _npm_deref(matches[0]) if len(matches) == 1 else None


def _node_plan(words, a):
    """`node --run <name>` (node(1)): package.json's script, run with no pre or post script.  Read up to the program
    node runs from a file, if any, past the values of the options that take one."""
    i = 1
    while i < len(words):
        w = prepare.deglob(words[i])
        if w == "--" or not w.startswith("-"):
            return None
        if w in ("--run",) or w.startswith("--run="):
            k = i + 1 if w == "--run" else i
            name = _text(words[k], a) if k < len(words) else None
            if name is not None and w != "--run":
                name = name.partition("=")[2]
            if name is None or syntax.unknown_operand(words[k] if k < len(words) else ""):
                return ("refuse", "node", _shown(words), "the name it runs is a word the line does not settle")
            return ("check", "node", _shown(words, k + 1), ((name, False, True),), (), ())
        i += 2 if w in ("-r", "--require", "--import", "--loader", "--experimental-loader", "-C", "--conditions",
                        "--env-file", "--input-type", "--title") else 1
    return None


def _make_plan(words, a):
    """make's words (make(1)): every target it is asked for, where it reads its makefile, or why a member is refused."""
    dirs, files, targets, i, options = [], [], [], 1, True

    def refuse(why):
        return ("refuse", "make", _shown(words), why)

    while i < len(words):
        word = words[i]
        if syntax.unknown_operand(word):
            return refuse("its words come from what xargs reads from its input")
        w = prepare.deglob(word)
        if options and w == "--":
            options, i = False, i + 1
            continue
        if options and w.startswith("--"):
            name, eq, _v = w.partition("=")
            if name in MAKE_LONG_REFUSED:
                return refuse(MAKE_LONG_REFUSED[name])
            if name in MAKE_LONG_VALUES:
                value = (_text(word, a) or "=").partition("=")[2] if eq else (_text(words[i + 1], a) if i + 1 < len(words) else "")
                if not value:
                    return refuse("`%s` names a directory or file the line does not settle" % name)
                if name == "--directory":
                    dirs.append(value)
                elif name in ("--file", "--makefile"):
                    files.append(value)
                i += 1 if eq else 2
                continue
            if name not in MAKE_LONG_FLAGS:
                return refuse("`%s` is an option the hook does not read" % name)
            i += 1
            continue
        if options and w.startswith("-") and len(w) > 1:
            k, step = 1, 1
            while k < len(w):
                c = w[k]
                if c in MAKE_REFUSED:
                    return refuse(MAKE_REFUSED[c])
                if c in MAKE_VALUES:
                    if k + 1 < len(w):
                        value = _text(word, a)
                        value = None if value is None else value[k + 1:]
                    else:
                        value, step = (_text(words[i + 1], a) if i + 1 < len(words) else None), 2
                    if not value:
                        return refuse("`-%s` names a directory or file the line does not settle" % c)
                    if c == "C":
                        dirs.append(value)
                    elif c == "f":
                        files.append(value)
                    break
                if c in MAKE_NUMBERS:
                    k += 1
                    while k < len(w) and w[k].isdigit():
                        k += 1
                    if k == len(w) and i + 1 < len(words) and prepare.deglob(words[i + 1]).isdigit():
                        step = 2  # `-j 4`
                    continue
                if c == "O":
                    break  # --output-sync's type, the rest of the word
                if c not in MAKE_FLAGS:
                    return refuse("`-%s` is an option the hook does not read" % c)
                k += 1
            i += step
            continue
        text = _text(word, a)
        if text is None:
            return refuse("a target or variable it is given is a word the line does not settle")
        if "=" in text:
            return refuse("`%s` sets a make variable from the line, which overrides the makefile's own (SHELL among them)"
                          % syntax.shown_operands(text))
        targets.append(text)
        i += 1
    if not targets and any(prepare.deglob(w) in ("-v", "--version", "-h", "--help") for w in words[1:]):
        return None  # make prints its version or its usage and runs nothing
    if not targets:
        return refuse("with no target it runs the makefile's default goal, which the hook does not name; name the target")
    return ("check", "make", syntax.shown_operands(" ".join(["make"] + targets)), _names(targets, False, True),
            tuple(dirs), tuple(files))

