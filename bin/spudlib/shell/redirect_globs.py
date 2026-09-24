"""shell/redirect_globs: The files a redirection glob opens.

Kept whole past the size look-again point (SPD-179): one reading of a zsh or bash glob, from its braces and qualifiers
through each segment's regex (groups, ranges, brackets and their classes) to the bounded scan, where a change to how zsh
reads a pattern lands in one place."""

import os
import re

from . import bash_rule, globbing, prepare, syntax


def _split_brace(body):
    """The alternatives a bare brace group's body expands to (a comma list, or a numeric or single-letter range), or None
    when it is neither (so the braces are literal, as `{1}` and `{a}` are in both shells)."""
    parts, depth, buf = [], 0, []
    for c in body:
        if c == "{":
            depth += 1
            buf.append(c)
        elif c == "}":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
    parts.append("".join(buf))
    if len(parts) > 1:
        return parts
    m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)(?:\.\.-?(\d+))?", body)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        step = int(m.group(3)) if m.group(3) else 1
        step = step or 1
        width = max(len(m.group(1).lstrip("-")), len(m.group(2).lstrip("-"))) if (m.group(1).lstrip("-").startswith("0") or m.group(2).lstrip("-").startswith("0")) else 0
        seq = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
        return [("-" if v < 0 else "") + str(abs(v)).zfill(width) for v in seq]
    m = re.fullmatch(r"([A-Za-z])\.\.([A-Za-z])(?:\.\.-?(\d+))?", body)
    if m:
        lo, hi = ord(m.group(1)), ord(m.group(2))
        step = int(m.group(3)) if m.group(3) else 1
        step = step or 1
        seq = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
        return [chr(v) for v in seq]
    return None


def _expand_one_brace(pattern):
    """Expand the leftmost bare brace list or range in a masked pattern; return the list of patterns (just [pattern] when
    there is none).  A brace with no top-level comma or range is left literal, and scanning goes on past it."""
    depth, start = 0, -1
    for i, c in enumerate(pattern):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                items = _split_brace(pattern[start + 1:i])
                if items is not None:
                    return [pattern[:start] + it + pattern[i + 1:] for it in items]
    return [pattern]


def brace_expand(pattern):
    """Every pattern a masked pattern's bare brace expansions produce, and whether the count reached the match budget
    (zsh writes each; bash calls a multi-word target an ambiguous redirect, so expanding is the safe reading)."""
    done, queue, capped = [], [pattern], False
    while queue:
        p = queue.pop(0)
        expanded = _expand_one_brace(p)
        if expanded == [p]:
            done.append(p)
        else:
            queue = expanded + queue
        if len(done) + len(queue) > syntax.GLOB_MATCH_CAP:
            return done + queue, True
    return done, capped


def _has_bare_glob(seg):
    """True when a masked path segment holds an unquoted `* ? [`, a zsh group or a zsh range (a quoted sentinel is literal)."""
    return any(c in "*?[" or c == syntax.ZSH_OPEN or c == syntax.ZSH_RANGE_OPEN for c in seg)


def _digit_span(a, b):
    """A regex for the digit strings as long as a and b that lie between them (a <= b, equal lengths, zeros kept)."""
    if a == b:
        return a
    if set(a) == {"0"} and set(b) == {"9"}:
        return r"\d{%d}" % len(a)
    if len(a) == 1:
        return "[%s-%s]" % (a, b)
    if a[0] == b[0]:
        return a[0] + "(?:%s)" % _digit_span(a[1:], b[1:])
    rest = len(a) - 1
    parts = [a[0] + "(?:%s)" % _digit_span(a[1:], "9" * rest)]
    if int(b[0]) - int(a[0]) > 1:
        parts.append(r"[%d-%d]\d{%d}" % (int(a[0]) + 1, int(b[0]) - 1, rest))
    parts.append(b[0] + "(?:%s)" % _digit_span("0" * rest, b[1:]))
    return "|".join(parts)


def numeric_range_regex(lo, hi):
    """A regex for the digit strings zsh's `<lo-hi>` matches (probed: `note-<1-1>.md` opened note-001.md, `<->` every
    number): a run of digits whose value lies in the range, leading zeros included, an empty bound open.  A reversed range,
    which zsh matches to nothing, is read as the ordered one, and bounds too long to split as any digits: more files checked."""
    if len(lo) > 18 or len(hi) > 18:
        return r"\d+"
    low, high = int(lo or 0), (int(hi) if hi else None)
    if high is not None and high < low:
        low, high = high, low
    top = len(str(high if high is not None else low))
    spans = []
    for width in range(len(str(low)), top + 1):
        a = max(low, 10 ** (width - 1) if width > 1 else 0)
        b = 10 ** width - 1 if high is None else min(high, 10 ** width - 1)
        if a <= b:
            spans.append(_digit_span(str(a), str(b)))
    if high is None:
        spans.append(r"[1-9]\d{%d,}" % top)
    return "0*(?:%s)" % "|".join(spans)


# The characters a bracket's POSIX class matches, as a regex set's body: the classes zsh 5.9 and bash 3.2 share, and zsh's
# own IDENT, IFS, IFSSPACE and WORD (probed: `/bin/ec[[:alpha:]]o`, `[[:lower:]]`, `[![:digit:]]`, `[[:IDENT:]]` and
# `[[:WORD:]]` ran echo).  A letter class also takes every character past ASCII, and WORD every printable one (its
# WORDCHARS are the shell's to set): more names matched.  A name zsh has no class for matches nothing (`[[:bogus:]]`
# matched no file), as INCOMPLETE and INVALID match no whole character.
_WIDE = "\u0080-\U0010ffff"
_POSIX_CLASSES = {
    "alpha": "a-zA-Z" + _WIDE, "alnum": "a-zA-Z0-9" + _WIDE, "lower": "a-z" + _WIDE, "upper": "A-Z" + _WIDE,
    "digit": "0-9", "xdigit": "0-9A-Fa-f", "blank": " \t", "space": " \t\n\r\f\v", "cntrl": "\x00-\x1f\x7f",
    "punct": re.escape("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"), "graph": "!-~" + _WIDE, "print": " -~" + _WIDE,
    "ascii": "\x00-\x7f", "IDENT": "a-zA-Z0-9_" + _WIDE, "WORD": "!-~" + _WIDE, "IFS": " \t\n\x00", "IFSSPACE": " \t\n",
    "INCOMPLETE": "", "INVALID": "",
}
_NO_NAME = r"(?!)"  # a pattern zsh calls bad, which matches no file and so no name


def _bracket_regex(seg, i):
    """(the regex for the bracket expression opening at seg[i], the index after it), or None when no `]` closes it (a
    literal `[`, as both shells read it, probed).  A `]` first, or first after `!` or `^`, is a member; `[:name:]` is a
    class (_POSIX_CLASSES); a range whose ends are reversed matches nothing (zsh matched no file for `[z-a]`)."""
    n, j = len(seg), i + 1
    negate = j < n and seg[j] in "!^"
    j += negate
    items, first = [], True
    while j < n and (seg[j] != "]" or first):
        first = False
        named = re.match(r"\[:(\w*):\]", seg[j : j + 16]) if seg.startswith("[:", j) else None
        if named:
            items.append(_POSIX_CLASSES.get(named.group(1), ""))
            j += named.end()
            continue
        lo = syntax._SENTINEL_TEXT.get(seg[j], seg[j])
        if j + 2 < n and seg[j + 1] == "-" and seg[j + 2] != "]":
            hi = syntax._SENTINEL_TEXT.get(seg[j + 2], seg[j + 2])
            if lo <= hi:
                items.append(re.escape(lo) + "-" + re.escape(hi))
            j += 3
            continue
        items.append(re.escape(lo))
        j += 1
    if j >= n:
        return None
    body = "".join(items)
    if not body:
        return ("[^/]" if negate else _NO_NAME), j + 1
    return ("[^%s]" if negate else "[%s]") % body, j + 1


def _segment_regex(seg):
    """A regex matching one filename against a masked glob segment: bare `*` `?` `[...]` glob, a zsh group is an alternation
    and a zsh range a number in range, a quoted sentinel or any other character is literal.  An unbalanced `[` is
    a literal bracket, as the shells read it (probed).  What zsh calls a bad pattern matches no name, as zsh opens no file
    and runs no command for it (probed in 5.9, -f and -f -o nobareglobqual): a group left open or closed twice, or holding
    a `/` outside a bracket (`time(ls /tmp)`, `e(1|/)`; `(e|[/])cho` is read, its bracket matching no name).  None when
    the regex engine still cannot compile it (nesting too deep): what it matches is more than the reader can say."""
    out, i, n, depth = [], 0, len(seg), 0
    while i < n:
        c = seg[i]
        if c in syntax._GLOB_UNSENTINEL:
            out.append(re.escape(syntax._GLOB_UNSENTINEL[c]))
            i += 1
        elif c == syntax.ZSH_OPEN:
            depth += 1
            out.append("(?:")
            i += 1
        elif c == syntax.ZSH_CLOSE:
            if not depth:
                return re.compile(_NO_NAME)
            depth -= 1
            out.append(")")
            i += 1
        elif c == syntax.ZSH_BAR:
            out.append("|")
            i += 1
        elif c == "/" and depth:
            return re.compile(_NO_NAME)
        elif c == syntax.ZSH_RANGE_OPEN and syntax.ZSH_RANGE_CLOSE in seg[i:]:
            end = seg.index(syntax.ZSH_RANGE_CLOSE, i)
            lo, _, hi = seg[i + 1 : end].partition("-")
            out.append("(?:%s)" % numeric_range_regex(lo, hi))
            i = end + 1
        elif c in syntax._SENTINEL_TEXT:  # a blank inside a group, or a stray range bracket
            out.append(re.escape(syntax._SENTINEL_TEXT[c]))
            i += 1
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            found = _bracket_regex(seg, i)
            if found is None:
                out.append(re.escape("["))
                i += 1
            else:
                out.append(found[0])
                i = found[1]
        else:
            out.append(re.escape(c))
            i += 1
    if depth:
        return re.compile(_NO_NAME)
    try:
        return re.compile("(?:" + "".join(out) + r")\Z")
    except (re.error, RecursionError, OverflowError):  # nesting too deep to compile
        return None


def path_segments(pattern):
    """A masked pattern's path segments, split where zsh splits it: at each `/` outside every group, a bracket's own
    included (`d1[/]d2/f` matched nothing with d1/d2/f present), and inside a group at none, since a `/` in a group's
    bracket is the bracket's (`echo x > d1/d2/(f|[/])` wrote d1/d2/f); None when a group holds a `/` outside a bracket, a
    bad pattern zsh opens nothing for (probed)."""
    if syntax.ZSH_OPEN not in pattern and syntax.ZSH_CLOSE not in pattern:
        return pattern.split("/")
    parts, depth, start, i, n = [], 0, 0, 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == syntax.ZSH_OPEN:
            depth += 1
        elif c == syntax.ZSH_CLOSE:
            depth -= 1
        elif c == "[" and depth > 0:
            found = _bracket_regex(pattern, i)
            if found is not None:
                i = found[1]
                continue
        elif c == "/":
            if depth > 0:
                return None
            parts.append(pattern[start:i])
            start = i + 1
        i += 1
    parts.append(pattern[start:])
    return parts


def bounded_glob(pattern):
    """The existing files a masked absolute glob pattern names, and whether the scan budget was reached.  `**` matches
    directories recursively; the scan is bounded by GLOB_SCAN_CAP entries and GLOB_MATCH_CAP matches so a recursive glob
    never walks a large tree without limit, and stops reporting the bound was hit instead.  A zsh group holding a
    `/` outside a bracket, or one left open, is a bad pattern zsh opens nothing for (probed; path_segments).  A segment
    the reader cannot compile (_segment_regex) is read as every name there, and the bound as reached."""
    depth = 0
    for c in pattern:
        if c == syntax.ZSH_OPEN:
            depth += 1
        elif c == syntax.ZSH_CLOSE:
            depth -= 1
            if depth < 0:
                return [], False
    segments = path_segments(pattern)
    if depth or segments is None:
        return [], False
    parts = [p for p in segments if p != ""]
    frontier = {"/" if pattern.startswith("/") else os.getcwd()}
    scanned, capped, unreadable = 0, False, False
    for idx, part in enumerate(parts):
        if capped:
            break
        last = idx == len(parts) - 1
        if not _has_bare_glob(part) and part != "**":
            lit = prepare.deglob(part)
            frontier = {os.path.join(d, lit) for d in frontier if last or os.path.isdir(os.path.join(d, lit))}
            continue
        if part == "**":
            seen, stack = set(), list(frontier)
            while stack and not capped:
                d = stack.pop()
                if d in seen:
                    continue
                seen.add(d)
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            scanned += 1
                            if scanned > syntax.GLOB_SCAN_CAP:
                                capped = True
                                break
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                except OSError:
                    pass
            frontier = seen
            continue
        rx = _segment_regex(part)
        if rx is None:
            rx, unreadable = re.compile(r"[^/]*\Z"), True
        nf = set()
        for d in frontier:
            if capped:
                break
            try:
                with os.scandir(d) as it:
                    for e in it:
                        scanned += 1
                        if scanned > syntax.GLOB_SCAN_CAP:
                            capped = True
                            break
                        if rx.match(e.name) and (last or e.is_dir(follow_symlinks=False)):
                            nf.add(e.path)
            except OSError:
                pass
        frontier = nf
        if len(frontier) > syntax.GLOB_MATCH_CAP:
            capped = True
    matches = sorted(m for m in frontier if os.path.lexists(m))
    if len(matches) > syntax.GLOB_MATCH_CAP:
        return matches[:syntax.GLOB_MATCH_CAP], True
    return matches, capped or unreadable


def expand_redirect_target(target, cwds):
    """Every existing file the masked glob `target` opens from each candidate directory, and whether the scan budget was
    reached; None when the directory is unknown (the caller cannot follow it)."""
    bases = bash_rule.redirection_paths(target, cwds)
    if bases is None:
        return None
    bases = [os.path.expanduser(b) if b.startswith("~") else b for b in bases]
    matches, capped = set(), False
    for base in bases:
        patterns, c = brace_expand(base)
        capped = capped or c
        for pat in patterns:
            for reading in qualifier_readings(pat):
                m, c2 = bounded_glob(reading)
                matches.update(m)
                capped = capped or c2
                if len(matches) > syntax.GLOB_MATCH_CAP:
                    return sorted(matches)[:syntax.GLOB_MATCH_CAP], True
    return sorted(matches), capped


def qualifier_readings(pattern):
    """The patterns a marked pattern stands for: with a trailing group that has no `|`, zsh's default bareglobqual reads glob
    qualifiers, which only narrow the files the rest matches (`(N)` also drops a word matching nothing), and nobareglobqual
    (this Mac's Bash tool) reads a group (probed: `note.md(.)` opened the file only with bareglobqual); both count."""
    span = globbing.trailing_group(pattern)
    return [pattern] if span is None else [pattern[: span[0]], pattern]
