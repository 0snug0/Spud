"""shell/walk: ShellFrame and ShellWalk: one pass over a line's tokens.  Moved from bin/spud_ledger.py (SPD-065)."""

from . import analyse, directories, globbing, prepare, syntax
from ..hooks import hookio


class ShellFrame:
    """An open compound command: its kind, the word that closes it, the directories it started in, the directories any of
    its branches ended in so far, and the enclosing list's state to restore."""

    __slots__ = ("kind", "closer", "saved", "seen", "outer", "pattern", "mark", "body")

    def __init__(self, kind, closer, saved, outer, mark=0):
        self.kind, self.closer, self.saved, self.seen, self.outer = kind, closer, saved, saved, outer
        self.pattern = kind == "case"  # a case command reads a pattern first, and again after each ;;
        self.mark = mark  # how many assignments the line had made when it opened (SPD-043)
        # SPD-042: a loop's body form.  None for anything but a for, select or repeat; then "header" while its header is read,
        # "pending" until the body's first word, and then "long" (`do ... done`), "compound" (a `{ ... }` or `( ... )` body that
        # closes this frame with it) or "sublist" (zsh's SHORT_LOOPS: one and-or list, closing this frame where the list ends).
        self.body = None


class ShellWalk:
    """One pass over a command line's tokens (SPD-030).  Simple commands go to analyse_segment; the grammar around them
    decides which directories the shell may be in when each runs, as zsh 5.9 and bash 3.2 do (probed):

    - a subshell, a command or process substitution, a background job and every pipeline element but the last run in their
      own process, so a cd there does not carry out; the last element runs in zsh's own shell and in bash's subshell, so
      either directory follows it;
    - a cd that may not run (after && or ||, in an if, case or loop body, in a function body) or may fail (its target does
      not exist now) leaves either directory for what runs after its and-or list; what runs after `cd x &&` is in x;
    - a relative cd in a loop or a function body may repeat, so the hook cannot follow it;
    - a compound command's redirections open where it started;
    - zsh's SHORT_LOOPS (on by default, SPD-042) run a loop body with no `do` and `done`: after `repeat word`, after
      `for name ( word ... )` and after a `for`/`select` list closed by `;` or a newline, the body is a `do ... done`, a
      `{ list }`, a `( list )` or one sublist, and the loop ends where that sublist ends."""

    def __init__(self, a, inner, bodies, depth):
        self.a, self.inner, self.bodies, self.depth = a, list(inner), list(bodies), depth
        self.words, self.stack = [], []
        self.skip = False  # the words are a for, select or case header or a function's name, not a command
        self.header = None  # which header they are: "for" (for, select), "repeat" or "func"
        self.expect_body = False  # the header is complete: the next word decides the body's form (SPD-042)
        self.function_next = False  # `name ()` or `function name` was read: the next body is a function's
        self.redirect_cwds = syntax._CURRENT
        self.start_list()

    # -- lists and pipelines ------------------------------------------------------------
    def start_list(self):
        self.list_start = self.list_seen = self.pipeline_start = self.a.cwds
        self.uncertain = self.conditional = self.piped = False
        self.list_mark = len(self.a.assigned)  # the assignments before this and-or list (SPD-043)

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
        self.stack.append(ShellFrame(kind, closer, self.a.cwds, outer, len(self.a.assigned)))
        if kind in ("loop", "func"):
            self.a.loop_depth += 1
        self.words, self.skip, self.header, self.expect_body = [], False, None, False
        self.start_list()

    def pop(self):
        self.finish()
        self.end_list()
        frame = self.stack.pop()
        if frame.kind in ("loop", "func"):
            self.a.loop_depth -= 1
        assigned = self.a.assigned[frame.mark :]
        self.a.doubt.update(assigned)  # what a compound command assigned may not have run, or may not persist (SPD-043)
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
            self.pop()  # the short loop whose body this `{ ... }` or `( ... )` was (SPD-042)

    def branch(self):
        """then, else, elif, do, a case arm: the body may start from the directories the compound command started in."""
        self.finish()
        self.end_list()
        if self.stack and self.stack[-1].kind in ("cond", "case", "loop"):
            top = self.stack[-1]
            top.seen = directories.union_dirs(top.seen, self.a.cwds)  # where the branch before this one ended
            self.a.doubt.update(self.a.assigned[top.mark :])  # a branch may run without what an earlier one assigned (SPD-043)
            self.a.cwds = directories.union_dirs(top.saved, self.a.cwds)
        self.start_list()

    # -- zsh's short loop forms (SPD-042) -----------------------------------------------
    def open_loop(self, t):
        """A `for`, `select` or `repeat` in command position: its header is read, then its body, with or without `do`."""
        self.push("loop", "done")
        self.skip, self.header = True, "repeat" if t == "repeat" else "for"
        self.stack[-1].body = "header"

    def end_header(self):
        """The loop's header is complete.  Its body may follow with no `do`, so the next word decides the body's form."""
        self.finish()
        if self.stack and self.stack[-1].body == "header":
            self.stack[-1].body, self.expect_body = "pending", True

    def resolve_body(self, t):
        """The first word after a complete header: `do` opens a `do ... done` body, `{` or `(` a compound one that closes the
        loop with it, anything else a single sublist (probed in zsh 5.9: `repeat 2 echo a; echo b` ran `echo a` twice)."""
        self.expect_body = False
        frame = self.stack[-1] if self.stack else None
        if frame is None or frame.body != "pending":
            return
        frame.body = "long" if t == "do" else ("compound" if t in ("{", "(") else "sublist")

    def close_sublists(self):
        """A short loop whose body is one sublist ends where that sublist ends."""
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
            if header != "repeat":  # a for or select header assigns its name (SPD-043); a repeat count assigns nothing
                for w in cleaned:
                    a.doubt.update(syntax._NAME_RE.findall(prepare.deglob(w)))
            return
        before = a.cwds
        # SPD-043: an assignment in a command that may not run (after && or ||) or runs in its own process may not hold after it
        unsure = unsure or self.conditional or self.piped
        a.unsure += unsure
        if self.function_next:  # zsh's `name () command`: a body that runs when called, perhaps more than once
            self.function_next = False
            a.loop_depth += 1
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds)
            a.loop_depth -= 1
            a.cwds = directories.union_dirs(before, a.cwds)
        else:
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds)
        a.unsure -= unsure
        if a.cd_uncertain or (a.cwds != before and self.conditional):
            self.uncertain = True
        a.cd_uncertain = False
        self.list_seen = directories.union_dirs(self.list_seen, a.cwds)

    def add_word(self, t):
        if not self.words and not self.skip:
            if t == "{":
                self.push("func" if self.function_next else "group", "}")
                self.function_next = False
                return
            if t in ("}", "fi", "done", "esac"):
                self.close_sublists()
                if self.stack and self.stack[-1].closer == t:
                    self.pop()
                return
            if t in ("if", "while", "until"):
                self.push("cond" if t == "if" else "loop", "fi" if t == "if" else "done")
                return
            if t in ("for", "select", "repeat"):
                self.open_loop(t)
                return
            if t == "case":  # its subject and `in` are read with the first pattern and discarded at the pattern's `)`
                self.push("case", "esac")
                return
            if t in ("then", "else", "elif", "do"):
                self.branch()
                return
            if t == "function":
                self.function_next = self.skip = True
                self.header = "func"
                return
        if self.skip and self.function_next and t == "{":  # function name {
            self.discard()
            self.skip, self.header = False, None
            self.push("func", "}")
            self.function_next = False
            return
        if t in ("for", "select", "repeat") and not self.skip and self.words and all(w in syntax.LOOP_PREFIX_WORDS for w in self.words):
            self.discard()  # zsh runs a compound command after `coproc`, `time` and `!` (probed: `coproc repeat 1 git push` ran it)
            self.open_loop(t)
            return
        self.words.append(t)
        if self.skip and self.header == "repeat":  # `repeat word`: one word of header, then the body (SPD-042)
            self.end_header()

    def walk(self, tokens):
        toks = [p for t in tokens for p in syntax.operator_parts(t)]
        i = 0
        while i < len(toks):
            t = toks[i]
            if self.expect_body and t not in syntax.LIST_TERMINATORS:  # terminators may stand between a header and its body
                self.resolve_body(t)
            case = self.stack[-1] if self.stack and self.stack[-1].kind == "case" else None
            if t == ")" and case:
                self.discard()  # the end of a case pattern (with the subject and `in` before the first)
                case.pattern = False
                self.branch()
            elif t == "(" and case and case.pattern:
                pass  # a pattern's optional opening parenthesis
            elif t == "(" and self.words and syntax.ARRAY_ASSIGNMENT_RE.match(self.words[-1]) and not self.skip:
                j = i + 1
                while j < len(toks) and toks[j] != ")":
                    j += 1
                self.words[-1] += syntax._ARRAY_VALUE + " ".join(toks[i + 1 : j])  # name=(a b), name=(): one assignment word, marked an array
                i = j
            elif t == "(" and i + 1 < len(toks) and toks[i + 1] == ")" and not self.skip:
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
                    self.end_header()  # `for (( ... ))` or `for name ( ... )` closed: its body follows (SPD-042)
            elif t in ("(", "<(", ">("):
                self.function_next = False
                self.push("sub", ")")
            elif t == ")":
                self.close_sublists()
                if self.stack and self.stack[-1].closer == ")":
                    self.pop()
                else:
                    self.finish()
            elif t in ("&&", "||"):
                self.finish()
                self.end_pipeline()
                if t == "||" and self.uncertain:
                    self.a.cwds = directories.union_dirs(self.list_seen, self.a.cwds)
                self.conditional = True
                self.pipeline_start = self.a.cwds
            elif t in ("|", "|&"):
                self.finish(unsure=True)
                self.a.cwds = self.pipeline_start  # that element ran in its own process
                self.piped = True
            elif t == "&":
                self.finish(unsure=True)
                self.close_sublists()  # `repeat 2 git push &`: the `&` ends the body's sublist, and the loop with it
                self.a.doubt.update(self.a.assigned[self.list_mark :])  # a background list assigns in its own process (SPD-043)
                self.end_pipeline()
                self.a.cwds = self.list_start  # the whole and-or list ran in the background
                self.start_list()
            elif t in syntax.LIST_TERMINATORS:
                if self.skip and self.header == "for":
                    self.end_header()  # `for f in a b;` and `for f;`: zsh takes what follows as the body (SPD-042)
                self.finish()
                self.close_sublists()
                case = self.stack[-1] if self.stack and self.stack[-1].kind == "case" else None  # a short loop closed above it
                if t != ";" and case:
                    self.branch()
                    case.pattern = True
                else:
                    self.end_list()
            else:
                self.add_word(t)
            i += 1
        self.finish()
        while self.stack:
            self.pop()
        self.end_list()
        while self.inner:  # a substitution no word held (a malformed line): still analysed
            analyse.analyse_isolated(self.a, self.inner.pop(0), self.depth + 1)
