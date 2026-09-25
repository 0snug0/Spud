"""shell/walk: ShellFrame and ShellWalk: one pass over a line's tokens.

Past 1000 lines as one class whose methods share state: the walk's frames, its token position and the lifted text it
has consumed are what a definition's body is taken down from (ShellWalk.define through settle_definitions), so that
reading stays with the walk.  LineBody and the module functions that read one at a call (read_call, read_line_body,
assign_function, hook_functions) are a seam of their own, shell/line_functions (SPD-280)."""

from . import (analyse, assignment_words, directories, expansions, globbing, held_text, heredocs, line_aliases,
               line_functions, loop_bindings, prepare, reevaluation, stdin_text, syntax, unread)
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


def _lifted(word):
    """How many lifted bodies a word's placeholders pair with, in order (ShellWalk.consume): a `<( )`'s file name none."""
    return word.count(hookio.SUBST) - word.count(PROCSUB_FILE)


def _assigned_words(header, words):
    """The words of a complete header, `words`, whose names it assigns (ShellWalk.finish): a for's, a select's or a
    foreach's names alone -- the first word, and for a for or a foreach each word after it zsh reads as one more name,
    up to the `in` or the `( ... )` that opens the list (names_end) -- and never its list's, which the shell expands
    before the loop runs, so a variable a list word spells keeps its value (SPD-269: `x=1; for w in x; do :; done; echo
    $x` printed 1).  An assignment a list word's own expansion makes (`$((x=5))`, a glob qualifier's code) is read with
    its words (consume), inside the loop's frame, which leaves it doubted, and `${x:=v}` with the line (analyse_command).
    Every word of any other header: an arithmetic one's expressions may assign any name they spell as the loop runs."""
    if header not in _NAMED_LOOPS or not words or words[0].startswith("(" + _ARITH_OPEN):
        return words
    end = 1
    while header != "select" and end < len(words) and syntax.loop_name(words[end]):
        end += 1
    return words[:end]


def _value_may_start_with_dash(word):
    """Whether a masked word, as the shell expands it, may start with `-`: spelled so, an expansion or an operand
    the line does not spell at its start, or a glob or brace list that may expand to such a word."""
    if word.startswith("-") or word.startswith((hookio.SUBST, "`")) or word[:1] in (syntax.FIND_PATH, syntax.INPUT_OPERAND, syntax.ANY_PATH):
        return True
    if word.startswith("$") and not word.startswith("$" + syntax._LITERAL_DOLLAR):
        return True
    return globbing.active_glob_word(word) and (globbing.may_start_with_dash(word) or word.startswith("{"))


# SPD-291: the operators after which a newline carries the command on to the next line, so the shell parses both lines
# before it runs either (ShellWalk.new_line; probed: `alias ls=... &&` then `ls -d /` printed `/` in sh, zsh and dash)
_CARRIED = frozenset(("&&", "||", "|", "|&"))
# The tokens after which a `case` opens a case command, not a word of one (_procsub_words)
_COMMAND_START = frozenset((";", "&&", "||", "|", "|&", "&", "(", "<(", ">(", "{", "!", "then", "else", "elif", "do", ";;",
                            ";&", ";|"))


def _procsub_words(toks, start):
    """The indices of the tokens a `<( )` or `>( )` body starting at toks[start] holds as its own, up to the `)` that
    closes it (ShellWalk.expand_globals, SPD-293): not those of a `<( )` or `>( )` nested in it, which the walk expands
    where that one opens.  A case command's patterns end in a `)` that closes nothing (`cat <(case a in a) echo X;;
    esac)`), so a `)` read while the innermost thing open is a case, between its `case` in command position and its
    `esac`, is a pattern's; a `case` the walk reads as an argument opens one here too, and the body is then read to the
    line's end, words the body does not hold among them: more than zsh runs, never less."""
    own, open_, prev = [], [], None
    for j in range(start, len(toks)):
        t = toks[j]
        if t in ("(", "<(", ">("):
            open_.append(t)
        elif t == ")" and not (open_ and open_[-1] == "case"):
            if not open_:
                break
            open_.pop()
        elif t == "case" and (prev is None or prev in _COMMAND_START):
            open_.append(t)
        elif t == "esac" and open_ and open_[-1] == "case":
            open_.pop()
        if "<(" not in open_ and ">(" not in open_:
            own.append(j)
        prev = t
    return own


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


# The analysis's lists a reading appends its entries to, in the order LineBody.marks and pending hold them
_RECORDS = ("findings", "redirects", "git_calls", "git_writes", "arg_writes")


class ShellFrame:
    """An open compound command: its kind, the word that closes it, the directories it started in, the directories any of
    its branches ended in so far, and the enclosing list's state to restore.

    The kinds: "group" a `{ list }`, "sub" one that runs in its own process (a `( list )` subshell, and a coproc's
    `{ list }` group, whose fork the `coproc` before it makes), "loop" a for, select, repeat, foreach, while or until,
    "cond" an if, "case" a case, "func" a function body.  A foreach's closer is `end` until its body turns out to be a
    `do ... done` (resolve_body)."""

    __slots__ = ("kind", "closer", "saved", "seen", "outer", "pattern", "mark", "body", "funcs", "printed", "earlier", "stdin", "prints",
                 "procsub", "serial", "bare", "defines", "arith", "bound", "form", "locals", "assigns", "element_mark",
                 "alias_view", "again")

    def __init__(self, kind, closer, saved, outer, mark=0, funcs=None):
        self.kind, self.closer, self.saved, self.seen, self.outer = kind, closer, saved, saved, outer
        self.pattern = kind == "case"  # a case command reads a pattern first, and again after each ;;
        self.mark = mark  # how many assignments the line had made when it opened
        # the locals the innermost function body the shell holds had declared when it opened (ShellAnalysis.body_locals),
        # put back when it closes: a declaration inside a compound command, which may not run it (a condition, a loop) or
        # runs it in a subshell, makes no name surely local after it -- a group's is read the same way, the safe side
        # (SPD-246); None outside such a body
        self.locals = None
        # the functions defined before a `( ... )` subshell opened, with their bodies (ShellAnalysis.functions and
        # function_bodies), restored when it closes; None for every other frame kind, whose function definitions reach a
        # call after it and are kept.
        self.funcs = funcs
        # the function definition, a LineBody, whose body this compound command is (ShellWalk.define), or None (SPD-212,
        # SPD-277)
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
        # a process substitution's body read a level into the line's alias scope (ShellWalk.open_process_substitution,
        # SPD-287): the AliasView in force where it opened, put back when it closes, how many levels of alias scope it
        # opened (1, or 0 where it only put the snapshot's aliases back in force inside a snapshot function's body, SPD-300),
        # and, where a global alias's words were set among its own (expand_globals), the ShellAnalysis.quoted_text and
        # quoted_sets it opened with, put back as well (SPD-315), else None; None for every other frame
        self.alias_view = None
        # which compound command of the walk this is, counting from 0 in the order they open -- the same in every walk of
        # one line's tokens, which is how walk_line hands the second walk each compound's input (SPD-210) -- and whether
        # it opens a command, no word before it, so that the words after its closer are its own redirections: not so for
        # a process substitution, nor for a `(` after words.
        self.serial, self.bare = 0, False
        # the walk's element_mark and list_defined where it opened, put back when it closes (SPD-272)
        self.element_mark = (0, 0)
        # this `( ... )` frame is the subshell mark_zsh_patterns leaves for an arithmetic command `(( ... ))` (its outer
        # parenthesis kept, its inside marked), so its close ends a condition it stands at the end of (SPD-177); and the
        # names that arithmetic assigned where the command stands (SPD-225, ShellWalk.pop), which a pipe after it doubts
        self.arith, self.assigns = False, ()
        # the for loop's variable shell/loop_bindings bound for its body (SPD-146), unbound where the loop closes
        self.bound = None
        # a case's form (ShellWalk.case_in): "in" where `in` follows its word, "brace" for zsh's `case word { ... }`
        # (SPD-185, ShellWalk.open_brace_case), whose closer is then `}`, False for neither, and None until the walk has
        # read that far
        self.form = None
        # a loop's own start, which ShellWalk.read_loop_again reads it from again where its body changed the line's alias
        # table (SPD-294): the index of its reserved word among the walk's tokens, that word, how much of the lifted text
        # and of the here-documents the walk had consumed, and the table it opened with (_alias_state); None for any
        # other frame
        self.again = None


def _alias_state(a):
    """The line's alias table as it stands, for a loop to compare where it opens and closes (ShellWalk.read_loop_again):
    its line_aliases.AliasView, or None where it holds no alias and none the hook cannot read."""
    return line_aliases.AliasView(a, False) if a.aliases or a.alias_unknown else None


def _set_aliases(a, state):
    """Put the table an _alias_state holds back as the line's (its names' doubt stays as it stands, the stricter)."""
    a.aliases, a.alias_unknown = ({}, False) if state is None else (dict(state.table), state.unknown)


# How many times a loop's body is read again where each reading changes the line's alias table (SPD-294,
# ShellWalk.read_loop_again): one more reading settles every table an `alias` the body spells defines, and each alias a
# definition runs through (an eval of an alias whose body defines one) takes one more; past this the table is one the
# hook cannot read
_ALIAS_PASSES = 4


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
    - zsh's brace form of case, `case word { pattern) list ;; ... }`, reads its patterns as `in ... esac` does and closes
      at its `}`, after a body's words too; where a pattern would stand an `esac` or a `}` closes either form (SPD-185,
      open_brace_case and read_word);
    - zsh's try-always form, `{ list } always { list }`, is one compound command holding both lists, so the always
      block runs where the try block left the shell and the compound ends at the always block's `}` (see close_brace);
    - zsh splits a brace off the word it is glued to: `{git push}` is the group `{ git push }` (see add_word).
      bash does not, so `glued` says which reading this walk is, and `split_brace` whether zsh's split one off this line:
      analyse_command then walks the line again the other way and keeps both readings, as it does for zsh's globs;
    - every command on the line starts from the standard input the line is run on -- `stdin`, None where the line does not
      spell it, and `fed`, whether anything stands there: a `-c` string's is its shell's (SPD-210) -- and a compound
      command's own input redirections stand where it opens, as a simple command's do (walk_line); a substitution in a
      command's words reads the input of the list it stands in (substitution_input);
    - a function's body runs where and when each call runs it, on the input the call is given (SPD-212, SPD-277): the
      walk takes a definition's tokens down as a LineBody (define, end_body), which each call reads from the state it
      starts in (line_functions.read_call), and reads the body in place as well, where it stands, which counts only
      where no call reads it (settle_definitions); `line` is the reading the definitions are bound to, and `into` says
      this walk is a call's reading of a compound body, whose input stands as a pipe into it
      (line_functions.read_line_body);
    - a shell that reads its text a line at a time (`lines`: a script fed to it, sh's `-c` string) parses each line once
      the lines before it ran, a compound command's lines with the line it starts on, so a newline (syntax.LINE_END)
      that ends one starts the next with the aliases the lines before it left (new_line, SPD-291) -- eval's words, a
      trap's action and a `$( )` body too where the shell parses them so (held_text.parsed_lines), and a `<( )` body's
      lines where it parses that so (new_body_line, SPD-323)."""

    def __init__(self, a, inner, bodies, expanded, depth, glued=True, stdin=None, fed=False, inputs=None, line=None, into=False,
                 text=None, lines=False):
        self.a, self.inner, self.bodies, self.depth = a, list(inner), list(bodies), depth
        self.expanded = list(expanded)  # whether the shell expands each body (heredocs.strip_heredocs)
        # ... and all three as they came, for the stretch a definition's body lifts (end_body)
        self.lifted, self.docs, self.expands = tuple(inner), tuple(bodies), tuple(expanded)
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
        # The function definitions (SPD-212, SPD-277): the LineBody objects this walk has read so far, in order, the one
        # whose body is still to come, and the ones whose body the walk is reading, innermost last; `source`, the tokens as
        # the walk first had them, which a body's are taken from; `queued`, the bodies an assignment to zsh's `functions` or
        # to a hook array handed the command being read, for finish to read after it (SPD-278, SPD-276).
        self.defined, self.pending_body, self.capturing, self.source, self.queued = [], None, [], None, []
        self.line = line
        # a call's reading of a compound body (SPD-212): and, once its own output redirections are read (finish), the text
        # it prints where a pipe follows the call, which zsh joins them to (SPD-272, line_functions.read_line_body); None
        # where that is the text the walk printed
        self.into, self.body_piped = into, None
        # read_loop_again's walk of a loop once more (SPD-294): `once`, it ends where the loop it opens first closes, and
        # `header_state`, (the table the loop's header ran with, the one its body is read with) until the header is over
        self.once, self.header_state = False, None
        # SPD-293: the text the tokens were read from (analyse_command's; for a body line_functions reads, the text its
        # definition was read from, LineBody.written, SPD-311), whose unquoted words inside a `<( )` body are the ones
        # expand_globals may take for a global alias's name, found when it first asks (`plain`); and, once it has set an
        # alias's expansion in a word's place, `origin`: each token's index among the tokens as the line spelled them,
        # which `source` then holds (pristine)
        self.text, self.plain, self.origin = text, None, None
        # SPD-291: the shell reads the text a line at a time (`lines`), so each line after one that left an alias is read
        # a level into ShellAnalysis.alias_scope with the table the lines before it left (new_line): how many levels
        # that opened (0 or 1), and the AliasView to put back when the walk ends; and the text's quotes to put back then,
        # where a line's global alias set words the text does not spell (expand_line_globals), else None
        self.lines, self.line_scope, self.line_saved, self.line_quoted = lines, 0, None, None
        self.start_list()
        self.pipe_feeds = into  # a call's input, a pipe into the compound body (SPD-212)

    # -- lists and pipelines ------------------------------------------------------------
    def start_list(self):
        self.list_start = self.list_seen = self.pipeline_start = self.a.cwds
        self.uncertain = self.conditional = self.piped = False
        self.list_mark = len(self.a.assigned)  # the assignments before this and-or list
        self.list_defined = len(self.defined)  # ... and the definitions (uncertain_element)
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
        self.element_mark = len(self.defined)  # the definitions the element read from here on (uncertain_element)

    def uncertain_element(self, background=False):
        """The pipeline element read so far runs in a process of its own, a `|` after it -- or, `background`, the whole
        and-or list, a `&` after it: no definition in it reaches a call after it in the line's shell (LineBody.certain,
        SPD-272)."""
        for body in self.defined[self.list_defined if background else self.element_mark :]:
            body.certain = False

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
        self.close_definition()  # a definition's body closed with nothing after it but this compound (SPD-277)
        outer = (self.list_start, self.list_seen, self.pipeline_start, self.uncertain, self.conditional, self.piped, self.words,
                 self.skip, self.header, self.expect_body)
        # a subshell's own function definitions do not escape, nor their bodies
        funcs = (set(self.a.functions), bodies_copy(self.a.function_bodies)) if kind == "sub" else None
        frame = ShellFrame(kind, closer, self.a.cwds, outer, len(self.a.assigned), funcs)
        if self.a.body_locals is not None:
            frame.locals = dict(self.a.body_locals[-1])
        frame.serial, frame.bare, self.opened = self.opened, not self.words, self.opened + 1
        if frame.bare:  # the compound command a definition's header is followed by, and not a `<( )` in a command's words
            frame.defines, self.pending_body = self.pending_body, None
            if frame.defines is not None:
                self.function_next = False  # `f() if ...`: the body is this compound, not its first simple command
        # what the element around it printed so far is kept for after the compound command, and the standard
        # input that element was given is the input every list inside it starts from -- with the compound's own input
        # redirections, where an earlier walk of the line found any after its closer, read as a simple command's are,
        # zsh's reading and bash's (stdin_text.command_input, SPD-209 and SPD-210)
        frame.printed, frame.earlier, frame.element_mark = self.printed, self.frame_printed, (self.element_mark, self.list_defined)
        frame.stdin = (self.piped_text, self.frame_stdin, self.piped_fed, self.frame_stdin_fed, self.pipe_feeds)
        self.printed, self.frame_printed = "", ""
        self.frame_stdin, self.frame_stdin_fed = self.piped_text, self.piped_fed
        if frame.serial in self.inputs:
            # ... where a call's input stands as a pipe into a compound body (`into`), zsh reads it and then each of the
            # definition's own in turn, and bash their last alone (SPD-212, probed: FunctionInputTest)
            redirects, bodies = self.inputs[frame.serial]
            self.frame_stdin = stdin_text.command_input(redirects, bodies, self.piped_text, self.pipe_feeds, self.a)
            self.frame_stdin_fed = stdin_text.input_fed(redirects, bodies, self.piped_fed)
        self.stack.append(frame)
        if kind in ("loop", "func"):
            self.a.loop_depth += 1
        self.a.func_depth += kind == "func"
        self.words, self.skip, self.header, self.expect_body = [], False, None, False
        self.start_list()

    def pop(self):
        # an arithmetic command's text, which the frame's own reading of its words (inert, all marked) does not read for
        # what it assigns: read_arithmetic_command does, once the frame is closed (SPD-225)
        arithmetic = " ".join(self.words) if self.stack[-1].arith else None
        self.finish()
        self.end_list()  # ... which joins the last element's text to the rest of what this compound command printed
        frame = self.stack.pop()
        if frame.alias_view is not None:  # its body is read: the scope open_process_substitution opened closes (SPD-287)
            self.a.alias_view, opened, quoted = frame.alias_view
            self.a.alias_scope -= opened
            if quoted is not None:  # ... and the text's quotes are seen again past it (SPD-315)
                self.a.quoted_text, self.a.quoted_sets = quoted
        # the compound command's own output stands where it opened, in the element that holds it
        self.printed = stdin_text.joined(frame.printed, self.frame_printed) if frame.prints else frame.printed
        self.frame_printed, (self.element_mark, self.list_defined) = frame.earlier, frame.element_mark
        self.piped_text, self.frame_stdin, self.piped_fed, self.frame_stdin_fed, self.pipe_feeds = frame.stdin
        if frame.kind == "sub":
            self.a.functions, self.a.function_bodies = frame.funcs  # a function defined in a subshell does not reach a call after it
        if frame.locals is not None:
            self.a.body_locals[-1] = frame.locals  # a local declared inside it may not be one after it (SPD-246)
        if frame.kind in ("loop", "func"):
            self.a.loop_depth -= 1
        self.a.func_depth -= frame.kind == "func"
        loop_bindings.unbind_frame(frame, self.a)
        assigned = self.a.assigned[frame.mark :]
        self.a.doubt.update(assigned)  # what a compound command assigned may not have run, or may not persist
        if frame.kind == "func" or frame.defines is not None:
            self.a.sticky.update(assigned)  # a function body assigns again whenever it is called
        if frame.again is not None:
            if self.once and not self.stack:
                del self.toks[self.at + 1 :]  # read_loop_again's walk: its loop is read, and nothing after it is
            else:
                self.read_loop_again(frame)
        inner = self.a.cwds
        if frame.defines is not None:
            # a definition runs nothing where it stands, so the line is where it was; each call moves it (SPD-277)
            after = frame.saved
        else:
            after = frame.saved if frame.kind == "sub" else (inner if frame.kind == "group" else directories.union_dirs(frame.seen, inner))
        (self.list_start, self.list_seen, self.pipeline_start, self.uncertain, self.conditional, self.piped, self.words,
         self.skip, self.header, self.expect_body) = frame.outer
        # the words up to the next terminator are this compound's redirections (finish), or, after a short loop's compound
        # body, the loop's, whose pop below takes this over
        self.closed = frame if frame.bare else None
        if frame.procsub and not self.in_pattern():
            # the file name `<( list )` hands the command: a word the line does not spell, which every reader of
            # hookio.SUBST takes for one and consume pairs with no lifted body (SPD-190).  Not in a case's pattern
            # (SPD-184), which no command reads: where the walk takes a pattern's `|` for a pipe (a form it cannot place;
            # case_in), it reads the words before it as a command, which this word would name
            self.words.append(PROCSUB_FILE)
        elif frame.procsub and self.stack[-1].form is None and not self.words:
            # ... but the case's own word, which the `in` or the `{` after it follows (SPD-185: `case <(true) { ... }`), is
            # held as a word so the walk sees it read
            self.words.append(PROCSUB_FILE)
        self.a.cwds = after
        if after != frame.saved and self.conditional:
            self.uncertain = True
        self.list_seen = directories.union_dirs(self.list_seen, after)
        if frame.kind != "sub":
            self.redirect_cwds = directories.union_dirs(frame.saved, after)
        if arithmetic is not None:
            self.read_arithmetic_command(frame, arithmetic)
        if self.stack and self.stack[-1].body == "compound":
            if self.stack[-1].kind == "cond":
                # `if [[ -n x ]] { list }`: an `else` or an `elif` may still follow the group, so the conditional ends where a
                # sublist body would, at the next terminator (probed: `if c { a } else { b }` and `if c { a }; echo`)
                self.stack[-1].body = "sublist"
            else:
                self.pop()  # the short loop whose body this `{ ... }` or `( ... )` was

    def open_process_substitution(self, frame):
        """A `<( list )` or `>( list )` just opened as `frame`: zsh parses its body when it runs it, as the command around
        it expands its words, not with the line, so an alias the line defined before it stands there as it does in a `$(
        )` body and in eval's words (SPD-287, probed in zsh 5.9 -f and -f -o nobareglobqual through
        tests/probes/shell_probe.py: after `alias gt="echo GT-RAN"`, `cat <(gt)` and `echo x > >(gt)` each printed GT-RAN,
        and `alias x=y; eval 'alias g2="echo G2-RAN"; cat <(g2)'` printed G2-RAN, parsed after eval's own alias ran).  The
        walk reads the body as a frame of the line's own words, so while the line's table holds any alias the frame is
        read a level into ShellAnalysis.alias_scope, as analyse.analyse_isolated reads a `$( )` body, with the table as
        it stands here (line_aliases.AliasView): zsh parses the body whole, so an alias it defines stands in none of its
        own words (`cat <(alias g3=...; g3)` found no command g3, on one line or two).  bash 3.2 with `shopt -s
        expand_aliases` reads the body a line at a time (`cat <(alias g4=...<newline>g4)` ran it), and the walk's tokens
        no longer tell a newline from a `;`, so the view is one of several lines (line_aliases.line_reading): a body's
        own alias is read for its later words on one line too, a reading of more than runs, never less; where they do,
        in text read a line at a time, each line of the body after the first is read with the aliases the lines before
        it left (new_body_line, SPD-323).  A line with no
        alias reads its process substitutions as it always did.  pop closes the scope with the frame.  The global aliases
        that stand there are expanded in the body's words as the scope opens (expand_globals, SPD-293).

        SPD-300: inside a function body the snapshot defines, read with an AliasView marked `early` (SPD-290: parsed
        before the snapshot's aliases), the body is still parsed when it runs, once the snapshot is sourced, so the
        snapshot's aliases stand in it (line_aliases.held_standing; probed in zsh 5.9 -f through
        tests/probes/shell_probe.py, a file of functions then `alias gp='echo GP-RAN'` sourced: a body's `cat <(gp)`,
        `echo x > >(gp)` and a loop's `cat <(gp)` ran GP-RAN, where the body's own `gp` and `cat <(sh -c gp)` found no
        command gp).  Where the line's table holds none the view above is not made, so the frame still puts the body's
        view back in force as a text parsed late (AliasView.parsed_late), opening no alias scope: the line's table,
        empty, stands there as it does.

        SPD-315: the body's command word reads exactly as a `$( )` body's does -- the alias where the text spells it
        unquoted, the command it names where quoted, both where both (SPD-308) -- by the line's own quotes
        (line_aliases.spellings), which spell every word of the body but the ones a global alias set there
        (expand_globals).  Where one did, the body is read as a text whose quotes the hook cannot see
        (ShellAnalysis.quoted_text None: every word both ways, more than zsh runs, never less) until it closes, when pop
        puts the text back; every other body is read by the line's quotes, so under the line's `alias git=hub`, `cat <(git
        push)` reads hub's push, as zsh runs it (probed in zsh 5.9 -f through tests/probes/shell_probe.py: under `alias
        ls='echo ALIASED'`, `cat <(ls -d /)` printed `ALIASED -d /`), where it had read git's push as well."""
        view = self.a.alias_view
        if self.a.aliases or self.a.alias_unknown:
            self.a.alias_view = line_aliases.AliasView(self.a, True)
            self.a.alias_scope += 1
            quoted = (self.a.quoted_text, self.a.quoted_sets) if self.expand_globals() else None
            if quoted is not None:
                self.a.quoted_text = self.a.quoted_sets = None
            frame.alias_view = (view, 1, quoted)
        elif view is not None and view.early:
            frame.alias_view = (view, 0, None)
            self.a.alias_view = view.parsed_late()

    def expand_globals(self, words=None):
        """The global aliases that stand in the `<( )` or `>( )` body just opened at self.at, expanded in its words
        (SPD-293) -- or in the tokens at the indices `words`, a line of text a shell reads a line at a time (SPD-291,
        expand_line_globals).  zsh parses the body when it runs it, so every unquoted word there that spells a global
        alias of the table as it stands here -- the line's own, and the snapshot's as the line left them -- is that
        alias's body, as in a `$( )` body (analyse.analyse_isolated) and eval's words (line_aliases.global_aliased), and
        the line's own text leaves them be (line_aliases.alias_words).  Probed in zsh 5.9 -f and -f -o nobareglobqual through
        tests/probes/shell_probe.py, each line run by eval as the Bash tool's shell runs it: after `alias -g Y="; echo
        PUSHED"`, `cat <(echo p2 Y)` printed p2 then PUSHED, and `echo x > >(cat; echo in-out Y2)` the same; with `alias -g
        X=snapshot` held, `alias -g X=line; cat <(echo p1 X)` printed `p1 line`; `'Q'`, `"Q"` and `\\Q` were left as
        written; a body holding another global alias expanded it, one holding `$( )` ran it, a case in the body read its
        words; `cat <(alias -g Z=own; echo p5 Z)` printed `p5 zz`, the body parsed whole; and `alias -g U=uu; f() { cat
        <(echo p8 U); }; alias -g U=later; f` printed `p8 later`.

        The walk has the body as tokens, which no longer show a word's quotes, so a word is taken for a name only where
        the text writes it unquoted (line_aliases.plain_words; for a function body line_functions reads, the text its
        definition was read from, SPD-311; any word where the walk has no text: more than zsh runs, never less).  Each
        such word's expansion is tokenized as analyse_command would (analyse.spliced_tokens) and set in its place among the walk's tokens, which
        the walk then reads as the body's own: its commands, what it prints, the directories it moves through.  A word of
        a `<( )` nested in this body is left to that one, which opens with the table as it stands there.  A word that may
        be a global alias the hook cannot resolve, and every plain word where the line defined one whose name it cannot
        read, is refused a member unread, as global_aliased refuses one.  The tokens as the line spelled them stay in
        `source`, and `origin` maps each token back to them (pristine), so a function body a call reads and a loop
        read_loop_again reads once more are read from the words the line wrote, with the table where they run.  Returns
        whether it set any alias's words in the body, whose quotes the line's text then does not show (SPD-315,
        open_process_substitution)."""
        a = self.a
        names = line_aliases.global_names(a)
        if not names:
            return False
        if self.plain is None and self.text is not None:
            self.plain = line_aliases.plain_words(self.text)
        unknown, budget, spliced = line_aliases.unknown_global(a, names), [line_aliases.GLOBAL_EXPANSIONS], []
        for j in _procsub_words(self.toks, self.at + 1) if words is None else words:
            word = self.toks[j]
            if self.plain is not None and word not in self.plain or all(c in syntax.SHELL_PUNCTUATION for c in word):
                continue
            if unknown:  # once for the body, as global_aliased records it once for its text
                unread.record_unread(a, "alias-word", unread.unread_shown(word))
                unknown = False
            text = line_aliases.global_word(word, names, a, budget)
            tokens = None if text is None else analyse.spliced_tokens(text, a, self.glued)
            if tokens is not None:
                spliced.append((j, tokens))
                if words is None and line_aliases.spelled_too(a):
                    # SPD-322: a bash, which has no global alias, runs the body's word as written, as global_aliased
                    # refuses it in a `$( )` body; a later line's word (`words`) is read as written by the reading that
                    # reads its text whole (analyse.walk_readings, held_text.text_lines)
                    unread.record_unread(a, "bash-alias", unread.unread_shown(word))
        if spliced and self.origin is None:
            if self.source is None:
                self.source = list(self.toks)
            self.origin = list(range(len(self.toks)))
        for j, tokens in reversed(spliced):  # from the last, so each index still names its word
            self.toks[j : j + 1] = tokens
            self.origin[j : j + 1] = [self.origin[j]] * len(tokens)
        return bool(spliced)

    def new_line(self, before):
        """A newline, syntax.LINE_END (SPD-291), just ended a list in text the shell reads a line at a time (`lines`):
        where it ends one of the text's lines -- no compound command open, no definition's header waiting for its body,
        no `&&`, `||` or `|` (`before`) carrying the command on to the next line -- the shell has run that line before it
        parses the next, so the next is read as text parsed as the text runs, a level into ShellAnalysis.alias_scope,
        with the aliases the lines before it left (line_aliases.AliasView) -- once the table holds one, and for every line
        after that.  Its global aliases are expanded in its words as it opens (expand_line_globals), the rest where a
        command word reads the view (analyse.dispatch_words, line_aliases.suffix_substitution).  Probed through
        tests/probes/shell_probe.py (2026-09-24), sh, zsh and dash fed each script by a pipe, after a line `alias
        ls="echo ALIASED"`: `{ ls -d /; }` and `ls -d /` after a here-document's body, a comment, blank lines or a case's
        `esac` printed `ALIASED -d /`; with the alias at the head of a `{ ... }`, an if's `then`, a for's `do` or a
        subshell, `ls -d /` on the compound's next line printed `/`, and so did `ls -d /` after `alias ... &&` or
        `alias ...; \\` ending the line before; `unalias ls; ls -d /` on the next line printed `ALIASED -d /`, the line
        parsed before its unalias ran; a function a later line defined ran the alias after it was unaliased; and zsh fed
        `alias -g GG=...` then `ls -d GG` expanded it, and `alias -s txt=...` then `a.txt x` ran it."""
        a = self.a
        ends = not (self.pending_body is not None or self.function_next or self.skip or before in _CARRIED)
        if ends and not self.stack and (self.line_scope or a.aliases or a.alias_unknown):
            if not self.line_scope:
                self.line_saved, self.line_scope = a.alias_view, 1
                a.alias_scope += 1
            a.alias_view = line_aliases.AliasView(a, False)
        elif ends and self.stack and self.stack[-1].kind == "sub" and not self.stack[-1].prints:
            self.new_body_line(self.stack[-1])
        # ... and in every line after that, a compound's too, but for one inside a `<( )` or `>( )` body (the "sub" frame
        # whose output goes to the file it stands for), whose words expand_globals read where it opened
        if self.line_scope and not any(frame.kind == "sub" and not frame.prints for frame in self.stack):
            self.expand_line_globals()

    def new_body_line(self, frame):
        """SPD-323: a newline just ended a line of the `<( )` or `>( )` body `frame` is, nothing opened inside it still open,
        in text the shell reads a line at a time.  bash, which parses such a body when it runs it, parses it a line at a
        time as it does eval's words, each line once the lines before it ran (held_text.parsed_lines has the probe: `cat
        <(alias ls=...<newline>unalias ls; ls -d /)` printed ALIASED), so the body's next line is read with the aliases
        the lines before it left (line_aliases.AliasView), a level into ShellAnalysis.alias_scope that the frame opened
        where it opened (open_process_substitution) or opens now, and pop closes.  Where the shell parses the body whole
        (zsh, ksh) the view it opened with stands for every line of it, and so it does in zsh's reading (`glued`) where
        the shell may read the body either way (syntax.LINES_BOTH: an sh that may be zsh), as analyse.walk_readings reads
        such a text."""
        a, opened = self.a, frame.alias_view is not None and frame.alias_view[1]
        if not (opened or a.aliases or a.alias_unknown):
            return
        how = held_text.parsed_lines(a, True)
        if not how or how == syntax.LINES_BOTH and self.glued:
            return
        if not opened:
            view, _, quoted = frame.alias_view or (a.alias_view, 0, None)
            frame.alias_view = (view, 1, quoted)
            a.alias_scope += 1
        a.alias_view = line_aliases.AliasView(a, False)

    def expand_line_globals(self):
        """The global aliases that stand in the next line of the text, expanded in its words as zsh parses it (SPD-291):
        from the newline just read to the next, with the view in force -- the one new_line set where the line before
        ended, or the one the line a compound command's lines belong to opened with, since zsh parses all of them first --
        and not in a `<( )` or `>( )` body's words, which expand_globals reads where the body opens.  Where it set any
        alias's words, the text's quotes no longer show how each word the walk reads was written, and every word is read
        both ways from here to the walk's end, which puts the text back (ShellAnalysis.quoted_text None, SPD-315): more
        than zsh runs, never less."""
        toks, own, opened, j = self.toks, [], [], self.at + 1
        while j < len(toks):
            t = toks[j]
            body = "<(" in opened or ">(" in opened  # a newline in such a body is the body's (new_line reads none there)
            if t is syntax.LINE_END and not body:
                break
            if t in ("(", "<(", ">("):
                opened.append(t)
            elif t == ")" and opened:
                opened.pop()
            elif not body:
                own.append(j)
            j += 1
        if own and self.expand_globals(own):
            if self.line_quoted is None:
                self.line_quoted = (self.a.quoted_text, self.a.quoted_sets)
            self.a.quoted_text = self.a.quoted_sets = None

    def pristine(self, i):
        """The index among the tokens as the line spelled them (`source`) of the walk's token i, which expand_globals may
        have moved; a token of an alias's expansion is its word's.  Past the last token, the end of the line."""
        if self.origin is None:
            return i
        return self.origin[i] if i < len(self.origin) else len(self.source)

    def read_arithmetic_command(self, frame, text):
        """What an arithmetic command `(( ... ))` assigns, recorded as the line's where the command stands (SPD-225):
        certain where it surely runs in the line's shell, doubted after `&&` or `||` and in a pipeline element (probed in
        zsh 5.9 -f, -f -o nobareglobqual and bash 3.2.57: `((X=5))` and `true && (( X = 13 ))` assigned X in all three,
        `(( X = 10 )) | cat` in none, `cat /dev/null | (( X = 9 ))` in zsh alone).  Its names stay on the frame, the
        closed compound until its terminator, so a `|` after it (finish) doubts them: the command ran in the pipe's
        process.  `text` is its words, the arithmetic between its outer parentheses marked as zsh.py marks it."""
        unsure = self.conditional or self.piped
        mark = len(self.a.assigned)
        self.a.unsure += unsure
        expansions.read_arithmetic(self.a, prepare.deglob(text))
        self.a.unsure -= unsure
        frame.assigns = tuple(self.a.assigned[mark:])

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
        self.note_loop_start(t)

    def note_loop_start(self, t):
        """A loop just opened at its reserved word `t`: where read_loop_again would read it from again (ShellFrame.again)."""
        self.stack[-1].again = (self.at, t, len(self.lifted) - len(self.inner), len(self.docs) - len(self.bodies),
                                _alias_state(self.a))

    def read_loop_again(self, frame):
        """A loop whose reading (`frame`, just closed) changed the line's alias table is read once more from its reserved
        word, with the table as that reading left it, and again while each reading changes it (SPD-294).  zsh parses
        eval's words, a `$( )` or backtick body, a `<( )` body and a glob qualifier's code each time the loop runs them,
        so an alias the body defines after them stands there in the next pass, while the walk read the body once, with
        the table as it stood when the loop opened (probed in zsh 5.9 -f and -f -o nobareglobqual through
        tests/probes/shell_probe.py: `for i in 1 2; do eval "q9 2>/dev/null || echo miss"; alias q9="echo Q9RAN"; done`
        printed miss, then Q9RAN; so did an eval text that defines its own alias after running it, a `$( )` body, a `<(
        )` body and a `repeat 2 { ... }` body, and a while's condition from its second pass on; bash 3.2 expands no
        alias in a script).  The loop's own words are parsed with it, and read the table as they always did (`eval 'for
        ...; do q2; alias q2=...; done'` printed miss twice).

        An `alias` the body spells defines the same body in every pass, so the second reading leaves the table the first
        did and the reading stops there; an alias a definition runs through (an eval of one whose body defines another)
        took one more pass per level (miss1 miss2 Q9RAN), which each further reading follows.  Past _ALIAS_PASSES the
        table is taken as one the hook cannot read (alias_unknown) and the body read once more with it, so each word an
        eval there runs is refused as unresolvable.  A for's, select's, repeat's or foreach's header runs once, before
        the body, so it is read with the table the loop opened with (`for i in $(q4); do alias q4=...` never ran q4), and
        a while's or an until's condition, run every pass, with the body's.  A loop that changes no alias is read once."""
        at, keyword, lifted, docs, opened = frame.again
        a, previous = self.a, opened
        left = _alias_state(a)
        if left == previous:
            return
        tokens = [keyword] + (self.source or self.toks)[self.pristine(at) + 1 :]
        piped_text, _frame_stdin, piped_fed, _frame_fed, pipe_feeds = frame.stdin
        passes = 0
        while left != previous:
            if passes == _ALIAS_PASSES:
                a.alias_unknown = True
            again = ShellWalk(a, self.lifted[lifted:], self.docs[docs:], self.expands[docs:], self.depth, self.glued,
                              piped_text, piped_fed, self.inputs, self.line, text=self.text)
            again.opened, again.once, again.pipe_feeds = frame.serial, True, pipe_feeds
            if keyword not in ("while", "until"):
                again.header_state = (opened, _alias_state(a))
                _set_aliases(a, opened)
            again.walk(tokens)
            if again.header_state is not None:  # a header that never ended: no body read, the table as it was
                _set_aliases(a, again.header_state[1])
            if passes == _ALIAS_PASSES:
                return
            previous, left, passes = left, _alias_state(a), passes + 1

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
        if t != "if":
            self.note_loop_start(t)

    def loop_header_word(self, t):
        """A `for` or `select` loop's variable holds whatever its list gives it, which a member writes: a doubt,
        so a word it fills where a command reads options or primaries is one the member controls -- unless every
        word of the list, as the shell expands it, starts with something other than `-` (`for d in /tmp/*/; do find "$d"
        ...`), which ShellAnalysis.dashless_loops records until the list says otherwise."""
        if "in" not in self.words:
            if syntax.IDENTIFIER_RE.match(t):
                self.a.doubt.add(t)
                self.a.line_assigned.update(self.a.reaching((t,)))  # assigned as a variable is (SPD-246)
            return
        at = self.words.index("in")
        names = [n for n in self.words[:at] if syntax.IDENTIFIER_RE.match(n)]
        # read one word at a time, as it arrives, so a long list stays linear: dashless at its `in`, and not from the first
        # word that may start with `-` on
        if len(self.words) == at + 1:
            self.a.dashless_loops.update(names)
            # the loop assigns its variables in the shell it runs in, a function body's global among them (SPD-246)
            self.a.line_assigned.update(self.a.reaching(names))
        elif _value_may_start_with_dash(t):
            self.a.dashless_loops.difference_update(names)
        # a list holding a positional parameter (`for a in "$@"`), or -- once shell/positional has set the call's words
        # where the body reads them -- one of the member's own words, fills the loop variable with what the member wrote,
        # so a finding on it is the member's own inside a function body (SPD-205, analyse_shell_text's prune).  Only the
        # word just read is tested, so a long list stays linear: each word reaches this once as `t`.
        self.fill_loop(names, [t], self.words[:-1])

    def fill_loop(self, names, listed, before):
        """A for, select or foreach list's words `listed`, after the header's words `before`, fill the loop's `names` with
        what the member supplies wherever a value would (expansions.fill_from, SPD-258): a positional, one of the call's
        own words, a substitution shell/positional set them in -- its body the next one lifted, which consume has not
        paired yet -- a variable they fill, and the line's own variables (SPD-253)."""
        k = sum(_lifted(w) for w in before)
        for w in listed:
            n = _lifted(w)
            bodies = tuple(self.inner[k : k + n])
            k += n
            if prepare.deglob(w) in self.a.shell_words:
                self.a.fill_members(names)
            expansions.fill_from(self.a, names, w, bodies + (None,) * (n - len(bodies)))

    def end_header(self):
        """The loop's header, or an if/while/until condition ending in `]]`, is complete.  Its body may follow with no `do`
        or `then`, so the next word decides the body's form."""
        self.finish()
        if self.stack and self.stack[-1].body in ("header", "cond"):
            self.stack[-1].body = "pending" if self.stack[-1].body == "header" else "cond-pending"
            self.expect_body = True
        if self.header_state is not None and len(self.stack) == 1:
            # read_loop_again's loop: its header ran with the table the loop opened with, and its body is read with the
            # one the reading before left (SPD-294)
            _set_aliases(self.a, self.header_state[1])
            self.header_state = None

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
    def consume(self, words, unsure=False):
        """Analyse the substitutions in these words (expanded before the command runs, each in its own process), and the
        text zsh's (e) flag evaluates in them with the variables the line holds here (shell/reevaluation), and take the
        here-document bodies their `<<` operators read; return the words without the operators and their delimiters.

        A body whose delimiter is unquoted is expanded here too, before the command reads it, wherever it is fed
        (SPD-192): its substitutions run in the command's directory with the values the line holds when the command runs,
        which its own prefix assignments do not reach (probed: `x=a; x=b cat <<EOF` and `cat <<EOF ...; x=b` wrote into
        a, `cat <<EOF ...; cd d` where the line stood before the cd; tests/test_hooks_input.py HereDocumentExpansionTest).
        What the command reads, and what the bodies returned hold, is then the text the expansion leaves, its escapes
        resolved (heredocs.received_body, SPD-206): `sh <<EOF` fed `\\$(git push)` runs the push.  Where the expansion ran a
        substitution that text holds its output, which the line does not spell, and the body returned is a
        heredocs.OutputBody (SPD-207): `sh <<EOF` fed `echo a $(cat x.sh)` runs x.sh's lines.  Each parameter's value
        stands in that text as well, where the line settles it, and a shell fed it parses the value again
        (reevaluation.body_values, SPD-208): `x='a; git push'; sh <<EOF` fed `echo $x` pushes; where the line does not
        settle a value the body is an OutputBody too."""
        reevaluation.read_eval_words(words, self.a, self.depth)
        # what the words' arithmetic assigns, where the shell expands them (SPD-225): `unsure`, the command may not run
        # there (after && or ||, a pipeline element, a background job), as finish reads it
        self.a.unsure += unsure
        expansions.read_word_arithmetic(words, self.a)
        self.a.unsure -= unsure
        self.substitutions = {}  # each word -> the bodies its substitutions lifted, which shell/loop_bindings reads (SPD-146)
        for w in words:
            lifted = []
            for _ in range(_lifted(w)):  # a `<( )`'s file name lifted no body
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
                    self.read_qualifier_code(code)
                    self.a.loop_depth -= 1
                    self.a.cwds = directories.union_dirs(before, self.a.cwds)
                    self.a.cd_uncertain = False
        cleaned, bodies, k, values = [], [], 0, None
        while k < len(words):
            if words[k] in ("<<", "<<-"):
                if self.bodies:
                    body = self.bodies.pop(0)
                    if self.expanded.pop(0):
                        expansions.read_word_arithmetic((), self.a, (body,))  # its arithmetic may assign (SPD-225)
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

    def read_qualifier_code(self, code):
        """An `e` or `+` glob qualifier's code (globbing.qualifier_code), read where the glob expands.  zsh parses it then,
        once for every file the glob matches, as eval parses its words, so an alias the line -- or an eval text before
        the glob -- defined stands there (SPD-292, probed in zsh 5.9 -f through tests/probes/shell_probe.py with two
        files: after `alias ls='echo ALIASED'`, `echo *(e:'ls -d /':)` printed `ALIASED -d /` twice and `echo *(+ls)`
        ran the alias; `eval 'alias l2="echo L2"; echo *(e:l2:)'` ran L2, which since SPD-286 no word of that eval text's
        own reads; under -o nobareglobqual each is `bad pattern`).  So while the line's table holds any alias, the code is
        read a level into ShellAnalysis.alias_scope with the table as it stands here (line_aliases.AliasView), as
        analyse.analyse_isolated reads a `$( )` body -- without analyse.isolated, since the code runs in the line's own
        shell.  zsh alone runs it, parsing it whole, so the view reads it as one line.  What the code defines stands for
        the next match, not its own (`echo *(e:'alias ls="echo INNER"; ls -d /':)` ran ALIASED, then INNER; an alias
        defined after a command ran that command for the second file), so where the reading changed the table the code
        is read once more with the table it left: the parse every later match makes.  A line with no alias reads the
        code as it always did.

        SPD-300: inside a function body the snapshot defines (an AliasView marked `early`, SPD-290) the code is parsed as
        the glob expands, once the snapshot is sourced, so the snapshot's aliases stand there (probed in zsh 5.9 -f
        through tests/probes/shell_probe.py, a file of functions then `alias gp='echo GP-RAN'` sourced: a body's `echo
        *(e:gp:)` and `echo *(+gp)` ran GP-RAN once per file, where the body's own `gp` found no command gp); where the
        line's table holds none, the code is read with the body's view as a text parsed late (AliasView.parsed_late),
        opening no alias scope."""
        view, read_with = self.a.alias_view, None
        late = None if view is None else view.parsed_late()
        for again in (False, True):
            parsed = 1 if self.a.aliases or self.a.alias_unknown else 0
            current = line_aliases.AliasView(self.a, False) if parsed else None
            if again and current == read_with:
                return
            self.a.alias_view = current if parsed else late
            self.a.alias_scope += parsed
            try:
                analyse.analyse_command(code, self.a, self.depth + 1)
            finally:
                self.a.alias_scope -= parsed
                self.a.alias_view = view
            read_with = current

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

    def finish(self, unsure=False, feeds_pipe=False):
        """Analyse the simple command read so far.  `unsure`: it runs in its own process (a pipeline element, a background
        job); `feeds_pipe`: a `|` or `|&` follows it, which zsh joins to a redirection of its standard output (SPD-214,
        stdin_text.redirected_text)."""
        words, self.words = self.words, []
        skip, self.skip = self.skip, False
        header, self.header = self.header, None
        redirect_cwds, self.redirect_cwds = self.redirect_cwds, syntax._CURRENT
        closed, self.closed = self.closed, None
        if unsure and closed is not None and closed.assigns:
            self.a.doubt.update(closed.assigns)  # `(( X = 5 )) | cat`: the arithmetic ran in the pipe's process (SPD-225)
            loop_bindings.forget(self.a, closed.assigns)
        if not words:
            self.ended(closed)
            return
        cleaned, bodies = self.consume(words, bool(unsure or self.conditional or self.piped))
        if closed is not None and stdin_text.input_fed(words, bodies, False):
            # a compound command's own input redirections, which zsh and bash perform before it runs, so the commands in it
            # read that input: kept for walk_line to walk the line again with it standing where the compound opens
            self.found[closed.serial] = (words, bodies)
        a = self.a
        if skip:
            if header == "for" and self.stack and self.stack[-1].kind == "loop":
                # its words, once per value in the body (SPD-146), read with the values the line settled before the header
                loop_bindings.bind_loop(cleaned, self.stack[-1], a)
            if header == "for":
                for w in cleaned:  # `for (( init; test; step ))`: arithmetic the loop runs, its names doubted with it (SPD-225)
                    if w.startswith("(" + _ARITH_OPEN):
                        for part in prepare.deglob(w)[2:-2].split(";"):
                            expansions.read_arithmetic(a, part)
            if header != "repeat":  # a for, select or foreach header assigns its names; a repeat count assigns nothing
                for w in _assigned_words(header, cleaned):
                    names = syntax._NAME_RE.findall(prepare.deglob(w))
                    a.doubt.update(names)
                    loop_bindings.forget(a, names)  # a loop body's basename of any of them no longer holds (SPD-221)
            self.ended(closed)
            return
        outer_substitutions, a.subst_words = a.subst_words, self.substitutions
        before = a.cwds
        # an assignment in a command that may not run (after && or ||) or runs in its own process may not hold after it
        unsure = unsure or self.conditional or self.piped
        a.unsure += unsure
        # the settled for loop whose body this command stands in directly, run in every pass (SPD-221), or None
        body_loop, a.body_loop = a.body_loop, None if unsure or self.function_next else loop_bindings.certain_loop(self.stack)
        # the text the command reads on standard input: the pipe's or the compound command's, and its own input
        # redirections in the order they stand in `words`, which still hold its here-document operators, as zsh and bash
        # each read them (stdin_text.command_input, SPD-209), with the values the line settled before it runs (SPD-148)
        stdin = stdin_text.command_input(words, bodies, self.piped_text, self.pipe_feeds, a)
        simple = None
        if self.function_next:  # zsh's `name () command`: a body that runs when called, perhaps more than once
            self.function_next = False
            simple, self.pending_body = self.pending_body, None
            # read in place, as every body is (settle_definitions); a call reads it from where it runs, on the input it
            # is given, which the command's own input redirections replace (probed: `z() cat < def.txt; z < call.txt`
            # printed def alone in zsh)
            a.loop_depth += 1
            a.func_depth += 1
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, stdin, self.piped_fed)
            a.func_depth -= 1
            a.loop_depth -= 1
            # a definition runs nothing where it stands (SPD-277); zsh's anonymous `() command` runs there, once or not
            a.cwds = before if simple is not None else directories.union_dirs(before, a.cwds)
        elif closed is not None and not directories.separate_redirects(cleaned)[0]:
            # a compound command's own redirections, after its closer (SPD-214): the text it printed stands, less what a
            # redirection of its standard output takes from the pipe -- in bash's reading, and in zsh's where no pipe
            # follows (probed: `{ echo '...'; } 2>/dev/null | sh` ran the text in zsh 5.9 and bash 3.2.57, `{ echo '...'; } >
            # /dev/null | sh` in zsh alone; tests/test_hooks_input.py CompoundOutputTest)
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, stdin, self.piped_fed)
            if closed.serial in self.found and closed.serial not in self.inputs:
                # its own input, which this walk read none of inside it: what it printed is walk_line's next walk's to read
                # (`{ cat; } <<'EOF' | sh`), and nothing here
                self.printed = ""
            else:
                if self.into and not self.stack and closed.kind != "sub":
                    # a call's reading of a compound body, whose own output redirections these are: where a pipe follows
                    # the call, zsh joins them to it (SPD-272, probed: `f() { echo '...'; } > /dev/null; f | sh` ran the
                    # text in zsh 5.9 and not in bash 3.2.57; `s() ( echo '...' ) > /dev/null; s | sh` in neither)
                    self.body_piped = stdin_text.joined(self.frame_printed,
                                                        stdin_text.redirected_text(self.printed, cleaned, True))
                self.printed = stdin_text.redirected_text(self.printed, cleaned, feeds_pipe)
        else:
            # what this command adds to the text its pipeline element prints, which a shell after a `|` runs, read with
            # the values the line settled before it runs (SPD-148) -- a call of a function the line defines prints what the
            # call's reading of its body printed, known once analyse_segment has read it (SPD-272, stdin_text.LineCall)
            printed = stdin_text.printed_text(cleaned, stdin, a, feeds_pipe)
            call = printed if isinstance(printed, stdin_text.LineCall) else None
            if call is not None:
                a.call_printed = None
            analyse.analyse_segment(cleaned, bodies, a, self.depth, redirect_cwds, stdin, self.piped_fed)
            if call is not None:
                printed = call.output(a.call_printed)
            self.printed = stdin_text.joined(self.printed, printed)
        a.unsure -= unsure
        a.body_loop = body_loop
        a.subst_words = outer_substitutions
        if a.cd_uncertain or (a.cwds != before and self.conditional):
            self.uncertain = True
        a.cd_uncertain = False
        self.list_seen = directories.union_dirs(self.list_seen, a.cwds)
        self.ended(closed, simple)
        while self.queued:
            self.read_queued(*self.queued.pop(0))

    # -- function definitions (SPD-277) -----------------------------------------------------
    def define(self, words, start):
        """A function definition's header was read, `words` its name words: bind its names to a shell function
        (ShellAnalysis.functions), and to a LineBody (function_bodies), whose tokens run from `start`, the header's end, to
        the end of the compound command the walk opens next (push) or of zsh's one simple command (finish), with the
        definition's own redirections after either.  The walk reads the body in place as it goes; begin_body, once the
        header's words are consumed, notes where that reading begins.  zsh's anonymous function, `() { ... }`, names
        nothing and runs where it stands, at once: it binds no body, and the walk reads it in place as it always has
        (None)."""
        names = _function_names(words)
        self.a.functions.update(names)
        if not names:
            self.pending_body = None
            return None
        if self.source is None:
            self.source = list(self.toks)  # close_brace may write a token over (a glued always block)
        body = self.bind(names)
        body.start, body.certain = start, self.certain_definition()
        self.pending_body = body
        return body

    def certain_definition(self):
        """Whether a definition read here surely runs before whatever follows it in the same shell (LineBody.certain,
        SPD-272): in the line's own walk, not the text of an eval, a body or a string it reads, which may not run; not after
        `&&` or `||`, nor in a pipeline element after a `|`; and inside no compound command but a group or a subshell that
        no such operator precedes, since a condition, a loop, a case or a function body may not run it.  A `|` or a `&`
        read after it takes it back (uncertain_element): that element ran in a process of its own."""
        outer_ok = all(frame.kind in ("group", "sub") and frame.defines is None and not frame.procsub
                       and not (frame.outer[4] or frame.outer[5]) for frame in self.stack)
        return outer_ok and len(self.a.walks) == 1 and not (self.conditional or self.piped)

    def bind(self, names):
        """A LineBody for these names, bound to them and to this walk's reading of the line, and holding the text this
        walk reads (LineBody.written, SPD-311): the one zsh parsed the definition in, which a call reads the body by
        wherever it runs -- after the eval whose text defined it, or inside another body's reading, whose walk reads by
        that body's own text in turn (line_functions.read_line_body) -- and the aliases that text was parsed with
        (LineBody.parsed, SPD-312): whether the line's stand in it (ShellAnalysis.alias_scope, one level or none, so a
        key holding it does not count levels) and the AliasView they stand with, as the walk reads its own command words
        here, which zsh expands in the body once, as it parses the definition."""
        body = line_functions.LineBody(names, self.line, self.a)
        body.written, body.parsed = self.text, (1 if self.a.alias_scope else 0, self.a.alias_view)
        for name in names:
            self.a.function_bodies.setdefault(name, set()).add(body)
        self.defined.append(body)
        return body

    def begin_body(self, body):
        """The walk begins reading `body` in place: note how much of the lifted text it has consumed, and -- unless the
        definition stands inside another's body, whose reading in place holds it -- the analysis's lists and caches, for
        end_body to take that reading out and put them back.  None, zsh's anonymous function, has no body to note."""
        if body is None:
            return
        body.consumed = (len(self.lifted) - len(self.inner), len(self.docs) - len(self.bodies))
        if not self.capturing:
            a = self.a
            body.marks = tuple(len(getattr(a, field)) for field in _RECORDS)
            body.caches = (set(a.isolated_done), {name: set(reads) for name, reads in a.bodies_read.items()},
                           dict(a.body_dirs), set(a.moving_traps), a.dir_moves, a.body_walks)
        self.capturing.append(body)

    def close_definition(self):
        """The compound command whose closer the walk read last is over: where it is a definition's body with no
        redirections of its own after it, so is the definition."""
        closed, self.closed = self.closed, None
        self.ended(closed)

    def ended(self, closed, simple=None):
        """A definition whose body finish has just read to its end -- zsh's `name () command` (`simple`), or the compound
        command `closed`, with its own redirections -- is over."""
        if simple is not None:
            self.end_body(simple, False)
        elif closed is not None and closed.defines is not None:
            self.end_body(closed.defines, True)

    def end_body(self, body, into):
        """`body`'s definition is over: take its tokens and the text they lift down, and settle its reading in place."""
        if body not in self.capturing:
            return
        self.capturing.remove(body)
        body.tokens = tuple((self.source or self.toks)[self.pristine(body.start) : self.pristine(self.at)])
        (lifted, docs), (lifted_now, docs_now) = body.consumed, (len(self.lifted) - len(self.inner), len(self.docs) - len(self.bodies))
        body.inner, body.docs, body.expanded = self.lifted[lifted:lifted_now], self.docs[docs:docs_now], self.expands[docs:docs_now]
        body.glued, body.into = self.glued, into
        self.settle_body(body)

    def settle_body(self, body):
        """A definition's reading in place is over.  What it added to the analysis's lists is taken out, to go back where
        it stood when the walk ends unless a call reads the body (settle_definitions), and its caches are put back as the
        definition found them, so a call reads again what the reading in place read: a body runs nothing where it is
        defined.  A body zsh runs by itself -- a TRAPxxx, zshexit or chpwd function, one a hook array names (SPD-276) -- is
        read now as a trap's action is, and its reading in place counts for nothing."""
        a = self.a
        if body.marks is not None:
            body.pending = []
            for field, mark in zip(_RECORDS, body.marks):
                entries = getattr(a, field)
                body.pending.append(entries[mark:])
                del entries[mark:]
            a.isolated_done, a.bodies_read, a.body_dirs, a.moving_traps, a.dir_moves, a.body_walks = body.caches
            body.caches = None
        in_line = expansions.runner(body.names, a)
        if in_line is not None:
            body.called = True
            expansions.read_deferred_body(a, body, self.depth, in_line)

    def queue(self, body, in_line=None):
        """A body an assignment in the command being read defines (zsh's `functions`, SPD-278; in_line None) or names in a
        hook array (SPD-276; in_line, whether zsh may run it inside the line), for finish to read once the command is."""
        self.queued.append((body, in_line))

    def read_queued(self, body, in_line):
        """finish's reading of a queued body: a body zsh runs by itself as a trap's action, and a body `functions` was
        handed in place, as a definition's is, where it is spelled."""
        a = self.a
        if in_line is not None:
            body.called = True
            expansions.read_deferred_body(a, body, self.depth, in_line)
            return
        self.begin_body(body)
        a.loop_depth += 1
        a.func_depth += 1
        # the body itself, so its text is read with the aliases it was parsed with (LineBody.parsed, SPD-312)
        analyse.isolated(a, lambda: analyse.analyse_command(body, a, self.depth + 1))
        a.func_depth -= 1
        a.loop_depth -= 1
        self.capturing.remove(body)
        self.settle_body(body)

    def settle_definitions(self):
        """The walk whose reading of the line stands is over: each definition no call read goes back to its reading in
        place, its entries where they stood among the analysis's lists -- a function defined and never called reads as
        it always has, its Law 7 verbs found and its findings in their order on the line.  The latest first, so each goes
        back among the lists as they were when it was taken out.  An index of the outermost reading of the shell's own
        text (ShellAnalysis.shell_kept) past one moves with it."""
        a = self.a
        for body in reversed(self.defined):
            if body.pending is None or body.called:
                continue
            for field, mark, entries in zip(_RECORDS, body.marks, body.pending):
                getattr(a, field)[mark:mark] = entries
                if field == "findings" and entries and a.shell_kept:
                    a.shell_kept = {k + len(entries) if k >= mark else k for k in a.shell_kept}
            body.pending = None

    # -- braces --------------------------------------------------------------------------
    def in_pattern(self):
        """A case command is reading its subject, `in` or a pattern: no command position, so no word there opens a group."""
        return bool(self.stack) and self.stack[-1].kind == "case" and self.stack[-1].pattern

    def case_in(self, case):
        """Whether this case places its patterns, where a top-level `|` in one is an alternative, no pipe (SPD-187): the
        word after its subject is `in`, or it is zsh's brace form (SPD-185, open_brace_case).  Settled once the walk holds
        both words, and at its first pattern's `)`; until then, and for any other second word, False, so the walk reads a
        `|` there as it always has, a pipe."""
        if case.form is None and len(self.words) >= 2:
            case.form = "in" if self.words[1] == "in" and self.words[0] != "{" else False
        return bool(case.form)

    def open_brace_case(self, t):
        """Whether `t`, read where a case's `in` would stand after its word, is zsh's brace form's `{` (SPD-185): the case
        then reads its patterns as the `in` form does, and closes at its `}` (read_word, brace_closes).  A pattern glued to
        the brace (`{x)`, which mark_zsh_patterns writes apart unless the line is one it passes whole) is add_word's to
        read on.  Probed in zsh 5.9 -f and -f -o nobareglobqual: `case x { x) ( echo B1 );; }`, `case x {x) echo A11;; }`
        and `case x<newline>{ x) echo A5;; }` ran their bodies, bash 3.2.57 rejected them (tests/test_hooks_groups.py
        CaseBraceFormTest)."""
        case = self.stack[-1] if self.stack and self.stack[-1].kind == "case" else None
        if case is None or not case.pattern or case.form is not None or len(self.words) != 1 or t[:1] != "{":
            return False
        case.form, case.closer = "brace", "}"
        self.discard()  # the case's word
        return True

    def case_header_break(self, case, i):
        """The `;` at toks[i], which mark_zsh_patterns writes for a newline, stands in a case's header: after `case word
        in`, or between the word and an `in` or a `{` that follows it (probed: zsh 5.9 and bash 3.2 read the patterns after
        either, SPD-187, and zsh `case x<newline>{ x) echo A5;; }`, SPD-185), so the words before it are no command."""
        if len(self.words) == 2:
            return self.case_in(case)
        j = i + 1
        while j < len(self.toks) and self.toks[j] == ";":
            j += 1
        return len(self.words) == 1 and self.words[0] != "{" and j < len(self.toks) and (self.toks[j] == "in" or self.toks[j][:1] == "{")

    def open_brace(self, lone=True):
        """Open the `{ list }` a `{` read now begins, and say whether it did: in command position a group, or a function's
        body after `name ()` and zsh's anonymous `()`; after `function name` a function's body; after only `coproc`,
        `time` and `!` a prefixed group; after those and a literal name, bash's named coproc, when the `{` is a word of
        its own (`lone`; bash reads `{git` as a word, and zsh has no named form); after a lone `always` a group.  Anywhere
        else the `{` is a word."""
        if not self.words and not self.skip:
            self.push("func" if self.function_next else "group", "}")
            self.function_next = False
            return True
        if self.skip and self.function_next:  # function name {
            body = self.define(self.words, self.at)  # a function defined here shadows a later call's name
            self.discard()
            self.begin_body(body)
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
        if (lone and len(self.words) > 1 and "coproc" in self.words[:-1] and syntax.IDENTIFIER_RE.match(self.words[-1])
                and hookio.SUBST not in self.words[-1] and all(w in syntax.LOOP_PREFIX_WORDS for w in self.words[:-1])):
            # bash 4+'s named form, `coproc NAME { list }` (SPD-060), runs the group in the same forked shell, so it opens
            # the same frame and its cd moves the group's later commands and redirections, never the line (SPD-125).  A
            # name spelled by an expansion (`$N`, a lifted `$( )`) keeps SPD-060's flattened reading, which resolves or
            # refuses it.
            self.discard()
            self.push("sub", "}")
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
        if self.open_brace_case(t):  # zsh's `case word { ... }` (SPD-185)
            if t == "{":
                return
            t = t[1:]  # its first pattern, glued to the brace
        while t[:1] == "{" and (t == "{" or (self.glued and not self.in_pattern())) and self.open_brace(t == "{"):
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
        if t in ("esac", "}") and len(self.words) == 2 and self.in_pattern() and self.case_in(self.stack[-1]):
            # `case word in esac`, a case with no pattern, which closes there (probed: `case q in esac | echo e1` and `case
            # q in esac; echo e2` printed theirs in zsh 5.9 and bash 3.2): with a pattern's `|` an alternative (SPD-187),
            # a walk still in its pattern would read no pipe after it.  zsh's `}` closes it there too (SPD-185)
            self.discard()
        if not self.words and not self.skip:
            if t in ("}", "fi", "done", "esac") or t == "end" and self.ends_foreach():
                self.close_sublists()
                top = self.stack[-1] if self.stack else None
                if top is not None and top.closer == t:
                    if t == "}":
                        self.close_brace()
                    else:
                        self.pop()
                elif top is not None and top.kind == "case" and top.pattern and t in ("}", "esac"):
                    # SPD-185: where a pattern would stand either closer ends either form (probed in zsh 5.9 -f and -f -o
                    # nobareglobqual: `case x { x) echo A13 ;; esac` and `case x in x) echo A14;; }` ran; in a body only
                    # the form's own did, `case x { x) echo B70; esac` failing near its esac)
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
        self.a.walks.append(self)  # the walk an assignment to zsh's `functions` or to a hook array hands its body (queue)
        try:
            self.walk_tokens(tokens)
        finally:
            self.a.walks.pop()
            if self.line_scope:  # the scope new_line opened for the text's later lines closes with it (SPD-291)
                self.a.alias_scope -= self.line_scope
                self.a.alias_view, self.line_scope = self.line_saved, 0
            if self.line_quoted is not None:
                (self.a.quoted_text, self.a.quoted_sets), self.line_quoted = self.line_quoted, None

    def walk_tokens(self, tokens):
        toks = self.toks = [p for t in tokens for p in syntax.operator_parts(t)]
        i = 0
        while i < len(toks):
            self.at = i  # where a definition's body ends, when finish reads its end at this token (end_body)
            t = toks[i]
            if self.expect_body and t not in syntax.BODY_DEFERRING:  # a terminator or a list operator may stand between them
                self.resolve_body(t)
            case = self.stack[-1] if self.stack and self.stack[-1].kind == "case" else None
            if t == ")" and case:
                if case.form is None:
                    case.form = self.case_in(case) and case.form  # settled here at the latest: later patterns hold no subject
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
                body = self.define(self.words, i + 2)  # name (), name() and zsh's `a b () ...`
                self.discard()  # name (): a function definition's header
                self.begin_body(body)
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
                if self.header in _NAMED_LOOPS and self.words and self.loop_names(self.words):
                    # zsh's `for f ( word ... )`: its words fill the names as an `in` list's do (SPD-258)
                    self.fill_loop([n for n in self.words if syntax.IDENTIFIER_RE.match(n)], toks[i + 1 : j], self.words)
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
                if t != "(":
                    self.open_process_substitution(self.stack[-1])
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
                self.finish(unsure=True, feeds_pipe=True)
                self.a.cwds = self.pipeline_start  # that element ran in its own process
                self.piped = True
                self.uncertain_element()
                self.end_element(into_pipe=True)  # the next element reads what this one printed
            elif t == "&":
                self.reopen_condition()
                self.finish(unsure=True)
                self.uncertain_element(background=True)
                self.close_sublists()  # `repeat 2 git push &`: the `&` ends the body's sublist, and the loop with it
                self.a.doubt.update(self.a.assigned[self.list_mark :])  # a background list assigns in its own process
                loop_bindings.forget(self.a, self.a.assigned[self.list_mark :])
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
                if t is syntax.LINE_END and self.lines:
                    self.new_line(toks[i - 1] if i else None)
            else:
                self.add_word(t)  # close_brace reads the tokens after a `}` and may take them, moving self.at
                i = self.at
            i += 1
        self.at = len(toks)
        self.finish()
        while self.stack:
            self.pop()
        self.close_definition()
        while self.capturing:  # a definition the line leaves open (`f() {` with no `}`): its body is what the line holds
            self.end_body(self.capturing[-1], True)
        self.end_list()
        while self.inner and not self.once:  # a substitution no word held (a malformed line): still analysed
            analyse.analyse_isolated(self.a, self.inner.pop(0), self.depth + 1, *self.substitution_input())


def walk_line(a, tokens, inner, bodies, expanded, depth, start, glued=True, stdin=None, fed=False, into=False, text=None,
              lines=False):
    """Walk a line's tokens (ShellWalk), and walk them again where a compound command on it has input redirections of its
    own (SPD-210): the shells perform those before the compound runs, so every command in it reads that input, but the
    walk reads them after its closer, when those commands are read already.  The second walk hands each such compound its
    input where it opens (ShellWalk.push), from the words the first found after its closer.  `start` is reading_start's
    state from before the first walk, put back so the second reads the line from where the first did; whatever else the
    first learnt stays -- doubt, a function's or a hashed name, an alias the hook cannot read, each of which only makes
    the reading stricter -- and a body read in its own process is read once per starting state (analyse_isolated), so
    the first walk's findings stand beside the second's, which adds the ones the input earns.  Returns the walk whose
    reading stands, for analyse_command to ask whether zsh split a brace off a word, once its definitions no call read
    are read in place (ShellWalk.settle_definitions).  `into`: the tokens are a compound function body a call reads, on
    whose input it stands as a pipe into it (line_functions.read_line_body).  `lines`: the shell reads the text a line
    at a time (ShellWalk.new_line, SPD-291)."""
    line = object()  # this reading of the line, which its definitions are bound to (ShellWalk.define)
    a.walking.add(line)
    try:
        shell_walk = ShellWalk(a, inner, bodies, expanded, depth, glued, stdin, fed, line=line, into=into, text=text,
                               lines=lines)
        shell_walk.walk(tokens)
        if shell_walk.found:
            restore_reading(a, start)
            shell_walk = ShellWalk(a, inner, bodies, expanded, depth, glued, stdin, fed, shell_walk.found, line, into, text,
                                   lines)
            shell_walk.walk(tokens)
        shell_walk.settle_definitions()
        return shell_walk
    finally:
        a.walking.discard(line)


def reading_start(a):
    """The state a walk reads a line's commands with, and changes as it goes, that restore_reading puts back: the
    directories, the variables, the loop depth, the aliases, the loop names read as dashless, and the function bodies
    bound to their names (SPD-212: each walk of the line binds its own definitions again, and zsh's reading's do not
    stand for bash's reading's while it walks), and shell/loop_bindings' loop and substitution values (SPD-146), and the
    locals the innermost function body the shell holds has declared (SPD-246)."""
    return (a.cwds, dict(a.vars), a.loop_depth, dict(a.aliases), set(a.dashless_loops), bodies_copy(a.function_bodies),
            dict(a.loop_words), dict(a.derived), a.func_depth, dict(a.loop_derived),
            None if a.body_locals is None else dict(a.body_locals[-1]))


def restore_reading(a, start):
    """Put back reading_start's state, copied, so one start serves every walk of the line."""
    cwds, variables, loop_depth, aliases, dashless, function_bodies, loop_words, derived, func_depth, loop_derived, local = start
    a.cwds, a.vars, a.loop_depth, a.cd_uncertain = cwds, dict(variables), loop_depth, False
    a.aliases, a.dashless_loops, a.function_bodies = dict(aliases), set(dashless), bodies_copy(function_bodies)
    a.loop_words, a.derived, a.func_depth, a.loop_derived = dict(loop_words), dict(derived), func_depth, dict(loop_derived)
    if local is not None:
        a.body_locals[-1] = dict(local)


def bodies_copy(function_bodies):
    """A copy of ShellAnalysis.function_bodies that a definition added to it does not reach."""
    return {name: set(found) for name, found in function_bodies.items()}
