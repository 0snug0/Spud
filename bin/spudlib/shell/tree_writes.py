"""shell/tree_writes: the commands whose files land under a directory the line names but whose names it does not spell
-- an archive extracted, a patch applied, a tree synced or copied -- and the files the same commands name.

None of them used to be read: `tar -xf a.tar -C out`, `unzip a.zip -d out`, `patch -p1 < x.patch`, `rsync -a src/
dest/` and `ditto src dst` recorded no write at all, and `cp -R src dest` checked dest and dest/src and nothing under them,
so a source tree holding a `.git` planted one wherever it landed.  Each command here is read on this Mac's grammar (its man
page: bsdtar, Info-ZIP unzip, BSD patch, openrsync, ditto), and records a write anywhere under the directory its files land
in (arg_writes' kind "tree"), which the path rule lets a member make only where one of its globs covers the whole subtree
(hooks/pathrule.glob_covers_directory).  What the line cannot place at all -- bsdtar's -P, which keeps absolute paths and
`..`; unzip's `-:`, which keeps `../`; a list tar reads with -T; rsync's daemon; an option a substitution may hide -- is
syntax.ANY_PATH, refused to a member as a target the hook cannot resolve.  The files these commands name on the line are
read here too, since their grammar is: tar's archive under -c, -r and -u, and patch's file operand, -o, -r and backups,
rsync's --log-file and batch files, ditto -c's archive.  A download (curl, wget) is shell/downloads' reading.

What rsync and ditto copy is walked for a git directory by shell/tree_walk, as cp -R's and mv's are, through the destination
entry each records with arg_writes.RECURSIVE.  An archive's or a patch's names are in the file the line names (SPD-144):
shell/archive_names lists them as bsdtar, unzip, ditto -x -k and BSD patch place them, and each is recorded here as a write
of its own under the directory the tree reading already holds, so a `.git/config` in an archive, or a patch's `../x`, meets
the path rule and SPD-066's refusal as a spelled target does, the name in the reason; a `< file` the command has on
standard input is read as the archive or patch it reads there (syntax.ShellAnalysis.stdin_file).  What cannot be listed
-- an archive on any other standard input (a pipe from a program, an inherited one), one the line does not spell or
writes before the command reads it, one missing, too large, of a format or compression the standard library lacks, a
name that leaves the directory -- is an "archive" finding with its cause (UNLISTED), which bash_rule refuses a member
alone (SPD-217, SPD-275) with ARCHIVE_REASON: the archive, the cause and the respelling; Spud keeps the tree reading he
had.  A write the line may make after the command opens the file and before it reads it -- beside it in a pipeline or a
background job, after && or ||, in a loop's or a function's next pass, around a nested shell -- is held against the file
as SPD-151 holds one against a -f script ("rewritable", archive_reason): every write of the whole line but the command's
own, which come after it has opened the file, save in a loop, where the names it listed on the pass before count too.  A
download's names stay unread.

Past 250 lines (the package's look-again point) it stays whole: it is a list of five grammars, each one short reader with its
table, and its one caller, the analysis, dispatches to all of them through read_tree_writes; the walk, which another caller
wants alone, is the seam taken (shell/tree_walk), and so are the downloads, whose two grammars read files and trees alike
(shell/downloads), and the reading of an archive's or a patch's file (shell/archive_names, a leaf that opens files and
knows nothing of the line).  What SPD-144 left here records those names, and each reader hands it the options, the
directory and the mark only its own grammar has."""

import os
import re

from . import analyse, arg_writes, archive_names, bash_rule, globbing, prepare, script_files, stdin_text, syntax
from ..hooks import hookio, pathrule


# bsdtar (tar(1)): the letters and long options whose value is the rest of the word or the next one, the mode options, and
# the options that change where x mode writes.  bsdtar reads options up to the first operand (its cmdline.c), after a
# leading bundle such as `xzf a.tar` whose value letters take the words after it in order.
TAR_VALUE_LETTERS = frozenset("bCfIsTX")
TAR_VALUE_LONGS = frozenset({
    "--block-size", "--cd", "--directory", "--file", "--files-from", "--exclude", "--exclude-from", "--include", "--format",
    "--gid", "--gname", "--group", "--newer", "--newer-mtime", "--newer-than", "--newer-mtime-than", "--older", "--older-mtime",
    "--older-than", "--older-mtime-than", "--options", "--passphrase", "--strip-components", "--uid", "--uname", "--owner",
    "--use-compress-program"})
TAR_MODES = {"-x": "x", "--extract": "x", "--get": "x", "-c": "c", "--create": "c", "-t": "t", "--list": "t", "-r": "r",
             "--append": "r", "-u": "u", "--update": "u"}
TAR_CHDIR = ("-C", "--cd", "--directory")
TAR_ARCHIVE_MODES = ("c", "r", "u")  # the modes that write the archive -f names
TAR_STDOUT = ("-O", "--to-stdout")  # x mode writes each entry to standard output, no file
TAR_ANYWHERE = ("-P", "--absolute-paths", "-T", "-I", "--files-from")  # -P keeps `/` and `..`; -T's list may hold -C lines
# unzip (Info-ZIP): the modes that write no file (-c, -p to standard output; -l, -v list; -t test; -z the comment), the
# letters whose value follows (-d exdir, -P password), and `-:`, which keeps `../` in the names it extracts.
UNZIP_NO_FILES = frozenset("cpltvz")
UNZIP_ENV = ("UNZIP", "UNZIPOPT")  # options unzip reads from the environment, "effectively the first options" (unzip(1))
# BSD patch (patch(1)): the options taking a value, the ones that write nothing or write only a file the line names.
PATCH_VALUE_LETTERS = frozenset("BDdFgioprVxYz")
PATCH_VALUE_LONGS = frozenset({"--prefix", "--ifdef", "--directory", "--fuzz", "--get", "--input", "--output", "--strip",
                               "--reject-file", "--version-control", "--debug", "--basename-prefix", "--suffix", "--quoting-style"})
# openrsync (rsync(1) here, "rsync version 2.6.9 compatible") and GNU rsync: the options taking a value, the flags, the
# options naming a program it runs, a file it writes or a directory it writes into, and the filter options under which a
# plain --exclude no longer settles what is copied.
RSYNC_VALUE_LETTERS = frozenset("efBTM")
RSYNC_VALUE_LONGS = frozenset("""--address --backup-dir --block-size --bwlimit --checksum-seed --chmod --compare-dest
    --compress-level --contimeout --copy-dest --exclude --exclude-from --files-from --filter --include --include-from
    --link-dest --log-file --log-file-format --max-delete --max-size --min-size --modify-window --partial-dir --password-file
    --port --protocol --read-batch --rsh --rsync-path --sockopts --suffix --temp-dir --timeout --only-write-batch --write-batch
    --config --out-format --log-format --iconv --remote-option --usermap --groupmap --chown --info --debug --skip-compress
    --outbuf --stop-after --stop-at --max-alloc --compress-choice --checksum-choice --early-input --copy-as""".split())
RSYNC_FLAG_LONGS = frozenset("""--append --append-verify --blocking-io --cache --no-cache --copy-unsafe-links --del --delete
    --delete-before --delete-during --delete-delay --delete-after --delete-excluded --delay-updates --executability --force
    --ignore-errors --ignore-existing --ignore-non-existing --existing --inplace --list-only --no-implied-dirs --no-motd
    --numeric-ids --partial --progress --remove-source-files --safe-links --size-only --specials --devices --stats --super
    --help --version --archive --backup --checksum --compress --copy-links --copy-dirlinks --cvs-exclude --dirs
    --extended-attributes --from0 --group --hard-links --human-readable --ignore-times --keep-dirlinks --links --dry-run
    --omit-dir-times --owner --perms --prune-empty-dirs --quiet --recursive --relative --sparse --times --update --verbose
    --one-file-system --whole-file --fuzzy --ipv4 --ipv6 --8-bit-output --daemon --no-detach --itemize-changes --mkpath
    --acls --xattrs --atimes --crtimes --open-noatime --fake-super --preallocate --write-devices --copy-devices --protect-args
    --secluded-args --trust-sender --old-args --msgs2stderr --delete-missing-args --ignore-missing-args --munge-links
    --omit-link-times --8-bit-output""".split())
RSYNC_PROGRAMS = ("-e", "--rsh", "--rsync-path")
RSYNC_FILES = ("--log-file", "--write-batch", "--only-write-batch")
RSYNC_TREES = ("-T", "--temp-dir", "--backup-dir", "--partial-dir")  # relative backup and partial dirs lie under the destination
RSYNC_RECURSIVE = ("-r", "-a", "-d", "--recursive", "--archive", "--dirs")
RSYNC_FILTERS = ("-f", "--filter", "-F", "-C", "--cvs-exclude", "--include", "--include-from", "--exclude-from", "--files-from")
# ditto(1): the long options taking a value, and the two naming a file it writes.
DITTO_VALUE_LONGS = frozenset({"--arch", "--bom", "--zlibCompressionLevel", "--keepBinariesList", "--keepBinariesPattern",
                               "--lang", "--outBom"})
DITTO_FILES = ("--keepBinariesList", "--outBom")
# A word whose start is `$NAME`, `${NAME`, or a special or positional parameter.
_START_NAME_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])")
# SPD-144: why the hook cannot list what an archive or a patch writes, each the clause ARCHIVE_REASON gives its cause
# (SPD-275).  "written" is SPD-151's shape, which "rewritable" (archive_reason) reads too.
UNLISTED = {
    "read": "the %s `%s` %s, so the hook cannot list the names it writes",
    "stdin": ("it reads its %s from standard input or a default device, which the hook does not read, so it cannot list the"
              " names it writes (name the file: %s)"),
    "unspelled": ("it names its %s through a word the line does not spell or a directory the hook cannot follow (`%s`): a"
                  " substitution, a variable it cannot settle, find's or xargs's operand, a glob"),
    "written": ("the hook reads the %s `%s` before the line runs, and the line may write that file before `%s` reads it: a"
                " redirection, a copy, move or link onto it, a download, an extraction, an earlier command, or one that may"
                " run beside or after it (a pipeline, a background job, a command after && or ||, a loop's or a function's"
                " next pass, a nested shell), so the hook would list one file's names and the command write another's"),
    "leaves": ("the %s `%s` names %s, which lands outside the directory it writes into (a patch's -p strips leading"
               " directories from its names)"),
    "rewrites": "it rewrites the names the %s %s yields (%s), which the hook does not follow",
}
# SPD-275: the reason an "archive" finding earns a member (bash_rule's member loop, archive_reason): the command, the
# cause's clause, and the caps archive_names keeps.
ARCHIVE_REASON = (
    "`%s` writes the names an archive or a patch holds, and %s. The hook lists those names and holds each to the path rule"
    " and the .git refusal as it holds a path the line spells, so a git hook or config (Law 7), a ledger file (Law 5) or a"
    " file outside your deliverables cannot land unread, and it refuses a member what it cannot list. Name the archive or"
    " patch on the line as a file the hook can read (tar's -f, patch's -i, unzip's or `ditto -x -k`'s operand, or a `<`"
    " file), spelled out, with no -s or --use-compress-program, within the caps (%d names, %d MiB decompressed, %d under"
    " bzip2, %d KiB of patch); where the line makes or changes that file, write it in one Bash call and extract or apply it"
    " in the next, where the hook reads the file the command reads")


def read_tree_writes(cmd, base, words, a, depth):
    """Record what `words`, a command of syntax.TREE_WRITE_COMMANDS other than find, writes under a directory (module
    docstring); `cmd` is its command word as spelled, `base` what it dispatches on."""
    reader = {"tar": read_tar, "bsdtar": read_tar, "unzip": read_unzip, "patch": read_patch, "rsync": read_rsync,
              "ditto": read_ditto}[base]
    # every write target's one reading of a variable: a `$NAME` the line settled is read as the option or file it holds
    reader(cmd, [arg_writes.resolved(w, a) for w in words[1:]], a, depth)


def record(a, cmd, word, kind="tree", how="path", sources=()):
    """One write by argument, as arg_writes records it, the line's own values put in the words (arg_writes.resolved)."""
    a.arg_writes.append((cmd, arg_writes.resolved(word, a), a.cwds, tuple(arg_writes.resolved(s, a) for s in sources), how,
                         None, kind))


def hidden_word(word, a):
    """True when a word a command may read as options holds a value the member controls and the line does not settle: a
    substitution or backtick, an operand find or xargs hands it, a positional or special parameter, a variable the shell
    sets itself, or one the line assigns, loops over or reads without settling it (after `source` or `trap`, any).  Such a
    word may be `-C /elsewhere`, `-P` or `-delete`.  A variable the line never touches is the environment's, which a
    member's own line cannot set, and is read as the operand it spells."""
    if hookio.SUBST in word or "`" in word or syntax.unknown_operand(word):
        return True
    word = arg_writes.resolved(word, a)
    for m in _START_NAME_RE.finditer(word):
        name = m.group(1)
        if not (name[0].isalpha() or name[0] == "_") or a.all_doubt or name in syntax.DYNAMIC_VARIABLES:
            return True
        if name in a.dashless_loops and name not in a.assigned:
            continue  # a loop's variable whose every listed value starts with something other than `-`
        if name in a.assigned or name in a.doubt or name in a.sticky:
            return True
    return False


def scan_options(args, value_letters, value_longs, a, permute, spaced_longs=True):
    """(options, operands, hidden) of a command's arguments: options as (name, value word or None), `--` ending them;
    GNU long options take `=value`, or the next word when in `value_longs` (and `spaced_longs`); a short cluster's first
    value letter takes the rest of the word or the next one.  `permute`: options may follow operands (getopt_long, popt,
    unzip), else the first operand ends them (bsdtar).  `hidden`: a word where an option may stand holds a value the member
    controls (hidden_word)."""
    options, operands, hidden, i, ended = [], [], False, 0, False
    while i < len(args):
        w = args[i]
        t = prepare.deglob(w)
        if ended or t == "-" or not t.startswith("-"):
            if not ended and hidden_word(w, a):
                hidden = True
            operands.append(w)
            if not permute:
                ended = True
        elif t == "--":
            ended = True
        elif t.startswith("--"):
            name, eq, _ = t.partition("=")
            if eq:
                options.append((name, w[w.index("=") + 1 :] if "=" in w else ""))
            elif name in value_longs and spaced_longs:
                options.append((name, args[i + 1] if i + 1 < len(args) else ""))
                i += 1
            else:
                options.append((name, None))
        else:
            for k in range(1, len(t)):
                if t[k] in value_letters:
                    value = w[k + 1 :] if k + 1 < len(t) else (args[i + 1] if i + 1 < len(args) else "")
                    i += k + 1 == len(t)
                    options.append(("-" + t[k], value))
                    break
                options.append(("-" + t[k], None))
        i += 1
    return options, operands, hidden


def last_value(options, names):
    return next((v for n, v in reversed(options) if n in names), None)


def names_of(options):
    return {n for n, _ in options}


def read_tar(cmd, args, a, depth):
    """bsdtar: x mode extracts every entry under the directory -C changes to (each -C relative to the one before it, as
    chdir(2) goes) or the line's own; -O writes none; -P, and a -T list, may put one anywhere.  --use-compress-program names
    a program tar runs, read as a command.  c, r and u modes write the archive -f names (or TAPE,
    set on the line, when -f is not given; `-` is standard output), relative to the line's own directory: tar opens it
    before it adds a file, and -C "changes the directory before adding the following files" (tar(1)), so a -C before -f
    moves only what goes into the archive.  A first word, or a first operand where tar still reads options, that holds a
    value the member controls (hidden_word) may be the mode itself with -P or -f beside it (`echo '-xPf a.tar' | xargs tar`,
    `X=$(...); tar $X a.tar`, SPD-248), so with no mode the line spells the archive may land anywhere."""
    mark, anywhere = len(a.arg_writes), bool(args) and hidden_word(args[0], a)
    bundle = []
    if args and not prepare.deglob(args[0]).startswith("-"):
        letters, rest = prepare.deglob(args[0]), args[1:]
        for c in letters:
            if c in TAR_VALUE_LETTERS:
                bundle.append(("-" + c, rest[0] if rest else ""))
                rest = rest[1:]
            else:
                bundle.append(("-" + c, None))
        args = rest
    options, _, hidden = scan_options(args, TAR_VALUE_LETTERS, TAR_VALUE_LONGS, a, permute=False)
    options = bundle + options
    for name, value in options:
        if name == "--use-compress-program" and value:
            analyse.analyse_new_shell(a, prepare.deglob(value), depth + 1)
    mode = next((TAR_MODES[n] for n, _ in reversed(options) if n in TAR_MODES), None)
    if anywhere or (mode is None and hidden):
        record(a, cmd, syntax.ANY_PATH)
        return
    names = names_of(options)
    if mode in TAR_ARCHIVE_MODES:
        archive = last_value(options, ("-f", "--file"))
        if archive is None and ("TAPE" in a.vars or "TAPE" in a.assigned):
            archive = "$TAPE"
        if hidden:  # the first operand may be -f /elsewhere
            record(a, cmd, syntax.ANY_PATH, None)
        elif archive is not None and prepare.deglob(archive) not in ("", "-"):
            record(a, cmd, archive, None)
        return
    if mode != "x" or names.intersection(TAR_STDOUT):
        return
    if hidden or names.intersection(TAR_ANYWHERE):
        record(a, cmd, syntax.ANY_PATH)
        return
    directory = None
    for name, value in options:
        if name in TAR_CHDIR and value is not None:
            text = prepare.deglob(value)
            directory = value if directory is None or text.startswith(("/", "~")) else directory.rstrip("/") + "/" + value
    record(a, cmd, directory if directory is not None else ".")
    read_tar_names(cmd, options, directory, a, mark, depth)


def read_tar_names(cmd, options, directory, a, mark, depth):
    """SPD-144: the names bsdtar's x mode writes, from the archive -f names (or TAPE, set on the line), opened from the line's
    own directory ("In x mode, change directories after opening the archive", tar(1)), each placed under `directory` as
    archive_names.tar_placed places it after --strip-components.  -s and --use-compress-program change what the archive
    yields and are not followed; an archive on standard input or the default device is not read."""
    archive = last_value(options, ("-f", "--file"))
    if archive is None and ("TAPE" in a.vars or "TAPE" in a.assigned):
        archive = arg_writes.resolved("$TAPE", a)
    rewrites = [n for n, _ in options if n in ("-s", "--use-compress-program")]
    if rewrites:
        where = "`%s`" % shown(archive) if archive is not None else "on standard input"
        return unlisted(a, cmd, "rewrites", "archive", where, rewrites[-1])
    strip = last_value(options, ("--strip-components",))
    if strip is not None:
        if arg_writes.unresolved(strip) or not prepare.deglob(strip).isdigit():
            return unlisted(a, cmd, "unspelled", "--strip-components count", shown(strip))
        strip = int(prepare.deglob(strip))
    if archive is None or prepare.deglob(archive) in stdin_text.STDIN_OPERANDS:
        # standard input: bsdtar here reads it with no -f too (probed: `tar -x < t.tar` extracted t.tar's members); the
        # file the line redirects there is read, any other input is not
        if a.stdin_file is None:
            return unlisted(a, cmd, "stdin", "archive", "-f, or `<` a file")
        archive = arg_writes.resolved(a.stdin_file, a)
    read_archive(cmd, archive, a, mark, depth, directory,
                 lambda name: [p for p in [archive_names.tar_placed(name, strip or 0)] if p])


def shown(word):
    """A word as a reason shows it (bash_rule.shown_word)."""
    return bash_rule.shown_word(word)


def unlisted(a, cmd, form, *values):
    """Record what an archive or a patch holds that the hook cannot list: an "archive" finding (form, the command as
    spelled, UNLISTED[form] filled with `values`, None, ()), which bash_rule refuses a member alone (archive_reason).
    False, for a caller that returns it."""
    a.findings.append(("archive", (form, shown(cmd), UNLISTED[form] % values, None, ())))
    return False


def rewritable(a, cmd, what, word, paths, mark, depth):
    """SPD-275: where `cmd` may run beside or before a write the reading meets after it -- in a pipeline, a background job
    or after && or || (a.unsure), in a loop's or a function's body (a.loop_depth), in a nested reading (`depth`) -- record
    an "archive" finding of form "rewritable" holding the readings of the file it reads (`paths`, lexical and real) and
    the writes it makes itself (from `mark` on), which archive_reason holds every other write of the whole line against,
    as SPD-151's rewritable -f script is (shell/script_text.written_script).  A command opens its file before it writes, so
    its own writes are left out, save where it may run again -- a loop's or a function's body, or a nested reading, which
    find's -exec and xargs run once per file -- and one run writes before the next one reads: there only its tree write
    (SPD-126's reading of the whole directory) is left out, and the names it listed count."""
    if not (a.unsure or a.loop_depth or depth):
        return
    readings = tuple(sorted({r for p in paths for r in (p, os.path.realpath(p))}))
    again = a.loop_depth or depth
    own = tuple(e for e in a.arg_writes[mark:] if not again or e[6] == "tree")
    a.findings.append(("archive", ("rewritable", shown(cmd), UNLISTED["written"] % (what, shown(word), shown(cmd)),
                                   readings, own)))


def archive_reason(detail, written):
    """The refusal an "archive" finding earns a member (bash_rule's member loop), or None: ARCHIVE_REASON with the cause,
    for a "rewritable" one only where `written` -- every write of the line but the command's own (rewritable), a
    redirection or tee target, a git call's own write, a write by argument, as bash_rule.writes_but reads them -- names
    the file or a directory above it."""
    form, cmd, cause, readings, _own = detail
    if form == "rewritable" and not script_files.rewritten(readings, written):
        return None
    return ARCHIVE_REASON % (cmd, cause, archive_names.MAX_NAMES, archive_names.MAX_STREAM["r:gz"] >> 20,
                             archive_names.MAX_STREAM["r:bz2"] >> 20, archive_names.MAX_PATCH >> 10)


def file_readings(word, a, extra_dirs=()):
    """The absolute paths a file the line names may be, from each directory the shell may be in, and from each of
    `extra_dirs` (words relative to the line's own) as well; None when the hook cannot say: a word the line does not
    spell or settle, a glob, a directory it cannot follow."""
    if arg_writes.unresolved(word) or bash_rule.target_has_active_glob(word):
        return None
    text = prepare.deglob(word)
    paths = bash_rule.redirection_paths(text, a.cwds)
    if paths is None:
        return None
    for d in extra_dirs:
        if arg_writes.unresolved(d) or bash_rule.target_has_active_glob(d):
            return None
        if not text.startswith(("/", "~")):
            more = bash_rule.redirection_paths(prepare.deglob(d).rstrip("/") + "/" + text, a.cwds)
            if more is None:
                return None
            paths += more
    return sorted({os.path.normpath(os.path.expanduser(p)) for p in paths})


def written_first(paths, a, mark):
    """True when a write the line makes before this command -- a redirection or tee target, a git call's own write, a write
    by argument recorded before `mark`, bash_rule.written_targets' reading of them -- names one of `paths` or a directory
    above it, or when one names a file the hook cannot place (xargs's input, a command that writes anywhere).  The hook
    reads the file before the line runs, so it would list one archive and the command extract another (SPD-151's rule,
    shell/script_text).  The command's own writes, from `mark` on, come after it has opened the file."""
    before = a.arg_writes[:mark]
    if not (a.redirects or a.git_writes or before):
        return False
    if any(syntax.unknown_operand(entry[1]) for entry in before):
        return True
    written = bash_rule.written_targets(a, arg_writes.written_paths(before)[0])
    readings = {r for p in paths for r in (p, os.path.realpath(p))}
    return any(r == w or r.startswith(w.rstrip("/") + "/") for w in written for r in readings)


def read_archive(cmd, word, a, mark, depth, directory, place, zip_only=False, alternate=None):
    """Record every name the archive `word` names would write under `directory` (None for the line's own), each as
    `place` places a member name; what the hook cannot list is refused a member (unlisted), and a write of the line that
    may reach the file before the command reads it too (written_first, rewritable).  `alternate`: a suffix tried when the
    file as named does not exist (unzip's `.zip`), whose file the line's writes are held against as well."""
    what = "archive"
    paths = file_readings(word, a)
    if paths is None:
        return unlisted(a, cmd, "unspelled", what, shown(word))
    readings = paths + [p + alternate for p in paths] if alternate else paths
    if written_first(readings, a, mark):
        return unlisted(a, cmd, "written", what, shown(word), shown(cmd))
    names = []
    for path in paths:
        if alternate and not os.path.lexists(path) and os.path.lexists(path + alternate):
            path += alternate
        found, cause = archive_names.listed(path, zip_only)
        if found is None:
            return unlisted(a, cmd, "read", what, shown(word), archive_names.CAUSES[cause])
        names += found
    if record_names(a, cmd, directory, [(placed, is_dir) for name, is_dir in names for placed in place(name)], what, word):
        rewritable(a, cmd, what, word, readings, mark, depth)


def record_names(a, cmd, directory, names, what, word, extra=None):
    """Record each (name, is a directory) as a write under `directory`, a file's or a directory's making, held to the path
    rule as a spelled target is with the name in the reason, and return True; a name the hook cannot place, or one that
    leaves the directory, is refused a member instead (unlisted, False).  `extra(name)`: what else each file's write
    brings (patch's backups and reject file), given the name as a word relative to `directory`."""
    base = (directory if directory is not None else ".").rstrip("/")
    for name, is_dir in dict.fromkeys(names):
        if archive_names.unplaceable(name):
            return unlisted(a, cmd, "read", what, shown(word), archive_names.CAUSES["name"])
        if name.startswith("/") or os.path.normpath(name).split("/")[0] == "..":
            return unlisted(a, cmd, "leaves", what, shown(word), name)
    for name, is_dir in dict.fromkeys(names):
        literal = globbing.literalize(name)
        a.arg_writes.append((cmd, base + "/" + literal, a.cwds, (), "path", None, "make" if is_dir else None))
        if extra is not None and not is_dir:
            extra("./" + literal)  # never a leading `~` or `=` the shell would expand, whatever the name
    return True


def read_unzip(cmd, args, a, depth):
    """Info-ZIP unzip: extracts under -d's exdir (which may stand anywhere on the line) or the line's directory; -c, -p, -l,
    -t, -v and -z write no file, -Z is zipinfo; `-:` keeps `../`, and options from UNZIP or UNZIPOPT (the line's own
    assignment) may say anything."""
    if args and prepare.deglob(args[0]) == "-Z":
        return
    mark, exdir, excluding, anywhere, writes = len(a.arg_writes), None, False, any(v in a.vars for v in UNZIP_ENV), True
    archive, junk = None, False
    i = 0
    while i < len(args):
        w = args[i]
        t = prepare.deglob(w)
        if t.startswith("-") and len(t) > 1:
            excluding = False
            for k in range(1, len(t)):
                c = t[k]
                if c in "dP":
                    value = w[k + 1 :] if k + 1 < len(t) else (args[i + 1] if i + 1 < len(args) else "")
                    i += k + 1 == len(t)
                    if c == "d":
                        exdir = value
                    break
                if c == "x":
                    excluding = True  # the member names excluded after -x, up to the next option
                elif c == ":":
                    anywhere = True
                elif c == "j":
                    junk = True
                elif c in UNZIP_NO_FILES:
                    writes = False
        elif not excluding:
            if hidden_word(w, a):
                anywhere = True  # unzip reads options anywhere on its line (unzip(1), -d)
            archive = w if archive is None else archive  # the first operand is the zipfile, the rest its members
        i += 1
    if not writes:
        return
    record(a, cmd, syntax.ANY_PATH if anywhere else (exdir if exdir is not None else "."))
    if anywhere or archive is None:
        return
    # SPD-144: its names, as unzip places them (archive_names.zip_placed); a wildcard zipfile unzip matches itself names
    # archives the hook does not choose between
    if any(c in prepare.deglob(archive) for c in "*?["):
        return unlisted(a, cmd, "unspelled", "archive", shown(archive))
    read_archive(cmd, archive, a, mark, depth, exdir, lambda name: archive_names.zip_placed(name, junk), zip_only=True,
                 alternate=".zip")


def read_patch(cmd, args, a, depth):
    """BSD patch: writes the files its patch names under -d's directory or the line's -- a whole-subtree write -- whether
    or not the line names one, since a patch file may hold several patches and only the first takes the file operand and
    -o (patch.c's reinitialize_almost_everything clears both, and each later patch names its own file); -C writes nothing;
    each -d changes directory from the one before it, as patch does when it reads the option; a -B backup prefix holding a
    directory puts the backups under it.  The file operand, or -o's file in its place, is
    written as named, with its backup (patch_backups) and its reject file, -r's or <file>.rej (patch(1)); and so is every
    name the patch's own headers give (read_patch_names, SPD-144)."""
    mark = len(a.arg_writes)
    options, operands, hidden = scan_options(args, PATCH_VALUE_LETTERS, PATCH_VALUE_LONGS, a, permute=True)
    names = names_of(options)
    if names.intersection(("-C", "--check", "--dry-run")):
        return
    if hidden:  # an operand the member controls may be -d /elsewhere, before it is a file
        record(a, cmd, syntax.ANY_PATH)
        return
    directory = None
    for name, value in options:
        if name in ("-d", "--directory") and value is not None:
            text = prepare.deglob(value)
            directory = value if directory is None or text.startswith(("/", "~")) else directory.rstrip("/") + "/" + value
    record(a, cmd, directory if directory is not None else ".")
    prefix = last_value(options, ("-B", "--prefix"))
    text = prepare.deglob(prefix) if prefix else ""
    if "/" in text:
        head = prefix[: prefix.rindex("/")] if "/" in prefix else prefix
        if not text.startswith(("/", "~")) and directory is not None:
            head = directory.rstrip("/") + "/" + head
        record(a, cmd, head or "/")

    def here(word):  # a name patch opens once it has changed directory
        return word if directory is None or prepare.deglob(word).startswith(("/", "~")) else directory.rstrip("/") + "/" + word

    written = last_value(options, ("-o", "--output"))
    written = written if written is not None else (operands[0] if operands else None)
    if written is not None and prepare.deglob(written) in ("", "-"):
        written = None
    if written is not None:
        record(a, cmd, here(written), None)
        patch_backups(a, cmd, written, here, options, names)
    reject = last_value(options, ("-r", "--reject-file"))
    rejected = reject is not None and prepare.deglob(reject) not in ("", "-")
    if rejected:
        record(a, cmd, here(reject), None)
    elif written is not None:
        record(a, cmd, here(written) + ".rej", None)

    def beside(name):  # what patch writes beside a file its patch names (the name as -d leaves it): backups, <file>.rej
        patch_backups(a, cmd, name, here, options, names)
        if not rejected:
            record(a, cmd, here(name) + ".rej", None)

    read_patch_names(cmd, options, operands, directory, a, mark, depth, beside)


def read_patch_names(cmd, options, operands, directory, a, mark, depth, beside):
    """SPD-144: the names a patch's headers give (archive_names.patch_names, under -p), each written under -d's directory
    with what `beside` adds; a name that leaves the directory, absolute under -p0 or climbing out with `../`, is refused a
    member.  The patch is every -i file, else the second operand, else standard input: the file the line redirects there
    (a.stdin_file, opened by the shell from the line's directory before patch changes to -d's), or the text the line
    spells there (a here-document, a here-string, a printed pipe); any other standard input is refused a member.  patch
    changes to -d's directory "before doing anything else" (patch(1)), so a relative -i file or operand is read from both
    that directory and the line's, every one that exists."""
    strip = last_value(options, ("-p", "--strip"))
    if strip is not None:
        if arg_writes.unresolved(strip) or not prepare.deglob(strip).isdigit():
            return unlisted(a, cmd, "unspelled", "-p strip count", shown(strip))
        strip = int(prepare.deglob(strip))
    files = [(v, True) for n, v in options if n in ("-i", "--input") and v is not None] or [(w, True) for w in operands[1:2]]
    if any(prepare.deglob(w) in stdin_text.STDIN_OPERANDS for w, _ in files):
        files = []
    if not files and a.stdin_file is not None:
        files = [(arg_writes.resolved(a.stdin_file, a), False)]
    texts, word, read = [], None, []
    for word, beneath in files:
        paths = file_readings(word, a, [directory] if beneath and directory is not None else ())
        if paths is None:
            return unlisted(a, cmd, "unspelled", "patch", shown(word))
        if written_first(paths, a, mark):
            return unlisted(a, cmd, "written", "patch", shown(word), shown(cmd))
        present = [p for p in paths if os.path.lexists(p)] or paths[:1]
        for path in present:
            text, cause = archive_names.patch_file_text(path)
            if text is None:
                return unlisted(a, cmd, "read", "patch", shown(word), archive_names.CAUSES[cause])
            texts.append(text)
        read.append((word, paths))
    if not files:
        if a.stdin is None:
            if a.stdin_fed:
                return unlisted(a, cmd, "stdin", "patch", "-i, or `<` a file")
            return
        texts = stdin_text.each_reading(a.stdin)
        word = "-"
    names = [n for text in texts for n in archive_names.patch_names(text, strip)]
    if record_names(a, cmd, directory, [(n, False) for n in names], "patch", word, beside):
        for word, paths in read:  # each file it reads, every reading of it (the line's directory and -d's)
            rewritable(a, cmd, "patch", word, paths, mark, depth)


def patch_backups(a, cmd, written, here, options, names):
    """The backups BSD patch makes of the file it writes (patch(1), Backup Files): on a mismatch by default and for every
    file under -b, unless -V none, or --posix (POSIXLY_CORRECT set on the line) without -b, turns them off.  Named -B's
    prefix and the file, or -Y's prefix before its basename, or the file and -z's suffix (SIMPLE_BACKUP_SUFFIX set on the
    line, else .orig); a numbered <file>.~N~ too unless -V (PATCH_VERSION_CONTROL, VERSION_CONTROL) says simple."""
    version = last_value(options, ("-V", "--version-control"))
    for var in ("PATCH_VERSION_CONTROL", "VERSION_CONTROL"):
        if version is None and (var in a.vars or var in a.assigned):
            version = arg_writes.resolved("$" + var, a)
    kind = prepare.deglob(version) if version is not None and not arg_writes.unresolved(version) else ""
    asked = names.intersection(("-b", "--backup"))
    if (len(kind) > 1 and "none".startswith(kind)) or (("--posix" in names or "POSIXLY_CORRECT" in a.vars) and not asked):
        return
    prefix = last_value(options, ("-B", "--prefix"))
    basename_prefix = last_value(options, ("-Y", "--basename-prefix"))
    suffix = last_value(options, ("-z", "--suffix"))
    if suffix is None and ("SIMPLE_BACKUP_SUFFIX" in a.vars or "SIMPLE_BACKUP_SUFFIX" in a.assigned):
        suffix = "$SIMPLE_BACKUP_SUFFIX"
    suffix = suffix if suffix is not None else ".orig"
    if prefix is not None:
        record(a, cmd, here(prefix + written), None)
        return
    if basename_prefix is not None:
        folder, _, base = written.rpartition("/")
        record(a, cmd, here((folder + "/" if folder else "") + basename_prefix + base + suffix), None)
        return
    record(a, cmd, here(written) + suffix, None)
    if not (kind and ("never".startswith(kind) and len(kind) > 1 or "simple".startswith(kind))):
        record(a, cmd, here(written) + ".~" + pathrule.NAME_CHAR + pathrule.NAME_MORE + "~", None)


def remote_operand(word):
    """True when an rsync operand names another host (`host:path`, `host::module`, `rsync://`): nothing local is written."""
    t = prepare.deglob(word)
    head = t.split("/", 1)[0]
    return t.startswith("rsync://") or ":" in head


def read_rsync(cmd, args, a, depth):
    """openrsync: the destination, the last operand, is a directory every source is synchronised into and --delete removes
    under, a whole tree; each local source that is a directory lands there, as itself or, spelled with a trailing `/`, as
    its contents, and is walked for a git directory (a plain --exclude of a name honoured, as rsync(1) matches a pattern
    without `/` against each name); --remove-source-files removes under each source.  -e and --rsync-path name a program it
    runs, read as a command; --log-file and the batch files are files it writes; -T, and an absolute --backup-dir or
    --partial-dir, directories it writes into.  An option this reading does not know may take the next word, so then every
    operand is read as a possible destination; --daemon writes where a config file says."""
    options, operands, hidden = scan_options(args, RSYNC_VALUE_LETTERS, RSYNC_VALUE_LONGS, a, permute=True)
    names = names_of(options)
    for name, value in options:
        if name in RSYNC_PROGRAMS and value:
            analyse.analyse_new_shell(a, prepare.deglob(value), depth + 1)
        elif name in RSYNC_FILES and value is not None:
            record(a, cmd, value, None)
        elif name in RSYNC_TREES and value is not None and (name in ("-T", "--temp-dir") or prepare.deglob(value).startswith("/")):
            record(a, cmd, value)
    if hidden or "--daemon" in names:
        record(a, cmd, syntax.ANY_PATH)
        return
    unsure = any(n.startswith("--") and n not in RSYNC_VALUE_LONGS and n not in RSYNC_FLAG_LONGS and not n.startswith("--no-")
                 for n in names)
    local = [w for w in operands if not remote_operand(w)]
    for w in local if unsure else ():
        record(a, cmd, w)
    if len(operands) < 2:
        return
    dest, sources = operands[-1], [w for w in operands[:-1] if not remote_operand(w)]
    if not remote_operand(dest):
        record(a, cmd, dest)
        if names.intersection(RSYNC_RECURSIVE):
            excludes = None if names.intersection(RSYNC_FILTERS) else tuple(
                prepare.deglob(v) for n, v in options if n == "--exclude" and v is not None)
            record(a, cmd, dest, (arg_writes.RECURSIVE, excludes, True), "into", sources)
    for w in sources if "--remove-source-files" in names else ():
        record(a, cmd, w, "rm-tree")


def read_ditto(cmd, args, a, depth):
    """ditto: copies each source's contents into the destination directory, the last operand, a whole tree, each source
    walked for a git directory; -x extracts archives there; -c writes the archive the destination names, a file.
    --keepBinariesList and --outBom name files it writes."""
    mark = len(a.arg_writes)
    options, operands, hidden = scan_options(args, frozenset(), DITTO_VALUE_LONGS, a, permute=True)
    names = names_of(options)
    for name, value in options:
        if name in DITTO_FILES and value is not None:
            record(a, cmd, value, None)
    if "-h" in names or "--help" in names or len(operands) < 2:
        return
    dest, sources = operands[-1], operands[:-1]
    if hidden:
        record(a, cmd, syntax.ANY_PATH)
    elif "-c" in names:
        record(a, cmd, dest, None)
    else:
        record(a, cmd, dest)
        if "-x" not in names:
            record(a, cmd, dest, (arg_writes.RECURSIVE, (), True), "into", [s if s.endswith("/") else s + "/" for s in sources])
        else:  # SPD-144: each archive's names, a zip's under -k; without it ditto reads cpio, which the hook cannot list
            for source in sources:
                if "-k" not in names:
                    unlisted(a, cmd, "read", "archive", shown(source), archive_names.CAUSES["cpio"])
                    break
                if prepare.deglob(source) == "-":  # ditto(1): `-` reads the archive from standard input
                    if a.stdin_file is None:
                        unlisted(a, cmd, "stdin", "archive", "a file, or `<` one")
                        break
                    source = arg_writes.resolved(a.stdin_file, a)
                read_archive(cmd, source, a, mark, depth, dest, archive_names.zip_placed, zip_only=True)

