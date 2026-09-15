"""state/transcripts: Transcript sums and the stored usage columns.  Moved from bin/spud_ledger.py (SPD-065)."""

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


# How a transcript sum counts (SPD-023).  The harness writes an API response as one transcript entry per
# content block, each repeating the response's message.id, its requestId and the request's usage, with only
# output_tokens growing to the final count (a read-only survey of five live subagent transcripts on
# 2026-09-13: 177 requests, every entry one block, the last entry of each request the maximum on every key).
# Before SPD-023 every entry was added, so a request counted once per block and the tokens ran four to five
# times too high.  A sum counted once per request carries "counting": "request"; a stored transcript sum
# without it is found and re-summed by `spud member resum`.
REQUEST_COUNTING = "request"
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

# The per-model breakdown a transcript sum keeps beside its usage since SPD-013: what a run's list-price cost is computed
# from (run_cost).  A read-only survey of the live transcripts on 2026-09-13 (1,590 requests in 46 files) found every
# request's usage with its model (message.model), a service_tier and an inference_geo, nearly all with a speed; a
# cache_creation split by TTL that sums to cache_creation_input_tokens (one exception, a server-side fallback's request:
# 4,369 split beside a total of 0); server_tool_use counts; and usage.iterations holding one message iteration (the same
# exception held a fallback_message attempt).  The API bills a compaction iteration beside the top-level figures and a
# fallback per attempt, neither of which the top level shows whole.
BREAKDOWN_IDENTITY = ("model", "speed", "service_tier", "inference_geo")
BREAKDOWN_TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens")


def usage_count(value):
    """A usage figure as a sum adds it: an int or a float, never a bool, as an int; anything else counts 0."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def usage_breakdown(requests):
    """[(message.model, usage)] of a transcript's requests, each by its last entry, grouped into buckets by model, speed,
    service tier and inference geography as the transcript writes them (a key left out when the usage has none), sorted
    by those four.  A bucket keeps its requests; its input, output and cache-read tokens; its cache writes by TTL under
    cache_creation when the request's split adds up to cache_creation_input_tokens, as the API documents it, else as
    cache_creation_unsplit_input_tokens; its usage.server_tool_use counts; and, under iterations, the iteration types of
    any request whose usage.iterations holds more than one message iteration (a compaction, a fallback's attempts),
    which the top-level figures do not bill whole.  Its token figures add up to the sum's usage."""
    buckets = {}
    for model, usage in requests:
        identity = tuple(v if isinstance(v, str) and v else None for v in (model,) + tuple(usage.get(k) for k in BREAKDOWN_IDENTITY[1:]))
        bucket = buckets.get(identity)
        if bucket is None:
            bucket = buckets[identity] = {k: v for k, v in zip(BREAKDOWN_IDENTITY, identity) if v is not None}
            bucket.update(requests=0, **dict.fromkeys(BREAKDOWN_TOKENS, 0))
        bucket["requests"] += 1
        for k in BREAKDOWN_TOKENS:
            bucket[k] += usage_count(usage.get(k))
        writes = usage_count(usage.get("cache_creation_input_tokens"))
        split = usage.get("cache_creation")
        split = {k: usage_count(v) for k, v in split.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(split, dict) else None
        if split is not None and sum(split.values()) == writes:
            into = bucket.setdefault("cache_creation", {})
            for k, v in split.items():
                into[k] = into.get(k, 0) + v
        elif writes:
            bucket["cache_creation_unsplit_input_tokens"] = bucket.get("cache_creation_unsplit_input_tokens", 0) + writes
        tools = usage.get("server_tool_use")
        if isinstance(tools, dict):
            into = bucket.setdefault("server_tool_use", {})
            for k, v in tools.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    into[k] = into.get(k, 0) + int(v)
        iterations = usage.get("iterations")
        if isinstance(iterations, list) and iterations and not (
                len(iterations) == 1 and isinstance(iterations[0], dict) and iterations[0].get("type") == "message"):
            into = bucket.setdefault("iterations", {})
            for iteration in iterations:
                kind = iteration.get("type") if isinstance(iteration, dict) and isinstance(iteration.get("type"), str) else "unknown"
                into[kind] = into.get(kind, 0) + 1
    return [buckets[identity] for identity in sorted(buckets, key=lambda identity: tuple(v or "" for v in identity))]


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
    """A subagent transcript summed once per API request (SPD-023): a member's tokens whatever the spawn
    shape, since a foreground completion's figures cover only its final request (run_totals keeps both,
    SPD-021).  The assistant entries are grouped by request (request_key) and each request counts once,
    by the usage of its last entry; messages counts the requests.  tool_uses counts distinct tool_use
    block ids (a block without an id counts where it appears), the same whether a response's blocks come
    one per entry or together; duration_ms runs from the first timestamp in the file to the last.  The
    sum is marked "counting": "request" and keeps, since SPD-013, the per-model breakdown of its requests
    (usage_breakdown) that a list-price cost is computed from.  None when the file holds no assistant usage;
    OSError when it cannot be read."""
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
            if isinstance(msg.get("usage"), dict):
                requests[request_key(entry, index)] = (msg.get("model"), msg["usage"])  # a later entry of the request replaces an earlier one
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
    for _, usage in requests.values():
        for k in TOKEN_KEYS:
            v = usage.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                totals[k] += int(v)
    duration = int((last - first).total_seconds() * 1000) if first is not None and last is not None else None
    return {"total_tokens": sum(totals.values()), "duration_ms": duration, "tool_uses": len(tool_ids) + tools_without_id,
            "usage_json": {"source": "transcript", "counting": REQUEST_COUNTING, "messages": len(requests), "usage": totals,
                           "breakdown": usage_breakdown(requests.values())}}
