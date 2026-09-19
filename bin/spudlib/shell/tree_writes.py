"""shell/tree_writes: the commands whose files land under a directory the line names but whose names it does not spell
(SPD-126) -- an archive extracted, a patch applied, a tree synced or copied -- and the files the same commands name.

Until SPD-126 none of them was read: `tar -xf a.tar -C out`, `unzip a.zip -d out`, `patch -p1 < x.patch`, `rsync -a src/
dest/` and `ditto src dst` recorded no write at all, and `cp -R src dest` checked dest and dest/src and nothing under them,
so a source tree holding a `.git` planted one wherever it landed.  Each command here is read on this Mac's grammar (its man
page: bsdtar, Info-ZIP unzip, BSD patch, openrsync, ditto), and records a write anywhere under the directory its files land
in (arg_writes' kind "tree"), which the path rule lets a member make only where one of its globs covers the whole subtree
(hooks/pathrule.glob_covers_directory).  What the line cannot place at all -- bsdtar's -P, which keeps absolute paths and
`..`; unzip's `-:`, which keeps `../`; a list tar reads with -T; rsync's daemon; an option a substitution may hide -- is
syntax.ANY_PATH, refused to a member as a target the hook cannot resolve.  The files these commands name on the line are
read here too, since their grammar is: tar's archive under -c, -r and -u, and patch's file operand, -o, -r and backups (the
second SPD-126 engineer's), rsync's --log-file and batch files, ditto -c's archive.  A download (curl, wget) is
shell/downloads' reading.

What rsync and ditto copy is walked for a git directory by shell/tree_walk, as cp -R's and mv's are, through the destination
entry each records with arg_writes.RECURSIVE.  An archive's, a patch's or a download's names are not on disk to read, and
are not walked: such a command may land a `.git` under the directory it writes into, which the tree reading cannot see.

Past 250 lines (SPD-065's look-again point) it stays whole: it is a list of five grammars, each one short reader with its
table, and its one caller, the analysis, dispatches to all of them through read_tree_writes; the walk, which another caller
wants alone, is the seam taken (shell/tree_walk), and so are the downloads, whose two grammars read files and trees alike
(shell/downloads)."""

import re

from . import analyse, arg_writes, prepare, syntax
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
TAR_ARCHIVE_MODES = ("c", "r", "u")  # SPD-126: the modes that write the archive -f names
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


def read_tree_writes(cmd, base, words, a, depth):
    """Record what `words`, a command of syntax.TREE_WRITE_COMMANDS other than find, writes under a directory (module
    docstring); `cmd` is its command word as spelled, `base` what it dispatches on."""
    reader = {"tar": read_tar, "bsdtar": read_tar, "unzip": read_unzip, "patch": read_patch, "rsync": read_rsync,
              "ditto": read_ditto}[base]
    # SPD-127's one reading of a variable: a `$NAME` the line settled is read as the option or the file it holds
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
    a program tar runs, read as a command.  SPD-126's second engineer: c, r and u modes write the archive -f names (or TAPE,
    set on the line, when -f is not given; `-` is standard output), relative to the line's own directory: tar opens it
    before it adds a file, and -C "changes the directory before adding the following files" (tar(1)), so a -C before -f
    moves only what goes into the archive."""
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


def read_unzip(cmd, args, a, depth):
    """Info-ZIP unzip: extracts under -d's exdir (which may stand anywhere on the line) or the line's directory; -c, -p, -l,
    -t, -v and -z write no file, -Z is zipinfo; `-:` keeps `../`, and options from UNZIP or UNZIPOPT (the line's own
    assignment) may say anything."""
    if args and prepare.deglob(args[0]) == "-Z":
        return
    exdir, excluding, anywhere, writes = None, False, any(v in a.vars for v in UNZIP_ENV), True
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
                elif c in UNZIP_NO_FILES:
                    writes = False
        elif not excluding and hidden_word(w, a):
            anywhere = True  # unzip reads options anywhere on its line (unzip(1), -d)
        i += 1
    if not writes:
        return
    record(a, cmd, syntax.ANY_PATH if anywhere else (exdir if exdir is not None else "."))


def read_patch(cmd, args, a, depth):
    """BSD patch: writes the files its patch names under -d's directory or the line's -- a whole-subtree write -- whether
    or not the line names one, since a patch file may hold several patches and only the first takes the file operand and
    -o (patch.c's reinitialize_almost_everything clears both, and each later patch names its own file); -C writes nothing;
    each -d changes directory from the one before it, as patch does when it reads the option; a -B backup prefix holding a
    directory puts the backups under it.  SPD-126's second engineer: the file operand, or -o's file in its place, is
    written as named, with its backup (patch_backups) and its reject file, -r's or <file>.rej (patch(1))."""
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
    if reject is not None and prepare.deglob(reject) not in ("", "-"):
        record(a, cmd, here(reject), None)
    elif written is not None:
        record(a, cmd, here(written) + ".rej", None)


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

