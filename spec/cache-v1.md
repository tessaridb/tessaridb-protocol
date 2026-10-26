# TessariDB space as a cache — client contract, version 1

**Cache contract version 1.1.** Drafted 2026-09-30; 1.1 (2026-10-26) adds the container calls of §2a, which need
a node at `0.39.0` or later for their reads (the `set::` and `scores::` functions, `array::at`, `object::get`). Needs a node at `0.15.0-beta` or later for the routes of
protocol §5.10; the statements below work on any node with spaces (`0.5.0-beta`), except `DELETE … RETURN BEFORE`
on a space, which needs `0.6.0-beta`.

Status: **draft, authoritative for clients.**

> **This is language, not protocol.** Like the query-builder and consumer contracts it sits beside
> `protocol-v1.md`: everything here travels as ordinary statements over the **wire** connection. What it fixes is
> that five clients offering "a cache over a space" send the same statements and answer the same way, so moving a
> service from one language to another changes nothing a reader of the space can observe.

## 1. The handle

A client offers a handle bound to a **namespace**, a **database** and a **space**, over a connection the caller
already holds. The three are **names** (query-builder §3) and a client MUST refuse one that is not, before sending
anything. Keys are **strings**.

Every call sends its own tenancy with its statement, in **one** request:

```
USE NAMESPACE {ns}; USE DATABASE {db}; <statement>
```

The answer to read is the **last** outcome. A connection is a session, and a handle MUST NOT rely on a `USE` sent
earlier: a pooled connection may have been reconnected and forgotten it, and reading another database is not an
error.

Every key, value, duration and holder is a **bound parameter** with the name shown; only `{s}` (the space, a
checked name) and a listing's `{n}` (an integer the client parsed) are written into the text.

## 2. The calls

Names follow each language's conventions (`set_if_absent`, `SetIfAbsent`); the statements do not change.

| call | statement | answer |
|---|---|---|
| `get(key)` | `GET {s}:$k;` | the value, or **absent** when the answer is `NONE` |
| `set(key, value, ttl?)` | `SET {s}:$k = $v;` · with a ttl `SET {s}:$k = $v EXPIRE $t;` | nothing |
| `setIfAbsent(key, value, ttl?)` | `SET {s}:$k = $v IF ABSENT[ EXPIRE $t];` | the boolean the node answered |
| `setIfPresent(key, value, ttl?)` | `SET {s}:$k = $v IF PRESENT[ EXPIRE $t];` | the boolean |
| `compareAndSet(key, expected, value, ttl?)` | `SET {s}:$k = $v IF = $e[ EXPIRE $t];` | the boolean |
| `delete(key)` | `DELETE {s}:$k RETURN BEFORE;` | `true` unless the answer is `NONE` — a key holding `NULL` is a key |
| `incr(key, by = 1)` | `INCR {s}:$k BY $n;` | the new integer |
| `ttl(key)` | `RETURN TTL {s}:$k;` | three answers kept apart: a duration; **never expires** (`NULL`); **no key** (`NONE`) |
| `expire(key, ttl)` | `EXPIRE {s}:$k $t;` | the boolean — whether there was a key |
| `persist(key)` | `PERSIST {s}:$k;` | the boolean |
| `keys(prefix?, after?, limit = 100)` | `KEYS FROM {s}[ PREFIX $p][ AFTER $a] LIMIT {n};` | the keys, in key order |
| `getOrSet(key, ttl, loader)` | §3 | the stored value |
| `lock(key, ttl, holder?)` | `SET {s}:$k = $h IF ABSENT EXPIRE $t;` | a **lease** when `true`, nothing when `false` |
| `lease.extend(ttl?)` | `SET {s}:$k = $h IF = $h EXPIRE $t;` | the boolean — `false` means the lease was lost |
| `lease.release()` | `SET {s}:$k = 'free' IF = $h EXPIRE 1ms;` | the boolean |

Parameter names are fixed: `$k` key, `$v` value, `$e` expected, `$t` duration, `$n` increment, `$p` prefix, `$a`
after, `$h` holder. The brackets `[ … ]` mark text present only when the argument is given; the spacing is exactly
as shown.

A client MUST refuse, before sending:

- a ttl that is not **positive** — `EXPIRE` with a zero or negative duration **removes** the key, which a caller
  passing a ttl never meant;
- a `limit` outside 1–1000;
- an empty prefix (the node refuses it; the client leaves `PREFIX` out instead) — so an empty or absent prefix
  means *every key*;
- an empty holder.

A ttl is carried as the store's **duration** value (protocol §4), converted from the language's own duration type.

**`keys` answers strings.** The wire spells each key in the language's own form (protocol §3.5, the keys outcome):
a text key arrives quoted, `'user:1'`. A client turns a quoted key back into the string — drop the surrounding
quotes, `\'` becomes `'` and `\\` becomes `\` — and returns any other kind of key (an integer id a script wrote)
in its language form unchanged, because it is not a key this handle wrote.

## 2a. Containers on a key (1.1)

A key may hold a **list**, a **hash**, a **set of members** or a **ranking** (members with scores). Writes are the
node's container statements and are atomic per key; reads are functions over `GET`, so each call is still ONE
statement. A read of a key that is not there answers the **empty** form — an empty list, map or set, `0` for a
length, `false` for a membership — and an element, field, score or rank that is not there answers **absent**.

| call | statement | answer |
|---|---|---|
| `listPush(key, values, front = false)` | `LIST PUSH {s}:$k BACK $v0, $v1, …;` · front `LIST PUSH {s}:$k FRONT $v0, …;` | the new length |
| `listPop(key, front = true)` | `LIST POP {s}:$k FRONT;` · back `LIST POP {s}:$k BACK;` | the element, or **absent** |
| `listPop(key, front, count)` | `LIST POP {s}:$k FRONT LIMIT $n;` (`BACK` likewise) | the elements, in the order taken |
| `listRange(key, start, count)` | `RETURN array::slice(GET {s}:$k, $i, $n);` | the elements |
| `listAt(key, index)` | `RETURN array::at(GET {s}:$k, $i);` | the element (negative from the back), or **absent** |
| `listLen(key)` | `RETURN array::len(GET {s}:$k);` | the length |
| `listRemove(key, value, count?)` | `LIST REMOVE {s}:$k $v;` · with a count `LIST REMOVE {s}:$k $v LIMIT $n;` | how many went |
| `listTrim(key, start, stop)` | `LIST TRIM {s}:$k FROM $i TO $j;` | nothing |
| `hashSet(key, fields)` | `HASH SET {s}:$k $f0 = $v0, $f1 = $v1, …;` | how many fields are new |
| `hashGet(key, field)` | `RETURN object::get(GET {s}:$k, $f);` | the value, or **absent** |
| `hashGetAll(key)` | `GET {s}:$k;` | the fields as a map |
| `hashRemove(key, fields)` | `HASH REMOVE {s}:$k $f0, $f1, …;` | how many went |
| `hashIncr(key, field, by = 1)` | `HASH INCR {s}:$k $f BY $n;` | the field's new integer |
| `hashLen(key)` | `RETURN object::len(GET {s}:$k);` | how many fields |
| `membersAdd(key, members)` | `MEMBERS ADD {s}:$k $v0, $v1, …;` | how many are new |
| `membersRemove(key, members)` | `MEMBERS REMOVE {s}:$k $v0, $v1, …;` | how many went |
| `membersHas(key, member)` | `RETURN $v IN GET {s}:$k;` | the boolean |
| `membersAll(key)` | `GET {s}:$k;` | the members |
| `membersLen(key)` | `RETURN set::len(GET {s}:$k);` | how many |
| `scoresAdd(key, scores)` | `SCORES ADD {s}:$k $m0 = $c0, $m1 = $c1, …;` | how many members are new |
| `scoresIncr(key, member, by)` | `SCORES INCR {s}:$k $m BY $c;` | the member's new score |
| `scoresRemove(key, members)` | `SCORES REMOVE {s}:$k $m0, $m1, …;` | how many went |
| `scoresScore(key, member)` | `RETURN scores::score(GET {s}:$k, $m);` | the score, or **absent** |
| `scoresRank(key, member)` | `RETURN scores::rank(GET {s}:$k, $m);` | the place from the lowest, from 0, or **absent** |
| `scoresRange(key, start, stop)` | `RETURN scores::range(GET {s}:$k, $i, $j);` | `(member, score)` pairs, lowest first |
| `scoresBetween(key, low?, high?)` | `RETURN scores::between(GET {s}:$k, $lo, $hi);` — an absent bound is written `NONE` in its place | `(member, score)` pairs, lowest first |

Parameter names: `$v0`, `$v1`, … values and members in the order given; `$f0`, `$f1`, … field names; `$m0`, … ranked
members with `$c0`, … their scores; single ones `$v`, `$f`, `$m`, `$c`; `$n` a count or an increment; `$i`, `$j` positions;
`$lo`, `$hi` score bounds. A hash's fields and a ranking's scores are sent in the order the caller gave them, and a
client whose language has no ordered map sends them in its map's iteration order — the node's answer does not depend
on it.

A client MUST refuse, before sending: an **empty** list of values, fields or members (the statement needs one); a
negative `start` or `count` for `listRange` (a slice starts at the first element; `listAt` and `scoresRange` count
from the back instead); a negative count for `listPop` or `listRemove`; a field, member or ranked member that is not
a string; a score that is not a finite number.

Kinds stay apart: a call on a key holding another kind is the node's refusal (`KeyHoldsAnotherKind` for a write,
`WrongArgument` for a read), raised as §5 says.

## 3. `getOrSet`

```
v = get(key)
if v is present: return v
v = loader()                                  -- the caller's code, outside any transaction
if setIfAbsent(key, v, ttl): return v
w = get(key)                                  -- somebody else stored first
return w if present else v                    -- …and it has already expired: keep ours
```

It does **not** coordinate racing callers: every caller that misses runs its loader, the first `IF ABSENT` wins,
and the others return the winner's value. A client says so in its documentation. The ttl is required, because a
cache entry with no expiry is not a cache entry.

## 4. The lease

`lock` returns a lease carrying the key, the holder and the ttl. The holder defaults to **128 bits in lowercase
hex, unique to the lease** — random where the language's standard library offers randomness, and otherwise derived
from the process's own entropy and a counter. Uniqueness is what matters, not secrecy: anybody who can read the
space can read the holder. A caller may pass its own (a worker name) when it needs to recognise the holder.

- A lease is **not a mutex**. Past its ttl another holder may take it and neither is told. Work that must not run
  twice extends before the ttl passes (and stops when `extend` answers `false`), or is made safe to run twice.
- `release` is **never** a delete and **never** a write without an expiry: a delete after the lease lapsed removes
  the next holder's lock, and a hand-back with no expiry makes the key permanent, after which every `lock` answers
  `false` for ever.
- `extend` without a ttl reuses the lease's own.

## 5. Refusals

A refusal from the node is raised as the client's ordinary refusal error, carrying the node's words. Retrying is
the caller's decision; `INCR` and a conditional `SET` alone already retry a lost race on the node for up to one
second.

## 6. Verification

Each client carries an offline test that the statements it sends are, byte for byte, the ones in §2 and §2a, and a
live test against a node covering every call — including a release followed by a `lock` from another holder succeeding,
which fails when the release writes no expiry.
