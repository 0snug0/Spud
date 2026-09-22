"""commands/vaultlock: the Obsidian vault the tool ships -- what `share/obsidian/` and `share/obsidian.lock.json` hold,
the one function every download goes through, and the comparisons `vault install`, `vault capture` and `doctor` share
(SPD-156, the portable-home design sections 1 to 3 read against `share/`).

Four facts about Obsidian this module is built on, each confirmed against Eric's own vault rather than reasoned about:

- **Obsidian installs no plugin because a file lists one.**  `community-plugins.json` is the list of plugins it *enables*;
  it loads only the ones whose files are already under `.obsidian/plugins/<id>/`.  So the tool downloads them, and the
  lock is what says which bytes.
- **Obsidian appends `\\n/* nosourcemap */` to every `main.js` it installs.**  An installed plugin file is therefore never
  byte-identical to its release asset, and a comparison that did not know this would call every plugin locally changed.
  `released` takes that suffix back off; `matches` accepts a file with or without it.
- **A plugin's release assets are pinned by its version**, at `github.com/<repo>/releases/download/<version>/<file>`,
  with no `v` prefix on the tag.
- **A theme has no release assets at all.**  Obsidian takes a theme from its repository's default branch, so the theme's
  `version` in the manifest pins nothing: AnuPpuccin's `v1.5.0` tag and its default branch hold different `theme.css`
  files, and the installed one is the branch's.  Capture therefore resolves the commit that branch is at and pins
  `raw.githubusercontent.com/<repo>/<commit>/<file>`, which is immutable, so an install a year from now still works.

`json.dumps(..., indent=2, sort_keys=True)` is what a shipped settings file is written as, by capture, and what the
home's copy is read through before any comparison.  Obsidian rewrites these files in its own whitespace and its own key
order whenever it feels like it, and a difference of formatting is not a difference of settings; canonical text on both
sides is what keeps `doctor` quiet until a *value* moves.

Nothing on the hook path imports this module or its two commands: `cli/cliparser`, `commands/doctor` and
`commands/homeinit` are the only readers, and all three are off it (`tests/test_package.py`'s HOOK_PATH).

**Past 250 lines on purpose** (`.claude/skills/spudlib-modules` section 6).  This is one body of data read as one --
the file names, the two URL shapes, the refusals (a URL of any other scheme, a lock whose names are not each one plain
file name), and the three comparisons (`released`, `matches`, `canonical`) that decide, in identical words, whether two
copies of a vault file are the same one.  Every definition in it is small, so it is a list of things and could be cut
anywhere; the one seam that looked real, moving `vault_findings` out as
doctor's own module, is not: those findings *are* the comparisons above asked in the other direction, and
`installed_version` would have to stay here anyway, since capture reads a manifest through it.  Three readers each use
some of each, which is the reason the three do not each carry their own rule for what Obsidian's `/* nosourcemap */`
means.
"""

import json
import os
from pathlib import Path, PureWindowsPath

from ..core import kernel, lazy, shipped

# Where the vault lives on each side.  Under `share/` the directory cannot be called `.obsidian`: this repository's
# `.gitignore` ignores that name, which is the reason the whole template is hard to share by hand in the first place.
OBSIDIAN = ".obsidian"
SHARE_OBSIDIAN = "obsidian"
LOCK = "obsidian.lock.json"
VAULT_BASES = "ledger"  # the shipped `.base` files sit beside the rest of share/ledger/, and of the home's ledger/
# The settings files the tool ships, in the order a listing reads best.  Never `workspace.json` or
# `workspace-mobile.json` (one person's window layout), never `.DS_Store`, and never a plugin's or a theme's code.
SETTINGS = ("app.json", "appearance.json", "community-plugins.json", "core-plugins.json",
            "graph.json", "page-preview.json", "types.json")
NEVER = ("workspace.json", "workspace-mobile.json", ".DS_Store")
PLUGINS = "plugins"
THEMES = "themes"
PLUGIN_DATA = "data.json"  # a turned-on plugin's own settings, shipped where it has any
PLUGIN_FILES = ("manifest.json", "main.js", "styles.css")  # styles.css is optional; manifest.json and main.js are not
THEME_FILES = ("manifest.json", "theme.css")
CORE_PLUGINS = "core-plugins.json"
SYNC = "sync"  # the one core plugin the template turns off: it is tied to one person's Obsidian account
NOSOURCEMAP = b"\n/* nosourcemap */"
# The backups directory `vault install` keeps a replaced file under, beside the ledger's own daily copies.
BACKUP_DIR = "vault-install"

# The suite runs the CLI as a subprocess, so no `mock.patch` reaches a download made inside one.  This env var is how a
# test steers `download`, exactly as SPD-077's SPUD_GH steers the GitHub reader: `off` refuses every download, which is
# what `tests/helpers.Home` sets on every scratch home, so no test can reach the network by forgetting something; a
# directory serves each URL from the file named by that URL, percent-encoded whole (`served_name`); unset is the network.
DOWNLOAD_ENV = "SPUD_VAULT_DOWNLOADS"
DOWNLOAD_OFF = "off"
TIMEOUT = 30
USER_AGENT = "spud-vault/1.0"
HTTPS = "https"  # the one scheme `download` opens: see its docstring
DOWNLOADS_OFF = "downloads are off (%s=" + DOWNLOAD_OFF + "), so %s was not fetched"
NOT_SERVED = "%s is not served by %s=%s: no %s"
ONLY_HTTPS = ("%s is not fetched: its scheme is %s, and every URL in the lock is a GitHub release asset over "
              + HTTPS + " -- no other scheme is opened, so no lock can make a download read a local file")
NO_LOCK = "no %s: the tool repository's share/ is the source, and `spud vault capture --into <worktree>` writes it"
BAD_LOCK = "%s is not a usable lock: %s"
# Every id, name and file name in the lock is joined onto a path inside the vault (`target_dir`, `install_locked`,
# `vaultinstall.keep_copy`), and `Path("/a") / "/tmp/x"` is `/tmp/x`: a lock naming one of these is refused whole.
NOT_A_NAME = "%s %r is not one plain file name (it %s), and the lock's names are joined onto the vault's own path"
NOT_A_FILE_NAME = "%s %r pins a file whose name %r is not one plain file name (it %s)"
# The two lists Obsidian's own store reads, which is where a plugin's or a theme's GitHub repository comes from.
RELEASES = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/"
PLUGIN_LIST = RELEASES + "community-plugins.json"
THEME_LIST = RELEASES + "community-css-themes.json"
RELEASE_URL = "https://github.com/%s/releases/download/%s/%s"
RAW_URL = "https://raw.githubusercontent.com/%s/%s/%s"
HEAD_SHA_URL = "https://api.github.com/repos/%s/commits/HEAD"
HEAD_SHA_ACCEPT = "application/vnd.github.sha"  # the bare commit id, no JSON to parse
# What doctor says when the home and the shipped copy have drifted.  Notes, never problems, and deliberately: Obsidian
# rewrites `graph.json` when Eric pans the graph, and a problem there would fail `spud init`, which stops on every
# problem doctor finds (commands/homeinit.verify).  Nothing is broken when these differ -- the template is behind, and
# only a ticket can settle that, because capture writes into a worktree.  One line here is all it takes to change.
CAPTURE_HINT = "`spud --as <agent_id> vault capture --into <worktree>` on a ticket brings the shipped copy up to date"
DIFFERS = "the vault's %s differs from the one the tool ships (%s); " + CAPTURE_HINT
VERSION_DIFFERS = "%s %s is installed and the lock pins %s; " + CAPTURE_HINT
FILES_DIFFER = "%s %s is installed and %s %s not what the lock pins; " + CAPTURE_HINT


class VaultDownloadError(Exception):
    """One download did not happen.  Never fatal on its own: `vault install` names the plugin or theme it refuses and
    installs the rest, and `spud init` turns it into a note and a line telling the person to run `vault install` later."""


# ----------------------------------------------------------------------------
# The one door to the network
# ----------------------------------------------------------------------------


def served_name(url):
    """The file name a directory named by SPUD_VAULT_DOWNLOADS serves `url` from: the whole URL, percent-encoded, so a
    test writes a fixture whose name a reader can still see the URL in."""
    return lazy.urllib_parse.quote(url, safe="")


def download(url, timeout=TIMEOUT, accept=None):
    """`url`'s bytes: the one function every download of the vault commands goes through, and the one the suite replaces.

    **https and nothing else.**  Every URL here comes from the lock, and what a lock pins is a GitHub release asset;
    `urlopen` would just as willingly open `file://`, `ftp://` or `data:`, so a crafted lock could otherwise make an
    install read a local file and write it into the vault.  The scheme is checked before anything else looks at the
    URL -- before the fixture directory below is even consulted -- and any other one is refused by name.

    Standard library only, with a timeout on every call.  Raises VaultDownloadError for anything that is not a body --
    a refused download, a 404, a name that does not resolve, a timeout, a body cut short -- so a caller can name what it
    could not get and carry on with the rest.  `urllib` is reached through `core/lazy`: `cli/cliparser` imports every
    command module, so importing it here at the top put 16 ms on every `spud` run for a command that downloads nothing.
    """
    try:
        scheme = lazy.urllib_parse.urlsplit(url).scheme
    except ValueError as e:  # a URL urllib will not even split, such as a bracket that opens no IPv6 host
        raise VaultDownloadError("%s: %s" % (url, e)) from e
    if scheme != HTTPS:
        raise VaultDownloadError(ONLY_HTTPS % (url, scheme or "none"))
    where = os.environ.get(DOWNLOAD_ENV)
    if where == DOWNLOAD_OFF:
        raise VaultDownloadError(DOWNLOADS_OFF % (DOWNLOAD_ENV, url))
    if where:
        served = Path(where) / served_name(url)
        if not served.is_file():
            raise VaultDownloadError(NOT_SERVED % (url, DOWNLOAD_ENV, where, served.name))
        return served.read_bytes()
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    # `http.client.IncompleteRead` -- a body cut short -- is an HTTPException and *not* an OSError, so before SPD-156's
    # review a truncated download left `spud vault install` and `spud init` as a traceback instead of a refusal.  It is
    # named through `urllib.request`, which imports `http.client` itself and is the only thing here that raises one, so
    # catching it costs no import of its own -- and none on the `spud` runs that download nothing.
    cut_short = lazy.urllib_request.http.client.HTTPException
    try:
        request = lazy.urllib_request.Request(url, headers=headers)  # inside the try: a URL urllib refuses is a ValueError
        with lazy.urllib_request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (lazy.urllib_error.URLError, cut_short, OSError, ValueError) as e:
        raise VaultDownloadError("%s: %s" % (url, e)) from e


def download_json(url, timeout=TIMEOUT):
    """A downloaded body read as JSON, a body that is not JSON refused the way a body that never arrived is."""
    body = download(url, timeout)
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise VaultDownloadError("%s: not JSON (%s)" % (url, e)) from e


# ----------------------------------------------------------------------------
# Bytes, and what Obsidian does to them
# ----------------------------------------------------------------------------


def released(data):
    """`data` without the `\\n/* nosourcemap */` Obsidian appends to a `main.js` it installs: the release asset's own
    bytes, which is what the lock pins and what anyone can download."""
    return data[:-len(NOSOURCEMAP)] if data.endswith(NOSOURCEMAP) else data


def matches(sha256, data):
    """True when `data` is the file the lock pins: its own bytes, or those bytes with Obsidian's marker still on them."""
    return sha256 in (kernel.sha256_bytes(data), kernel.sha256_bytes(released(data)))


def write_bytes(path, data):
    """`kernel.write_whole` for bytes: a temporary file in the same directory, renamed into place, so a reader -- and
    Obsidian is a reader -- never sees half a plugin."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (".spud-" + path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def canonical(name, text):
    """A settings file's shipped text: its JSON, re-serialized with sorted keys and two-space indent, and for
    `core-plugins.json` with Sync turned off.  A file that is not JSON -- an enabled CSS snippet -- passes through.

    Both sides of every comparison go through this, so Obsidian's own rewrites of whitespace and key order are not
    differences and a changed *value* still is.  Sync is off because it is tied to one person's Obsidian account; every
    other core-plugin setting is the one that was captured.
    """
    if not name.endswith(".json"):
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s is not JSON: %s" % (name, e))
    if name == CORE_PLUGINS and isinstance(value, dict):
        value = dict(value, **{SYNC: False})
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------
# The two sides: what the tool ships, and what the home has
# ----------------------------------------------------------------------------


def share_vault(ctx):
    """`<tool>/share/obsidian/`: the settings the tool ships, installed as a home's `.obsidian/`."""
    return shipped.share_dir(ctx) / SHARE_OBSIDIAN


def lock_path(ctx):
    return shipped.share_dir(ctx) / LOCK


def shipped_settings(ctx):
    """[(the path under `.obsidian/`, its text)] for every settings file the tool ships, sorted -- the seven at the top
    and each turned-on plugin's `plugins/<id>/data.json`."""
    root = share_vault(ctx)
    if not root.is_dir():
        return []
    return [(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
            for path in sorted(root.rglob("*")) if path.is_file()]


def read_lock(ctx):
    """The lock the tool ships, checked for shape.  Raises the way `core/shipped.read` does when it is gone, so a
    command refuses before it writes anything."""
    path = lock_path(ctx)
    if not path.is_file():
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_LOCK % path)
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, BAD_LOCK % (path, e))
    problems = lock_problems(lock)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, BAD_LOCK % (path, "; ".join(problems)))
    return lock


def component_problem(value):
    """Why `value` is not one plain file name, as a phrase; None when it is one.

    The lock's plugin ids, theme names and file names are all joined onto a path inside the vault, and pathlib does what
    it is told: `Path("/a") / "/tmp/x"` is `/tmp/x`, and `Path("/a") / ".." / "x"` is a sibling of `/a`.  So a lock
    holding one of those would have `vault install` write outside `.obsidian/` -- into anywhere at all, for an absolute
    one -- and that is what this refuses, at `read_lock`, before an install has written a file.

    Windows's rules read the value, on purpose: they are the stricter set -- both separators, drive letters and UNC
    roots -- and a name that is one plain component under them is one under every other rule too.  `.` and `..` are
    named separately because pathlib's parts do not tell them apart from an ordinary name.
    """
    if not isinstance(value, str):
        return "is not a string (%r)" % (value,)
    if not value:
        return "is empty"
    if value in (os.curdir, os.pardir):
        return "is %r, which is a directory above or the one it sits in" % value
    if PureWindowsPath(value).parts != (value,):
        return "holds a path separator, a drive or a root"
    return None


def lock_problems(lock):
    """What is wrong with a parsed lock, as a list of sentences; [] when it is one every command can read.

    Shape, and then names: every id, theme name and file name must be one plain file name (`component_problem`), since
    all three end up joined onto the vault's path.  A lock that fails this is refused whole, naming the value.
    """
    problems = []
    if not isinstance(lock, dict):
        return ["not an object (%r)" % (lock,)]
    for kind, key in (("plugin", "plugins"), ("theme", "themes")):
        entries = lock.get(key)
        if not isinstance(entries, list):
            problems.append("%s is not a list (%r)" % (key, entries))
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                problems.append("a %s entry is not an object (%r)" % (kind, entry))
                continue
            named = "id" if kind == "plugin" else "name"
            name = entry.get(named)
            for field in (("id", "views") if kind == "plugin" else ("name",)) + ("version", "repo", "files"):
                if field not in entry:
                    problems.append("%s %r has no %s" % (kind, name, field))
            problem = component_problem(name) if named in entry else None  # a missing one is already a problem above
            if problem:
                problems.append(NOT_A_NAME % (kind, name, problem))
            for f in entry.get("files") or []:
                if not isinstance(f, dict) or not all(f.get(k) for k in ("name", "url", "sha256")):
                    problems.append("%s %r has a file entry that is not {name, url, sha256} (%r)" % (kind, name, f))
                    continue
                problem = component_problem(f["name"])
                if problem:
                    problems.append(NOT_A_FILE_NAME % (kind, name, f["name"], problem))
            if not entry.get("files"):
                problems.append("%s %r pins no file" % (kind, name))
    return problems


def locked(lock):
    """[(kind, name, entry)] for every plugin and theme the lock pins, plugins first: the one loop install, capture and
    doctor all walk, so the two kinds never drift apart in three places."""
    return ([("plugin", e.get("id"), e) for e in lock.get("plugins", [])]
            + [("theme", e.get("name"), e) for e in lock.get("themes", [])])


def target_dir(ctx, kind, name):
    """Where a locked plugin's or theme's files go in a home: `.obsidian/plugins/<id>/`, `.obsidian/themes/<name>/`.

    Safe to join only because `name` reached its caller through `read_lock`, which refuses a lock whose names are not
    each one plain file name (`component_problem`)."""
    return ctx.home / OBSIDIAN / (PLUGINS if kind == "plugin" else THEMES) / name


def view_types(lock):
    """Every Obsidian Bases view type the locked plugins provide, `table` included -- the set a shipped `.base` file's
    views are checked against (`tests/test_share.py`).

    A plugin's own list is in the lock beside it, because nothing else in the tool can know it: Obsidian registers a view
    type from inside the plugin's code.  `vault capture` carries a plugin's list forward from the lock it overwrites, so
    a version bump keeps it and a plugin new to the lock arrives with none -- and then shipping a `.base` that uses one
    of its views is the reviewed edit that writes the list down.
    """
    types = {"table"}
    for entry in lock.get("plugins", []):
        types.update(entry.get("views") or [])
    return types


# ----------------------------------------------------------------------------
# doctor: where the home and the shipped copy have drifted
# ----------------------------------------------------------------------------


def vault_findings(ctx):
    """[(what, sentence)] for every shipped settings file, `.base` file, plugin or theme the home has *and* has changed.

    A file the home does not have is no finding at all: it means the vault was never installed from this template, or
    that plugin is not in it, and neither is something capture can settle -- `vault install` is the command for that,
    and `spud init` already says so when a download was refused.  What is left is drift: Eric changed a setting, moved a
    view, or Obsidian updated a plugin, and the shipped copy is behind until a ticket captures it.
    """
    findings = []
    vault = ctx.home / OBSIDIAN
    if not vault.is_dir():
        return findings
    for rel, text in shipped_settings(ctx):
        here = vault / rel
        if here.is_file() and canonical(rel, here.read_text(encoding="utf-8")) != text:
            findings.append((OBSIDIAN + "/" + rel, DIFFERS % (OBSIDIAN + "/" + rel, shipped_settings_path(ctx, rel))))
    for path in sorted(shipped.share_dir(ctx).glob(VAULT_BASES + "/*.base")):
        here = ctx.home / VAULT_BASES / path.name
        if here.is_file() and here.read_bytes() != path.read_bytes():
            rel = VAULT_BASES + "/" + path.name
            findings.append((rel, DIFFERS % (rel, path)))
    findings.extend(locked_findings(ctx))
    return findings


def shipped_settings_path(ctx, rel):
    return share_vault(ctx) / rel


def locked_findings(ctx):
    """The drift half of `vault_findings` for the lock: a plugin or theme the home has at another version, or at the
    lock's version with files whose bytes are not the ones it pins."""
    findings = []
    try:
        lock = read_lock(ctx)
    except kernel.SpudError:
        return findings  # a tool with no lock ships no plugin; `vault install` is what reports that
    for kind, name, entry in locked(lock):
        here = target_dir(ctx, kind, name)
        if not here.is_dir():
            continue
        version = installed_version(here)
        if version is not None and version != entry["version"]:
            findings.append((name, VERSION_DIFFERS % (kind, name + " " + version, entry["version"])))
            continue
        changed = [f["name"] for f in entry["files"]
                   if (here / f["name"]).is_file() and not matches(f["sha256"], (here / f["name"]).read_bytes())]
        if changed:
            findings.append((name, FILES_DIFFER % (kind, name, ", ".join(changed), "is" if len(changed) == 1 else "are")))
    return findings


def installed_version(directory):
    """The `version` of the `manifest.json` in an installed plugin's or theme's directory; None when there is none to
    read, which is a directory that is not one Obsidian wrote and no version to compare."""
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) else None
