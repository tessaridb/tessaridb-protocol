#!/usr/bin/env python3
"""Send the vault corpus to a running node — the check that reaches the node.

`generate_vault.py --check` proves the vectors are what the specification says.
It cannot prove the node reads them: that a frame body of §3.14 is the frame the
node decodes, or that a rendered statement parses and runs. This sends every frame
case as a Vault frame (tag 17) and every statement case as a Request, in an order
that makes each one meaningful, and reports one row per case.

Run it against a node with an empty in-memory store, built from the engine's dev
branch, serving the wire protocol: the frames set the store's first passphrase.

    tessaridb --serve 127.0.0.1:47915
    python3 verify_vault_against_node.py --node 127.0.0.1:47915
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from generate import encode  # a second implementation of the codec
from wire import FRAME_ANSWER, FRAME_REFUSAL, Malformed, Node, Reader, Refused

FRAME_VAULT = 17

# What each frame case must answer, in the order the corpus lists them, against a
# store that has never been unsealed. A refusal is a real answer: it proves the
# body decoded, because a body the node could not read closes the connection.
FRAMES_EXPECT = {
    "status-as-nobody": ("answer", "uninitialised"),
    "unseal-as-nobody": ("answer", "unsealed"),
    "seal-as-nobody": ("answer", "sealed"),
    "change-as-nobody": ("answer", "sealed"),
    "unseal-as-a-user": ("refusal", None),  # no such user on this open store
    "a-passphrase-is-utf8-and-counted-in-bytes": ("refusal", None),  # not the passphrase
    "an-empty-passphrase-is-still-a-field": ("refusal", None),  # nor this
}

# The passphrase the frames leave the store with (the change case's `new`).
PASSPHRASE_AFTER_FRAMES = "staple 9f2b"

SETUP = [
    "DEFINE NAMESPACE shop;",
    "USE NAMESPACE shop;",
    "DEFINE DATABASE live;",
    "USE DATABASE live;",
    "DEFINE VAULT team;",
    "DEFINE FIELD token ON team TYPE string SECRET;",
    "DEFINE FIELD 'password' ON team TYPE string SECRET;",
    "DEFINE FIELD Recovery ON team TYPE string SECRET;",
    "DEFINE FIELD login ON team TYPE string;",
]


def vault_frame(node: Node, body: bytes) -> tuple[str, str | None]:
    node.send_frame(FRAME_VAULT, body)
    kind, answer = node.read_frame()
    if kind == FRAME_REFUSAL:
        return "refusal", None
    if kind != FRAME_ANSWER:
        raise Malformed(f"expected an Answer or a Refusal, got kind {kind}")
    outcomes = node.read_outcomes(Reader(answer))
    held = outcomes[0].get("bytes", b"") if len(outcomes) == 1 else b""
    # The state is a string value: tag 0x05, a four-byte length, the text.
    for state in ("uninitialised", "unsealed", "sealed"):
        if b"\x05" + len(state).to_bytes(4, "big") + state.encode() in held:
            return "answer", state
    return "answer", repr(outcomes)[:120]


def bind(parameters: dict) -> list[tuple[str, bytes]]:
    return [(name, encode(value)) for name, value in parameters.items()]


def verify(corpus: dict, node: Node) -> list[dict]:
    rows = []
    if node.minor < 2:
        return [{"case": "greeting", "ok": False, "said": f"node minor {node.minor}; the frame needs 2"}]
    for case in corpus["frames"]:
        expected = FRAMES_EXPECT[case["name"]]
        got = vault_frame(node, bytes.fromhex(case["body_hex"]))
        rows.append({"case": case["name"], "ok": got == expected, "said": f"{got}, expected {expected}"})

    node.request(f"UNSEAL VAULT WITH '{PASSPHRASE_AFTER_FRAMES}';")
    for statement in SETUP:
        node.request(statement)
    # The write first, so the reads after it find a record to read.
    ordered = sorted(corpus["statements"], key=lambda case: "write" not in case["build"])
    for case in ordered:
        if "refused" in case:
            rows.append({"case": case["name"], "ok": True, "said": "refused by the client, nothing sent"})
            continue
        try:
            outcomes = node.request(case["script"], bind(case["parameters"]))
            rows.append({"case": case["name"], "ok": True, "said": json.dumps(outcomes[-1], default=str)[:120]})
        except Refused as refused:
            rows.append({"case": case["name"], "ok": False, "said": f"refused: {refused}"})
    return rows


def report(rows: list[dict]) -> int:
    for row in rows:
        print(f"{'ok ' if row['ok'] else 'BAD'} {row['case']}: {row['said']}")
    failed = sum(1 for row in rows if not row["ok"])
    print(f"vault: {len(rows) - failed} of {len(rows)} cases as the corpus says")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--node", default="127.0.0.1:47915", help="host:port of a node serving --serve")
    parser.add_argument("--corpus", default=str(Path(__file__).with_name("vault-v1.json")))
    args = parser.parse_args()
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    host, _, port = args.node.rpartition(":")
    node = Node(host, int(port))
    try:
        rows = verify(corpus, node)
    finally:
        node.close()
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
