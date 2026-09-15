"""render/workedon: The worked-on sentence: markdown blocks to one line.  Moved from bin/spud_ledger.py (SPD-065)."""

import re

from . import teamcard


# Worked on / built (section 3): the first of summary, result and return_text whose text holds a
# paragraph that reads as work, flattened to one line and cut on whole sentences.
MD_COMMENT = re.compile(r"<!--.*?-->", re.S)
MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
MD_HEADING = re.compile(r"^ {0,3}#{1,6}(\s|$)")
MD_RULE = re.compile(r"^ {0,3}([-*_])( *\1){2,} *$")
MD_SETEXT = re.compile(r"^ {0,3}(=+|-+) *$")
MD_ITEM = re.compile(r"^ {0,3}([-+*]|\d{1,9}[.)])(\s|$)")
MD_QUOTE = re.compile(r"^ {0,3}>")
MD_TABLE = re.compile(r"^ {0,3}\|")
MD_CODE = re.compile(r"`[^`]*`")
MD_WIKILINK = re.compile(r"\[\[[^\]]*\]\]")
MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
STOP_LABELS = ("verified", "verification", "tests", "test observations", "left", "next", "status", "handoff", "handoffs",
               "team", "decisions", "open question", "open questions", "proposal", "proposals", "prose for spud",
               "method note", "sources", "evidence", "note", "notes")
BOLD_LABEL = re.compile(r"^(\*\*|__)(?P<label>[^*_]{1,60}?)(?P<p1>[.:])?\1(?P<p2>[.:])?(?:\s*\((?P<paren>[^()]*)\)(?P<p3>[.:]))?\s+")
PLAIN_LABEL = re.compile(r"^(?P<label>[A-Z][A-Za-z]*(?: [A-Za-z]+){0,4}):\s+")
STATUS_START = re.compile(r"(done|accepted|complete|completed|finished|ready|nothing (further|left|else|to do)"
                          r"|no (proposals|open questions|blockers|changes)|all (done|green)|result recorded)\b")
WHERE_START = re.compile(r"((produced|built|changed|written|delivered|committed|pushed)\s+)?(on|in)\s+(the\s+)?(worktree|branch)\b")
HANDLE_START = re.compile(r"spud-\d+/\S+ \(\d+(\.\d+)*, ")


def markdown_blocks(text):
    """[(kind, lines)] over a CommonMark-shaped subset; a blank line ends any block."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = MD_FENCE.match(line)
        if fence:
            close = re.compile(r"^ {0,3}%s{%d,} *$" % (re.escape(fence.group(1)[0]), len(fence.group(1))))
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            out.append(("fence", lines[i : j + 1]))
            i = j + 1
            continue
        if MD_HEADING.match(line):
            out.append(("heading", [line]))
            i += 1
            continue
        if MD_RULE.match(line):  # before list items: `* * *` is a rule
            out.append(("rule", [line]))
            i += 1
            continue
        for kind, rx in (("list", MD_ITEM), ("quote", MD_QUOTE), ("table", MD_TABLE)):
            if rx.match(line):
                j = i + 1
                while j < len(lines) and lines[j].strip() and (kind == "list" or rx.match(lines[j])):
                    j += 1
                out.append((kind, lines[i:j]))
                i = j
                break
        else:
            j, kind = i + 1, "paragraph"
            while j < len(lines):
                following = lines[j]
                if not following.strip() or MD_FENCE.match(following) or MD_HEADING.match(following) or MD_QUOTE.match(following) or MD_TABLE.match(following):
                    break
                if MD_SETEXT.match(following):
                    kind, j = "heading", j + 1
                    break
                if MD_RULE.match(following) or MD_ITEM.match(following):
                    break
                j += 1
            out.append((kind, lines[i:j]))
            i = j
    return out


def paragraph_label(text):
    """(label, the rest) when a paragraph opens with a label, else (None, text).  Bold: up to 60
    characters and 1 to 6 words, a label only when `.` or `:` ends it (inside the markers, after them,
    or after a parenthetical); otherwise it is emphasis.  Plain: a capitalised word, up to four more, a colon."""
    m = BOLD_LABEL.match(text)
    if m and 1 <= len(m.group("label").split()) <= 6 and (m.group("p1") or m.group("p2") or m.group("p3")):
        return m.group("label").strip().rstrip(".:").strip(), text[m.end():]
    m = PLAIN_LABEL.match(text)
    if m:
        return m.group("label"), text[m.end():]
    return None, text


def is_stop_label(label):
    low = label.lower()
    return any(low == stop or low.startswith(stop + " ") for stop in STOP_LABELS)


def is_status_line(text):
    """A paragraph that says how the work went or where it sits, opens with a member handle, introduces
    a list, or has fewer than three words of prose outside code spans and links."""
    test = text.replace("`", "").replace("*", "").strip().lower()
    if STATUS_START.match(test) or WHERE_START.match(test) or HANDLE_START.match(test) or test.endswith(":"):
        return True
    prose = MD_LINK.sub(" ", MD_WIKILINK.sub(" ", MD_CODE.sub(" ", text)))
    return len(re.findall(r"[A-Za-z][A-Za-z'’-]*", prose)) < 3


def protected_spans(text):
    """[start, end) of every code span, wikilink and markdown link."""
    return [(m.start(), m.end()) for rx in (MD_CODE, MD_WIKILINK, MD_LINK) for m in rx.finditer(text)]


def sentence_ends(text):
    protected = protected_spans(text)
    ends = []
    for m in re.finditer(r"[.!?][)\]\"'’”*_]*(?=\s|$)", text):
        if any(a <= m.start() < b for a, b in protected):
            continue
        after = text[m.end():].lstrip()
        if after and after[0].islower():  # `e.g. the`
            continue
        ends.append(m.end())
    return ends


def cut_worked_on(text):
    """At most WORKED_ON_CAP characters: the whole sentences that fit, else a cut inside the first
    sentence at a space outside every span, ended with an ellipsis."""
    if len(text) <= teamcard.WORKED_ON_CAP:
        return text
    fits = [end for end in sentence_ends(text) if end <= teamcard.WORKED_ON_CAP]
    if fits:
        return text[: max(fits)].rstrip()
    protected = protected_spans(text)
    i, moved = teamcard.WORKED_ON_CAP - 1, True
    while moved:
        moved = False
        for a, b in protected:
            if a < i < b:
                i, moved = a, True
    space = text.rfind(" ", 0, i + 1)
    i = space if space > 0 else i
    return text[:i].rstrip(" ,;:—–-") + "…"


def worked_on_text(source):
    """One source through the pipeline: its first paragraph that reads as work, as one line, or None."""
    if not source:
        return None
    text = MD_COMMENT.sub("", source.replace("\r\n", "\n"))
    for kind, lines in markdown_blocks(text):
        if kind != "paragraph":
            continue
        flat = re.sub(r"\s+", " ", " ".join(line.strip() for line in lines)).strip()
        label, rest = paragraph_label(flat)
        if label is not None and is_stop_label(label):
            continue
        rest = rest.strip()
        if not rest or is_status_line(rest):
            continue
        return cut_worked_on(rest.rstrip("\\").rstrip())  # a trailing backslash would be a hard break
    return None


def worked_on(m):
    """What a member worked on and built, for its tree line: its summary, else its Result, else its final message."""
    for column in ("summary", "result", "return_text"):
        text = worked_on_text(m[column])
        if text:
            return text
    return None
