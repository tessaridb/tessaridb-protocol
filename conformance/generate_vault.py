#!/usr/bin/env python3
"""Generate the vault conformance corpus for vault contract version 1.

A **second implementation** of the frame body in `spec/protocol-v1.md` §3.14 and of
the statements in `spec/vault-v1.md` §3, written from those documents alone. Five
clients offer vault functions; each one's live test only proves its node accepts
what it sends. This corpus is what compares the five: a client encodes each
`frames` case to exactly its `body_hex`, renders each `statements` case to exactly
its `script` with exactly its `parameters`, and refuses each case carrying
`refused` before sending anything.

Deliberately no dependency on anything but the standard library.

Usage:
    python3 generate_vault.py > vault-v1.json     regenerate the corpus
    python3 generate_vault.py --check             fail if the committed corpus differs
"""

import argparse
import json
import pathlib
import re
import struct
import sys

CORPUS = pathlib.Path(__file__).with_name("vault-v1.json")

# --- protocol §3.14, the frame body --------------------------------------------


def text(value):
    raw = value.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def frame_body(build):
    """The body of a Vault frame (tag 17) for one build."""
    body = bytearray()
    credentials = build.get("credentials")
    if credentials is None:
        body.append(0)
    else:
        body.append(1)
        body += text(credentials["name"])
        body += text(credentials["password"])
    vault = build.get("vault")
    if vault is None:
        body.append(0)
    else:
        body.append(1)
        body += text(vault["namespace"])
        body += text(vault["database"])
        body += text(vault["vault"])
    act = build["act"]
    if act == "status":
        body.append(1)
    elif act == "unseal":
        body.append(2)
        body += text(build["passphrase"])
    elif act == "seal":
        body.append(3)
    elif act == "change":
        body.append(4)
        body += text(build["current"])
        body += text(build["new"])
    else:
        raise ValueError(f"no such act: {act}")
    return bytes(body).hex()


# --- vault contract §3 and §4, the statements ----------------------------------

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class Refused(Exception):
    """A case the client refuses before sending anything, contract §6."""

    def __init__(self, reason, what, name=None):
        super().__init__(reason)
        self.reason = reason
        self.what = what
        self.name = name


def checked(what, name):
    if not NAME.fullmatch(name):
        raise Refused("not-a-name", what, name)
    return name


def render(build):
    """Answer (script, parameters) for one statement build, or raise Refused."""
    (kind, fields), = build.items()
    namespace = checked("a namespace", fields["namespace"])
    database = checked("a database", fields["database"])
    tenancy = f"USE NAMESPACE {namespace}; USE DATABASE {database}; "
    if kind == "audit":
        actor = fields.get("actor")
        if actor is None:
            return f"{tenancy}INFO FOR AUDIT;", {}
        return f"{tenancy}INFO FOR AUDIT BY {checked('an actor', actor)};", {}
    vault = checked("a vault", fields["vault"])
    if kind == "list":
        clauses = ""
        parameters = {}
        if "after" in fields:
            clauses += f" AFTER {vault}:$after"
            parameters["after"] = fields["after"]
        if "limit" in fields:
            limit = fields["limit"]
            if not (isinstance(limit, int) and 1 <= limit <= 10_000):
                raise Refused("bad-limit", "a limit", str(limit))
            clauses += f" LIMIT {limit}"
        return f"{tenancy}INFO FOR VAULT {vault} RECORDS{clauses};", parameters
    parameters = {"id": fields["id"]}
    if kind == "reveal":
        names = sorted((checked("a field", name) for name in fields.get("fields", [])), key=str.encode)
        which = ", ".join(f"'{name}'" for name in names) if names else "*"
        return f"{tenancy}REVEAL {which} FROM {vault}:$id;", parameters
    if kind == "write":
        written = fields["fields"]
        if not written:
            raise Refused("no-fields", "a write")
        names = sorted((checked("a field", name) for name in written), key=str.encode)
        pairs = []
        for index, name in enumerate(names):
            pairs.append(f"'{name}': $f{index}")
            parameters[f"f{index}"] = written[name]
        return f"{tenancy}UPSERT {vault}:$id MERGE {{ {', '.join(pairs)} }};", parameters
    if kind == "recipients":
        return f"{tenancy}INFO FOR RECIPIENTS OF {vault}:$id;", parameters
    if kind == "add_recipient":
        parameters["name"] = {"string": fields["name"]}
        parameters["key"] = {"bytes": fields["key"]}
        return f"{tenancy}ADD RECIPIENT $name TO {vault}:$id KEY $key;", parameters
    if kind == "remove_recipient":
        parameters["name"] = {"string": fields["name"]}
        return f"{tenancy}REMOVE RECIPIENT $name FROM {vault}:$id;", parameters
    raise ValueError(f"no such statement: {kind}")


# --- cases --------------------------------------------------------------------

WHERE = {"namespace": "shop", "database": "live", "vault": "team"}
TEAM_OWN = {"namespace": "shop", "database": "live", "vault": "team_own"}
GITHUB = {"string": "github"}


def at(**fields):
    return {**WHERE, **fields}


def frame(name, build, note=None):
    entry = {"name": name, "build": build}
    if note is not None:
        entry["note"] = note
    return entry


def statement(name, build, note=None):
    return frame(name, build, note)


FRAMES = [
    frame("status-as-nobody", {"act": "status"}),
    frame("unseal-as-nobody", {"act": "unseal", "passphrase": "correct horse battery"}),
    frame("seal-as-nobody", {"act": "seal"}),
    frame(
        "change-as-nobody",
        {"act": "change", "current": "correct horse battery", "new": "staple 9f2b"},
    ),
    frame(
        "unseal-as-a-user",
        {
            "act": "unseal",
            "passphrase": "correct horse battery",
            "credentials": {"name": "root", "password": "root password"},
        },
        "Credentials come first, as in a Request (§3.4), then the act.",
    ),
    frame(
        "a-passphrase-is-utf8-and-counted-in-bytes",
        {"act": "unseal", "passphrase": "пароль ключ"},
        "The length prefix counts bytes, not characters.",
    ),
    frame("an-empty-passphrase-is-still-a-field", {"act": "unseal", "passphrase": ""}),
    frame(
        "status-of-one-vault",
        {"act": "status", "vault": TEAM_OWN},
        "The target sits between the credentials and the act: 1, then namespace, database, vault.",
    ),
    frame("seal-one-vault", {"act": "seal", "vault": TEAM_OWN}),
    frame("unseal-one-vault", {"act": "unseal", "passphrase": "a team passphrase", "vault": TEAM_OWN}),
    frame(
        "change-one-vaults-passphrase",
        {"act": "change", "current": "a team passphrase", "new": "a new team passphrase", "vault": TEAM_OWN},
    ),
]

STATEMENTS = [
    statement("list-the-first-page", {"list": at()}),
    statement("list-a-page-of-ten", {"list": at(limit=10)}),
    statement(
        "list-the-page-after",
        {"list": at(after=GITHUB, limit=10)},
        "`after` is the previous page's `next`, bound as the value it came back as.",
    ),
    statement("list-after-an-integer-id", {"list": at(after={"integer": "42"})}),
    statement("a-limit-of-zero-is-refused", {"list": at(limit=0)}),
    statement("a-limit-above-the-ceiling-is-refused", {"list": at(limit=10_001)}),
    statement("reveal-every-secret-field", {"reveal": at(id=GITHUB)}),
    statement(
        "reveal-named-fields-in-byte-order",
        {"reveal": at(id=GITHUB, fields=["token", "password", "Recovery"])},
        "Ascending byte order, so `Recovery` sorts before the lower-case names; quoted, so `password` is a name.",
    ),
    statement(
        "write-fields-in-byte-order-with-bound-values",
        {"write": at(id=GITHUB, fields={"token": {"string": "t0"}, "login": {"string": "boog"}})},
        "UPSERT … MERGE creates when absent and keeps every other field and every recipient.",
    ),
    statement("a-write-with-no-fields-is-refused", {"write": at(id=GITHUB, fields={})}),
    statement("list-recipients", {"recipients": at(id=GITHUB)}),
    statement("add-a-recipient", {"add_recipient": at(id=GITHUB, name="bob", key="0a0b0c")}),
    statement("remove-a-recipient", {"remove_recipient": at(id=GITHUB, name="bob")}),
    statement("the-whole-audit-trail", {"audit": {"namespace": "shop", "database": "live"}}),
    statement("one-actors-audit-trail", {"audit": {"namespace": "shop", "database": "live", "actor": "ada"}}),
    statement("a-vault-that-is-not-a-name-is-refused", {"list": at(vault="team-2")}),
    statement(
        "a-field-that-is-not-a-name-is-refused-not-quoted",
        {"reveal": at(id=GITHUB, fields=["pass word"])},
    ),
    statement("a-namespace-that-is-not-a-name-is-refused", {"reveal": at(namespace="1shop", id=GITHUB)}),
    statement(
        "an-actor-that-is-not-a-name-is-refused",
        {"audit": {"namespace": "shop", "database": "live", "actor": "o'brien"}},
    ),
]


def build_corpus():
    frames = [{**entry, "body_hex": frame_body(entry["build"])} for entry in FRAMES]
    statements = []
    for entry in STATEMENTS:
        rendered = dict(entry)
        try:
            script, parameters = render(entry["build"])
        except Refused as refusal:
            rendered["refused"] = {"reason": refusal.reason, "what": refusal.what}
            if refusal.name is not None:
                rendered["refused"]["name"] = refusal.name
        else:
            rendered["script"] = script
            rendered["parameters"] = parameters
        statements.append(rendered)

    return {
        "contract_major": 1,
        "contract_minor": 0,
        "protocol_minor": 2,
        "what_this_is": (
            "Vectors for the vault contract in spec/vault-v1.md. A conforming client encodes each `frames` "
            "case's `build` to exactly its `body_hex` (the body of a Vault frame, tag 17, protocol §3.14), "
            "renders each `statements` case to exactly its `script` with exactly its `parameters`, and "
            "refuses each case carrying `refused` before sending."
        ),
        "parameter_kinds": "string, integer (as decimal text) and bytes (as hex), each a one-key object.",
        "run_it_against_a_node_too": (
            "Agreement here is the offline half. Section 7 of the contract is the other: each client runs "
            "its vault functions against a node built from the engine's dev branch."
        ),
        "generated_by": "generate_vault.py",
        "frames": frames,
        "statements": statements,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed corpus differs")
    arguments = parser.parse_args()

    corpus = build_corpus()
    text_out = json.dumps(corpus, indent=2, ensure_ascii=False) + "\n"

    if arguments.check:
        if not CORPUS.exists():
            print(f"{CORPUS} does not exist", file=sys.stderr)
            return 1
        if CORPUS.read_text(encoding="utf-8") != text_out:
            print(
                f"{CORPUS.name} differs from what this generator produces.\n"
                "Regenerate it in the same commit as the change that moved it.",
                file=sys.stderr,
            )
            return 1
        print(f"{CORPUS.name}: {len(corpus['frames'])} frames and {len(corpus['statements'])} statements, current")
        return 0

    sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
