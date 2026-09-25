"""shell/held_text: The text the shell holds, an alias's body and a function's, read and pruned of what is not the member's.

A module of its own since SPD-264, taken out of shell/analyse: analyse.dispatch_words asks read_shell_name for a command
word the Bash tool's shell already defines, and analyse_shell_text reads the body through analyse.analyse_command, then
drops what would fall on a member for text it did not write (names_member_var through unresolvable_write).  A call of
Claude Code's own grep, find, rg or pkill is recorded as that reading records it, without reading the body, by
shell/held_shadows (SPD-247), and the options the shell holds are read into the state a line starts from by
shell/held_options (SPD-263), both taken out of this module on SPD-267.  It joins the shell reading's import cycle, every
read of analyse inside a function body.  Past 250 lines as one reading: analyse_shell_text's prune and the helpers it
keeps by are one rule, and read_shell_name and read_body are its only way in.  A function body the line itself defines
is read at each call through the same per-state reading (read_once, read_function, SPD-277), since a call runs it where
the shell stands then exactly as it runs a snapshot's.  Whether a new shell's text holds the snapshot at all -- which
startup files a shell sources, read from its options -- is shell_start's (SPD-301), since only a zsh sourcing them does."""

import os
import re

from . import analyse, directories, expansions, globbing, held_shadows, line_aliases, line_functions, loop_bindings, positional, prepare, stdin_text, syntax
from ..hooks import hookio, snapshots


def read_body(a, text, depth, stdin, fed, shadow=False):
    """Read a function's body the shell holds -- or one the line defines, a line_functions.LineBody, which analyse_command
    reads as the walk had its tokens (SPD-277) -- isolated, in a scope of its own (SPD-246): a name it surely declares local
    (assignment_words.local_names) is not the line's while it runs, and when it returns the shell drops the local, so the
    name is what it was before -- its value in `vars`, no loop binding or basename of the body's, and its doubt as it
    stood before the call, unless the body assigned the name before declaring it (`V=x; local V`), which changed the
    line's.  An assignment to any other name, the body's own `NAME=value` or a loop's variable, reaches the line's shell
    and stays, doubted after the call as isolated doubts it.

    Its directory changes stay too (SPD-252): a cd, pushd or popd in the body, or in a function it calls, is where the
    line's shell is when the call returns, as the same cd on the line leaves it -- either directory after one that may
    not run or may fail, and one the hook cannot follow where the body cds into a value it cannot settle.  What the body
    runs in a process of its own (a subshell, a substitution, a pipeline element) its walk already puts back.

    A body the shell holds (text, not a LineBody) was parsed where its snapshot defined it, before its aliases and long
    before the line's, so no alias of the line's table is expanded in its own text, wherever the call stands -- eval's
    words, a substitution (SPD-283: ShellAnalysis.alias_scope is 0 while it is read, and line_aliases.held_names gives the
    shell's own none there; probed: a snapshot's `f() { echo in-f X; }`, defined before `alias -g X=snapshot`, printed
    `in-f X`).  An eval or a substitution inside it is parsed as it runs, where they stand again.

    Nor is any alias the snapshot defines, its plain ones included (SPD-290, line_aliases.held_standing): the body is read
    with a line_aliases.AliasView marked `early`, which no text it parses as it runs inherits (probed in zsh 5.9 -f: a
    body's `gp` found no command gp, where its `eval gp` and `$(gp)` ran the alias).  The harness's shadows (`shadow`),
    which the snapshot defines after its aliases, are not marked; a body the line defines, parsed with the line, is read
    unmarked wherever it is called, a snapshot body's call included (`f() { gp; }; runit f` ran the alias, runit's body
    being `"$@"`)."""
    values, doubted, own, left = dict(a.vars), frozenset(a.doubt), {}, []
    parsed = isinstance(text, str)

    def run():
        a.body_locals = (a.body_locals or []) + [{}]
        scope, view = a.alias_scope, a.alias_view
        if parsed:
            a.alias_scope = 0
            if not shadow:
                a.alias_view = line_aliases.AliasView(a, False, early=True)
        elif view is not None:
            a.alias_view = view.parsed_late()
        try:
            analyse.analyse_command(text, a, depth, stdin, fed)
        finally:
            a.alias_scope, a.alias_view = scope, view
        own.update(a.body_locals[-1])
        left.append(a.cwds)

    analyse.isolated(a, run)
    a.cwds = left[0]
    for name, before in own.items():
        if before is syntax.UNSET:
            a.vars.pop(name, None)
        else:
            a.vars[name] = before
        a.derived.pop(name, None)
        if values.get(name, syntax.UNSET) == before and name not in doubted:
            a.doubt.discard(name)
    loop_bindings.unbind(a, own)


def read_shell_name(words, a, depth, stdin=None, fed=False, effect="shell", aliased=True, function=True):
    """A command word the shell the Bash tool starts already defines, read for what it actually runs: an alias,
    whose body and the words after it are analysed as the text the shell put there -- and True, since that text is the
    command now -- or a function, whose body is read as an `eval` string is while the call's own words go on to be
    dispatched for what they name.  An alias shadows a function of the same name, as the shell resolves them.  `aliased`:
    the word stands where the shell expands an alias (the command position); `function`: where it looks a function up,
    which zsh's noglob, exec and `-` keep and the command position does not (SPD-262, analyse.dispatch_words).  Neither
    stands in a new shell's text, which never sources the snapshot (line_aliases.held_standing, SPD-290; snapshot_sourced,
    SPD-298): there the word is the program or builtin it names -- except a zsh that sources the user's startup files,
    which holds them, and one that sources part of them, where the word is read both ways (shell_start, SPD-301).

    Either runs in the line's shell, so the directory it leaves is the line's after it (SPD-252), settled by `effect`,
    where the command runs (directories.prefix_effect): `coproc takedir x` moves a forked shell and leaves the line where
    it was, as a coproc's cd does.  Probed in zsh 5.9 -f -o nobareglobqual and -f and bash 3.2.57: after `takedir x1`,
    `mkcd p q`, `take x2/y`, a pushd, a popd or a cd through a second function in a body, `pwd` printed where the body
    went; after a subshell body `( cd "$1" )`, which the walk of the body reads as the subshell it is, where the call
    started (tests/test_hooks_snapshots.py FunctionDirectoryTest).

    A function the line itself defines under that name is read at the call as well, from the state the call starts in and
    on its standard input, `stdin` (`fed`: whether anything stands there) -- line_functions.read_call and read_function,
    SPD-212 and SPD-277 -- and where both it and the snapshot define the name, each body is read from the call's start and the
    directories either leaves the line in are joined, since the line's definition may not have run.  An alias's body and
    a snapshot function's read that same input here, so `xs < x.sh` (alias xs=sh) and `shfn < x.sh` (a snapshot `shfn(){
    sh }`) read the file's program as `sh < x.sh` does, past SPD-145 and SPD-150 (SPD-215); a body is read once per call's
    words and standard input both.

    The call's words reach a function's body as its positional parameters, so the body is read with them set where it
    reads those (shell/positional, SPD-203): `gitfn push`, whose body is `command git "$@"`, is `git push`.  It is read
    once per call's words, standard input and the state the call starts in (ShellAnalysis.reading_state: the
    directories, the line's variables, SPD-252, and its aliases, which an eval or a substitution in the body expands,
    SPD-288: `evalit pz; alias pz='git push'; evalit pz`, evalit running `eval "$@"`, pushes in the second call), not
    once per name, so the second call of `gitfn status; gitfn push` is read too, and so are `noteit; cd ..; noteit` (its
    relative write lands again, elsewhere) and `V=status; vgit; V=push; vgit` (`git $V` pushes); a body that calls itself from where it started reads it no further.  A call that
    reads exactly as one read before -- after a subshell put the directory back, or in the line's second walk -- is
    given the directories that reading left (ShellAnalysis.body_dirs), and one inside its own reading leaves them
    unknown if the body moves them, since the shell would move them again from where the inner call returns.  Past
    positional.READINGS_PER_NAME readings of one name on a line, a call with other words reads the body as it stands,
    once per starting state, whose findings the member's words keep: a profile whose functions call one another with
    ever other words cannot multiply one line's readings without bound."""
    cmd = prepare.deglob(words[0])
    before = a.cwds
    if aliased and read_shell_alias(words, a, depth, stdin, fed, effect):
        return True
    before = a.cwds  # ... where the word may be read on as spelled from either directory (SPD-308)
    if not function:
        return False
    # a body the line itself defines under the name (SPD-277)
    line_moved = line_functions.read_call(a, cmd, depth, stdin, fed)
    # SPD-305: none where the line surely removed it (`unset -f cd; cd /tmp` runs the builtin), and one a removal that may
    # not have run leaves read beside the command of that name (line_functions.held_function)
    held = line_functions.held_function(a, cmd)
    if held == "sure" and line_aliases.held_partly(a):
        held = "maybe"  # SPD-301: a new zsh's startup files may not define it -- the body and the command both (shell_start)
    body = line_aliases.shell_function(cmd, a) if held else None
    if body is None and line_moved is not line_functions.NO_BODY:
        a.cwds = directories.settle(effect, before, line_moved)
    elif body is not None:
        a.cwds = before  # ... which may not be the one that runs: the snapshot's is read from the same start
        claude = snapshots.harness_shadow(cmd, body)  # Claude Code's own grep, find, rg or pkill (SPD-247)
        shadow = claude is not None  # ... which the snapshot defines after its aliases (read_body, SPD-290)
        if claude is None:
            text, sound, filled = positional.substitution(body, words[1:])
        else:
            text, sound, filled = held_shadows.shadow_text(body, words[1:]), True, ()
        # a body is read once per call's words, standard input (SPD-215) and starting state (SPD-252)
        key = (stdin_text.reading_key(stdin, fed), a.reading_state())
        read, reads = (text, tuple(words[1:]), key), a.bodies_read.setdefault(cmd, set())
        if read not in reads and len(reads) >= positional.READINGS_PER_NAME:
            text, sound, filled, claude = body, False, (), None
            read = (body, bool(words[1:]) and (a.shell_reading == 0 or bool(a.shell_words)), key)
        if read not in reads:
            a.shell_expanded.append((cmd, "a shell function"))
            # the substitutions the call's words were set in (SPD-258): the member's where the words are, the outermost
            # call's and a nested call's that passes one of them on; a nested call's own literals are its body's
            if a.shell_reading and not any(w in a.shell_words for w in words[1:]):
                filled = ()

        def run():
            if claude is None or not held_shadows.read_shadow(a, cmd, claude, words[1:], depth):
                analyse_shell_text(a, text, depth + 1, words[1:], own_process=True, substituted=sound, stdin=stdin,
                                   fed=fed, filled=filled, shadow=shadow)

        after = read_once(a, cmd, read, before, run)
        if line_moved is not line_functions.NO_BODY:
            after = directories.union_dirs(after, line_moved)  # either function may be the one that runs
        if held == "maybe":
            after = directories.union_dirs(after, before)  # ... or neither: the command, whose own move is dispatched after
        a.cwds = directories.settle(effect, before, after)
    return False


def read_shell_alias(words, a, depth, stdin, fed, effect):
    """read_shell_name's reading of a command word the shell's snapshot aliases (line_aliases.shell_aliased): each text the
    alias may put in its place, read as the shell text it is from where the command starts, the directories each leaves
    joined; True when that is the whole reading, False where the word is no such alias or may run as spelled too.

    SPD-308: where the text being read spells the name both quoted and unquoted, the hook cannot tell which this word is,
    so it reads the alias an unquoted one runs and then, returning False, the word as a quoted one runs it -- the function
    or the command it names, which read_shell_name and analyse.dispatch_words go on to read from the directories either
    reading may leave (probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py: under `alias
    ls='echo ALIASED'`, `'ls' -d /; ls -d /` printed `/`, then `ALIASED -d /`).  Under `alias git=hub`, `'git' push; git
    status` is read as hub's push and git's too, and refused a member."""
    before = a.cwds
    readings, expanded, unreadable, as_spelled = line_aliases.shell_aliased(words, a)
    a.shell_expanded.extend(expanded)
    if unreadable is not None:  # the chain reached a body whose quoting the hook cannot take off: it runs the line, unread
        a.kinds.append("other")
        a.findings.append(("shell-alias", unreadable))
    after = before
    for k, (text, own_words, names) in enumerate(readings):
        a.cwds = before
        names = body_flight(a, words[0], text, own_words, names)  # SPD-316: in flight for the body's words, not the member's
        a.expanding.extend(names)
        try:
            analyse_shell_text(a, text, depth + 1, own_words, stdin=stdin, fed=fed)
        finally:
            del a.expanding[len(a.expanding) - len(names):]
        moved = directories.settle(effect, before, a.cwds)
        after = moved if k == 0 else directories.union_dirs(after, moved)
    a.cwds = after
    if not (readings or unreadable is not None):
        return False
    if as_spelled:
        a.cwds = directories.union_dirs(before, after)
    return not as_spelled


# SPD-316: zsh keeps an alias's name in flight while the text of its body is being read, through its last word, and no
# longer: the words after the body are read from the line once it is over, and one named like the alias is looked up as any
# other (probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py, with `alias s='nice '`,
# `x='echo; s'`, `gp='echo SNAP-GP'`: `x x gp` printed two blank lines, then SNAP-GP, the second x chained behind the
# body's s; so did `x2 x2 gp` with x2='echo X2;', where the member's x2 stands at a command word, and `z z gp` with
# z='echo Z; s x', the z read after x's expansion ran on past z's body; `x3` with x3='echo A3; x3' printed A3 and found no
# command x3, the body's own last word still in its flight).  The hook reads a body and the member's words after it as one
# text, where a name is in flight for all of it or none of it (ShellAnalysis.expanding), so it holds a name only where the
# body may look it up -- where that text could otherwise expand it again -- and nowhere else, where the member's words
# are.  A name both may look up stays in flight for both, the reading before this ticket, which reads less than zsh only
# where a body that names its own alias also chains into the member's words, and never reads without end.
_FLIGHT_WORD_RE = re.compile(r"[^\s;&|<>()'\"\\$`{}=]+")  # every name a text may look up, and more


def body_flight(a, head, text, own_words, names):
    """The names of `names` -- the alias `head` and each its body's first word expanded (line_aliases.shell_aliased) --
    to hold in flight while `text`, the body then the member's `own_words` after it, is read (SPD-316, above): those the
    body may look up, where zsh reads them inside its flight; every one where the hook cannot say.  The body is the text
    before the member's words where it ends in them as alias_rest spells them, and otherwise -- a body ending in a blank,
    whose chain spelled them in -- the head's own, which that text's part before them is a chain of."""
    rest = line_aliases.alias_rest(own_words, a)
    if not rest:
        return names  # nothing of the member's after the body
    if text.endswith(" " + rest):
        body = text[: -len(rest) - 1]
    else:
        body = snapshots.shell_table(a.home).aliases.get(prepare.deglob(head))
    reached = looked_up(a, body)
    return names if reached is None else [name for name in names if name in reached]


def looked_up(a, text):
    """Every word `text` may look up as a name, as an alias it names may on down the chain (the shell's snapshot's, and the
    line's table as it stands and as the text being read was parsed with it) -- a superset, every word of each body --
    or None where it may look up what the hook cannot see: a function (whose body may expand an alias as eval's text
    does), a body the hook cannot read, an alias whose name it cannot read, past line_aliases.CHAIN_STEPS bodies."""
    found, view = snapshots.shell_table(a.home), a.alias_view
    if text is None or a.alias_unknown or view is not None and view.unknown or syntax.UNKNOWN_NAME in a.functions:
        return None
    tables = [a.aliases] if view is None else [a.aliases, view.table]
    seen, texts, left = set(), [text], line_aliases.CHAIN_STEPS
    while texts:
        for word in _FLIGHT_WORD_RE.findall(texts.pop()):
            if word in seen:
                continue
            seen.add(word)
            if word in found.functions or word in a.functions:
                return None
            suffix = word.rpartition(".")[2] if "." in word else None
            bodies = [table[key] for table in tables for key in (word, line_aliases.GLOBAL_ALIAS + word,
                      line_aliases.SUFFIX_ALIAS + suffix if suffix else None) if key in table]
            bodies += [table[key] for table, key in ((found.aliases, word), (found.galiases, word), (found.saliases, suffix))
                       if key in table]
            left -= len(bodies)
            if left < 0 or None in bodies:
                return None
            texts.extend(bodies)
    return seen


def snapshot_sourced(a):
    """Whether the shell that runs the text being read sourced Claude Code's snapshot, so a function it defines -- the
    profile's, or one of the harness's shadows -- stands there (SPD-298): the Bash tool's own shell, and every text it
    parses as the line runs, a snapshot function's body included; never a new shell's text (`sh -c`, `zsh -c`, `bash -c`,
    a shell fed its text on standard input, analyse.analyse_new_shell), nor any text parsed inside it, which never sources
    the snapshot and so runs the program of that name, or finds no command.  Probed through tests/probes/shell_probe.py in
    zsh 5.9 -f -o nobareglobqual and -f and bash 3.2.57, after sourcing a file defining pushit (`echo PUSHIT-RAN`),
    intests (`cd tests`) and grep (`echo GREP-FN`): the sourcing shell's own `eval` ran all three, while `sh -c pushit`,
    `zsh -f -c pushit`, `zsh -c pushit`, `bash -c pushit`, `echo pushit | sh`, `zsh -f -c 'eval pushit; echo $(pushit)'`
    and `sh -c 'intests; pwd'` found no command and left the directory where it was, and `sh -c 'grep -c x /dev/null'`
    ran the program.  Reading the body there read other text: `sh -c 'intests; echo hi > kept.txt'`, which writes
    ./kept.txt, was read as a write to tests/kept.txt.

    This is the `held` mark of line_aliases.AliasView, which analyse_new_shell clears and every text parsed inside it
    inherits (SPD-290), and not line_aliases.held_standing, which a snapshot body's `early` mark also clears: the
    snapshot defines its functions before its aliases, so a body's own words expand none of its aliases, but every one of
    its functions stands when a body runs.

    SPD-301: a new zsh that sources the user's startup files, where the profile's functions live, holds them too
    (shell_start): every one where it sources all the files the snapshot came from, and where it sources part of them
    (line_aliases.held_partly), each read as a function that may or may not stand there (read_shell_name)."""
    view = a.alias_view
    return view is None or bool(view.held)


# SPD-301: which of the user's startup files a new shell sources, read from its options.  Claude Code writes its snapshot
# from `$SHELL -c -l` sourcing ~/.zshrc (read in its 2.1.282 bundle), so the snapshot holds what zsh's login files
# (.zshenv, .zprofile, .zlogin) and ~/.zshrc define.  Probed through tests/probes/shell_probe.py in zsh 5.9 and bash
# 3.2.57, HOME the probe's directory, each startup file defining an alias and a function of its own (NewShellStartupTest
# has the whole record): a plain `zsh -c` ran .zshenv's alone; `-i`, `-o interactive`, `--interactive`, `-ointeractive`,
# `-io interactive` and `-c -i` added .zshrc's; `-l`, `--login` and `-o login` added .zprofile's and .zlogin's but not
# .zshrc's; `-il` and `-o interactive -o login` ran all four; `-f`, `-o norcs`, `+o rcs` and `--no-rcs` ran none wherever
# they stood before the string, the last of `-f`/`-o rcs` winning; `-i +o interactive` and `+i` ran .zshenv's alone;
# `zsh -c gb -i` ran gb non-interactive, -i being its $0; `-ib -c` read `-c` as a script, -b ending the options.  So a zsh
# both interactive and login reads the files the snapshot came from, and its text is read as the Bash tool's line is
# (held True); one that is either alone, or plain where a .zshenv exists, reads part of them, which part the hook cannot
# tell, and is read both ways (line_aliases.PARTLY); one with rcs off reads none but /etc/zshenv (absent on macOS).
#
# bash -i ran ~/.bashrc's alias and function, bash -l ~/.bash_profile's function (no alias: a non-interactive bash expands
# none), sh -i the file $ENV names, and `--norc -i` and `--noprofile -l` ran neither.  On this Mac ~/.bashrc is absent and
# ~/.bash_profile evaluates conda's generated hook (conda's functions, PATH), none of which the zsh snapshot read.  So a
# bash, sh, dash, ksh or ash started interactive or login sources files the snapshot may share with it or not -- a
# profile both shells source is common -- and its text reads the snapshot's names both ways as well (PARTLY), more than
# runs; a name only the bash files define stays unread, as tests/test_hooks_programs.py ScriptFileTest pins it (`bash -lc
# 'git status'` reads no file a member could have written; ~/.bash_profile is the user's).  Every one of those shells and
# zsh read an option after `-c` as an option (`sh -c -x 'echo A1'` traced `echo A1`), the string being the first word
# after the options -- where the hook had read the option (`sh -c -x 'git push'` was read as `-x`); csh, tcsh and fish
# take the word after -c.
_ZSH_OPTIONS = {"interactive", "login", "rcs"}
_POSIX_SHELLS = frozenset({"sh", "bash", "dash", "ksh", "ash"})
_NEXT_WORD_STRING = frozenset({"csh", "tcsh", "fish"})


def shell_start(base, words):
    """(the index of a shell command's first operand -- its `-c` string where it has one -- or len(words); whether it
    has `-c`; line_aliases.AliasView's `held` for its text, True, False or PARTLY) for the shell `base` and its words as
    the line spells them (SPD-301, above).  zsh's single letters `-i`, `-l`, `-f` and `+` of each, `-o NAME` (glued or
    not) and `--NAME`, a name read as zsh reads it (case, `-` and `_` ignored, `no` before it turning it off); bash's
    `--login`, `--norc`, `--noprofile`, and the value `--rcfile`, `--init-file`, `-o` and `-O` take."""
    is_zsh, n = base == "zsh", len(words)
    on = {"interactive": False, "login": False, "rcs": True, "norc": False, "noprofile": False}
    i, dash_c = 1, False
    while i < n:
        w = prepare.deglob(words[i])
        if w in ("--", "-"):
            i += 1
            break
        if len(w) < 2 or w[0] not in "-+" or dash_c and base in _NEXT_WORD_STRING:
            break
        i += 1
        if w.startswith("--"):
            name = w[2:].lower()
            if name in ("rcfile", "init-file") and not is_zsh:  # bash's, whose file stdin_text.startup_files reads
                i += 1
            elif name in ("login", "norc", "noprofile") and not is_zsh:
                on[name] = True
            elif is_zsh:
                _zsh_option(on, name, True)
            continue
        ends = False
        for k, letter in enumerate(w[1:]):
            if letter == "c":
                dash_c = True
            elif letter in "oO":
                value = w[k + 2 :]
                if not value and i < n:
                    value, i = prepare.deglob(words[i]), i + 1
                if is_zsh and letter == "o":
                    _zsh_option(on, value, w[0] == "-")
                break
            elif letter in "il":
                on["interactive" if letter == "i" else "login"] = w[0] == "-"
            elif letter == "f" and is_zsh:
                on["rcs"] = w[0] == "+"
            elif letter == "b" and is_zsh:
                ends = True
        if ends:
            break
    if is_zsh:
        return i, dash_c, _zsh_held(on)
    sources = base in _POSIX_SHELLS and (on["interactive"] and not (base == "bash" and on["norc"])
                                         or on["login"] and not (base == "bash" and on["noprofile"]))
    return i, dash_c, line_aliases.PARTLY if sources else False


def _zsh_option(on, name, value):
    """Set one of zsh's options shell_start reads, `name` as zsh reads an option's name, `value` for `-o`, not for `+o`."""
    name = name.lower().replace("_", "").replace("-", "")  # `--no-rcs` ran none (probed)
    if name not in _ZSH_OPTIONS and name.startswith("no") and name[2:] in _ZSH_OPTIONS:
        name, value = name[2:], not value
    if name in _ZSH_OPTIONS:
        on[name] = value


def _zsh_held(on):
    """AliasView.held for the text of a zsh started with the options shell_start read (above): True where it sources every
    startup file the snapshot came from, PARTLY where it sources part of them, False where it sources none."""
    if on["rcs"] and on["interactive"] and on["login"]:
        return True
    if on["rcs"] and (on["interactive"] or on["login"]):
        return line_aliases.PARTLY
    home = os.environ.get("ZDOTDIR") or os.environ.get("HOME") or os.path.expanduser("~")
    files = ["/etc/zshenv"] + ([os.path.join(home, ".zshenv")] if on["rcs"] else [])
    return line_aliases.PARTLY if any(os.path.exists(f) for f in files) else False


# SPD-291: which shells read their text a line at a time, parsing each line once the lines before it ran, so an alias one
# line defines stands in the next line's words (never in its own line's: the shell parses a whole line, and every line a
# compound command spans, before it runs any of it).  Probed through tests/probes/shell_probe.py (2026-09-24), each of
# zsh 5.9 -f -o nobareglobqual, zsh 5.9 -f and bash 3.2.57 driving the shells below, with `alias ls="echo ALIASED"`:
# `/bin/sh -c`, `/bin/dash -c` and `/bin/ksh -c` with the alias and `ls -d /` on two lines printed `ALIASED -d /`, on one
# line `/`; `/bin/zsh -c` printed `/` either way (it parses the string whole, SPD-286), and so did `/bin/bash -c`, whose
# expand_aliases is off in a shell that is not interactive, until `shopt -s expand_aliases` on a line before or `-O
# expand_aliases` turned it on (`ALIASED -d /`); fed a script by a pipe, with and without `-s`, sh, zsh, dash and ksh
# printed `/` for `alias ...; ls -d /` and `ALIASED -d /` for the `ls -d /` on the next line, and bash `/` twice, or `/`
# then ALIASED after a `shopt -s expand_aliases` line (tests/test_hooks_input.py LineAtATimeAliasTest has the rest).  On
# macOS /bin/sh runs the shell /private/var/select/sh names -- /bin/bash here, in POSIX mode, `/bin/sh --version` printing
# GNU bash 3.2.57 -- which may be set to /bin/zsh or /bin/dash, and zsh run as sh parsed the two-line `-c` string whole
# (`zsh --emulate sh -f -c` printed `/`); a script on its standard input each of them reads a line at a time.
_LINE_SHELLS = frozenset({"dash", "ksh", "ash"})


def text_lines(base, fed):
    """How the shell `base` reads a text of its own -- its `-c` string, or, `fed`, a script it reads on standard input
    (above): True a line at a time (dash, ksh and ash, and sh and zsh fed one); syntax.LINES_BOTH for bash, a line at a
    time where expand_aliases is on and with no alias at all where it is off, as it is unless something turns it on (in
    eval's words and its substitutions too, which the whole reading does not stand for: expands_no_alias, SPD-322), and
    for sh's `-c` string, which the shell /bin/sh stands for may read either way; False whole (zsh's `-c` string; csh,
    tcsh and fish, whose `alias` the reader does not read)."""
    if base == "bash" or base == "sh" and not fed:
        return syntax.LINES_BOTH
    return base in _LINE_SHELLS or fed and base in ("sh", "zsh")


def expands_no_alias(base):
    """Whether the shell `base` may expand no alias anywhere in its text -- eval's words, a `$( )`, backtick or `<( )`
    body and a trap's action included, which the whole reading text_lines gives it reads as text parsed as it runs, with
    the text's own aliases (SPD-322, line_aliases.spelled_too has the probe): bash, whose expand_aliases is off unless it
    is interactive, in POSIX mode or turned on, and which is read both ways wherever it may be (line_aliases.AliasView
    .bare).  The others expand one in eval's words (probed through tests/probes/shell_probe.py, 2026-09-25: after `alias
    ls="echo ALIASED"`, `eval ls -d /` printed `ALIASED -d /` in /bin/sh -- bash 3.2.57 in POSIX mode -- /bin/dash,
    /bin/ksh and /bin/zsh, from a `-c` string and fed by a pipe alike)."""
    return base == "bash"


def tool_lines(a):
    """How the Bash tool's own shell reads the member's line (SPD-291): Claude Code sources its snapshot and runs the line
    through eval, which zsh parses whole (line_aliases, SPD-286) and bash 3.2 with `shopt -s expand_aliases`, which a bash
    snapshot's option lines run, a line at a time (probed through tests/probes/shell_probe.py: `bash -c 'shopt -s
    expand_aliases; eval "$(printf ...)"'` with the alias and `zq hi` on two lines printed `ALIASED hi`, on one line
    `zq: command not found`; zsh -f's eval of the two lines `command not found: zq`).  The snapshot's name says its shell
    (`snapshot-<shell>-<stamp>-<id>.sh`, hooks/snapshots), and any of them may be the one a session sources: False where
    none is bash's, the line read whole as it always was; else syntax.LINES_BOTH, zsh's reading whole and the other a
    line at a time -- a zsh snapshot may be the one sourced, and a bash one that leaves expand_aliases off (a line only
    `shopt -s` in the snapshot turns on) expands no alias at all, which the whole reading stands for too."""
    for path in snapshots.shell_table(a.home).files:
        name = os.path.basename(path)
        if name.startswith(snapshots.SNAPSHOT_PREFIX + "bash-"):
            return syntax.LINES_BOTH
    return False


# SPD-323: a shell parses eval's words, a trap's action and a `$( )` or backtick body as its text runs (line_aliases,
# SPD-286), and sh and bash parse such a text a line at a time too, each line once the lines before it ran, where zsh
# parses it whole.  Probed through tests/probes/shell_probe.py (2026-09-25), GNU bash 3.2.57 driving each shell with the
# text on two lines, `alias ls='echo ALIASED'` then `unalias ls; ls -d /`: eval's printed `ALIASED -d /` in /bin/sh
# (bash 3.2.57 in POSIX mode), /bin/dash and /bin/bash after `shopt -s expand_aliases`, from `-c` and fed by a pipe, and
# `/` in /bin/zsh -f (from `-c` and fed) and /bin/ksh (AJM 93u+ 2012-08-01), and in /bin/bash with expand_aliases off
# (line_aliases.spelled_too reads that); with `alias ls='echo SECOND'; ls -d /` for the second line, ALIASED in the
# first three; a trap's action read as eval's in each shell; a `$( )` and a backtick body printed ALIASED in sh and bash,
# `/` in zsh, ksh and dash, which parses the body with the line around it (SPD-326's), and so did `$(alias ls=...
# <newline>ls -d /)`, the alias on the body's first line, zsh -f and -f -o nobareglobqual driving them too; a `<( )`
# body printed ALIASED in bash, `/` in zsh and ksh, and is a syntax error in sh and dash.  With the unalias alone on the
# second line and `ls -d /` on a third, eval's, a trap's, a `$( )` body's and a `<( )` body's printed `/` in each.
_WHOLE_PARSED = frozenset({"zsh", "ksh", "csh", "tcsh", "fish"})
_LINE_PARSED = frozenset({"dash", "ash"})


def parsed_lines(a, substitution=False):
    """How the shell that runs the text being read reads a text it parses as that text runs -- eval's words and a trap's
    action, or (`substitution`) a `$( )` or backtick body -- as analyse.analyse_command's `lines` says it (SPD-323,
    above): the shell line_aliases.AliasView.shell names, the Bash tool's own read as tool_lines reads its line; whole in
    zsh and ksh (False, and in csh, tcsh and fish, whose `alias` the reader does not read); a line at a time in dash and
    ash (True), but for a substitution's body, which they parse with the line around it, read whole as before; and in
    bash, sh and a shell the hook cannot name either way (syntax.LINES_BOTH): bash may expand no alias at all, and the
    shell /bin/sh stands for may be zsh."""
    view = a.alias_view
    shell = line_aliases.TOOL_SHELL if view is None else view.shell
    if shell == line_aliases.TOOL_SHELL:
        return tool_lines(a)
    if shell in _LINE_PARSED:
        return not substitution
    return shell not in _WHOLE_PARSED and syntax.LINES_BOTH


# SPD-326: dash and ash parse a `$( )` or backtick body with the text around it (above), so the body's words expand the
# aliases that text was parsed with, not the ones its own line defined, changed or cleared before the body.  Probed
# through tests/probes/shell_probe.py (2026-09-25), zsh 5.9 -f -o nobareglobqual, -f and bash 3.2.57 each driving
# /bin/dash, after `alias ls="echo ALIASED"` on the same line: `echo "[$(ls -d /)]"` printed `[/]`, and so did its
# backtick form and the text fed by a pipe; on the next line `[ALIASED -d /]`, and eval's `echo [\$(ls -d /)]` too, eval
# parsing its words when it runs; /bin/sh (bash in POSIX mode), ksh and zsh printed `[ALIASED -d /]` on the same line.
# The shell /bin/sh stands for may be dash, and so may one the hook cannot name.
_SUBSTITUTION_WITH_TEXT = _LINE_PARSED | {"sh", ""}


def substitution_view(a):
    """The alias table a `$( )` or backtick body about to be read may have been parsed with besides the table as it stands
    (SPD-326, above): where the shell running the text being read may be dash, the AliasView of that text -- its line's,
    as ShellWalk.new_line opens it, the new shell's where it is the text's first line, eval's or an enclosing body's --
    read whole, as dash parses the body with it (AliasView.whole); None where that view reads what the table as it
    stands does, or the shell parses the body when it runs it (bash, zsh, ksh and the Bash tool's own)."""
    view = a.alias_view
    if view is None or view.shell not in _SUBSTITUTION_WITH_TEXT:
        return None
    now = line_aliases.AliasView(a, False)
    if (now.table, now.doubted, now.unknown) == (view.table, view.doubted, view.unknown):
        return None
    return view.whole()


# ShellAnalysis.body_dirs' marks for a function body whose reading is under way, and for one a call inside that reading
# reached again from the same state (read_once)
_READING, _REENTERED = object(), object()


def read_once(a, cmd, read, before, run):
    """Read a function's body for a call once per `read` -- its text, the call's words where they reach it, its standard
    input and the state it starts from -- with `run`, and return the directories the line is in after it (SPD-252): the
    ones that reading left, given again to a call that reads exactly as it did (after a subshell put the directory back,
    in the line's second walk), and, for a call inside its own reading from where that reading started, the directories
    it started in -- left unknown once the reading is over if the body moves them, since the shell would move them again
    from where the inner call returns."""
    reads = a.bodies_read.setdefault(cmd, set())
    if read in reads:
        after = a.body_dirs.get((cmd, read), before)
        if after is _READING or after is _REENTERED:  # a call inside its own reading, from where that reading started
            a.body_dirs[cmd, read], after = _REENTERED, before
        a.dir_moves += after != before  # the move given again, as the reading counted it
        return after
    reads.add(read)
    a.body_dirs[cmd, read] = _READING
    run()
    after = None if a.body_dirs[cmd, read] is _REENTERED and a.cwds != before else a.cwds
    a.body_dirs[cmd, read] = after
    return after


def read_function(a, cmd, body, depth, stdin, fed):
    """A call's reading of a function body the line defines (SPD-277, line_functions.read_call), a LineBody: read as a body
    the shell's snapshot holds is (read_body), in a scope of its own, from the state the call starts in, once per that state
    and standard input (read_once) -- but as the member's own text, which no finding is pruned from -- and the directories
    the line is in after it.  Its readings are bounded as SPD-212 bounded them: a reading on a call's input counts
    against positional.READINGS_PER_NAME across the analysis (ShellAnalysis.body_walks), past which a call's input is
    read as input the line does not spell, refused a member a shell or an interpreter reading it; and a body read from
    that many states reads a call from any other once more, from directories the hook cannot follow, and gives every later
    one that reading.  A call inside the body's own reading, from another state, is read a level deeper, so a body that
    calls itself from ever other states stops at analyse.READING_DEPTH.

    Returns (the directories, the pair of texts that reading printed, SPD-272: ShellAnalysis.body_printed), the texts
    kept per reading as the directories are, so a call that reads exactly as one read before prints what it printed,
    and one inside its own reading from where that reading started prints text the hook cannot spell (None)."""
    before, counted = a.cwds, False
    if fed and a.body_walks >= positional.READINGS_PER_NAME:
        stdin = None  # past the bound: input the line does not spell (SPD-212)
    elif fed:
        counted = True
    # keyed by the body, not the name `cmd`, whose readings of a snapshot function of the same name count apart; and by
    # the writes the line has made so far, which a reading holds a file it runs from against (a sed or awk -f script,
    # SPD-151; an archive, tree_writes): `f; echo x > f.awk; f`, where f runs `awk -f f.awk`, reads the second call again
    # -- but not for a call inside the body's own reading, which the body's own writes would read again without end
    writes = None if body.active else (len(a.redirects), len(a.git_writes), len(a.arg_writes))
    read = (cmd, stdin_text.reading_key(stdin, fed), a.reading_state(), writes)
    if read not in a.bodies_read.get(body, ()) and body.readings >= positional.READINGS_PER_NAME:
        read = (cmd, stdin_text.reading_key(stdin, fed), None, None)

    def run():
        a.body_walks += counted
        body.readings += 1
        if read[2] is None:
            a.cwds = None
            a.dir_moves += 1
        body.active += 1
        marks = [len(getattr(a, field)) for field in _PRUNED] if a.shell_reading else None
        a.read_printed = None  # a reading past analyse.READING_DEPTH walks nothing, and prints text the hook cannot spell
        try:
            read_body(a, body, depth + (body.active > 1), stdin, fed)
        finally:
            body.active -= 1
        a.body_printed[body, read] = a.read_printed
        if marks is not None:
            # called from the shell's own text (a snapshot function's body): what the member's body earns is the member's,
            # which analyse_shell_text's prune keeps
            a.shell_own.update(id(entry) for field, mark in zip(_PRUNED, marks) for entry in getattr(a, field)[mark:])

    after = read_once(a, body, read, before, run)
    return after, a.body_printed.get((body, read))


# The analysis's lists analyse_shell_text prunes of what falls on a member for text it did not write
_PRUNED = ("findings", "redirects", "git_writes", "arg_writes")


def analyse_shell_text(a, text, depth, own_words, own_process=False, substituted=None, stdin=None, fed=False, filled=(),
                       shadow=False):
    """Read text the shell itself holds: an alias's body, which is the line's own text once the shell has parsed it, or a
    function's, which runs in the line's shell in a scope of its own (own_process, read_body).  `own_words` are the member's own
    words of the line that reach this text, as the line's reading tokenized them -- the words after the alias the shell
    expanded, or the call's arguments, which a function receives as its positional parameters.  `substituted`, for a
    function's body: whether shell/positional set those words where the body reads them (True), or left it as it stands
    because it cannot follow them there (False).  `shadow`: the body is one of the harness's shadows (read_body).

    The findings are the findings the text would earn on the line, minus two kinds that would fall on a member for text it
    did not write and cannot change: the ones that say only that the hook cannot read a word (syntax.SHELL_TEXT_TOLERATED)
    and a write whose target the hook cannot resolve.  Claude Code shadows find, grep, rg and pkill with functions that
    dispatch through `"$_cc_bin"` and write through `$data`-shaped names of their own; without both prunes a member would
    be refused every `grep` it runs.

    What the member's own words earn is never pruned, since the member wrote those and can spell them out (SPD-203):

    - a target the member supplied (member_supplied): an alias's expansion is its body followed by the member's own words,
      and a function's `$@` is them, so pruning on the target's spelling alone let an alias launder exactly what the
      unresolvable-target rule exists to refuse -- `md $HOME/planted` recorded no write while `mkdir -p $HOME/planted`
      was refused;
    - a finding that spells one of the member's words the hook cannot read (spells_member_word): `_ $(echo git) push`
      (`_='sudo '`) is `sudo $(echo git) push`, whose command word refuses a member as it does spelled out;
    - for a call with words, every finding of a function's body the substitution could not set them into
      (substituted=False), and one that spells a reference to them the substitution left where another reading takes
      it (`sh -c 'git "$@"' _ "$@"`): the member's words reach those where the hook does not follow them;
    - every finding, of every kind, and every write that names a variable the member filled (names_member_var): one the
      line assigned before the text (SPD-205), one a value naming such a variable fills in the text (SPD-253: the
      harness's `_cc_bin="${CLAUDE_CODE_EXECPATH:-}"`), and, where the call has words or standard input the member gave
      it, one they fill -- a loop over them, a substitution shell/positional set them in (`filled`, SPD-258), a builtin
      that reads them, another such variable (expansions.fill_from).  The command word `"$_cc_bin"` of the harness's
      shadows is kept too when the line fills `_cc_bin` (SPD-248: `for f in "$@"; do $f push; done` ran `git push`
      for `loopcmd git`), and stays the body's own where only the environment and the body's literals do.

    A concrete file the text writes is held to the path rule for the caller, as an alias's redirection is anywhere else.
    Only the outermost of these readings prunes, so a nested one never drops what the reading closest to the member's
    words keeps; a function's body nested in it marks its own findings to keep while the outermost reading has words.

    A target the text's own line settles is resolved before this sees it, so it is a concrete file and the prune
    does not reach it: a body that writes `$data` after assigning it goes to the path rule like any spelled path, while the
    harness's `"$_cc_bin"` and the environment it reads, which no line settles, stay as unresolvable as they were."""
    outermost = a.shell_reading == 0
    # what the line's shell holds from the line before this text (SPD-205): what its own text assigned and what a function
    # body it called assigned to a name the body did not declare local -- never a body's local, which is gone (SPD-246)
    line_vars = frozenset(a.line_assigned) if outermost else frozenset()
    if outermost:
        a.shell_words = list(own_words)
        a.member_vars = set(a.line_members)  # ... and an earlier body's local that its call's words filled is gone too
        a.shell_line_vars, a.line_filled, a.filled_texts = line_vars, set(), set()  # what fills a body's variable (SPD-258)
    a.filled_texts.update(filled)
    marks = (len(a.findings), len(a.redirects), len(a.git_writes), len(a.arg_writes))
    a.shell_reading += 1
    try:
        if own_process:
            # not analyse_isolated's cache: a body read before without the member's words had its findings pruned, and
            # read_shell_name reads each call's once.  On the call's standard input, so a body running a shell or an
            # interpreter reads the file the call feeds it (SPD-215); in a scope of its own, for its locals (SPD-246)
            read_body(a, text, depth, stdin, fed, shadow)
        else:
            analyse.analyse_command(text, a, depth, stdin, fed)
    finally:
        a.shell_reading -= 1
    if substituted is not None and own_words and a.shell_words:
        a.shell_kept.update(i for i in range(marks[0], len(a.findings)) if a.findings[i][0] in syntax.SHELL_TEXT_TOLERATED
                            and (not substituted or positional.REFERENCE_RE.search(a.findings[i][1])))
    if not outermost:
        return
    unread_words = [member_spelling(w) for w in own_words if unreadable_word(w)]
    # the variables the line's shell held from the line before the text (always the member's: line_vars) and those a value
    # naming one of them filled in the text (line_filled, SPD-253), and, where the call passed words or standard input,
    # those they filled inside the body (SPD-205, SPD-258) or an earlier body's global they filled (line_members, SPD-246);
    # with neither there is nothing of the member's to fill one
    member = line_vars | a.line_filled | (a.member_vars if own_words or fed else frozenset())
    kept, a.shell_kept, a.shell_words = a.shell_kept, set(), []
    own, a.shell_own = a.shell_own, set()  # the entries of a function body the line defines, the member's text (SPD-277)
    a.shell_line_vars = frozenset()
    a.findings[marks[0]:] = [f for i, f in enumerate(a.findings[marks[0]:], marks[0])
                             if f[0] not in syntax.SHELL_TEXT_TOLERATED or i in kept or id(f) in own or names_member_var(f[1], member)
                             or any(spells_member_word(f[1], spelled) for spelled in unread_words)]
    supplied = frozenset(prepare.deglob(w) for w in own_words)
    a.redirects[marks[1]:] = [e for e in a.redirects[marks[1]:] if id(e) in own or keeps_write(e[0], supplied, member)]
    a.git_writes[marks[2]:] = [e for e in a.git_writes[marks[2]:] if id(e) in own or keeps_write(e[1], supplied, member)]
    a.arg_writes[marks[3]:] = [e for e in a.arg_writes[marks[3]:] if id(e) in own or keeps_write(e[1], supplied, member)]


def names_member_var(detail, member):
    """Whether a finding or a write target names a variable the member filled (SPD-205): its detail -- a string, or a
    tuple's strings -- holds a `$NAME` (syntax.READ_NAME_RE, read with its glob sentinels taken off) whose NAME is in
    `member`, analyse_shell_text's set.  Such a variable is the member's own, so the finding is kept, not dropped as the
    body's, and refuses the member on doubt as the same reference does on a plain line -- whatever its kind (SPD-258),
    the command word a variable gives among them, which the harness's own shadows spell `"$_cc_bin"` and which stays
    theirs wherever nothing of the member's fills `_cc_bin`."""
    if not member:
        return False
    texts = (detail,) if isinstance(detail, str) else detail if isinstance(detail, tuple) else ()
    return any(name in member for text in texts if isinstance(text, str)
               for name in syntax.READ_NAME_RE.findall(prepare.deglob(text)))


def unreadable_word(word):
    """Whether the hook cannot read this word of the member's as a fixed string: it holds an expansion, a substitution or
    a glob the shell expands."""
    return expansions.expansion_word(word) or globbing.active_glob_word(word)


def member_spelling(word):
    """How a finding spells a word of the member's that the hook cannot read: a substitution as `$(...)`, as
    resolve_expansion shows every word that holds one, and anything else as the line spells it."""
    return "$(...)" if hookio.SUBST in word else prepare.deglob(word)


def spells_member_word(detail, spelled):
    """Whether a finding's detail is the member's word the hook cannot read: the word itself, or -- for an expansion,
    which a body may glue to text of its own (`--author=$1`) -- a longer word that holds it, not followed by a character
    that would lengthen its name.  A glob of the member's is its whole word: `*` is not the body's own `-*-config*`."""
    if detail == spelled:
        return True
    if "$" not in spelled:
        return False
    at = detail.find(spelled)
    while at >= 0:
        after = detail[at + len(spelled) : at + len(spelled) + 1]
        if not (after and (after.isalnum() or after == "_") and (spelled[-1].isalnum() or spelled[-1] == "_")):
            return True
        at = detail.find(spelled, at + 1)
    return False


def keeps_write(target, supplied, member=frozenset()):
    """Whether a write this text would make is read as it stands: every target the hook can resolve, every one the
    member's own words supplied, and every one naming a variable the member filled (`member`, names_member_var, SPD-258),
    whatever the hook can make of it."""
    return not unresolvable_write(target) or member_supplied(target, supplied) or names_member_var(target, member)


def member_supplied(target, supplied):
    """True when this target is one the member's own line gave the text: one of the words that followed the alias the
    shell expanded, or a positional parameter, which is how a function receives them.  An own word that is itself
    unresolvable is also read where it stands inside a longer target, since that is the spelling the refusal is for."""
    spelled = prepare.deglob(target)
    if syntax.POSITIONAL_RE.search(spelled) is not None or spelled in supplied:
        return True
    return any(word in spelled for word in supplied if unresolvable_write(word))


def unresolvable_write(target):
    """True when the hook cannot tell which file this target names: it holds a variable or a substitution, the test
    bash_reason makes of a redirection target before it refuses a member for one."""
    return "$" in target or "`" in target or hookio.SUBST in target or syntax.unknown_operand(target)
