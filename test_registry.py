#!/usr/bin/env python3
"""Module-registry completeness: every module in proxylab/ is reachable through
BOTH registries.

WHAT THIS SUITE IS FOR. CLAUDE.md promises two lookup contracts:
  * `proxylab.__init__._MODULES` — the LAZY package registry, so
    `from proxylab import billing` boots billing+core+writer and nothing else.
  * `logproxy._SUBMODULES` — the eager shim's PEP-562 search order, so a
    REBINDABLE GLOBAL read through the shim (`lp.SORT_TOOLS`) reads LIVE.

Both are hand-maintained tuples, and on 2026-08-11 both had drifted: `hints`,
`hints_native`, `tokest` and `fold` were in neither, `pot` and `bake_session`
were in only one. The failure is SILENT and asymmetric — `lp.SORT_TOOLS`
returned True while `lp.HINTS` and `lp.FOLD_EXPERIMENTAL` returned False, not
because the flags were off but because the shim could not see the modules that
own them. Code branching on `lp.<FLAG>` therefore took the wrong branch with no
error anywhere.

THE ASSERTION IS THE **SET**, NOT THE NAMES. A per-name check ("is `fold`
listed?") is exactly what allowed the drift: it only ever covers modules
someone remembered to add, so the next new module is invisible again. These
checks diff against the DIRECTORY LISTING, so a module added tomorrow fails the
gate the day it lands and the fix is one tuple edit.

Could these come back false? Yes — delete any name from either tuple, or add a
new proxylab/*.py without registering it, and the set diff is non-empty.

Run: python3 test_registry.py
"""
import importlib
import pathlib
import sys

PKG = pathlib.Path(__file__).resolve().parent / "proxylab"

failures = []
checks = 0


def check(cond, label):
    global checks
    checks += 1
    if not cond:
        failures.append(label)
        print(f"FAIL: {label}")


def on_disk():
    """Every importable module in the package, except the package itself."""
    return {
        p.stem for p in PKG.glob("*.py")
        if p.stem != "__init__" and not p.stem.startswith("_")
    }


def main():
    disk = on_disk()
    check(bool(disk), "sanity: the package directory contains modules")
    # Guard the guard: if the glob silently returned nothing, every set
    # comparison below would pass vacuously (the absence-passes trap that
    # test_release_sh.sh's header warns about).
    check(len(disk) >= 20, f"sanity: expected >=20 modules on disk, found {len(disk)}")

    import proxylab
    import logproxy

    # --- the lazy package registry ------------------------------------------
    lazy = set(proxylab._MODULES)
    missing = disk - lazy
    extra = lazy - disk
    check(not missing, f"proxylab._MODULES is missing: {sorted(missing)}")
    check(not extra, f"proxylab._MODULES lists non-existent: {sorted(extra)}")

    # --- the eager shim search order ----------------------------------------
    shim = {m.__name__.rsplit(".", 1)[-1] for m in logproxy._SUBMODULES}
    missing = disk - shim
    extra = shim - disk
    check(not missing, f"logproxy._SUBMODULES is missing: {sorted(missing)}")
    check(not extra, f"logproxy._SUBMODULES lists non-existent: {sorted(extra)}")

    # --- the contracts actually hold, not just the bookkeeping --------------
    # Every registered name must really resolve through the package __getattr__.
    for name in sorted(disk):
        try:
            mod = getattr(proxylab, name)
            check(mod is importlib.import_module(f"proxylab.{name}"),
                  f"proxylab.{name} resolves to the right module")
        except AttributeError:
            check(False, f"proxylab.{name} does not resolve via __getattr__")

    # The shim's REBINDABLE-GLOBAL contract: a module-level flag must be
    # readable through the shim. This is the defect's observable consequence,
    # not a restatement of the tuple contents — it fails if a module is listed
    # but unimportable, too. One flag per previously-missing module, plus a
    # positive control from a module that was never missing (if the control
    # ever fails, the harness is broken, not the registry).
    flags = [
        ("SORT_TOOLS", "transforms"),       # positive control — always worked
        ("HINTS", "hints"),
        ("FOLD_EXPERIMENTAL", "fold"),
    ]
    for flag, owner in flags:
        mod = importlib.import_module(f"proxylab.{owner}")
        if not hasattr(mod, flag):
            continue  # flag renamed/retired; the set checks above still bind
        check(getattr(logproxy, flag) == getattr(mod, flag),
              f"lp.{flag} reads through to proxylab.{owner}.{flag}")

    print(f"\n{checks} checks, {len(failures)} failed")
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
