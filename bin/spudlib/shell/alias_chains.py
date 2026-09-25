"""shell/alias_chains: the text zsh parses for a command word an alias stands in -- the alias's body, the names in flight
marked, and the member's words after it, chained after a body ending in a blank.

Taken out of shell/line_aliases past its 1000-line band (SPD-314): alias_texts, for a line's alias
(analyse.read_alias_body, SPD-317) and the snapshot's (line_aliases.shell_aliased, SPD-313), the chain (SPD-310) read
in the line's table and the snapshot's (_chain_bodies) and capped (CHAIN_STEPS, CHAIN_READINGS); the words after a body
that ends in no blank, as the reading parses them again (alias_rest, alias_requoted, SPD-201); and a body's own words
that name an alias in flight, written quoted (_held_in_flight, which analyse reads too).  It imports nothing of the
shell's reading but alias_spellings, alias_views, prepare and syntax, so the import cycle's modules reach it and it
reaches none of them."""

import re

from . import alias_spellings, alias_views, prepare, syntax
from ..hooks import snapshots


def alias_rest(words, a):
    """The text the command's words after an alias's expansion stand for where the reading parses them again
    (alias_requoted, SPD-201), joined, where zsh looks none of them up as an alias; alias_texts after a body ending in
    a blank, where it does."""
    return " ".join(alias_requoted(w, a) for w in words)


def alias_requoted(word, a):
    """prepare.requoted for a word the line already read where eval's text is read again with an alias body set before it
    (an alias's, analyse.dispatch_words; a snapshot alias's, line_aliases.shell_aliased): a word that spells a global
    alias of the table the text was parsed with (alias_views.parsed_table, SPD-286) -- the line's own or the shell's
    (line_aliases.held_aliases) -- was quoted where it was read, or it would have been expanded then, so it is escaped,
    which keeps it from being expanded a second time (SPD-109, SPD-283)."""
    text = prepare.requoted(word)
    return "\\" + text if alias_views.GLOBAL_ALIAS + prepare.deglob(word) in alias_views.parsed_table(a) else text


# SPD-310: zsh looks the word after an alias whose body ends in a blank up as an alias too, wherever that word stands,
# so a wrapper at the head of the body, which takes the command position away from the words after it, does not end the
# chain (probed in zsh 5.9 -f and -f -o nobareglobqual, tests/probes/shell_probe.py, with `alias n='nice '`, `alias
# e='echo '` and `alias g2='echo G2-RAN'`: `eval 'n g2'` ran G2-RAN, and so did `n n g2`, `n m g2` with m another `nice
# `, and `e y g2` with y='e', the chain going on from the last alias the body expanded).  zsh reads the body as it reads
# text, so the body's own first word stands where the word did and is looked up too, and the chain runs on inside the
# body (`e z g2` with z='e g2' printed `echo G2-RAN g2`: z's g2 ran its alias, the g2 after z, behind a body with no
# blank at its end, did not); a name is not looked up inside its own expansion (`e k g2` with k='e k' printed `k g2`),
# and is again once its body is read (`n n g2`).  A quoted word, one holding an expansion, or one that is no alias ends
# the chain, and a suffix alias is never looked up there (`n a.txt` found no file a.txt).  The hook read such a body and
# the words after it as one text, whose wrapper left the next word a plain command (`alias s='sudo '; alias gp='git
# push'; eval 's gp'` pushed unread); the chain is now spelled into the text before it is read, as zsh parses it.
#
# SPD-313: zsh looks each of them up in the one table it holds, the snapshot's aliases and the line's (probed as above,
# the snapshot sourced: with its `s='nice '`, `ws='s'` and `u='gp'`, `alias lg=...; eval 's lg'` ran the line's lg, `alias
# ln2='nice '; eval 'ln2 gp'`, `s u` and `ws ws gp` its gp, and `alias gp=...; eval 's gp'` the line's gp where the
# line's own `s gp` ran the snapshot's: tests/test_hooks_snapshots.py MixedAliasChainTest).  The hook chained the line's
# aliases into the line's and the snapshot's into the snapshot's, never looking a chained body's first word up, so `s u`
# was read as `sudo gp`, a program; one walk now reads every word of a chain in the table zsh holds (_chain_bodies).
_CHAIN_WORD_RE = re.compile(r"([ \t]*)([^\s;&|<>()'\"\\$`\x00]+)(?=[\s;&|<>()]|\Z)")
CHAIN_STEPS = 64  # the most aliases one chain expands, past which the word is refused a member as one the hook cannot read
CHAIN_READINGS = 16  # ... and the most texts one word's chain is read as, bash's and sh's readings among them
_BLANK_END = (" ", "\t")


def alias_texts(name, body, words, a, notes):
    """[(a text zsh parses for a command word `name` an alias puts `body` in place of, the names in flight at its first
    word)] -- a line's alias (analyse.read_alias_body, SPD-317) or the snapshot's (line_aliases.shell_aliased, SPD-313):
    the body with the names in flight written quoted where it spells them (_held_in_flight), its own first word looked
    up too, then, where the body so read ends in a blank, the member's `words` after it with the chain spelled in
    (above), else those words as the reading parses them again (alias_rest).  Each word the chain reaches, where the
    text being read writes it unquoted, is replaced by what the table holds for it (_chain_at), and where it writes it
    quoted too, left quoted, which ends the chain (alias_spellings.spellings, SPD-308, probed in zsh 5.9: after `alias
    v='V=1 '`, `v 'ls' -d /` ran the real ls), an empty quoted string before it keeping the reading from expanding it.
    No name is in flight at the member's words but those ShellAnalysis.expanding holds: zsh looks them up once the body
    is read (SPD-317, _held_in_flight).  Each snapshot alias the chain expands is appended to `notes`, as
    ShellAnalysis.shell_expanded names it."""
    flight, steps, tails, out = tuple(a.expanding) + (name,), [CHAIN_STEPS], None, []
    for text, heads in _chain_at(_held_in_flight(body, flight), a, flight, notes, steps):
        chained = text.endswith(_BLANK_END)
        if chained and tails is None:
            tails = _chain_words(words, a, notes, steps)
        for rest in tails if chained else [alias_rest(words, a)]:
            out.append((text + (" " + rest if rest else ""), (name, *heads)))
    return _capped(out, name, a)


# SPD-317: zsh looks no name up as an alias while the text of its own expansion is read -- the body's first word and
# every other word of it -- and looks it up again once that text is read, so the words after the body are aliases again.
# Probed in zsh 5.9 -f and -f -o nobareglobqual through tests/probes/shell_probe.py: `alias ls='ls -d'; eval 'ls /'`
# printed `/`; `alias ls='ls -d; ls -d /'; eval 'ls /tmp'` printed `.`, `/`, `/tmp`, and so did `&&` for the `;`; with
# `function m` and `alias m='echo M; m'`, `eval 'm q'` ran the function; with `function a`, `alias a=b b='a x'` ran it
# for `a y`, a name staying in flight while a body its own reached is read; `alias e='echo E;'; eval 'e e'` printed E
# twice.  The hook read a line alias's body again as text with no name in flight, so `alias ls='ls -G'; eval ls`
# expanded ls at every level to the reading depth, refused a member.  A name held in ShellAnalysis.expanding would stand
# for the whole text read, the member's words after the body too (SPD-316's shape); the body's own words are marked
# instead, the flight thus scoped to exactly them.  An empty quoted string before a word changes no value it has in any
# shell, and every reading takes the word so written for a quoted one, which no plain or global alias expands
# (alias_spellings.spellings, SPD-295), while a function or the command it names still runs.  A reserved word so written
# would no longer be one, and a here-document the body holds would have its text changed: there nothing is marked, and a
# name the body spells again is expanded again to the depth, refused a member, more than runs and never less.  So is a
# word inside a `$( )` or a `<( )` of the body, which zsh parses when it runs it, the alias no longer in flight
# (alias_spellings.alias_words skips them).
def _held_in_flight(body, flight):
    """`body` with each word zsh reads unquoted and plain (alias_spellings.alias_words) that names an alias in `flight`
    written after an empty quoted string, which keeps every later reading from expanding it (above)."""
    if "<<" in body or not any(name in body for name in flight):
        return body
    out, at = [], 0
    for start, end, plain in alias_spellings.alias_words(body):
        word = body[start:end]
        if plain and word in flight and word not in syntax.RESERVED_WORDS and word not in syntax.ZSH_RESERVED_WORDS:
            out.append(body[at:start] + "''")
            at = start
    return "".join(out) + body[at:]


def _chain_words(words, a, notes, steps):
    """The texts zsh parses for the member's `words` after an alias body ending in a blank (alias_texts), from words[0]
    on."""
    rest = [alias_requoted(w, a) for w in words]
    if not rest:
        return [""]
    written, tails, out = alias_spellings.spellings(a, words[0]), None, []
    if written & alias_spellings.UNQUOTED:
        for head, _ in _chain_at(rest[0], a, tuple(a.expanding), notes, steps):
            if head.endswith(_BLANK_END):  # a body ending in a blank: the next word is looked up too
                tails = _chain_words(words[1:], a, notes, steps) if tails is None else tails
                out.extend(head + tail for tail in tails)
            else:
                out.append(" ".join([head] + rest[1:]))
    if written != alias_spellings.UNQUOTED:
        out.append(" ".join(["''" + rest[0]] + rest[1:]))
    return _capped(out, prepare.deglob(words[0]), a)


def _chain_at(text, a, flight, notes, steps):
    """[(a text zsh parses for `text`, the names in flight at its first word past `flight`)], `text`'s first word standing
    where an alias is looked up, with the chain spelled in: that word, where it is one plain word, replaced by each body
    the table holds for it (_chain_bodies), that body read the same way with the name in flight, which zsh does not look
    up again (`flight`) there or anywhere else in the body (_held_in_flight, SPD-317), and, where the body so read ends in
    a blank, the text after the word too; `steps`, the expansions left (CHAIN_STEPS), past which the word is refused a
    member and read as it is written."""
    m = _CHAIN_WORD_RE.match(text)
    name = m.group(2) if m else None
    if name is None or name.startswith("#") or name in flight:
        return [(text, ())]
    bodies = _chain_bodies(name, a, notes)
    steps[0] -= sum(b is not None for b in bodies)
    if steps[0] < 0:
        alias_views.note_alias_doubt(a, ("alias", name))
        return [(text, ())]
    lead, after, tails, out = m.group(1), text[m.end() :], None, []
    for body in bodies:
        if body is None:
            out.append((text, ()))
            continue
        for expanded, heads in _chain_at(_held_in_flight(body, flight + (name,)), a, flight + (name,), notes, steps):
            if expanded.endswith(_BLANK_END):
                tails = [t for t, _ in _chain_at(after, a, flight, notes, steps)] if tails is None else tails
                out.extend((lead + expanded + tail, (name,) + heads) for tail in tails)
            else:
                out.append((lead + expanded + after, (name,) + heads))
    return _capped(out, name, a)


def _capped(out, name, a):
    """At most CHAIN_READINGS of one word's readings -- several lines read a line at a time, each word written both ways,
    multiplying -- past which the word is refused a member as one the hook cannot read."""
    if len(out) > CHAIN_READINGS:
        alias_views.note_alias_doubt(a, ("alias", name))
        del out[CHAIN_READINGS:]
    return out


def _chain_bodies(name, a, notes):
    """The bodies zsh's table puts in place of `name` along a chain (SPD-313), None for the word as written: in text
    parsed as the line runs, the line's own entry as that text was parsed (alias_views.parsed_view: one it defined or
    cleared, the snapshot's among them, SPD-299), else the snapshot's where its aliases stand (_held_body); where bash
    and sh read the text a line at a time (line_aliases.line_reading), the table as it stands too.  A name the line's
    table doubts, or may hold under a name the hook cannot read, is refused a member (the "alias" finding) and read all
    the same."""
    view = alias_views.parsed_view(a)
    tables = [] if view is None else [(view.table, name in view.doubted, view.unknown)]
    if view is not None and view.lines:
        tables.append((a.aliases, alias_views.alias_doubted(a, name), a.alias_unknown))
    held = _held_body(name, a, notes) if not tables or any(name not in t for t, _, _ in tables) else None
    bodies, doubtful = ([held] if not tables else []), False
    for table, doubted, unknown in tables:
        body = table[name] if name in table else held
        doubtful = doubtful or (doubted if name in table else unknown)
        if body not in bodies:
            bodies.append(body)
    if doubtful:
        alias_views.note_alias_doubt(a, ("alias", name))
    return bodies


def _held_body(name, a, notes):
    """The snapshot's plain alias body for `name` where its aliases stand (alias_views.held_standing, SPD-290), named in
    `notes`; None where it holds none, or one the hook cannot read, refused a member as a command word's is
    ("shell-alias")."""
    held = snapshots.shell_table(a.home).aliases
    if name not in held or not alias_views.held_standing(a):
        return None
    body = held[name]
    if body is None:
        alias_views.note_alias_doubt(a, ("shell-alias", name))
    else:
        notes.append((name, alias_views.held_shown("an alias", body)))
    return body
