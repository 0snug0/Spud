"""The real download of every file `share/obsidian.lock.json` pins, checked against its SHA-256 (SPD-156).

  python3.14 -I -S tests/probes/vault_download.py [LOCK] [DIR]

LOCK is the lock to read, `<repo>/share/obsidian.lock.json` by default.  DIR is where the files land: a scratch
directory of its own by default, made under the system temporary directory and removed at exit; name one to keep what
it fetched (it is created if it does not exist, and never removed).  Nothing else on the machine is read or written --
no home, no database, no `.obsidian/` anywhere -- and the probe runs no `spud` command: it reads the lock and fetches.

**This is the one thing in the repository that reaches the network on purpose.**  The suite cannot: every download in
the program goes through `commands/vaultlock.download`, and `tests/helpers.Home` sets SPUD_VAULT_DOWNLOADS=off on every
scratch home it builds.  So the hashes in the lock are checked against the real releases here and nowhere else, which is
why this runs once before each landing that changes the lock.  It clears SPUD_VAULT_DOWNLOADS for its own run, so a
shell that has one set still makes real requests.

It prints one line per file -- the plugin or theme, the file, its size and `ok` or what went wrong -- and a last line
naming how many of how many matched.  Exit 0 when every pinned file downloaded and hashed as the lock says; exit 1
otherwise, naming each one that did not.  A failure is real news: it means a release was replaced or withdrawn
upstream, and `spud vault install` will refuse that plugin by name until a ticket runs `spud vault capture`.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_LOCK = os.path.join(ROOT, "share", "obsidian.lock.json")


def load_vaultlock():
    """`commands/vaultlock`, loaded the way the suite loads the program: through the entry, which installs the finder
    that answers `spudlib.*` from beside it.  The probe uses the program's own `download`, never a second copy of it."""
    import importlib
    import importlib.machinery
    import importlib.util

    sys.dont_write_bytecode = True  # the repository ends every run with no bytecode (the spudlib-modules skill, §4)
    entry = os.path.join(ROOT, "bin", "spud_ledger.py")
    loader = importlib.machinery.SourceFileLoader("spud_ledger", entry)
    spec = importlib.util.spec_from_loader("spud_ledger", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return importlib.import_module("spudlib.commands.vaultlock")


def main(argv):
    lock_path = argv[0] if argv else DEFAULT_LOCK
    into, keep = (argv[1], True) if len(argv) > 1 else (None, False)
    os.environ.pop(vaultlock.DOWNLOAD_ENV, None)  # a shell with SPUD_VAULT_DOWNLOADS set still downloads for real
    with open(lock_path, encoding="utf-8") as f:
        lock = json.load(f)
    problems = vaultlock.lock_problems(lock)
    if problems:
        print("%s is not a usable lock: %s" % (lock_path, "; ".join(problems)))
        return 1
    directory = into or tempfile.mkdtemp(prefix="spud-vault-download-")
    os.makedirs(directory, exist_ok=True)
    print("lock  %s" % lock_path)
    print("into  %s%s" % (directory, "" if keep else " (removed at exit)"))
    ok, bad, started = 0, [], time.monotonic()
    try:
        for kind, name, entry in vaultlock.locked(lock):
            print("%s %s %s (%s)" % (kind, name, entry["version"], entry["repo"]))
            for f in entry["files"]:
                where = os.path.join(directory, kind, name, f["name"])
                os.makedirs(os.path.dirname(where), exist_ok=True)
                try:
                    data = vaultlock.download(f["url"])
                except vaultlock.VaultDownloadError as e:
                    bad.append("%s %s %s: %s" % (kind, name, f["name"], e))
                    print("    %-14s --      failed: %s" % (f["name"], e))
                    continue
                got = hashlib.sha256(data).hexdigest()
                with open(where, "wb") as out:
                    out.write(data)
                if got == f["sha256"]:
                    ok += 1
                    print("    %-14s %7d bytes  ok" % (f["name"], len(data)))
                else:
                    bad.append("%s %s %s: sha256 %s, the lock pins %s" % (kind, name, f["name"], got, f["sha256"]))
                    print("    %-14s %7d bytes  SHA-256 %s, the lock pins %s" % (f["name"], len(data), got, f["sha256"]))
    finally:
        if not keep:
            shutil.rmtree(directory, ignore_errors=True)
    total = ok + len(bad)
    for line in bad:
        print("failed: %s" % line)
    print("%s: %d of %d pinned file(s) downloaded and matched in %.1f s"
          % ("OK" if not bad else "FAILED", ok, total, time.monotonic() - started))
    return 0 if not bad else 1


vaultlock = load_vaultlock()

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
