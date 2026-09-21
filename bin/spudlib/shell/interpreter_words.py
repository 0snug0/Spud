"""shell/interpreter_words: the words an interpreter run is read with (SPD-152), against the table in shell/inline_programs.

SPD-150 tabled each interpreter's option grammar and read the words the line spells.  Two shapes were left: a word the
line cannot settle where an option may stand, and a word the line never spells at all because an xargs reads it from
its input.  Both are about the words rather than the grammar, and both have a precedent this module follows.

- **A word the line cannot settle.**  `node $FLAG code` and `ruby $FLAG code` recorded nothing, while python's same
  shape was refused, because the python branch read its options by name (SPD-043's rule: a word the dispatch reads by
  name that the shell expands first is read as each word it can become, and one the hook cannot resolve refuses a
  member).  An interpreter's option or program position is such a word: `$FLAG` there may be `-e`, and then the hook
  would read none of the program that follows.  read_index below finds it for every family in the table, walking the
  same grammar spelled_program walks, so the two can never disagree; the finding, the reason and the reading of a
  settled `$NAME` are all SPD-043's and SPD-127's, unchanged.
- **The option out of xargs's input.**  `echo '-e code' | xargs node` runs `node -e code`, an `-e` the line never
  spells as node's word: `xargs -e` on the line was already refused, its input was not.  SPD-143 reads the input for
  the shell an xargs runs (stdin_text.xargs_string, the command string it hands `sh -c`); read_run below does the same
  for an interpreter, which is handed every word of that input rather than one string.  Where the line does not spell
  the input the option position cannot be read at all, which earns the first shape's reason from a finding of its own,
  read where bash_rule reads an inline program: last, so a write out of that same input keeps SPD-126's reason.
"""

from . import expansions, inline_programs, prepare, syntax


def read_point(base, words, start):
    """expansions.script_point for the first word, from `start`, that this interpreter run's own option scan reads and
    the shell expands first -- a glob or an expansion (SPD-152) -- or None for a command the table does not name, which
    reads no word by name here.  analyse's interpreter branches read it with read_points, as git's, a shell's and
    python's own branches read theirs."""
    kind = inline_programs.interpreter(base)
    return None if kind is None else expansions.script_point(read_index(kind, words, start))


def read_index(kind, words, start=1):
    """(index, whether that word is the program's file), from `start`, of the first word this interpreter's option scan
    reads that holds a glob or an expansion; or None.  The shape expansions.python_read_index answers in, so
    expansions.script_point reads either: the program's file is matched as a path, an option like any other.

    The scan is inline_programs.read_words, the one grammar spelled_program reads, so a word this returns is a word
    that reading would have taken for an option, an option's value, a subcommand or the program -- and a word it does
    not return stands past the point where the run reads any word by name (the text of `-c`, a script's own
    arguments)."""
    for i, role, _spelled in inline_programs.read_words(kind, words):
        if i is not None and i >= start and expansions.active_read_word(words[i]):
            return i, role == "program"
    return None


def read_run(cmd, base, words, a, fed, xargs_input=None):
    """Record what this interpreter run spells as its own program (inline_programs.read_inline), read with the words an
    xargs that runs it hands it where one does (SPD-152).  `xargs_input` is None where no xargs runs this command."""
    got = words if xargs_input is None else xargs_reading(cmd, words, a, xargs_input)
    if got is not None:
        inline_programs.read_inline(cmd, base, got, a, fed)


def xargs_reading(cmd, words, a, xargs_input):
    """The words an xargs hands the interpreter it runs: the ones the line spells with what xargs reads from its input
    appended, or with that input standing where -I or -J puts it (shell/find_xargs marked the word INPUT_OPERAND).
    None where the line does not spell that input, which leaves a position the hook cannot read at all -- an `-e` may
    stand there and the hook would read neither it nor the program it carries.  That is recorded as "inline-word",
    which earns SPD-043's own reason for a word the hook cannot resolve where a command is read by name, in the
    operand SPD-126 shows as `{input}` (analyse's unknown_operand case records a command word the same way); bash_rule
    reads it where it reads an inline program, last of all, so a write out of that same input keeps SPD-126's reason
    (`xargs perl -pi -e s/a/b/ < list`).

    `xargs_input` is (the command string an xargs makes of that input for a shell, whether xargs appends it after the
    words the line spells, the words it appends), which analyse's dispatch built where it read the xargs (SPD-143 and
    stdin_text.xargs_string, xargs_words)."""
    string, appended, input_words = xargs_input
    if appended:
        if input_words is None:
            a.findings.append(("inline-word", _unspelled(cmd)))
            return None
        return words + input_words
    if any(syntax.unknown_operand(w) for w in words):
        if string is None:
            a.findings.append(("inline-word", _unspelled(cmd)))
            return None
        # -I and -J hand the utility one record per run, which is the whole input the line spells, exactly as the string
        # an xargs hands a shell is (SPD-143, stdin_text.xargs_string)
        return [w.replace(syntax.INPUT_OPERAND, string) for w in words]
    return words


def _unspelled(cmd):
    """The word the reason names for an input xargs reads and the line does not spell, shown as SPD-126 shows such an
    operand: `{input}`, with the interpreter it stands beside."""
    return syntax.shown_operands("%s %s" % (prepare.deglob(cmd), syntax.INPUT_OPERAND))
