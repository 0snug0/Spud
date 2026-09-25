"""shell/line_functions: LineBody: a function body the line defines, and its reading at each call (SPD-277).

The seam SPD-280 took out of shell/walk: walk takes a definition's tokens down as a LineBody (ShellWalk.define through
settle_definitions, which share the walk's frames, position and lifted text and stay with it); a call reads it here
(read_call, through held_text.read_function), analyse_command walks it (read_line_body), and zsh's `functions`
parameter and its hook arrays bind and queue one (assign_function, hook_functions).  The text a call's reading prints
(SPD-272) is left here too, in ShellAnalysis.read_printed and call_printed.  zsh's `functions -c` binds a copy of one
(copy_function, SPD-279), and `unset -f` and its kin remove the bodies bound to a name (remove_functions, SPD-281), which
line_bodies, the bodies a call runs, reads.  Past 250 lines as one table: every function here binds, reads or settles the
LineBody objects ShellAnalysis.function_bodies holds, and a copy or a removal is a binding read by the same line_bodies."""

import functools

from . import (analyse, arg_writes, assigning_builtins, assignment_words, directories, expansions, held_text, prepare,
               script_files, stdin_text, syntax, unread, walk)
from ..hooks import snapshots


class LineBody:
    """A function body the line defines (SPD-277): read at each call from the state the call starts in -- its directories,
    its variables, its options, its standard input -- as a body the shell's snapshot holds is (read_call, SPD-252), and,
    where no call reads it, in place where it is defined, as every body was read before, so a function defined and never
    called still has its Law 7 verbs found and its findings keep their place on the line.

    `names`, the names the definition binds; `line`, the reading of the line it stands in (walk.walk_line), while whose walk a
    call hands the body its input (SPD-212); `serial`, its number in the analysis, the order a call reads several bodies
    of one name in.  Its text, once the walk has read to its end: `tokens`, the line's tokens from the definition's header
    to the end of its own redirections, which each call opens where it runs (probed), `inner`, `docs` and `expanded`, the
    substitution bodies and here-documents those tokens lift, `glued`, which reading of the line (zsh's, or bash's) they
    are, and `into`, whether the body is a compound command, where a call's input stands as a pipe into it does (SPD-212:
    zsh reads the call's and then the definition's own); or `text`, a body zsh's `functions` parameter is handed (SPD-278).

    `pending`, what its reading in place added to the analysis's lists (walk._RECORDS), taken out when the definition ends and
    put back where it stood when the walk ends (walk.ShellWalk.settle_definitions) unless a call read the body (`called`) --
    and `marks` and `caches`, those lists' lengths and the reading's caches as the definition began, put back with it; all
    three None for a definition inside another's body, whose reading in place is that one's.  `readings` and `active`: how
    many readings of it calls have made, and how many are under way (held_text.read_function).  `certain`: the definition
    surely runs before any call after it on the line (walk.ShellWalk.certain_definition), so a call runs this body or another
    the line defines under the name and never the command of that name (stdin_text.printed_text, SPD-272).

    `removes` (SPD-281): no body but the mark a removal leaves among a name's bodies (remove_functions), which holds no text
    and is never complete: every body bound to the name before it is gone where it is `certain` -- the removal surely
    ran in the line's shell -- and may be gone where it is not (line_bodies).  Bound as a definition is, so a `|` or a
    `&` read after it takes its certainty back as it takes a definition's (walk.ShellWalk.uncertain_element)."""

    __slots__ = ("names", "line", "serial", "start", "consumed", "tokens", "inner", "docs", "expanded", "glued", "into",
                 "text", "pending", "marks", "caches", "called", "readings", "active", "certain", "removes")

    def __init__(self, names, line, a):
        self.names, self.line = tuple(names), line
        a.body_serial += 1
        self.serial = a.body_serial
        self.start, self.consumed, self.tokens, self.text = 0, (0, 0), None, None
        self.inner, self.docs, self.expanded, self.glued, self.into = (), (), (), True, False
        self.pending = self.marks = self.caches = None
        self.called, self.readings, self.active, self.certain, self.removes = False, 0, 0, False, False

    def complete(self):
        """Whether the walk has read the body to its end, so a call can read it: not while its own reading in place is
        under way, which a call inside it reaches."""
        return self.tokens is not None or self.text is not None

    # Compared by what it is -- its number in the analysis, its names, its text -- so two analyses of one line compare
    # alike field by field (tests/test_hooks_snapshots.py HarnessShadowReadingTest); in one analysis no two share a number.
    # Hashed by what never changes, for the analysis's caches it keys (bodies_read, isolated_done).
    def __eq__(self, other):
        return isinstance(other, LineBody) and (self.serial, self.names, self.tokens, self.text) == (
            other.serial, other.names, other.tokens, other.text)

    def __hash__(self):
        return hash((self.serial, self.names))

    def __str__(self):
        return self.text if self.text is not None else " ".join(prepare.deglob(t) for t in self.tokens or ())


# read_call's answer where the line defines no body under the name
NO_BODY = object()


def read_call(a, name, depth, stdin, fed):
    """A call of `name` in command position, on standard input `stdin` (fed: whether anything stands there): each body the
    line defines under that name read as the call runs it (SPD-277) -- from the directories, the variables and the options
    the call starts from, on the input it is given (SPD-212), as a body the shell's snapshot holds is read at each call
    (held_text.read_function, SPD-252) -- and the directories the line may be in after it, each body's from the same start
    joined, or NO_BODY where the line defines none.  Probed 2026-09-24 through tests/probes/shell_probe.py in zsh 5.9 -f -o
    nobareglobqual and -f and bash 3.2.57: `f() { pwd; echo x > out1.txt; }; cd d; f` printed d and wrote d/out1.txt, and
    `h() { cd d; }; h; pwd` printed d (tests/test_hooks_words.py LineFunctionCallTest).

    A body in text whose reading is over -- an `eval` string's, which defines the function in the shell that runs the
    line -- is read at the call too, without the call's input, which is refused a member as a shell reading input the
    line does not spell is (script_files, "function"), as SPD-212 left it.  A body whose own reading in place is under way
    -- a call inside it -- is not complete, and its call reads nothing."""
    if a.deferring:
        # a call inside an action a shell runs later (a trap's, a body zsh runs by itself): a body the line defines under
        # this name after it is read as that action is (expansions.read_action, SPD-276)
        a.hook_names[name] = a.hook_names.get(name, False) or a.deferring[-1]
    found = line_bodies(a, name)[0]
    if not found:
        return NO_BODY
    before, after, texts = a.cwds, NO_BODY, []
    for body in found:
        a.cwds, given, given_fed = before, stdin, fed
        if body.line in a.walking:
            body.called = True
        else:
            if fed:
                script_files.record_script(a, "function", name)
            given, given_fed = None, False
        moved, printed = held_text.read_function(a, name, body, depth, given, given_fed)
        after = moved if after is NO_BODY else directories.union_dirs(after, moved)
        texts.append(printed or (None, None))
    # the text the call prints, either body's where the line defines more than one (SPD-272): set once every reading
    # under this call is over, for the walk.ShellWalk.finish that reads the call (stdin_text.LineCall)
    a.call_printed = (name,) + tuple(stdin_text.either_text(list(each)) for each in zip(*texts))
    return after


def read_line_body(a, body, depth, stdin, fed):
    """analyse_command's reading of a LineBody: its tokens walked as the line's own are, on the input a call hands it, or
    the text zsh's `functions` parameter was handed, read as any text is (SPD-278), a level down from the line's own so
    the reading of the raw line (held options, markers) is not made again.

    The text the reading prints (SPD-272) is left in ShellAnalysis.read_printed for held_text.read_function: the walk's,
    and the one it prints where a pipe follows the call (walk.ShellWalk.body_piped); None for a `functions` body's text,
    whose readings (zsh's and bash's) this does not follow."""
    if body.text is not None:
        analyse.analyse_command(body.text, a, max(depth, 1), stdin, fed)
        a.read_printed = None
        return a
    shell_walk = walk.walk_line(a, list(body.tokens), body.inner, body.docs, body.expanded, depth, walk.reading_start(a),
                                body.glued, stdin, fed, body.into)
    printed = shell_walk.frame_printed
    a.read_printed = (printed, printed if shell_walk.body_piped is None else shell_walk.body_piped)
    return a


def assign_function(a, found):
    """SPD-278: an assignment to zsh's `functions` parameter -- `functions[name]=body`, `functions+=(name body ...)`,
    `functions=(name body ...)` -- defines the function whose body it spells, which runs when it is called, or by itself
    for a name zsh runs so (SPD-276): each such body is bound to its name as a definition's is and read as one is
    (walk.ShellWalk.read_queued), in place where it is spelled and at each call where it runs.  A body the line does not
    spell -- a variable, a substitution, text appended to a body -- refuses a member as SPD-217 refuses text the reader did
    not read, naming the respelling: define the function with name() { ... }.  Probed 2026-09-24 in zsh 5.9 -f -o
    nobareglobqual and -f: `functions[fb]="pwd; echo x > out13.txt"; cd d; fb` printed d and wrote d/out13.txt, and
    `functions[TRAPEXIT]=...` ran at exit (tests/test_hooks_words.py FunctionsParameterBodyTest)."""
    pairs, unspelled = assignment_words.function_bodies(*found)
    if unspelled is not None:
        unread.record_unread(a, "function-body", unread.unread_shown(unspelled))
    if not a.walks:
        return
    shell_walk = a.walks[-1]
    for name, text in pairs:
        body = shell_walk.bind([name])
        body.text = text
        shell_walk.queue(body)


def hook_functions(a, names, in_line):
    """SPD-276: an assignment to zsh's zshexit_functions, chpwd_functions or zsh_directory_name_functions names functions
    zsh runs by itself (`names`, None for an element the hook cannot read, which may name any of them): each body the line
    defines under one of them, before this or after, is read as a trap's action is (expansions.read_deferred_body).  Probed
    2026-09-24 in zsh 5.9 -f -o nobareglobqual and -f: `chpwd_functions=(hk); hk() { ... }; cd d` and `hk() { ... };
    chpwd_functions+=(hk); cd d` ran hk in d, and zshexit_functions at exit."""
    names = [syntax.UNKNOWN_NAME] if names is None else names
    for name in names:
        a.hook_names[name] = a.hook_names.get(name, False) or in_line
    if not a.walks:
        return
    shell_walk, every = a.walks[-1], syntax.UNKNOWN_NAME in names
    held = {body for key in a.function_bodies if every or key in names for body in line_bodies(a, key)[0]}
    for body in sorted(held, key=_serial):
        shell_walk.queue(body, in_line)


def _serial(body):
    return body.serial


def _settled(bodies):
    """(what stands of `bodies`, a name's LineBody objects -- each body no removal surely took, incomplete ones too, and
    each removal that may not have run -- in the order the line bound them; whether a complete body surely runs at a call
    after them all)."""
    kept, sure = [], False
    for body in sorted(bodies, key=_serial):
        if body.removes and body.certain:
            kept, sure = [], False
            continue
        kept.append(body)
        if body.removes:
            sure = False
        elif body.certain and body.complete():
            sure = True
    return kept, sure


def line_bodies(a, name):
    """The bodies a call of `name` runs where the line defines it (read_call, stdin_text.printed_text), in the order the
    line bound them, each complete, and whether one of them surely runs rather than the command of that name (SPD-272's
    LineBody.certain): a removal that surely ran leaves none of those bound before it (SPD-281), and one that may not have
    run leaves them as definitions that may not have, the command's reading beside theirs."""
    kept, sure = _settled(a.function_bodies.get(name, ()))
    return [body for body in kept if not body.removes and body.complete()], sure


def merge_readings(a, zsh_bodies):
    """analyse_command's two readings of one line, zsh's `zsh_bodies` and the other shell's in ShellAnalysis.function_bodies:
    each name keeps the bodies either reading defines, each reading's removals settled in it first (_settled), so one
    reading's removal takes nothing the other's defines; where the readings differ on whether a body surely runs, a removal
    that may not have run stands after them all, so the command's reading counts beside theirs."""
    merged = {}
    for name in set(zsh_bodies) | set(a.function_bodies):
        zsh_kept, zsh_sure = _settled(zsh_bodies.get(name, ()))
        kept, sure = _settled(a.function_bodies.get(name, ()))
        bodies = set(zsh_kept) | set(kept)
        if bodies and zsh_sure != sure:
            doubt = LineBody((name,), None, a)
            doubt.removes = True
            bodies.add(doubt)
        merged[name] = bodies
    a.function_bodies.clear()
    a.function_bodies.update(merged)


def copy_function(words, a):
    """SPD-279: zsh's `functions -c OLD NEW` binds NEW to OLD's bodies as a definition of NEW binds its own -- read at each
    call (read_call), a name that shadows the command (ShellAnalysis.functions), read as a trap's action where zsh runs
    NEW by itself (expansions.runner: chpwd, zshexit, command_not_found_handler, zsh_directory_name, a hook array's name)
    -- each a definition that may not have run (LineBody.certain), since bash has no `functions` builtin.  Probed
    2026-09-24 through tests/probes/shell_probe.py in zsh 5.9 -f -o nobareglobqual and -f: `f() { echo F-in $PWD; };
    functions -c f g; cd d; g` printed d; copies to chpwd, zshexit, command_not_found_handler and zsh_directory_name ran
    as those do, but one to TRAPEXIT, TRAPUSR1 or TRAPDEBUG set no trap, zsh setting one when it defines a TRAPxxx
    function and not when it copies one; a missing OLD copied nothing (tests/test_hooks_words.py FunctionCopyTest).

    A copy the hook cannot follow refuses a member as SPD-217 refuses text it did not read, naming the respelling: a name
    the line does not spell (NEW unread binds every name, UNKNOWN_NAME), or a function the shell's snapshot defines under
    OLD where no body of the line's surely runs, whose body the hook reads only at a call of OLD's own name."""
    found = assigning_builtins.function_copy([words[0]] + [arg_writes.resolved(w, a) for w in words[1:]],
                                             functools.partial(expansions.settled_text, a))
    if found is None:
        return
    old, new = found
    bodies, sure = line_bodies(a, old) if old is not None else ([], False)
    profile = old is not None and not sure and old in snapshots.shell_table(a.home).functions
    if old is None or new is None or profile:
        unread.record_unread(a, "function-copy", unread.unread_shown(" ".join(words)))
    if _in_place(a):
        return
    if new is None:
        a.functions.add(syntax.UNKNOWN_NAME)
        return
    if not (old is None or bodies or profile or old in a.functions or syntax.UNKNOWN_NAME in a.functions):
        return  # no function of that name: zsh copies nothing (`no such function`)
    a.functions.add(new)
    if not a.walks:
        return
    shell_walk = a.walks[-1]
    in_line = expansions.runner([] if new.startswith("TRAP") else [new], a)
    for body in bodies:
        copy = shell_walk.bind([new])
        copy.line, copy.tokens, copy.text, copy.inner, copy.docs = body.line, body.tokens, body.text, body.inner, body.docs
        copy.expanded, copy.glued, copy.into = body.expanded, body.glued, body.into
        if in_line is not None:
            shell_walk.queue(copy, in_line)


def _in_place(a):
    """Whether a walk is reading a definition's body in place (walk.ShellWalk.begin_body), where it runs nothing: a copy
    or a removal there binds nothing, and a call of that function reads it again where it runs (read_call)."""
    return any(each.capturing for each in a.walks)


# The builtins that may remove a shell function (SPD-281, assigning_builtins.function_removals)
REMOVING = frozenset(("unset", "unfunction", "unhash", "disable"))


def remove_functions(words, a, effect):
    """SPD-281: `unset -f NAME`, zsh's `unfunction`, `unhash -f` and `disable -f`, and bash's plain `unset NAME` remove the
    function NAME, so a call after them runs the command of that name: each name's bodies are marked removed from here on
    (LineBody.removes, read by line_bodies) -- surely, and the name no longer shadows the command (ShellAnalysis.functions),
    where both shells remove it and the removal surely runs in the line's shell (`effect`, and the place a definition's
    certainty is read from, walk.ShellWalk.certain_definition, before a `|` or a `&` too); maybe, the bodies read beside
    the command's reading, everywhere else, and for every name the line defines where the builtin may remove any.  Probed
    2026-09-24 as assigning_builtins.function_removals says, and: a removal before `&` or `|`, in `{ ...; } &` or in a
    subshell did not reach a call after it, while one in a called body or an eval did (tests/test_hooks_words.py
    FunctionRemovalTest)."""
    found = assigning_builtins.function_removals([words[0]] + [arg_writes.resolved(w, a) for w in words[1:]],
                                                 functools.partial(expansions.settled_text, a))
    if found is None or not a.walks or _in_place(a):
        return
    names, sure = found
    shell_walk = a.walks[-1]
    if names is None:
        names, sure = list(a.function_bodies), False
    sure = sure and effect == "shell" and not a.unsure and shell_walk.certain_definition()
    held = [name for name in dict.fromkeys(names) if name in a.function_bodies]
    if held:
        mark = shell_walk.bind(held)
        mark.removes, mark.certain = True, sure
    if sure:
        a.functions.difference_update(names)
