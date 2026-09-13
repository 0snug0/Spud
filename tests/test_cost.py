"""Token and cost accounting per member and per ticket (SPD-013).

A transcript sum keeps, beside the four-key usage the Team card reads, a per-model breakdown: its API requests
grouped by message.model and by the usage's speed, service_tier and inference_geo as the transcript records them,
each group with its requests, its input, output and cache-read tokens, its cache writes split by TTL
(cache_creation.ephemeral_5m_input_tokens and ephemeral_1h_input_tokens, else unsplit), its server-tool requests,
and the iterations of any request billed per attempt.  Cost is the API list price in USD, computed when the card
and the notes render, from that breakdown and the dated price table spud.config.json gives (`pricing`); it is
never stored, so a price change only re-renders.  tests/fixtures/pricing.json is that table as read from
https://platform.claude.com/docs/en/about-claude/pricing on 2026-09-13.  Every case runs in a scratch SPUD_HOME
over hand-built transcripts in the shape of the live ones (the usage keys a read-only survey of them found)."""

import copy
import json
import unittest
from fractions import Fraction
from pathlib import Path

from helpers import EXIT_ERROR, TEAM_TABLE_DELIMITER, TEAM_TABLE_HEADER, SpudTestCase, load_spud_module, real_config
from test_hooks import AGENT_A, AGENT_B, COMPLETION, TWO_REQUESTS_SUM, HookCase
from test_team_card import MEMBER, TICKET_ONE, TICKET_TWO, YUKON_USAGE, cells, frontmatter, member_of, table_rows, team_section, total_row

spud = load_spud_module()
PRICING = json.loads((Path(__file__).resolve().parent / "fixtures" / "pricing.json").read_text(encoding="utf-8"))["pricing"]
LIST_PRICE = "at API list price (USD, prices as of 2026-09-13)"
NO_TABLE = "no price table in spud.config.json"
NO_BREAKDOWN = "no breakdown, which `spud member resum` adds"


def config_with(pricing):
    """The repository's config with this price table in it, or with none when pricing is None, whatever
    spud.config.json holds today."""
    config = real_config()
    config.pop("pricing", None)
    if pricing is not None:
        config["pricing"] = copy.deepcopy(pricing)
    return config


def repriced(models):
    """The fixture's table with some rates changed, e.g. {"claude-sonnet-5": {"output": 15}}."""
    pricing = copy.deepcopy(PRICING)
    for model, rates in models.items():
        pricing["models"][model].update(rates)
    return pricing


def table(pricing=PRICING):
    """The price table bin/spud reads from a config holding this block; a problem with it fails the test."""
    found, problems = spud.price_table({"pricing": copy.deepcopy(pricing)})
    if problems:
        raise AssertionError(problems)
    return found


# -- transcripts in the shape of the live ones ------------------------------------------------------------------


def usage(input_tokens=0, output_tokens=0, write_5m=0, write_1h=0, read=0, web_search=0, web_fetch=0, speed="standard", tier="standard", geo="not_available"):
    """A request's usage as Claude Code's transcripts record it (the keys of the 2026-09-13 survey): the four token
    keys, the TTL split, the thinking share of the output, server-tool counts, the service tier, the inference
    geography, the speed, and the one message iteration a request served in one attempt carries."""
    figures = {"input_tokens": input_tokens, "cache_creation_input_tokens": write_5m + write_1h, "cache_read_input_tokens": read, "output_tokens": output_tokens}
    split = {"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": write_1h}
    return dict(figures, cache_creation=split, output_tokens_details={"thinking_tokens": 0},
                server_tool_use={"web_search_requests": web_search, "web_fetch_requests": web_fetch},
                service_tier=tier, inference_geo=geo, speed=speed, iterations=[dict(figures, type="message", cache_creation=dict(split))])


def entry(message_id, model, figures, ts, block=None):
    """One transcript entry of an API response: a content block, with the response's id, model and requestId and
    the request's usage (repeated on every entry of the request, output_tokens growing)."""
    return {"type": "assistant", "timestamp": ts, "requestId": "req_" + message_id, "message": {
        "id": message_id, "type": "message", "role": "assistant", "model": model,
        "content": [block if block is not None else {"type": "text", "text": "x"}], "usage": figures}}


def user(ts):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": "go"}}


def tool_use(block_id):
    return {"type": "tool_use", "id": block_id, "name": "Bash", "input": {}}


def one_request(model, **figures):
    """A run of one API request on this model, with these usage figures."""
    return [user("2026-09-13T09:00:00.000Z"), entry("msg_1", model, usage(**figures), "2026-09-13T09:00:01.000Z")]


FIRST = dict(input_tokens=400, write_5m=300_000)


def two_models():
    """Proof 1's run: three requests on two models.  msg_A (claude-opus-5) is written as two entries, its output
    growing to 60,000, and writes 300,000 tokens to the 5-minute cache; msg_B (claude-sonnet-5) searches the web
    once; msg_C (claude-opus-5) writes 100,000 tokens to the 1-hour cache, searches three times and fetches twice."""
    return [
        user("2026-09-13T09:00:00.000Z"),
        entry("msg_A", "claude-opus-5", usage(output_tokens=5, **FIRST), "2026-09-13T09:00:01.000Z", {"type": "thinking", "thinking": "", "signature": "s"}),
        entry("msg_A", "claude-opus-5", usage(output_tokens=60_000, **FIRST), "2026-09-13T09:00:02.000Z", tool_use("toolu_A1")),
        user("2026-09-13T09:00:03.000Z"),
        entry("msg_B", "claude-sonnet-5", usage(input_tokens=500, output_tokens=50_000, write_5m=2_000, read=1_000_000, web_search=1), "2026-09-13T09:00:05.000Z"),
        entry("msg_C", "claude-opus-5", usage(input_tokens=600, output_tokens=40_000, write_1h=100_000, read=2_000_000, web_search=3, web_fetch=2),
              "2026-09-13T09:00:10.000Z", tool_use("toolu_C1")),
    ]


TWO_MODELS_USAGE = {"input_tokens": 1_500, "output_tokens": 150_000, "cache_creation_input_tokens": 402_000, "cache_read_input_tokens": 3_000_000}
TWO_MODELS_BREAKDOWN = [
    {"model": "claude-opus-5", "speed": "standard", "service_tier": "standard", "inference_geo": "not_available", "requests": 2,
     "input_tokens": 1_000, "output_tokens": 100_000, "cache_read_input_tokens": 2_000_000,
     "cache_creation": {"ephemeral_5m_input_tokens": 300_000, "ephemeral_1h_input_tokens": 100_000},
     "server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 2}},
    {"model": "claude-sonnet-5", "speed": "standard", "service_tier": "standard", "inference_geo": "not_available", "requests": 1,
     "input_tokens": 500, "output_tokens": 50_000, "cache_read_input_tokens": 1_000_000,
     "cache_creation": {"ephemeral_5m_input_tokens": 2_000, "ephemeral_1h_input_tokens": 0},
     "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}},
]


# -- stored sums ---------------------------------------------------------------------------------------------------


def bucket(model="claude-opus-5", requests=1, input_tokens=0, output_tokens=0, read=0, write_5m=0, write_1h=0, unsplit=0, web_search=0, web_fetch=0, **identity):
    """A breakdown entry as transcript_usage keeps it; identity holds speed, service_tier and inference_geo when given."""
    b = {} if model is None else {"model": model}
    b.update(identity)
    b.update(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens, cache_read_input_tokens=read,
             cache_creation={"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": write_1h},
             server_tool_use={"web_search_requests": web_search, "web_fetch_requests": web_fetch})
    if unsplit:
        b["cache_creation_unsplit_input_tokens"] = unsplit
    return b


def summed(*buckets, **extra):
    """A stored transcript sum (usage_json text) over these breakdown entries, its four-key usage their totals."""
    def total(key):
        return sum(b.get(key, 0) for b in buckets)

    writes = sum(sum(b.get("cache_creation", {}).values()) + b.get("cache_creation_unsplit_input_tokens", 0) for b in buckets)
    stored = {"source": "transcript", "counting": "request", "messages": total("requests"), "breakdown": list(buckets), "usage": {
        "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
        "cache_creation_input_tokens": writes, "cache_read_input_tokens": total("cache_read_input_tokens")}}
    stored.update(extra)
    return json.dumps(stored)


def flatten(nodes):
    flat = []
    for n in nodes:
        flat.append(n)
        flat.extend(flatten(n["children"]))
    return flat


# =============================================================================
# The breakdown a transcript sum keeps (proof 1)
# =============================================================================


class BreakdownTest(HookCase):
    def sum_of(self, entries):
        return spud.transcript_usage(self.write_transcript(AGENT_A, entries))

    def test_two_models_ttl_writes_and_server_tools_sum_into_the_breakdown(self):  # proof 1
        got = self.sum_of(two_models())
        self.assertEqual(got["usage_json"]["breakdown"], TWO_MODELS_BREAKDOWN)
        # the four-key usage, the counting and everything else a sum stored before SPD-013 are unchanged
        self.assertEqual({k: v for k, v in got["usage_json"].items() if k != "breakdown"},
                         {"source": "transcript", "counting": "request", "messages": 3, "usage": TWO_MODELS_USAGE})
        self.assertEqual((got["total_tokens"], got["duration_ms"], got["tool_uses"]), (3_553_500, 10_000, 2))
        self.assertEqual(spud.token_counts(json.dumps(got["usage_json"])), {"out": 150_000, "in": 403_500, "cached": 3_000_000})

    def test_a_stop_records_the_breakdown(self):  # proof 1, through the hooks
        m = self.plan(persona="engineer", model="opus")
        self.spawn(m, AGENT_A)
        self.home.json("member", "result", "Built it.", actor=AGENT_A)
        path = self.write_transcript(AGENT_A, two_models())
        r = self.home.hook("SubagentStop", self.sub_stop(AGENT_A, transcript=str(path)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        row = self.home.rows("SELECT total_tokens, usage_json FROM members WHERE id = ?", m["id"])[0]
        self.assertEqual(row["total_tokens"], 3_553_500)
        self.assertEqual(json.loads(row["usage_json"]), {"source": "transcript", "counting": "request", "messages": 3,
                                                          "usage": TWO_MODELS_USAGE, "breakdown": TWO_MODELS_BREAKDOWN})

    def test_what_makes_a_bucket(self):
        """One bucket per model, speed, service tier and inference geography as the transcript writes them, a key left
        out when the usage has none, sorted by those four; a request counts in its bucket by its last entry."""
        no_model = {"type": "assistant", "timestamp": "2026-09-13T09:00:06.000Z", "requestId": "req_6",
                    "message": {"id": "msg_6", "role": "assistant", "content": [{"type": "text", "text": "x"}], "usage": {"output_tokens": 6}}}
        got = self.sum_of([
            entry("msg_1", "claude-sonnet-5", usage(output_tokens=1, speed="fast"), "2026-09-13T09:00:01.000Z"),
            entry("msg_2", "claude-opus-5", usage(output_tokens=2, geo="us"), "2026-09-13T09:00:02.000Z"),
            entry("msg_3", "claude-opus-5", usage(output_tokens=3, tier="batch"), "2026-09-13T09:00:03.000Z"),
            entry("msg_4", "claude-opus-5", usage(output_tokens=1), "2026-09-13T09:00:04.000Z"),
            entry("msg_4", "claude-opus-5", usage(output_tokens=4), "2026-09-13T09:00:04.500Z"),
            entry("msg_5", "claude-opus-5", {"input_tokens": 1, "output_tokens": 5}, "2026-09-13T09:00:05.000Z"),
            no_model,
            entry("msg_8", "claude-opus-5", usage(output_tokens=8), "2026-09-13T09:00:08.000Z"),
        ])
        breakdown = got["usage_json"]["breakdown"]
        self.assertEqual([(b.get("model"), b.get("speed"), b.get("service_tier"), b.get("inference_geo"), b["requests"], b["output_tokens"]) for b in breakdown], [
            (None, None, None, None, 1, 6),
            ("claude-opus-5", None, None, None, 1, 5),
            ("claude-opus-5", "standard", "batch", "not_available", 1, 3),
            ("claude-opus-5", "standard", "standard", "not_available", 2, 12),
            ("claude-opus-5", "standard", "standard", "us", 1, 2),
            ("claude-sonnet-5", "fast", "standard", "not_available", 1, 1),
        ])
        self.assertEqual(breakdown[0], {"requests": 1, "input_tokens": 0, "output_tokens": 6, "cache_read_input_tokens": 0})
        self.assertEqual(breakdown[1], {"model": "claude-opus-5", "requests": 1, "input_tokens": 1, "output_tokens": 5, "cache_read_input_tokens": 0})
        self.assertEqual(got["usage_json"]["messages"], 7)

    def test_cache_writes_count_as_unsplit_unless_their_split_adds_up(self):
        """The TTL split is kept only when it adds up to cache_creation_input_tokens, as the API documents it; else the
        request's writes count as unsplit, whether it has no split (a transcript from before the split) or one that
        disagrees (the one live case: 4,369 tokens split beside a total of 0, on a fallback attempt)."""
        plain = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 700, "cache_read_input_tokens": 0}
        got = self.sum_of([
            entry("msg_1", "claude-opus-5", plain, "2026-09-13T09:00:01.000Z"),
            entry("msg_2", "claude-opus-5", dict(plain, cache_creation_input_tokens=0, cache_creation={"ephemeral_5m_input_tokens": 4369, "ephemeral_1h_input_tokens": 0}), "2026-09-13T09:00:02.000Z"),
            entry("msg_3", "claude-opus-5", dict(plain, cache_creation_input_tokens=50, cache_creation={"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 0}), "2026-09-13T09:00:03.000Z"),
            entry("msg_4", "claude-opus-5", dict(plain, cache_creation_input_tokens=15, cache_creation={"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 5}), "2026-09-13T09:00:04.000Z"),
        ])
        self.assertEqual(got["usage_json"]["breakdown"], [{"model": "claude-opus-5", "requests": 4, "input_tokens": 4, "output_tokens": 4, "cache_read_input_tokens": 0,
                                                           "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 5},
                                                           "cache_creation_unsplit_input_tokens": 750}])
        self.assertEqual(got["usage_json"]["usage"]["cache_creation_input_tokens"], 765)  # 10 + 5 + 750: the breakdown adds up to the usage

    def test_a_request_billed_per_attempt_keeps_its_iterations(self):
        """usage.iterations beyond one message iteration (a server-side fallback's attempts, a compaction) is counted
        with the bucket by type; the top-level figures, which cover the attempt that served the response, stay the sum."""
        attempt = {"type": "message", "input_tokens": 5, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        fallback = usage(input_tokens=5, output_tokens=30)
        fallback["iterations"] = [attempt, dict(attempt, type="fallback_message", model="claude-opus-4-8", output_tokens=30)]
        compacted = usage(input_tokens=7, output_tokens=2)
        compacted["iterations"] = [dict(attempt, type="compaction", input_tokens=180_000, output_tokens=3_500), dict(attempt, input_tokens=7, output_tokens=2)]
        no_iterations = usage(output_tokens=1)
        no_iterations["iterations"] = []
        got = self.sum_of([
            entry("msg_1", "claude-opus-4-8", fallback, "2026-09-13T09:00:01.000Z"),
            entry("msg_2", "claude-opus-4-8", usage(output_tokens=1), "2026-09-13T09:00:02.000Z"),
            entry("msg_3", "claude-opus-4-8", no_iterations, "2026-09-13T09:00:03.000Z"),
            entry("msg_4", "claude-opus-4-8", compacted, "2026-09-13T09:00:04.000Z"),
        ])
        [b] = got["usage_json"]["breakdown"]
        self.assertEqual((b["requests"], b["input_tokens"], b["output_tokens"], b["iterations"]), (4, 12, 34, {"message": 2, "fallback_message": 1, "compaction": 1}))
        self.assertEqual(got["usage_json"]["usage"], {"input_tokens": 12, "output_tokens": 34, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})

    def test_a_run_served_in_one_attempt_records_no_iterations(self):
        got = self.sum_of(two_models())
        self.assertFalse(any("iterations" in b for b in got["usage_json"]["breakdown"]))


# =============================================================================
# Cost at the list price (proofs 2 and 3, the reasons)
# =============================================================================


class CostTest(unittest.TestCase):
    def test_the_proof_run_costs_exactly(self):  # proof 2
        # Per the pricing page, per million tokens.  claude-opus-5: 1,000 input x $5 + 100,000 output x $25 + 2,000,000
        # cache reads x $0.50 + 300,000 5-minute writes x $6.25 + 100,000 1-hour writes x $10 = $6.38, 3 web searches
        # at $10 per 1,000 = $0.03, 2 web fetches free.  claude-sonnet-5: 500 x $2 + 50,000 x $10 + 1,000,000 x $0.20
        # + 2,000 x $2.50 = $0.706, 1 web search $0.01.  In all $7.126, shown $7.13.
        text = json.dumps({"source": "transcript", "counting": "request", "messages": 3, "usage": TWO_MODELS_USAGE, "breakdown": TWO_MODELS_BREAKDOWN})
        cost, reasons = spud.run_cost(text, table())
        self.assertEqual((cost, reasons), (Fraction("7.126"), []))
        self.assertEqual((spud.money(cost), spud.cost_number(cost)), ("$7.13", "7.13"))

    def test_fast_mode_us_only_inference_and_the_batch_tier(self):
        # fast mode on claude-opus-5: $10 input, $50 output, and the caching multipliers on top ($1 reads, $12.50 5m,
        # $20 1h): 10 + 5 + 1 + 1.25 + 2 = $19.25, times 1.1 for US-only inference = $21.175
        fast_us = bucket("claude-opus-5", input_tokens=1_000_000, output_tokens=100_000, read=1_000_000, write_5m=100_000, write_1h=100_000,
                         speed="fast", service_tier="standard", inference_geo="us")
        # the batch tier halves every token price: ($2 + $1) / 2 = $1.50; its 10 web searches are not halved, $0.10
        batch = bucket("claude-sonnet-5", input_tokens=1_000_000, output_tokens=100_000, web_search=10, speed="standard", service_tier="batch", inference_geo="global")
        cost, reasons = spud.run_cost(summed(fast_us, batch), table())
        self.assertEqual((cost, reasons), (Fraction("22.775"), []))
        self.assertEqual(spud.money(cost), "$22.78")

    def test_unsplit_writes_are_priced_at_the_five_minute_rate(self):
        # claude-sonnet-5: 1,000,000 unsplit writes at the 5-minute $2.50 (the API's default TTL), 1,000,000 1-hour writes at $4
        self.assertEqual(spud.run_cost(summed(bucket("claude-sonnet-5", unsplit=1_000_000, write_1h=1_000_000)), table()), (Fraction("6.5"), []))

    def test_a_bucket_with_nothing_billed_costs_nothing_whatever_its_model(self):
        for model in ("<synthetic>", None):
            with self.subTest(model=model):
                self.assertEqual(spud.run_cost(summed(bucket(model), bucket("claude-sonnet-5", output_tokens=100_000)), table()), (Fraction(1), []))

    def test_what_the_table_lacks_leaves_the_run_unpriced(self):  # proof 3, the reasons
        count = {"usage": {"input_tokens": 0, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}
        cases = [
            ("a model the table lacks", [bucket("claude-future-9", output_tokens=1)], {}, ["no price for model claude-future-9"]),
            ("requests that name no model", [bucket(None, output_tokens=1)], {}, ["no price for requests that name no model"]),
            ("fast mode on a model without fast rates", [bucket("claude-sonnet-5", output_tokens=1, speed="fast")], {}, ["no price for speed fast on claude-sonnet-5"]),
            ("a speed the table does not know", [bucket(output_tokens=1, speed="turbo")], {}, ["no price for speed turbo on claude-opus-5"]),
            ("the Priority Tier", [bucket(output_tokens=1, service_tier="priority")], {}, ["no price for service_tier priority"]),
            ("another geography", [bucket(output_tokens=1, inference_geo="eu")], {}, ["no price for inference_geo eu"]),
            ("another cache TTL", [dict(bucket(output_tokens=1), cache_creation={"ephemeral_24h_input_tokens": 5})], {}, ["no price for cache_creation.ephemeral_24h_input_tokens"]),
            ("a server tool billed by the hour", [dict(bucket(output_tokens=1), server_tool_use={"code_execution_requests": 1})], {}, ["no price for server_tool_use.code_execution_requests"]),
            ("a request billed per attempt", [dict(bucket(output_tokens=1), iterations={"message": 1, "fallback_message": 1})], {},
             ["billed per attempt: usage.iterations holds fallback_message, message"]),
            ("one bucket priced and one not", [bucket(output_tokens=1), bucket("claude-future-9", output_tokens=1), bucket("claude-future-9", output_tokens=2, speed="fast")], {},
             ["no price for model claude-future-9"]),
            ("a figure that is no count", [dict(bucket(), output_tokens=-1)], count, ["a breakdown figure that is not a count"]),
            ("an entry that is no object", [bucket(output_tokens=1), [1]], count, ["a breakdown entry that is not an object"]),
        ]
        for label, buckets, extra, reasons in cases:
            with self.subTest(label):
                self.assertEqual(spud.run_cost(summed(*buckets, **extra) if not extra else json.dumps(dict(json.loads(summed(*[b for b in buckets if isinstance(b, dict)])), breakdown=buckets, **extra)), table()),
                                 (None, reasons))

    def test_sums_without_a_breakdown_imports_and_completions(self):
        cases = [
            ("a sum counted by request before SPD-013", json.dumps({"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM}), (None, [NO_BREAKDOWN])),
            ("a sum that added every entry", json.dumps({"source": "transcript", "messages": 5, "usage": TWO_REQUESTS_SUM}), (None, [NO_BREAKDOWN])),
            ("an imported sum with its cost", json.dumps({"source": "transcript", "imported": True, "cost_usd": "12.30", "usage": TWO_REQUESTS_SUM}), (Fraction("12.3"), [])),
            ("an imported sum without one", json.dumps({"source": "transcript", "imported": True, "usage": TWO_REQUESTS_SUM}), (None, ["imported without a cost"])),
            ("a completion alone: nothing to price", json.dumps({"source": "PostToolUse", "completion": COMPLETION}), (None, [])),
            ("a sum beside a completion: only the sum", summed(bucket("claude-sonnet-5", output_tokens=1_000_000), completion=COMPLETION), (Fraction(10), [])),
            ("no usage", None, (None, [])),
            ("usage that is not a count", summed(bucket("claude-sonnet-5", output_tokens=1), usage={"output_tokens": -1}), (None, [])),
        ]
        for label, text, want in cases:
            with self.subTest(label):
                self.assertEqual(spud.run_cost(text, table()), want)
        with self.subTest("no price table"):
            self.assertEqual(spud.run_cost(summed(bucket("claude-sonnet-5", output_tokens=1)), None), (None, [NO_TABLE]))
            self.assertEqual(spud.run_cost(cases[2][1], None), (Fraction("12.3"), []))  # an imported cost needs no table

    def test_money_rounds_half_up_once(self):
        vectors = [(Fraction(0), "$0.00", "0"), (Fraction("0.004999"), "$0.00", "0"), (Fraction("0.005"), "$0.01", "0.01"),
                   (Fraction("0.125"), "$0.13", "0.13"), (Fraction("12.3"), "$12.30", "12.3"), (Fraction(12), "$12.00", "12"),
                   (Fraction("1234.505"), "$1234.51", "1234.51"), (Fraction(1, 3), "$0.33", "0.33"), (Fraction("0.07"), "$0.07", "0.07")]
        self.assertEqual([(spud.money(f), spud.cost_number(f)) for f, _, _ in vectors], [(m, n) for _, m, n in vectors])


class PriceTableTest(SpudTestCase):
    def test_the_fixture_table_has_no_problem(self):
        found, problems = spud.price_table({"pricing": copy.deepcopy(PRICING)})
        self.assertEqual(problems, [])
        self.assertEqual(spud.price_table({}), (None, []))  # no table is no problem: every cost shows a dash
        self.assertEqual(spud.config_problems(config_with(PRICING)), [])

    def test_a_broken_table_is_a_config_problem(self):
        def broke(change):
            pricing = copy.deepcopy(PRICING)
            change(pricing)
            return pricing

        opus = summed(bucket("claude-opus-5", output_tokens=1_000_000))
        sonnet = summed(bucket("claude-sonnet-5", output_tokens=1_000_000))
        cases = [
            ("another currency", broke(lambda p: p.update(currency="EUR")), "pricing.currency", None),
            ("another unit", broke(lambda p: p.update(per="1K tokens")), "pricing.per", None),
            ("an undated table", broke(lambda p: p.update(as_of="Sept 13")), "pricing.as_of", None),
            ("no source", broke(lambda p: p.pop("source")), "pricing.source", (sonnet, (Fraction(10), []))),
            ("a missing rate", broke(lambda p: p["models"]["claude-sonnet-5"].pop("cache_read")), "pricing.models.claude-sonnet-5", (sonnet, (None, ["no price for model claude-sonnet-5"]))),
            ("a negative rate", broke(lambda p: p["models"]["claude-sonnet-5"].update(output=-10)), "pricing.models.claude-sonnet-5", (sonnet, (None, ["no price for model claude-sonnet-5"]))),
            ("a rate in quotes", broke(lambda p: p["models"]["claude-sonnet-5"].update(output="10")), "pricing.models.claude-sonnet-5", (sonnet, (None, ["no price for model claude-sonnet-5"]))),
            ("a rate that is true", broke(lambda p: p["models"]["claude-sonnet-5"].update(output=True)), "pricing.models.claude-sonnet-5", (sonnet, (None, ["no price for model claude-sonnet-5"]))),
            ("fast rates missing one", broke(lambda p: p["models"]["claude-opus-5"]["fast"].pop("output")), "pricing.models.claude-opus-5.fast", (opus, (None, ["no price for model claude-opus-5"]))),
            ("a server tool per nothing", broke(lambda p: p["server_tools"]["web_search_requests"].update(per=0)), "pricing.server_tools.web_search_requests",
             (summed(bucket("claude-sonnet-5", web_search=1)), (None, ["no price for server_tool_use.web_search_requests"]))),
            ("a multiplier in words", broke(lambda p: p["multipliers"]["inference_geo"].update(us="1.1")), "pricing.multipliers.inference_geo.us",
             (summed(bucket("claude-sonnet-5", output_tokens=1, inference_geo="us")), (None, ["no price for inference_geo us"]))),
            ("not an object", ["claude-opus-5"], "pricing is not an object", None),
        ]
        for label, pricing, problem, check in cases:
            with self.subTest(label):
                found, problems = spud.price_table({"pricing": pricing})
                self.assertTrue(any(p.startswith(problem) for p in problems), problems)
                self.assertTrue(any(p.startswith(problem) for p in spud.config_problems(config_with(pricing))))
                if check is None:
                    self.assertIsNone(found)
                else:
                    self.assertEqual(spud.run_cost(check[0], found), check[1])

    def test_doctor_reports_the_table_and_flags_a_broken_one(self):
        self.home.write_config(config_with(PRICING))
        out = self.home.json("doctor")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["pricing"], {"as_of": "2026-09-13", "source": PRICING["source"], "currency": "USD", "models": sorted(PRICING["models"]), "not_priced": []})
        self.assertIn("pricing     API list price in USD as of 2026-09-13 from %s: 5 models" % PRICING["source"], self.home.run("doctor").stdout.splitlines())
        self.home.write_config(config_with(None))
        out = self.home.json("doctor")
        self.assertEqual((out["ok"], out["pricing"]), (True, None))
        self.assertIn("pricing     %s: every cost shows —" % NO_TABLE, self.home.run("doctor").stdout.splitlines())
        self.home.write_config(config_with(repriced({"claude-sonnet-5": {"output": -10}})))
        proc = self.home.run("--json", "doctor", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertTrue(any("pricing.models.claude-sonnet-5" in p for p in json.loads(proc.stdout)["problems"]), proc.stdout)


# =============================================================================
# Where cost shows: the card, the Team table, the member note (proofs 3, 5 and 6)
# =============================================================================


class PricedCase(HookCase):
    """A scratch home whose config holds the fixture's price table, and members run through the hooks."""

    config = config_with(PRICING)

    def run_member(self, agent_id, entries, name, persona="engineer", model="opus"):
        """A member spawned in the background, its Result recorded, and its SubagentStop fired over these entries."""
        m = self.plan(persona=persona, model=model, name=name)
        self.spawn(m, agent_id)
        self.home.json("member", "result", "Built it.", actor=agent_id)
        path = self.write_transcript(agent_id, entries)
        r = self.home.hook("SubagentStop", self.sub_stop(agent_id, transcript=str(path)))
        self.assertEqual((r.code, r.stdout), (0, ""), r)
        return m

    def render_out(self):
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        return out

    def section(self, out):
        return team_section((out / "ledger" / "tickets" / ("%s.md" % self.t["key"])).read_text(encoding="utf-8"))

    def note(self, out, m):
        return (out / "ledger" / "teams" / self.team / ("%s.md" % m["name"])).read_text(encoding="utf-8")

    def cost_lines(self, text):
        """A note's frontmatter from tokens_out on."""
        lines = frontmatter(text)
        starts = [i for i, line in enumerate(lines) if line.startswith("tokens_out:")]
        return lines[starts[0]:] if starts else []

    def card_lines(self):
        return self.home.run("card", self.t["key"]).stdout.rstrip("\n").split("\n")

    @staticmethod
    def line_of(lines, name):
        return next(line for line in lines if line.startswith("- %s (" % name))


class CardAndNotesTest(PricedCase):
    def half_cent_team(self):
        """Russet and Yukon each ran one claude-sonnet-5 request writing 500 tokens: $0.005 each at $10 per million.
        Kennebec is planned and never ran."""
        russet = self.run_member(AGENT_A, one_request("claude-sonnet-5", output_tokens=500), "Russet")
        yukon = self.run_member(AGENT_B, one_request("claude-sonnet-5", output_tokens=500), "Yukon")
        kennebec = self.plan(name="Kennebec")
        return russet, yukon, kennebec

    def test_the_card_prints_each_members_cost_and_the_ticket_total(self):  # proof 5, the card
        self.half_cent_team()
        lines = self.card_lines()
        for name in ("Russet", "Yukon"):
            self.assertTrue(self.line_of(lines, name).endswith("; 500 out · 0 in · 0 cached · $0.01"), lines)
        self.assertNotIn(" · ", self.line_of(lines, "Kennebec"))  # no usage: its line is as before
        # $0.005 twice is $0.01, rounded once for the total, not $0.01 twice
        self.assertEqual(lines[-1], "total: 1.0k out · 0 in · 0 cached · $0.01 %s" % LIST_PRICE)
        card = self.home.json("card", self.t["key"])
        nodes = {n["name"]: n for n in flatten(card["team"])}
        self.assertEqual({name: (n["tokens"], n["cost_usd"], n["not_priced"]) for name, n in nodes.items()}, {
            "Russet": ({"out": 500, "in": 0, "cached": 0}, "0.01", []),
            "Yukon": ({"out": 500, "in": 0, "cached": 0}, "0.01", []),
            "Kennebec": (None, None, []),
        })
        self.assertEqual(card["total"], {"tokens": {"out": 1000, "in": 0, "cached": 0}, "cost_usd": "0.01", "partial": False, "not_priced": [], "tool_uses": 0,
                                         "pricing": {"as_of": "2026-09-13", "source": PRICING["source"], "currency": "USD"}})

    def test_the_team_table_and_the_notes_carry_the_same(self):  # proof 5, the table and the notes
        russet, yukon, kennebec = self.half_cent_team()
        out = self.render_out()
        section = self.section(out)
        self.assertEqual(section.split("\n")[:2], [TEAM_TABLE_HEADER, TEAM_TABLE_DELIMITER])
        self.assertEqual(TEAM_TABLE_HEADER, "| Member | ID | Persona | Model | Status | Run | Tokens | Cost (list) | Tools |")
        rows = {member_of(r): cells(r) for r in table_rows(section)}
        self.assertEqual({name: row[6:] for name, row in rows.items()}, {
            "Russet": ["500 out · 0 in · 0 cached", "$0.01", "0"],
            "Yukon": ["500 out · 0 in · 0 cached", "$0.01", "0"],
            "Kennebec": ["—", "—", "—"],
        })
        self.assertEqual(total_row(section), "| **Total** |  |  |  |  |  | 1.0k out · 0 in · 0 cached | $0.01 | 0 |")
        for m in (russet, yukon):
            self.assertEqual(self.cost_lines(self.note(out, m)), ["tokens_out: 500", "tokens_in: 0", "tokens_cached: 0", "cost_usd: 0.01", "tags: [spudagent]"])
        self.assertNotIn("cost_usd", self.note(out, kennebec))

    def test_a_completion_is_never_priced(self):
        summed_row, alone = self.plan(name="Russet"), self.plan(name="Yukon")
        self.set_member(summed_row["id"], total_tokens=1_000_000, duration_ms=4791, tool_uses=3,
                        usage_json=summed(bucket("claude-sonnet-5", output_tokens=1_000_000), completion=COMPLETION))
        self.set_member(alone["id"], duration_ms=4791, tool_uses=1, usage_json=json.dumps({"source": "PostToolUse", "completion": COMPLETION}))
        section = self.section(self.render_out())
        rows = {member_of(r): cells(r)[6:] for r in table_rows(section)}
        self.assertEqual(rows, {"Russet": ["1.0M out · 0 in · 0 cached", "$10.00", "3"], "Yukon": ["—", "—", "1"]})
        self.assertEqual(total_row(section), "| **Total** |  |  |  |  |  | 1.0M out · 0 in · 0 cached | $10.00 | 4 |")  # a completion alone makes no total partial

    def test_import_file_refuses_an_edited_cost(self):  # proof 5, the refusal
        m = self.run_member(AGENT_A, one_request("claude-sonnet-5", output_tokens=1_000_000), "Russet")
        self.home.json("render")
        path = self.home.path / "ledger" / "teams" / self.team / ("%s.md" % m["name"])
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("cost_usd: 10\n"), 1)
        before = self.home.scalar("SELECT usage_json FROM members WHERE id = ?", m["id"])
        path.write_text(text.replace("cost_usd: 10\n", "cost_usd: 1\n"), encoding="utf-8")
        proc = self.home.run("import", "--file", path, actor="spud", check=False)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc)
        for needle in ("cost_usd", "not editable by hand", "price table"):
            self.assertIn(needle, proc.stderr)
        self.assertNotIn("has no column", proc.stderr)
        self.assertEqual(self.home.scalar("SELECT usage_json FROM members WHERE id = ?", m["id"]), before)

    def test_a_restyled_note_with_its_cost_is_style_only(self):
        self.run_member(AGENT_A, one_request("claude-sonnet-5", output_tokens=1_230_000), "Russet")
        self.home.json("render")
        rel = "ledger/teams/%s/Russet.md" % self.team
        path = self.home.path / rel
        rendered = path.read_text(encoding="utf-8")
        block = "tokens_cached: 0\ncost_usd: 12.3\ntags: [spudagent]\n"  # $12.30 written as Obsidian writes a number, so its rewrite is only style
        self.assertEqual(rendered.count(block), 1)
        path.write_text(rendered.replace(block, "cost_usd: 12.3\ntokens_cached: 0\ntags:\n  - spudagent\n"), encoding="utf-8")
        out = self.home.json("render")
        self.assertEqual((out["restyled"], out["conflicts"]), ([rel], []))
        self.assertEqual(path.read_text(encoding="utf-8"), rendered)


class NotPricedTest(PricedCase):
    def test_an_unpriced_model_shows_a_dash_a_partial_total_and_a_doctor_line(self):  # proof 3
        priced = self.run_member(AGENT_A, one_request("claude-sonnet-5", output_tokens=1_000_000), "Russet")
        future = self.run_member(AGENT_B, one_request("claude-future-9", output_tokens=1_000), "Yukon")
        out = self.render_out()
        section = self.section(out)
        rows = {member_of(r): cells(r) for r in table_rows(section)}
        self.assertEqual((rows["Russet"][7], rows["Yukon"][7]), ("$10.00", "—"))
        self.assertEqual(total_row(section), "| **Total** |  |  |  |  |  | 1.0M out · 0 in · 0 cached | $10.00 (partial) | 0 |")
        self.assertEqual(self.cost_lines(self.note(out, priced))[3], "cost_usd: 10")
        self.assertNotIn("cost_usd", self.note(out, future))
        lines = self.card_lines()
        reason = "no price for model claude-future-9"
        self.assertTrue(self.line_of(lines, "Yukon").endswith("; 1.0k out · 0 in · 0 cached · — (%s)" % reason), lines)
        self.assertEqual(lines[-1], "total: 1.0M out · 0 in · 0 cached · $10.00 (partial) %s; not priced: %s/Yukon (%s)" % (LIST_PRICE, self.team, reason))
        card = self.home.json("card", self.t["key"])
        self.assertEqual((card["total"]["cost_usd"], card["total"]["partial"], card["total"]["not_priced"]), ("10.00", True, [{"ref": "%s/Yukon" % self.team, "reasons": [reason]}]))
        doctor = self.home.json("doctor")
        self.assertTrue(doctor["ok"], doctor)
        self.assertEqual(doctor["pricing"]["not_priced"], [{"ref": "%s/Yukon" % self.team, "reasons": [reason]}])
        self.assertIn("            not priced: %s/Yukon (%s)" % (self.team, reason), self.home.run("doctor").stdout.splitlines())

    def test_without_a_price_table_every_cost_is_a_dash(self):
        self.home.write_config(config_with(None))
        m = self.run_member(AGENT_A, one_request("claude-sonnet-5", output_tokens=1_000_000), "Russet")
        out = self.render_out()
        section = self.section(out)
        self.assertEqual(cells(table_rows(section)[0])[6:], ["1.0M out · 0 in · 0 cached", "—", "0"])
        self.assertEqual(total_row(section), "| **Total** |  |  |  |  |  | 1.0M out · 0 in · 0 cached | — | 0 |")
        self.assertNotIn("cost_usd", self.note(out, m))
        lines = self.card_lines()
        self.assertTrue(self.line_of(lines, "Russet").endswith("; 1.0M out · 0 in · 0 cached · — (%s)" % NO_TABLE), lines)
        self.assertEqual(lines[-1], "total: 1.0M out · 0 in · 0 cached · — (%s)" % NO_TABLE)

    def test_a_sum_without_its_breakdown_is_named_until_member_resum_adds_it(self):
        m = self.plan(name="Russet")
        self.set_member(m["id"], total_tokens=442, duration_ms=4500, tool_uses=3,
                        usage_json=json.dumps({"source": "transcript", "counting": "request", "messages": 2, "usage": TWO_REQUESTS_SUM}))
        lines = self.card_lines()
        self.assertTrue(self.line_of(lines, "Russet").endswith("; 12 out · 130 in · 300 cached · — (%s)" % NO_BREAKDOWN), lines)
        self.assertEqual(lines[-1], "total: 12 out · 130 in · 300 cached · — %s; not priced: %s/Russet (%s)" % (LIST_PRICE, self.team, NO_BREAKDOWN))
        self.assertEqual(self.home.json("doctor")["pricing"]["not_priced"], [{"ref": "%s/Russet" % self.team, "reasons": [NO_BREAKDOWN]}])

    def test_a_ticket_without_usage_says_so(self):
        self.plan(name="Russet")
        self.assertEqual(self.card_lines()[-1], "total: no tokens recorded")
        section = self.section(self.render_out())
        self.assertEqual(total_row(section), "| **Total** |  |  |  |  |  | — | — | — |")


class PriceChangeTest(PricedCase):
    def tables(self):
        return {name: self.home.rows("SELECT * FROM %s ORDER BY rowid" % name) for name in ("members", "events", "renders")}

    def test_a_price_change_re_renders_the_cost_without_writing_the_database(self):  # proof 6
        m = self.run_member(AGENT_A, one_request("claude-sonnet-5", output_tokens=1_000_000), "Russet")
        before = self.tables()
        seen = []
        for pricing in (PRICING, repriced({"claude-sonnet-5": {"output": 15}})):
            self.home.write_config(config_with(pricing))
            out = self.render_out()
            seen.append((cells(table_rows(self.section(out))[0])[7], [line for line in frontmatter(self.note(out, m)) if line.startswith("cost_usd:")], self.card_lines()[-1]))
        self.assertEqual(seen, [
            ("$10.00", ["cost_usd: 10"], "total: 1.0M out · 0 in · 0 cached · $10.00 %s" % LIST_PRICE),
            ("$15.00", ["cost_usd: 15"], "total: 1.0M out · 0 in · 0 cached · $15.00 %s" % LIST_PRICE),
        ])
        self.assertEqual(self.tables(), before)  # render --out and card wrote nothing: no member row, no event, no render record
        # the ledger's own render after a price change: the notes that carry the cost are rewritten, nothing conflicts,
        # no member row changes, and the next render writes nothing
        members = self.home.rows("SELECT * FROM members ORDER BY id")
        self.home.json("render")
        self.home.write_config(config_with(PRICING))
        again = self.home.json("render")
        note = "ledger/teams/%s/%s.md" % (self.team, m["name"])
        self.assertEqual((sorted(again["written"]), again["conflicts"]), (sorted(["ledger/tickets/%s.md" % self.t["key"], note]), []))
        self.assertIn("cost_usd: 10\n", (self.home.path / note).read_text(encoding="utf-8"))
        self.assertEqual(self.home.rows("SELECT * FROM members ORDER BY id"), members)
        self.assertEqual(self.home.json("render")["written"], [])


# =============================================================================
# The importer reads cost_usd back (proof 5)
# =============================================================================


class CostImportTest(SpudTestCase):
    def write_tree(self, yukon_usage):
        """test_team_card's corpus, with Yukon's usage keys as given."""
        root = self.home.path / "corpus"
        tickets = root / "ledger" / "tickets"
        tickets.mkdir(parents=True, exist_ok=True)
        (tickets / "SPD-001.md").write_text(TICKET_ONE, encoding="utf-8")
        (tickets / "SPD-002.md").write_text(TICKET_TWO, encoding="utf-8")
        members = [
            ("SPD-001", "01", "Russet", "[[Spud]]", "", "- [[SPUD-001/Yukon|Yukon]] (01.01, scout, haiku)\n- [[SPUD-001/Kennebec|Kennebec]] (01.02, scout, haiku)\n"),
            ("SPD-001", "01.01", "Yukon", "[[SPUD-001/Russet]]", yukon_usage, ""),
            ("SPD-001", "01.02", "Kennebec", "[[SPUD-001/Russet]]", "duration_ms: 4791\ntool_uses: 3\n", ""),
            ("SPD-002", "01", "Kennebec", "[[Spud]]", "", ""),
        ]
        for ticket, lineage, name, parent, usage_keys, subagents in members:
            folder = root / "ledger" / "teams" / ticket.replace("SPD", "SPUD")
            folder.mkdir(parents=True, exist_ok=True)
            note = MEMBER.format(ticket=ticket, lineage=lineage, name=name, parent=parent, usage=usage_keys, subagents=subagents)
            (folder / ("%s.md" % name)).write_text(note, encoding="utf-8")
        return root

    def test_cost_usd_is_read_back_and_renders_the_same(self):  # proof 5, the read-back
        root = self.write_tree(YUKON_USAGE + "cost_usd: 12.3\n")
        self.home.json("import", root)
        stored = json.loads(self.home.scalar("SELECT m.usage_json FROM members m JOIN tickets t ON t.id = m.ticket_id WHERE t.key = 'SPD-001' AND m.name = 'Yukon'"))
        self.assertEqual(stored, {"source": "transcript", "imported": True, "cost_usd": "12.30", "usage": {
            "input_tokens": 2888805, "output_tokens": 160812, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 44345065}})
        out = self.home.path / "out"
        self.home.json("render", "--out", out)
        rel = "ledger/teams/SPUD-001/Yukon.md"
        self.assertEqual(frontmatter((out / rel).read_text(encoding="utf-8")), frontmatter((root / rel).read_text(encoding="utf-8")))
        section = team_section((out / "ledger" / "tickets" / "SPD-001.md").read_text(encoding="utf-8"))
        rows = {cells(r)[1]: cells(r) for r in table_rows(section)}
        self.assertEqual(rows["01.01"][6:], ["161k out · 2.9M in · 44.3M cached", "$12.30", "105"])
        self.assertEqual(total_row(section), "| **Total** |  |  |  |  |  | 161k out · 2.9M in · 44.3M cached | $12.30 | 108 |")

    def test_a_cost_usd_that_is_no_amount_is_refused(self):
        cases = [(value, YUKON_USAGE + "cost_usd: %s\n" % value) for value in ("-1", "1.234", "12abc", '""', "1e3", "１２", "12.")]
        cases.append(("a cost without the token keys", "duration_ms: 4791\ncost_usd: 1.5\n"))
        for label, usage_keys in cases:
            with self.subTest(label):
                proc = self.home.run("import", self.write_tree(usage_keys), check=False)
                self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout)
                self.assertIn("ledger/teams/SPUD-001/Yukon.md: cost_usd ", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(self.home.scalar("SELECT count(*) FROM tickets"), 0)


if __name__ == "__main__":
    unittest.main()
