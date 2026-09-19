"""shell/downloads: the two downloaders, curl and wget, read whole (SPD-126): the files they are told to write by name and
the ones the URL or the server names under a directory.

Until SPD-126 neither was read.  SPD-126's first engineer read curl's -O, --remote-name-all and -J -- a file named by the
URL or the server, a whole-subtree write (arg_writes' kind "tree") of --output-dir's directory or the line's -- in
shell/tree_writes; the second moved that reading here beside the files curl names itself, so one scan of curl's options
reads both, and added wget.
- curl 8.7.1 (curl(1)): -o/--output writes its file, under --output-dir's directory (which applies to -o and -O up to the
  next -:/--next); -D/--dump-header, -c/--cookie-jar, --trace, --trace-ascii, --stderr, --libcurl, --etag-save, --hsts and
  --alt-svc write theirs (`-` is standard output, an empty --hsts or --alt-svc name keeps the cache in memory); -w's
  %output{name} writes name; --no-clobber writes <file>.1 to <file>.100 in place of a file that exists.  What the line
  cannot place is syntax.ANY_PATH: a config file curl reads (-K, or one CURL_HOME, XDG_CONFIG_HOME or HOME points it at,
  unless -q comes first), a -w format read from a file, an --expand-<option> whose value holds a {{variable}}, an -o name
  holding #N under URL globbing (the text a URL's [] or {} set puts there), what xargs hands it.
- wget, GNU wget's manual alone (wget is not installed on this Mac, so nothing here was probed): without -O every download
  lands under -P/--directory-prefix (default `.`), named by the URL or the server, and -r, -m, -p and -x make directories
  there: a whole-subtree write, as curl -O's; -O/--output-document writes all of it to its file (`-` is standard output)
  and nothing else, with -K's <file>.orig and --backups' numbered copies beside it; -o/-a (the log), --rejected-log,
  --save-cookies and --hsts-file write theirs, and the HSTS database is ~/.wget-hsts unless --no-hsts or --hsts-file says
  otherwise; -b without -o or -a logs to wget-log (wget-log.1 and on when it exists) in the line's directory; --warc-file's
  name is the start of the WARC files it writes, and --warc-tempdir a directory it fills.  -e/--execute runs a wgetrc
  command, which can set any of those, and so can a startup file (--config, or WGETRC, SYSTEM_WGETRC or HOME set on the
  line): ANY_PATH.  --use-askpass names a program wget runs, read as a command.

A word the line cannot settle where an option may stand (both read their options anywhere on the line) may be any of those
options, and is ANY_PATH too.  Both are read with getopt_long's grammar (spelled_writes.read_options), curl's own taking no
`=value`, which only ever reads more."""

import re

from . import analyse, arg_writes, globbing, prepare, spelled_writes, syntax, tree_writes


# curl 8 (curl --help all): the short options and the long ones that take the next word as their value.  The long tables of
# both commands are built at the first curl or wget the hook reads and kept (option_table): between them they are some four
# hundred names, and the Bash hook imports this module for every command line there is.
CURL_VALUE_LETTERS = frozenset("ACDEFHKPQTUXYbcdehmortuwxyz")
CURL_VALUE_LONGS = """--abstract-unix-socket --alt-svc --aws-sigv4 --cacert --capath --cert --cert-type --ciphers
    --config --connect-timeout --connect-to --continue-at --cookie --cookie-jar --create-file-mode --crlfile --curves --data
    --data-ascii --data-binary --data-raw --data-urlencode --delegation --dns-interface --dns-ipv4-addr --dns-ipv6-addr
    --dns-servers --doh-url --dump-header --egd-file --engine --etag-compare --etag-save --expect100-timeout --form
    --form-string --ftp-account --ftp-alternative-to-user --ftp-method --ftp-port --ftp-ssl-ccc-mode --happy-eyeballs-timeout-ms
    --haproxy-clientip --header --help --hostpubmd5 --hostpubsha256 --hsts --interface --ipfs-gateway --json --keepalive-time
    --key --key-type --krb --libcurl --limit-rate --local-port --login-options --mail-auth --mail-from --mail-rcpt
    --max-filesize --max-redirs --max-time --netrc-file --noproxy --oauth2-bearer --output --output-dir --parallel-max --pass
    --pinnedpubkey --preproxy --proto --proto-default --proto-redir --proxy --proxy-cacert --proxy-capath --proxy-cert
    --proxy-cert-type --proxy-ciphers --proxy-crlfile --proxy-header --proxy-key --proxy-key-type --proxy-pass
    --proxy-pinnedpubkey --proxy-service-name --proxy-tls13-ciphers --proxy-tlsauthtype --proxy-tlspassword --proxy-tlsuser
    --proxy-user --proxy1.0 --pubkey --quote --random-file --range --rate --referer --request --request-target --resolve
    --retry --retry-delay --retry-max-time --sasl-authzid --service-name --socks4 --socks4a --socks5 --socks5-gssapi-service
    --socks5-hostname --speed-limit --speed-time --stderr --telnet-option --tftp-blksize --time-cond --tls-max --tls13-ciphers
    --tlsauthtype --tlspassword --tlsuser --trace --trace-ascii --trace-config --unix-socket --upload-file --url --url-query
    --user --user-agent --variable --write-out"""
CURL_REMOTE_NAME = ("--remote-name", "--remote-name-all", "--remote-header-name")  # -O and -J: a name from the URL or server
# The flags this reading acts on, beside the value options (and every value option's --expand- form, curl 8.3's).
CURL_FLAGS = ("remote-name", "remote-name-all", "remote-header-name", "next", "globoff", "no-clobber", "disable")
# The files curl writes by the name an option gives (curl(1)), and the names that write none there.
CURL_FILES = ("-D", "--dump-header", "-c", "--cookie-jar", "--trace", "--trace-ascii", "--stderr", "--libcurl", "--etag-save",
              "--hsts", "--alt-svc")
CURL_NO_FILE = ("", "-", "%")  # an empty cache name, standard output, and --trace's standard error
# The variables that point curl at a config file of the line's choosing, which can name any output (curl(1): the default
# config file is looked for under CURL_HOME, XDG_CONFIG_HOME and HOME, unless -q comes first).
CURL_CONFIG_VARS = ("CURL_HOME", "XDG_CONFIG_HOME", "HOME")
# wget (GNU wget's manual, 1.25): the short options taking a value, every long option with whether it requires one, and the
# short options' long names.  An option not listed is read as a flag, which reads its next word as an option: never looser.
WGET_VALUE_LETTERS = frozenset("eoaiBtOTwQPUlARDIXn")
WGET_VALUE_LONGS = """execute output-file append-output report-speed input-file input-metalink metalink-index
    base config rejected-log bind-address bind-dns-address dns-servers tries output-document backups start-pos progress timeout
    dns-timeout connect-timeout read-timeout limit-rate wait waitretry quota restrict-file-names prefer-family user password
    use-askpass local-encoding remote-encoding cut-dirs directory-prefix default-page http-user http-password load-cookies
    save-cookies header compression max-redirect proxy-user proxy-password referer user-agent post-data post-file method
    body-data body-file retry-on-http-error secure-protocol ciphers certificate certificate-type private-key private-key-type
    ca-certificate ca-directory crl-file pinnedpubkey random-file egd-file hsts-file warc-file warc-header warc-max-size
    warc-dedup warc-tempdir ftp-user ftp-password level accept reject accept-regex reject-regex regex-type domains
    exclude-domains follow-tags ignore-tags include-directories exclude-directories"""
WGET_FLAG_LONGS = """version help background debug quiet verbose no-verbose force-html no-config no-clobber
    no-netrc continue show-progress timestamping no-if-modified-since no-use-server-timestamps server-response spider random-wait
    no-proxy no-dns-cache inet4-only inet6-only retry-connrefused ask-password no-iri unlink no-directories force-directories
    no-host-directories protocol-directories adjust-extension no-http-keep-alive no-cache no-cookies keep-session-cookies
    ignore-length save-headers content-disposition content-on-error trust-server-names auth-no-challenge retry-on-host-error
    https-only no-check-certificate no-hsts warc-cdx no-warc-compression no-warc-digests no-warc-keep-log no-remove-listing
    no-glob no-passive-ftp preserve-permissions retr-symlinks ftps-implicit no-ftps-resume-ssl ftps-clear-data-connection
    ftps-fallback-to-ftp recursive delete-after convert-links convert-file-only backup-converted mirror page-requisites
    strict-comments follow-ftp span-hosts relative no-parent keep-badhash metalink-over-http preferred-location
    xattr"""
_OPTION_TABLES = {}  # the long-option tables, built on the first line that names the command they belong to
WGET_SHORT = {"-e": "--execute", "-o": "--output-file", "-a": "--append-output", "-O": "--output-document", "-P": "--directory-prefix",
              "-b": "--background", "-r": "--recursive", "-m": "--mirror", "-p": "--page-requisites", "-K": "--backup-converted"}
WGET_ANYWHERE = ("--execute", "--config")  # a wgetrc command, or a startup file of the line's choosing, can set any output
WGET_CONFIG_VARS = ("WGETRC", "SYSTEM_WGETRC", "HOME")  # where wget looks for its startup files (the manual, Startup File)
WGET_LOGS = ("--output-file", "--append-output", "--rejected-log", "--save-cookies")
WGET_HSTS = "~/.wget-hsts"  # the HSTS database wget keeps unless told otherwise
WGET_RECURSIVE = ("--recursive", "--mirror", "--page-requisites")


def option_table(base):
    """{the long option's name without its dashes: whether it requires a value}, built once per run and kept.  curl's holds
    its --expand-<option> forms too (curl 8.3), and the flags this reading acts on; wget's every long option its manual
    documents, so that getopt_long's abbreviations resolve as wget resolves them."""
    if base not in _OPTION_TABLES:
        if base == "curl":
            values = [n[2:] for n in CURL_VALUE_LONGS.split()]
            _OPTION_TABLES[base] = dict({n: True for n in values}, **{"expand-" + n: True for n in values},
                                 **{n: False for n in CURL_FLAGS})
        else:
            _OPTION_TABLES[base] = dict({n: True for n in WGET_VALUE_LONGS.split()}, **{n: False for n in WGET_FLAG_LONGS.split()})
    return _OPTION_TABLES[base]


def read_download(cmd, base, words, a, depth):
    """Record what `words`, a curl or a wget, writes (module docstring); the line's own values are put in its words first."""
    args = [arg_writes.resolved(w, a) for w in words[1:]]
    (read_curl if base == "curl" else read_wget)(cmd, args, a, depth)


def read_curl(cmd, args, a, depth):
    """curl: the files its options name and, per -:/--next section, -O's, --remote-name-all's and -J's directory (module
    docstring)."""
    first = prepare.deglob(args[0]) if args else ""
    anywhere = first not in ("-q", "--disable") and any(v in a.vars for v in CURL_CONFIG_VARS)
    anywhere = anywhere or any(syntax.INPUT_OPERAND in w for w in args)
    options, urls, hidden = spelled_writes.read_options(args, CURL_VALUE_LETTERS, option_table("curl"), a)
    names = {n for n, _ in options}
    urls = urls + spelled_writes.values_of(options, ("--url",))
    url_globs = not names.intersection(("-g", "--globoff")) and any(c in prepare.deglob(u) for u in urls for c in "[{")
    files, outputs, trees, section = [], [], [], {"dir": None, "remote": False, "outputs": []}

    def close(section):
        folder = section["dir"]
        trees.extend([folder if folder is not None else "."] if section["remote"] else [])
        for out in section["outputs"]:
            outputs.append(out if folder is None else folder.rstrip("/") + "/" + out)
            if folder is not None and prepare.deglob(out).startswith("/"):
                outputs.append(out)  # curl joins even an absolute name to the directory; read both
        return {"dir": None, "remote": False, "outputs": []}

    for name, value in options:
        if name.startswith("--expand-"):
            if value is not None and "{{" in prepare.deglob(value):
                anywhere = True  # a variable curl puts in the value, from a file or the environment
            name = "--" + name[len("--expand-") :]
        if name in ("-:", "--next"):
            section = close(section)
        elif name in ("-O", "-J") or name in CURL_REMOTE_NAME:
            section["remote"] = True
        elif name in ("-K", "--config"):
            anywhere = True
        elif name == "--output-dir":
            section["dir"] = value
        elif name in ("-o", "--output") and value is not None and prepare.deglob(value) not in CURL_NO_FILE:
            anywhere = anywhere or (url_globs and re.search(r"#\d", prepare.deglob(value)) is not None)
            section["outputs"].append(value)
        elif name in CURL_FILES and value is not None and prepare.deglob(value) not in CURL_NO_FILE:
            files.append(value)
        elif name in ("-w", "--write-out") and value is not None:
            text = prepare.deglob(value)
            anywhere = anywhere or text.startswith("@")  # a format read from a file, or standard input, may hold %output{}
            files += [globbing.literalize(n.removeprefix(">>")) for n in write_out_files(text)]
    close(section)
    if anywhere or hidden:
        tree_writes.record(a, cmd, syntax.ANY_PATH)
        return
    for tree in dict.fromkeys(trees):
        tree_writes.record(a, cmd, tree)
    for f in files + outputs:
        tree_writes.record(a, cmd, f, None)
    for f in outputs if "--no-clobber" in names else ():
        spelled_writes.picked_write(a, cmd, f + ".X", 1, "0-9", more=True)  # <file>.1 up to <file>.100


def write_out_files(text):
    """The names a -w format's %output{name} instructions write (curl(1), write-out), `>>name` appending."""
    names, at = [], text.find("%output{")
    while at != -1:
        end = text.find("}", at)
        if end == -1:
            break
        names.append(text[at + len("%output{") : end])
        at = text.find("%output{", end)
    return names


def read_wget(cmd, args, a, depth):
    """wget: every file its options name and, without -O, the directory its downloads land in (module docstring)."""
    options, _, hidden = spelled_writes.read_options(args, WGET_VALUE_LETTERS, option_table("wget"), a)
    options = [(WGET_SHORT.get(n, n), v) for n, v in options]
    names = {n for n, _ in options}
    config = "--no-config" not in names
    if (hidden or any(syntax.INPUT_OPERAND in w for w in args) or names.intersection(WGET_ANYWHERE)
            or (config and any(v in a.vars for v in WGET_CONFIG_VARS))):
        tree_writes.record(a, cmd, syntax.ANY_PATH)
        return
    for value in spelled_writes.values_of(options, ("--use-askpass",)):
        analyse.analyse_new_shell(a, prepare.deglob(value), depth + 1)
    outputs = [v for v in spelled_writes.values_of(options, ("--output-document",)) if prepare.deglob(v) != "-"]
    for out in outputs:
        tree_writes.record(a, cmd, out, None)
        if "--backup-converted" in names:
            tree_writes.record(a, cmd, out + ".orig", None)
        if "--backups" in names:
            spelled_writes.picked_write(a, cmd, out + ".X", 1, "0-9", more=True)
    for value in spelled_writes.values_of(options, WGET_LOGS):
        tree_writes.record(a, cmd, value, None)
    if "--background" in names and not names.intersection(("--output-file", "--append-output")):
        spelled_writes.picked_write(a, cmd, "wget-log", 0, "0-9.", more=True)
    if "--no-hsts" not in names:
        for value in spelled_writes.values_of(options, ("--hsts-file",)) or [WGET_HSTS]:
            tree_writes.record(a, cmd, value, None)
    for value in spelled_writes.values_of(options, ("--warc-file",)):
        spelled_writes.picked_write(a, cmd, value, 0, "-.0-9a-z", more=True)
    for value in spelled_writes.values_of(options, ("--warc-tempdir",)):
        tree_writes.record(a, cmd, value)
    spider = "--spider" in names and not names.intersection(WGET_RECURSIVE)
    if not outputs and not spider and "--output-document" not in names:
        prefixes = spelled_writes.values_of(options, ("--directory-prefix",))
        tree_writes.record(a, cmd, prefixes[-1] if prefixes else ".")
