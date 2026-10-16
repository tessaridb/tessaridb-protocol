#!/usr/bin/env python3
"""Generate the queue worker conformance corpus for queue contract version 1.

A **second implementation** of the statements in `spec/queue-v1.md` §2 and the
name checks in §3, written from that document alone, so the five clients'
workers are compared with each other and not only each with its own node.

Deliberately no dependency on anything but the standard library.

Usage:
    python3 generate_queue.py > queue-v1.json     regenerate the corpus
    python3 generate_queue.py --check             fail if the committed corpus differs
"""

import argparse
import json
import pathlib
import re
import sys

CORPUS = pathlib.Path(__file__).with_name("queue-v1.json")

# --- §3 names -----------------------------------------------------------------

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WORKER = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
CEILING = 500


class Refused(Exception):
    """Something the client refuses before sending anything."""

    def __init__(self, reason, what, name):
        super().__init__(reason)
        self.reason = reason
        self.what = what
        self.name = name


def checked(what, name):
    if not NAME.fullmatch(name):
        raise Refused("not-a-name", what, name)
    return name


def checked_worker(name):
    if not WORKER.fullmatch(name):
        raise Refused("not-a-name", "a worker", name)
    return name


def checked_count(count):
    if not 1 <= count <= CEILING:
        raise Refused("not-a-count", "a count", str(count))
    return count


# --- the filter, as query-builder-v1.md §4.3 renders it ------------------------

OPERATORS = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


class Binder:
    """Names parameters p0, p1, … in binding order, per statement."""

    def __init__(self):
        self.parameters = {}

    def bind(self, value):
        name = f"p{len(self.parameters)}"
        self.parameters[name] = value
        return f"${name}"


def render_filter(node, binder):
    if "compare" in node:
        comparison = node["compare"]
        field = checked("a field", comparison["field"])
        return f"{field} {OPERATORS[comparison['op']]} {binder.bind(comparison['value'])}"
    (joiner, (left, right)), = node.items()
    word = {"and": "AND", "or": "OR"}[joiner]
    return f"({render_filter(left, binder)} {word} {render_filter(right, binder)})"


# --- §2 statements ------------------------------------------------------------


def render(build):
    """Answer (script, parameters) for one build, or raise Refused."""
    (kind, fields), = build.items()
    namespace = checked("a namespace", fields["namespace"])
    database = checked("a database", fields["database"])
    queue = checked("a queue", fields["queue"])
    tenancy = f"USE NAMESPACE {namespace}; USE DATABASE {database}; "
    binder = Binder()
    if kind == "claim":
        worker = checked_worker(fields["worker"])
        count = checked_count(fields["count"])
        narrowed = ""
        if "where" in fields:
            narrowed = f" WHERE {render_filter(fields['where'], binder)}"
        script = f"{tenancy}USE CONSUMER '{worker}'; CLAIM {count} FROM {queue}{narrowed};"
        return script, binder.parameters
    # The identity is the claim's own spelling, written back unchanged (§2).
    record = fields["id"]
    if kind == "done":
        return f"{tenancy}DELETE {queue}:{record};", binder.parameters
    worker = checked_worker(fields["worker"])
    after = fields.get("after_ms", 0)
    tail = f" NOT BEFORE {after}ms" if after > 0 else ""
    script = f"{tenancy}RELEASE {queue}:{record} FOR CONSUMER '{worker}'{tail};"
    return script, binder.parameters


# --- cases --------------------------------------------------------------------

WHERE = {"namespace": "shop", "database": "live", "queue": "jobs"}
SEVEN = "7"


def at(**fields):
    return {**WHERE, **fields}


def compare(field, op, value):
    return {"compare": {"field": field, "op": op, "value": value}}


def case(name, build, note=None):
    entry = {"name": name, "build": build}
    if note is not None:
        entry["note"] = note
    return entry


CASES = [
    case("claim-a-batch", {"claim": at(worker="resizer", count=10)}),
    case("claim-one", {"claim": at(worker="resizer", count=1)}),
    case(
        "claim-only-what-matches",
        {"claim": at(worker="resizer", count=5, where=compare("kind", "eq", {"string": "image"}))},
        "The condition is the query builder's WHERE, every value bound.",
    ),
    case(
        "claim-under-a-nested-condition",
        {
            "claim": at(
                worker="resizer",
                count=5,
                where={"and": [compare("kind", "eq", {"string": "image"}), compare("size", "lt", {"integer": "1000"})]},
            )
        },
    ),
    case(
        "a-worker-name-may-carry-dots-colons-and-dashes",
        {"claim": at(worker="resizer.v2:eu-west", count=10)},
        "The worker is a string literal, so it allows more than an identifier does.",
    ),
    case("done-with-an-integer-identity", {"done": at(id=SEVEN)}),
    case(
        "done-with-a-text-identity",
        {"done": at(id="'a7'")},
        "The identity is written back as the claim spelled it; the quotes are what make it text.",
    ),
    case("done-with-a-uuid-identity", {"done": at(id="uuid '0190a0b1-0000-7000-8000-000000000001'")}),
    case(
        "hand-back-at-once",
        {"release": at(worker="resizer", id=SEVEN)},
        "Named with FOR CONSUMER: each claim declared the worker again as a new instance.",
    ),
    case(
        "hand-back-with-no-delay-writes-no-delay-clause",
        {"release": at(worker="resizer", id=SEVEN, after_ms=0)},
        "A delay of nothing is no clause at all.",
    ),
    case("hand-back-after-a-delay", {"release": at(worker="resizer", id=SEVEN, after_ms=1500)}),
    case("the-largest-count-is-claimed", {"claim": at(worker="resizer", count=500)}),
    case("a-count-of-nothing-is-refused", {"claim": at(worker="resizer", count=0)}),
    case(
        "a-count-above-the-ceiling-is-refused",
        {"claim": at(worker="resizer", count=501)},
        "The node refuses it too; the client says so before sending.",
    ),
    case("a-queue-that-is-not-a-name-is-refused", {"claim": at(queue="jobs-v2", worker="w", count=1)}),
    case("a-namespace-that-is-not-a-name-is-refused", {"done": at(namespace="1shop", id=SEVEN)}),
    case(
        "a-filter-field-that-is-not-a-name-is-refused",
        {"claim": at(worker="w", count=1, where=compare("kind of", "eq", {"string": "image"}))},
    ),
    case(
        "a-worker-carrying-a-quote-is-refused-not-escaped",
        {"release": at(worker="o'brien", id=SEVEN)},
        "Refusing is the defence; an escaped quote would be a client deciding what the node's lexer does.",
    ),
    case("an-empty-worker-is-refused", {"claim": at(worker="", count=1)}),
    case("a-worker-longer-than-128-is-refused", {"claim": at(worker="w" * 129, count=1)}),
]


def build_corpus():
    cases = []
    for entry in CASES:
        rendered = dict(entry)
        try:
            script, parameters = render(entry["build"])
        except Refused as refusal:
            rendered["refused"] = {"reason": refusal.reason, "what": refusal.what, "name": refusal.name}
        else:
            rendered["script"] = script
            rendered["parameters"] = parameters
        cases.append(rendered)

    return {
        "contract_major": 1,
        "contract_minor": 0,
        "what_this_is": (
            "Rendering vectors for the queue worker contract in spec/queue-v1.md. "
            "A conforming client renders each case's `build` to exactly its `script` with "
            "exactly its `parameters`, and refuses each case carrying `refused` before sending."
        ),
        "this_is_language_not_protocol": (
            "Section 6 of protocol-v1.md puts the query language outside the protocol. "
            "This corpus sits beside the query and consumer corpora for the same reason."
        ),
        "run_it_against_a_node_too": (
            "Rendering agreement is the offline half. Section 7 of the contract is the other: "
            "each client runs its worker against a node built from the engine's dev branch."
        ),
        "generated_by": "generate_queue.py",
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed corpus differs")
    arguments = parser.parse_args()

    text = json.dumps(build_corpus(), indent=2, ensure_ascii=False) + "\n"

    if arguments.check:
        if not CORPUS.exists():
            print(f"{CORPUS} does not exist", file=sys.stderr)
            return 1
        committed = CORPUS.read_text(encoding="utf-8")
        if committed != text:
            print(
                f"{CORPUS.name} differs from what this generator produces.\n"
                "Regenerate it in the same commit as the change that moved it.",
                file=sys.stderr,
            )
            return 1
        print(f"{CORPUS.name}: {len(build_corpus()['cases'])} cases, current")
        return 0

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
