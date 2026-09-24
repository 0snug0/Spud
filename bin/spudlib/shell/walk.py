"""shell/walk: ShellFrame and ShellWalk: one pass over a line's tokens."""

from . import (analyse, assignment_words, directories, globbing, heredocs, loop_bindings, positional, prepare, reevaluation,
               script_files, stdin_text, syntax, unread)
from ..hooks import hookio

# The loops whose header names a variable, one header grammar to zsh (its parser's par_for; ShellWalk.names_end)
_NAMED_LOOPS = ("for", "select", "foreach")
# The sentinel mark_zsh_patterns leaves for the `(` inside an arithmetic command's `((`, so the token after its outer
# `(` begins with it and no other subshell's does (SPD-177): the walk tells an arithmetic condition from a plain one.
_ARITH_OPEN = syntax._ARITH_SENTINELS["("]
# The word ShellWalk.pop puts among a command's words for the file name a `<( list )` hands it (SPD-145): hookio.SUBST,
# which every reader of it takes for a word the line does not spell, marked (syntax.PROCSUB_MARK) so that
# ShellWalk.consume pairs it with no lifted body.  As SUBST alone it took the first `$( )` after it on the line, analysed
# with the process substitution's command before any cd between them, and the `$( )` it belonged to found none (SPD-190).
PROCSUB_FILE = hookio.SUBST + syntax.PROCSUB_MARK


def _value_may_start_with_dash(word):
    """Whether a masked word, as the shell expands it, may start with `-`: spelled so, an expansion or an operand
    the line does not spell at its start, or a glob or brace list that may expand to such a word."""
    if word.startswith("-") or word.startswith((hookio.SUBST, "`")) or word[:1] in (syntax.FIND_PATH, syntax.INPUT_OPERAND, syntax.ANY_PATH):
        return True
    if word.startswith("$") and not word.startswith("$" + syntax._LITERAL_DOLLAR):
        return True
    return globbing.active_glob_word(word) and (globbing.may_start_with_dash(word) or word.startswith("{"))


def _function_names(words):
    """The names a function definition binds, deglobbed: the name words that reached a `name ()` or
    `function name {` header -- `name`, both of zsh's several names in `a b () { ... }`, `function name` and the
    `name` of `function name () { ... }` (its `()` is dropped).  Deglobbed as hashed_names is, so `g?t` counts for git."""
    names = []
    for w in words:
        name = prepare.deglob(w)
        if name.strip("() "):  # not the header's parentheses
            names.append(name)
    return names


class ShellFrame:
    """An open compound command: its kind, the word that closes it, the directories it started in, the directories any of
    its branches ended in so far, and the enclosing list's state to restore.

    The kinds: "group" a `{ list }`, "sub" one that runs in its own process (a `( list )` subshell, and a coproc's
    `{ list }` group, whose fork the `coproc` before it makes), "loop" a for, select, repeat, foreach, while or until,
    "cond" an if, "case" a case, "func" a function body.  A foreach's closer is `end` until its body turns out to be a
    `do ... done` (resolve_body)."""

    __slots__ = ("kind", "closer", "saved", "seen", "outer", "pattern", "mark", "body", "funcs", "printed", "earlier", "stdin", "prints",
                 "procsub", "serial", "bare", "defines", "arith", "bound", "form")

    def __init__(self, kind, closer, saved, outer, mark=0, funcs=None):
        self.kind, self.closer, self.saved, self.seen, self.outer = kind, closer, saved, saved, outer
        self.pattern = kind == "case"  # a case command reads a pattern first, and again after each ;;
        self.mark = mark  # how many assignments the line had made when it opened
        # the functions defined before a `( ... )` subshell opened, with their bodies (ShellAnalysis.functions and
        # function_bodies), restored when it closes; None for every other frame kind, whose function definitions reach a
        # call after it and are kept.
        self.funcs = funcs
        # the function definition whose body this compound command is (ShellWalk.define), or None (SPD-212)
        self.defines = None
        # a loop's or a conditional's body form.  None for anything but a for, select, repeat or foreach (which starts
        # at "header") and an if, while or until (which starts at "cond", its condition list); then "pending" or "cond-pending"
        # once that is complete, until the body's first word, and then "long" (`do ... done`, `then ... fi`, a foreach's list
        # up to its `end`), "compound" (a `{ ... }` or `( ... )` body; a foreach's `{ ... }` only) or "sublist" (zsh's
        # SHORT_LOOPS and SHORT_REPEAT: one and-or list, closing this frame where the list ends).  A loop's compound body
        # closes the loop with it; a conditional's becomes "sublist", since an `else` or an `elif` may still follow it
        # (probed: `if [[ -n x ]] { echo a } else { echo b }` ran).  An `elif` puts a conditional's frame back to "cond",
        # the elif's own condition list, from any of these (see branch).
        self.body = None
        # the text the pipeline element around it had printed when it opened, the text the lists before that
        # element in the enclosing compound command had printed, and that element's standard input with the one every
        # list inside this compound starts from -- all of it put back by ShellWalk.pop.  `prints`: what the commands in
        # it print reaches the enclosing element's own output -- not so for a function body, which prints when it is
        # called, nor for a process substitution, whose output goes to the file it stands for.
        # `stdin` carries two flags beside those two texts: whether anything at all stands on that input, and a third,
        # whether that element's pipe fed it (ShellWalk.pipe_feeds).
        self.printed, self.earlier, self.stdin, self.prints = "", "", (None, None, False, False, False), kind != "func"
        # an input process substitution, `<( list )`: the command around it is handed a file name it stands for
        # (/dev/fd/N), a word the line does not spell, which ShellWalk.pop puts among that command's words as PROCSUB_FILE
        # (SPD-145: `bash <(curl ...)` runs that file's text as a script; SPD-190).
        self.procsub = False
        # which compound command of the walk this is, counting from 0 in the order they open -- the same in every walk of
        # one line's tokens, which is how walk_line hands the second walk each compound's input (SPD-210) -- and whether
        # it opens a command, no word before it, so that the words after its closer are its own redirections: not so for
        # a process substitution, nor for a `(` after words.
        self.serial, self.bare = 0, False
        # this `( ... )` frame is the subshell mark_zsh_patterns leaves for an arithmetic command `(( ... ))` (its outer
        # parenthesis kept, its inside marked), so its close ends a condition it stands at the end of (SPD-177)
        self.arith = False
        # the for loop's variable shell/loop_bindings bound for its body (SPD-146), unbound where the loop closes
        self.bound = None
        # a case's form (ShellWalk.case_in): True where `in` follows its word, False otherwise -- zsh's brace form
        # `case word { ... }` (SPD-185) among them -- and None until the walk has read that far
        self.form = None


class ShellWalk:
    """One pass over a command line's tokens.  Simple commands go to analyse_segment; the grammar around them
    decides which directories the shell may be in when each runs, as zsh 5.9 and bash 3.2 do (probed):

    - a subshell, a command or process substitution, a background job and every pipeline element but the last run in their
      own process, so a cd there does not carry out; the last element runs in zsh's own shell and in bash's subshell, so
      either directory follows it;
    - a cd that may not run (after && or ||, in an if, case or loop body, in a function body) or may fail (its target does
      not exist now) leaves either directory for what runs after its and-or list; what runs after `cd x &&` is in x;
    - a relative cd in a loop or a function body may repeat, so the hook cannot follow it;
    - a compound command's redirections open where it started;
    - zsh's SHORT_LOOPS (on by default) run a loop body with no `do` and `done`: after `repeat word`, after
      `for name ( word ... )` and after a `for`/`select` list closed by `;` or a newline, the body is a `do ... done`, a
      `{ list }`, a `( list )` or one sublist, and the loop ends where that sublist ends;
    - a `for`, `select` or `foreach` header (zsh's par_for) is its names, then `( word ... )`, `in word ... TERM` or neither,
      the positional parameters.  zsh reads the word after each name in command position: for a for or a foreach an
      identifier there is one more name, and any other word -- `do`, `{`, another reserved word, a redirection -- opens the
      body; a select takes one name, and the word after it opens its body unless it is `in` or `(` (SPD-182; see
      names_end);
    - the same holds for an `if`, `while` or `until` whose condition ends in `[[ ... ]]`, which closes the condition
      the way a terminator closes a loop header, so `then` and `do` are optional there too; an `elif` after such a body
      opens the next condition list the same way, so its own `[[ ... ]]` and body are read as the `if`'s were;
    - zsh's `foreach name ... ( word ... )` (or `in word ... TERM`, or neither: the positional parameters) is a loop whose
      body is a `do ... done`, a `{ list }`, or a list of any length up to an `end` in command position, a `( list )`
      among its commands (SPD-180; see resolve_body and ends_foreach);
    - zsh's try-always form, `{ list } always { list }`, is one compound command holding both lists, so the always
      block runs where the try block left the shell and the compound ends at the always block's `}` (see close_brace);
    - zsh splits a brace off the word it is glued to: `{git push}` is the group `{ git push }` (see add_word).
      bash does not, so `glued` says which reading this walk is, and `split_brace` whether zsh's split one off this line:
      analyse_command then walks the line again the other way and keeps both readings, as it does for zsh's globs;
    - every command on the line starts from the standard input the line is run on -- `stdin`, None where the line does not
      spell it, and `fed`, whether anything stands there: a `-c` string's is its shell's (SPD-210) -- and a compound
      command's own input redirections stand where it opens, as a simple command's do (walk_line); a substitution in a
      command's words reads the input of the list it stands in (substitution_input);
    - a function's body runs on the input its call is given, not on the one where it is defined (SPD-212): `calls`, each
      definition -> the (text, whether anything stands there) this walk reads its body with, one call's input that an
      earlier walk of the line found (walk_line), and `line`, the reading those definitions are bound to (define)."""

    def __init__(self, a, inner, bodies, expanded, depth, glued=True, stdin=None, fed=False, inputs=None, calls=None, line=None):
        self.a, self.inner, self.bodies, self.depth = a, list(inner), list(bodies), depth
        self.expanded = list(expanded)  # whether the shell expands each body (heredocs.strip_heredocs)
        self.glued = glued  # zsh's reading: a brace glued to a word opens or closes a group where a lone one would
        self.split_brace = False  # ... and it did on this line, so the reading differs from bash's
        self.words, self.stack = [], []
        self.skip = False  # the words are a for, select, foreach or case header or a function's name, not a command
        self.header = None  # which header they are: "for", "select", "foreach" (_NAMED_LOOPS), "repeat" or "func"
        self.expect_body = False  # the header is complete: the next word decides the body's form
        self.function_next = False  # `name ()` or `function name` was read: the next body is a function's
        self.redirect_cwds = syntax._CURRENT
        self.toks, self.at = [], 0  # the line's tokens, and the index of the one add_word is reading
        # the text the pipeline element being read has printed so far, the text the elements before it in this
        # compound command printed, the text the element before it in its pipeline printed -- which this one reads on
        # standard input -- and the input the compound command itself was given, the line's own at the outermost level.
        # None is text the line does not spell, which absorbs (stdin_text.joined).
        self.printed, self.frame_printed, self.piped_text, self.frame_stdin = "", "", None, stdin
        # whether a pipe feeds the element being read at all, and whether anything stands on the input of the compound
        # command around it, which the texts above cannot say (None is both "nothing" and "text the line does not spell").
        self.piped_fed, self.frame_stdin_fed = False, fed
        # whether the pipe feeds the element being read itself, a simple command's own input among its redirections in
        # zsh's reading (stdin_text.command_input, SPD-209), and not a compound command around it
        self.pipe_feeds = False
        # The compound commands' own input redirections (SPD-210): the ones this walk found after a closer -- serial ->
        # (the words after it, with their operators, and the bodies consume took for them) -- and the ones an earlier walk
        # of the line found, which push reads where each compound opens (walk_line).  `closed`: the compound whose closer
        # the walk has just read, while the words after it are read.
        self.found, self.inputs, self.closed, self.opened = {}, inputs or {}, None, 0
        # The function definitions (SPD-212): the numbers define gave the ones read so far, in order -- the same in every
        # walk of one line's tokens -- the one whose body is still to come, and the calls' inputs this walk reads bodies with.
        self.defined, self.defining, self.calls, self.line = [], None, calls or {}, line
        self.start_list()

    # -- lists and pipelines ------------------------------------------------------------
    def start_list(self):
        self.list_start = self.list_seen = self.pipeline_start = self.a.cwds
        self.uncertain = self.conditional = self.piped = False
        self.list_mark = len(self.a.assigned)  # the assignments before this and-or list
        self.end_element()  # no pipe feeds the first element of a new list

    def end_element(self, into_pipe=False):
        """The pipeline element read so far is over.  What it printed feeds the element after the `|`
        (`into_pipe`), or joins what this compound command has printed, which is where its own output goes: a `;`, a
        `&&`, a `||` or a `&` ends an element without ending the compound command around it, and the text a shell after
        a later `|` runs is the one element before it, never the list before that (`echo x; echo 'git push' | sh`)."""
        if into_pipe:
            self.piped_text, self.piped_fed = self.printed, True
        else:
            self.frame_printed = stdin_text.joined(self.frame_printed, self.printed)
            self.piped_text, self.piped_fed = self.frame_stdin, self.frame_stdin_fed
        self.pipe_feeds = into_pipe
        self.printed = ""

    def end_pipeline(self):
        if self.piped:
            self.a.cwds = directories.union_dirs(self.pipeline_start, self.a.cwds)
            self.list_seen = directories.union_dirs(self.list_seen, self.a.cwds)
            self.piped = False

    def end_list(self):
        self.end_pipeline()
        if self.uncertain:
            self.a.cwds = directories.union_dirs(self.list_seen, self.a.cwds)
        self.start_list()

    # -- compound commands --------------------------------------------------------------
    def push(self, kind, closer):
        outer = (self.list_start, self.list_seen, self.pipeline_start, self.uncertain, self.conditional, self.piped, self.words,
                 self.skip, self.header, self.expect_body)
        # a subshell's own function definitions do not escape, nor their bodies
        funcs = (set(self.a.functions), bodies_copy(self.a.function_bodies)) if kind == "sub" else None
        frame = ShellFrame(kind, closer, self.a.cwds, outer, len(self.a.assigned), funcs)
        frame.serial, frame.bare, self.opened, self.closed = self.opened, not self.words, self.opened + 1, None
        if frame.bare:  # the compound command a definition's header is followed by, and not a `<( )` in a command's words
            frame.defines, self.defining = self.defining, None
        # what the element around it printed so far is kept for after the compound command, and the standard
        # input that element was given is the input every list inside it starts from -- with the compound's own input
        # redirections, where an earlier walk of the line found any after its closer, read as a simple command's are,
        # zsh's reading and bash's (stdin_text.command_input, SPD-209 and SPD-210)
        frame.printed, frame.earlier = self.printed, self.frame_printed
        frame.stdin = (self.piped_text, self.frame_stdin, self.piped_fed, self.frame_stdin_fed, self.pipe_feeds)
        self.printed, self.frame_printed = "", ""
        self.frame_stdin, self.frame_stdin_fed = self.piped_text, self.piped_fed
        if frame.serial in self.inputs:
            redirects, bodies = self.inputs[frame.serial]
            self.frame_stdin = stdin_text.command_input(redirects, bodies, self.piped_text, self.pipe_feeds, self.a)
            self.frame_stdin_fed = stdin_text.input_fed(redirects, bodies, self.piped_fed)
        if frame.defines in self.calls:
            # a function's body, read on a call's input in place of what stands where it is defined (SPD-212); with input
            # redirections of the definition's own, zsh reads the call's and then each of them in turn, and bash their last
            # alone -- the call's input taking the place of a pipe into the compound (probed: FunctionInputTest)
            text, fed = self.calls[frame.defines]
            if frame.serial in self.inputs:
                redirects, bodies = self.inputs[frame.serial]
                self.frame_stdin, self.frame_stdin_fed = stdin_text.command_input(redirects, bodies, text, True, self.a), True
            else:
                self.frame_stdin, self.frame_stdin_fed = text, fed
        self.stack.append(frame)
        if kind in ("loop", "func"):
            self.a.loop_depth += 1
        self.a.func_depth += kind == "func"
        self.words, self.skip, self.header, self.expect_body = [], False, None, False
        self.start_list()

    def pop(self):
        self.finish()
        self.end_list()  # ... which joins the last element's text to the rest of what this compound command printed
        frame = self.stack.pop()
        # the compound command's own output stands where it opened, in the element that holds it
        self.printed = stdin_text.joined(frame.printed, self.frame_printed) if frame.prints else frame.printed
        self.frame_printed = frame.earlier
        self.piped_text, self.frame_stdin, self.piped_fed, self.frame_stdin_fed, self.pipe_feeds = frame.stdin
        if frame.kind == "sub":
            self.a.functions, self.a.function_bodies = frame.funcs  # a function defined in a subshell does not reach a call after it
        if frame.kind in ("loop", "func"):
            self.a.loop_depth -= 1
        self.a.func_depth -= frame.kind == "func"
        loop_bindings.unbind_frame(frame, self.a)
        assigned = self.a.assigned[frame.mark :]
        self.a.doubt.update(assigned)  # what a compound command assigned may not have run, or may not persist
        if frame.kind == "func":
            self.a.sticky.update(assigned)  # a function body assigns again whenever it is called
        inner = self.a.cwds
        after = frame.saved if frame.kind == "sub" else (inner if frame.kind == "group" else directories.union_dirs(frame.seen, inner))
        (self.list_start, self.list_seen, self.pipeline_start, self.uncertain, self.conditional, self.piped, self.words,
         self.skip, self.header, self.expect_body) = frame.outer
        # the words up to the next terminator are this compound's redirections (finish), or, after a short loop's compound
        # body, the loop's, whose pop below takes this over
        self.closed = frame if frame.bare else None
        if frame.procsub and not self.in_pattern():
            # the file name `<( list )` hands the command: a word the line does not spell, which every reader of
            # hookio.SUBST takes for one and consume pairs with no lifted body (SPD-190).  Not in a case's word or pattern
            # (SPD-184), which no command reads: where the walk still takes a pattern's `|` for a pipe (the brace form,
            # SPD-185; case_in), it reads the words before it as a command, which this word would name
            self.words.append(PROCSUB_FILE)
        self.a.cwds = after
        if after != frame.saved and self.conditional:
            self.uncertain = True
        self.list_seen = directories.union_dirs(self.list_seen, after)
        if frame.kind != "sub":
            self.redirect_cwds = directories.union_dirs(frame.saved, after)
        if self.stack and self.stack[-1].body == "compound":
            if self.stack[-1].kind == "cond":
                # `if [[ -n x ]] { list }`: an `else` or an `elif` may still follow the group, so the conditional ends where a
                # sublist body would, at the next terminator (probed: `if c { a } else { b }` and `if c { a }; echo`)
                self.stack[-1].body = "sublist"
            else:
                self.pop()  # the short loop whose body this `{ ... }` or `( ... )` was

    def close_brace(self):
        """The `}` that closes a `{ list }` -- a group, a coproc's group, a function body.

        zsh's try-always form (probed in zsh 5.9): an unquoted `always` right after that `}`, then `{` after any
        number of `;` and newlines, runs the always block after the try block, in the same shell or fork, starting in the
        directories the try block ended in, and the compound command ends at the always block's `}`.  So the frame stays
        open and the always block is read as one more list of it, as a list after a `;` would be: `{ A } always { B }` is
        read as `{ A; B; }`, with its prefixes, redirections and pipelines.  The same holds at the always block's own `}`
        (a second `always` chained is a parse error, read all the same) and after a function's or a short loop's body,
        where zsh parses no always form either: over-reading those costs a line no shell runs.  An `always` with no `{`
        to follow is a parse error too; it is dropped as the keyword it is there, so the words after it are read as
        commands and not as a command named `always`'s arguments.  In zsh's reading the always block's `{` may be glued
        to its first word (`{vcs try} always {vcs alw}` ran both): the token is read again without it, by the
        walk's own loop, so a chain of such blocks never recurses."""
        toks, j = self.toks, self.at + 1
        if j < len(toks) and toks[j] == "always":
            k = j + 1
            while k < len(toks) and toks[k] == ";":  # a newline reaches here as `;`
                k += 1
            if k < len(toks) and toks[k][:1] == "{" and (toks[k] == "{" or self.glued):
                self.finish()
                self.end_list()
                if toks[k] == "{":
                    self.at = k
                else:
                    toks[k], self.at, self.split_brace = toks[k][1:], k - 1, True
                return
            self.at = j
        self.pop()

    def brace_closes(self, closer=None):
        """Whether a `}` after a word closes a `{ list }`: zsh's sole `}` is significant anywhere, so it ends the short
        loops' and short conditionals' sublists standing in the group before it closes the group (a probe ran
        `coproc { repeat 1 vcs push }`, which parses only so).  `closer`: a `fi`, `done` or `esac` read first, which
        must close the compound command it reaches before the `}` does (zsh reads `fi}` as `fi` and `}`)."""
        for frame in reversed(self.stack):
            if frame.body == "sublist":
                continue
            if closer is None:
                return frame.closer == "}"
            if frame.closer != closer:
                return False
            closer = None
        return False

    def branch(self, word=None):
        """then, else, elif, do, a case arm: the body may start from the directories the compound command started in.
        `word` is the reserved word that reached this branch, where one did; a case arm's `)` passes none.

        An `elif` reopens the conditional's condition list, whatever form the branch before it took -- a short
        conditional's "sublist", the "compound" it passes through, or the long form's "long".  The frame is then in the
        state the `if`'s own condition list put it in, so a `]]` at the end of the elif's condition ends it (end_header)
        and the body after it is a `then ... fi`, a `{ ... }` or `( ... )`, or one sublist, exactly as the `if`'s was
        (probed in zsh 5.9: `if [[ -z x ]] { vcs a } elif [[ -n y ]] { vcs b } else { vcs c }` ran b, and so did its
        `fi`, terminator and sublist spellings).  A condition with no `]]` runs on to the `then` below, which turns it
        into the long form."""
        self.finish()
        self.end_list()
        if self.stack and self.stack[-1].kind in ("cond", "case", "loop"):
            top = self.stack[-1]
            if word == "elif" and top.kind == "cond":
                top.body, self.expect_body = "cond", False  # the elif's own condition list
            elif top.body in ("cond", "cond-pending"):
                top.body, self.expect_body = "long", False  # `then` or `do`: the condition is over
            top.seen = directories.union_dirs(top.seen, self.a.cwds)  # where the branch before this one ended
            self.a.doubt.update(self.a.assigned[top.mark :])  # a branch may run without what an earlier one assigned
            self.a.cwds = directories.union_dirs(top.saved, self.a.cwds)
        self.start_list()

    # -- zsh's short loop forms ---------------------------------------------------------
    def open_loop(self, t):
        """A `for`, `select`, `repeat` or `foreach` in command position: its header is read, then its body, with or without
        `do`.  A foreach's body may run to an `end`, which is its closer until the body shows its form (resolve_body)."""
        self.push("loop", "end" if t == "foreach" else "done")
        self.skip, self.header = True, t
        self.stack[-1].body = "header"

    def loop_names(self, words):
        """Whether a for, select or foreach header's words so far are only its names, no word list begun: after them zsh's
        parser (par_for) reads the next word in command position, where a `( ... )` is the word list and `in` starts one.
        A select takes one name; every word a for's or a foreach's header took after its first is a name until an `in`,
        since any other word ends the header (names_end)."""
        if self.header == "select":
            return len(words) <= 1
        return "in" not in words[1:]

    def names_end(self, t):
        """Whether `t`, read in a for, select or foreach header after its words so far, ends the names and opens the body of
        a loop over the positional parameters: a `do ... done`, a `{ ... }`, or the first command of one sublist -- of a
        list up to `end`, for a foreach (resolve_body).  The first word is the first name if zsh takes it for one
        (syntax.loop_name), and otherwise a parse error, read as the body, fail closed.  After the names, with no word list
        begun, a select's body starts at once, and a for's or a foreach's at the first word that is no name -- a reserved
        word, a redirection, a word zsh rejects -- where a run of digits just before a redirection operator is that
        redirection's descriptor, as directories.separate_redirects reads it, not a name.

        Probed in zsh 5.9 -f and -f -o nobareglobqual with `set -- p` (SPD-180, SPD-182): `foreach a b (1 2 3 4) echo
        two-$a$b; end` and `for a b (1 2 3 4) echo two-$a$b` printed two-12 two-34; `for f do ...; done`, `for f { ... }`,
        `for a b do ...; done`, `foreach f g { ... }` and syntax.ZSH_RESERVED_WORDS' lines ran their bodies; `for f > o1`
        made o1, `for f 2> o2 echo x` made o2 and ran echo, and `for f g >o3 echo x` wrote x to o3; `for f git push` and
        `foreach f git push; end` ran nothing, git and push being names; `for f x-y` and `foreach f x-y; end` failed near
        `x-y`, a word that is neither (read as the body, fail closed); and a select's body started after its one name,
        `select f x-y` running a command named x-y and `select a b c` one named b."""
        if not self.words:
            return not syntax.loop_name(t, first=True)
        if t == "in":
            return False
        if self.header != "select" and syntax.loop_name(t):
            following = self.toks[self.at + 1] if self.at + 1 < len(self.toks) else None
            if not (t.isdigit() and (following in syntax.OUT_REDIRECTS or following in syntax.IN_REDIRECTS)):
                return False  # one more name, or a word of the list
        return self.loop_names(self.words)

    def ends_foreach(self):
        """Whether an `end` in command position closes a foreach: the compound command it would close, past the short
        loops' and short conditionals' sublists, is a foreach whose body runs to its `end`.  zsh rejects an `end` anywhere
        else as a parse error; the walk reads it there as the word it is to bash, and to the lines before SPD-180.
        Probed in zsh 5.9: that `end` closes after a `}`, a subshell's `)` and an `esac`, not after a `fi` or a `done`
        (a parse error, a line that runs nothing, read all the same), and zsh splits it off a glued `}` (glued_close)."""
        for frame in reversed(self.stack):
            if frame.body != "sublist":
                return frame.closer == "end"
        return False

    def open_conditional(self, t):
        """An `if`, `while` or `until` in command position: its condition list, then its body, which a `[[ ... ]]` at the end
        of that list may open with no `then` or `do`."""
        self.push("cond" if t == "if" else "loop", "fi" if t == "if" else "done")
        self.stack[-1].body = "cond"

    def loop_header_word(self, t):
        """A `for` or `select` loop's variable holds whatever its list gives it, which a member writes: a doubt,
        so a word it fills where a command reads options or primaries is one the member controls -- unless every
        word of the list, as the shell expands it, starts with something other than `-` (`for d in /tmp/*/; do find "$d"
        ...`), which ShellAnalysis.dashless_loops records until the list says otherwise."""
        if "in" not in self.words:
            if syntax.IDENTIFIER_RE.match(t):
                self.a.doubt.add(t)
            return
        at = self.words.index("in")
        names = [n for n in self.words[:at] if syntax.IDENTIFIER_RE.match(n)]
        # read one word at a time, as it arrives, so a long list stays linear: dashless at its `in`, and not from the first
        # word that may start with `-` on
        if len(self.words) == at + 1:
            self.a.dashless_loops.update(names)
        elif _value_may_start_with_dash(t):
            self.a.dashless_loops.difference_update(names)
        # a list holding a positional parameter (`for a in "$@"`), or -- once shell/positional has set the call's words
        # where the body reads them -- one of the member's own words, fills the loop variable with what the member wrote,
        # so a finding on it is the member's own inside a function body (SPD-205, analyse_shell_text's prune).  Only the
        # word just read is tested, so a long list stays linear: each word reaches this once as `t`.
        word = prepare.deglob(t)
        if syntax.POSITIONAL_RE.search(word) is not None or word in self.a.shell_words:
            self.a.member_vars.update(names)

    def end_header(self):
        """The loop's header, or an if/while/until condition ending in `]]`, is complete.  Its body may follow with no `do`
        or `then`, so the next word decides the body's form."""
        self.finish()
        if self.stack and self.stack[-1].body in ("header", "cond"):
            self.stack[-1].body = "pending" if self.stack[-1].body == "header" else "cond-pending"
            self.expect_body = True

    def reopen_condition(self):
        """A `&&`, `||`, `|` or `&` where a body was expected: the condition list goes on, so the `]]` before it did not end
        it after all (probed: `if [[ -n x ]] && [[ -n y ]] echo both` ran the body, and only the last `]]` ends the
        condition).  A loop header's `pending` is left alone: no operator stands inside one."""
        if self.stack and self.stack[-1].body == "cond-pending":
            self.stack[-1].body, self.expect_body = "cond", False

    def resolve_body(self, t):
        """The first word after a complete header or condition: `do` or `then` opens a `do ... done` or `then ... fi` body,
        `{` or `(` a compound one, anything else a single sublist (probed in zsh 5.9: `repeat 2 echo a; echo b` ran `echo a`
        twice; `if [[ -n x ]] then echo t; fi` and `while [[ $((n++)) -lt 2 ]] do echo t; done` both ran)."""
        self.expect_body = False
        frame = self.stack[-1] if self.stack else None
        if frame is None or frame.body not in ("pending", "cond-pending"):
            return
        braced = t[:1] == "{" and (t == "{" or self.glued)  # `repeat 1 {git push}`: a group, its brace glued
        if frame.closer == "end":
            # a foreach (SPD-180, probed in zsh 5.9): a `do ... done` or a `{ ... }` body ends the loop as a for's does, with
            # no `end` after it (`foreach f (a) { echo x }; end` failed near `end`); any other word, a `(` among them, starts
            # a list that runs to the `end` (`foreach f (a) ( echo sub ) end` and `foreach f (a b) echo s1; echo s2; end` ran)
            if t == "do":
                frame.closer = "done"
            frame.body = "compound" if braced else "long"
            return
        frame.body = "long" if t in ("do", "then") else ("compound" if braced or t == "(" else "sublist")

    def close_sublists(self):
        """A short loop or conditional whose body is one sublist ends where that sublist ends."""
        while self.stack and self.stack[-1].body == "sublist":
            self.pop()

    # -- simple commands ----------------------------------------------------------------
    def consume(self, words):
        """Analyse the substitutions in these words (expanded before the command runs, each in its own process), and the
        text zsh's (e) flag evaluates in them with the variables the line holds here (shell/reevaluation), and take the
        here-document bodies their `<<` operators read; return the words without the operators and their delimiters.

        A body whose delimiter is unquoted is expanded here too, before the command reads it, wherever it is fed
        (SPD-192): its substitutions run in the command's directory with the values the line holds when the command runs,
        which its own prefix assignments do not reach (probed: `x=a; x=b cat <<EOF` and `cat <<EOF ...; x=b` wrote into
        a, `cat <<EOF ...; cd d` where the line stood before the cd; tests/test_hooks.py HereDocumentExpansionTest).
        What the command reads, and what the bodies returned hold, is then the text the expansion leaves, its escapes
        resolved (heredocs.received_body, SPD-206): `sh <<EOF` fed `\\$(git push)` runs the push.  Where the expansion ran a
        substitution that text holds its output, which the line does not spell, and the body returned is a
        heredocs.OutputBody (SPD-207): `sh <<EOF` fed `echo a $(cat x.sh)` runs x.sh's lines.  Each parameter's value
        stands in that text as well, where the line settles it, and a shell fed it parses the value again
        (reevaluation.body_values, SPD-208): `x='a; git push'; sh <<EOF` fed `echo $x` pushes; where the line does not
        settle a value the body is an OutputBody too."""
        reevaluation.read_eval_words(words, self.a, self.depth)
        self.substitutions = {}  # each word -> the bodies its substitutions lifted, which shell/loop_bindings reads (SPD-146)
        for w in words:
            lifted = []
            for _ in range(w.count(hookio.SUBST) - w.count(PROCSUB_FILE)):  # a `<( )`'s file name lifted no body
                if self.inner:
                    lifted.append(self.inner.pop(0))
                    analyse.analyse_isolated(self.a, lifted[-1], self.depth + 1, *self.substitution_input())
            if lifted and PROCSUB_FILE not in w:
                bodies = tuple(lifted)
                self.substitutions[w] = bodies if self.substitutions.get(w, bodies) == bodies else None
            if syntax.ZSH_CLOSE in w:
                for code in globbing.qualifier_code(w):
                    # zsh runs it in the shell that expands the word, once for every file the glob matches (probed: a cd there
                    # moved the command's own directory), so its commands are checked and its cd is followed as a loop's
                    before = self.a.cwds
                    self.a.loop_depth += 1
                    analyse.analyse_command(code, self.a, self.depth + 1)
                    self.a.loop_depth -= 1
                    self.a.cwds = directories.union_dirs(before, self.a.cwds)
                    self.a.cd_uncertain = False
        cleaned, bodies, k, values = [], [], 0, None
        while k < len(words):
            if words[k] in ("<<", "<<-"):
                if self.bodies:
                    body = self.bodies.pop(0)
                    if self.expanded.pop(0):
                        ran = reevaluation.read_expanded_body(body, self.a, self.depth + 1)
                        # bash expands the body's substitutions with the command's own prefix, zsh with the outer value (SPD-211)
                        ran = reevaluation.read_body_with_prefix(body, self.a, self.depth + 1, words) or ran
                        values = values or reevaluation.body_values(self.a, words)
                        body = heredocs.received_body(body, values)
                        if ran:
                            body = heredocs.OutputBody(body)
                    bodies.append(body)
                k += 2
                continue
            cleaned.append(words[k])
            k += 1
        return cleaned, bodies

    def substitution_input(self):
        """(the text, whether anything stands there) on the standard input a command substitution in the words being read
        runs on.  zsh expands a substitution in the shell running the list it stands in, so it reads that list's input, the
        compound command's (SPD-210); bash expands one in a pipeline element in the subshell the pipe feeds, so there it
        reads the pipe (SPD-213, CompoundInputTest): `printf ... | echo $(sh)` runs the pipe's text in bash.  Not the
        command's own redirections, which the shells perform after they expand its words.  Where a list has no input its
        substitution reads nothing (a spelled empty text, never a refusal); where the two shells' readings differ the text
        is a MultiosText holding each, and each_reading gives a shell each."""
        zsh_text = self.frame_stdin if self.frame_stdin_fed else ""
        if self.pipe_feeds:
            bash_text, fed = (self.piped_text if self.piped_fed else ""), self.frame_stdin_fed or self.piped_fed
        else:
            bash_text, fed = zsh_text, self.frame_stdin_fed
        return stdin_text._paired(bash_text, zsh_text), fed

    def discard(self):
        self.consume(self.words)
        self.words = []

    def finish(self, unsure=False):
        """Analyse the simple command read so far.  `unsure`: it runs in its own process (a pipeline element, a background job)."""
        words, self.words = self.words, []
        skip, self.skip = self.skip, False
        header, self.header = self.header, None
        redirect_cwds, self.redirect_cwds = self.redirect_cwds, syntax._CURRENT
        closed, self.closed = self.closed, None
        if not words:
            return
        cleaned, bodies = self.consume(words)
        if closed is not None and stdin_text.input_fed(words, bodies, False):
            # a compound command's own input redirections, which zsh and bash perform before it runs, so the commands in it
            # read that input: kept for walk_line to walk the line again with it standing where the compound opens
            self.found[closed.serial] = (words, bodies)
        a = self.a
        if skip:
            if header == "for" and self.stack and self.stack[-1].kind == "loop":
                # its words, once per value in the body (SPD-146), read with the values the line settled before the header
                loop_bindings.bind_loop(cleaned, self.stack[-1], a)
            if header != "repeat":  # a for or select header assigns its name; a repeat count assigns nothing
                for w in cleaned:
                    a.doubt.update(syntax._NAME_RE.findall(prepare.deglob(w)))
            return
        outer_substitutions, a.subst_words = a.subst_words, self.substitutions
        before = a.cwds
        # an assignment in a command that may not run (after && or ||) or runs in its own process may not hold after it
        unsure = unsure or self.conditional or self.piped
        a.unsure += unsure
        # the text the command reads on standard input: the pipe's or the compound command's, and its own input
        # redirections in the order they stand in `words`, which still hold its here-document operators, as zsh and bash
        # each read them (stdin_text.command_input, SPD-209), with the values the line settled before it runs (SPD-148)
        stdin = stdin_text.command_input(words, bodies, self.piped_text, self.pipe_feeds, a)
        if self.function_next:  # zsh's `name () command`: a body that runs when called, perhaps more than once
            self.function_next = False
            defining, self.defining, piped_fed = self.defining, None, self.piped_fed
            if defining in self.calls:
                # read on a call's input (SPD-212), which the command's own input redirections replace (probed: `z() cat
                # < def.txt; z < call.txt` printed def alone in zsh)
                text, piped_fed = self.calls[defining]
                stdin = stdin_text.command_input(words, bodies, text, False, a)
            a.loop_depth += 1
            a.func_depth += 1
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, stdin, piped_fed)
            a.func_depth -= 1
            a.loop_depth -= 1
            a.cwds = directories.union_dirs(before, a.cwds)
        else:
            # what this command adds to the text its pipeline element prints, which a shell after a `|` runs, read with
            # the values the line settled before it runs (SPD-148)
            printed = stdin_text.printed_text(cleaned, stdin, a)
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, stdin, self.piped_fed)
            self.printed = stdin_text.joined(self.printed, printed)
        a.unsure -= unsure
        a.subst_words = outer_substitutions
        if a.cd_uncertain or (a.cwds != before and self.conditional):
            self.uncertain = True
        a.cd_uncertain = False
        self.list_seen = directories.union_dirs(self.list_seen, a.cwds)

    # -- braces --------------------------------------------------------------------------
    def in_pattern(self):
        """A case command is reading its subject, `in` or a pattern: no command position, so no word there opens a group."""
        return bool(self.stack) and self.stack[-1].kind == "case" and self.stack[-1].pattern

    def case_in(self, case):
        """Whether this case reads `in ... esac`, where a top-level `|` in a pattern is an alternative, no pipe (SPD-187):
        the word after its subject is `in`.  Settled once the walk holds both words, and at its first pattern's `)`; until
        then, and for zsh's brace form (SPD-185, whose patterns the walk does not place yet) or a subject no word stands
        for (a `<( )`), False, so the walk reads a `|` there as it always has, a pipe."""
        if case.form is None and len(self.words) >= 2:
            case.form = self.words[1] == "in" and self.words[0] != "{"
        return bool(case.form)

    def case_header_break(self, case, i):
        """The `;` at toks[i], which mark_zsh_patterns writes for a newline, stands in a case's header: after `case word
        in`, or between the word and an `in` that follows it (probed: zsh 5.9 and bash 3.2 read the patterns after either,
        SPD-187), so the words before it are no command."""
        if len(self.words) == 2:
            return self.case_in(case)
        j = i + 1
        while j < len(self.toks) and self.toks[j] == ";":
            j += 1
        return len(self.words) == 1 and self.words[0] != "{" and j < len(self.toks) and self.toks[j] == "in"

    def define(self, words):
        """A function definition's header was read, `words` its name words: bind its names to a shell function
        (ShellAnalysis.functions), and to this definition's body, which a call reads with the input it is given (SPD-212,
        walk_line).  The definition is numbered in the order the walk reads them, the same in every walk of the line's
        tokens, and its body is the compound command the walk opens next (push), or zsh's one simple command (finish)."""
        names = _function_names(words)
        self.a.functions.update(names)
        self.defining = len(self.defined)
        self.defined.append(self.defining)
        for name in names:
            self.a.function_bodies.setdefault(name, set()).add((self.line, self.defining))

    def open_brace(self):
        """Open the `{ list }` a `{` read now begins, and say whether it did: in command position a group, or a function's
        body after `name ()` and zsh's anonymous `()`; after `function name` a function's body; after only `coproc`,
        `time` and `!` a prefixed group; after a lone `always` a group.  Anywhere else the `{` is a word."""
        if not self.words and not self.skip:
            self.push("func" if self.function_next else "group", "}")
            self.function_next = False
            return True
        if self.skip and self.function_next:  # function name {
            self.define(self.words)  # a function defined here shadows a later call's name
            self.discard()
            self.skip, self.header = False, None
            self.push("func", "}")
            self.function_next = False
            return True
        if self.skip or self.function_next or not self.words:
            return False
        if all(w in syntax.LOOP_PREFIX_WORDS for w in self.words):
            # zsh runs a `{ list }` after `coproc`, `time` and `!` as well (probed: `coproc { repeat 1 vcs push }`,
            # `time { repeat 1 vcs push; }`, `! { repeat 1 vcs push; }`, `time ! { ... }`, `! time { ... }`,
            # `time coproc { ... }`, `coproc time { ... }` and `time { { ... } }` each ran the body), so the group is read as
            # a group and a short loop or short conditional inside it is checked.  With `coproc` among the prefix words the
            # group is a forked shell's, as `coproc ( ... )` already was: its directory and its assignments never reach the
            # line (probed: `coproc { x=1; cd /tmp; }` left the line where it was with x unset, while `time { x=1; cd /tmp; }`
            # and `! { x=1; cd /tmp; }` left it in /tmp with x=1), and a trap set there still fires.
            forked = "coproc" in self.words  # read before discard takes the prefix words away
            self.discard()
            self.push("sub" if forked else "group", "}")
            return True
        if self.words[-1] == "always" and directories.separate_redirects(self.words)[0] == ["always"]:
            # an `always` in command position before a `{` -- after a terminator or a newline (`{ a }; always
            # { b }`), after a subshell (`( a ) always { b }`), after a group's redirection (`{ a } 2>&1 always { b }`) --
            # is a parse error in zsh, whose sole `}` closes no group there.  The block is read as a group all the same
            # rather than as a command named `always`'s arguments; the word and any redirection before it are read first,
            # as the command they would be.
            self.finish()
            self.push("group", "}")
            return True
        return False

    def glued_close(self, t):
        """The word before the `}` zsh splits off the end of `t`, where a lone `}` after that word would close a frame; else
        None, the `}` staying in the word (probed in zsh 5.9).  The `}` is unquoted and unescaped -- a quoted or
        escaped one is a sentinel by now -- and closes no `{` or `${` opened earlier in the word, and only the last is split:
        `{vcs a}b}` logged `a}b`, `{vcs try}}` `try}`, `{vcs ${x-q}}` q and `{vcs x{a,b}}` xa xb, while `{vcs a}x`,
        `{vcs {a}`, `{vcs ${x-q}` and `{vcs a\\}` left the group open.  An assignment in assignment position keeps its `}`
        (`{x=1}` and `{ x=1}` never closed; `{x=1 }` set x and `{vcs x=1}` logged `x=1`), and so does a word in a header
        the walk skips.  Outside any group zsh rejects the line and bash runs it with the `}` in the word, which is how the
        hook always read it.  After `fi`, `done`, `esac` or a foreach's `end` the `}` closes what a lone one would after
        that word."""
        if self.skip:
            return None
        rest = t[:-1]
        if "{" in rest:
            depth = 0
            for c in rest:
                if c == "{":
                    depth += 1
                elif c == "}" and depth:
                    depth -= 1
            if depth:
                return None
        if assignment_words.assignment_word(t) and all(assignment_words.assignment_word(w) for w in self.words):
            return None
        closes = rest in ("fi", "done", "esac") or rest == "end" and self.ends_foreach()  # `end}` (probed: `{ foreach ...; end}`)
        closer = rest if not self.words and closes else None
        return rest if self.brace_closes(closer) else None

    def add_word(self, t):
        """One word, read in its place.  In zsh's reading a word that starts with `{` opens the group a lone `{`
        would open there -- never in a case pattern -- and the rest of it is read as the next word, from command position
        (probed: `{vcs try}`, `time {vcs try}`, `f() {vcs a}`, `{vcs}` and `{}` ran as groups, `{"vcs" try}` and
        `{\\vcs try}` too; `{vcs,x}` ran a command named `vcs,x`, no brace expansion); and a word whose `}` glued_close
        splits off is read, then the `}` is, as a lone one after it.  bash reads both braces as part of their words.

        After a for's, a select's or a foreach's names, with no word list, a word that is no name -- a reserved word, a
        redirection -- ends the header and opens the body (names_end)."""
        if self.skip and self.header in _NAMED_LOOPS and self.names_end(t):
            self.end_header()
            self.resolve_body(t)
        while t[:1] == "{" and (t == "{" or (self.glued and not self.in_pattern())) and self.open_brace():
            if t == "{":
                return
            t, self.split_brace = t[1:], True
        rest = self.glued_close(t) if self.glued and len(t) > 1 and t[-1] == "}" else None
        if rest is None:
            self.read_word(t)
            return
        self.split_brace = True
        self.read_word(rest)
        self.read_word("}")

    def read_word(self, t):
        """add_word's reading of a word once its braces are settled: a reserved word, a `}`, or one more word of the command."""
        if t == "esac" and len(self.words) == 2 and self.in_pattern() and self.case_in(self.stack[-1]):
            # `case word in esac`, a case with no pattern, which closes there (probed: `case q in esac | echo e1` and `case
            # q in esac; echo e2` printed theirs in zsh 5.9 and bash 3.2): with a pattern's `|` an alternative (SPD-187),
            # a walk still in its pattern would read no pipe after it
            self.discard()
        if not self.words and not self.skip:
            if t in ("}", "fi", "done", "esac") or t == "end" and self.ends_foreach():
                self.close_sublists()
                if self.stack and self.stack[-1].closer == t:
                    if t == "}":
                        self.close_brace()
                    else:
                        self.pop()
                return
            if t in ("if", "while", "until"):
                self.open_conditional(t)
                return
            if t in ("for", "select", "repeat", "foreach"):
                self.open_loop(t)
                return
            if t == "case":  # its subject and `in` are read with the first pattern and discarded at the pattern's `)`
                self.push("case", "esac")
                return
            if t in ("then", "else", "elif", "do"):
                self.branch(t)  # `elif` reopens the condition
                return
            if t == "function":
                self.function_next = self.skip = True
                self.header = "func"
                return
        if not self.skip and self.words and all(w in syntax.LOOP_PREFIX_WORDS for w in self.words) \
                and t in ("for", "select", "repeat", "foreach", "if", "while", "until"):
            # zsh runs a compound command after `coproc`, `time` and `!` (probed: `coproc repeat 1 git push`, `coproc if
            # [[ -n x ]] git push`, `time if [[ -n x ]] git push` and `! if [[ -n x ]] git push` each ran it, and so did
            # `time foreach f (a) echo x; end` and `! foreach ...`)
            self.discard()
            if t in ("if", "while", "until"):
                self.open_conditional(t)
            else:
                self.open_loop(t)
            return
        if t == "}" and not self.skip and self.words and self.brace_closes():
            # zsh closes a `{ list }` at a `}` that follows a word with no terminator before it (probed: `if [[ -n x ]]
            # { echo then } else { echo else }` printed then, and `repeat 2 { git push }` pushed twice); bash takes the `}`
            # for an argument and leaves the group open, so closing it is the reading that sees what follows as well
            self.close_sublists()
            self.close_brace()
            return
        self.words.append(t)
        if self.skip and self.header in _NAMED_LOOPS:
            self.loop_header_word(t)
        if self.skip and self.header == "repeat":  # `repeat word`: one word of header, then the body
            self.end_header()
        elif t == "]]" and "[[" in self.words and not self.skip and self.stack and self.stack[-1].body == "cond":
            # zsh: `if [[ ... ]] git push`, `while [[ ... ]] { git push }`, `until [[ ... ]] git push`.  A quoted
            # `]]` never reaches here as this word: neutralize_quoted_globs has replaced its brackets with sentinels.
            self.end_header()

    def walk(self, tokens):
        toks = self.toks = [p for t in tokens for p in syntax.operator_parts(t)]
        i = 0
        while i < len(toks):
            t = toks[i]
            if self.expect_body and t not in syntax.BODY_DEFERRING:  # a terminator or a list operator may stand between them
                self.resolve_body(t)
            case = self.stack[-1] if self.stack and self.stack[-1].kind == "case" else None
            if t == ")" and case:
                if case.form is None:
                    case.form = self.case_in(case)  # settled here at the latest: later patterns hold no subject
                self.discard()  # the end of a case pattern (with the subject and `in` before the first)
                case.pattern = False
                self.branch()
            elif t == "(" and case and case.pattern:
                pass  # a pattern's optional opening parenthesis
            elif t == "|" and case and case.pattern and self.case_in(case):
                # SPD-187: a pattern's alternatives.  Its words run nothing and are discarded at its `)`, where consume
                # reads their substitutions; a `|` inside a substitution or a group there never reaches this frame
                pass
            elif t == ";" and case and case.pattern and case.form is None and self.case_header_break(case, i):
                # SPD-187: a newline after `case word in`, or between the word and its `in`, which the patterns follow
                if len(self.words) == 2:
                    self.discard()
            elif t == "(" and self.words and assignment_words.array_head(self.words[-1]) and not self.skip:
                j = i + 1
                while j < len(toks) and toks[j] != ")":
                    j += 1
                if unread.process_sub_in(toks[i + 1 : j]):  # SPD-198: a `<( )`, `>( )` or `=( )` in an array value, unread
                    unread.record_unread(self.a, "procsub-list", unread.unread_shown("".join(toks[i : j + 1])))
                # name=(a b), name=(), name[1,0]=(a): one assignment word, marked an array
                self.words[-1] += assignment_words.array_value(toks[i + 1 : j])
                i = j
            elif t == "(" and i + 1 < len(toks) and toks[i + 1] == ")" and not self.skip:
                self.define(self.words)  # name (), name() and zsh's `a b () ...`
                self.discard()  # name (): a function definition's header
                self.function_next = True
                i += 1
            elif t == "(" and self.skip:
                depth, j = 0, i  # for (( ... )) and for name ( ... ): the loop's header, not a subshell
                while j < len(toks):
                    depth += toks[j] in ("(", "<(", ">(")
                    depth -= toks[j] == ")"
                    if depth == 0:
                        break
                    j += 1
                if unread.process_sub_in(toks[i : j + 1]):  # SPD-198: a `<( )`, `>( )` or `=( )` in a for/foreach list, unread
                    unread.record_unread(self.a, "procsub-list", unread.unread_shown("".join(toks[i : j + 1])))
                self.words.append("".join(toks[i : j + 1]))
                i = j
                # `for (( ... ))`, `repeat (( ... ))`, or a `for`, `select` or `foreach` list after its names closed: its
                # body follows
                if self.header == "repeat" and len(self.words) <= 2 \
                        or self.header in _NAMED_LOOPS and self.loop_names(self.words[:-1]):
                    self.end_header()
            elif t in ("(", "<(", ">("):
                self.function_next = False
                self.push("sub", ")")
                self.stack[-1].prints = t == "("  # a process substitution's output goes to the file it stands for
                self.stack[-1].procsub = t == "<("
                self.stack[-1].bare = self.stack[-1].bare and t == "("  # a word in the command around it, not a command
                # mark_zsh_patterns keeps an arithmetic command's outer parenthesis and marks its inside, so `(( ... ))`
                # opens this frame with a next token that begins with the arithmetic sentinel for `(` (SPD-177)
                self.stack[-1].arith = t == "(" and i + 1 < len(toks) and toks[i + 1].startswith(_ARITH_OPEN)
            elif t == ")":
                self.close_sublists()
                if self.stack and self.stack[-1].closer == ")":
                    arith = self.stack[-1].arith
                    self.pop()
                    if arith and self.stack and self.stack[-1].kind in ("cond", "loop") and self.stack[-1].body == "cond":
                        # SPD-177: an arithmetic command ending an if/while/until condition ends it as a `]]` does, so a
                        # short body after the `))` -- a `cd` among it -- runs conditionally, in either directory
                        self.end_header()
                else:
                    self.finish()
            elif t in ("&&", "||"):
                self.reopen_condition()
                self.finish()
                self.end_pipeline()
                if t == "||" and self.uncertain:
                    self.a.cwds = directories.union_dirs(self.list_seen, self.a.cwds)
                self.conditional = True
                self.pipeline_start = self.a.cwds
                self.end_element()  # no pipe feeds what follows the operator
            elif t in ("|", "|&"):
                self.reopen_condition()
                self.finish(unsure=True)
                self.a.cwds = self.pipeline_start  # that element ran in its own process
                self.piped = True
                self.end_element(into_pipe=True)  # the next element reads what this one printed
            elif t == "&":
                self.reopen_condition()
                self.finish(unsure=True)
                self.close_sublists()  # `repeat 2 git push &`: the `&` ends the body's sublist, and the loop with it
                self.a.doubt.update(self.a.assigned[self.list_mark :])  # a background list assigns in its own process
                self.end_pipeline()
                self.a.cwds = self.list_start  # the whole and-or list ran in the background
                self.start_list()
            elif t in syntax.LIST_TERMINATORS:
                if self.skip and self.header in _NAMED_LOOPS:
                    self.end_header()  # `for f in a b;` and `for f;`: zsh takes what follows as the body
                self.finish()
                self.close_sublists()
                case = self.stack[-1] if self.stack and self.stack[-1].kind == "case" else None  # a short loop closed above it
                if t != ";" and case:
                    self.branch()
                    case.pattern = True
                else:
                    self.end_list()
            else:
                self.at = i  # close_brace reads the tokens after a `}` and may take them
                self.add_word(t)
                i = self.at
            i += 1
        self.finish()
        while self.stack:
            self.pop()
        self.end_list()
        while self.inner:  # a substitution no word held (a malformed line): still analysed
            analyse.analyse_isolated(self.a, self.inner.pop(0), self.depth + 1, *self.substitution_input())


def walk_line(a, tokens, inner, bodies, expanded, depth, start, glued=True, stdin=None, fed=False):
    """Walk a line's tokens (ShellWalk), and walk them again where a compound command on it has input redirections of its
    own (SPD-210): the shells perform those before the compound runs, so every command in it reads that input, but the
    walk reads them after its closer, when those commands are read already.  The second walk hands each such compound its
    input where it opens (ShellWalk.push), from the words the first found after its closer.  `start` is reading_start's
    state from before the first walk, put back so the second reads the line from where the first did; whatever else the
    first learnt stays -- doubt, a function's or a hashed name, an alias the hook cannot read, each of which only makes
    the reading stricter -- and a body read in its own process is read once per starting state (analyse_isolated), so
    the first walk's findings stand beside the second's, which adds the ones the input earns.  Returns the walk whose
    reading stands, for analyse_command to ask whether zsh split a brace off a word.

    A function the line defines runs its body on the input each call of it is given (SPD-212), which the walk reads where
    the body stands, before any call: the calls a walk finds record their inputs (read_call), and the line is walked again
    with each body read on one of them (ShellWalk.push, and finish for zsh's `name () command`), once per distinct input,
    until no call has handed a body input it has not been read with -- a call inside a body, or in a compound given input,
    shows its own only in the walk that reads it so.  Each such walk reads every body with its next unread input at once.
    The walks are bounded as SPD-203's per-call readings of a function are (positional.READINGS_PER_NAME), across the
    whole analysis (ShellAnalysis.body_walks), so a line nested in a body cannot multiply them: once they are spent, a
    line with a call's input still unread is walked once more with every body on it on input the line does not spell,
    which refuses a member a shell or an interpreter reading it there, as SPD-145 refuses one anywhere."""
    line = object()  # this reading of the line, which its definitions are bound to (ShellWalk.define)
    a.walking.add(line)
    try:
        return _walks(a, line, tokens, inner, bodies, expanded, depth, start, glued, stdin, fed)
    finally:
        a.walking.discard(line)


def _walks(a, line, tokens, inner, bodies, expanded, depth, start, glued, stdin, fed):
    """walk_line's walks of one reading of a line, `line`; the last one's reading stands."""
    walk = ShellWalk(a, inner, bodies, expanded, depth, glued, stdin, fed, line=line)
    walk.walk(tokens)
    found, defined, read, again = walk.found, walk.defined, {}, bool(walk.found)
    while True:
        calls = unread_inputs(a.function_inputs.get(line, {}), read)
        if not (calls or again):
            return walk
        spent = bool(calls) and a.body_walks >= positional.READINGS_PER_NAME
        if spent:
            calls = dict.fromkeys(defined, (None, True))  # every body on the line, on input the line does not spell
        elif calls:
            a.body_walks += 1
        restore_reading(a, start)
        walk = ShellWalk(a, inner, bodies, expanded, depth, glued, stdin, fed, found, calls, line)
        walk.walk(tokens)
        if spent:
            return walk
        again = False


def unread_inputs(inputs, read):
    """definition -> the first input its calls hand its body (ShellAnalysis.function_inputs) that no walk has read it
    with yet, each marked read in `read`, definition -> the reading keys read."""
    calls = {}
    for number, given in inputs.items():
        seen = read.setdefault(number, set())
        for key, pair in given.items():
            if key not in seen:
                seen.add(key)
                calls[number] = pair
                break
    return calls


def read_call(a, name, stdin, fed):
    """A call of `name` in command position, on standard input `stdin` (fed: whether anything stands there): the input each
    body the line defines under that name is handed, for walk_line to read it with (SPD-212).  A call on nothing hands the
    body nothing its reading where it is defined lacks.  A body in text whose reading is over -- an `eval` string's,
    which defines the function in the shell that runs the line -- is read on no call's input after it: the call is
    refused a member as a shell reading input the line does not spell is (script_files, "function")."""
    if not fed:
        return
    key = stdin_text.reading_key(stdin, fed)
    for line, number in a.function_bodies.get(name, ()):
        if line in a.walking:
            a.function_inputs.setdefault(line, {}).setdefault(number, {}).setdefault(key, (stdin, fed))
        else:
            script_files.record_script(a, "function", name)


def reading_start(a):
    """The state a walk reads a line's commands with, and changes as it goes, that restore_reading puts back: the
    directories, the variables, the loop depth, the aliases, the loop names read as dashless, and the function bodies
    bound to their names (SPD-212: each walk of the line binds its own definitions again, and zsh's reading's do not
    stand for bash's reading's while it walks), and shell/loop_bindings' loop and substitution values (SPD-146)."""
    return (a.cwds, dict(a.vars), a.loop_depth, dict(a.aliases), set(a.dashless_loops), bodies_copy(a.function_bodies),
            dict(a.loop_words), dict(a.derived), a.func_depth)


def restore_reading(a, start):
    """Put back reading_start's state, copied, so one start serves every walk of the line."""
    cwds, variables, loop_depth, aliases, dashless, function_bodies, loop_words, derived, func_depth = start
    a.cwds, a.vars, a.loop_depth, a.cd_uncertain = cwds, dict(variables), loop_depth, False
    a.aliases, a.dashless_loops, a.function_bodies = dict(aliases), set(dashless), bodies_copy(function_bodies)
    a.loop_words, a.derived, a.func_depth = dict(loop_words), dict(derived), func_depth


def bodies_copy(function_bodies):
    """A copy of ShellAnalysis.function_bodies that a definition added to it does not reach."""
    return {name: set(found) for name, found in function_bodies.items()}
