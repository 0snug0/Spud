"""core/lazy: LazyModule and the standard-library modules no hook imports.  Moved from bin/spud_ledger.py (SPD-065)."""

import sys


class LazyModule:
    """A standard-library module imported at its first attribute access.  No hook uses these, and importing them up
    front cost every hook run about 11 ms (SPD-016).

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
# SPD-156, the vault's downloads, and lazy for the same reason as the five above rather than for the hook path, which
# no vault module is on: `cli/cliparser` imports every command module, so a plain `import urllib.request` in
# `commands/vaultlock` put 16 ms on every `spud` run -- every `member log` a spudagent makes -- for a command that
# downloads nothing.  Measured on SPD-156 as +15 ms on the `board` case of tests/probes/hook_timing.py.
urllib_error = LazyModule("urllib.error", "urllib_error")
urllib_parse = LazyModule("urllib.parse", "urllib_parse")
urllib_request = LazyModule("urllib.request", "urllib_request")
