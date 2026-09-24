"""shell/program_writes: the write markers an inline program's text shows, tabled per interpreter family (SPD-175).

SPD-150 refused a member every interpreter run whose program the line spells, because the hook read none of that
program.  Its first two days of refusals held 71 programs that only read and print, 26 find-and-replace edits inside
the member's own globs, 6 writes to a temp directory and 3 that wrote nothing at all -- and none that would have written
outside the member's globs, the one thing the rule is for.  Eric's call (2026-09-22): refuse only a program whose text
visibly writes.  shell/inline_programs hands this module the text the line spells -- an option's value, a
here-document, a here-string, the text a printer pipes -- and first_write answers the first write marker in it, or None.

Each family's markers are what its own documentation says writes a file, removes or renames one, makes a directory,
runs another program, or evaluates text the scan never sees:

- **python** (the Python Library Reference: pathlib, os, shutil, io, tempfile, subprocess, built-in functions):
  Path.write_text/write_bytes and the Path methods that write (touch, mkdir, unlink, rmdir, rename, replace with its one
  argument, symlink_to, hardlink_to, chmod), open and its kin (io.open, os.fdopen, gzip.open, codecs.open, tarfile.open,
  ZipFile) with a mode that writes (a `w`, `a`, `x` or `+`), os's calls that write, remove, rename, make directories or
  run programs, the O_* flags that open for writing, shutil's copies, moves and removals, subprocess, pty, ctypes,
  importlib and __import__, exec and eval, tempfile's files, urlretrieve, extractall, logging's FileHandler,
  fileinput's inplace, a database module (sqlite3, shelve, dbm), and a data frame's or an array's save.
- **node, bun and deno** (the Node.js fs, child_process and vm documentation, Bun's and Deno's runtime APIs): the fs
  calls that write, append, copy, remove, rename, make directories, link or change modes, a write stream, fs.open with a
  flag that writes, child_process and the other modules that run code (vm, worker_threads, cluster, inspector),
  Deno's writes, removals, renames, commands and opens, Bun.write, Bun.spawn and Bun's shell, eval and Function.
- **perl** (perlfunc, perlop, perlrun): open for `>`, `>>`, `+<` or a pipe, sysopen and syswrite, unlink, rename,
  mkdir, rmdir, symlink, link, chmod, chown, utime, truncate, system, exec, fork, backticks and qx, File::Copy,
  File::Path and File::Temp, IPC::Open2/3, a string eval, and `$^I`; and the -i switch itself, which
  shell/inline_programs reads among the options.
- **ruby** (the Ruby core and standard library documentation): File's and IO's writes, deletions, renames, links and
  mode changes, File.open and open with a mode that writes or a `|` command, Dir's mkdir, rmdir and mktmpdir,
  FileUtils, Tempfile, Open3, PTY, system, exec, spawn, fork, backticks and %x, eval and its kin, a Pathname's
  write, rename, unlink, mkpath and rmtree, and `$-i`; and -i among the options, as perl's.

The scan is a nudge against the plain spellings members actually use, not a wall: a program from a file already runs
unread, and one that hides its writes -- a computed attribute, a module name built at run time, an alias of os -- is
not what it is for.  It reads the text whole, strings and comments too, so a marker in a string earns a refusal a
program that never runs it did not need; that errs the safe way.  A family with no row here (osascript, php, lua,
Rscript, swift) is no family this module can answer for, and shell/inline_programs keeps refusing its programs.

Every pattern compiles at its first use (core/lazy.LazyPattern, SPD-216): this module is on the hook path and a line
with no inline program never reaches it.  At the package's 250-line look-again point it is one body of data read as
one -- a table per family, each its documentation's -- with the one reader of an open call's arguments they share.
"""

import re

from ..core import lazy

# python: a marker is a match of one of these, or an open-like call whose mode writes (_open_writes), or a Path.replace
# (a `.replace(` with one argument: str.replace always has two).
PYTHON_MARKERS = lazy.LazyPattern(r"""
    \bwrite_(?:text|bytes)\b
  | \.(?:touch|mkdir|unlink|rmdir|rename|symlink_to|hardlink_to|link_to|chmod|lchmod)\s*\(
  | \bos\s*\.\s*(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork\w*|remove|unlink|rmdir|removedirs|rename|renames
                 |replace|mkdir|makedirs|mkfifo|mknod|symlink|link|truncate|ftruncate|chmod|lchmod|fchmod|chown|lchown
                 |fchown|chflags|lchflags|utime|write|pwrite|writev|pwritev|sendfile|copy_file_range|open|setxattr
                 |removexattr)\b
  | \bO_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)\b
  | \bshutil\s*\.\s*(?:copy\w*|move|rmtree|chown|make_archive|unpack_archive)\b
  | \bfrom\s+(?:os|shutil)\s+import\b[^\n;]*?(?:\*|\b(?:system|popen|exec\w*|spawn\w*|remove|unlink|rmdir|removedirs
                 |rename|renames|replace|mkdir|makedirs|symlink|link|truncate|chmod|chown|utime|write|open|copy\w*|move
                 |rmtree|make_archive|unpack_archive)\b)
  | \bimport\s+(?:os|shutil)\s+as\b
  | \b(?:subprocess|pty|ctypes|importlib|sqlite3|shelve|dbm|__import__)\b
  | (?<![\w.])(?:exec|eval)\s*\(
  | \btempfile\s*\.\s*(?:mk\w+|NamedTemporaryFile|TemporaryFile|SpooledTemporaryFile|TemporaryDirectory)\b
  | \b(?:urlretrieve|extractall|FileHandler)\b
  | \binplace\s*=\s*(?:True|1)\b
  | \.to_(?:csv|excel|parquet|pickle|feather|hdf|stata)\s*\(
  | \.save\w*\s*\(
""", re.X)
# node, bun and deno.  Backticks are template literals here, and `.exec(` is RegExp's: neither is a marker.
JS_MARKERS = lazy.LazyPattern(r"""
    \b(?:writeFile|appendFile|copyFile|cpSync|rmSync|rmdir|unlink|rename|mkdir|mkdtemp|symlink|lchown|chown|lchmod
       |chmod|lutimes|futimes|utimes|ftruncate|truncate|createWriteStream|writeSync|writev|execSync|execFileSync
       |spawnSync)\w*
  | \bchild_process\b
  | ['"](?:node:)?(?:vm|worker_threads|cluster|inspector)['"]
  | \bfs\w*\s*\.\s*(?:cp|rm|link|write)\b
  | (?<![\w.$])(?:cp|rm)\s*\(
  | \bDeno\s*\.\s*(?:write\w*|remove\w*|rename\w*|mkdir\w*|copyFile\w*|create\w*|symlink\w*|link\w*|truncate\w*
                   |chmod\w*|chown\w*|makeTemp\w*|utime\w*|open\w*|run|Command)\b
  | \bBun\s*\.\s*(?:write|spawn\w*|\$)
  | \$\s*`
  | \.writer\s*\(
  | (?<![\w.$])eval\s*\(
  | \bnew\s+Function\b
  | (?<![\w.$])Function\s*\(
  | \bprocess\s*\.\s*(?:binding|dlopen|_linkedBinding)\b
  | \bO_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)\b
""", re.X)
# perl.  The look-behind keeps a variable (`$link`), a method (`->link`), a package's name (`Foo::link`) and a file
# test (`-link`) out; readlink is no link.
PERL_MARKERS = lazy.LazyPattern(r"""
    (?<![\w$@%&:>-])(?:unlink|rename|mkdir|rmdir|symlink|link|chmod|chown|utime|truncate|system|exec|fork|sysopen
                      |syswrite|dbmopen|qx)\b
  | `
  | \b(?:File::Copy|File::Path|File::Temp|IPC::Open\d|IPC::Run\w*|IPC::Cmd)\b
  | (?<![\w$@%&:>-])(?:copy|move|make_path|remove_tree|mkpath|rmtree|tempfile|tempdir)\s*\(
  | (?<![\w$@%&:>-])eval\b(?!\s*\{)
  | \$\^I\b
""", re.X)
# ruby.  Hash#delete, Array#delete and Float#truncate are no writes, so only File's and Dir's are markers.
RUBY_MARKERS = lazy.LazyPattern(r"""
    \b(?:File|IO)\s*\.\s*(?:write|binwrite|delete|unlink|rename|symlink|link|chmod|lchmod|chown|lchown|truncate|utime
                          |mkfifo|copy_stream|popen)\b
  | \bDir\s*\.\s*(?:mkdir|rmdir|delete|unlink|mktmpdir)\b
  | \b(?:FileUtils|Tempfile|Open3|PTY)\b
  | \b(?:Process|Kernel)\s*\.\s*(?:spawn|system|exec)\b
  | (?<![\w.:$@])(?:system|exec|spawn|fork|syscall|eval|instance_eval|class_eval|module_eval)\b
  | `
  | %x[({\[<]
  | (?<!stdout)(?<!STDOUT)(?<!stderr)(?<!STDERR)\.(?:bin)?write\b
  | \.(?:unlink|rename|mkpath|rmtree|make_symlink|make_link|rmdir|mkdir)\b
  | \$-i\b
""", re.X)

# A call that opens a file, whose mode _open_writes reads: open and its kin (fdopen, popen, urlopen, gzip.open,
# fs.openSync), File.new and IO.new, and the zip and tar classes.
OPEN_CALL = lazy.LazyPattern(r"\b(?:\w*open(?:Sync)?|File\s*\.\s*new|IO\s*\.\s*new|ZipFile|TarFile)\b")
# A mode that writes, as python's open, node's fs flags and ruby's File.open spell one: a `w`, `a`, `x` or `+` among
# the mode letters, with ruby's `:encoding` or tarfile's `:gz` after it.
WRITE_MODE = lazy.LazyPattern(r"[rwabxtsU+]*[wax+][rwabxtsU+]*(?::[\w:-]*)?")
KEYWORD = lazy.LazyPattern(r"(\w+)\s*(?:=(?!=)|:(?!:))\s*")
PATH_REPLACE = lazy.LazyPattern(r"\.replace\s*\(")
_QUOTES = "'\"`"


class Family:
    """One family's markers: `markers`, the pattern; `letters`, whether a mode spelled in letters writes (python's,
    node's, ruby's); `symbols`, whether one spelled in perl's symbols does (`>`, `>>`, `+<`, a `|` command: perl's and
    ruby's open); `bare`, whether an open call may go without parentheses (perl, ruby)."""

    __slots__ = ("markers", "letters", "symbols", "bare")

    def __init__(self, markers, letters, symbols, bare):
        self.markers, self.letters, self.symbols, self.bare = markers, letters, symbols, bare


FAMILIES = {
    "python": Family(PYTHON_MARKERS, True, False, False),
    "js": Family(JS_MARKERS, True, False, False),
    "perl": Family(PERL_MARKERS, False, True, True),
    "ruby": Family(RUBY_MARKERS, True, True, True),
}


def first_write(family, text):
    """The first write marker `text` shows for this family, as the text spells it (a mode as `open ... "w"`), or None."""
    fam = FAMILIES[family]
    m = fam.markers.search(text)
    if m:
        return " ".join(m.group(0).split())
    for m in OPEN_CALL.finditer(text):
        args = _call_args(text, m.end(), fam.bare)
        if args is None:
            continue
        mode = _open_writes(fam, args, text[:m.start()].rstrip().endswith("."))
        if mode is not None:
            return "%s ... %s" % (" ".join(m.group(0).split()), mode)
    if family == "python":
        for m in PATH_REPLACE.finditer(text):
            args = _call_args(text, m.end() - 1, False)
            if args is not None and len(args) == 1:
                return ".replace("
    return None


def _open_writes(fam, args, method):
    """The literal mode, as spelled with its quotes, by which an open call with these arguments writes, or None.

    Letters: a `mode`/`flag`/`flags` keyword, or a positional argument after the first -- or the first where it is the
    only one of a method call (`Path.open('w')`); the first of a function is the path.  Symbols: any literal argument,
    since perl's two-argument open puts the mode at the front of the name (`open(F, ">x")`) and ruby's open runs a
    `|` command from its first."""
    positional_args = []
    for arg in args:
        kw = KEYWORD.match(arg)
        if kw and kw.group(1) in ("mode", "flag", "flags"):
            lit = _literal(arg[kw.end():])
            if fam.letters and lit is not None and WRITE_MODE.fullmatch(lit[1:-1]):
                return lit
            continue
        if kw is None:
            positional_args.append(arg)
    for i, arg in enumerate(positional_args):
        lit = _literal(arg)
        if lit is None:
            continue
        body = lit[1:-1]
        if fam.letters and (i >= 1 or (method and len(positional_args) == 1)) and WRITE_MODE.fullmatch(body):
            return lit
        if fam.symbols and (body.lstrip()[:1] in (">", "+", "|") or body.lstrip().startswith("-|")
                            or body.rstrip().endswith("|")):
            return lit
    return None


def _literal(arg):
    """The argument as spelled, where it is one quoted string and nothing else; else None."""
    arg = arg.strip()
    if len(arg) >= 2 and arg[0] in _QUOTES and arg[-1] == arg[0] and arg[0] not in arg[1:-1]:
        return arg
    return None


def _call_args(text, at, bare):
    """The top-level arguments of the call whose name ends at `at`, each as spelled: inside the parentheses that follow,
    or -- where `bare` allows a call without them -- to the end of the statement (a newline, `;`, a block's `{`, `do`,
    or perl's `or`).  None where no call follows.  Quoted text is skipped whole, so a comma or a bracket in a string
    splits nothing; the reading is a scan, not a parse, and a program that defeats it is one that hides its writes."""
    i, n = at, len(text)
    while i < n and text[i] in " \t":
        i += 1
    paren = i < n and text[i] == "("
    if paren:
        i += 1
    elif not bare or i >= n or text[i] in "\n;=.":
        return None
    args, start, depth = [], i, 0
    while i < n:
        c = text[i]
        if c in _QUOTES:
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{" and (paren or c != "{"):
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (c == "," or (not paren and (c in "\n;{" or _word_at(text, i, ("do", "or", "and"))))):
            args.append(text[start:i].strip())
            if c != ",":
                return [a for a in args if a]
            start = i + 1
        i += 1
    args.append(text[start:i].strip())
    return [a for a in args if a]


def _word_at(text, i, words):
    """Whether one of `words` stands whole at `i` (a blank before it, and no word character after)."""
    if i == 0 or text[i - 1] not in " \t":
        return False
    for w in words:
        end = i + len(w)
        if text.startswith(w, i) and (end >= len(text) or not (text[end].isalnum() or text[end] == "_")):
            return True
    return False
