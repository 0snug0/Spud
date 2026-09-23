"""shell/unread: record text the reader did not read, the one fail-closed finding (SPD-217).

The reader has a handful of places where it once dropped, or read past, text the shell will run: a substitution nested
past its depth bound, a `${ }` nested past its own, a `$( )` whose end it cannot place, its own placeholder in a word the
line did not lift, a backslash-escaped substitution a re-reading unescapes, and a value a shell evaluates as code in a
form it does not model.  Each such site records one "unread" finding, (form, shown), which bash_rule.unread_reason turns
into the refusal a member earns -- the form named, and how to respell the line so the hook can read it.  A member alone
is refused; Spud reads on (the findings loop skips these for him), as under every Law 7 fence.  The reasons and the
per-form messages live in bash_rule with the other refusals; this module is only where the findings are made."""

import re

from . import prepare, syntax
from ..hooks import hookio

# A here-document operator and its delimiter word (`<<EOF`, `<< 'EOF'`, `<<-"EOF"`), for finding an unterminated one in a
# `$( )` body the naive extent may have cut short (SPD-194); a quoted delimiter's quotes do not change its name.
_HEREDOC_OP_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

SHOWN = 60  # the most of the offending text a finding carries, before bash_rule cuts it further for the reason
# The reader's private-use alphabet: the quoted/zsh/arithmetic/operator sentinels, the operand markers, PROCSUB_MARK and
# hooks/pathrule's, all in 0xE000-0xE0FF.  A line the member wrote never holds one, so its presence is text the member
# typed into the reading's own marks, which corrupts the pairing of a lifted substitution with its body (SPD-199).
_MARKER_LO, _MARKER_HI = "", ""
BRACE_DEPTH = 32  # the most `${ }` a member may nest before the line is refused unread (SPD-103): no plausible line
# nests parameter expansions this deep, so past it the hook stops reading and refuses rather than model the nesting or
# spend the growing time reading a pathological run of openings (40,000 once took 7.7 s).


def record_unread(a, form, shown):
    """Add one "unread" finding for text the reader did not read (SPD-217), unless the same one is already there: a form
    the reader met more than once on a line (two dropped substitutions) earns one refusal, and the first stands."""
    finding = ("unread", (form, shown))
    if finding not in a.findings:
        a.findings.append(finding)


def unread_shown(text):
    """A compact, readable spelling of text the hook did not read, for a finding's `shown`: the hook's own marks taken
    off, a lifted substitution shown as `$(...)`, every run of blanks and newlines one space, cut to SHOWN characters."""
    shown = " ".join(syntax.shown_operands(prepare.deglob(text)).replace(hookio.SUBST, "$(...)").split())
    return shown if len(shown) <= SHOWN else shown[:SHOWN] + "..."


def has_marker(text):
    """Whether the member's own text holds one of the reader's private-use markers (SPD-199): a sentinel, an operand
    marker or PROCSUB_MARK, which the reader alone produces, so a member's line that holds one corrupts the reading."""
    return any(_MARKER_LO <= c <= _MARKER_HI for c in text)


def marker_shown(text):
    """A readable spelling of text holding a marker, for an unread "placeholder" finding: the substitution placeholder
    kept as it stands, every private-use marker shown as `?`, blanks and newlines collapsed, cut to SHOWN characters."""
    shown = " ".join("".join("?" if _MARKER_LO <= c <= _MARKER_HI else c for c in text).split())
    return shown if len(shown) <= SHOWN else shown[:SHOWN] + "..."


def escaped_substitution(text):
    """Whether text a shell re-parses (a `-c` string, `eval`'s words) holds a backslash-escaped `$(` or backtick the
    shell unescapes and runs, outside single quotes (SPD-196).  shlex leaves the backslash of a double-quoted `\\$` or
    `` \\` `` in the word, where the outer shell removes it before handing the text on, so the substitution the shell runs
    was read as escaped and no body was lifted.  A `\\$(` inside single quotes in the re-parsed text is the shell's own
    literal, run by nothing, and is not matched; a `\\$` with no `(` is no substitution."""
    i, n, single = 0, len(text), False
    while i < n:
        c = text[i]
        if single:
            single = c != "'"
            i += 1
        elif c == "'":
            single = True
            i += 1
        elif c == "\\" and (text.startswith("$(", i + 1) or text[i + 1 : i + 2] == "`"):
            return True
        elif c == "\\":
            i += 2
        else:
            i += 1
    return False


def escaped_shown(text):
    """The escaped substitution fragment an unread "escaped-subst" finding names: from the first backslash before `$(` or
    a backtick, outside single quotes, cut to SHOWN characters; the whole text where none is found."""
    i, n, single = 0, len(text), False
    while i < n:
        c = text[i]
        if single:
            single = c != "'"
            i += 1
        elif c == "'":
            single = True
            i += 1
        elif c == "\\" and (text.startswith("$(", i + 1) or text[i + 1 : i + 2] == "`"):
            shown = " ".join(text[i:].split())
            return shown if len(shown) <= SHOWN else shown[:SHOWN] + "..."
        elif c == "\\":
            i += 2
        else:
            i += 1
    return unread_shown(text)


def substitution_incomplete(body):
    """Whether a `$( )` body naive paren-counting extracted may have ended early at a `)` that closed a `case` pattern or
    stood in a here-document body, not the substitution (SPD-194): prepare.split_substitutions counts a quoted `)`, a
    `case` pattern's `)` and a `)` in a here-document body alike.  True when the extracted body holds an unterminated
    here-document (its `<<` delimiter never closes in it), or a `case` command in command position with no matching
    `esac`, so the naive extent stopped inside one, not at the substitution's own `)`.  A complete here-document or
    `case` inside a `$( )` closes here and is not incomplete; a quoted `)` leaves an unbalanced quote and is refused as a
    line the hook cannot tokenize (SPD-191), not here."""
    for m in _HEREDOC_OP_RE.finditer(body):
        if not re.search(r"(?m)^[ \t]*" + re.escape(m.group(2)) + r"[ \t]*$", body[m.end():]):
            return True  # a here-document whose delimiter never closes: the extent stopped inside its body
    toks = syntax.shell_tokens(prepare.neutralize_quoted_globs(prepare.newlines_as_separators(body)))
    if toks is None:
        return False  # SPD-191 refuses a body the hook cannot tokenize
    toks = [p for t in toks for p in syntax.operator_parts(t)]
    cases = esacs = 0
    command_position = True
    for t in toks:
        word = prepare.deglob(t)
        if command_position and word == "case":
            cases += 1
        elif word == "esac":
            esacs += 1
        command_position = t in syntax.LIST_TERMINATORS or t in ("&&", "||", "|", "|&", "(", "{", "&")
    return cases > esacs


def process_sub_in(tokens):
    """Whether a run of tokens the walk joins into one word -- a zsh `for`/`foreach` `( ... )` list, or a `name=( ... )`
    array value -- holds a process substitution the walk would otherwise analyse: `<( )`, `>( )` or zsh's `=( )`, whose
    command is never read where the list is joined (SPD-198)."""
    return any(t in ("<(", ">(") or (t == "=" and k + 1 < len(tokens) and tokens[k + 1] == "(")
               for k, t in enumerate(tokens))


def brace_depth_exceeds(text, bound):
    """True when a `${ }` parameter expansion nests deeper than `bound` in `text` (SPD-103), read as the shells parse it:
    single-quoted runs and a backslash-escaped character do not open one, and a `}` closes the innermost `${` still open
    (a `}` with none open, or one closing a `{ }` brace group, closes nothing here).  The scan stops as soon as the bound
    is passed, so a pathological run of openings costs the bound, not the line's length."""
    depth = i = 0
    n = len(text)
    single = False
    while i < n:
        c = text[i]
        if single:
            i += 1
            if c == "'":
                single = False
        elif c == "\\":
            i += 2
        elif c == "'":
            single = True
            i += 1
        elif c == "$" and text.startswith("${", i):
            depth += 1
            if depth > bound:
                return True
            i += 2
        elif c == "}":
            depth -= depth > 0
            i += 1
        else:
            i += 1
    return False
