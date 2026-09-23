"""Run a shell snippet in zsh and bash inside a macOS sandbox, and print what each shell did (SPD-094).

  python3.14 -I -S tests/probes/shell_probe.py FILE
  python3.14 -I -S tests/probes/shell_probe.py < FILE
  python3.14 -I -S tests/probes/shell_probe.py [--timeout SECONDS] [--profile] [FILE | -]

The shell modules' comments rest on probes ("probed in zsh 5.9 -f -o nobareglobqual and bash 3.2"), and a spudagent in
a worktree is refused every command that runs zsh or bash, because what a shell reads cannot be shown not to run git.
This probe is the way a member asks the shells anyway: it reads the snippet from FILE, or from standard input when
FILE is `-` or absent, and runs it, one shell after another, as a script under

  zsh -f -o nobareglobqual   the grammar the hook's reader follows (bare glob qualifiers off)
  zsh -f                     zsh's own default, bare glob qualifiers on
  /bin/bash --noprofile --norc   bash 3.2 on this Mac

each in a fresh directory of its own under one `mktemp -d`, which is its working directory, HOME and TMPDIR, and which
the probe removes when it is done.  For each shell it prints the shell's version, the exit status, standard output and
standard error, labelled; the directory's real path in any of them reads `<probe-dir>`, so two runs print the same.
Standard input is /dev/null.  A shell that runs past `--timeout` (10 seconds) is killed with its whole process group,
and so is anything it left running.  The probe exits 0 when every shell ran, whatever the snippet's own statuses.

**The guard.**  The snippet is arbitrary shell, so every shell runs under `sandbox-exec` with the profile this file
builds (`--profile` prints it and runs nothing).  The profile denies by default and allows only:

- reading files, except anywhere under the caller's home (its checkouts, keys and ledger), and writing only inside
  that shell's own directory (plus /dev/null, /dev/tty, /dev/fd);
- executing only files under /bin and /usr/bin, and of those never git or its helpers, xcrun, xcode-select or
  xcodebuild, python, pip or pydoc, perl, ruby, tclsh, wish, swift or osascript (an interpreter could leave the
  process group), sudo, su, login, open or launchctl.  Every other place is refused, so Xcode's and the Command Line
  Tools' git, Homebrew's, python3.14, the spud launcher (bin/spud) and anything the snippet writes or builds into its
  directory cannot run.  A refusal is the kernel's, at exec, so it holds for `/usr/bin/git` by path, `xcrun git`,
  `exec git`, `env git`, a PATH the snippet sets, a copy of git, and any program's child alike; and every /usr/bin
  tool that shims to Xcode (make, clang) fails when it reaches for the real binary;
- forking, and signalling and inspecting its own processes.

Everything else is denied: the network (no socket at all, and no name resolution), Mach and XPC services (so `open`
and launchd cannot start a process outside the sandbox), and writes anywhere else, the caller's home included.  A hard
link from the directory to a file outside it is refused too (`file-link`), so nothing outside can be written through
one.  As defence in depth the environment is replaced, not filtered: PATH=/usr/bin:/bin, HOME and TMPDIR the shell's
own directory, LANG=C, and no GIT_*, BASH_ENV, ENV, SPUD_HOME or anything else from the caller.

What the guard costs a probe: an answer only Directory Services has is missing (`id -un` prints the uid; `~root` still
expands, from /etc/passwd), and zsh's background jobs print `nice(5) failed` (its BG_NICE option; the job still runs).  Neither touches the grammar a probe is for.

**No sandbox, no run.**  When sandbox-exec is missing or cannot apply the profile (inside another sandbox, say), the
probe says so and exits 3 without running the snippet in any shell.  Usage errors exit 2.  The guard's cases are pinned
in tests/test_shell_probe.py, each failing in every shell: git by absolute path, through xcrun, env, exec and a PATH
the snippet sets, a copy of git; python and the launcher; a write to the caller's home, to another directory, through
a hard or symbolic link; reading the caller's home; and a connection to a socket the test listens on.

How the Bash hook reads it (SPD-094, checked from a worktree): the probe run on a FILE, or with `< FILE`, is allowed
whatever the file says, since the hook reads no file; a here-document fed to it is allowed unless its text names git.
"""

import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
MASK = "<probe-dir>"
DEFAULT_TIMEOUT = 10.0

# (label, argv before the script path, argv that prints the version)
SHELLS = [
    ("zsh -f -o nobareglobqual", ["/bin/zsh", "-f", "-o", "nobareglobqual"], ["/bin/zsh", "--version"]),
    ("zsh -f", ["/bin/zsh", "-f"], ["/bin/zsh", "--version"]),
    ("bash", ["/bin/bash", "--noprofile", "--norc"], ["/bin/bash", "--version"]),
]

# Executables under /bin and /usr/bin the snippet may never run.  Anything outside /bin and /usr/bin is refused already.
DENIED_EXEC = [
    r"^/usr/bin/git",  # git, git-receive-pack, git-shell, git-upload-archive, git-upload-pack, and any git-* to come
    r"^/usr/bin/(xcrun|xcode-select|xcodebuild)$",
    r"^/usr/bin/(python|pip|pydoc)",
    r"^/usr/bin/(perl|ruby|tclsh|wish|swift|osascript)",  # interpreters that can leave the process group (setsid)
    r"^/usr/bin/(sudo|su|login|open|sandbox-exec)$",
    r"^/(bin|usr/bin)/launchctl$",  # launchctl ships at /bin/launchctl, not /usr/bin/launchctl
]
READ_GRACE = 2.0  # seconds to wait for output once the group is killed

PROFILE = """\
(version 1)
(deny default)

; reading is allowed everywhere the shells, their libraries and the dyld cache live, but not in the caller's home
; (its checkouts, keys and ledger); the probe's own directory is readable wherever the caller's temp directory is
(allow file-read*)
(deny file-read* (subpath (param "USER_HOME")))
(allow file-read* (subpath (param "ROOT")))

; writing only inside this shell's own directory, and the character devices a script writes to
(allow file-write*
  (subpath (param "WORK"))
  (literal "/dev/null")
  (literal "/dev/tty")
  (literal "/dev/dtracehelper")
  (subpath "/dev/fd"))
(allow file-ioctl (literal "/dev/tty") (literal "/dev/null") (subpath "/dev/fd"))

; a hard link inside the directory to a file outside it would let a write reach that file
(deny file-link)

; executing only /bin and /usr/bin, never git, xcrun, python or a program that starts another outside the sandbox
(allow process-fork)
(allow process-exec (subpath "/bin") (subpath "/usr/bin"))
(deny process-exec
%(denied)s)

(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)

; the network, Mach and XPC services, and everything else stay denied by default; said again for the reader
(deny network*)
(deny mach-lookup)
"""


def profile():
    """The SBPL profile every shell runs under; its one parameter, WORK, is the shell's own directory."""
    return PROFILE % {"denied": "\n".join('  (regex #"%s")' % pattern for pattern in DENIED_EXEC)}


def sandboxed(root, work, argv):
    """argv under sandbox-exec with the profile: ROOT the probe's directory, WORK this shell's own inside it, USER_HOME
    the caller's home.  All are real paths, because the kernel matches real paths."""
    return [SANDBOX_EXEC, "-D", "ROOT=" + root, "-D", "WORK=" + work, "-D", "USER_HOME=" + user_home(),
            "-p", profile()] + list(argv)


def user_home():
    """The caller's home as a real path, from the password database rather than $HOME, so the deny holds even when a
    caller's environment sets HOME elsewhere; read before the environment is replaced."""
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def environment(work):
    """The whole environment a shell gets: replaced, never inherited, so no GIT_*, BASH_ENV or ENV reaches it."""
    return {"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work + "/", "PWD": work, "LANG": "C", "TERM": "dumb",
            "SHELL": "/bin/sh", "USER": "probe", "LOGNAME": "probe"}


def drain(stream, chunks):
    for chunk in iter(lambda: stream.read1(65536), b""):
        chunks.append(chunk)


def run(argv, root, work, timeout):
    """(status, stdout, stderr) of argv sandboxed in `work`.  status is the shell's own exit status, or 'timeout after
    N s'.  When the shell exits, or times out, its whole process group is killed, so a job it left in the background
    neither outlives it nor holds its output open."""
    proc = subprocess.Popen(sandboxed(root, work, argv), cwd=work, env=environment(work), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    out, err = [], []
    readers = [threading.Thread(target=drain, args=(proc.stdout, out), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        status = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        status = "timeout after %g s" % timeout
    kill_group(proc.pid)
    proc.wait()
    for reader in readers:
        reader.join(READ_GRACE)
    if any(reader.is_alive() for reader in readers):  # a process that left the group still holds the pipe
        err.append(b"\n[shell_probe: output cut: a process outside the shell's group still held it open]\n")
    return status, b"".join(out).decode("utf-8", "replace"), b"".join(err).decode("utf-8", "replace")


def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def sandbox_works(root, work):
    """None when sandbox-exec applies the profile here, else why not."""
    if not os.path.exists(SANDBOX_EXEC):
        return "%s does not exist (this probe runs on macOS only)" % SANDBOX_EXEC
    try:
        status, out, err = run(["/bin/echo", "sandboxed"], root, work, DEFAULT_TIMEOUT)
    except OSError as e:
        return "%s could not run: %s" % (SANDBOX_EXEC, e)
    if status != 0 or out != "sandboxed\n":
        return "%s could not apply the profile: status %s, %s" % (SANDBOX_EXEC, status, (err or out).strip())
    return None


def remove(path):
    """Remove the probe's directory, whatever modes or flags the snippet left in it.  This runs outside the sandbox, so
    it never follows a symbolic link: every chmod and chflags is on the entry itself, and rmtree does not follow one."""
    stack = [path]
    while stack:
        directory = stack.pop()
        for fix in (lambda p: os.chflags(p, 0, follow_symlinks=False), lambda p: os.chmod(p, 0o700, follow_symlinks=False)):
            try:
                fix(directory)
            except OSError:
                pass
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
            elif not entry.is_symlink():
                try:
                    os.chflags(entry.path, 0, follow_symlinks=False)
                except OSError:
                    pass
    try:
        shutil.rmtree(path)
    except OSError as e:
        sys.stderr.write("shell_probe: could not remove %s: %s\n" % (path, e))


def masked(text, root):
    return text.replace(root, MASK)


def probe(snippet, timeout=DEFAULT_TIMEOUT):
    """Run `snippet` (bytes) in every shell.  Returns (None, [(label, version, status, stdout, stderr)]), or
    (why, []) when the sandbox cannot be used, in which case no shell has run it."""
    root = os.path.realpath(tempfile.mkdtemp(prefix="spud-shell-probe-"))
    try:
        check = os.path.join(root, "check")
        os.mkdir(check)
        why = sandbox_works(root, check)
        if why:
            return why, []
        script = os.path.join(root, "snippet.sh")
        with open(script, "wb") as f:
            f.write(snippet)
        os.chmod(script, 0o444)  # outside every shell's directory, so no shell can change what the next one reads
        results = []
        for n, (label, argv, version_argv) in enumerate(SHELLS, 1):
            work = os.path.join(root, "shell%d" % n)
            os.mkdir(work)
            _, version, _ = run(version_argv, root, work, DEFAULT_TIMEOUT)
            status, out, err = run(argv + [script], root, work, timeout)
            results.append((label, version.strip().splitlines()[0] if version.strip() else "?", status,
                            masked(out, root), masked(err, root)))
        return None, results
    finally:
        remove(root)


def report(source, snippet, results, out=sys.stdout):
    write = out.write
    write("shell probe (SPD-094): %s, %d bytes, sandboxed: exec only /bin and /usr/bin, never git, xcrun or python; "
          "writes only its own directory; no network\n" % (source, len(snippet)))
    for label, version, status, stdout, stderr in results:
        write("\n== %s  (%s)\n" % (label, version))
        write("status: %s\n" % status)
        for name, text in (("stdout", stdout), ("stderr", stderr)):
            if not text:
                write("-- %s: (empty)\n" % name)
                continue
            write("-- %s:\n%s" % (name, text))
            if not text.endswith("\n"):
                write("\n-- (no newline at end of %s)\n" % name)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    timeout, source = DEFAULT_TIMEOUT, None
    while argv:
        arg = argv.pop(0)
        if arg in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        if arg == "--profile":
            sys.stdout.write(profile())
            return 0
        if arg == "--timeout" and argv:
            try:
                timeout = float(argv.pop(0))
            except ValueError:
                sys.stderr.write("shell_probe: --timeout takes a number of seconds\n")
                return 2
            continue
        if source is not None or (arg.startswith("-") and arg != "-"):
            sys.stderr.write("shell_probe: usage: shell_probe.py [--timeout SECONDS] [--profile] [FILE | -]\n")
            return 2
        source = arg
    if source in (None, "-"):
        source, snippet = "standard input", sys.stdin.buffer.read()
    else:
        try:
            with open(source, "rb") as f:
                snippet = f.read()
        except OSError as e:
            sys.stderr.write("shell_probe: %s\n" % e)
            return 2
    why, results = probe(snippet, timeout)
    if why:
        sys.stderr.write("shell_probe: no sandbox, so no shell ran the snippet: %s\n" % why)
        return 3
    report(source, snippet, results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
