# TessariDB topic consumer — client contract, version 1

**Consumer contract version 1.0.** Drafted 2026-09-29. Needs a node at `0.12.0-beta`
or later (consumer groups).

Status: **draft, authoritative for clients.**

> **This is language, not protocol.** Like the query-builder contract, it sits
> beside `protocol-v1.md` rather than inside it: everything here travels as
> ordinary statements over the wire or HTTP, and a client that implements the
> protocol alone remains conforming. What this document fixes is that five
> clients offering "consume a topic with a callback" behave the same way, so
> that moving a worker from one language to another changes nothing a reader of
> the topic can observe.

## 1. What a consumer is

A consumer reads a topic as a member of a **consumer group** and calls a
function the application supplies — the *handler* — once per message, in the
order the group hands the messages out. A group is declared in the store:

```
DEFINE GROUP 'billing' ON TOPIC events ACK DEADLINE 30s [DELIVERIES n] [IN FLIGHT n] [DEAD LETTER TO t];
```

The group, not the connection, holds the state: the last position handed out,
the messages in flight and their deadlines. So a consumer that crashes loses
nothing it had not acknowledged, and a new process under the same group name
continues where the group stands.

A client MUST NOT declare the group implicitly. Declaring it is a schema act
(it needs the `manage` class) and chooses a deadline no client can guess.
A client MAY offer a separate call that runs the `DEFINE GROUP` statement.

## 2. The statements a consumer sends

Exactly these, rendered exactly so. `<topic>` is a name and `<group>` a quoted
string (§3); `$p0 …` are bound parameters, named from `p0` in the order the positions are given.

| step | statement |
|---|---|
| select the tenancy | `USE NAMESPACE <ns>; USE DATABASE <db>;` — sent with every read, never once at start-up (§5) |
| take messages | `READ FROM <topic> FOR CONSUMER '<group>' LIMIT <n>;` |
| acknowledge | `ACK <topic> FOR CONSUMER '<group>' AT $p0[, $p1 …];` |
| hand back | `NACK <topic> FOR CONSUMER '<group>' AT $p0[, $p1 …] [DELAY <d>];` |

A read answers records whose value is `{ position, value, deliveries }`.
`position` is a whole number from 1; `deliveries` is how many times this message
has been handed out, 1 the first time. `ACK` and `NACK` answer a whole number:
how many of the positions were in flight. A position that was not in flight
counts 0 and is **not** an error, so an acknowledgement retried after a lost
answer is harmless and MUST NOT be reported as a failure.

## 3. Names

`<topic>`, `<ns>` and `<db>` are identifiers and are written into the statement,
never bound. A client MUST check each against `^[A-Za-z_][A-Za-z0-9_]*$` and
refuse, before sending anything, a name that does not match. The group name is
a string literal the statement cannot take as a parameter; a client MUST check it
against `^[A-Za-z0-9_.:-]{1,128}$` and refuse otherwise, rather than escaping it.
Positions are always bound.

## 4. The loop

```
consume(topic, group, handler, ack = auto | manual, batch = 10)
```

1. Read with `LIMIT batch`.
2. For each message, in the order answered, call `handler(message)`. A message
   exposes `position`, `value` and `deliveries`.
3. **auto**: when the handler returns normally, the client sends `ACK` for that
   message **before** calling the handler with the next one. When the handler
   raises or returns an error, the client sends `NACK` for that message (no
   delay) and continues with the next. Auto therefore means *acknowledge after
   processing*: at least once, never at most once.
4. **manual**: the handler decides each message itself: acknowledge it, hand it
   back (with an optional delay), or leave it. A client offers this either as a
   value the handler returns (`Ack`, `Nack(delay?)`, `Leave`) — natural where a
   callback returns a result — or as `ack()` / `nack(delay?)` methods on the
   message; the statements sent are the same. `ack` and `nack` report the node's
   count. A message left alone is handed out again when the group's deadline
   passes — that is the design, not a leak.
5. When a read answers no messages, wait and read again: 50 ms, doubling on each
   empty read to at most 1 s, and back to none after any read that answered
   something. A client MUST NOT spin.
6. `stop()` lets the handler that is running finish (and, in auto mode, its
   acknowledgement be sent), then stops reading. Messages still in flight are
   not handed back; they return to the group when their deadline passes.

A consumer is at least once in both modes. An effect outside the store that must
not happen twice has to be idempotent — the message's `position` together with
the topic and group names is a stable key for that.

## 5. Connections

A consumer holds its own connection, or its own checkout from a pool for each
read-and-acknowledge cycle. Because a reconnected or re-checked-out connection
has forgotten its `USE`, the tenancy is sent with **every** read (§2), never
once at start-up — a consumer that relied on an earlier `USE` would read another
database's topic of the same name with no error at all.

## 6. Refusals

Members of one group take turns on the group's record, so two of them reading
or acknowledging at the same moment meet. **The node already handles that**: a
lone `READ`, `ACK` or `NACK` that loses the race is run again on a fresh
snapshot for up to one second before anything is refused. A consumer therefore
does not retry on its own account, and over the wire it could not tell a
contention refusal from any other: a refusal carries the node's words and no
class (protocol §3.11), and a client MUST NOT parse the words to decide.

- **Wire**: a transport failure (`Io`, `Truncated`) may be retried by opening a
  new connection and reading again — the group holds the state, so nothing is
  lost or doubled beyond what at-least-once already allows. A `Refused` ends the
  consumer and is reported to the caller with the node's words.
- **HTTP**: `409` is the store-level conflict and MAY be retried after the §4.5
  wait; every other status ends the consumer and is reported.

`NotAGroup` (no group under that name) and `NotATopic` are misconfiguration, not
load, and reach the caller as refusals like any other. `401` and `403` stay
apart, as everywhere: the first means sign in, the second means signing in again
will never help.

## 7. Verification

Each client verifies its consumer against a running node built from the
engine's `dev` branch, in both modes: auto hands every message of a small topic
to the handler in order and leaves nothing in flight; manual hands an
unacknowledged message out again after the deadline; a handler that fails once
sees the same message again with `deliveries` one higher. Offline, each client
renders every case of `conformance/consumer-v1.json` through the same code its
consumer sends, and refuses every case that carries `refused`.
