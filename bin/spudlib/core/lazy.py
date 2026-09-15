"""core/lazy: LazyModule and the five standard-library modules no hook imports.  Moved from bin/spud_ledger.py (SPD-065)."""

class LazyModule:
    """A standard-library module imported at its first attribute access.  No hook uses these, and importing them up
    front cost every hook run about 11 ms (SPD-016)."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        module = __import__(self._name)
        globals()[self._name] = module  # from now on the name is the module itself
        return getattr(module, attr)


argparse = LazyModule("argparse")
fractions = LazyModule("fractions")
hashlib = LazyModule("hashlib")
subprocess = LazyModule("subprocess")
tempfile = LazyModule("tempfile")
