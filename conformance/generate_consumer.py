#!/usr/bin/env python3
"""Generate the topic consumer conformance corpus for consumer contract version 1.

A **second implementation** of the statements in `spec/consumer-v1.md` §2 and the
name checks in §3, written from that document alone. Five clients compose these
statements, and each one's own live test only proves that its node accepts what
it sends; nothing compared the five with each other. This corpus is that
comparison: a client renders each case's `build` to exactly its `script` with
exactly its `parameters`, and refuses each case carrying `refused` before
anything is sent.

Deliberately no dependency on anything but the standard library.

Usage:
    python3 generate_consumer.py > consumer-v1.json     regenerate the corpus
    python3 generate_consumer.py --check                fail if the committed corpus differs
"""

import argparse
import json
import pathlib
import re
import sys

CORPUS = pathlib.Path(__file__).with_name("consumer-v1.json")

# --- §3 names -----------------------------------------------------------------

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
GROUP = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class Refused(Exception):
    """A name the client refuses before sending anything, §3."""

    def __init__(self, reason, what, name):
        super().__init__(reason)
        self.reason = reason
        self.what = what
        self.name = name


def checked(what, name):
    if not NAME.fullmatch(name):
        raise Refused("not-a-name", what, name)
    return name


def checked_group(name):
    if not GROUP.fullmatch(name):
        raise Refused("not-a-name", "a group", name)
    return name


# --- §2 statements ------------------------------------------------------------


def render(build):
    """Answer (script, parameters) for one build, or raise Refused."""
    (kind, fields), = build.items()
    namespace = checked("a namespace", fields["namespace"])
    database = checked("a database", fields["database"])
    topic = checked("a topic", fields["topic"])
    group = checked_group(fields["group"])
    tenancy = f"USE NAMESPACE {namespace}; USE DATABASE {database}; "
    if kind == "read":
        return f"{tenancy}READ FROM {topic} FOR CONSUMER '{group}' LIMIT {fields['limit']};", {}
    positions = fields["positions"]
    parameters = {f"p{index}": {"integer": str(position)} for index, position in enumerate(positions)}
    references = ", ".join(f"${name}" for name in parameters)
    verb = {"ack": "ACK", "nack": "NACK"}[kind]
    delay = fields.get("delay_ms", 0)
    tail = f" DELAY {delay}ms" if delay > 0 else ""
    return f"{tenancy}{verb} {topic} FOR CONSUMER '{group}' AT {references}{tail};", parameters


# --- cases --------------------------------------------------------------------

WHERE = {"namespace": "shop", "database": "live", "topic": "orders", "group": "mailer"}


def at(**fields):
    return {**WHERE, **fields}


def case(name, build, note=None):
    entry = {"name": name, "build": build}
    if note is not None:
        entry["note"] = note
    return entry


CASES = [
    case("read-a-batch", {"read": at(limit=10)}),
    case("read-one-at-a-time", {"read": at(limit=1)}),
    case(
        "a-group-name-may-carry-dots-colons-and-dashes",
        {"read": at(group="billing.v2:eu-west", limit=10)},
        "The group is a string literal, so §3 allows more than an identifier does.",
    ),
    case("acknowledge-one-position", {"ack": at(positions=[7])}),
    case(
        "acknowledge-several-positions-in-the-order-given",
        {"ack": at(positions=[9, 3, 12])},
        "Positions are bound, never written into the text, and named from p0.",
    ),
    case("hand-back-at-once", {"nack": at(positions=[4])}),
    case(
        "hand-back-with-no-delay-writes-no-delay-clause",
        {"nack": at(positions=[4], delay_ms=0)},
        "A delay of nothing is no clause at all, not DELAY 0ms.",
    ),
    case(
        "hand-back-after-a-delay",
        {"nack": at(positions=[4, 5], delay_ms=1500)},
        "The delay is a duration literal the client formats from a number, in milliseconds.",
    ),
    case("a-topic-that-is-not-a-name-is-refused", {"read": at(topic="orders-v2", limit=10)}),
    case("a-namespace-that-is-not-a-name-is-refused", {"ack": at(namespace="1shop", positions=[1])}),
    case("a-database-that-is-not-a-name-is-refused", {"nack": at(database="live db", positions=[1])}),
    case(
        "a-group-carrying-a-quote-is-refused-not-escaped",
        {"read": at(group="o'brien", limit=10)},
        "Refusing is the defence (§3); an escaped quote would be a client deciding what the node's lexer does.",
    ),
    case("an-empty-group-is-refused", {"ack": at(group="", positions=[1])}),
    case("a-group-longer-than-128-is-refused", {"read": at(group="g" * 129, limit=10)}),
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
            "Rendering vectors for the topic consumer contract in spec/consumer-v1.md. "
            "A conforming client renders each case's `build` to exactly its `script` with "
            "exactly its `parameters`, and refuses each case carrying `refused` before sending."
        ),
        "this_is_language_not_protocol": (
            "Section 6 of protocol-v1.md puts the query language outside the protocol. "
            "This corpus sits beside the query corpus for the same reason."
        ),
        "run_it_against_a_node_too": (
            "Rendering agreement is the offline half. Section 7 of the contract is the other: "
            "each client runs its consumer against a node built from the engine's dev branch."
        ),
        "generated_by": "generate_consumer.py",
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
