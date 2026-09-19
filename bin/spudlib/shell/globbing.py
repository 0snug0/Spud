"""shell/globbing: Glob words, qualifiers and their readings.  Moved from bin/spud_ledger.py (SPD-065)."""

import functools
import os
import re

from . import analyse, directories, prepare, redirect_globs, spud_calls, syntax
from ..hooks import hookio


# A word the dispatch reads by name that the shell expands first (SPD-041) is read as each of these it can match: the names a
# command word dispatches on, and every option, verb and argument the dispatch compares a later word with.
GLOB_COMMAND_SAMPLES = frozenset(syntax.WRAPPERS | syntax.SHELLS | syntax.DIRECTORY_COMMANDS | syntax.SHELL_DECLARATIONS | syntax.JS_RUNTIMES
                                 | {"git", "spud", "eval", "source", ".", "trap", "sqlite3", "sqlite", "tee", "python", "python3", "python3.14",
                                    "hash"}  # `hash` shadows a name the hook reads (SPD-062)
                                 | set(syntax.ARG_WRITE_COMMANDS)  # SPD-121: `/bin/c? a b` runs cp
                                 | syntax.TREE_WRITE_COMMANDS)  # SPD-126: `fin? . -delete` runs find
GLOB_SAMPLES = frozenset(
    GLOB_COMMAND_SAMPLES | syntax.GIT_WRITE_VERBS | syntax.GIT_GLOBAL_VALUE_FLAGS | syntax.BRANCH_READ_FLAGS | syntax.BRANCH_READ_VALUE_FLAGS | syntax.TAG_READ_FLAGS
    # CONFIG_READ_SUBCOMMANDS are left out: a glob read as `get` or `list` refuses nothing, so sampling them would only
    # widen an ambiguity refusal (`command -v g?t` reads as git alone) without closing anything.
    | syntax.TAG_READ_VALUE_FLAGS | syntax.CONFIG_READ_FLAGS | syntax.CONFIG_VALUE_FLAGS | syntax.CONFIG_WRITE_FLAGS | syntax.CONFIG_WRITE_SUBCOMMANDS
    | {"stash", "worktree", "remote", "reflog", "branch", "tag", "config", "list", "show", "add", "remove", "rm", "rename", "set-url",
       "set-head", "set-branches", "prune", "update", "expire", "delete"}
    | {o for options in syntax.WRAPPER_VALUE_OPTIONS.values() for o in options}
    # SPD-051: the verbs that carry a program-naming option and the options themselves, so a glob that can expand to one is
    # read as it (`git ls-remote --upload-pac? cmd .` was read only as spelled, and the prefix check never saw --upload-pack).
    | set(syntax.GIT_VERB_PROGRAM_OPTIONS)
    | {o for longs, _ in syntax.GIT_VERB_PROGRAM_OPTIONS.values() for o in longs}
    | {"-" + c for _, shorts in syntax.GIT_VERB_PROGRAM_OPTIONS.values() for c in shorts}
    | {"-c", "-lc", "-ic", "-m", "-", "-X", "-W", "-Q", "-I", "-S", "--as", "--as=spud", "--json", "--help", "-h", "--version"}
    | set(hookio.SPUD_COMMANDS) | {w for pair in hookio.SPUD_ONLY_SUBCOMMANDS + hookio.MEMBER_OWN_COMMANDS for w in pair}
    | syntax.ARG_WRITE_OPTIONS)  # SPD-121: `sed -? '' s/a/b/ f` is `sed -i` when a file named -i is there
GLOB_OPTION = "-%"  # a glob that may start with `-` read as an option that takes no value (SPD-041)
GLOB_WORD_LIMIT = 256  # a longer glob word, or a segment with more than two stars or four groups, is not matched (backtracking)
GLOB_READING_BUDGET = 128  # the readings of one simple command's glob words before the hook stops reading them and refuses a member
_EQUALS_RE = re.compile(r"=([^/=\s]+)\Z")  # zsh's EQUALS: `=name` is the path of the command name
_STAR_RUN_RE = re.compile(r"\*+")


_QUALIFIER_CLOSERS = {"(": ")", "[": "]", "{": "}", "<": ">"}


def trailing_group(word):
    """(start, end) of the group a marked word ends with, when it has no top-level `|`: what zsh with bareglobqual, its
    default, reads as a glob qualifier list (SPD-039, probed: `SPD-001.md(.)` opened the file); else None."""
    if not word.endswith(syntax.ZSH_CLOSE):
        return None
    depth, k = 0, len(word) - 1
    while k >= 0:
        if word[k] == syntax.ZSH_CLOSE:
            depth += 1
        elif word[k] == syntax.ZSH_OPEN:
            depth -= 1
            if depth == 0:
                break
        elif word[k] == syntax.ZSH_BAR and depth == 1:
            return None
        k -= 1
    return (k, len(word)) if k > 0 else None


def qualifier_code(word):
    """The shell code a trailing qualifier list runs in zsh with bareglobqual (probed: `(e:"touch ran":)`, `(oe:...:)`,
    `(e{...})`, `(e[...])` ran their string, `(+f)` ran f): each `e` qualifier's string, delimited by the character after the
    `e` (or its closing bracket), and each name after a `+`.  Read loosely, from every `e` and `+` outside a string already
    taken, so another qualifier's argument is at worst read as one more command."""
    span = trailing_group(word)
    if span is None:
        return []
    text = prepare.deglob(word[span[0] + 1 : span[1] - 1])
    codes, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "e" and i + 1 < n:
            end = text.find(_QUALIFIER_CLOSERS.get(text[i + 1], text[i + 1]), i + 2)
            end = n if end == -1 else end
            codes.append(text[i + 2 : end])
            i = end + 1
            continue
        if text[i] == "+":
            m = _QUALIFIER_NAME_RE.match(text, i + 1)
            if m:
                codes.append(m.group())
                i = m.end()
                continue
        i += 1
    return [code for code in codes if code.strip()]


_QUALIFIER_NAME_RE = re.compile(r"[\w:.-]+")  # the command a `+` qualifier names


def active_glob_word(word):
    """True when the shell expands this masked word before running the command (SPD-041): an unquoted glob character, brace
    list or zsh group or range (GLOB_RE), or zsh's `=name`.  `[` and `[[` are commands, not patterns."""
    if word in ("[", "[["):
        return False
    return syntax.GLOB_RE.search(word) is not None or _EQUALS_RE.match(word) is not None


def literalize(word):
    """The word as the shell passes it when it does not expand it (bash, when a glob matches nothing): its glob characters
    quoted and a leading `=` marked, so it is never expanded again; deglob still restores its text (SPD-041)."""
    word = word.translate(syntax._LITERALIZE)
    return syntax._LITERAL_EQUALS + word[1:] if word.startswith("=") else word


def may_start_with_dash(word):
    """True when a glob word that does not start with `-` may expand to a word that does: a leading `*`, `?` or zsh group, or
    a bracket expression that is negated or holds `-` (SPD-041, probed: `git [-]p push` ran `git -p push` with a file -p)."""
    c = word[:1]
    if c in ("*", "?", syntax.ZSH_OPEN):
        return True
    if c == "[":
        end = word.find("]", 2)
        return end != -1 and (word[1] in "!^" or "-" in word[1:end])
    return False


def glob_too_complex(text):
    """True when a masked glob is too long, or a segment holds too many stars, ranges or groups, to match without the regex
    engine's backtracking running away (SPD-041); the hook then reads it as spelled and refuses a member."""
    if len(text) > GLOB_WORD_LIMIT:
        return True
    for seg in text.split("/"):
        seg = _STAR_RUN_RE.sub("*", seg)
        if seg.count("*") + seg.count(syntax.ZSH_RANGE_OPEN) > 2 or seg.count(syntax.ZSH_OPEN) > 4:
            return True
    return False


@functools.lru_cache(maxsize=512)
def glob_sample_matches(segment, fold):
    """The GLOB_SAMPLES a masked glob segment matches (case-insensitively when `fold`), or None when it is too complex to match."""
    if glob_too_complex(segment):
        return None
    rx = redirect_globs._segment_regex(_STAR_RUN_RE.sub("*", segment))
    if fold:
        rx = re.compile(rx.pattern, re.IGNORECASE)
    return frozenset(s for s in GLOB_SAMPLES if rx.match(s))


def command_path(name):
    """The file PATH finds for a command name, as zsh's `=name` does, or a path named for it when none is found now."""
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        found = os.path.join(entry, name)
        if entry and os.path.isfile(found) and os.access(found, os.X_OK):
            return found
    return "/usr/bin/" + name


def glob_readings(word, a, command=False, script=False, dash=False, shift=False):
    """(readings, ambiguous) for a masked word the shell expands before it runs the command (SPD-041, probed in zsh 5.9 -f, zsh
    -f -o nobareglobqual as the Bash tool runs it, and bash 3.2 with a fake git on a scratch PATH).  Each reading is the list of
    words the word may become:

    - `=name`: as spelled (bash), and the path zsh puts in its place (`=git push` pushed in zsh);
    - a brace list: its words, each read again (`{git,push}` pushed in bash, `git {push,status}` in both);
    - a glob: as spelled (bash runs it when nothing matches, zsh runs nothing), and each name the hook checks that it can match,
      whether or not a file matches now, since the line may create one (`touch push; git p?sh` pushed).  A command word, or a
      script, is matched on its last path segment and case-insensitively (macOS finds GIT as git), and a command word only
      against the names a command dispatches on; any other word against every name, option and verb.  A trailing `(N)` may
      drop the word (`git nomatch(N) push` pushed in zsh with bareglobqual); `dash`: a glob that may start with `-` may be an
      option (GLOB_OPTION); `shift`: a word at a place the command may skip as an option's value is also read with each name
      after it, since a glob matching two files is two words; `script` and a command word with a `/`: each existing file it
      matches that runs the spud launcher, whatever its name (SPD-029).

    Ambiguous: it can match two names the hook checks (both files may exist, and the shell passes both: `* x` ran `git push x`
    with files git and push), it is too complex to match, or its files reach the scan budget."""
    m = _EQUALS_RE.match(word)
    if m:
        return [[literalize(word)], [literalize(command_path(m.group(1)))]], False
    parts, capped = redirect_globs.brace_expand(word)
    if capped:
        return [[literalize(word)]], True
    if parts != [word]:
        return [[p for p in parts if p]], False
    literal = literalize(word)
    readings, names = [[literal]], set()
    ambiguous = glob_too_complex(word)
    for pattern in ([] if ambiguous else redirect_globs.qualifier_readings(word)):
        head, sep, segment = pattern.rpartition("/") if (command or script) else ("", "", pattern)
        matched = glob_sample_matches(segment, command or script)
        if matched is None:
            ambiguous = True
            continue
        names.update(matched)
        readings += [[literalize(head + sep) + s] for s in sorted(matched & GLOB_COMMAND_SAMPLES if command else matched)]
    span = trailing_group(word)
    if span is not None and "N" in word[span[0] :]:
        readings.append([])
    if dash and may_start_with_dash(word):
        readings.append([GLOB_OPTION])
    if (script or (command and "/" in word)) and not ambiguous:
        expansion = redirect_globs.expand_redirect_target(word, a.cwds)
        if expansion is not None:
            matches, capped = expansion
            ambiguous = capped
            readings += [[literalize(path)] for path in matches if spud_calls.spud_launcher(path, None)]
    if shift:
        readings += [[literal] + r for r in readings[1:] if r]
    unique = []
    for r in readings:
        if r not in unique:
            unique.append(r)
    return unique, ambiguous or len(names) >= 2


def resolve_glob(words, i, kind, bodies, a, depth, budget, effect, prefixed, fresh=0):
    """Read words[i], a word the shell expands first, as each reading glob_readings gives (SPD-041).  One reading replaces it in
    place and the caller reads on (False).  Several are each analysed from the start of `words`, with the directories and
    variables after them those of every reading, as ShellWalk merges branches, and the caller stops (True).  An ambiguous word
    is a "glob" finding, which refuses a member; past the budget every glob word left is read as spelled, ambiguous too."""
    if budget[0] <= 0:
        spelled = prepare.deglob(words[i])
        words[i:] = [literalize(w) if active_glob_word(w) else w for w in words[i:]]
        a.kinds.append("glob")
        a.findings.append(("glob", spelled))
        return False
    readings, ambiguous = glob_readings(words[i], a, **kind)
    if len(readings) > budget[0]:
        readings, ambiguous = readings[:1], True
    budget[0] -= len(readings)
    spelled = prepare.deglob(words[i])
    if len(readings) == 1:
        words[i : i + 1] = readings[0]
    else:
        analyse_readings(words, i, readings, bodies, a, depth, budget, effect, prefixed, fresh)
    if ambiguous:
        a.kinds.append("glob")
        a.findings.append(("glob", spelled))
    return len(readings) > 1


def analyse_readings(words, i, readings, bodies, a, depth, budget, effect, prefixed, fresh, expanded=False):
    """Analyse `words` once for each reading of words[i], from the start, with the directories, variables and doubts after them
    those of every reading, as ShellWalk merges branches (SPD-041).  `expanded`: the readings are an expansion's words, so in the
    command word none of the words from it on is an assignment or a reserved word (SPD-043)."""
    cwds, variables, uncertain, doubt = a.cwds, dict(a.vars), a.cd_uncertain, set(a.doubt)
    outcomes = []
    for reading in readings:
        a.cwds, a.vars, a.cd_uncertain, a.doubt = cwds, dict(variables), uncertain, set(doubt)
        spliced = words[:i] + reading + words[i + 1 :]
        analyse.analyse_words(spliced, bodies, a, depth, budget, effect, prefixed, len(spliced) if expanded and i == 0 else fresh)
        outcomes.append((a.cwds, a.vars, a.cd_uncertain, a.doubt))
    a.cwds, a.vars, a.cd_uncertain, a.doubt = outcomes[0]
    for other_cwds, other_vars, other_uncertain, other_doubt in outcomes[1:]:
        a.cwds = directories.union_dirs(a.cwds, other_cwds)
        a.doubt |= other_doubt | (set(a.vars) ^ set(other_vars))  # a variable only some readings assign (SPD-043)
        for name, value in other_vars.items():
            a.vars[name] = value if a.vars.get(name, value) == value else hookio.SUBST  # readings that disagree: a value the hook cannot know
        a.cd_uncertain = a.cd_uncertain or other_uncertain
