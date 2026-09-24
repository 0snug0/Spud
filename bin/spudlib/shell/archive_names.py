"""shell/archive_names: the names an archive or a patch would write, read from the file for shell/tree_writes (SPD-144).

SPD-126 reads `tar -x`, `unzip`, `ditto -x` and `patch` as a write anywhere under the directory they land in, since their
file names are not on the line.  They are in the archive or the patch, and an archive can hold `.git/config` or
`.git/hooks/post-index-change`, which SPD-066 refuses a member by every other route, while BSD patch keeps a relative name
whole when its directories exist, so `../` leaves -d's directory.  This module lists those names as each tool places
them; shell/tree_writes holds every one to the path rule, and refuses a member what cannot be read here.  It reads files
alone and imports nothing of the shell's reading, so it is a leaf the cycle reaches (the skill's §5), and the standard
library's tarfile and zipfile, with the compression modules they pull in, load through core/lazy only when a line holds
an archive the hook lists: a hook run whose line has none imports neither (tests/test_package.py, HookPathTest).

Where each tool puts a name, probed on this Mac through tests/probes/shell_probe.py (zsh 5.9, bash 3.2.57) against
archives the standard library wrote, one member each of `/abs/a.txt`, `../up/b.txt`, `in/../../esc.txt`, `//dbl/d.txt`,
`./dot/e.txt`, `~/tilde.txt`, `sub\\back\\slash.txt` and `.git/config`:
- bsdtar 3.5.3 (libarchive 3.7.4) removes every leading `/` and refuses a name with a `..` component ("Path contains
  '..'"), and without -P that is all (tar(1), -P); --strip-components drops leading elements first and skips a name with
  no more ("edited ... before security checks").  It reads any format libarchive does, a zip included, so a file tarfile
  does not open is tried as a zip, and one neither opens -- cpio, 7z, xar, an mtree, a compression the standard library
  lacks -- is not listed.  tarfile reads on past a zeroed block, as --ignore-zeros does, so the names are never fewer.
- Info-ZIP unzip 6.00 (Apple's) removes a leading `/` and every `..` component (`in/../../esc.txt` wrote in/esc.txt)
  unless `-:`, which tree_writes already reads as anywhere; ditto -x -k resolved the same name against the root it
  extracts to (esc.txt).  Both readings are held.  unzip turns `\\` into `/` in an archive made on MS-DOS, and prefers the
  UTF-8 name of an Info-ZIP Unicode Path field (0x7075) over the header's, so both of those are held too.
- ditto -x without -k reads cpio, which the standard library does not ("cpio read error: bad file format" on a tar).
- BSD patch takes a file's name from each patch's `*** `/`--- ` (context), `--- `/`+++ ` (unified) and `Index:` lines
  (patch(1), Filename Determination; its fetchname: leading blanks skipped, the name ending at a tab if the line holds
  one, else at the first blank, `/dev/null` naming none).  -pN strips N slash-separated components; -p0 keeps the name
  whole, absolute included; with no -p the name is its last component, or the whole relative name when its directories
  exist (patch -d d2 -i p2.patch, with d2/deep/dir present, wrote deep/dir/new.txt), and for a `diff --git` patch the
  whole name past `a/` or `b/`.  Which of old, new and index it picks depends on what exists when it runs, so all three
  are held, every reading of each.

What is not listed, and why the caller refuses a member for it: a file larger than the caps below, one that is missing,
not a regular file, not a format the standard library reads, or damaged; a member name holding a character the hook uses
for its own marks or cannot place as a path (`$`, a backtick, a private-use character, bytes that are not UTF-8).  The
caps bound what one archive adds to a hook run to about a tenth of a second, timed in process on this Mac (SPD-144): the
path rule reads each name in about 0.1 ms, so MAX_NAMES names cost about as much as the slowest capped stream, 4 MiB of
incompressible bzip2 (16 MiB of gzip, xz or zstd decompressed in a fifth of that or less); a plain tar's headers are
sought, not decompressed, and a zip's are its central directory, so neither has a stream to cap.

Past 250 lines (the look-again point) it stays whole: three short readings, tar's, zip's and patch's, each with the
placement rule its tool keeps, and one caller, shell/tree_writes, which takes all three; the size is the placement rules'
and the patch header scan's, no definition long."""

import os
import stat

from ..core import lazy


MAX_NAMES = 1000  # members (or patch names) listed per file, each of which the path rule then reads
# The bytes of a compressed tar decompressed to reach its last header, by the mode that opens it: bzip2 decompresses far
# slower than the others (module docstring), so its stream is capped lower; a plain tar ("r:") has no stream to cap.
MAX_STREAM = {"r:gz": 16 << 20, "r:xz": 16 << 20, "r:zst": 16 << 20, "r:bz2": 4 << 20, "r:": None}
MAX_PATCH = 1 << 20  # bytes of a patch read for its headers
# The first bytes of the compressions tarfile opens itself (gzip, bzip2, xz, zstd) and the mode that opens each, so a
# line imports only the one module its archive needs (tarfile's own "r:*" imports all four trying them in turn).
COMPRESSED = ((b"\x1f\x8b", "r:gz"), (b"BZh", "r:bz2"), (b"\xfd7zXZ\x00", "r:xz"), (b"\x28\xb5\x2f\xfd", "r:zst"))
UNICODE_PATH = 0x7075  # Info-ZIP's Unicode Path extra field: version, the header name's CRC, the name in UTF-8
# What a caller shows for each way a file cannot be listed.
CAUSES = {
    "missing": "does not exist now",
    "special": "is not a regular file",
    "format": "is not a tar or zip archive the standard library reads (another format, a compression it lacks, or damaged)",
    "large": "holds more than %d names, or more than %d MiB once decompressed (%d under bzip2)" % (
        MAX_NAMES, MAX_STREAM["r:gz"] >> 20, MAX_STREAM["r:bz2"] >> 20),
    "patch-large": "is larger than the %d KiB of a patch the hook reads" % (MAX_PATCH >> 10),
    "cpio": "is read as a cpio archive (ditto -x without -k), which the standard library does not read",
    "name": "holds a name the hook cannot place as a path (`$`, a backtick, a character it marks its own text with, bytes"
            " that are not UTF-8)",
}


def unplaceable(name):
    """True when a member name holds what the hook cannot hold as a path: `$` or a backtick (a target holding one reads
    as an expansion), the hook's substitution mark, a private-use character (its sentinels), or a byte that is not UTF-8
    (tarfile's and this module's surrogate escapes)."""
    if "$" in name or "`" in name or "__SPUD_SUBST__" in name:
        return True
    return any(0xE000 <= ord(c) <= 0xF8FF or 0xDC80 <= ord(c) <= 0xDCFF for c in name)


def regular_file(path):
    """None when `path` is a regular file the hook may open, else the cause."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return "missing"
    return None if stat.S_ISREG(mode) else "special"


def listed(path, zip_only=False):
    """([(name, is a directory)], None) for the archive at `path`, as bsdtar reads it (a tar, else a zip), or unzip and
    ditto -k (`zip_only`); (None, cause) where it cannot be listed (CAUSES)."""
    cause = regular_file(path)
    if cause:
        return None, cause
    if not zip_only:
        found = tar_names(path)
        if found is not None:
            return found
    return zip_names(path)


def tar_names(path):
    """The members of a tar tarfile opens, as listed answers; None when tarfile does not read it as a tar."""
    with open(path, "rb") as f:
        head = f.read(6)
    mode = next((m for magic, m in COMPRESSED if head.startswith(magic)), "r:")
    cap = MAX_STREAM[mode]
    try:
        tf = lazy.tarfile.open(path, mode)
    except Exception:  # not a tar in any compression tarfile knows, or damaged at its first header
        return None
    names = []
    try:
        with tf:
            tf.ignore_zeros = True  # past its first header, as bsdtar --ignore-zeros reads: never fewer names
            while True:
                if cap is not None and tf.offset > cap:
                    return None, "large"
                info = tf.next()
                if info is None:
                    break
                names.append((info.name, info.isdir()))
                if len(names) > MAX_NAMES:
                    return None, "large"
    except Exception:  # truncated or damaged past its first header
        return None, "format"
    return names, None


def zip_names(path):
    """The members of a zip, from its central directory, as listed answers: each header name, and the UTF-8 name of an
    Info-ZIP Unicode Path field beside it."""
    try:
        with lazy.zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except Exception:
        return None, "format"
    if len(infos) > MAX_NAMES:
        return None, "large"
    names = []
    for info in infos:
        directory = info.filename.endswith("/")
        names.append((info.filename, directory))
        unicode_name = unicode_path(info.extra)
        if unicode_name is not None:
            names.append((unicode_name, directory))
    return names, None


def unicode_path(extra):
    """The name an Info-ZIP Unicode Path extra field holds, or None; unzip prefers it to the header's own."""
    i = 0
    while i + 4 <= len(extra):
        kind = int.from_bytes(extra[i : i + 2], "little")
        size = int.from_bytes(extra[i + 2 : i + 4], "little")
        body = extra[i + 4 : i + 4 + size]
        if kind == UNICODE_PATH and len(body) > 5:
            return body[5:].decode("utf-8", "surrogateescape")
        i += 4 + size
    return None


def tar_placed(name, strip=0):
    """The name bsdtar writes a member as, relative to the directory it extracts into, or None where it writes none: the
    leading `/`s gone, `strip` leading elements dropped (a name with no more is skipped), and a name with a `..` component
    refused (module docstring)."""
    parts = [p for p in name.split("/") if p and p != "."]
    if strip:
        if len(parts) <= strip:
            return None
        parts = parts[strip:]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def zip_placed(name, junk=False):
    """The names unzip and ditto -x -k may write a member as, relative to the directory they extract into: unzip's, with
    every `..` component removed, and ditto's, each resolved against that directory as its root; a `\\` read as `/`
    too, as unzip reads a name made on MS-DOS; only the last component under unzip's -j."""
    readings = set()
    for spelled in {name, name.replace("\\", "/")}:
        parts = [p for p in spelled.split("/") if p and p != "."]
        dropped = [p for p in parts if p != ".."]
        resolved = []
        for p in parts:
            if p == "..":
                if resolved:
                    resolved.pop()
            else:
                resolved.append(p)
        for kept in (dropped, resolved):
            if junk:
                kept = kept[-1:]
            if kept:
                readings.add("/".join(kept))
    return sorted(readings)


# ----------------------------------------------------------------------------
# patch(1)
# ----------------------------------------------------------------------------


def patch_file_text(path):
    """(text, None) for a patch file the hook may read, decoded as the bytes its names are; (None, cause) otherwise."""
    cause = regular_file(path)
    if cause:
        return None, cause
    if os.path.getsize(path) > MAX_PATCH:
        return None, "patch-large"
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "surrogateescape"), None


def patch_headers(text):
    """[(a header's text after its marker, whether a `diff --git` line came before it)] for every old, new and index
    header in `text`, the patches in it read as one run.  A unified hunk's lines are counted off as its `@@` line says, so
    a removed line `-- x` or an added `++ x` is never read as a header; a context hunk's own `*** n,m ****` and `--- n,m
    ----` lines are not names.  Once a `diff --git` line is seen every later header is read as a git patch's too, which
    only adds readings."""
    out, git, hunk = [], False, None
    for line in text.splitlines():
        if hunk is not None:
            if line.startswith("\\"):  # `\ No newline at end of file`
                continue
            lead = line[:1]
            if lead in ("", " ", "-", "+"):
                old, new = hunk
                old -= lead != "+"
                new -= lead != "-"
                hunk = (old, new) if old > 0 or new > 0 else None
                continue
            hunk = None
        if line.startswith("@@ -"):
            hunk = unified_counts(line)
            continue
        body = line.lstrip(" \tX")  # patch reads a header after any indentation of blanks and X's (pch.c)
        if body.startswith("diff --git "):
            git = True
            continue
        for marker in ("*** ", "--- ", "+++ ", "Index:"):
            if body.startswith(marker):
                rest = body[len(marker) :]
                if not (marker in ("*** ", "--- ") and rest.rstrip().endswith(("****", "----"))):  # a context range line
                    out.append((rest, git))
                break
    return out


def unified_counts(line):
    """(old, new) line counts of a unified hunk's `@@ -l[,n] +l[,m] @@` line, each 1 when not given."""
    counts = []
    for part in line.split()[1:3]:
        _, _, n = part.partition(",")
        counts.append(int(n) if n.isdigit() else 1)
    return tuple(counts) if len(counts) == 2 else None


def fetch_name(spelled):
    """The name patch's fetchname takes from a header's text: leading blanks skipped, up to a tab when the text holds
    one, else up to the first blank; None for `/dev/null` or nothing."""
    text = spelled.lstrip(" \t")
    if not text or text.startswith("/dev/null"):
        return None
    end = text.find("\t") if "\t" in text else min((i for i, c in enumerate(text) if c.isspace()), default=len(text))
    return text[:end] or None


def strip_name(name, strip):
    """fetchname's strip of `strip` components: each `/` followed by something other than `/` counts one, and the name
    is what follows the last one counted."""
    kept, left = name, strip
    for i, c in enumerate(name):
        if c == "/" and i + 1 < len(name) and name[i + 1] != "/":
            left -= 1
            if left >= 0:
                kept = name[i + 1 :]
    return kept


def patch_names(text, strip):
    """Every name patch may write a file as, relative to its directory or absolute, for the patches in `text` under -p
    `strip` (None when the line gives none): the stripped name; with no -p, the last component and, for a relative
    name, the whole of it, past `a/` or `b/` in a `diff --git` patch too (module docstring)."""
    out = []
    for spelled, git in patch_headers(text):
        name = fetch_name(spelled)
        if name is None:
            continue
        if strip is not None:
            out.append(strip_name(name, strip))
            continue
        out.append(strip_name(name, 1 << 30))
        if not name.startswith("/"):
            out.append(name)
            if git and name.startswith(("a/", "b/")):
                out.append(name[2:])
    return [n for n in dict.fromkeys(out) if n]
