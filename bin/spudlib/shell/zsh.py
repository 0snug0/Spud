"""shell/zsh: zsh's own glob operators.  Moved from bin/spud_ledger.py (SPD-065)."""

import bisect
import re

from . import syntax


# Reserved words after which zsh is still in command position, so `(` opens a subshell (zsh's lexer: a word turns command
# position off, these and an assignment keep it; probed with `time (cd x)`, `! (cd x)`, `if (cd x)`, `{ (cd x) }`).
ZSH_COMMAND_POSITION_WORDS = {"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "{", "}", "!", "time", "coproc", "nocorrect"}
_PLAIN_RUN_RE = re.compile(r"[^\s;&|<>()'\"\\$]+")  # characters a word copies as they are
_ARRAY_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")  # `name=` or `name+=` before an array's parenthesis


def _scan_pairs(text):
    """One pass over a line's masked outer text, quotes and escapes skipped (SPD-039): the index of the `)` matching each
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


def mark_zsh_patterns(text):
    """zsh's reading of its own glob operators (SPD-039), for the masked outer text of a line: a group `(a|b)` and a numeric range
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
    - glued inside a word, `(` is a pattern in zsh and a syntax error in bash, whole in both readings, except `()` (a function's
      header), `$((`, `name=(` and a reserved word in command position (bash runs `!(`, `{(`, `if(`, `time(`, `then(`, `do(`
      and `else(` as a subshell, zsh `{(` and `else(`);
    - `>(` and `2>(` stay a process substitution; `&>(` and `>|(` open a pattern target;
    - case patterns, `[[ ... ]]`, `(( ... ))`, `${...}` and here-document delimiters are left as they are.

    Both texts are the input when it holds none of these."""
    if "(" not in text and "<" not in text:
        return text, text
    scan = _scan_pairs(text)
    parens, braces = scan[0], scan[1]
    out, other, i, n = [], [], 0, len(text)
    command = True  # zsh's command position
    target = None  # after a redirection operator: the command position to restore after its target
    heredoc = cond = arith_next = punctuation_next = False
    # SPD-042: zsh's `for name ( word ... )` word list is not a glob (`for f (a|b)` is a parse error), and a command follows
    # it and a `repeat` count, so `for f (a b) (git push)` and `repeat 1 (git push)` open subshells, not patterns.
    for_list = 0  # 1: the loop's name is next; 2: a `(` here opens its word list
    repeat_count = False  # the next word is a `repeat` count; the body, in command position, follows it
    for_close = -1  # where a `for name (` list closes
    cases = []  # per open case command: "subject", "in", "pattern" or "body"
    while i < n:
        c = text[i]
        if c in " \t":
            out.append(c)
            other.append(c)
            i += 1
            continue
        in_pattern = bool(cases) and cases[-1] == "pattern"
        if punctuation_next:  # a word stopped here without reading a pattern: this is shell punctuation, as before SPD-039
            word_start = False
        elif c == "(":
            if text.startswith("((", i) and (command or arith_next) and i in parens:  # (( arithmetic )), or a for loop's header
                end = parens[i] + 1
                out.append(text[i:end])
                other.append(text[i:end])
                # a `for (( ... ))` header is followed by its body, in command position; a `(( ... ))` command is not
                i, command, arith_next = end, arith_next and not command, False
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
            for_list, repeat_count = 0, False
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
                    piece = text[j : parens[j] + 1]
                elif j == start + 1 and text[start] == "=":
                    # zsh's `=(...)` process substitution runs its command (SPD-041, probed: `cat =(git push)` pushed): read as
                    # a subshell, as before SPD-039, not as a group glued to `=`
                    punctuation_next = True
                    break
                else:
                    reserved = command and 0 < j - start <= 9 and text[start:j] in ZSH_COMMAND_POSITION_WORDS  # `{(`, `else(`: a subshell
                    array = target is None and j > start and text[j - 1] == "=" and _ARRAY_NAME_RE.fullmatch(text, start, j)
                    group = None
                    if not (cond or heredoc or reserved or array or text.startswith("()", j)):
                        group = _zsh_group(text, j, scan)
                    if group is None:
                        punctuation_next = True  # the walk reads this parenthesis as it did before SPD-039
                        break
                    word.append(group[0])
                    alternative.append(text[j : group[1]] if j == start else group[0])
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
        if heredoc:
            heredoc = False
        if target is not None:
            command, target = target, None
        elif w.isdigit() and j < n and text[j] in "<>":
            pass  # a file descriptor before its redirection operator
        elif cond:
            if w == "]]":
                cond, command = False, True
        elif cases and cases[-1] == "subject":
            cases[-1] = "in"
        elif cases and cases[-1] == "in":
            cases[-1] = "pattern" if w == "in" else "in"
        elif in_pattern:
            if w == "esac":
                cases.pop()
                command = True
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
            elif w in ZSH_COMMAND_POSITION_WORDS or syntax.ASSIGNMENT_WORD_RE.match(w):
                pass
            else:
                command = False
        if was_name and not for_list:
            for_list = 2  # the loop's name was read: a `(` now opens its word list, not a pattern
        elif was_count:
            command = True  # `repeat word`: its body follows, in command position (zsh's SHORT_LOOPS)
    return "".join(out), "".join(other)
