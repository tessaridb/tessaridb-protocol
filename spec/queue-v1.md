# TessariDB queue worker — client contract, version 1

**Queue contract version 1.0.** Drafted 2026-10-16. Needs a node at `0.35.0` or
later (a claim narrowed by a condition, a claim passing over records another
claim is taking, a dead letter).

Status: **draft, authoritative for clients.**

> **This is language, not protocol.** Like the consumer and cache contracts, it
> sits beside `protocol-v1.md`: everything here travels as ordinary statements,
> and a client that implements the protocol alone remains conforming. What this
> document fixes is that five clients offering "take work from a queue" send
> the same statements and settle work the same way.

## 1. What a queue worker is

A queue hands each record to one worker at a time under a hold that lapses:

```
DEFINE QUEUE jobs TIMEOUT 30s [ATTEMPTS n] [PRIORITY BY f] [NOT BEFORE f] [DEAD LETTER TO t];
```

A worker **claims** records, does the work, and then either says the work is
**done** — the record is deleted — or **releases** it, optionally not before a
delay, so it is handed out again. A worker that dies says nothing: its hold
lapses after `TIMEOUT` and the next claim takes the record. With `ATTEMPTS n`
a record handed out `n` times is not handed out again; with `DEAD LETTER TO t`
the claim that meets it moves it to the topic `t` as
`{ queue, id, attempts, value }`, which is read with the topic consumer
(`consumer-v1.md`).

A client MUST NOT declare the queue. Declaring it is a schema act and chooses a
timeout no client can guess. A client MAY offer a separate call that runs it.

## 2. The statements a worker sends

Exactly these, rendered exactly so. `<queue>`, `<ns>`, `<db>` and filter fields
are names and `<worker>` a quoted string (§3); `$p0 …` are bound parameters,
named from `p0` in binding order.

| step | statement |
|---|---|
| take work | `USE NAMESPACE <ns>; USE DATABASE <db>; USE CONSUMER '<worker>'; CLAIM <n> FROM <queue>[ WHERE <filter>];` |
| done | `USE NAMESPACE <ns>; USE DATABASE <db>; DELETE <queue>:<id>;` |
| hand back | `USE NAMESPACE <ns>; USE DATABASE <db>; RELEASE <queue>:<id> FOR CONSUMER '<worker>'[ NOT BEFORE <d>ms];` |

`<n>` is a whole number from 1 to 500, written into the statement; a client
refuses any other count before sending. `<filter>` is rendered exactly as the
query builder renders a `WHERE` (`query-builder-v1.md` §4.3), every value bound.
`<id>` is the record's identity **exactly as the claim's answer spelled it** —
`7`, `'a7'`, `uuid '…'` — written back unchanged and never parsed into a typed
value (protocol §3.5: a client that re-parses identities is a second spelling
authority). A client therefore settles only a record it claimed, through the
value its claim produced, and never from text a caller typed. This is the wire's
spelling; the HTTP surface collapses `7` and `'7'` (§5.7.1), so a worker runs
over the wire.

A claim answers the records it took, in the order the queue hands them out —
arrival, or `PRIORITY BY` — each `<id>: { …fields, attempts, claimed_until,
claimed_by }`. A client exposes the identity, the record's own fields and
`attempts` (1 the first time it is handed out). An empty answer is an empty
queue, not an error. A claim passes over records another worker's claim is
taking, so workers on one queue take different records.

The hand-back names the worker (`FOR CONSUMER`) because every claim declares
the worker again, and each declaration is a new instance: a release under the
bare form would be refused as somebody else's hold. A client MUST NOT send
`RELEASE ALL … FOR CONSUMER`: it reaches every live process under that name.
`NOT BEFORE` needs the queue to declare a `NOT BEFORE` field; the node refuses it
otherwise, and a client reports that refusal rather than dropping the delay.

## 3. Names

`<queue>`, `<ns>`, `<db>` and every filter field are identifiers, written into
the statement and never bound: a client MUST check each against
`^[A-Za-z_][A-Za-z0-9_]*$` and refuse, before sending, a name that does not
match. The worker is a string literal a statement cannot take as a parameter;
a client MUST check it against `^[A-Za-z0-9_.:-]{1,128}$` and refuse otherwise,
rather than escaping it.

## 4. The loop

```
work(queue, worker, handler, settle = auto | manual, batch = 10, where = none)
```

1. Claim `batch` records, narrowed by `where` when one is given.
2. For each record, in the order answered, call `handler(record)`.
3. **auto**: when the handler returns normally, send done for that record
   before calling the handler with the next one; when it raises or returns an
   error, send a hand-back with no delay and go on. The attempt was counted at
   the claim, so a record that keeps failing reaches `ATTEMPTS`.
4. **manual**: the handler answers `Done`, `Release(after?)` or `Leave` (or calls
   `done()` / `release(after?)` on the record); the statements are the same. A
   record left alone is handed out again when its hold lapses.
5. When a claim answers nothing, wait and claim again: 50 ms, doubling on each
   empty claim to at most 1 s, back to none after a claim that took something.
   A client MUST NOT spin.
6. `stop()` lets the running handler finish (and, in auto mode, its settlement
   be sent), hands back every record of the batch the handler has not seen, one
   statement each, and stops claiming.

A worker is at least once in both modes: a handler that did the work and died
before done sees the record again. An external effect keyed on the queue name
and the record's identity is the idempotency key.

## 5. Connections

A worker holds its own connection, or a checkout per claim-and-settle cycle. The
tenancy is sent with **every** statement (§2), never once at start-up, because a
reconnected connection has forgotten its `USE` and would claim from another
database's queue of the same name with no error.

## 6. Refusals

A refusal carries its class (protocol §3.11). A claim refused with class
`retry` — two claims raced past what the store's register sees — is waited on
as an empty claim (§4.5) and claimed again. Every other refusal, and every
refusal of done or a hand-back, ends the worker and reaches the caller with the
node's words. A transport failure may be retried on a new connection; the holds
are in the store, so nothing is lost beyond what at-least-once allows.
`NotAQueue`, `ClaimAboveCeiling` and `NoDelayField` are misconfiguration and
reach the caller like any other refusal. `401` and `403` stay apart.

## 7. Verification

Each client verifies its worker against a node built from the engine's `dev`
branch: auto takes every record of a small queue once and leaves it empty; a
handler that fails once sees the record again with `attempts` one higher; a
`where` takes only matching records; manual `Release(after)` hands a record out
again only after the delay. Offline, each client renders every case of
`conformance/queue-v1.json` through the code its worker sends, and refuses every
case carrying `refused`.
