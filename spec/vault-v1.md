# TessariDB vault — client contract, version 1

**Vault contract version 1.0.** Drafted 2026-09-30. Needs a node at `0.17.0-beta` or later: the frame of protocol
§3.14 (protocol minor 2), the routes of §5.11, `INFO FOR SEAL` and `INFO FOR VAULT … RECORDS`.

Status: **draft, authoritative for clients.**

> Five clients offering "vault functions" must offer the same functions, send the same bytes and statements, and
> keep a passphrase out of the same places. This contract fixes all three, and `conformance/vault-v1.json` is the
> offline check: every client encodes each frame case to exactly its bytes and renders each statement case to
> exactly its script and parameters.

## 1. What a vault is, in one paragraph a client can repeat

A vault is a table whose `SECRET` fields are encrypted before they are stored. The store opens them only while it is
**unsealed** — a passphrase has been presented and the period it lasts (ten minutes unless the node says otherwise)
has not run out — and only through `REVEAL`, which names one record and is recorded before it answers. A client
repeats the store's claim exactly: the stored bytes, backups and replicas are ciphertext; a running unsealed node can
decrypt. It never claims more.

A vault opens either with the **store's** passphrase (the default) or with **its own**, when it was declared
`DEFINE VAULT v PASSPHRASE '…'`. The store's passphrase and store-wide authority open nothing in a vault with its own;
each such vault is sealed, unsealed and rekeyed by itself, with its own period and its own throttle.

## 2. The functions

Every client offers these, named in its language's own style (`vault_status` / `vaultStatus` / `VaultStatus`):

| function | does | sent as |
|---|---|---|
| `vault_status()` | the seal status: `state` (`uninitialised`, `sealed`, `unsealed` — a closed set, typed as one), `seals_at` (optional instant), `unseal_for` (duration) | frame act 1, or `GET /vault` |
| `unseal(passphrase)` | present the passphrase; answers the status, with `initialised` on the first | frame act 2, or `POST /vault/unseal` |
| `seal()` | drop the key; answers the status | frame act 3, or `POST /vault/seal` |
| `change_passphrase(current, new)` | re-wrap under a new passphrase; answers the status | frame act 4, or `POST /vault/passphrase` |
| `vault(ns, db, name)` | a handle on one vault — the three are **names** (§4) | nothing is sent |
| `handle.status()` | the vault's seal status: the fields of `vault_status()` plus `custody` (`own`, `store` — a closed set, typed as one); for `store` the state is the store's | frame act 1 with the vault target, or `GET /vault/{ns}/{db}/{v}` |
| `handle.unseal(passphrase)` | unseal a vault that carries its own passphrase; answers its status | frame act 2 with the vault target, or `POST /vault/{ns}/{db}/{v}/unseal` |
| `handle.seal()` | seal it; answers its status | frame act 3 with the vault target, or `POST …/seal` |
| `handle.change_passphrase(current, new)` | re-wrap its key under a new passphrase; answers its status | frame act 4 with the vault target, or `POST …/passphrase` |
| `handle.list(after?, limit?)` | one page of record ids: `ids` and `next` (absent on the last page) | statement §3.1 |
| `handle.reveal(id, fields?)` | the named secret fields of one record, or every secret field when none are named; a map field → value | statement §3.2 |
| `handle.write(id, fields)` | set these fields on the record, creating it when absent, keeping every other field and every recipient | statement §3.3 |
| `handle.recipients(id)` | the recipient set: name → key bytes | statement §3.4 |
| `handle.add_recipient(id, name, key)` / `remove_recipient(id, name)` | change the set | statement §3.4 |
| `vault_audit(by?)` | the store's trail of vault reads, optionally one actor's | statement §3.5 |

The handle's four verbs on a vault that opens with the store's passphrase are refused by the node (`status` is not —
it answers the store's state with `custody: store`); the client surfaces that refusal and does not fall back to the store
verbs, because unsealing the store opens every vault in its custody.

A client that speaks only HTTP uses the routes; one that speaks the wire uses the frame, and a wire client MUST check
the node's greeting minor is at least 2 before sending it (protocol §2.3), answering its caller with a named
*node too old* error rather than sending.

## 3. The statements

Every statement is sent with its tenancy, in one request, exactly as the cache contract's are:
`USE NAMESPACE {ns}; USE DATABASE {db}; <statement>` — a connection is a session and a `USE` sent earlier may belong
to a connection that has since been replaced. The answer to read is the **last** outcome.

`{v}` is the vault's name, checked (§4) and written in. `{f}` is a field's name, checked (§4) and written in **single
quotes** — `'password'` — because a vault is exactly where somebody names a field with a word the language reserves,
and the quoted form is the one the node reads as a name whatever the word. A checked name holds no quote, so quoting
it cannot change how the node reads it. Everything else is a bound parameter with the name shown.

### 3.1 List

```
INFO FOR VAULT {v} RECORDS;
INFO FOR VAULT {v} RECORDS LIMIT {n};
INFO FOR VAULT {v} RECORDS AFTER {v}:$after;
INFO FOR VAULT {v} RECORDS AFTER {v}:$after LIMIT {n};
```

`{n}` is a whole number from 1 to 10000 the client writes as digits, refusing anything else before sending (an
absent limit is the node's own 1000). `$after` is the previous page's `next`, bound as the value it came back as.
The answer is `{ records: [id…], next: id }`, `next` present only when the page was full.

### 3.2 Reveal

```
REVEAL * FROM {v}:$id;
REVEAL '{f1}', '{f2}' FROM {v}:$id;
```

Fields render in ascending byte order of their names (query-builder §4.8). The answer is an object of the revealed
fields. Asking for a field that is not `SECRET` is refused by the node; the client does not pre-empt that, because it
does not know the declaration.

### 3.3 Write

```
UPSERT {v}:$id MERGE { '{f1}': $f0, '{f2}': $f1 };
```

Fields in ascending byte order, values bound as `$f0`, `$f1`, … in that order. `UPSERT … MERGE` is the one form that
creates the record when it is absent, keeps every field it does not name, and keeps the recipients — replacing a
vault record whole mints a fresh data key and clears them. An empty `fields` is refused before sending.

### 3.4 Recipients

```
INFO FOR RECIPIENTS OF {v}:$id;
ADD RECIPIENT $name TO {v}:$id KEY $key;
REMOVE RECIPIENT $name FROM {v}:$id;
```

`$name` is a string, `$key` bytes. The store keeps both and interprets neither: what the key material is — a data
key wrapped to someone's public key, a handle in a key service — is the application's scheme.

### 3.5 Audit

```
INFO FOR AUDIT;
INFO FOR AUDIT BY {actor};
```

`{actor}` is a user name, which the statement takes as a name and not as a value, so it is checked (§4) and written
in. Only a caller who administers the whole store is answered.

## 4. Names

A namespace, database, vault, field or actor name matches `[A-Za-z_][A-Za-z0-9_]*`. A client refuses any other **before
sending anything**, and refuses rather than quotes: an escaped name is a client deciding what the node's lexer does.

## 5. The passphrase

- It is a parameter of a function and a field of a frame or a body, **never** part of a statement a client builds.
- It never appears in an error the client raises, a `Debug`/`repr`/`toString` of any client type, or a log line.
  A type holding one prints a placeholder.
- A refusal from the node never quotes it either (protocol §3.14), so surfacing the node's text is safe.
- A throttled attempt (`429` over HTTP; the node's refusal over the wire) means **wait**, never **wrong**. A client
  does not retry it by itself.

A revealed value is returned to the caller and **not cached** by the client.

## 6. Refusals a client raises itself

| case | reason |
|---|---|
| a name that does not match §4 | `not-a-name` |
| a listing limit outside 1-10000 | `bad-limit` |
| a write with no fields | `no-fields` |
| the vault frame to a node whose minor is below 2 | `node-too-old` (wire clients) |

## 7. Verification

Offline: `conformance/vault-v1.json`, generated by `generate_vault.py` — frame bodies for §3.14 as hex, and the
statements of §3 as scripts and parameters. Live: each client runs its vault functions against a node built from the
engine's `dev` branch — status, unseal, write, list across two pages, reveal, recipients added and removed, audit,
change the passphrase, seal, unseal with the new one; and for a vault declared with its own passphrase, its status,
seal, a refused unseal with the store's passphrase, unseal with its own, change it — and asserts that the passphrase appears in none of its own
error texts or debug renderings.
