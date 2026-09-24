"""shell/assigning_builtins: The names an assigning builtin's own words assign, read by each builtin's grammar (SPD-254).

A module of its own since SPD-259, taken out of shell/assignment_words: shell/expansions records what builtin_names reads
for read, printf -v, mapfile, getopts, unset, set -A, zsh's module builtins and the rest of syntax.ASSIGNING_COMMANDS'
builtins; its `settle` is shell/arithmetic_assignments'.  It reads words only and imports nothing of the shell
reading's import cycle: assignment_words, which reads it an element's subscript, is outside it too.  Past 250 lines as
one reading: builtin_names and every grammar it dispatches to share one option scan (_scan) and one gatherer (_Assigned),
which no cut would leave whole."""

import re

from . import assignment_words, prepare, syntax
from ..core import lazy
from ..hooks import hookio


# Each builtin's grammar, as the bash 5 and zsh 5.9 manuals give it and the probes of tests/test_hooks_words.py
# AssigningBuiltinTest confirm where this Mac's zsh 5.9 and bash 3.2.57 have the builtin: (the option letters that take a
# value, the letters whose value is a name the builtin assigns, the letters whose value zsh takes only glued or as the next
# word, both read).  A builtin with one grammar per shell is read with each, and a name either reading finds counts.
_READ_GRAMMARS = (("adinNptu", "a", ""), ("du", "", "tk"))  # bash's read, then zsh's (its -p is the coprocess, no prompt)
_OPTION_GRAMMARS = {
    "printf": ("v", "v"), "print": ("uCfvxX", "v"), "wait": ("p", "p"), "strftime": ("s", "s"), "zstat": ("fFAH", "AH"),
    "stat": ("fFAH", "AH"), "zselect": ("taA", "aA"),
}
# The builtins whose operand names what they assign, at this index past their options, and the name each assigns with none
_POSITIONAL_GRAMMARS = {
    "mapfile": ("dnOsuCc", 0, "MAPFILE"), "readarray": ("dnOsuCc", 0, "MAPFILE"), "vared": ("prMmift", 0, None),
    "sysread": ("cisot", 0, "REPLY"),
}
_DEFAULT_NAMES = {"zselect": "reply"}
# The builtins that assign no variable where a Bash call runs them: zle defines and runs widgets, and the completion
# builtins fail outside completion (probed in zsh 5.9 -f, non-interactive: `zle -N X` left X, `zle -M hi`, `compadd -A X a
# b` and `compset -p 1` exited 1 and left X)
_ASSIGNING_NOTHING = frozenset(("zle", "compadd", "compset"))
_ONE_WORD_RE = lazy.LazyPattern(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})" + syntax._QUOTED_NAME
                                + "|" + re.escape(hookio.SUBST) + syntax._QUOTED_SUBST)


def _word_reach(word):
    """How far a masked word may reach once the shell expands it: "plain", no expansion and no glob, the text it spells;
    "one", double-quoted expansions alone, one word whose text the hook does not know; "any", an unquoted expansion or a
    glob, which may become no word, several, or any text at all."""
    if hookio.SUBST not in word and not syntax._EXPANDING_DOLLAR_RE.search(word):
        return "any" if syntax.GLOB_RE.search(word) else "plain"
    rest = _ONE_WORD_RE.sub("", word)
    if hookio.SUBST in rest or syntax._EXPANDING_DOLLAR_RE.search(rest) or syntax.GLOB_RE.search(rest):
        return "any"
    return "one"


_BARE_EXPANSION_RE = lazy.LazyPattern(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})")


def _may_open(word, settle=None):
    """Whether a word the hook cannot read wholly is one the builtin reads as options: spelled with a `-` or `+` (`-v$N`),
    or a `$name` the line settles (`settle`, as arithmetic_names takes it) to a value that begins with one (`X='-v Y';
    printf $X hi` assigns Y).  An expansion the line does not settle -- the environment's, a loop's file name, a
    substitution's output -- is read as an operand (`printf "$f\\n"`, `wait $pid`): what it holds is data the line does not
    spell, which a member has no plausible reason to shape into an option (the Bash rule's threat model: a cooperative
    member), and a name the builtin reads from it is still refused (_Assigned.name)."""
    text = prepare.deglob(word)
    m = _BARE_EXPANSION_RE.match(text) if settle is not None else None
    value = settle(m.group(1) or m.group(2)) if m is not None else None
    if value is not None:
        text = value.lstrip() + text[m.end() :]
    return text[:1] in ("-", "+")


_NOT_NAME_RE = lazy.LazyPattern(r"[^A-Za-z0-9_]")


def _first_expansion(text):
    """Where the first expansion or substitution stands in a word's spelled text (deglob's), or its length."""
    found = [k for k in (text.find("$"), text.find(hookio.SUBST)) if k >= 0]
    return min(found) if found else len(text)


def _scan(args, values, optional="", plus=False, settle=None):
    """[(options, operands, stuck)]: each way a builtin's option scan may read `args`, its words after its name --
    options as [(letter, value)], operands the words after them, and stuck the first word the scan cannot read (one that
    may become an option, or an option word holding an expansion) or None.  Options end at `--`, `-` or the first word
    that is none.  A letter of `values` takes the rest of its word, else the next word (a masked word, whatever it holds);
    one of `optional` the rest of its word, or else none and the next word both, each a reading of its own; with `plus`
    a `+` opens options too (zsh's `set +A`).  `settle`: _may_open's."""
    readings, pending = [], [(0, [])]
    while pending:
        i, options = pending.pop()
        stuck = None
        while i < len(args):
            word, text = args[i], prepare.deglob(args[i])
            if _word_reach(word) != "plain":
                # an option word whose expansion stands in an option's value (`print -u$(( 2 - help ))`) is read as spelled;
                # one whose expansion may be a letter (`-$X`, `-r$X`) or may itself be an option (_may_open) is not read
                opens = text[:1] == "-" or plus and text[:1] == "+"
                if not (opens and any(c in values or c in optional for c in text[1 : _first_expansion(text)])):
                    if _may_open(word, settle):
                        stuck = word
                    break
            if text in ("--", "-"):
                i += 1
                break
            if len(text) < 2 or not (text[0] == "-" or plus and text[0] == "+"):
                break
            i += 1
            for k in range(1, len(text)):
                c = text[k]
                if c not in values and c not in optional:
                    options.append((c, None))
                    continue
                if text[k + 1 :]:
                    options.append((c, text[k + 1 :]))
                elif c in optional:
                    pending.append((i + 1, options + [(c, args[i] if i < len(args) else None)]))
                    options.append((c, None))
                else:
                    options.append((c, args[i] if i < len(args) else None))
                    i += 1
                break
        readings.append((options, [] if stuck is not None else args[i:], stuck))
    return readings


class _Assigned:
    """What builtin_names gathers from a builtin's words: the names it assigns, the subscripts of the elements among them,
    the first word it reads a name from that the hook cannot read, and the code it is handed to run."""

    __slots__ = ("names", "subscripts", "stuck", "evaluated")

    def __init__(self):
        self.names, self.subscripts, self.stuck, self.evaluated = [], [], None, None

    def refuse(self, word):
        if self.stuck is None:
            self.stuck = word

    def name(self, word, prompt=False):
        """A word the builtin reads a name from: a name, an element `name[subscript]` (`read 'a[1]'` assigned one in both
        shells), or, zsh's read's first operand, `name?prompt`.  A word that names no variable names none -- the builtin
        fails on it, or assigns a positional parameter (zsh's `read -n 1 X` put `a` in $1) -- and one the hook cannot read
        may name any, unless it stays one word whose text before its first expansion already names an element (`"a[$i]"`)
        or can be no name (`"Enter $what: "`)."""
        if word is None:
            return
        reach, text = _word_reach(word), prepare.deglob(word)
        known = text if reach == "plain" else text[: _first_expansion(text)]
        if prompt and "?" in known:
            text = known = known[: known.index("?")]
            reach = "plain"
        head = assignment_words._SUBSCRIPTED_HEAD_RE.match(known)
        if reach == "plain" and syntax.IDENTIFIER_RE.match(text):
            self.names.append(text)
        elif reach == "plain" and head is not None and assignment_words.subscript_end(text, head.end() - 1) == len(text) - 1:
            self.names.append(text[: head.end() - 1])
            self.subscripts.append(text[head.end() : -1])
        elif reach != "plain" and head is not None:  # `a[$i]`: an element of a, whatever its subscript holds
            self.names.append(known[: head.end() - 1])
            self.subscripts.append(text[head.end() :].removesuffix("]"))
        elif reach != "plain" and (reach == "any" or not _NOT_NAME_RE.search(known)):
            self.refuse(word)

    def at(self, operands, k):
        """The operand at index k names what the builtin assigns: each word up to it must stay one word, or the name
        moves (an unquoted empty expansion is no word at all)."""
        for word in operands[: k + 1]:
            if _word_reach(word) == "any":
                self.refuse(word)
                return
        if k < len(operands):
            self.name(operands[k])

    def read(self, readings, names, every=False, prompt=False):
        """Each reading _scan made: the word it stuck on, the names its options' values give, and, `every`, its operands."""
        for options, operands, stuck in readings:
            if stuck is not None:
                self.refuse(stuck)
            for letter, value in options:
                if letter in names:
                    self.name(value)
            if every:
                for k, word in enumerate(operands):
                    self.name(word, prompt and k == 0)


def builtin_names(words, settle=None):
    """(names, subscripts, stuck, evaluated) for an assigning builtin (syntax.ASSIGNING_COMMANDS but for `let`, `integer`,
    `float` and `private`, whose operands are arithmetic or declarations), read by the builtin's own grammar where
    ASSIGNING_COMMANDS once read every identifier in every word (SPD-254): the names it assigns in zsh's reading or bash's,
    the subscripts of the elements among them (each arithmetic, SPD-225), the first word the hook cannot read where the
    builtin reads a name or an option (refused a member, SPD-217), and the word holding code the builtin is handed to run
    (`mapfile -C`'s callback, `zstyle -e`'s value), or None.  `settle`, as arithmetic_names takes it, says what a `$name`
    where an option may stand begins with (_may_open).

    Probed in zsh 5.9 -f, zsh -f -o nobareglobqual and bash 3.2.57, each fed `a b`: `read -t 1 X Y` gave X=a Y=b in all
    three; `read -tX Y` took X for the timeout in both; `read -p P X` failed in zsh and gave X in bash; `read 'X?prompt'`
    gave X in zsh; `read -a A` gave bash's array, `read -A A` zsh's; `printf -vX %s hi` assigned X in bash alone (zsh
    printed `-vX`) and `printf -- -v X` in neither; `print -rv X hi` assigned X in zsh; `getopts a: Y -a val` set Y, OPTARG
    and OPTIND; `unset -f X` kept the variable and zsh's `unset -m 'X*'` unset X by a pattern; `set -sA X b a` and `set +A
    X c` assigned X in zsh; `zparseopts -a A h=H -help=H2` set A, H and H2.  The builtins bash 4 and 5 added (mapfile,
    readarray, `read -i`/`-N`, `wait -p`, `unset -n`) and zsh's module builtins are read from their manuals, the side that
    records a name where the shell may not."""
    cmd, args = prepare.deglob(words[0]), list(words[1:])
    got = _Assigned()
    if cmd == "read":
        for (values, names, optional), prompt in zip(_READ_GRAMMARS, (False, True)):
            got.read(_scan(args, values, optional, settle=settle), names, every=True, prompt=prompt)
        if not got.names and got.stuck is None:
            got.names.append("REPLY")
    elif cmd in _OPTION_GRAMMARS:
        values, names = _OPTION_GRAMMARS[cmd]
        got.read(_scan(args, values, settle=settle), names)
        if not got.names and cmd in _DEFAULT_NAMES:
            got.names.append(_DEFAULT_NAMES[cmd])
    elif cmd in _POSITIONAL_GRAMMARS:
        values, k, default = _POSITIONAL_GRAMMARS[cmd]
        for options, operands, stuck in _scan(args, values, settle=settle):
            got.read([(options, operands, stuck)], "c" if cmd == "sysread" else "")
            callback = next((v for c, v in options if c == "C" and cmd != "sysread"), None)
            if callback is not None:
                got.evaluated = callback
            got.at(operands, k)
        if not got.names and default is not None and got.stuck is None:
            got.names.append(default)
    elif cmd == "getopts":
        got.at(args, 1)
        got.names.extend(("OPTARG", "OPTIND"))
    elif cmd == "getln":
        got.read(_scan(args, "", settle=settle), "", every=True)
    elif cmd == "unset":
        for options, operands, stuck in _scan(args, "", settle=settle):
            letters = {c for c, _ in options}
            if "m" in letters:  # zsh's pattern: whichever names match it
                got.refuse(operands[0] if operands else "-m")
            elif "f" not in letters or "v" in letters:
                got.read([(options, operands, stuck)], "", every=True)
            elif stuck is not None:
                got.refuse(stuck)
    elif cmd == "set":
        got.read(_scan(args, "Ao", plus=True, settle=settle), "A")
    elif cmd in ("zstyle", "zformat", "zsystem", "zparseopts"):
        _module_names(cmd, args, got, settle)
    elif cmd in ("zregexparse", "zpty"):
        # zregexparse's two parameters before its regexes (probed: `zregexparse p q a` set p and q to 0), and `zpty -r
        # [-mt] name [param [pattern]]`'s param
        for options, operands, stuck in _scan(args, "", settle=settle):
            got.read([(options, [], stuck)], "")
            if cmd == "zregexparse":
                got.at(operands, 0)
                got.at(operands, 1)
            elif "r" in {c for c, _ in options}:
                got.at(operands, 1)
    elif cmd in ("zsocket", "ztcp"):
        got.names.append("REPLY")  # the descriptor they open
    elif cmd not in _ASSIGNING_NOTHING:  # zcurses, and a loop keyword read as a command: every name any word spells
        for word in args:
            if _word_reach(word) != "plain":
                got.refuse(word)
                break
            got.names.extend(syntax._NAME_RE.findall(prepare.deglob(word)))
        got.names.extend(("REPLY", "reply"))
    return got.names, got.subscripts, got.stuck, got.evaluated


def _module_names(cmd, args, got, settle=None):
    """builtin_names for zsh's zstyle, zformat, zsystem and zparseopts, whose first word decides where a name stands:
    `zstyle -s|-b|-a context style name` and `zstyle -g name` (probed: `zstyle -s ':x' y V` set V), `zformat -f|-F|-a name`
    (`zformat -f V '%a' a:1` set V), `zsystem flock -f var file`, and zparseopts's `-a`, `-A` and `-v` arrays and each
    spec's `=array`.  `zstyle -e` defines a style whose value zsh evaluates when it is looked up: code, refused unread.
    A first word the hook cannot read is refused where it may be an option (_may_open), zsystem's subcommand or a
    zparseopts spec, and read as the operand it is otherwise (`zstyle $context style value` defines a style)."""
    if not args:
        return
    if _word_reach(args[0]) != "plain":
        if _may_open(args[0], settle) or cmd in ("zparseopts", "zsystem"):
            got.refuse(args[0])
        return
    first = prepare.deglob(args[0])
    if cmd == "zstyle":
        if first == "-e":
            got.evaluated = " ".join(args)
        elif first in ("-s", "-b", "-a", "-g"):
            got.at(args[1:], 0 if first == "-g" else 2)
    elif cmd == "zformat":
        if first in ("-f", "-F", "-a"):
            got.at(args[1:], 0)
    elif cmd == "zsystem":
        if first == "flock":
            got.read(_scan(args[1:], "tf"), "f")
    else:
        _zparseopts_names(args, got)


def _zparseopts_names(args, got):
    """zparseopts's own options (-D -E -F -K -M flags, -a, -A and -v arrays) up to `-`, `--` or the first spec, then each
    spec's array, the name after its last `=` (probed: `zparseopts -a A h=H -help=H2` set A, H=-h and H2=--help)."""
    i = 0
    while i < len(args):
        word = args[i]
        if _word_reach(word) != "plain":
            got.refuse(word)
            return
        text = prepare.deglob(word)
        if text in ("-", "--"):
            i += 1
            break
        if text[:2] in ("-a", "-A", "-v"):
            if len(text) > 2:
                got.name(text[2:])
                i += 1
            else:
                got.name(args[i + 1] if i + 1 < len(args) else None)
                i += 2
            continue
        if len(text) > 1 and text[0] == "-" and set(text[1:]) <= set("DEFKM"):
            i += 1
            continue
        break
    for word in args[i:]:
        if _word_reach(word) != "plain":
            got.refuse(word)
            return
        spec = prepare.deglob(word)
        if "=" in spec:
            got.name(spec.rsplit("=", 1)[1])
