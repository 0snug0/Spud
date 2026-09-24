"""state/transcripts: Transcript sums and the stored usage columns."""

import json
from datetime import datetime


def as_int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def usage_parts(usage_json):
    """A stored usage_json taken apart: (the transcript sum without its completion when the source is
    transcript, else None; the completion kept beside the sum or alone, else None)."""
    try:
        usage = json.loads(usage_json) if usage_json else None
    except ValueError:
        return None, None
    if not isinstance(usage, dict):
        return None, None
    completion = usage.get("completion") if isinstance(usage.get("completion"), dict) else None
    if usage.get("source") != "transcript":
        return None, completion
    return {k: v for k, v in usage.items() if k != "completion"}, completion


def run_totals(member, summed=None, completion=None):
    """The usage columns once a new transcript sum (transcript_usage's result) or a new completion is
    merged with what the member row holds; the stored sum and the stored completion stay unless a new
    one is given.  total_tokens is the sum's or NULL; duration_ms and tool_uses are the completion's
    whole-run figures when it has them, else the sum's."""
    stored_sum, stored_completion = usage_parts(member["usage_json"])
    if completion is None:
        completion = stored_completion
    if summed is not None:
        tokens, duration, tools, usage = summed["total_tokens"], summed["duration_ms"], summed["tool_uses"], dict(summed["usage_json"])
    elif stored_sum is not None:
        tokens, duration, tools, usage = member["total_tokens"], member["duration_ms"], member["tool_uses"], stored_sum
    else:
        tokens, duration, tools, usage = None, None, None, {"source": "PostToolUse"}
    if completion is not None:
        if as_int(completion.get("totalDurationMs")) is not None:
            duration = as_int(completion["totalDurationMs"])
        if as_int(completion.get("totalToolUseCount")) is not None:
            tools = as_int(completion["totalToolUseCount"])
        usage["completion"] = completion
    return {"total_tokens": tokens, "duration_ms": duration, "tool_uses": tools, "usage_json": json.dumps(usage)}


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# How a transcript sum counts.  The harness writes an API response as one transcript entry per
# content block, each repeating the response's message.id, its requestId and the request's usage, with only
# output_tokens growing to the final count (a read-only survey of five live subagent transcripts on
# 2026-09-13: 177 requests, every entry one block, the last entry of each request the maximum on every key).
# An older spud added every entry, so a request counted once per block and the tokens ran four to five
# times too high.  A sum counted once per request carries "counting": "request"; a stored transcript sum
# without it is found and re-summed by `spud member resum`.
REQUEST_COUNTING = "request"
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

# The per-model breakdown a transcript sum keeps beside its usage: what a run's list-price cost is computed
# from (run_cost).  A read-only survey of the live transcripts on 2026-09-13 (1,590 requests in 46 files) found every
# request's usage with its model (message.model), a service_tier and an inference_geo, nearly all with a speed; a
# cache_creation split by TTL that sums to cache_creation_input_tokens (one exception, a server-side fallback's request:
# 4,369 split beside a total of 0); server_tool_use counts; and usage.iterations holding one message iteration (the same
# exception held a fallback_message attempt).
BREAKDOWN_IDENTITY = ("model", "speed", "service_tier", "inference_geo")
BREAKDOWN_TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens")

# A request billed per attempt (SPD-220).  usage.iterations records each sampling attempt of one API request, in order: a
# compaction (the summary the API writes of earlier context), a message for each model that declined the request and a
# fallback_message for the model that served it (server-side fallback).  The Claude API documentation, read on 2026-09-23:
# - platform.claude.com/docs/en/build-with-claude/refusals-and-fallback, "Billing and rate limits": an attempt that declined
#   before producing any output is not billed, its tokens reported on its usage.iterations entry but not charged; every
#   attempt that produced output, one that declined partway through included, is billed separately at the rates of the
#   model that ran it; usage.iterations is the per-attempt record of what is billed, and the top-level usage describes only
#   the attempt that produced the returned message.  "A model that declined appears as an ordinary message entry"; when
#   every model declines, the response is the last model's refusal.  The same page: the API runs "the same request" on the
#   fallback model.
# - .../compaction-threshold, "Understanding usage", and .../compaction-on-demand, "Count compaction usage": the top-level
#   input_tokens and output_tokens exclude compaction and reflect the non-compaction iterations; the tokens consumed and
#   billed are the sum across usage.iterations; the summarization call "uses the request's model" and is billed whatever
#   it returns.
# The live transcripts agree.  A read-only survey on 2026-09-23 (2,137 files, 106,384 requests carrying usage.iterations)
# found four requests that fell back, SPUD-094/Tim's msg_011CfKaSPRN9q2ds7wVRRfSB and SPUD-217/Hector's
# msg_011CfLxEUXfjaQRcanJbrLFF among them.  Each held [message on the requested model (claude-fable-5-1, claude-opus-5-5),
# declined mid-output after 427 to 2,291 output tokens; fallback_message on claude-opus-4-8], every iteration naming its
# model and carrying input, output, cache-read and cache-write tokens with a TTL split that adds up, and none a speed, a
# service tier, an inference geography or a server-tool count.  Their top-level usage is the fallback attempt's, beside the
# declined attempt's stale TTL split (the 4,369 split beside a total of 0 above is one).  A message iteration of a request
# served in one attempt names no model (the key absent, or null); no request compacted.
# A request served in one attempt is billed by the same rule (SPD-224).  The refusals-and-fallback page, "How refusals are
# billed", read on 2026-09-24: "You are not billed for a refusal that arrives before any output. `content` is empty, and
# token counts appear in `usage` but are not charged."  A mid-stream refusal "bills the input tokens and the output already
# streamed at normal rates".  The page's refusal is stop_reason "refusal" with output_tokens 0, and the rule names no
# fallback: a request that set none, or whose fallback the API skipped, is one attempt.  A read-only survey of the same 2,137
# files on 2026-09-23 found 11 requests that ended in a refusal, every one mid-output and so billed.
PER_ATTEMPT = ("message", "fallback_message", "compaction")  # the iteration types a request is summed and priced by, attempt by attempt
KEPT_WHOLE = "iterations"  # before SPD-220 a breakdown kept a request billed per attempt whole, counting its iteration types here
UNPRICED_ITERATIONS = "unpriced_iterations"  # since: a request kept whole for an iteration type outside PER_ATTEMPT, those types


def usage_count(value):
    """A usage figure as a sum adds it: an int or a float, never a bool, as an int; anything else counts 0."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def request_attempts(model, usage, stop_reason=None):
    """(the attempts one API request billed, [(the model that ran it, its usage figures)]; the iteration types that kept it
    whole, {} when none), by the rule and the evidence above.  A request served in one attempt (no usage.iterations, an
    empty list, or a single message iteration) is its top-level usage on message.model, or no attempt when it ended in a
    refusal with no output tokens: a refusal before any output is not billed.  Any other is summed from
    usage.iterations alone, the top-level figures never added (they repeat the serving attempt, or the non-compaction
    iterations).  An attempt ran on the model it names; one that names none ran on message.model when the request did
    not fall back (the model it asked for and was served by, which a compaction uses), and on no model the breakdown can
    price when it did (the model it asked for is not recorded).  A declined attempt (each message iteration of a request
    holding a fallback_message, and the last attempt but a compaction when the request ended in a refusal) that reports
    no output tokens is left out: it is not billed.  A request whose iterations hold a type outside PER_ATTEMPT is kept
    whole, its top-level usage on message.model, with those types counted: its bill is not known."""
    iterations = usage.get("iterations")
    if not isinstance(iterations, list) or not iterations or (
            len(iterations) == 1 and isinstance(iterations[0], dict) and iterations[0].get("type") == "message"):
        return ([] if stop_reason == "refusal" and not usage_count(usage.get("output_tokens")) else [(model, usage)]), {}
    kinds = [it["type"] if isinstance(it, dict) and isinstance(it.get("type"), str) else "unknown" for it in iterations]
    outside = {}
    for kind in kinds:
        if kind not in PER_ATTEMPT:
            outside[kind] = outside.get(kind, 0) + 1
    if outside:
        return [(model, usage)], outside
    fell_back = "fallback_message" in kinds
    attempts = []
    for index, (kind, iteration) in enumerate(zip(kinds, iterations)):
        declined = (kind == "message" and fell_back) or (index == len(kinds) - 1 and kind != "compaction" and stop_reason == "refusal")
        if declined and not usage_count(iteration.get("output_tokens")):
            continue
        named = iteration.get("model")
        attempts.append((named if isinstance(named, str) and named else None if fell_back else model, iteration))
    return attempts, {}


def breakdown_bucket(buckets, model, usage):
    """The bucket of usage_breakdown's buckets for this model and the speed, service tier and inference geography this
    usage names, made empty when it is new."""
    identity = tuple(v if isinstance(v, str) and v else None for v in (model,) + tuple(usage.get(k) for k in BREAKDOWN_IDENTITY[1:]))
    bucket = buckets.get(identity)
    if bucket is None:
        bucket = buckets[identity] = {k: v for k, v in zip(BREAKDOWN_IDENTITY, identity) if v is not None}
        bucket.update(requests=0, **dict.fromkeys(BREAKDOWN_TOKENS, 0))
    return bucket


def usage_breakdown(requests):
    """[(message.model, usage, stop_reason)] of a transcript's requests, each by its last entry, as the attempts each
    billed (request_attempts), grouped into buckets by the model that ran the attempt and the request's speed, service
    tier and inference geography as the transcript writes them (a key left out when the usage has none), sorted by those
    four.  An attempt takes its request's speed, tier and geography: the iterations carry none, and each attempt runs the
    same request.  A bucket keeps the requests its model served (message.model), each counted once, so a model that only
    billed declined attempts has a bucket of 0 requests; the input, output and cache-read tokens of the attempts it ran; their
    cache writes by TTL under cache_creation when an attempt's split adds up to its cache_creation_input_tokens, as the API
    documents it, else as cache_creation_unsplit_input_tokens; the usage.server_tool_use counts of the requests it served
    (the iterations carry none, and a server tool's fee does not depend on the model); and, under unpriced_iterations, the
    iteration types of a request kept whole because request_attempts does not price them.  Its token figures add up to
    the sum's usage, which counts every billed attempt."""
    buckets = {}
    for model, usage, stop_reason in requests:
        attempts, outside = request_attempts(model, usage, stop_reason)
        served = breakdown_bucket(buckets, model, usage)
        served["requests"] += 1
        for by, figures in attempts:
            bucket = breakdown_bucket(buckets, by, usage)
            for k in BREAKDOWN_TOKENS:
                bucket[k] += usage_count(figures.get(k))
            writes = usage_count(figures.get("cache_creation_input_tokens"))
            split = figures.get("cache_creation")
            split = {k: usage_count(v) for k, v in split.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(split, dict) else None
            if split is not None and sum(split.values()) == writes:
                into = bucket.setdefault("cache_creation", {})
                for k, v in split.items():
                    into[k] = into.get(k, 0) + v
            elif writes:
                bucket["cache_creation_unsplit_input_tokens"] = bucket.get("cache_creation_unsplit_input_tokens", 0) + writes
        tools = usage.get("server_tool_use")
        if isinstance(tools, dict):
            into = served.setdefault("server_tool_use", {})
            for k, v in tools.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    into[k] = into.get(k, 0) + int(v)
        if outside:
            into = served.setdefault(UNPRICED_ITERATIONS, {})
            for kind, n in outside.items():
                into[kind] = into.get(kind, 0) + n
    return [buckets[identity] for identity in sorted(buckets, key=lambda identity: tuple(v or "" for v in identity))]


def kept_whole(breakdown):
    """Whether a stored breakdown keeps a request billed per attempt whole, as one stored before SPD-220 did (its
    iteration types counted under iterations): `spud member resum` sums it again, attempt by attempt."""
    return isinstance(breakdown, list) and any(isinstance(b, dict) and KEPT_WHOLE in b for b in breakdown)


def request_key(entry, index):
    """The API request a transcript's assistant entry belongs to: its message.id with its requestId (None
    when the entry has none).  An entry without a message id is a request of its own, whatever else it
    carries; index is its line in the file."""
    message_id = entry["message"].get("id")
    if not isinstance(message_id, str) or not message_id:
        return ("entry", index)
    request_id = entry.get("requestId")
    return ("request", message_id, request_id if isinstance(request_id, str) and request_id else None)


def transcript_usage(path):
    """A subagent transcript summed once per API request: a member's tokens whatever the spawn
    shape, since a foreground completion's figures cover only its final request (run_totals keeps
    both).  The assistant entries are grouped by request (request_key) and each request counts once,
    by the usage of its last entry; messages counts the requests.  tool_uses counts distinct tool_use
    block ids (a block without an id counts where it appears), the same whether a response's blocks come
    one per entry or together; duration_ms runs from the first timestamp in the file to the last.  The
    sum is marked "counting": "request" and keeps the per-model breakdown of its requests
    (usage_breakdown) that a list-price cost is computed from.  Its usage counts what each request billed: a request
    served in one attempt by its top-level usage, one billed per attempt by the attempts it billed (request_attempts),
    so a declined attempt that produced output and a compaction count, and one declined before any output does not,
    nor a request served in one attempt that was refused before any output.
    None when the file holds no assistant usage; OSError when it cannot be read."""
    requests = {}
    tool_ids = set()
    tools_without_id = 0
    first = last = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for index, line in enumerate(f):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            ts = parse_timestamp(entry.get("timestamp"))
            if ts is not None:
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last
            msg = entry.get("message")
            if entry.get("type") != "assistant" or not isinstance(msg, dict):
                continue
            if isinstance(msg.get("usage"), dict):  # a later entry of the request replaces an earlier one
                requests[request_key(entry, index)] = (msg.get("model"), msg["usage"], msg.get("stop_reason"))
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if isinstance(block.get("id"), str) and block["id"]:
                            tool_ids.add(block["id"])
                        else:
                            tools_without_id += 1
    if not requests:
        return None
    totals = dict.fromkeys(TOKEN_KEYS, 0)
    for model, usage, stop_reason in requests.values():
        for _, figures in request_attempts(model, usage, stop_reason)[0]:
            for k in TOKEN_KEYS:
                totals[k] += usage_count(figures.get(k))
    duration = int((last - first).total_seconds() * 1000) if first is not None and last is not None else None
    return {"total_tokens": sum(totals.values()), "duration_ms": duration, "tool_uses": len(tool_ids) + tools_without_id,
            "usage_json": {"source": "transcript", "counting": REQUEST_COUNTING, "messages": len(requests), "usage": totals,
                           "breakdown": usage_breakdown(requests.values())}}
