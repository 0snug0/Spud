"""shell/zsh: zsh's own glob operators, and the arithmetic both shells read as arithmetic."""

import bisect
import re

from . import assignment_words, syntax


# Reserved words after which zsh is still in command position, so `(` opens a subshell (zsh's lexer: a word turns command
# position off, these and an assignment keep it; probed with `time (cd x)`, `! (cd x)`, `if (cd x)`, `{ (cd x) }`).
ZSH_COMMAND_POSITION_WORDS = {"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "{", "}", "!", "time", "coproc", "nocorrect"}
_PLAIN_RUN_RE = re.compile(r"[^\s;&|<>()'\"\\$]+")  # characters a word copies as they are
_BRACE_RUN_RE = re.compile(r"\{+")  # the braces a word opens with, each a group of its own in command position
# How the text of an arithmetic command `(( ... ))` and of an arithmetic expansion `$(( ... ))` is marked.  Both
# shells evaluate it as arithmetic and run no command in it, so every character shlex, separate_redirects or ShellWalk would
# otherwise read as an operator is replaced with an arithmetic sentinel (syntax._ARITH_SENTINELS), every glob metacharacter
# with the quoted-glob sentinel that already means "not expanded here", and every `$` between the parentheses with the mark
# that says it expands to nothing the hook reads.  deglob restores all of them, so a reason still names what the line
# spells.  Before this, `(( n > 2 ))` reached separate_redirects as `n`, `>`, `2` and was refused as a write to a file
# named 2.
#
# _ARITH_WORD marks the blanks as well, so the whole of an expansion reaches shlex inside the word it is part of
# (`x=$(( 1 > 2 ))` is one assignment word, where it used to be `x=$`, `((`, `1`, `>`, `2`, `))`).  _ARITH_COMMAND leaves
# the blanks alone: an arithmetic command stands where a command stands, so its blanks separate the same words they always
# separated -- each of them inert now -- and a `$( ... )` inside it stays out of the command word, where a substitution is
# a refusal.  The command's outer parenthesis and its match are left as they are, for ShellWalk to open and close the frame
# it already opened and closed and for its `for (( ... ))` header scan to find; every character between them is marked, the
# inner `(` of `((` included, so the segment's first word always begins with a sentinel and can never be read as a command
# name, a variable, an assignment or a glob.  `(( $n > 2 ))` is silent for that reason and not only for the marked `>`:
# there is no command word in an arithmetic command, so the hook reads none.
#
# Two edges of that, both deliberate.  An expansion's own `$`, the one in front of the parentheses, is left unmarked: it
# still says the word's value comes from an expansion, and `$((1)) push` names a command the hook cannot read -- arithmetic
# yields a number, and a number names an executable on a PATH of the line's own choosing -- so the expansion check still
# refuses it.  And `(( x=1 ))` no longer records x, its word now standing behind the sentinel: that is the half that
# fails closed, a later `$x` refusing a member where it used to resolve to 1, and an arithmetic value is a number, never
# a path.
_ARITH_MARKS = dict({c: syntax._ARITH_SENTINELS[c] for c in "()<>|&;\n"},
                    **{c: syntax._GLOB_SENTINELS[c] for c in syntax._GLOB_META},
                    **{"$": "$" + syntax._LITERAL_DOLLAR})
_ARITH_COMMAND = str.maketrans(_ARITH_MARKS)
_ARITH_WORD = str.maketrans(dict(_ARITH_MARKS, **{c: syntax._ARITH_SENTINELS[c] for c in " \t"}))


def _scan_pairs(text):
    """One pass over a line's masked outer text, quotes and escapes skipped: the index of the `)` matching each
    unquoted `(` and of the `}` matching each unquoted `{`, and the sorted indexes of the unquoted characters a zsh glob group
    cannot hold (`;` `&` `>`, a newline, a `<` that opens no range).  Marking a line reads groups through it, in time linear in
    the line's length: scanning from every `(` of a line of unbalanced ones was quadratic."""
    parens, braces, bad, opened, braced = {}, {}, [], [], []
    state, i, n = None, 0, len(text)
    while i < n:
        c = text[i]
        if state == "'":
            state = None if c == "'" else state
        elif c == "\\":
            i += 2
            continue
        elif state == '"':
            state = None if c == '"' else state
        elif c in "'\"":
            state = c
        elif c == "(":
            opened.append(i)
        elif c == ")":
            if opened:
                parens[opened.pop()] = i
        elif c == "{":
            braced.append(i)
        elif c == "}":
            if braced:
                braces[braced.pop()] = i
        elif c == "<":
            m = syntax.ZSH_RANGE_RE.match(text, i)
            if m:
                i = m.end()
                continue
            bad.append(i)
        elif c in ";&>\n":
            bad.append(i)
        i += 1
    return parens, braces, bad


def _zsh_group(text, i, scan):
    """(the marked text, the index after it) of the zsh glob group opening at text[i], or None where zsh reads none: unbalanced,
    or holding an unquoted `;` `&` `>` or a `<` that opens no range, which zsh rejects as a parse error (probed).  Blanks, bars,
    nested groups and ranges are part of the pattern; quoted and escaped characters stay as they are.  `scan` is the line's
    _scan_pairs."""
    parens, _braces, bad = scan
    end = parens.get(i)
    if end is None:
        return None
    k = bisect.bisect_right(bad, i)
    if k < len(bad) and bad[k] < end:
        return None
    out, depth, n, state, j = [], 0, end + 1, None, i
    while j < n:
        c = text[j]
        if state == "'":
            out.append(c)
            state = None if c == "'" else state
        elif c == "\\" and j + 1 < n:
            out.append(text[j : j + 2])
            j += 2
            continue
        elif state == '"':
            out.append(c)
            state = None if c == '"' else state
        elif c in "'\"":
            state = c
            out.append(c)
        elif c == "(":
            depth += 1
            out.append(syntax.ZSH_OPEN)
        elif c == ")":
            depth -= 1
            out.append(syntax.ZSH_CLOSE)
            if depth == 0:
                return "".join(out), j + 1
        elif c == "|":
            out.append(syntax.ZSH_BAR)
        elif c in " \t":
            out.append(syntax._ZSH_SENTINELS[c])
        elif c == "<":
            m = syntax.ZSH_RANGE_RE.match(text, j)
            if not m:
                return None
            out.append(syntax.ZSH_RANGE_OPEN + text[j + 1 : m.end() - 1] + syntax.ZSH_RANGE_CLOSE)
            j = m.end()
            continue
        elif c in ";&>\n":
            return None
        else:
            out.append(c)
        j += 1
    return None


def _before_close(word, command):
    """What zsh reads of `word` before the `}` that closes a `{ list }`, where the word ends in one, else None; `command`:
    the word stands in command position.  A sole `}` is significant wherever it stands and is its own answer.  A `}` glued
    to the end of a word is split off it (SPD-132) unless it closes a `{` or `${` opened earlier in the word; in command
    position each leading `{` is split off first, as the group it opens, and an assignment keeps its `}`.  Probed in zsh 5.9
    -f and -f -o nobareglobqual (SPD-142, tests/probes/shell_probe.py): `{ echo a}`, `{true}`, `{ echo x > g}` (which made
    g, not `g}`), `{ echo try}}` (try}), `{ echo x{a,b}}` (xa xb), `{ echo ${x-q}}` (q), `{ [[ -n x ]]}` and `{ case x in
    x) echo a;; esac}` each closed their group, `{ echo a}b }` printed a}b and `{ x=1}` never closed, and `echo a}` and
    `for f in a} b` -- a brace that closes no group -- are parse errors.  A quoted or escaped `}` is a sentinel by now, so it
    never ends the word here."""
    if word[-1:] != "}":
        return None
    if word == "}":
        return word
    body = word.lstrip("{") if command else word
    if command and assignment_words.assignment_word(body):
        return None
    depth = 0
    for c in body[:-1]:
        if c == "{":
            depth += 1
        elif c == "}" and depth:
            depth -= 1
    return None if depth else word[:-1]


def mark_zsh_patterns(text):
    """zsh's reading of its own glob operators, for the masked outer text of a line: a group `(a|b)` and a numeric range
    `<n-m>` that zsh reads as part of a word are kept in that word with sentinels, where shlex would read a subshell and an
    input redirection.  Returns (zsh's text, the other reading's text).  Probed in zsh 5.9 with its default options and with
    nobareglobqual, and in bash 3.2:

    - a range (`<->`, `<n->`, `<-m>`, `<n-m>`) is a pattern wherever it stands unquoted, in command position too; bash reads
      its `<` and `>` as redirections, so the other reading restores it;
    - `(` opening a word is a pattern outside command position (after a command word, a redirection operator, `for x in`),
      a subshell in it (at the start of a command, after an assignment, `()`, a reserved word such as `time`, `!` or `{`, and
      after a redirection's target at the start of a command).  bash rejects the pattern line, but the shells place command
      position differently in places (`time -p (` and `coproc NAME (` are bash subshells, `repeat 1 (` a zsh one), so the
      other reading restores the parenthesis for shlex to read as before;
    - the `}` that closes a `{ list }` -- a sole one, or one zsh splits off the end of a word (_before_close) -- gives command
      position back (SPD-142), and the try-always form's `always` right after it keeps it: zsh reads `else`, `elif`, `fi`,
      `then`, `do`, `done` and `esac` after such a brace as the reserved words they are, so `if [[ c ]] { a } elif [[ d ]]
      ( list )`, `if [[ c ]] { a } else ( list ); fi`, `{ a } always {( list )}` and `if { c } then ( list ) fi` run a
      subshell.  A `(` right after the brace is a parse error.  After a short loop's `{ }` body zsh leaves command position
      for good, takes that `(` for a pattern word and rejects the line, as it rejects a `fi` or an `else` there (`if true;
      then repeat 1 { echo r } fi`), so the subshell read in its place reads a line that runs nothing;
    - an arithmetic command's `))` leaves command position as its `((` found it (SPD-173): zsh reads `((` as arithmetic
      only in command position and in a for loop's header, and after the `))` it reads `then`, `do`, `{`, a second `((`
      and a `(` as it does at the start of a command, so the body of a short if, elif, while or until whose condition ends
      in one -- `if (( c )) ( list )`, `while (( c )) ( list )`, `if [[ c ]] { a } elif (( d )) ( list )`, `if (( c ))
      then ( list ) fi`, `if (( c )) {( list )}; b` -- is a subshell.  With no compound command around it `(( c )) (
      list )` is a parse error, so the subshell read in its place reads a line that runs nothing;
    - glued inside a word, `(` is a pattern in zsh and a syntax error in bash, whole in both readings, except `()` (a function's
      header), `$((`, `name=(` and a reserved word in command position.  There zsh splits every brace off a run of them
      opening the word, so `{(` and `{{(` run a subshell in their groups, read so in both readings; every other reserved
      word glued to `(` -- `else(`, `then(`, `do(`, `time(`, `!(`, `}(` and the rest -- is one glob word to zsh wherever it
      stands, its group a pattern or, with bareglobqual, glob qualifiers whose code runs for each file the word matches
      (SPD-174, tests/probes/shell_probe.py: with a file named else present, zsh -f printed QRAN-else for `if true; then :;
      else(e:'echo QRAN-$REPLY':); fi` and zsh -f -o nobareglobqual found no match; GluedReservedWordTest has the rest),
      while bash runs `!(`, `if(`, `then(`, `else(`, `elif(`, `while(`, `until(`, `do(` and `time(` as the reserved word
      and a subshell where that word belongs.  zsh's reading keeps such a group in its word, and the other reading
      restores the parenthesis;
    - `>(` and `2>(` stay a process substitution; `&>(` and `>|(` open a pattern target;
    - case patterns, `[[ ... ]]`, `${...}` and here-document delimiters are left as they are.

    An arithmetic command `(( ... ))` and an arithmetic expansion `$(( ... ))` are marked too, with the arithmetic
    sentinels rather than the pattern ones: both shells evaluate what stands between the parentheses, so an operator there is
    an operator of the arithmetic and never of the shell, and both readings get the same marking.

    Both texts are the input when it holds none of these."""
    if "(" not in text and "<" not in text:
        return text, text
    scan = _scan_pairs(text)
    parens, braces = scan[0], scan[1]
    out, other, i, n = [], [], 0, len(text)
    command = True  # zsh's command position
    target = None  # after a redirection operator: the command position to restore after its target
    heredoc = cond = arith_next = punctuation_next = False
    # zsh's `for name ( word ... )` word list is not a glob (`for f (a|b)` is a parse error), and a command follows
    # it and a `repeat` count, so `for f (a b) (git push)` and `repeat 1 (git push)` open subshells, not patterns.
    for_list = 0  # 1: the loop's name is next; 2: a `(` here opens its word list
    repeat_count = False  # the next word is a `repeat` count; the body, in command position, follows it
    for_close = -1  # where a `for name (` list closes
    closed = False  # the word just read ended in the `}` that closes a group (SPD-142: an `always` after it keeps command position)
    cases = []  # per open case command: "subject", "in", "pattern" or "body"
    while i < n:
        c = text[i]
        if c in " \t":
            out.append(c)
            other.append(c)
            i += 1
            continue
        in_pattern = bool(cases) and cases[-1] == "pattern"
        if punctuation_next:  # a word stopped here without reading a pattern: this is shell punctuation, the plain reading
            word_start = False
        elif c == "(":
            if text.startswith("((", i) and (command or arith_next) and i in parens:  # (( arithmetic )), or a for loop's header
                end = parens[i] + 1
                # The parenthesis and its match stay; everything between them is arithmetic in both shells, so no
                # operator in it survives into either reading
                arith = text[i] + text[i + 1 : end - 1].translate(_ARITH_COMMAND) + text[end - 1]
                out.append(arith)
                other.append(arith)
                # zsh is in command position after the `))`, a `for (( ... ))` header's and an arithmetic command's alike
                # (SPD-173): probed in zsh 5.9 -f and -f -o nobareglobqual, `if (( 1 )) ( echo a )`, `while (( n++ < 1 ))
                # ( echo a )` and `if (( 1 )) then ( echo a ) fi` ran the subshell, `if (( 1 )) (( 3 > 2 )) && echo a`
                # made no file 2, and `(( 1 )) (( 1 ))` failed near the second ` 1 `, its `((` read as arithmetic
                i, command, arith_next = end, True, False
                continue
            if for_list == 2 and i in parens:  # `for f ( a b )`: the loop's word list
                for_close = parens[i]
                word_start = False
            else:
                word_start = not (command or cond or heredoc or in_pattern or text.startswith("()", i)) and _zsh_group(text, i, scan) is not None
        else:
            word_start = c == "<" and not (cond or heredoc) and syntax.ZSH_RANGE_RE.match(text, i) is not None
        punctuation_next = False
        if c in ";&|<>()" and not word_start:
            op = "()" if text.startswith("()", i) else next((o for o in syntax.SHELL_OPERATORS if text.startswith(o, i)), c)
            out.append(op)
            other.append(op)
            pos, i = i, i + len(op)
            arith_next = False
            for_list, repeat_count, closed = 0, False, False
            if op in ("<(", ">("):
                command, target = True, None
            elif op in ("(", "()"):  # a subshell, or a function's header: a command follows (zsh's INOUTPAR)
                command = True
            elif op == ")":
                if in_pattern:
                    cases[-1], command = "body", True
                else:
                    command = pos == for_close  # a `for name ( ... )` list closes: its body follows
            elif op in syntax.OUT_REDIRECTS or op in syntax.IN_REDIRECTS:
                target = command if target is None else target
                command, heredoc = False, op in ("<<", "<<-")
            elif not in_pattern:  # ; ;; ;& ;;& & && || | |&
                command, target, heredoc = True, None, False
                if op in (";;", ";&", ";;&") and cases and cases[-1] == "body":
                    cases[-1] = "pattern"
            continue
        # a word: copy it, marking the groups and ranges zsh reads in it; the other reading restores a range and a group that
        # opens the word, and keeps a group glued inside it whole
        start, j, word, alternative, state = i, i, [], [], None
        braced = _BRACE_RUN_RE.match(text, i).end() if c == "{" else i  # where the braces the word opens with end
        while j < n:
            ch = text[j]
            if state == "'":
                piece, state = ch, (None if ch == "'" else state)
            elif ch == "\\" and j + 1 < n:
                piece = text[j : j + 2]
            elif state == '"':
                piece, state = ch, (None if ch == '"' else state)
            elif ch in "'\"":
                piece, state = ch, ch
            elif ch in " \t;&|>)":
                break
            elif ch == "$" and text.startswith("${", j):
                piece = text[j : braces[j + 1] + 1] if j + 1 in braces else text[j:]
            elif ch == "<":
                m = None if (cond or heredoc) else syntax.ZSH_RANGE_RE.match(text, j)
                if not m:
                    break
                word.append(syntax.ZSH_RANGE_OPEN + text[j + 1 : m.end() - 1] + syntax.ZSH_RANGE_CLOSE)
                alternative.append(m.group())
                j = m.end()
                continue
            elif ch == "(":
                if j > start and text[j - 1] == "$" and j in parens:  # $(( arithmetic ))
                    # Marked whole, blanks and parentheses included, so the expansion stays inside this word.  The
                    # `$` the word already holds is left alone: it still says this word's value comes from an expansion, and
                    # `$((1)) push` is refused for the command word it names, which is the expansion check's reading and
                    # not this marking's to change.  Only the `$`s inside the parentheses are marked, by the table.
                    marked = text[j : parens[j] + 1].translate(_ARITH_WORD)
                    word.append(marked)
                    alternative.append(marked)
                    j = parens[j] + 1
                    continue
                if j == start + 1 and text[start] == "=":
                    # zsh's `=(...)` process substitution runs its command (probed: `cat =(git push)` pushed): read as
                    # a subshell, the plain reading, not as a group glued to `=`
                    punctuation_next = True
                    break
                else:
                    # A reserved word glued to `(` in command position (SPD-174; GluedReservedWordTest has the probes).
                    # zsh splits every brace off a run of them opening the word, so `{(` and `{{(` open their groups and
                    # a subshell, in both readings.  Any other -- `else(`, `then(`, `do(`, `time(`, `!(`, `}(` -- is
                    # one glob word to zsh, whose group bareglobqual reads as qualifiers that may run code, and bash runs
                    # it as the reserved word and a subshell where that word belongs: zsh's reading keeps the group in the
                    # word, and the other reading restores the parenthesis.
                    brace_run = command and start < j == braced
                    reserved = command and not brace_run and 0 < j - start <= 9 and text[start:j] in ZSH_COMMAND_POSITION_WORDS
                    # `name=(`, `name+=(` and `name[1,0]=(`: an array assignment's parenthesis, never a group
                    array = target is None and j > start and text[j - 1] == "=" and assignment_words.array_head(text[start:j])
                    group = None
                    if not (cond or heredoc or brace_run or array or text.startswith("()", j)):
                        group = _zsh_group(text, j, scan)
                    if group is None:
                        punctuation_next = True  # the walk reads this parenthesis in the plain reading
                        break
                    word.append(group[0])
                    alternative.append(text[j : group[1]] if j == start or reserved else group[0])
                    j = group[1]
                    continue
            else:
                run = _PLAIN_RUN_RE.match(text, j)  # ordinary characters, copied at once
                piece = run.group() if run else ch
            word.append(piece)
            alternative.append(piece)
            j += len(piece)
        if j == start:  # nothing a word could hold: the outer loop reads it as punctuation
            punctuation_next = True
            if c not in ";&|<>()":
                out.append(c)
                other.append(c)
                i += 1
            continue
        out.append("".join(word))
        other.append("".join(alternative))
        w, i = text[start:j], j
        was_name, was_count = for_list == 1, repeat_count
        arith_next, for_list, repeat_count = False, 0, False
        after_close, closed = closed, False
        # SPD-142: a word ending in the `}` that closes a group is read as the word before that brace, which then gives
        # zsh its command position back -- but inside `[[ ... ]]` and a case's subject or patterns only the `]]` and the
        # esac that end them close a group with a brace glued on (`{ [[ -n x ]]}`, `{ case ... esac}`)
        before = _before_close(w, command)
        if before is not None:
            w = before
        if heredoc:
            heredoc = False
        if target is not None:
            command, target = target, None
        elif w.isdigit() and j < n and text[j] in "<>":
            pass  # a file descriptor before its redirection operator
        elif cond:
            if w == "]]":
                cond, command = False, True
            else:
                before = None
        elif cases and cases[-1] == "subject":
            cases[-1], before = "in", None
        elif cases and cases[-1] == "in":
            cases[-1], before = ("pattern" if w == "in" else "in"), None
        elif in_pattern:
            if w == "esac":
                cases.pop()
                command = True
            else:
                before = None
        elif command:
            if w == "case":
                cases.append("subject")
                command = False
            elif w == "esac" and cases:
                cases.pop()
            elif w == "[[":
                cond, command = True, False
            elif w in ("for", "select", "foreach", "function", "repeat"):
                command, arith_next = False, w in ("for", "select")
                for_list, repeat_count = 1 if w in ("for", "select") else 0, w == "repeat"
            elif w in ZSH_COMMAND_POSITION_WORDS or assignment_words.assignment_word(w):
                pass
            elif w == "always" and after_close:
                pass  # zsh's try-always form, `{ a } always { b }` (SPD-124): its block follows in command position
            else:
                command = False
        if before is not None:
            command, closed = True, True
        if was_name and not for_list:
            for_list = 2  # the loop's name was read: a `(` now opens its word list, not a pattern
        elif was_count:
            command = True  # `repeat word`: its body follows, in command position (zsh's SHORT_LOOPS)
    return "".join(out), "".join(other)
