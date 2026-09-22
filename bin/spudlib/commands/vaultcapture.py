"""commands/vaultcapture: `spud vault capture --into <worktree>` (SPD-156, the portable-home design section 3).

Obsidian writes the vault's settings and the `.base` views; this is the command that takes them back into the tool, so
that a change Eric makes in his own vault reaches everyone else's home through an ordinary ticket.  It is a member's
command, run inside a linked worktree of this repository, and it writes only what that member's deliverables cover.

What it captures is **only what is turned on** -- the plugins `community-plugins.json` enables, the theme and the
snippets `appearance.json` names -- and never a note, never a window layout (`workspace.json`), never a plugin's or a
theme's code.  For each plugin and theme it finds the GitHub repository in Obsidian's own community lists, downloads the
release files for the installed version, and pins one **only if the download is byte for byte what is installed**, so
what the lock names is always what anyone can download.  A plugin changed locally, or installed from outside the store,
is refused by name and nothing is written: a lock that quietly left out a turned-on plugin would ship a vault that opens
wrong, and the person capturing is the one who can fix it.

Two facts about Obsidian this rests on, both confirmed against Eric's vault and written down in `commands/vaultlock`:
an installed `main.js` carries Obsidian's own `/* nosourcemap */` suffix, and a theme comes from its repository's
default branch rather than from any release, so a theme is pinned to the commit that branch is at.

**Past 250 lines on purpose** (`.claude/skills/spudlib-modules` section 6), on `commands/homeinit`'s own argument:
this is one procedure and every function below is a step of it, called from `cmd_vault_capture` and from nowhere else,
in the order that function calls them.  The order is the point -- the actor, then the worktree, then what is turned on,
then the downloads, then the deliverable check, and only then a single write -- because all of it has to refuse before
any of it writes, and a lock beside files it does not describe is the failure this shape exists to make impossible.
Cutting it would give two modules that may only ever be called in one order, with the order written nowhere.
"""

import json
import os
import re
from pathlib import Path

from . import vaultlock, worktreebind
from ..core import kernel
from ..hooks import pathrule, worktrees
from ..state import actors, ledgerdb, lookup, ops

SHARE = "share"
NOT_A_CHECKOUT = "--into %s is not inside a git repository; it must be a linked worktree of %s"
NOT_THIS_REPOSITORY = ("--into %s is a checkout of another repository (%s); the vault template is this tool's, so capture"
                       " writes only into a linked worktree of %s")
IS_MAIN_CHECKOUT = ("--into %s is the main checkout of %s, where code is never built (the tool's CLAUDE.md): enter a linked"
                    " worktree of it and capture into that")
NOT_THE_BOUND_WORKTREE = ("Law 5: %s is bound to the worktree %s (SPD-098) and --into names %s; a member writes in its"
                          " ticket's own worktree, and capturing the vault into another one puts the work where nobody"
                          " will commit it. Capture into %s, or ask your parent for a ticket bound there")
SPUD_CAPTURES_NOTHING = ("capturing the vault produces files in a worktree, and Spud never produces a deliverable (Law 1):"
                         " plan a member on a ticket and run this with `--as <agent_id>`")
OUTSIDE_DELIVERABLES = ("Law 5: %s is not among %s's deliverables (%s), and capture writes every one of its paths or none:"
                        " ask your parent to extend them")
NOT_A_VAULT = "no %s: this home is not an Obsidian vault, so there is nothing to capture"
NOT_A_PLAIN_NAME = ("%s %r is turned on in %s and is not one plain file name (it %s): each of these is joined onto a"
                    " path -- under share/ here, and inside every home's own vault by `vault install` -- so it is not"
                    " captured. Fix the vault's own settings and capture again")
UNUSABLE_LOCK = "the lock this capture built is not one any install would accept, so none of it is written: %s"
CHANGED = ("%s %s is not what its release ships: %s differs from %s. What the lock pins must be what anyone can"
           " download, so a plugin changed by hand or installed from outside the store cannot be captured -- reinstall"
           " it from Obsidian's community store, or leave it turned off")
SIDELOADED = ("%s %s is in no Obsidian community list (%s), so there is no repository to pin it to: it was installed from"
              " outside the store, and the lock names only what anyone can download")
NO_MANIFEST = "%s %s has no readable manifest.json in %s, so there is no version to pin"
NO_FILES = "%s %s has none of %s in %s, so there is nothing to pin"
BAD_SHA = "%s did not answer a commit id for %s (%r)"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


# ----------------------------------------------------------------------------
# Where it may write
# ----------------------------------------------------------------------------


def into_worktree(ctx, into):
    """The real path of `--into`, refused unless it is a linked worktree of this repository.

    The three refusals are the ones the design names: a directory in no repository, a checkout of another repository,
    and the main checkout of this one -- where a merge is a deploy and code is never built.
    """
    path = os.path.realpath(os.path.expanduser(str(into)))
    facts = worktreebind.checkout_of(path)
    if facts is None:
        raise kernel.SpudError(kernel.EXIT_USAGE, NOT_A_CHECKOUT % (into, ctx.tool))
    top, git_dir, common = facts
    tool = worktreebind.checkout_of(str(ctx.tool))
    if tool is None or worktrees.file_identity(common) != worktrees.file_identity(tool[2]):
        raise kernel.SpudError(kernel.EXIT_USAGE, NOT_THIS_REPOSITORY % (into, top, ctx.tool))
    if worktrees.file_identity(git_dir) == worktrees.file_identity(common):
        raise kernel.SpudError(kernel.EXIT_USAGE, IS_MAIN_CHECKOUT % (into, ctx.tool))
    return top


def tool_project_key(ctx, con):
    """The key of the project whose root is the tool repository, or None when none is registered -- which is every
    scratch home the suite builds.  A deliverable glob qualified with that key names this worktree's checkout."""
    for row in con.execute("SELECT key, root_path FROM projects WHERE archived_at IS NULL").fetchall():
        if worktrees.file_identity(row["root_path"]) == worktrees.file_identity(str(ctx.tool)):
            return row["key"]
    return None


def check_deliverables(con, actor, key, paths):
    """Every path this capture would write, checked against the member's deliverables before anything is written.

    A bare glob is the member's own checkout, which is where `--into` points; one qualified with the tool project's key
    names the same checkout; `home:` and another project's key never do.  All or none: a capture that wrote half of
    `share/obsidian/` would leave a lock describing files that are not there.
    """
    globs = json.loads(actor.member["deliverables"]) if actor.member["deliverables"] else []
    usable = [bare for scope, bare in (ops.glob_scope(g) for g in globs) if scope in (None, key)]
    for rel in paths:
        if not any(pathrule.path_matches_glob(rel, g) for g in usable):
            raise kernel.SpudError(kernel.EXIT_OWNERSHIP,
                                   OUTSIDE_DELIVERABLES % (rel, actor.ref(con), ", ".join(globs) or "none"))


# ----------------------------------------------------------------------------
# What is turned on
# ----------------------------------------------------------------------------


def read_json(path, what):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise kernel.SpudError(kernel.EXIT_ERROR, "%s (%s) cannot be read: %s" % (what, path, e))


def turned_on(ctx):
    """(the enabled plugin ids, the active theme or None, the enabled snippet names), read from the vault's own files:
    `community-plugins.json` for the plugins, `appearance.json` for the theme and the snippets.

    Each of the three is one plain file name or the capture refuses, naming it and the file it came from: every one of
    them is joined onto a path both here (`share/obsidian/plugins/<id>/`, `share/obsidian/snippets/<name>.css`) and in
    every home a `vault install` later writes, and pathlib does what a `..` or a leading `/` in one tells it to.
    """
    vault = ctx.home / vaultlock.OBSIDIAN
    if not vault.is_dir():
        raise kernel.SpudError(kernel.EXIT_ERROR, NOT_A_VAULT % vault)
    enabled = vault / "community-plugins.json"
    plugins = [p for p in (read_json(enabled, "the enabled plugins") if enabled.is_file() else []) if isinstance(p, str)]
    appearance = vault / "appearance.json"
    look = read_json(appearance, "the appearance settings") if appearance.is_file() else {}
    theme = look.get("cssTheme") or None
    snippets = [s for s in (look.get("enabledCssSnippets") or []) if isinstance(s, str)]
    named = ([("plugin", p, enabled) for p in plugins] + [("snippet", s, appearance) for s in snippets]
             + ([("theme", theme, appearance)] if theme else []))
    for kind, name, where in named:
        problem = vaultlock.component_problem(name)
        if problem:
            raise kernel.SpudError(kernel.EXIT_ERROR, NOT_A_PLAIN_NAME % (kind, name, where, problem))
    return plugins, theme, snippets


def capture_settings(ctx, plugins, snippets):
    """{the path under share/: its text} for the settings half: the seven files, each turned-on plugin's `data.json`
    where it has one, and each enabled snippet's CSS.  A file the vault does not have is simply not shipped."""
    vault = ctx.home / vaultlock.OBSIDIAN
    out = {}
    for name in vaultlock.SETTINGS:
        path = vault / name
        if path.is_file():
            out["%s/%s" % (vaultlock.SHARE_OBSIDIAN, name)] = vaultlock.canonical(name, path.read_text(encoding="utf-8"))
    for pid in plugins:
        path = vault / vaultlock.PLUGINS / pid / vaultlock.PLUGIN_DATA
        if path.is_file():
            rel = "%s/%s/%s/%s" % (vaultlock.SHARE_OBSIDIAN, vaultlock.PLUGINS, pid, vaultlock.PLUGIN_DATA)
            out[rel] = vaultlock.canonical(vaultlock.PLUGIN_DATA, path.read_text(encoding="utf-8"))
    for name in snippets:
        path = vault / "snippets" / (name + ".css")
        if path.is_file():
            out["%s/snippets/%s.css" % (vaultlock.SHARE_OBSIDIAN, name)] = path.read_text(encoding="utf-8")
    return out


def capture_bases(ctx):
    """{the path under share/: its text} for every `.base` file in the home's `ledger/`, copied verbatim.

    Verbatim and not canonical: a `.base` is Obsidian's own YAML, and the point of shipping it is the views Eric built,
    down to the column widths he dragged.  No note is ever read -- only `ledger/*.base`.
    """
    return {"%s/%s" % (vaultlock.VAULT_BASES, path.name): path.read_text(encoding="utf-8")
            for path in sorted((ctx.home / vaultlock.VAULT_BASES).glob("*.base"))}


# ----------------------------------------------------------------------------
# The lock
# ----------------------------------------------------------------------------


def community_repos():
    """({plugin id: repo}, {theme name: repo}) from `obsidianmd/obsidian-releases`, the two lists Obsidian's own store
    reads.  Two downloads, and the only place a repository name comes from: the tool guesses none."""
    plugins = vaultlock.download_json(vaultlock.PLUGIN_LIST)
    themes = vaultlock.download_json(vaultlock.THEME_LIST)
    return ({e["id"]: e["repo"] for e in plugins if isinstance(e, dict) and e.get("id") and e.get("repo")},
            {e["name"]: e["repo"] for e in themes if isinstance(e, dict) and e.get("name") and e.get("repo")})


def head_commit(repo):
    """The commit the repository's default branch is at, as `api.github.com` answers it in one line.

    A theme has no release assets at all -- Obsidian takes it from that branch -- so this is what makes a theme's pinned
    URL immutable, and an install a year from now the same install as today's.
    """
    answer = vaultlock.download(vaultlock.HEAD_SHA_URL % repo, accept=vaultlock.HEAD_SHA_ACCEPT).decode("utf-8", "replace").strip()
    if not COMMIT_RE.fullmatch(answer):
        raise kernel.SpudError(kernel.EXIT_ERROR, BAD_SHA % (vaultlock.HEAD_SHA_URL % repo, repo, answer[:80]))
    return answer


def pin(kind, name, directory, names, url_for):
    """One plugin's or theme's lock entry: each of `names` that is installed, its pinned URL and the SHA-256 of what
    that URL gave -- and only once the download is byte for byte the installed file (Obsidian's `/* nosourcemap */`
    suffix aside).  A file that differs refuses the whole capture and names what differs."""
    files = []
    for fname in names:
        path = directory / fname
        if not path.is_file():
            continue
        url = url_for(fname)
        try:
            data = vaultlock.download(url)
        except vaultlock.VaultDownloadError as e:
            raise kernel.SpudError(kernel.EXIT_ERROR, "%s %s: %s" % (kind, name, e))
        if vaultlock.released(path.read_bytes()) != data:
            raise kernel.SpudError(kernel.EXIT_ERROR, CHANGED % (kind, name, path, url))
        files.append({"name": fname, "url": url, "sha256": kernel.sha256_bytes(data)})
    if not files:
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_FILES % (kind, name, ", ".join(names), directory))
    return files


def capture_lock(ctx, plugins, theme, previous):
    """The whole `obsidian.lock.json`: every turned-on plugin and the active theme, each pinned to its version, its
    GitHub repository, and one URL and SHA-256 per file.

    A plugin's `views` -- the Bases view types it registers, which is why a shipped `.base` may use them -- is carried
    forward from `previous`, the lock this capture overwrites: nothing in the tool can read a view type out of a
    plugin's code, so the list is written down once, by hand, in the reviewed edit that first ships a view of that type.
    """
    plugin_repos, theme_repos = community_repos()
    lock = {"plugins": [], "themes": []}
    kept = {e.get("id"): e.get("views") or [] for e in (previous or {}).get("plugins", [])}
    for pid in sorted(plugins):
        directory = ctx.home / vaultlock.OBSIDIAN / vaultlock.PLUGINS / pid
        manifest = manifest_of("plugin", pid, directory)
        repo = plugin_repos.get(pid)
        if not repo:
            raise kernel.SpudError(kernel.EXIT_ERROR, SIDELOADED % ("plugin", pid, vaultlock.PLUGIN_LIST))
        version = manifest["version"]
        files = pin("plugin", pid, directory, vaultlock.PLUGIN_FILES,
                    lambda fname, r=repo, v=version: vaultlock.RELEASE_URL % (r, v, fname))
        lock["plugins"].append({"id": pid, "name": manifest.get("name") or pid, "version": version, "repo": repo,
                                "views": kept.get(pid, []), "files": files})
    if theme:
        directory = ctx.home / vaultlock.OBSIDIAN / vaultlock.THEMES / theme
        manifest = manifest_of("theme", theme, directory)
        repo = theme_repos.get(theme)
        if not repo:
            raise kernel.SpudError(kernel.EXIT_ERROR, SIDELOADED % ("theme", theme, vaultlock.THEME_LIST))
        commit = head_commit(repo)
        files = pin("theme", theme, directory, vaultlock.THEME_FILES,
                    lambda fname, r=repo, c=commit: vaultlock.RAW_URL % (r, c, fname))
        lock["themes"].append({"name": theme, "version": manifest["version"], "repo": repo, "commit": commit, "files": files})
    return lock


def manifest_of(kind, name, directory):
    """An installed plugin's or theme's `manifest.json`, which must carry a version: what is turned on and not installed
    at all is the one thing capture cannot pin, and it says so rather than shipping a lock that names nothing."""
    version = vaultlock.installed_version(directory)
    if version is None:
        raise kernel.SpudError(kernel.EXIT_ERROR, NO_MANIFEST % (kind, name, directory))
    return read_json(directory / "manifest.json", "%s %s's manifest" % (kind, name))


# ----------------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------------


def capture_files(ctx, previous):
    """{the path under the worktree: its text} for everything this capture writes: the settings, the lock, the views.

    The lock it built is read back through `vaultlock.lock_problems`, the same check `vault install` makes of the lock
    it is about to install from: what capture writes is what everyone else's `spud init` obeys, so a lock this tool
    would refuse to install is one it refuses to ship, and it refuses here, before the first file is written.
    """
    plugins, theme, snippets = turned_on(ctx)
    files = {}
    for rel, text in capture_settings(ctx, plugins, snippets).items():
        files["%s/%s" % (SHARE, rel)] = text
    for rel, text in capture_bases(ctx).items():
        files["%s/%s" % (SHARE, rel)] = text
    lock = capture_lock(ctx, plugins, theme, previous)
    problems = vaultlock.lock_problems(lock)
    if problems:
        raise kernel.SpudError(kernel.EXIT_ERROR, UNUSABLE_LOCK % "; ".join(problems))
    files["%s/%s" % (SHARE, vaultlock.LOCK)] = json.dumps(lock, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return files, lock


def stale_files(worktree, files):
    """The shipped vault files in the worktree that this capture does not produce: a plugin turned off since the last
    capture and its `data.json`, a `.base` file deleted from the home.  They are removed, and they go through the
    deliverable check with everything else.

    Only `share/obsidian/**` and `share/ledger/*.base` are looked at: everything else under `share/` belongs to some
    other part of the tool, and a capture that swept the directory would take it with it.
    """
    here = [worktree / SHARE / vaultlock.SHARE_OBSIDIAN, worktree / SHARE / vaultlock.VAULT_BASES]
    found = [p for p in here[0].rglob("*") if p.is_file()] + sorted(here[1].glob("*.base"))
    return sorted(rel for rel in (p.relative_to(worktree).as_posix() for p in found) if rel not in files)


def cmd_vault_capture(ctx, args):
    """`spud vault capture --into <worktree>` (a member's, on a ticket).

    Every refusal comes first and nothing is written until all of them have passed: the actor, the worktree, the
    downloads and the deliverable check.  A capture that got half way would leave a lock naming files that are not
    beside it.
    """
    con = ledgerdb.connect(ctx)
    try:
        actor = actors.resolve_actor(con, args.actor)
        if actor.kind != "member":
            raise kernel.SpudError(kernel.EXIT_OWNERSHIP, SPUD_CAPTURES_NOTHING)
        key = tool_project_key(ctx, con)
        worktree = Path(into_worktree(ctx, args.into))
        ticket_row = lookup.get_ticket_by_id(con, actor.member["ticket_id"])
        bound = ticket_row["worktree"]
        # SPD-098: a member writes in the worktree its ticket is bound to, and the edit hook holds it to that.  A
        # capture is a CLI write, which no edit hook sees, so the same rule is made here -- otherwise a member of a
        # ticket bound in another project could write share/ in this one, where its globs mean something else.
        if bound and worktrees.file_identity(bound) != worktrees.file_identity(str(worktree)):
            raise kernel.SpudError(kernel.EXIT_OWNERSHIP,
                                   NOT_THE_BOUND_WORKTREE % (ticket_row["key"], bound, worktree, bound))
        previous = read_json(vaultlock.lock_path(ctx), "the lock this capture overwrites") \
            if vaultlock.lock_path(ctx).is_file() else None
        files, lock = capture_files(ctx, previous)
        removed = stale_files(worktree, files)
        check_deliverables(con, actor, key, sorted(files) + removed)
        ref, ticket = actor.ref(con), ticket_row["key"]
    finally:
        con.close()
    written, unchanged = [], []
    for rel in sorted(files):
        path = worktree / rel
        want = files[rel].encode("utf-8")
        if path.is_file() and path.read_bytes() == want:
            unchanged.append(rel)
            continue
        kernel.write_whole(path, files[rel])
        written.append(rel)
    for rel in removed:
        (worktree / rel).unlink()
    record = {"worktree": str(worktree), "ticket": ticket, "actor": ref, "written": written, "unchanged": unchanged,
              "removed": removed,
              "plugins": [{"id": e["id"], "version": e["version"], "repo": e["repo"]} for e in lock["plugins"]],
              "themes": [{"name": e["name"], "version": e["version"], "repo": e["repo"], "commit": e["commit"]} for e in lock["themes"]]}
    lines = ["captured the home's vault into %s: %d file(s) written, %d unchanged, %d removed"
             % (worktree, len(written), len(unchanged), len(removed))]
    lines.extend("  pinned plugin %s %s (%s)" % (p["id"], p["version"], p["repo"]) for p in record["plugins"])
    lines.extend("  pinned theme %s %s (%s at %s)" % (t["name"], t["version"], t["repo"], t["commit"][:12]) for t in record["themes"])
    lines.extend("  wrote %s" % rel for rel in written)
    lines.extend("  removed %s" % rel for rel in removed)
    return kernel.Result(record, "\n".join(lines))
