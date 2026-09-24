"""core/lazy: LazyModule, the standard-library modules no hook imports unless its line needs one, and LazyPattern."""

import re
import sys


class LazyModule:
    """A standard-library module imported at its first attribute access.  No hook uses these, and importing them up
    front cost every hook run about 11 ms.

    `name` may be dotted, and then `alias` is the name this module binds the import to, because the rebinding below --
    what makes every access after the first a plain global read -- needs an identifier and a dotted name is not one.
    """

    def __init__(self, name, alias=None):
        self._name = name
        self._alias = alias or name

    def __getattr__(self, attr):
        __import__(self._name)
        module = sys.modules[self._name]
        globals()[self._alias] = module  # from now on the name is the module itself
        return getattr(module, attr)


argparse = LazyModule("argparse")
fractions = LazyModule("fractions")
hashlib = LazyModule("hashlib")
subprocess = LazyModule("subprocess")
tempfile = LazyModule("tempfile")
# The vault's downloads, lazy for the same reason as the five above rather than for the hook path, which
# no vault module is on: `cli/cliparser` imports every command module, so a plain `import urllib.request` in
# `commands/vaultlock` put 16 ms on every `spud` run -- every `member log` a spudagent makes -- for a command that
# downloads nothing.  Measured as +15 ms on the `board` case of tests/probes/hook_timing.py.
urllib_error = LazyModule("urllib.error", "urllib_error")
urllib_parse = LazyModule("urllib.parse", "urllib_parse")
urllib_request = LazyModule("urllib.request", "urllib_request")
# The archives the Bash hook lists (shell/archive_names, SPD-144), lazy because the hook is: tarfile and zipfile, with the
# compression modules they import, would put several milliseconds on every hook run for a line that extracts nothing.
tarfile = LazyModule("tarfile")
zipfile = LazyModule("zipfile")


class LazyPattern:
    """A regular expression compiled at its first use, for a module on the hook path whose patterns a line mostly never
    reaches (SPD-216): compiling a module's patterns at import cost every Bash and Write hook run the time of each, about
    0.8 ms for shell/positional, shell/reevaluation and shell/heredocs together, whatever the line held.

    It answers the compiled pattern's methods and attributes, `match`, `search`, `sub` and the rest, each bound once on
    this object at the first access, so every use after the first is a plain attribute read of the real method.  It is
    not a `re.Pattern`: give it to `re.match(...)` or `re.compile(...)` and it fails, so only a pattern read through its
    own methods is made one.
    """

    def __init__(self, pattern, flags=0):
        self._args = (pattern, flags)

    def __getattr__(self, attr):
        if attr.startswith("__") or attr == "_args":
            raise AttributeError(attr)
        compiled = re.compile(*self._args)
        for name in _PATTERN_ATTRIBUTES:
            setattr(self, name, getattr(compiled, name))
        return getattr(compiled, attr)


_PATTERN_ATTRIBUTES = ("match", "search", "fullmatch", "finditer", "findall", "sub", "subn", "split", "scanner",
                       "pattern", "flags", "groups", "groupindex")
