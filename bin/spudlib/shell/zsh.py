"""shell/zsh: zsh's own glob operators, the flag group of its `${(...)...}`, and the arithmetic both shells read as arithmetic."""

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
                    **{"$": "$" + syntax._LITERAL_DOLLAR, syntax.LINE_BREAK: syntax._ARITH_SENTINELS["\n"]})
_ARITH_COMMAND = str.maketrans(_ARITH_MARKS)
_ARITH_WORD = str.maketrans(dict(_ARITH_MARKS, **{c: syntax._ARITH_SENTINELS[c] for c in " \t"}))
# How _zsh_group marks a group's parentheses, bars, blanks and range brackets.  In a word zsh expands, with the zsh
# sentinels: active glob syntax, which the hook expands against the tree and whose trailing qualifiers' code it reads.  In a
# case pattern (SPD-181) with the inert arithmetic ones: zsh matches a pattern against the case's word and generates no file
# names from it, so it expands nothing there and runs no qualifier's code (probed in zsh 5.9 -f: with a file f1 present,
# `case f*(e:"echo SUBJ":) in f1) ...` took the `*)` arm, and the patterns `(x)(e:"echo QUAL":))`, `((x)(e:"echo
# QUAL2":))` and `x(e:"echo QUAL3":))` printed nothing).  A `;` a pattern's group holds is marked too (_zsh_group), and so
# is a newline a group holds, in a pattern or any other word (SPD-183).
_GROUP_MARKS = syntax._ZSH_SENTINELS
_PATTERN_MARKS = {c: syntax._ARITH_SENTINELS[c] for c in "(|)<> \t;\n"}
_WORD_END = " \t;&|>)" + syntax.LINE_BREAK  # what ends a word the scan copies, when no quote holds it
# zsh's parameter expansion flags (zshexpn(1)), the group in parentheses right after `${`: the characters a flag is spelled
# with, the flags that take a delimited argument (`j:,:`, `s/x/`, `l:10::0:` -- `l` and `r` up to three, each opened by
# the first one's delimiter), the closer of a delimiter that opens a pair, and how far an argument's closer is looked for.
_FLAG_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0#%@~*-+_")
_FLAG_ARGUMENTS = "gIjlrsZ_"
_FLAG_CLOSERS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_FLAG_ARGUMENT_LIMIT = 256
# A flag group's parentheses as mark_zsh_patterns writes them: a quoted operator character's sentinel, which keeps them in
# the word and deglob restores (SPD-189).
_FLAG_MARKS = str.maketrans({c: syntax._PUNCT_SENTINELS[c] for c in "()"})
# The states of mark_zsh_patterns' case stack that read a case's word or one of its patterns (SPD-184): a process
# substitution opened there is a list zsh runs, and its `)` is never the pattern's.  Beside them the stack holds "body",
# and, for SPD-184, "proc" (the list of a process substitution opened in one of these states), "inner" (a parenthesis
# opened inside that list: a subshell, a for loop's word list, a nested substitution) and "group" (a group opened there
# that _zsh_group does not take: one holding a `<( )`, or a parse error), each closed by its own `)`.
_CASE_WORD_STATES = ("subject", "in", "pattern")


def _scan_pairs(text):
    """One pass over a line's masked outer text, quotes and escapes skipped: the index of the `)` matching each
    unquoted `(` and of the `}` matching each unquoted `{`, the sorted indexes of the unquoted characters a zsh glob group
    cannot hold (`&` `>`, a `<` that opens no range), and those of its unquoted `;`s, which a group in a case pattern may
    hold (_zsh_group).  A newline, syntax.LINE_BREAK here, is none of them: a group holds it (SPD-183).  Marking a line
    reads groups through it, in time linear in the line's length: scanning from every `(` of a line of unbalanced ones was
    quadratic."""
    parens, braces, bad, semicolons, opened, braced = {}, {}, [], [], [], []
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
        elif c == ";":
            semicolons.append(i)
        elif c in "&>":
            bad.append(i)
        i += 1
    return parens, braces, bad, semicolons


def _zsh_group(text, i, scan, pattern=False):
    """(the marked text, the index after it) of the zsh glob group opening at text[i], or None where zsh reads none: unbalanced,
    or holding an unquoted `&` `>`, a `<` that opens no range or a spelled `;`.  zsh's lexer ends the word at each of those
    and leaves the group open, a parse error at its `)` on the same line (probed); a `;` whose `)` stands on a later line
    separates the command after it, which runs (SPD-183, probed in zsh 5.9 -f and -f -o nobareglobqual: `x=a(l ; echo RAN7`
    with its `)` on the next line printed RAN7), so the plain reading stands.  Blanks, bars, nested groups and ranges are
    part of the pattern, and so is a newline, syntax.LINE_BREAK here, wherever it stands in the group (SPD-183, probed in the
    same shells: `echo x > (l|<newline>x)/t`, `(l|x<newline>)/t` and `(l|x)(|<newline>)/t` wrote into l, `(<newline>l|x)/t`
    found no match, and `echo (a|<newline>b)` failed with "no matches found: (a|\\nb)"; bash rejects each of those lines); the
    blanks newlines_as_separators wrote around it are dropped, so the pattern holds the newline as the line spells it.
    Quoted and escaped characters stay as they are.  `scan` is the line's _scan_pairs.

    `pattern`: the group stands in a case pattern (SPD-181), marked with _PATTERN_MARKS, and a spelled `;` in it is part of
    the pattern too: zsh rejects the whole case command then, whatever line its `)` stands on (probed: `case x in ((x;y))
    echo S8;; esac` failed near `;`, and so did `case x in (x ; echo RAN1` with `)) echo B1;; esac` on the next line, running
    nothing of either), a line that runs nothing."""
    parens, _braces, bad, semicolons = scan
    end = parens.get(i)
    if end is None:
        return None
    for held in (bad,) if pattern else (bad, semicolons):
        k = bisect.bisect_right(held, i)
        if k < len(held) and held[k] < end:
            return None
    marks = _PATTERN_MARKS if pattern else _GROUP_MARKS
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
            out.append(marks["("])
        elif c == ")":
            depth -= 1
            out.append(marks[")"])
            if depth == 0:
                return "".join(out), j + 1
        elif c in "| \t":
            out.append(marks[c])
        elif c == syntax.LINE_BREAK:
            if out and out[-1] == marks[" "]:  # the blank newlines_as_separators wrote before it
                out.pop()
            out.append(marks["\n"])
            if text[j + 1 : j + 2] == " ":  # and the one after it
                j += 1
        elif c == "<":
            m = syntax.ZSH_RANGE_RE.match(text, j)
            if not m:
                return None
            out.append(marks["<"] + text[j + 1 : m.end() - 1] + marks[">"])
            j = m.end()
            continue
        elif c == ";" and pattern:
            out.append(marks[";"])
        elif c in ";&>":
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


def flag_group(text, k):
    """The parameter expansion flags zsh reads in the group that opens a `${`, text[k] being the character after its `(`:
    (the flag letters, the index after the group's `)`), or None where no group ends there: a character no flag is
    spelled with, which zsh rejects (probed in zsh 5.9 -f: `${(foo bar)x}` failed "error in flags"), or a delimited
    argument that does not close within _FLAG_ARGUMENT_LIMIT characters, the hook's own bound, which keeps a line of
    unclosed groups linear (EvalFlagTest's pathological input).  An argument's text is no flag: `(j:e:)`
    joins with `e` and evaluates nothing (probed: `x='$(touch q13)'; echo ${(j:e:)x}` made no q13), and `(l(10)(x)e)`
    holds the flags `l` and `e` (SPD-189)."""
    letters, n = [], len(text)
    while k < n:
        c = text[k]
        if c == ")":
            return "".join(letters), k + 1
        if c not in _FLAG_CHARS:
            return None
        letters.append(c)
        k += 1
        if c in _FLAG_ARGUMENTS:
            opener = text[k : k + 1]
            for count in range(3 if c in "lr" else 1):
                if text[k : k + 1] != opener or opener in ("", ")"):
                    if count == 0:
                        return None
                    break
                end = text.find(_FLAG_CLOSERS.get(opener, opener), k + 1, k + 1 + _FLAG_ARGUMENT_LIMIT)
                if end < 0:
                    return None
                k = end + 1
    return None


def _flag_groups(piece):
    """A `${...}` a word copies whole, with the parentheses of each flag group in it marked (SPD-189): zsh reads the
    flags after `${` as part of the expansion (`${(e)x}`, `${(Pe)n}`, `${(j:,:)x}`, a nested `${(e)${(P)n}}`), where
    shlex ended the word at their `(` and the walk read `echo ${(e)x}` as `echo ${`, a subshell running `e` and a command
    named `x}`.  A quoted or escaped `${(` is no expansion and has its sentinels already; a group flag_group cannot read
    is left as it was.  Both readings mark it: bash rejects the expansion ("bad substitution") and runs nothing of it."""
    if "${(" not in piece:
        return piece
    out, k, p = [], 0, piece.find("${(")
    while p >= 0:
        found = flag_group(piece, p + 3)
        if found is not None:
            out.append(piece[k : p + 2])
            out.append(piece[p + 2 : found[1]].translate(_FLAG_MARKS))
            k = found[1]
        p = piece.find("${(", max(k, p + 3))
    out.append(piece[k:])
    return "".join(out)


def _optional_list(text, k):
    """zsh's reading of a `=( ... )` that opens the content of a case pattern's optional parentheses, the `(` just before
    text[k] (SPD-184): (both readings' text for it, the index after it), or None where no `=(` opens that content.  zsh
    lexes that content as the pattern's text, so the substitution runs the list up to its first `)` with the pattern's
    `(` and `|` taken out of it, and only one opening the content runs: probed in zsh 5.9 -f and -f -o nobareglobqual (see
    tests/test_hooks_groups.py CaseSubstitutionTest), `( =(tou|ch r90) )` made r90, `( =(touch (r97|r98)) )` made r97r98,
    `( =(touch r105)|=(touch r106) )` made r105 alone, and a newline before it (`(<newline>=(touch r83) )`) made nothing.
    The list is marked as a line of its own, and read as the `<( ... )` whose file name it hands the pattern: bash
    rejects the line."""
    lead = k
    while lead < len(text) and text[lead] in " \t":
        lead += 1
    end = text.find(")", lead + 2) if text.startswith("=(", lead) else -1
    if end < 0:
        return None
    marked, other = mark_zsh_patterns(text[lead + 2 : end].replace("(", "").replace("|", ""))
    return (text[k:lead] + "<(" + marked + ")", text[k:lead] + "<(" + other + ")"), end + 1


def _ends_pattern(text, k):
    """Whether a case pattern ends at the `)` before text[k], which closes a group of it that _zsh_group did not take
    (SPD-184): unless the word goes on after it, or a `)` or a `|` follows it, that group was the pattern's optional
    parentheses, as SPD-181 reads a word that starts and ends with a group _zsh_group took (probed in zsh 5.9 -f and -f -o
    nobareglobqual: `(q|<(true)) (echo m72)` and `(q|<(true))) (echo m77)` ran their bodies)."""
    n = len(text)
    if k < n and text[k] not in " \t;&<>" + syntax.LINE_BREAK:
        return False
    while k < n and text[k] in " \t":
        k += 1
    return text[k : k + 1] not in (")", "|")


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
      list )` is a parse error, so the subshell read in its place reads a line that runs nothing.  No loop name and no
      word list follow a for loop's `(( ... ))` header: its short body's first word is a command word, and a group opening
      the word after that a pattern (SPD-178: `echo new | for (( i=0; i<1; i++ )) tee (l|x)/t` wrote l/t);
    - a foreach's `( word ... )` after its names is its word list, as a for's is, and never a glob (`foreach f (a|b)` is a
      parse error); a command follows it, and a group opening the word after that is a pattern (SPD-180: `echo ft |
      foreach f (a) tee (l|x)/t > /dev/null; end` wrote l/t).  zsh reads the word after each of a for's, a select's or a
      foreach's names in command position (SPD-182), so for a for or a foreach more names may follow the first, and a
      `{`, a `do` or another reserved word there opens a body over the positional parameters, a subshell possible after
      it, and a group opening the word after that body's command word is a pattern (`echo fd | for f do tee (l|x)/t >
      /dev/null; done` and `{ select f tee (l|x)/t < in; } < in` wrote l/t);
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
    - a case pattern is read as zsh's lexer reads it (SPD-181): each of its words whole, a group in it with the blanks, bars
      and newlines it holds, so the pattern ends at the `)` after its last word (`(x|y))`, `(x))`, `x|(y))`, `(x)y)`); a word
      that starts and ends with a group, followed by anything but a `)` or a `|`, is the pattern in its optional
      parentheses (`((x))`, `( (x) )`, `(x)`) and ends the pattern itself.  No word of a pattern stands in command
      position -- `((` after `;;`, `;&` or `;|` is a pattern, never an arithmetic command, and `mode=(` a group, never an
      array -- and the body after it does.  zsh generates no file names from a pattern, so its groups are marked with the
      inert _PATTERN_MARKS; bash rejects a group in a pattern, and the other reading restores one that opens a word
      (CasePatternGroupTest has the probes);
    - a case's word and patterns hold process substitutions zsh runs (SPD-184): a `=( ... )` opening the word or a
      top-level alternative of a pattern, one opening the content of the pattern's optional parentheses (whose list runs
      to its first `)` without the pattern's `(` and `|`: _optional_list), and a `<( ... )` or `>( ... )` anywhere in
      them, a group included.  Both readings take each for a `<( ... )` or `>( ... )`: its list is read as commands, its
      `)` never ends the pattern (the case stack's "proc", "inner" and "group" states, _CASE_WORD_STATES), and the
      pattern goes on after it.  A `=` anywhere else in a pattern is the pattern's text (CaseSubstitutionTest has the
      probes);
    - a newline, which newlines_as_separators writes as syntax.LINE_BREAK, is part of the pattern inside a group, in any
      word (SPD-183: `echo x | tee (l|<newline>x)/t` wrote l/t, and _zsh_group has the probes), and everywhere else the `;`
      it ends a command with, as the walk always read it; bash rejects such a group, and the other reading, restoring
      one that opens a word, reads its newline as `;` too;
    - `[[ ... ]]`, `${...}` and here-document delimiters are left as they are, but for the parentheses of a `${...}`'s
      flag group (`${(e)x}`), which stay in the word in both readings (_flag_groups, SPD-189).

    An arithmetic command `(( ... ))` and an arithmetic expansion `$(( ... ))` are marked too, with the arithmetic
    sentinels rather than the pattern ones: both shells evaluate what stands between the parentheses, so an operator there is
    an operator of the arithmetic and never of the shell, and both readings get the same marking.

    Both texts are the input, each LINE_BREAK a `;`, when it holds none of these."""
    if "(" not in text and "<" not in text:
        text = text.replace(syntax.LINE_BREAK, ";")
        return text, text
    scan = _scan_pairs(text)
    parens, braces = scan[0], scan[1]
    out, other, i, n = [], [], 0, len(text)
    command = True  # zsh's command position
    target = None  # after a redirection operator: the command position to restore after its target
    heredoc = cond = arith_next = punctuation_next = False
    # zsh's `for name ( word ... )` word list is not a glob (`for f (a|b)` is a parse error), and a command follows
    # it and a `repeat` count, so `for f (a b) (git push)` and `repeat 1 (git push)` open subshells, not patterns.
    for_list = 0  # 1: the loop's name is next; 2: a `(` here opens its word list; a `for (( ... ))` header clears it
    # a for's or a foreach's header (SPD-180, SPD-182): more names may follow its first; a select takes one
    more_names = False
    repeat_count = False  # the next word is a `repeat` count; the body, in command position, follows it
    for_close = -1  # where a `for name (` list closes
    closed = False  # the word just read ended in the `}` that closes a group (SPD-142: an `always` after it keeps command position)
    # per open case command: "subject", "in", "pattern" or "body"; above those, per parenthesis open in a case's word or
    # pattern, "proc", "inner" or "group" (_CASE_WORD_STATES, SPD-184)
    cases = []
    while i < n:
        c = text[i]
        if c in " \t":
            out.append(c)
            other.append(c)
            i += 1
            continue
        top = cases[-1] if cases else None
        in_pattern = top == "pattern"
        pattern_text = in_pattern or top == "group"  # a pattern's text, a group the scan did not take included
        if c == "=" and top in _CASE_WORD_STATES and text.startswith("=(", i) and not punctuation_next:
            # SPD-184: a `=( ... )` opening the case's word or a top-level alternative of a pattern is a process
            # substitution, its list parsed as commands, and its `)` is the list's, never the pattern's (probed in zsh 5.9
            # -f and -f -o nobareglobqual: `case x in =(touch r01)) ...`, `case =(touch r02) in x) ...`, `y|=(...)`,
            # `x| =(...)`, `;; =(...)` and `=(case y in y) touch r17;; esac))` made theirs, and `x|=(tou|ch r100))` ran
            # `tou` and `ch`; tests/test_hooks_groups.py CaseSubstitutionTest has the rest).  Both readings take it for the
            # `<( ... )` whose file name it hands the case, as zsh does -- a file holding the list's output -- and bash
            # rejects the line.
            out.append("<(")
            other.append("<(")
            cases.append("proc")
            i += 2
            command, target, arith_next = True, None, False
            for_list, repeat_count, closed, more_names = 0, False, False, False
            continue
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
                # ... and what the word before it left pending ends here, as it ends at any word or operator (SPD-178).
                # A `for (( ... ))` header has no name and no word list, so its short body's first word is its command
                # word and a `(` opening the word after that a pattern: in the same shells, with l/t present, `echo new |
                # for (( i=0; i<1; i++ )) tee (l|x)/t` wrote l/t and `for (( i=0; i<1; i++ )) echo (b|c)` printed `b c`
                # (tests/probes/shell_probe.py).  A repeat count and a closing brace are never pending here on a line
                # zsh runs: `((` right after `repeat` opens its count word, not an arithmetic command, and right after a
                # closing brace it is a parse error (`{ echo a } (( 1 ))` failed near ` 1 `).
                for_list, repeat_count, closed, more_names = 0, False, False, False
                continue
            if for_list == 2 and i in parens:  # `for f ( a b )`: the loop's word list
                for_close = parens[i]
                word_start = False
            else:
                # a case pattern stands outside command position (SPD-181), so a group opens its word there as anywhere else
                word_start = not (command or cond or heredoc or text.startswith("()", i)) and _zsh_group(text, i, scan, pattern_text) is not None
        else:
            word_start = c == "<" and not (cond or heredoc) and syntax.ZSH_RANGE_RE.match(text, i) is not None
        punctuation_next = False
        if c == syntax.LINE_BREAK or c in ";&|<>()" and not word_start:
            if c == syntax.LINE_BREAK:  # a newline outside a group: the `;` it always was to the walk (SPD-183)
                op = ";"
            else:
                op = "()" if text.startswith("()", i) else next((o for o in syntax.SHELL_OPERATORS if text.startswith(o, i)), c)
            out.append(op)
            other.append(op)
            pos, i = i, i + len(op)
            arith_next = False
            for_list, repeat_count, closed, more_names = 0, False, False, False
            if op in ("<(", ">("):
                command, target = True, None
                if top in ("proc", "inner"):
                    cases.append("inner")
                elif top in _CASE_WORD_STATES or top == "group":
                    # SPD-184: zsh runs a `<( )` or `>( )` anywhere in a case's word or pattern, a group included, its
                    # list parsed as commands (probed in zsh 5.9: `(x|<(touch r27)))`, `a>(touch r29))` and `case (y|<(touch
                    # r112)) in` made theirs, `(x|<(tou|ch r102)))` ran `tou` and `ch`); its `)` is the list's
                    cases.append("proc")
            elif op in ("(", "()"):  # a subshell, or a function's header: a command follows (zsh's INOUTPAR)
                command = True
                if op == "(" and top in ("proc", "inner"):
                    cases.append("inner")  # counted, so the substitution's list ends at its own `)`
                elif op == "(" and (top in _CASE_WORD_STATES or top == "group"):
                    # SPD-184: a group in a case's word or pattern that _zsh_group did not take -- one holding a `<( )`, or
                    # a parse error -- is still the pattern's text, and its `)` closes it; a `=( ... )` opening it is
                    # read as one opening the optional parentheses (probed: `( =(touch r82) <(true) )` made r82)
                    cases.append("group")
                    command = False
                    listed = _optional_list(text, i) if top == "pattern" else None
                    if listed is not None:
                        out.append(listed[0][0])
                        other.append(listed[0][1])
                        i = listed[1]
            elif op == ")":
                if top in ("proc", "inner", "group"):
                    cases.pop()
                    command = pos == for_close if top == "inner" else False
                    if top != "inner" and cases and cases[-1] == "subject":
                        cases[-1] = "in"  # the case's word is read, the substitution or the group part of it
                    elif top == "group" and cases and cases[-1] == "pattern" and _ends_pattern(text, i):
                        cases[-1], command = "body", True  # the pattern's optional parentheses: the body follows
                elif in_pattern:
                    cases[-1], command = "body", True
                else:
                    command = pos == for_close  # a `for name ( ... )` list closes: its body follows
            elif op in syntax.OUT_REDIRECTS or op in syntax.IN_REDIRECTS:
                target = command if target is None else target
                command, heredoc = False, op in ("<<", "<<-")
            elif not pattern_text:  # ; ;; ;& ;| ;;& & && || | |&
                command, target, heredoc = True, None, False
                if op in (";;", ";&", ";|", ";;&") and cases and cases[-1] == "body":
                    # the next arm's pattern, out of command position (SPD-181): a `((` there is its word, not an
                    # arithmetic command (probed in zsh 5.9 -f and -f -o nobareglobqual: `case y in a) true;; ((x)) echo
                    # wrong;; esac` printed nothing and `case y in ((x)) echo wrong2;; ((y)) echo W;; esac` printed W)
                    cases[-1], command = "pattern", False
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
            elif ch in _WORD_END:
                break
            elif ch == "$" and text.startswith("${", j):
                piece = text[j : braces[j + 1] + 1] if j + 1 in braces else text[j:]
                marked = _flag_groups(piece)  # its flags stay in the word (SPD-189)
                word.append(marked)
                alternative.append(marked)
                j += len(piece)
                continue
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
                    # `name=(`, `name+=(` and `name[1,0]=(`: an array assignment's parenthesis, never a group -- but no
                    # word of a case pattern assigns (SPD-181: `case mode=x in mode=(a|x)) echo m4;; esac` printed m4)
                    array = target is None and not pattern_text and j > start and text[j - 1] == "=" \
                        and assignment_words.array_head(text[start:j])
                    group = None
                    if not (cond or heredoc or brace_run or array or text.startswith("()", j)):
                        group = _zsh_group(text, j, scan, pattern_text)
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
        zsh_word, other_word = "".join(word), "".join(alternative)
        w, i = text[start:j], j
        another = more_names and for_list == 2 and syntax.loop_name(w)  # a for's or a foreach's next name, after its first
        was_name, was_count, several = for_list == 1 or another, repeat_count, more_names
        arith_next, for_list, repeat_count, more_names = False, 0, False, False
        after_close, closed = closed, False
        if in_pattern and zsh_word[:1] == _PATTERN_MARKS["("] and zsh_word[-1:] == _PATTERN_MARKS[")"]:
            k = j
            while k < n and text[k] in " \t":
                k += 1
            if text[k : k + 1] not in (")", "|"):
                # SPD-181: zsh's lexer reads a pattern's word whole, and where the first starts and ends with a group and
                # neither a `)` nor a `|` follows, its outer parentheses are the pattern's optional ones: the pattern
                # ends with the word, and the body after it stands in command position.  Probed in zsh 5.9 -f and -f -o
                # nobareglobqual: `case x in ((x)) ( {echo case-sub} );; esac` ran its subshell, `((x)) { echo Z13 }`
                # its group and `((x)) (( 3 > 2 )) && echo Z28` its arithmetic command, making no file 2, and the pattern
                # `(a)(x)` read as `a)(x` (a bad pattern); followed by a `)` or a `|` the word is itself the pattern or
                # its first alternative (`(x|y)) echo C` and `(x) | y) echo Z3` ran).  Any word but the first is a parse
                # error there (`(x)|(y) ( echo P21 )`, `z|(x) echo Z7`), and so read too: a comment's words, which
                # newlines_as_separators keeps, may stand before the first.  The optional parentheses are the shell's
                # in zsh's reading, for the walk to open and close the pattern with; bash, which reads `(x) body` alike,
                # rejects every other such word, and the other reading restores the group as it restores any opening a word.
                listed = _optional_list(text, start + 1)
                if listed is not None:
                    # SPD-184: a `=( ... )` opening the optional parentheses' content is a process substitution; the
                    # rest of the content is read again as the pattern's text, up to the `)` that closes them
                    out.append("(" + listed[0][0])
                    other.append("(" + listed[0][1])
                    cases.append("group")
                    i = listed[1]
                    continue
                out.append("(" + zsh_word[1:-1] + ")")
                other.append(other_word)
                cases[-1], command = "body", True
                continue
        out.append(zsh_word)
        other.append(other_word)
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
        elif pattern_text:
            if in_pattern and w == "esac":
                cases.pop()
                command = True
            else:
                before = None
        elif command:
            if w == "case":
                cases.append("subject")
                command = False
            elif w == "esac" and cases and top not in ("proc", "inner"):  # a substitution's list closes no case
                cases.pop()
            elif w == "[[":
                cond, command = True, False
            elif w in ("for", "select", "foreach", "function", "repeat"):
                command, arith_next = False, w in ("for", "select")
                for_list, repeat_count = 1 if w in ("for", "select", "foreach") else 0, w == "repeat"
                more_names = w in ("for", "foreach")
            elif w in ZSH_COMMAND_POSITION_WORDS or assignment_words.assignment_word(w):
                pass
            elif w == "always" and after_close:
                pass  # zsh's try-always form, `{ a } always { b }` (SPD-124): its block follows in command position
            else:
                command = False
        if before is not None:
            command, closed = True, True
        if was_name and not for_list:
            # the loop's name was read: a `(` now opens its word list, not a pattern.  zsh reads the word after a for's,
            # a select's or a foreach's name in command position (SPD-180, SPD-182), so a `{`, a `do` or another reserved
            # word there opens a body over the positional parameters, a subshell possible after it, and a `((` is an
            # arithmetic command; for a for or a foreach an identifier there is one more name (probed in zsh 5.9 -f and -f
            # -o nobareglobqual: `foreach f { echo x }`, `for f do (echo x); done`, `for f g { (echo x) }` and `select f
            # {(echo x)}` ran their bodies once for each positional parameter, and `for f ((1)) && echo x` ran echo)
            for_list, command, more_names = 2, True, several
        elif was_count:
            command = True  # `repeat word`: its body follows, in command position (zsh's SHORT_LOOPS)
    # what a group did not hold is the `;` the walk always read: a newline in a `${...}` the word copies whole, and one in
    # the other reading's text of a group
    return "".join(out).replace(syntax.LINE_BREAK, ";"), "".join(other).replace(syntax.LINE_BREAK, ";")
