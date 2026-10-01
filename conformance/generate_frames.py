#!/usr/bin/env python3
"""Generate the frame conformance corpus: the `Elsewhere` body of protocol §3.12.

A **second implementation** of that body, written from the specification alone.
A redirect is the one frame a client must act on rather than report, and an
encoder and a decoder that agree with each other can both disagree with the
document — so each client decodes every `elsewhere` case's `body_hex` to exactly
its `decoded`, and refuses every case carrying `malformed` as malformed rather
than reading a meaning into it.

Deliberately no dependency on anything but the standard library.

Usage:
    python3 generate_frames.py > frames-v1.json    regenerate the corpus
    python3 generate_frames.py --check             fail if the committed corpus differs
"""

import argparse
import json
import pathlib
import struct
import sys

CORPUS = pathlib.Path(__file__).with_name("frames-v1.json")

SETTLEMENT = {"settled": 1, "transient": 2}


def elsewhere_body(node, epoch, settlement_byte, endpoint):
    """§3.12: node (16 bytes), epoch (u64, big-endian), settlement (1 byte),
    endpoint (u32-length-prefixed UTF-8)."""
    raw = endpoint.encode("utf-8")
    return bytes(node) + struct.pack(">QB", epoch, settlement_byte) + struct.pack(">I", len(raw)) + raw


def decoded_case(name, node, epoch, settlement, endpoint):
    body = elsewhere_body(node, epoch, SETTLEMENT[settlement], endpoint)
    return {
        "name": name,
        "body_hex": body.hex(),
        "decoded": {
            "node": bytes(node).hex(),
            "epoch": str(epoch),
            "settlement": settlement,
            "endpoint": endpoint,
        },
    }


def malformed_case(name, body, why):
    return {"name": name, "body_hex": body.hex(), "malformed": why}


def build_corpus():
    node = bytes(range(16))
    good = elsewhere_body(node, 7, 1, "b.example:9080")
    return {
        "protocol_minor": 1,
        "what_this_is": (
            "Vectors for the Elsewhere frame body (tag 13), protocol §3.12. A conforming "
            "client decodes each `elsewhere` case's `body_hex` to exactly its `decoded` "
            "(epoch as decimal text, node as hex), and refuses each case carrying "
            "`malformed` as a malformed frame rather than reading a meaning into it."
        ),
        "generated_by": "generate_frames.py",
        "elsewhere": [
            decoded_case("a-write-sent-to-its-leader", node, 7, "settled", "b.example:9080"),
            decoded_case("a-read-sent-for-this-request-only", node, 7, "transient", "b.example:9080"),
            decoded_case(
                "an-epoch-above-two-to-the-63",
                bytes([0xFF] * 16),
                2**64 - 2,
                "settled",
                "[::1]:47916",
            ),
            decoded_case("an-address-in-utf-8", bytes(16), 1, "transient", "nøde.example:9080"),
            malformed_case(
                "settlement-zero",
                elsewhere_body(node, 7, 0, "b.example:9080"),
                "zero is what a zeroed buffer holds, and is unassigned",
            ),
            malformed_case(
                "settlement-three",
                elsewhere_body(node, 7, 3, "b.example:9080"),
                "a settlement byte is 1 or 2, never a third meaning",
            ),
            malformed_case("cut-inside-the-node", good[:10], "the body ends inside the node"),
            malformed_case("cut-inside-the-endpoint", good[:-3], "the endpoint is shorter than its length"),
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed corpus differs")
    arguments = parser.parse_args()

    text_out = json.dumps(build_corpus(), indent=2, ensure_ascii=False) + "\n"
    if arguments.check:
        if not CORPUS.exists():
            print(f"{CORPUS} does not exist", file=sys.stderr)
            return 1
        if CORPUS.read_text(encoding="utf-8") != text_out:
            print(
                f"{CORPUS.name} differs from what this generator produces.\n"
                f"Regenerate it with: python3 {pathlib.Path(__file__).name} > {CORPUS.name}",
                file=sys.stderr,
            )
            return 1
        return 0
    sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
