"""shell/held_shadows: A call of Claude Code's own grep, find, rg or pkill, recorded without reading its body.

A module of its own since SPD-267, taken out of shell/held_text: held_text.read_shell_name asks hooks/snapshots whether a
body is byte for byte one of the harness's shadows (the texts stay there), sets the call's words in it with shadow_text,
and has read_shadow record what held_text.analyse_shell_text's full reading would, measured on that text (SPD-247).  It
joins the shell reading's import cycle, every read of analyse inside a function body."""

from . import analyse, expansions, find_xargs, git_programs, globbing, prepare, script_files, spud_calls, syntax, walk
from ..hooks import hookio, snapshots


# What the full reading of each of Claude Code's own shadows (hooks/snapshots.HARNESS_SHADOWS) leaves in the analysis where
# read_shadow's conditions hold, measured on its text (SPD-247): one "other" kind for each of the `others` commands its body
# runs; the names it assigns, in order (`assigned`); the values it leaves the line's shell (`values`) -- ARGV0, which a
# command prefix assigns and no `local` scopes, and pkill's names, which its `local` inside an `if` does not make surely
# local (SPD-246); the names it leaves doubted, sticky and the line's (`doubted`, `sticky`, `line_assigned`); the files it
# redirects to (`redirects`); and for a body whose `for _cc_a` loop walks the call's words, the names those words fill as the
# member's (`fills`, SPD-205) -- pkill's `_cc_probe`, which its loop appends `$_cc_a` to, among them.  `names` are the
# variables its text reads or assigns, IFS among them for the "$_cc_bin" it resolves (expansions.variable_readings): a line
# whose own state holds one of them is read in full.  Every body but pkill's runs the claude binary three times, a
# "script" finding each (script_files.read_path_word), the one finding the prune of held_text.analyse_shell_text keeps.
# Plain data, so importing the module runs nothing of it (tests/suite_deps.py counts a function run at import as every
# importer's).
_RUNS_CLAUDE_NAMES = ("IFS", "_cc_bin", "CLAUDE_CODE_EXECPATH", "ZSH_VERSION", "OSTYPE", "ARGV0")
_RUNS_CLAUDE_ASSIGNED = ("_cc_bin", "_cc_bin", "ARGV0", "ARGV0")
_SHADOW_READINGS = {
    "find": {"others": 12, "assigned": _RUNS_CLAUDE_ASSIGNED, "values": (("ARGV0", "bfs"),), "doubted": ("ARGV0",),
             "names": _RUNS_CLAUDE_NAMES, "sticky": (), "line_assigned": ("ARGV0",), "redirects": (), "fills": None},
    "rg": {"others": 12, "assigned": _RUNS_CLAUDE_ASSIGNED, "values": (("ARGV0", "rg"),), "doubted": ("ARGV0",),
           "names": _RUNS_CLAUDE_NAMES, "sticky": (), "line_assigned": ("ARGV0",), "redirects": (), "fills": None},
    "grep": {"others": 15, "assigned": _RUNS_CLAUDE_ASSIGNED, "values": (("ARGV0", "ugrep"),), "doubted": ("ARGV0", "in"),
             "names": _RUNS_CLAUDE_NAMES + ("_cc_a",), "sticky": (), "line_assigned": ("ARGV0",), "redirects": (),
             "fills": ("_cc_a",)},
    "pkill": {"others": 11, "assigned": ("_cc_skip", "_cc_probe", "_cc_skip", "_cc_skip", "_cc_probe", "_cc_probe"),
              "values": (("_cc_probe", hookio.SUBST), ("_cc_skip", "1")), "doubted": ("_cc_skip", "_cc_a", "_cc_probe", "a", "in"),
              "names": ("IFS", "CLAUDE_PID", "_cc_skip", "_cc_a", "_cc_probe"), "sticky": ("_cc_probe", "_cc_skip"),
              "line_assigned": (), "redirects": ("/dev/null",), "fills": ("_cc_a", "_cc_probe")},
}
# The words the shadows' bodies look up as an alias or a function the shell holds (held_text.read_shell_name), beside the
# claude binary
_SHADOW_LOOKUPS = ("local", "[[", "[", "command", "return", "exec", "printf", "continue")
_FIND_OTHER_STARTS = ("-f", "-files0-from")  # find_xargs.read_find's starting points from a word of their own


def shadow_text(body, words):
    """The body of one of the harness's shadows with the call's words set where it reads them, as shell/positional sets
    them: each of its references is `${1+"$@"}` standing unquoted between blanks, which is each word whole, quoted again as
    the line spelled it (prepare.requoted), and nothing for no words (positional's probe)."""
    return body.replace('${1+"$@"}', " ".join(prepare.requoted(w) for w in words))


def read_shadow(a, name, claude, words, depth):
    """Record for a call of one of Claude Code's own shadows (SPD-247) exactly what held_text.analyse_shell_text's reading of
    its body records, without reading it -- True -- or record nothing and answer False, for the caller to read the body in full.

    The text is byte for byte the harness's (hooks/snapshots.harness_shadow), so what the full reading records is fixed
    (_SHADOW_READINGS) wherever nothing the body's commands read differs from where it was measured: the call is read on the
    line itself, outside every alias's, function's, eval's and loop's text; the line defined no function, alias or hash and
    set no PATH, option or code the hook does not read (`source`, `trap`) that the body's lookups or its expansions read;
    no reading past the depth bound; the profile holds none of the words the body looks up; the claude binary is no spud
    launcher; and the line's variables hold none of the body's names (the record's `names`) but those a shadow before it
    left: ARGV0 and grep's `_cc_a` among the dashless loops.  The call's words reach the body only where the full
    reading records them in that one way: each is literal (no substitution, glob, operand the line does not spell, or
    reference to a variable or parameter), none names a variable of the body, and none is a primary find acts on
    (find_xargs.read_find records nothing then).  A for loop over them (grep's, pkill's) doubts every name they spell
    (walk.ShellWalk.finish), keeps `_cc_a` dashless while no word may start with `-` (loop_header_word), and fills the
    member's names where a word, its sentinels taken off, is one of the call's own (fill_loop).

    Proved against the full reading, field by field of the analysis and the hook's answer, in tests/test_hooks_snapshots.py
    HarnessShadowReadingTest: a change to the reader that moves what a body records fails there."""
    record = _SHADOW_READINGS[name]
    if a.shell_reading or a.alias_scope or a.loop_depth or a.func_depth or a.loop_words or a.loop_derived or a.all_doubt:
        return False
    if a.functions or a.function_bodies or a.hashed or a.aliases or a.alias_unknown or a.cdable or a.chase or a.arith_opaque:
        return False
    if depth + 1 > analyse.READING_DEPTH or git_programs.path_in_force(a.vars) is not None:
        return False
    spelled = []
    for w in words:
        plain = prepare.deglob(w)
        if expansions.expansion_word(w) or globbing.active_glob_word(w) or syntax.unknown_operand(w) or "`" in plain \
                or syntax.POSITIONAL_RE.search(plain) or syntax.READ_NAME_RE.search(plain):
            return False  # what expands, or what fill_from reads as a name or a parameter once its quoting is gone
        if name == "find" and (plain in find_xargs.EXEC_PRIMARIES or plain in find_xargs.DELETE_PRIMARIES
                               or plain in find_xargs.FILE_PRIMARIES or plain in _FIND_OTHER_STARTS):
            return False
        spelled.extend(syntax._NAME_RE.findall(plain))
    names = record["names"]
    if any(n in names for n in spelled):
        return False
    argv0 = ("ARGV0",) if claude else ()
    for held, left in ((a.vars, argv0), (a.doubt, argv0), (a.line_assigned, argv0), (a.dashless_loops, ("_cc_a",)),
                       (a.sticky, ()), (a.typed, ()), (a.derived, ()), (a.unseen_assigned, ()), (a.line_members, ())):
        if any(n in held and n not in left for n in names):
            return False
    table = snapshots.shell_table(a.home)
    if any(w in table.aliases or w in table.functions for w in _SHADOW_LOOKUPS + ((claude,) if claude else ())):
        return False
    if claude and spud_calls.any_spud_launcher(claude, a.cwds):
        return False
    # held_text.analyse_shell_text's outermost reading: its state for the member's words, reset where it ends
    a.member_vars = set(a.line_members)
    a.line_filled, a.filled_texts, a.shell_words, a.shell_kept, a.shell_line_vars = set(), set(), [], set(), frozenset()
    a.cd_uncertain = False  # analyse.isolated
    a.kinds.extend(["other"] * record["others"])
    if claude:
        for _ in range(3):
            script_files.read_path_word(a, claude)
    a.assigned.extend(record["assigned"])
    a.redirects.extend((target, a.cwds) for target in record["redirects"])
    a.vars.update(record["values"])
    a.doubt.update(record["doubted"])
    a.sticky.update(record["sticky"])
    a.line_assigned.update(record["line_assigned"])
    if record["fills"] is not None:
        a.doubt.update(spelled)
        if any(walk._value_may_start_with_dash(w) for w in words):
            a.dashless_loops.discard("_cc_a")
        else:
            a.dashless_loops.add("_cc_a")
        if any(prepare.deglob(w) in words for w in words):
            a.member_vars.update(record["fills"])
    return True
