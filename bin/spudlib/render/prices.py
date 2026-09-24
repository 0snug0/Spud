"""render/prices: List-price cost from spud.config.json pricing, and the token counts it prices."""

import json
import re

from ..core import lazy
from ..state import transcripts


def token_counts(usage_json):
    """{out, in, cached} from a run's transcript sum: output_tokens; input_tokens plus
    cache_creation_input_tokens; cache_read_input_tokens (a missing key counts 0).  A sum counts each API
    request once ("counting": "request"); one stored by an older spud added every transcript entry,
    reads the same way, and runs four to five times too high until `spud member resum` re-sums it.  A
    request billed per attempt counts every attempt it billed since SPD-220 (transcripts.request_attempts), as
    its breakdown prices them; one stored before counted its serving attempt alone until `spud member resum`.  The
    completion kept beside the sum under "completion" is never read: its figures cover only its final
    request.  None for any other source (a completion alone), a figure that is not a
    non-negative integer, or JSON of another shape."""
    if not usage_json:
        return None
    try:
        usage = json.loads(usage_json)
    except ValueError:
        return None
    if not isinstance(usage, dict) or usage.get("source") != "transcript" or not isinstance(usage.get("usage"), dict):
        return None
    figures = {}
    for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage["usage"].get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        figures[key] = value
    return {"out": figures["output_tokens"], "in": figures["input_tokens"] + figures["cache_creation_input_tokens"],
            "cached": figures["cache_read_input_tokens"]}


# Cost at the API list price.  Eric decided the basis on 2026-09-13: the API list price in USD from Anthropic's
# published per-model prices, labelled as list price so it is never read as his subscription's bill, from the dated
# table spud.config.json gives (`pricing`), which he updates.  Cost is computed when the card and the notes render, from
# a stored transcript sum's per-model breakdown (usage_breakdown) and that table; it is never stored, so a price change
# only re-renders.  Arithmetic is exact (Fraction: integers over integers), rounded once, half up, to cents at display.
# The table: models, each id as the transcripts write message.model, with its input, output, cache_read, cache_write_5m
# and cache_write_1h prices in USD per 1M tokens and its fast-mode prices where fast mode is sold; server_tools, a fee per
# `per` requests of a usage.server_tool_use key; multipliers on every token price for usage.service_tier and
# usage.inference_geo values, an absent value meaning the API's default (standard, global).  A request billed per attempt
# (a server-side fallback, a compaction) is in the breakdown as the attempts it billed, each on the model that ran it
# (transcripts.request_attempts, SPD-220), so it is priced like any other usage.  Whatever a breakdown holds that the table
# or that rule does not price (a model, a speed, a tier, a geography, a cache TTL, a server tool, an attempt that names no
# model, an iteration type the rule does not cover, a request billed per attempt that a breakdown stored before SPD-220
# kept whole) leaves the run without a cost and names the reason, rather than pricing it at a guess.
PRICE_RATES = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
SPLIT_RATES = {"ephemeral_5m_input_tokens": "cache_write_5m", "ephemeral_1h_input_tokens": "cache_write_1h"}
UNSPLIT_RATE = "cache_write_5m"  # cache writes a transcript does not split by TTL: the API's default cache TTL is five minutes
PRICE_MULTIPLIERS = {"service_tier": "standard", "inference_geo": "global"}  # the usage key -> the value its absence means
PRICE_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
COST_USD = re.compile(r"[0-9]{1,15}(?:\.[0-9]{1,2})?")  # a member note's cost_usd as the importer reads it back
NO_TABLE = "no price table in spud.config.json"
NO_BREAKDOWN = "no breakdown, which `spud member resum` adds"
NOT_SPLIT = "a request billed per attempt kept whole, which `spud member resum` splits"


def price_number(value):
    """A price or a multiplier from the config as an exact fraction: a non-negative, finite int or float, never a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    number = lazy.fractions.Fraction(value) if isinstance(value, int) else lazy.fractions.Fraction(repr(value))
    return number if number >= 0 else None


def price_rates(block, where, problems):
    """A model's five prices (or its fast-mode prices) as fractions, or None with the problem named."""
    rates = {k: price_number(block.get(k)) for k in PRICE_RATES} if isinstance(block, dict) else None
    if rates is None or None in rates.values():
        bad = PRICE_RATES if rates is None else [k for k in PRICE_RATES if rates[k] is None]
        problems.append("%s: %s must be a non-negative number of USD per 1M tokens" % (where, ", ".join(bad)))
        return None
    return rates


def price_table(config):
    """(the price table a config gives, its problems).  (None, []) without a `pricing` block: every cost shows a dash.
    (None, problems) when the block is not USD per 1M tokens read on a named day.  Otherwise the table, with each model,
    server tool or multiplier that has a problem left out, so what it would price has no cost, and the problem named."""
    block = config.get("pricing") if isinstance(config, dict) else None
    if block is None:
        return None, []
    if not isinstance(block, dict):
        return None, ["pricing is not an object"]
    problems = []
    if block.get("currency") != "USD":
        problems.append('pricing.currency must be "USD" (%r)' % (block.get("currency"),))
    if block.get("per") != "1M tokens":
        problems.append('pricing.per must be "1M tokens" (%r)' % (block.get("per"),))
    if not isinstance(block.get("as_of"), str) or not PRICE_DATE.fullmatch(block["as_of"]):
        problems.append("pricing.as_of must be the day the prices were read, YYYY-MM-DD (%r)" % (block.get("as_of"),))
    if problems:
        return None, problems
    source = block.get("source")
    if not isinstance(source, str) or not source.strip():
        problems.append("pricing.source must name the page the prices were read from")
        source = None
    table = {"as_of": block["as_of"], "source": source, "currency": "USD", "models": {}, "server_tools": {}}
    table.update((name, {}) for name in PRICE_MULTIPLIERS)
    models = block.get("models")
    if not isinstance(models, dict):
        problems.append("pricing.models is not an object of model ids")
        models = {}
    for model, entry in models.items():
        speeds = {"standard": price_rates(entry, "pricing.models.%s" % model, problems)}
        if speeds["standard"] is None:
            continue
        if "fast" in entry:
            speeds["fast"] = price_rates(entry["fast"], "pricing.models.%s.fast" % model, problems)
            if speeds["fast"] is None:
                continue
        table["models"][model] = speeds
    tools = block.get("server_tools", {})
    if not isinstance(tools, dict):
        problems.append("pricing.server_tools is not an object")
        tools = {}
    for key, entry in tools.items():
        usd = price_number(entry.get("usd")) if isinstance(entry, dict) else None
        per = entry.get("per") if isinstance(entry, dict) else None
        if usd is None or isinstance(per, bool) or not isinstance(per, int) or per < 1:
            problems.append('pricing.server_tools.%s must be {"usd": a non-negative number, "per": a whole number of requests}' % key)
            continue
        table["server_tools"][key] = usd / per
    multipliers = block.get("multipliers", {})
    if not isinstance(multipliers, dict):
        problems.append("pricing.multipliers is not an object")
        multipliers = {}
    for name in PRICE_MULTIPLIERS:
        values = multipliers.get(name, {})
        if not isinstance(values, dict):
            problems.append("pricing.multipliers.%s is not an object" % name)
            continue
        for value, factor in values.items():
            number = price_number(factor)
            if number is None:
                problems.append("pricing.multipliers.%s.%s must be a non-negative number" % (name, value))
                continue
            table[name][value] = number
    return table, problems


def bucket_cost(bucket, table):
    """(one breakdown entry at the table's list prices, an exact fraction of a dollar, or None; the reasons it has none).
    An entry holds the attempts its model ran (transcripts.usage_breakdown), a declined attempt or a compaction among
    them, so its tokens are priced at its model's rates whichever request they came from.  An entry that names no model
    (usage without one, or an attempt of a request that fell back naming none), one counting unpriced_iterations (a
    request kept whole for an iteration type the per-attempt rule does not cover) and one stored before SPD-220 that kept
    a request billed per attempt whole (transcripts.KEPT_WHOLE, until `spud member resum` splits it) have no cost."""
    if not isinstance(bucket, dict):
        return None, ["a breakdown entry that is not an object"]
    split, tools, unpriced, whole = (bucket.get(key, {}) for key in ("cache_creation", "server_tool_use", transcripts.UNPRICED_ITERATIONS, transcripts.KEPT_WHOLE))
    tokens = {key: bucket.get(key, 0) for key in transcripts.BREAKDOWN_TOKENS + ("cache_creation_unsplit_input_tokens",)}
    if not all(isinstance(part, dict) for part in (split, tools, unpriced, whole)) or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in [*tokens.values(), *split.values(), *tools.values()]):
        return None, ["a breakdown figure that is not a count"]
    if not any([*tokens.values(), *split.values(), *tools.values()]) and not unpriced and not whole:
        return lazy.fractions.Fraction(0), []  # nothing billed is free whatever the model says (a synthetic entry, an empty response, a request declined before any output)
    reasons = []
    model = bucket.get("model")
    speeds = table["models"].get(model) if isinstance(model, str) else None
    rates = None
    if speeds is None:
        reasons.append("no price for model %s" % model if isinstance(model, str) else "no price for usage that names no model")
    else:
        speed = bucket.get("speed", "standard")
        rates = speeds.get(speed) if isinstance(speed, str) else None
        if rates is None:
            reasons.append("no price for speed %s on %s" % (speed, model))
    factor = lazy.fractions.Fraction(1)
    for name, default in PRICE_MULTIPLIERS.items():
        value = bucket.get(name, default)
        if isinstance(value, str) and value in table[name]:
            factor *= table[name][value]
        else:
            reasons.append("no price for %s %s" % (name, value))
    reasons += ["no price for cache_creation.%s" % key for key, n in split.items() if n and key not in SPLIT_RATES]
    fees = lazy.fractions.Fraction(0)
    for key, n in tools.items():
        if n and key not in table["server_tools"]:
            reasons.append("no price for server_tool_use.%s" % key)
        elif n:
            fees += n * table["server_tools"][key]
    reasons += ["no price for usage.iterations type %s" % kind for kind in sorted(unpriced)]
    if whole:
        reasons.append(NOT_SPLIT)
    if reasons:
        return None, reasons
    per_million = (tokens["input_tokens"] * rates["input"] + tokens["output_tokens"] * rates["output"]
                   + tokens["cache_read_input_tokens"] * rates["cache_read"]
                   + tokens["cache_creation_unsplit_input_tokens"] * rates[UNSPLIT_RATE]
                   + sum(n * rates[SPLIT_RATES[key]] for key, n in split.items() if n))
    return per_million * factor / 1_000_000 + fees, []


def run_cost(usage_json, table):
    """(a member's run at the API list price in USD, an exact fraction, or None; the reasons it has none).  Only a
    transcript sum is priced, the one token_counts reads: a completion kept beside it or alone covers a final request
    and is never priced, and a row without a sum has nothing to price and no reason.  A sum the importer read
    back from a rendered note carries the cost the note showed; any other sum is priced from its breakdown, which one
    stored before costs were priced lacks until `spud member resum` adds it."""
    if token_counts(usage_json) is None:
        return None, []
    stored, _ = transcripts.usage_parts(usage_json)
    if stored.get("imported") is True:
        text = stored.get("cost_usd")
        return (lazy.fractions.Fraction(text), []) if isinstance(text, str) and COST_USD.fullmatch(text) else (None, ["imported without a cost"])
    if table is None:
        return None, [NO_TABLE]
    breakdown = stored.get("breakdown")
    if not isinstance(breakdown, list):
        return None, [NO_BREAKDOWN]
    total, reasons = lazy.fractions.Fraction(0), []
    for bucket in breakdown:
        cost, why = bucket_cost(bucket, table)
        if cost is None:
            reasons += [reason for reason in why if reason not in reasons]
        else:
            total += cost
    return (None, reasons) if reasons else (total, [])


def cents_of(usd):
    """A dollar amount rounded once, half up, to whole cents."""
    usd = lazy.fractions.Fraction(usd)
    return (usd.numerator * 200 + usd.denominator) // (2 * usd.denominator)


def usd_text(usd):
    """A dollar amount with two decimals and no sign, as --json gives it: 7.13."""
    return "%d.%02d" % divmod(cents_of(usd), 100)


def money(usd):
    """A dollar amount as the card and the Team table show it: $7.13."""
    return "$" + usd_text(usd)


def cost_number(usd):
    """cost_usd as a member note carries it: the amount in cents written the way JavaScript writes the number (7.1, 7,
    0.05), which is how Obsidian writes a number back when it rewrites a note's properties; a trailing zero it drops
    would read as a changed value, not a change of style."""
    text = usd_text(usd)
    return text[:-3] if text.endswith(".00") else text.rstrip("0")
