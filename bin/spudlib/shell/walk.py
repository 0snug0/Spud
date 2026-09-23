"""shell/walk: ShellFrame and ShellWalk: one pass over a line's tokens."""

from . import analyse, assignment_words, directories, globbing, prepare, stdin_text, syntax
from ..hooks import hookio


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
    `{ list }` group, whose fork the `coproc` before it makes), "loop" a for, select, repeat, while or until,
    "cond" an if, "case" a case, "func" a function body."""

    __slots__ = ("kind", "closer", "saved", "seen", "outer", "pattern", "mark", "body", "funcs", "printed", "earlier", "stdin", "prints")

    def __init__(self, kind, closer, saved, outer, mark=0, funcs=None):
        self.kind, self.closer, self.saved, self.seen, self.outer = kind, closer, saved, saved, outer
        self.pattern = kind == "case"  # a case command reads a pattern first, and again after each ;;
        self.mark = mark  # how many assignments the line had made when it opened
        # the functions defined before a `( ... )` subshell opened, restored when it closes; None for every other
        # frame kind, whose function definitions reach a call after it and are kept.
        self.funcs = funcs
        # a loop's or a conditional's body form.  None for anything but a for, select or repeat (which starts
        # at "header") and an if, while or until (which starts at "cond", its condition list); then "pending" or "cond-pending"
        # once that is complete, until the body's first word, and then "long" (`do ... done`, `then ... fi`), "compound" (a
        # `{ ... }` or `( ... )` body) or "sublist" (zsh's SHORT_LOOPS and SHORT_REPEAT: one and-or list, closing this frame
        # where the list ends).  A loop's compound body closes the loop with it; a conditional's becomes "sublist", since an
        # `else` or an `elif` may still follow it (probed: `if [[ -n x ]] { echo a } else { echo b }` ran).  An `elif`
        # puts a conditional's frame back to "cond", the elif's own condition list, from any of these (see branch).
        self.body = None
        # the text the pipeline element around it had printed when it opened, the text the lists before that
        # element in the enclosing compound command had printed, and that element's standard input with the one every
        # list inside this compound starts from -- all of it put back by ShellWalk.pop.  `prints`: what the commands in
        # it print reaches the enclosing element's own output -- not so for a function body, which prints when it is
        # called, nor for a process substitution, whose output goes to the file it stands for.
        # `stdin` carries two flags beside those two texts: whether anything at all stands on that input.
        self.printed, self.earlier, self.stdin, self.prints = "", "", (None, None, False, False), kind != "func"


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
    - the same holds for an `if`, `while` or `until` whose condition ends in `[[ ... ]]`, which closes the condition
      the way a terminator closes a loop header, so `then` and `do` are optional there too; an `elif` after such a body
      opens the next condition list the same way, so its own `[[ ... ]]` and body are read as the `if`'s were;
    - zsh's try-always form, `{ list } always { list }`, is one compound command holding both lists, so the always
      block runs where the try block left the shell and the compound ends at the always block's `}` (see close_brace);
    - zsh splits a brace off the word it is glued to: `{git push}` is the group `{ git push }` (see add_word).
      bash does not, so `glued` says which reading this walk is, and `split_brace` whether zsh's split one off this line:
      analyse_command then walks the line again the other way and keeps both readings, as it does for zsh's globs."""

    def __init__(self, a, inner, bodies, depth, glued=True):
        self.a, self.inner, self.bodies, self.depth = a, list(inner), list(bodies), depth
        self.glued = glued  # zsh's reading: a brace glued to a word opens or closes a group where a lone one would
        self.split_brace = False  # ... and it did on this line, so the reading differs from bash's
        self.words, self.stack = [], []
        self.skip = False  # the words are a for, select or case header or a function's name, not a command
        self.header = None  # which header they are: "for" (for, select), "repeat" or "func"
        self.expect_body = False  # the header is complete: the next word decides the body's form
        self.function_next = False  # `name ()` or `function name` was read: the next body is a function's
        self.redirect_cwds = syntax._CURRENT
        self.toks, self.at = [], 0  # the line's tokens, and the index of the one add_word is reading
        # the text the pipeline element being read has printed so far, the text the elements before it in this
        # compound command printed, the text the element before it in its pipeline printed -- which this one reads on
        # standard input -- and the input the compound command itself was given.  None is text the line does not spell,
        # which absorbs (stdin_text.joined).
        self.printed, self.frame_printed, self.piped_text, self.frame_stdin = "", "", None, None
        # whether a pipe feeds the element being read at all, and whether one fed the compound command around
        # it, which the texts above cannot say (None is both "nothing" and "text the line does not spell").
        self.piped_fed, self.frame_stdin_fed = False, False
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
        funcs = set(self.a.functions) if kind == "sub" else None  # a subshell's own function definitions do not escape
        frame = ShellFrame(kind, closer, self.a.cwds, outer, len(self.a.assigned), funcs)
        # what the element around it printed so far is kept for after the compound command, and the standard
        # input that element was given is the input every list inside it starts from
        frame.printed, frame.earlier = self.printed, self.frame_printed
        frame.stdin = (self.piped_text, self.frame_stdin, self.piped_fed, self.frame_stdin_fed)
        self.printed, self.frame_printed = "", ""
        self.frame_stdin, self.frame_stdin_fed = self.piped_text, self.piped_fed
        self.stack.append(frame)
        if kind in ("loop", "func"):
            self.a.loop_depth += 1
        self.words, self.skip, self.header, self.expect_body = [], False, None, False
        self.start_list()

    def pop(self):
        self.finish()
        self.end_list()  # ... which joins the last element's text to the rest of what this compound command printed
        frame = self.stack.pop()
        # the compound command's own output stands where it opened, in the element that holds it
        self.printed = stdin_text.joined(frame.printed, self.frame_printed) if frame.prints else frame.printed
        self.frame_printed = frame.earlier
        self.piped_text, self.frame_stdin, self.piped_fed, self.frame_stdin_fed = frame.stdin
        if frame.kind == "sub":
            self.a.functions = frame.funcs  # a function defined in a subshell does not reach a call after it
        if frame.kind in ("loop", "func"):
            self.a.loop_depth -= 1
        assigned = self.a.assigned[frame.mark :]
        self.a.doubt.update(assigned)  # what a compound command assigned may not have run, or may not persist
        if frame.kind == "func":
            self.a.sticky.update(assigned)  # a function body assigns again whenever it is called
        inner = self.a.cwds
        after = frame.saved if frame.kind == "sub" else (inner if frame.kind == "group" else directories.union_dirs(frame.seen, inner))
        (self.list_start, self.list_seen, self.pipeline_start, self.uncertain, self.conditional, self.piped, self.words,
         self.skip, self.header, self.expect_body) = frame.outer
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
        """A `for`, `select` or `repeat` in command position: its header is read, then its body, with or without `do`."""
        self.push("loop", "done")
        self.skip, self.header = True, "repeat" if t == "repeat" else "for"
        self.stack[-1].body = "header"

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
        if any(_value_may_start_with_dash(w) for w in self.words[at + 1 :]):
            self.a.dashless_loops.difference_update(names)
        else:
            self.a.dashless_loops.update(names)

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
        frame.body = "long" if t in ("do", "then") else ("compound" if braced or t == "(" else "sublist")

    def close_sublists(self):
        """A short loop or conditional whose body is one sublist ends where that sublist ends."""
        while self.stack and self.stack[-1].body == "sublist":
            self.pop()

    # -- simple commands ----------------------------------------------------------------
    def consume(self, words):
        """Analyse the substitutions in these words (expanded before the command runs, each in its own process) and take the
        here-document bodies their `<<` operators read; return the words without the operators and their delimiters."""
        for w in words:
            for _ in range(w.count(hookio.SUBST)):
                if self.inner:
                    analyse.analyse_isolated(self.a, self.inner.pop(0), self.depth + 1)
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
        cleaned, bodies, k = [], [], 0
        while k < len(words):
            if words[k] in ("<<", "<<-"):
                if self.bodies:
                    bodies.append(self.bodies.pop(0))
                k += 2
                continue
            cleaned.append(words[k])
            k += 1
        return cleaned, bodies

    def discard(self):
        self.consume(self.words)
        self.words = []

    def finish(self, unsure=False):
        """Analyse the simple command read so far.  `unsure`: it runs in its own process (a pipeline element, a background job)."""
        words, self.words = self.words, []
        skip, self.skip = self.skip, False
        header, self.header = self.header, None
        redirect_cwds, self.redirect_cwds = self.redirect_cwds, syntax._CURRENT
        if not words:
            return
        cleaned, bodies = self.consume(words)
        a = self.a
        if skip:
            if header != "repeat":  # a for or select header assigns its name; a repeat count assigns nothing
                for w in cleaned:
                    a.doubt.update(syntax._NAME_RE.findall(prepare.deglob(w)))
            return
        before = a.cwds
        # an assignment in a command that may not run (after && or ||) or runs in its own process may not hold after it
        unsure = unsure or self.conditional or self.piped
        a.unsure += unsure
        if self.function_next:  # zsh's `name () command`: a body that runs when called, perhaps more than once
            self.function_next = False
            a.loop_depth += 1
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, self.piped_text, self.piped_fed)
            a.loop_depth -= 1
            a.cwds = directories.union_dirs(before, a.cwds)
        else:
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, self.piped_text, self.piped_fed)
            # what this command adds to the text its pipeline element prints, which a shell after a `|` runs
            self.printed = stdin_text.joined(self.printed, stdin_text.printed_text(cleaned, bodies, self.piped_text))
        a.unsure -= unsure
        if a.cd_uncertain or (a.cwds != before and self.conditional):
            self.uncertain = True
        a.cd_uncertain = False
        self.list_seen = directories.union_dirs(self.list_seen, a.cwds)

    # -- braces --------------------------------------------------------------------------
    def in_pattern(self):
        """A case command is reading its subject, `in` or a pattern: no command position, so no word there opens a group."""
        return bool(self.stack) and self.stack[-1].kind == "case" and self.stack[-1].pattern

    def open_brace(self):
        """Open the `{ list }` a `{` read now begins, and say whether it did: in command position a group, or a function's
        body after `name ()` and zsh's anonymous `()`; after `function name` a function's body; after only `coproc`,
        `time` and `!` a prefixed group; after a lone `always` a group.  Anywhere else the `{` is a word."""
        if not self.words and not self.skip:
            self.push("func" if self.function_next else "group", "}")
            self.function_next = False
            return True
        if self.skip and self.function_next:  # function name {
            self.a.functions.update(_function_names(self.words))  # a function defined here shadows a later call's name
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
        hook always read it.  After `fi`, `done` or `esac` the `}` closes what a lone one would after that word."""
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
        closer = rest if not self.words and rest in ("fi", "done", "esac") else None
        return rest if self.brace_closes(closer) else None

    def add_word(self, t):
        """One word, read in its place.  In zsh's reading a word that starts with `{` opens the group a lone `{`
        would open there -- never in a case pattern -- and the rest of it is read as the next word, from command position
        (probed: `{vcs try}`, `time {vcs try}`, `f() {vcs a}`, `{vcs}` and `{}` ran as groups, `{"vcs" try}` and
        `{\\vcs try}` too; `{vcs,x}` ran a command named `vcs,x`, no brace expansion); and a word whose `}` glued_close
        splits off is read, then the `}` is, as a lone one after it.  bash reads both braces as part of their words."""
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
        if not self.words and not self.skip:
            if t in ("}", "fi", "done", "esac"):
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
            if t in ("for", "select", "repeat"):
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
        if not self.skip and self.words and all(w in syntax.LOOP_PREFIX_WORDS for w in self.words) and t in ("for", "select", "repeat", "if", "while", "until"):
            # zsh runs a compound command after `coproc`, `time` and `!` (probed: `coproc repeat 1 git push`, `coproc if
            # [[ -n x ]] git push`, `time if [[ -n x ]] git push` and `! if [[ -n x ]] git push` each ran it)
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
        if self.skip and self.header == "for":
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
                self.discard()  # the end of a case pattern (with the subject and `in` before the first)
                case.pattern = False
                self.branch()
            elif t == "(" and case and case.pattern:
                pass  # a pattern's optional opening parenthesis
            elif t == "(" and self.words and assignment_words.array_head(self.words[-1]) and not self.skip:
                j = i + 1
                while j < len(toks) and toks[j] != ")":
                    j += 1
                # name=(a b), name=(), name[1,0]=(a): one assignment word, marked an array
                self.words[-1] += assignment_words.array_value(toks[i + 1 : j])
                i = j
            elif t == "(" and i + 1 < len(toks) and toks[i + 1] == ")" and not self.skip:
                self.a.functions.update(_function_names(self.words))  # name (), name() and zsh's `a b () ...`
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
                self.words.append("".join(toks[i : j + 1]))
                i = j
                if self.header in ("for", "repeat") and len(self.words) <= 2:
                    self.end_header()  # `for (( ... ))` or `for name ( ... )` closed: its body follows
            elif t in ("(", "<(", ">("):
                self.function_next = False
                self.push("sub", ")")
                self.stack[-1].prints = t == "("  # a process substitution's output goes to the file it stands for
            elif t == ")":
                self.close_sublists()
                if self.stack and self.stack[-1].closer == ")":
                    self.pop()
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
                if self.skip and self.header == "for":
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
            analyse.analyse_isolated(self.a, self.inner.pop(0), self.depth + 1)
