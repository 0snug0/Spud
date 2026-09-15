"""spud: the one program that writes the ledger (SPD-007, ledger CLI v1).

Run it as  python3.14 -I -S bin/spud [--as ACTOR] [--json] <command> ...

bin/spud is the launcher and this module is the program's entry (SPD-016).  Since SPD-065 the program's code is the
package spudlib beside this file: PackageFinder imports its modules from that directory alone, each with a loader of
the class that loaded this file, so the launcher caches every module's bytecode by its own rule.  main imports what the
run needs, and a hook run never imports the parser, the commands, render or import.  spud_ledger.<name> still reads
every name the program defines, from the module that defines it.  Running this file directly refuses.

The ledger is one SQLite file, <SPUD_HOME>/.spud/ledger.db, in WAL mode.
Markdown under ledger/ and reports/ is what `spud render` generates from it;
`spud import` reads the markdown-v0 files once.  Design: the ledger database
spike (docs/spikes/2026-09-12-ledger-database.md, SPD-006) as amended by the
decisions recorded on SPD-007.  Python 3.14 standard library only.

Exit codes: 0 ok, 1 error, 2 usage, 3 ownership refused, 4 limit refused,
5 state transition refused, 6 render conflict (a hand-edited file).
"""

import contextlib
import json
import os
import sqlite3
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

PACKAGE = "spudlib"


class PackageFinder:
    """Finds spudlib and its modules in the directory beside this file, and nothing else: never through sys.path or the
    working directory, so no module of the package shadows the standard library or is shadowed by it.  spudlib and its
    groups are directories, packages with no code of their own; a module gets a loader of the class that loaded this
    file: the launcher's, which caches bytecode under the home's state directory, or the suite's plain one, which writes
    none under sys.dont_write_bytecode."""

    def __init__(self, root, loader_class):
        self.root = root
        self.loader_class = loader_class

    def find_spec(self, fullname, path=None, target=None):
        if fullname != PACKAGE and not fullname.startswith(PACKAGE + "."):
            return None
        parts = fullname.split(".")[1:]
        base = os.path.join(self.root, *parts)
        if len(parts) < 2:  # spudlib, spudlib.shell
            spec = ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [base]
            return spec
        origin = base + ".py"  # spudlib.shell.walk
        spec = ModuleSpec(fullname, self.loader_class(fullname, origin), origin=origin)
        spec.has_location = True
        return spec


def install_finder():
    """Put the finder for this file's own package first on sys.meta_path, once: a second load of this file (the suite
    loads it many times) keeps the first finder and every module already imported."""
    root = os.path.join(os.path.dirname(__file__), PACKAGE)
    for finder in list(sys.meta_path):
        if type(finder).__name__ == "PackageFinder":
            if finder.root == root:
                return
            sys.meta_path.remove(finder)
            for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
                del sys.modules[name]
    sys.meta_path.insert(0, PackageFinder(root, type(__spec__.loader)))


_OWNERS = {}


def owners():
    """{name: the module of spudlib that defines it}, importing every module once.  Only an in-process reader of
    spud_ledger.<name>, the suite, builds it; a run through the launcher goes through main."""
    if not _OWNERS:
        import importlib
        import types

        root = os.path.join(os.path.dirname(__file__), PACKAGE)
        found = []
        for directory, subdirs, files in os.walk(root):
            subdirs[:] = sorted(d for d in subdirs if d.isidentifier() and not d.startswith("__"))
            for f in sorted(files):
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(directory, f[:-3]), root)
                    found.append(importlib.import_module(PACKAGE + "." + rel.replace(os.sep, ".")))
        imported = {}
        for module in found:
            for key, value in vars(module).items():
                if key.startswith("__") or (isinstance(value, types.ModuleType) and value.__name__.startswith(PACKAGE)):
                    continue
                if isinstance(value, types.ModuleType) or getattr(value, "__module__", module.__name__) != module.__name__:
                    imported.setdefault(key, module)  # a standard-library module or class the module imported
                else:
                    _OWNERS.setdefault(key, module)
        for key, module in imported.items():
            _OWNERS.setdefault(key, module)
    return _OWNERS


def __getattr__(name):
    """spud_ledger.<name>: the name as the module of spudlib that defines it holds it now."""
    owner = None if name.startswith("__") else owners().get(name)
    if owner is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return getattr(owner, name)


class HookCall:
    """What the parser gives for exactly `spud hook <event>`, the line settings sync installs: a hook run skips building
    the parser, which any other line still goes through (SPD-016)."""

    command = "hook"
    actor = None
    json = False

    def __init__(self, event, project=None):
        from spudlib.hooks import dispatch

        self.event = event
        self.project = project
        self.func = dispatch.cmd_hook


def main(argv=None):
    from spudlib.core import homeconf, kernel
    from spudlib.hooks import hookio
    from spudlib.state import actors

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) == 2 and argv[0] == "hook" and argv[1] in hookio.HOOK_EVENTS:
        args = HookCall(argv[1])
    elif len(argv) == 4 and argv[0] == "hook" and argv[1] in hookio.HOOK_EVENTS and argv[2] == "--project":  # a project's hook line (SPD-014)
        args = HookCall(argv[1], argv[3])
    else:
        from spudlib.cli import cliparser

        args = cliparser.build_parser().parse_args(cliparser.normalize_argv(argv))
    ctx = None
    try:
        home, how = homeconf.resolve_home(os.environ, Path(__file__))
        ctx = homeconf.Ctx(home, how, args.json)
        actors.ACTIVE_CTX[:] = [ctx]
        result = args.func(ctx, args)
    except kernel.SpudError as e:
        sys.stderr.write("spud: %s\n" % e.message)
        if args.json:
            payload = {"ok": False, "error": e.message, "exit": e.code}
            payload.update(e.data)
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return e.code
    except sqlite3.Error as e:
        sys.stderr.write("spud: database error: %s\n" % e)
        if args.json:
            sys.stdout.write(json.dumps({"ok": False, "error": "database error: %s" % e, "exit": kernel.EXIT_ERROR}, indent=2) + "\n")
        return kernel.EXIT_ERROR
    if result.raw is not None:
        if result.raw:
            sys.stdout.write(result.raw.rstrip("\n") + "\n")
        if result.stderr:
            sys.stderr.write(result.stderr.rstrip("\n") + "\n")
        return result.exit_code
    if args.json:
        payload = {"ok": result.exit_code == kernel.EXIT_OK}
        if result.exit_code != kernel.EXIT_OK:
            payload.update({"error": result.stderr, "exit": result.exit_code})
        payload.update(result.data)
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    elif result.text:
        sys.stdout.write(result.text.rstrip("\n") + "\n")
    if result.stderr:
        # a command that did its work and still exits non-zero (member resum leaving a sum) says why
        sys.stderr.write("spud: %s\n" % result.stderr.rstrip("\n"))
    # A recording hook that failed open left its gap in the spool; any successful command drains it.
    if args.command != "hook" and result.exit_code == kernel.EXIT_OK:
        with contextlib.suppress(Exception):
            hookio.spool_drain(ctx)
    return result.exit_code


if __name__ == "__main__":
    sys.stderr.write("spud: run the launcher, bin/spud, not %s: the hooks and the allow rules know the launcher\n" % os.path.basename(__file__))
    sys.exit(2)  # kernel.EXIT_USAGE; the refusal imports nothing, so running this file directly writes no bytecode
else:
    install_finder()  # reads no file: from here on spudlib's modules import, as mock.patch("spudlib.<group>.<module>.<name>") needs
