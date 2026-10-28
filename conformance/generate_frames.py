#!/usr/bin/env python3
"""Generate the frame conformance corpus: the `Elsewhere` body of protocol §3.12,
the classed `Refusal` body of §3.6, the `Progress` body of §3.15, and the
Request body's stream byte of §3.4 with the `Part` frame of §3.16, and the keys
a Request carries with acts 6 and 7 of the Vault body (§3.4, §3.14, minor 7).

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


CLASSES = [
    "invalid",
    "unauthenticated",
    "forbidden",
    "throttled",
    "elsewhere",
    "retry",
    "conflict",
    "unavailable",
    "internal",
]


def refusal_case(name, body, cls, message):
    """§3.6 from minor 3: a first byte 0-9 is the class (0 = could not be
    classed, 1-9 the table), the rest is the message; any other first byte means
    the whole body is the message from an older node, with no class."""
    return {
        "name": name,
        "body_hex": body.hex(),
        "decoded": {"class": cls, "message": message},
    }


def refusal_cases():
    message = "the table 'users' is not declared (at 14..19)"
    cases = [
        refusal_case(f"class-{word}", bytes([byte]) + message.encode("utf-8"), word, message)
        for byte, word in enumerate(CLASSES, start=1)
    ]
    cases.append(
        refusal_case("class-zero-is-unknown", b"\x00" + message.encode("utf-8"), "unknown", message)
    )
    cases.append(
        refusal_case("a-first-byte-above-nine-is-words", b"\x0a" + message.encode("utf-8"), None, "\n" + message)
    )
    cases.append(refusal_case("words-only-from-an-older-node", message.encode("utf-8"), None, message))
    cases.append(refusal_case("a-class-and-no-words", bytes([6]), "retry", ""))
    cases.append(refusal_case("words-in-utf-8", bytes([3]) + "accès refusé".encode("utf-8"), "forbidden", "accès refusé"))
    return cases


def progress_body(sequence, cursor):
    """§3.15: sequence (u64, big-endian), then the cursor (u32-length-prefixed
    UTF-8) only when there is one."""
    body = struct.pack(">Q", sequence)
    if cursor is not None:
        raw = cursor.encode("utf-8")
        body += struct.pack(">I", len(raw)) + raw
    return body


def progress_case(name, sequence, cursor):
    return {
        "name": name,
        "body_hex": progress_body(sequence, cursor).hex(),
        "decoded": {"sequence": str(sequence), "cursor": cursor},
    }


def text(value):
    """`text`: u32 byte-length, then that many UTF-8 bytes (§2.1)."""
    raw = value.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def request_body(script, stream):
    """§3.4 with no credentials and no parameters, then from minor 5 the stream
    byte — written only when set."""
    body = text(script) + b"\x00" + struct.pack(">I", 0)
    if stream:
        body += b"\x01"
    return body


def request_case(name, script, stream):
    return {
        "name": name,
        "body_hex": request_body(script, stream).hex(),
        "decoded": {"script": script, "stream": stream},
    }


KEY = bytes([0x5A] * 32)
NEXT_KEY = bytes([0x6B] * 32)


def keyed_request_body(script, stream, keys):
    """§3.4 from minor 7: the stream byte, written whenever keys follow, then the
    key count and each key as namespace, database, vault and 32 bytes."""
    body = text(script) + b"\x00" + struct.pack(">I", 0) + (b"\x01" if stream else b"\x00")
    body += struct.pack(">I", len(keys))
    for namespace, database, vault, key in keys:
        body += text(namespace) + text(database) + text(vault) + key
    return body


def keyed_request_case(name, script, stream, keys):
    return {
        "name": name,
        "body_hex": keyed_request_body(script, stream, keys).hex(),
        "decoded": {
            "script": script,
            "stream": stream,
            "keys": [
                {"namespace": n, "database": d, "vault": v, "key_hex": k.hex()} for n, d, v, k in keys
            ],
        },
    }


def vault_target(namespace, database, vault):
    """§3.14: no credentials, then target 1 naming one vault."""
    return b"\x00" + b"\x01" + text(namespace) + text(database) + text(vault)


def keyed_act_case(name, act, keys):
    body = vault_target("prod", "work", "team") + bytes([act]) + b"".join(keys)
    decoded = {"namespace": "prod", "database": "work", "vault": "team"}
    if act == 6:
        decoded.update({"act": "declare-keyed", "key_hex": keys[0].hex()})
    else:
        decoded.update({"act": "change-key", "current_hex": keys[0].hex(), "new_hex": keys[1].hex()})
    return {"name": name, "body_hex": body.hex(), "decoded": decoded}


def malformed_case(name, body, why):
    return {"name": name, "body_hex": body.hex(), "malformed": why}


def build_corpus():
    node = bytes(range(16))
    good = elsewhere_body(node, 7, 1, "b.example:9080")
    return {
        "protocol_minor": 7,
        "what_this_is": (
            "Vectors for the Elsewhere frame body (tag 13), protocol §3.12. A conforming "
            "client decodes each `elsewhere` case's `body_hex` to exactly its `decoded` "
            "(epoch as decimal text, node as hex), and refuses each case carrying "
            "`malformed` as a malformed frame rather than reading a meaning into it. "
            "And for the Refusal body (tag 3), protocol §3.6 from minor 3: each `refusal` "
            "case's `body_hex` reads as its `decoded` class — a word from the table, "
            "`unknown` for byte 0, or null when the body carries no class — and message. "
            "And for the Progress body (tag 37), protocol §3.15 from minor 4: each `progress` "
            "case decodes to its `decoded` sequence (decimal text) and cursor, or is refused "
            "as malformed. "
            "And for the Request body (tag 1), protocol §3.4 from minor 5: each `request` case "
            "is the body a client sends for its `decoded` script and stream flag — the stream "
            "byte appears only when set — and a node refuses a stream byte other than 0 or 1. "
            "A `Part` (tag 38, §3.16) carries raw snapshot bytes and has no body to vector. "
            "And from minor 7: each `keyed` case is either a Request body (`decoded.keys` present) "
            "carrying keys after its stream byte, or a Vault body (`decoded.act`) for act 6 or 7 on "
            "one vault; a client encodes `decoded` to exactly `body_hex`, a node decodes `body_hex` "
            "to exactly `decoded`, and every case carrying `malformed` is refused."
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
        "refusal": refusal_cases(),
        "progress": [
            progress_case("a-feed-over-one-log", 41, None),
            progress_case("a-feed-over-a-split-table", 41, "1.1:d=12,7.2=30"),
            progress_case("a-sequence-above-two-to-the-63", 2**64 - 2, None),
            malformed_case("cut-inside-the-sequence", progress_body(41, None)[:5], "the body ends inside the sequence"),
            malformed_case(
                "cut-inside-the-cursor",
                progress_body(41, "1.1:d=12")[:-2],
                "the cursor is shorter than its length",
            ),
        ],
        "request": [
            request_case("a-script-not-streamed", "BACKUP STATE;", False),
            request_case("a-snapshot-asked-to-stream", "BACKUP STATE;", True),
            malformed_case(
                "a-stream-byte-of-two",
                request_body("BACKUP STATE;", False) + b"\x02",
                "the stream byte is 1 when present, and 0 is the same as absent",
            ),
        ],
        "keyed": [
            keyed_request_case(
                "a-request-opening-one-keyed-vault",
                "REVEAL token FROM team:'github';",
                False,
                [("prod", "work", "team", KEY)],
            ),
            keyed_request_case(
                "keys-for-two-vaults-beside-a-stream",
                "BACKUP STATE;",
                True,
                [("prod", "work", "team", KEY), ("prod", "ops", "infra", NEXT_KEY)],
            ),
            keyed_act_case("declare-a-keyed-vault", 6, [KEY]),
            keyed_act_case("change-a-vaults-key", 7, [KEY, NEXT_KEY]),
            malformed_case(
                "a-key-cut-short",
                keyed_request_body("RETURN 1;", False, [("prod", "work", "team", KEY)])[:-1],
                "a key is 32 bytes, never fewer",
            ),
            malformed_case(
                "a-byte-after-the-last-key",
                keyed_request_body("RETURN 1;", False, [("prod", "work", "team", KEY)]) + b"\x00",
                "nothing follows the last key",
            ),
            malformed_case(
                "declare-keyed-on-the-store",
                b"\x00\x00\x06" + KEY,
                "acts 6 and 7 name one vault; the store has no keyed form",
            ),
            malformed_case(
                "change-key-missing-the-new-key",
                vault_target("prod", "work", "team") + b"\x07" + KEY,
                "act 7 carries the current key and the new one",
            ),
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
