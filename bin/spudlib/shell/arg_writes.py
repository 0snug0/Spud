"""shell/arg_writes: the files a command names as operands and writes -- cp, mv, ln, install, mkdir, touch, rm, rmdir,
truncate, chmod and its kin, sed in place -- read for bash_reason to hold to the path rule as a redirection target.

The Bash hook once held a write to the path rule only when it was a redirection, a tee operand or one of git's own write
options, so `cp x tests/fake/.git/hooks/post-index-change` and `cp x ~/.zshrc` were never read.  Spud's decision: a
write by argument is a write by redirection.  read_writes records what a command's words name (analyse_words calls it
wherever tee is read: behind every wrapper, in a pipeline, a subshell, eval, a function body), and written_paths turns each
record into the masked words bash_reason's targets_reason checks, a directory destination read against the filesystem as
a redirection glob is.  The grammar is syntax.ARG_WRITE_COMMANDS, this Mac's BSD one (probed in a scratch directory):
options end at `--` or the first operand, so `mkdir new -p` made ./-p; a cluster's first letter that takes a value takes
the rest of the word or the next one, so `sed -i -e X f` backed f up to f-e.  `resolved` puts the line's own value in a
file the command names (`S=<scratchpad>; mkdir -p $S/base`), which the differential over 3477 commands spudagents ran
showed is how a member writes in its scratchpad; it is the one reading of every write target the hook
checks -- a redirection's, a tee operand's and a git call's own write option's alike, resolved where each is recorded.

Each recorded write also carries a directory kind, which the command decides and only the path rule reads: mkdir's
operands and `install -d`'s without -m, -o or -g only make a directory, rmdir's and rm -d's only remove one, and a member's own deliverable glob
covers the directory it names without matching it; rm -r's, chmod -R's and mv's reach the whole subtree
under a directory operand (bash_rule.path_directories).  Every other write of the same path, a destination directory's
contents and a backup included, is a file and keeps the reading a redirection target has.

A module of its own because it is one reading with its own users, the analysis and bash_reason, and its own table.  It
reads here what the operands carry beyond themselves: rm -r's whole subtree ("rm-tree"), chmod -R's and mv's ("file-tree"),
each directory a recursive copy or a move lands whole, walked for a git directory (written_paths), and an operand the line
does not spell -- xargs's input, find's `{}` -- read as options too where getopt would read them (options_unknown).  What
find and xargs do themselves is shell/find_xargs's, the commands that write a whole tree shell/tree_writes'.

Past 250 lines (the look-again point) it stays whole: the two halves are one grammar read twice -- the scan that says
which words a command writes, and the paths those words name once the filesystem says whether a destination is a directory
-- and a seam between them would put the table's users in two files with nothing else to tell them apart.  No definition
here is long; the length is the grammar's, one short function per shape."""

import os
import re

from . import bash_rule, expansions, globbing, prepare, redirect_globs, syntax, tree_walk
from ..hooks import hookio


# chmod's own options (the man page's synopsis: -fhv, -R with -H/-L/-P, and the ACL forms -E, -C, -N, -i, -I): a word
# spelled with `-` and other letters is a mode (`-x`, `-w`), which ends the options as BSD chmod's getopt loop ends them.
CHMOD_OPTIONS = frozenset("fhvRHLPCEINi")
CHMOD_NO_MODE = frozenset("CEINi")  # the forms that take no mode operand: every operand is a file
# chmod's ACL modes and how many words follow each before the files: an entry (`+a`, `+ai`, `-a`, `=a`), an index (`-a#`),
# or both (`+a#`, `+ai#`, `=a#`); probed as the man page's examples read.
ACL_MODE_RE = re.compile(r"[+=-]ai?#?\Z")
# The commands whose options change what they write (sed's -i and -I, install's -d, -M and -b), so their first operand,
# where this Mac's getopt still reads an option, is read as an option too when it may begin with `-` once expanded.
OPTION_WRITERS = ("sed", "install")
TARGET_DIRECTORY = "--target-directory"
# the options that make rm remove a directory rather than a file, on this Mac's BSD rm (-r and -R recursively,
# -d the empty directory itself); scan reads them as it reads any cluster, so `rm -fr`, `rm -- -r` and `rm -rf` agree.
RM_DIRECTORY_OPTIONS = ("-r", "-R", "-d")
RM_TREE_OPTIONS = ("-r", "-R", "--recursive")  # the hierarchy rooted in each operand (rm(1): -r is -R)
# cp's options that copy a directory and everything under it (cp(1): -R, and -a, "Same as -RpP"; GNU's -r,
# --recursive and --archive too), and the recursive option of chmod, chown, chgrp and chflags, which change every file
# under a directory operand rather than the directory alone.
CP_TREE_OPTIONS = ("-R", "-r", "-a", "--recursive", "--archive")
# install's options that set a mode, an owner or a group (SPD-137): this Mac's BSD install -d applies them to a directory
# that already exists (probed: `install -d -m 700 d` on a 755 d leaves it 700, where `mkdir -m 700 d` says File exists), so
# with any of them among its options install -d is a metadata write of each operand, a file's reading, and never a make.
# GNU's long forms count too, and each abbreviation getopt_long would take for one (`--mo=700`, `--own`); -f's flags are
# ignored under -d (probed).
INSTALL_METADATA_OPTIONS = ("-m", "-o", "-g")
INSTALL_METADATA_LONGS = ("--mode", "--owner", "--group")
MODE_TREE_OPTIONS = ("-R", "--recursive")
# the kind a destination entry carries when what lands there may be a whole tree: (RECURSIVE, the rsync exclude
# patterns a walk honours or None, whether a source spelled with a trailing `/` lands as its contents).
RECURSIVE = "recursive"
# A `$NAME` or `${NAME}` anywhere in a word, which `resolved` puts the line's own value in place of.  A `$` the quoting marked
# literal is followed by that marker, never by a name, so it never matches.
_EXPANSION_RE = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})")


def long_name(name, longs):
    """A long option as the command reads it: itself, or GNU's abbreviation of --target-directory (`--target`, `--t`), the
    one long option that moves the destination; any other abbreviation is read as a flag, which keeps every word after it
    an operand."""
    if name not in longs and TARGET_DIRECTORY in longs and len(name) >= 3 and TARGET_DIRECTORY.startswith(name):
        return TARGET_DIRECTORY
    return name


def scan(words, values, longs, is_option=None):
    """(options, operands) of a command's arguments as this Mac's getopt reads them (probed): options until `--` or the
    first operand; in a cluster, the first letter that takes a value takes the rest of the word or the next one; a GNU long
    option takes the next word when it is in `longs` and has no `=`.  An option is (name, value or None, whether its value
    was the next word).  `is_option`: a test a word spelled with `-` must pass to be read as options (chmod's modes)."""
    options, i = [], 0
    while i < len(words):
        w = words[i]
        if w == "--":
            return options, words[i + 1 :]
        if len(w) < 2 or not w.startswith("-") or (is_option is not None and not is_option(w)):
            break
        if w.startswith("--"):
            name, eq, value = w.partition("=")
            name = long_name(name, longs)
            spaced = not eq and name in longs
            if spaced:
                value, i = (words[i + 1] if i + 1 < len(words) else ""), i + 1
            options.append((name, value if (eq or spaced) else None, spaced))
            i += 1
            continue
        for k in range(1, len(w)):
            if w[k] in values:
                spaced = k + 1 == len(w)
                if spaced:
                    value, i = (words[i + 1] if i + 1 < len(words) else ""), i + 1
                else:
                    value = w[k + 1 :]
                options.append(("-" + w[k], value, spaced))
                break
            options.append(("-" + w[k], None, False))
        i += 1
    return options, words[i:]


def chmod_option(word):
    return word.startswith("--") or all(c in CHMOD_OPTIONS for c in word[1:])


def read_writes(cmd, base, words, a):
    """Record in a.arg_writes each file the command `words` (its command word spelled `cmd`, dispatched as `base`) writes by
    argument: (cmd, the operand word, the directories the shell may be in, the source words a destination directory takes,
    how -- "path" written, "dest" a destination that is a directory or a file, "into" a directory -- a backup suffix, and
    the directory kind: "make" when the write only makes a directory, "remove" when it only removes one, else
    None, which is every write of a file."""
    shape, values, longs = syntax.ARG_WRITE_COMMANDS[base]
    args = words[1:]
    hidden = base in OPTION_WRITERS and hidden_option(args, values, longs)
    if shape == "sed":
        entries = sed_writes(args, hidden)
    elif shape == "mode":
        entries = mode_writes(base, args)
    else:
        entries = operand_writes(base, shape, args, hidden)
    for word, sources, how, suffix, kind in entries:
        a.arg_writes.append((cmd, resolved(word, a), a.cwds, tuple(resolved(s, a) for s in sources), how,
                             resolved(suffix, a) if suffix else suffix, kind))


def resolved(word, a):
    """The word with every `$NAME` or `${NAME}` the line assigned put in its place, when both shells pass that value as the
    one plain word it spells: a value the line settled (not doubted, not a loop's or a function body's, not one the shells
    set themselves), holding no blank (bash would split it), no glob character (bash expands an unquoted expansion's) and
    nothing left to expand.  A member writes into its scratchpad through a variable it set on the line
    (`S=<scratchpad>; mkdir -p $S/base`) far too often for the raw word to be the reading here; everything else stays as
    spelled and earns the unresolvable-target refusal a redirection's spelling earns.

    A glob character counts wherever the value holds one, quoted or not: the quoting that made it literal is
    the assignment's, and bash expands the unquoted expansion's characters afterwards while zsh does not, so `S='docs*';
    rm $S/f` removes docs/f in one shell and the literal docs*/f in the other.  The hook settles neither reading and
    keeps the refusal the raw word earns.

    Every write target the hook checks is read this way -- a file a command names as an operand, a redirection
    target, a tee operand and the file a git call's own option or environment names -- each resolved where the analysis
    records it, which is the point of the walk that holds the value the shell would use there."""
    if not word or "$" not in word or hookio.SUBST in word or "`" in word:
        return word

    def one(m):
        name = m.group(1) or m.group(2)
        value = a.vars.get(name)
        if value is None or name in a.doubt or name in a.sticky or a.all_doubt or name in syntax.DYNAMIC_VARIABLES:
            return m.group(0)
        if not value or unresolved(value) or syntax._IFS_BLANKS_RE.search(value) or syntax._ARRAY_VALUE in value \
                or syntax.GLOB_RE.search(prepare.deglob(value)):  # deglob: a glob character the assignment's own quoting marked literal counts too
            return m.group(0)
        return value

    return _EXPANSION_RE.sub(one, word)


def hidden_option(args, values, longs):
    """True when the first operand begins with an expansion, so this Mac's getopt may still read it as an option and what
    the command writes is not what the words as spelled say: `sed "$X" s/a/b/ f` is `sed -i.bak` when X is `-i.bak`.  The
    line is then read both ways and each reading's files are checked, which leaves a `sed -n "${n},$((n+3))p" f` that
    writes nothing silent while an in-place one behind the same word is not."""
    operands = scan(args, values, longs)[1]
    return bool(operands) and expansion_at_start(operands[0])


def expansion_at_start(word):
    """True when a word begins with an expansion the shell resolves, which alone can put a `-` at its start;
    also with an operand the line does not spell (find's `{}`, xargs's input), which may be an option too."""
    return (word.startswith(hookio.SUBST) or (word.startswith("$") and not word.startswith("$" + syntax._LITERAL_DOLLAR))
            or word[:1] in (syntax.FIND_PATH, syntax.INPUT_OPERAND, syntax.ANY_PATH))


def directory_kind(base, names):
    """What a command does to the operands it writes, when all it does to them is make or remove a directory:
    "make" for mkdir and `install -d`, whose operands are directories they create and nothing else; "remove" for rmdir,
    and for rm when -d puts a directory within its reach.  Every other command, and rm without one of those options,
    writes a file, and the path rule reads its operand as it reads a redirection target.  rm under -r or -R
    removes the whole hierarchy rooted in the operand, "rm-tree", which bash_rule reads as a whole-subtree removal ("tree")
    unless the operand exists now as something other than a directory -- a file, or a symlink, which rm removes as itself
    -- when it is "remove" as before: `rm -rf bin/sub` with `bin/*` removes bin/sub/<anything>, which `bin/*`
    does not cover.  install -d with a mode, an owner or a group among its options also changes each operand that already
    exists (install_metadata), so it writes a file."""
    if base == "mkdir" or (base == "install" and "-d" in names and not install_metadata(names)):
        return "make"
    if base == "rm" and any(n in names for n in RM_TREE_OPTIONS):
        return "rm-tree"
    if base == "rmdir" or (base == "rm" and any(n in names for n in RM_DIRECTORY_OPTIONS)):
        return "remove"
    return None


def install_metadata(names):
    """True when install's options set a mode, an owner or a group: -m, -o or -g in any spelling scan reads (separate,
    glued, in a cluster), or GNU's --mode, --owner, --group or an abbreviation of one."""
    return any(n in INSTALL_METADATA_OPTIONS
               or (n.startswith("--") and len(n) > 2 and any(l.startswith(n) for l in INSTALL_METADATA_LONGS)) for n in names)


def options_unknown(operands):
    """True when the first operand this Mac's getopt would read is an operand the line does not spell (find's `{}`,
    xargs's input): what it holds may be options, so the command is read as if every option that widens what it writes
    were given (rm -r, cp -R, chmod -R, install -m)."""
    return bool(operands) and operands[0][:1] in (syntax.FIND_PATH, syntax.INPUT_OPERAND, syntax.ANY_PATH)


def operand_writes(base, shape, args, hidden=False):
    """The writes of a command whose operands are its files: every operand ("each"), or a destination (cp, install, mv, ln)."""
    shape_values, longs = syntax.ARG_WRITE_COMMANDS[base][1:]
    options, operands = scan(args, shape_values, longs)
    names = {n for n, _, _ in options}
    if options_unknown(operands):
        names |= set(RM_TREE_OPTIONS) | set(CP_TREE_OPTIONS) | set(INSTALL_METADATA_OPTIONS)
    entries, suffix = [], None
    if base == "install":
        entries += [(v, (), "path", None, None) for n, v, _ in options if n == "-M" and v]  # the metalog it writes, a file
        if "-b" in names:  # a backup of each file it replaces, <file>.old unless -B names the suffix
            suffix = next((v for n, v, _ in reversed(options) if n in ("-B", "--suffix") and v), ".old")
        if "-d" in names:
            shape = "each"
    if hidden:  # `install "$X" a b` may be `install -d`, which writes every operand: a directory it makes, or one whose
        # mode it sets when X is `-dm700` (SPD-137), so each is read as the file it may be
        entries += [(w, (), "path", None, None) for w in operands[1:]]
    if shape == "each":
        return entries + [(w, (), "path", None, directory_kind(base, names)) for w in operands]
    target_dir = next((v for n, v, _ in reversed(options) if n in ("-t", TARGET_DIRECTORY)), None)
    # GNU's -T writes the destination itself, never into it; install's -T is BSD's mtree tags, a value
    no_target = "--no-target-directory" in names or ("-T" in names and base != "install")
    # what a recursive copy or a move of a directory lands, read in written_paths: each source that is a directory
    # now lands as a whole tree, walked for a git directory; cp copies a source spelled with a trailing `/` as its
    # contents (cp(1)), mv renames it whole
    recursive = None
    if base == "cp" and any(n in names for n in CP_TREE_OPTIONS):
        recursive = (RECURSIVE, None, True)
    elif shape == "move":
        recursive = (RECURSIVE, None, False)
    if target_dir is not None:
        sources = tuple(operands)
        entries.append((target_dir, sources, "into", suffix, recursive))
    elif operands:
        dest, sources = operands[-1], tuple(operands[:-1])
        if shape == "link" and not sources:
            entries.append((".", (dest,), "into", suffix, None))  # `ln -s TARGET`: the link ./<name of TARGET>
            sources = ()
        elif no_target and recursive:
            entries.append((dest, sources, "itself", suffix, recursive))  # -T: each source lands as the destination
        elif no_target:
            entries.append((dest, (), "path", suffix, None))
        else:
            entries.append((dest, sources, "into" if len(sources) > 1 else "dest", suffix, recursive))  # several sources: a directory
    else:
        sources = ()
    if shape == "move":
        # mv removes each source, read as the file it is; a source that is a directory now takes its
        # whole subtree with it, "file-tree", and a file otherwise
        entries = [(s, (), "path", None, "file-tree") for s in sources] + entries
    return entries


def mode_writes(base, args):
    """chmod, chown, chgrp and chflags: every operand after the mode, owner or flags (none after GNU's --reference or chmod's
    -E, -C, -N, -i, -I; after a chmod ACL mode, also its index and entry)."""
    values, longs = syntax.ARG_WRITE_COMMANDS[base][1:]
    options, operands = scan(args, values, longs, chmod_option if base == "chmod" else None)
    names = {n for n, _, _ in options}
    skip = 1
    if "--reference" in names or (base == "chmod" and any(n[1:] in CHMOD_NO_MODE for n in names if len(n) == 2)):
        skip = 0
    elif base == "chmod" and operands and ACL_MODE_RE.match(prepare.deglob(operands[0])):
        mode = prepare.deglob(operands[0])
        skip = 1 + ("#" in mode) + (mode != "-a#")
    # -R changes every file under a directory operand, "file-tree"; an operand the line does not spell where the
    # mode stands may be -R, the mode and the files at once
    unknown = options_unknown(operands)
    kind = "file-tree" if unknown or any(n in names for n in MODE_TREE_OPTIONS) else None
    return [(w, (), "path", None, kind) for w in (operands if unknown else operands[skip:])]


def sed_writes(args, hidden=False):
    """The files sed edits in place (-i, -I, GNU's --in-place), each with its backup when the suffix is not empty; nothing
    without them.  BSD's -i always takes the next word unless its suffix is attached (probed: `sed -i -e X f` backed f up to
    f-e), and a suffix holding `/` fails there, BSD appending it to each file's name (rename: Not a directory), so such a
    word is also read as GNU reads a bare -i: the script, with the files after it.  `hidden`: the first operand begins with
    an expansion, so it may be the in-place option itself, and the words after the script it would leave are read as files."""
    shape_values, longs = syntax.ARG_WRITE_COMMANDS["sed"][1:]
    options, operands = scan(args, shape_values, longs)
    in_place = [(v or "", spaced) for n, v, spaced in options if n in ("-i", "-I", "--in-place")]
    script_given = any(n in ("-e", "-f", "--expression", "--file") for n, _, _ in options)
    if not in_place:
        return [(f, (), "path", None, None) for f in (operands[1:] if script_given else operands[2:])] if hidden else []
    suffix, spaced = in_place[-1]  # the last in-place option is the one sed keeps
    files = operands if script_given else operands[1:]
    entries = [(f, (), "path", suffix or None, None) for f in files]
    if spaced and "/" in prepare.deglob(suffix):
        gnu = ([suffix] if script_given else []) + list(operands)
        entries += [(f, (), "path", None, None) for f in gnu if f not in files]
    return entries


def option_read_index(base, words, start, a):
    """The index, from `start`, of the first word of a command's options this Mac's getopt reads that holds a glob or an
    expansion, or None: each word spelled with `-` before `--` and the first operand, and for sed and
    install, whose options change what they write, a glob at the first operand that may become one."""
    values, longs = syntax.ARG_WRITE_COMMANDS[base][1:]
    i = 1
    while i < len(words):
        w = words[i]
        if w == "--":
            return None
        if len(w) < 2 or not w.startswith("-") or (base == "chmod" and not chmod_option(w)):
            # the first operand, which for sed and install may still be an option: a glob is read as each option it can
            # become; an expansion is read as both readings' writes instead (hidden_option), since a word the
            # line cannot settle is how a member spells a line range it only prints
            return i if i >= start and base in OPTION_WRITERS and globbing.active_glob_word(w) and globbing.may_start_with_dash(w) else None
        if i >= start and expansions.active_read_word(w):
            return i
        if w.startswith("--"):
            i += "=" not in w and long_name(w, longs) in longs  # its value, the next word
        else:
            k = next((k for k in range(1, len(w)) if w[k] in values), None)
            i += k is not None and k + 1 == len(w)
        i += 1
    return None


def unresolved(word):
    return "$" in word or "`" in word or hookio.SUBST in word or syntax.unknown_operand(word)


def written_paths(entries, walk=False):
    """([(named, target, cwds, directory)], capped, unwalked) for bash_reason: every file the recorded writes by argument
    name, each target a masked word checked as a redirection target is, `named` the spelling its reason gives and
    `directory` the kind the path rule reads a member's globs with; the first source glob whose files
    reached the match budget, or None; and the first tree whose walk stopped short, or None.  Only an operand the command
    writes as it stands carries a kind: a backup the command leaves beside it is a file, and so is every name a destination
    directory takes.

    A destination entry whose kind is RECURSIVE (cp -R, mv, rsync, ditto) lands each source that is a directory
    now as a whole tree ("tree"), under the source's name or, for a trailing `/` where the tool copies contents, as the
    destination itself; an operand the line does not spell lands anywhere under the destination.  `walk` (a caller the
    path rule holds): every tree such a copy lands, and every tree find hands its command ("find-tree", "walk"), is walked
    (tree_walk.first_git_entry), and a git directory or config file found there is a target of its own where it lands, so
    the rule against a member writing a .git path refuses a copied `.git` as it refuses writing one."""
    out, capped, unwalked = [], None, None
    for cmd, word, cwds, sources, how, suffix, kind in entries:
        targets, trees, walks = [], [], []  # walks: (the word a tree lands at, its source directory now, rsync's excludes)
        if how in ("path", "walk"):
            if how == "path":
                targets.append(word)
            if kind == "find-tree" or how == "walk":
                walks += [(globbing.literalize(path), path, None) for _, path in tree_walk.directory_sources(word, cwds)]
        else:
            recursive = kind if isinstance(kind, tuple) else None
            into, itself = ([], True) if how == "itself" else destination(word, cwds, how)
            if itself:
                targets.append(word)
            for folder in into:
                join = "" if folder.endswith("/") else "/"
                for source in sources:
                    if syntax.unknown_operand(source):
                        trees.append(folder)  # a name the line does not spell, and with -R a whole tree, lands under it
                        continue
                    names, cap = source_names(source, cwds)
                    if cap and capped is None:
                        capped = "`%s` %s" % (cmd, prepare.deglob(source))
                    targets += [folder + join + name for name in names]
                    for name, path in tree_walk.directory_sources(source, cwds) if recursive else ():
                        landing = folder if recursive[2] and prepare.deglob(source).endswith("/") else folder + join + name
                        trees.append(landing)
                        walks.append((landing, path, recursive[1]))
            for source in sources if itself and recursive else ():
                if syntax.unknown_operand(source):
                    trees.append(word)
                for _, path in tree_walk.directory_sources(source, cwds):
                    trees.append(word)  # the source copied or moved as the destination itself
                    walks.append((word, path, recursive[1]))
        for target in targets:
            out.append(("`%s` %s" % (cmd, target), target, cwds, kind if how == "path" else None))
            if suffix:
                out.append(("`%s` %s" % (cmd, target + suffix), target + suffix, cwds, None))
        out += [("`%s` %s" % (cmd, tree), tree, cwds, "tree") for tree in dict.fromkeys(trees)]
        for landing, path, excludes in walks if walk else ():
            rel, short = tree_walk.first_git_entry(path, excludes)
            if short and unwalked is None:
                unwalked = "`%s` %s" % (cmd, path)
            if rel is not None:  # where the git directory or config file lands, a file the .git path rule reads
                found = landing + ("" if landing.endswith("/") else "/") + globbing.literalize(rel)
                origin = os.path.join(path, rel)
                named = "`%s` %s" % (cmd, found) if origin == found else "`%s` %s, the copy of %s" % (cmd, found, origin)
                out.append((named, found, cwds, None))
    return out, capped, unwalked


def destination(word, cwds, how):
    """(the directory words a destination names that the command writes its sources into, whether it writes the word
    itself).  An "into" word is a directory whatever it is now.  A "dest" word is one when it exists as a directory in a
    directory the shell may be in, or is spelled with a trailing `/` (cp -R makes it then), and the file itself when it may
    be anything else or is a symlink (mv -h, ln -h replace the link); a glob, each directory it matches.  A word the hook
    cannot resolve or follow is left to the redirection's reading of it, which refuses it."""
    if how == "into":
        return [word], False
    if unresolved(word):
        return [], True
    if bash_rule.target_has_active_glob(word):
        expansion = redirect_globs.expand_redirect_target(word, cwds)
        if expansion is None:
            return [], True
        return [globbing.literalize(m) for m in expansion[0] if os.path.isdir(m)], True
    paths = bash_rule.redirection_paths(prepare.deglob(word), cwds)
    if paths is None:
        return [], True
    paths = [os.path.expanduser(p) for p in paths]
    is_dir = [os.path.isdir(p) for p in paths]
    link = any(os.path.islink(p.rstrip("/")) for p in paths)
    return ([word] if prepare.deglob(word).endswith("/") or any(is_dir) else []), not all(is_dir) or link


def source_names(source, cwds):
    """(the names a source takes inside a destination directory, as masked words, and whether a glob's files reached the
    match budget): its last path segment; for a glob there, the name of each file it matches now, or its literal name when
    it matches none (as bash passes it); a bare `~` or `~user`, the name of the directory it is."""
    text = source.rstrip("/") or source
    last = text.rpartition("/")[2]
    if unresolved(last):
        return [last], False
    if bash_rule.target_has_active_glob(last):
        expansion = redirect_globs.expand_redirect_target(text, cwds)
        if expansion is None:
            return [last], False  # a glob name in the destination, where the redirection's reading refuses a member
        matches, capped = expansion
        if not matches:
            return [globbing.literalize(prepare.deglob(last))], capped
        return [globbing.literalize(os.path.basename(m.rstrip("/"))) for m in matches], capped
    if last.startswith("~") and last == text:
        return [globbing.literalize(os.path.basename(os.path.expanduser(prepare.deglob(last))))], False
    return [last], False
