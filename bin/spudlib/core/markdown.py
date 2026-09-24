"""core/markdown: markdown-v0: the YAML subset, documents, log, handoff and report entries."""

import re

from . import kernel


# The headings an import opens a member note's sections at, its layout not known yet: the layout keys, and
# ## Sources, the one section a markdown-v0 member note carried beyond them, where its layout puts it.
IMPORT_MEMBER_SECTIONS = ["Brief", "Log", "Sub-agents", "Ticket proposals", "Result", "Blocked", "Sources", "Outcome"]


# ----------------------------------------------------------------------------
# markdown-v0: the YAML subset, documents, entries
# ----------------------------------------------------------------------------


def yaml_scalar(value):
    value = value.strip()
    if value == "":
        return ""
    if value.startswith('"'):
        if not value.endswith('"') or len(value) < 2:
            raise kernel.SpudError(kernel.EXIT_ERROR, "unterminated quoted value: %s" % value)
        inner = value[1:-1]
        out = []
        i = 0
        while i < len(inner):
            c = inner[i]
            if c == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out)
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise kernel.SpudError(kernel.EXIT_ERROR, "unterminated quoted value: %s" % value)
        return value[1:-1].replace("''", "'")
    if value.startswith("["):
        if not value.endswith("]"):
            raise kernel.SpudError(kernel.EXIT_ERROR, "unterminated list: %s" % value)
        inner = value[1:-1].strip()
        if inner == "":
            return []
        return [yaml_scalar(item) for item in inner.split(",")]
    return value


LIST_ITEM = re.compile(r"(\s*)-(?:\s+(.*))?")


def parse_frontmatter(lines):
    """The YAML subset the ledger reads: `key: value` where the value is plain, "double-" or
    'single-quoted', or a [flow, list]; and a block list, `key:` with no value followed by `- item`
    lines at one indent (none included), the way Obsidian writes lists.  An empty value, "" and ''
    read the same, and every value is the string the ledger stores (01 reads as "01").  Anything
    else is refused rather than guessed at: an indented line that is not an item of the list above
    it (a folded or continued value), a list inside a list, a property given twice."""
    out = {}
    open_key, indent = None, None  # the key a block list may follow, and the indent of its items
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = LIST_ITEM.fullmatch(line)
        if item:
            if open_key is None:
                raise kernel.SpudError(kernel.EXIT_ERROR, "frontmatter list item under no `key:` of its own: %r" % line)
            if indent is None:
                indent, out[open_key] = item.group(1), []
            elif item.group(1) != indent:
                raise kernel.SpudError(kernel.EXIT_ERROR, "frontmatter list item at another indent than the items above it: %r" % line)
            value = yaml_scalar(item.group(2) or "")
            if isinstance(value, list):
                raise kernel.SpudError(kernel.EXIT_ERROR, "frontmatter list inside a list: %r" % line)
            out[open_key].append(value)
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key[0].isspace():
            raise kernel.SpudError(kernel.EXIT_ERROR, "frontmatter line is not `key: value`: %r" % line)
        key = key.strip()
        if key in out:
            raise kernel.SpudError(kernel.EXIT_ERROR, "frontmatter property %r is given twice" % key)
        out[key] = yaml_scalar(value)
        open_key, indent = (key, None) if not value.strip() else (None, None)
    return out


def yaml_quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_list(items):
    rendered = []
    for item in items:
        item = str(item)
        rendered.append(item if re.fullmatch(r"[A-Za-z0-9_./-]+", item) else yaml_quote(item))
    return "[" + ", ".join(rendered) + "]"


def emit_frontmatter(pairs):
    """pairs: [(key, (kind, value))], kind in plain | quoted | list | stamp."""
    lines = ["---"]
    for key, (kind, value) in pairs:
        if kind == "quoted":
            text = yaml_quote(value)
        elif kind == "list":
            text = yaml_list(value)
        elif kind == "stamp":  # plain when set, "" when empty (spawned, finished)
            text = value if value else '""'
        else:
            text = str(value)
        lines.append("%s: %s" % (key, text))
    lines.append("---")
    return "\n".join(lines) + "\n"


def strip_blank_edges(lines):
    lines = list(lines)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def strip_trailing_blank(lines):
    """A section body keeps a leading blank line (the file had it); trailing ones go."""
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def frontmatter_block(text):
    """A note at its frontmatter: (the lines between the --- delimiters, every byte after the closing one)."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise kernel.SpudError(kernel.EXIT_ERROR, "no frontmatter block at byte 0")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise kernel.SpudError(kernel.EXIT_ERROR, "unterminated frontmatter")
    return lines[1:end], "\n".join(lines[end + 1 :])


def restyled_render(raw, renders):
    """Whether a note on disk that matches none of `renders` byte for byte is one of them with only
    its frontmatter written in another YAML style, the way Obsidian rewrites a note it has open:
    the same properties with the same values, in any order (the render puts the order back and
    loses nothing) and any style parse_frontmatter reads, and the same bytes after the closing ---.
    Returns (True, None); (False, None) when a value or the body differs; (False, why) when the
    note cannot be read.  An empty render (a renders row from before the content column) is skipped."""
    try:
        lines, body = frontmatter_block(raw.decode("utf-8"))
        note = (parse_frontmatter(lines), body)
    except UnicodeDecodeError as e:
        return False, str(e)
    except kernel.SpudError as e:
        return False, e.message
    for content in renders:
        if not content:
            continue
        try:
            lines, body = frontmatter_block(content)
            if (parse_frontmatter(lines), body) == note:
                return True, None
        except kernel.SpudError:
            continue
    return False, None


def owned_headings(lines, found, owned):
    """{line index: name}: which of the `## ` lines `found` [(line index, name)] open the sections of a note whose
    layout owns the names `owned`, in that order.  Prose may hold an owned name too, so the first owned line
    opens a section and the rest are the longest run of owned lines in layout order after it; of equally long runs,
    each next heading is one that follows a blank line, as the render writes every heading, then the later one.  Text
    the CLI writes cannot hold an owned heading line (check_prose_headings refuses it).  Hand-written or legacy text
    that does, fenced or not, may open a section there and move text between sections: prose that repeats its own
    section's name after a blank line, a fenced block holding owned headings, or an owned name the note does not carry."""
    cands = [(n, name, owned.index(name)) for n, name in found if name in owned]
    longest = [1] * len(cands)  # the longest run in layout order that starts at each candidate
    for k in range(len(cands) - 2, -1, -1):
        longest[k] += max((longest[j] for j in range(k + 1, len(cands)) if cands[j][2] > cands[k][2]), default=0)
    opens, k = {}, 0
    while cands:
        opens[cands[k][0]] = cands[k][1]
        after = [j for j in range(k + 1, len(cands)) if cands[j][2] > cands[k][2] and longest[j] == longest[k] - 1]
        if not after:
            break
        k = max(after, key=lambda j: (not lines[cands[j][0] - 1].strip(), j))
    return opens


def split_document(text, owned=None):
    """A ledger note: frontmatter, the H1 tail, and (name, body) sections in order.  Given `owned`, the section names
    the note's layout owns in order, only those headings open a section (owned_headings), so prose may hold any other
    `## ` line.  Fences hide no owned heading: the CLI refuses stored prose holding one, and hand-written or
    legacy text that does may move between sections.  Without it, markdown-v0 as written by hand: every `## ` line
    outside fenced code opens one."""
    fm_lines, body = frontmatter_block(text)
    fm = parse_frontmatter(fm_lines)
    rest = body.split("\n")
    i = 0
    while i < len(rest) and (not rest[i].strip() or rest[i].strip() == kernel.MARKER):
        i += 1
    heading = None
    if i < len(rest) and rest[i].startswith("# "):
        heading = rest[i][2:].strip()
        i += 1
    lines = rest[i:]
    found = []
    fence = False
    for n, line in enumerate(lines):
        if owned is None and line.startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            found.append((n, line[3:].strip()))
    opens = dict(found) if owned is None else owned_headings(lines, found, owned)
    sections = []
    name = None
    buf = []
    preamble = []
    for n, line in enumerate(lines):
        if n in opens:
            if name is None:
                preamble = buf
            else:
                sections.append((name, strip_trailing_blank(buf)))
            name = opens[n]
            buf = []
        else:
            buf.append(line)
    if name is None:
        preamble = buf
    else:
        sections.append((name, strip_trailing_blank(buf)))
    return {"frontmatter": fm, "heading": heading, "sections": sections, "preamble": strip_blank_edges(preamble)}


LOG_ENTRY = re.compile(r"^(?:- )?(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)(?:\s*[—–:-])?\s+(.*)$")


def continued_rows(text, opens):
    """A section written one row a line -> [(line, more)]: `line` a row's first line, `more` the lines that continue it.

    The render writes a row's later lines under it with a two-space indent: a Log entry's, a handoff's, a proposal's
    Why and Evidence.  So a line indented two spaces under a line `opens` accepts continues that row, and `more` keeps
    it verbatim, indent and all, for the caller to strip (joined_row) or keep.  An empty line between such a row and
    its next indented line is a blank line of the row, "" in `more` (an editor may have trimmed the render's `  `); any
    other blank line only separates rows.  Every other line is a row of its own, with no `more`.  The one rule the Log
    (parse_log_entries, SPD-236) and the handoffs (bulkimport.handoff_rows, SPD-078) read by."""
    rows = []
    more = None  # the open row's continuation lines; None while no row is open
    blanks = 0  # empty lines since the open row's last line
    for line in text.split("\n"):
        if more is not None and line.startswith("  "):
            more.extend([""] * blanks + [line])
            blanks = 0
        elif not line.strip():
            blanks += more is not None
        else:
            more, blanks = ([] if opens(line) else None), 0
            rows.append((line, [] if more is None else more))
    return rows


def joined_row(first, more):
    """A row's text: its first line's text, then each continuation line with the two-space indent removed."""
    return "\n".join([first] + [line[2:] for line in more])


def parse_log_entries(text):
    """Dated Log lines -> [(at, body)]; the comment and anything before the first dated line is not an entry.

    An entry's lines are read back by continued_rows, as render_log_rows writes them: a line indented two spaces under
    a dated line joins its entry with the indent removed, and so does a markdown-v0 log's written by hand, whose
    continuation lines carry the same indent (SPD-236).  A line the render never writes, unindented under an entry, and
    the lines indented under it, join the entry above as written."""
    entries = []
    for line, more in continued_rows(text, LOG_ENTRY.match):
        m = LOG_ENTRY.match(line)
        if m:
            entries.append([m.group(1), joined_row(m.group(2), more)])
        elif entries:
            entries[-1][1] += "\n" + line
    return [(at, body) for at, body in entries]


HANDOFF_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) — (.+?) → (.+?): (.*)$")


def parse_handoff_line(line):
    m = HANDOFF_LINE.match(line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


REPORT_HEADING = re.compile(r"^## (\d{2}:\d{2}) — (.*)$")


def parse_report_entries(text):
    """reports/YYYY-MM-DD.md -> [(HH:MM, title, body)]."""
    entries = []
    current = None
    buf = []
    for line in text.split("\n"):
        if line.strip() == kernel.MARKER:
            continue
        m = REPORT_HEADING.match(line)
        if m:
            if current:
                entries.append((current[0], current[1], strip_trailing_blank(buf)))
            current = (m.group(1), m.group(2).strip())
            buf = []
        elif current:
            buf.append(line)
    if current:
        entries.append((current[0], current[1], strip_trailing_blank(buf)))
    return entries


def normalize_markdown(text):
    """Formatting-only differences: trailing whitespace and runs of blank lines."""
    out = []
    for line in text.split("\n"):
        line = line.rstrip()
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
