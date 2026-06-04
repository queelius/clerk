# Design: Clerk "Faithful Mirror" (Trust & Correctness Round)

**Date:** 2026-06-04
**Status:** Approved design (pre-implementation-plan)
**Author:** Alexander Towell (with Claude Code)
**Scope:** `beta/clerk`. Sync engine, cache schema, send-safety, tests, docs.

---

## 1. Context & Motivation

Clerk is a deliberately thin CLI/MCP server that lets an LLM agent (Claude) interact
with email over IMAP/SMTP, a "bridge, not a brain." A multi-agent audit of the codebase
(7 readers plus an adversarial critic plus a landscape scan) surfaced that the most serious
problems are **not missing features**. They are **silent-wrong-answer bugs in the sync
engine**. The cache can confidently return incomplete or stale data to the agent with no
signal that it has done so. For a tool whose entire job is to be a faithful bridge, that is
the worst possible failure mode: the user cannot see it happening.

This round centers on **trust and correctness**: make the cache a faithful, UID-keyed,
body-complete mirror of the server that never silently lies, close the one send-safety hole,
bound cache growth, and make the test suite actually exercise the risky paths. Feature
expansion (attachments, multi-folder, typed search tools, threading revival, injection
hardening) is explicitly deferred to later rounds.

### Decisions taken during brainstorming

| # | Decision | Choice |
|---|----------|--------|
| 1 | Primary thrust | **Trust & correctness** |
| 2 | Sync-engine depth | **UID identity plus reconciliation** (schema change plus migration) |
| 3 | Search/body model | **Eager bodies on sync** (cache becomes a true mirror) |
| 4 | Migration strategy | **A: preserve non-derivable, rebuild derivable** |
| 5 | Flags storage | **Bitmask INTEGER** |
| 6 | HTML-to-text parser | **BeautifulSoup4** |
| 7 | `priorities` config | **Keep, mark advisory** in `clerk://config` |

---

## 2. Goals

1. The cache is a **faithful mirror**: what the agent reads matches the server, including
   flags/folder/expunge changes made on *other devices* (phone, webmail).
2. **Full-text search is honest**: `MATCH` never silently misses messages because their
   body was never fetched.
3. **No silent truncation**: a large or first sync fetches everything (within retention),
   not a hidden 200-message window.
4. **Send safety holds**: a failed audit-log write can never raise the effective send rate
   limit.
5. **Bounded growth**: the cache cannot grow without limit; size is observable.
6. **Honest state**: the agent can see the "as-of" time of cached data.
7. **Real tests**: the riskiest code (`_parse_message`, send-safety layers, reconciliation,
   migration) is covered; the dead integration suite is repaired.
8. **Honest docs**: README and CLAUDE.md describe what actually exists.

## 3. Non-Goals (deferred to later rounds)

- **Attachment fetch/send.** `ImapClient.fetch_attachment()` remains present (a dead-code
  seed for the capability round) but is *not* wired to a tool. We **do** fix the README,
  which currently advertises a nonexistent `clerk attachment ... --save` command.
- **Multi-folder / Sent sync, triage bundle, typed `clerk_list`/`clerk_search`, batch
  flag/move/read** (ergonomics and capability rounds). Sync remains INBOX-only as today; the
  engine work generalizes to other folders but no multi-folder orchestration is added.
- **JWZ threading revival** (`threading.py` stays as-is; we only fix the cheap
  canonical-subject nit, see section 4.1). **Prompt-injection sanitization.**
  **Retry/backoff/reconnect/timeouts and token-aware reconnect** (the "full re-arch"
  option, declined). **Address normalization** of to/cc/reply_to (stays JSON this round).

---

## 4. Detailed Design

### 4.1 Schema v2 plus migration (Workstream 1)

**Versioning.** Store an integer `schema_version` in `cache_meta`. Define
`SCHEMA_VERSION = 2`. On cache open, read it; absent or `< 2` triggers migration.

**Migration (approach A, preserve non-derivable, rebuild derivable):**
- **Preserve** `drafts` (unsent work) and `send_log` (audit trail). These are *not*
  reconstructible from the server.
- `send_log` gains a `status TEXT NOT NULL DEFAULT 'sent'` column via
  `ALTER TABLE ... ADD COLUMN` (existing rows are historical sends, so `'sent'`). See
  section 4.4.
- **Rebuild** the derivable tables: `DROP` `messages`, `messages_fts`, and `sync_state`;
  recreate them in the v2 shape; set `schema_version = 2`.
- The next sync repopulates `messages` from the server, now with UIDs and bodies. No
  flag/body *data* migration is written, because that data is re-derived from IMAP. This is
  why approach A keeps migration code tiny and de-risks the invasive flags-bitmask change.

**`messages` v2 shape** (identity equals server truth):

```
messages (
  account            TEXT    NOT NULL,
  folder             TEXT    NOT NULL,
  uid                INTEGER NOT NULL,        -- server UID within (account, folder)
  message_id         TEXT,                    -- RFC822 Message-ID (indexed; may dup/null)
  conv_id            TEXT    NOT NULL,         -- 12-char thread id (unchanged scheme)
  root_message_id    TEXT,                    -- thread root id
  thread_subject     TEXT,                    -- normalized (Re/Fwd-stripped) subject
  subject            TEXT,
  from_name          TEXT,
  from_addr          TEXT,
  to_json            TEXT,                    -- address normalization deferred
  cc_json            TEXT,
  reply_to_json      TEXT,
  date_utc           TEXT,
  flags              INTEGER NOT NULL DEFAULT 0,  -- bitmask, see below
  body_text          TEXT,
  body_html          TEXT,
  body_skipped       INTEGER NOT NULL DEFAULT 0,  -- 1 if body exceeded size cap
  size               INTEGER,
  headers_fetched_at TEXT,
  body_fetched_at    TEXT,
  PRIMARY KEY (account, folder, uid)
)
```

- Indexes: `conv_id`, `date_utc DESC`, `from_addr`, `message_id`, `(account, folder)`,
  `flags`.
- `message_id` is no longer the primary key. This fixes the bug where two distinct server
  messages sharing a Message-ID (forwarded or looped mail) silently overwrote each other.
- `clerk_read(message_id)` and reply lookups resolve via the `message_id` index; with
  INBOX-only sync this is unambiguous in practice. Multi-folder disambiguation is deferred.

**Flags bitmask.** Replace the `flags` JSON-in-TEXT column (which forced the brittle,
un-indexable `flags NOT LIKE '%"seen"%'` predicate in three places) with an INTEGER bitmask:

```
SEEN = 1, ANSWERED = 2, FLAGGED = 4, DELETED = 8, DRAFT = 16, RECENT = 32
```

- A mapping layer converts IMAP flag tuples to/from the bitmask and the existing
  `MessageFlag` enum and `Message.is_read`/`is_flagged` derived properties (which now read
  the bitmask).
- `update_flags` operates on the bitmask. Unread becomes the indexable `flags & 1 = 0`.
- `clerk://schema` example queries and `EXAMPLE_QUERIES` are updated accordingly (no more
  `LIKE` hacks).

**FTS5.** `messages_fts` is recreated as an external-content table over
`(message_id, subject, body_text, from_name, from_addr)` keyed on `rowid`, with the existing
insert/delete/update sync triggers. Because bodies are now fetched eagerly (section 4.2), the
FTS index is populated with real body text and search is complete. `clerk://schema` gains
`bm25()` and `snippet()` examples (relevance ranking plus previews) that are free and
currently never shown to the agent.

**Canonical subject (cheap correctness nit).** Listings currently use `MIN(subject)`
(alphabetically-first, wrong). Since we rebuild the schema anyway, persist `thread_subject`
(normalized at store time using the normalization logic that already exists) and
`root_message_id`, and use `thread_subject` for conversation listings.

### 4.2 Sync engine v2 (Workstream 2)

**Eager bodies.** Sync fetches full bodies (`BODY.PEEK[]`) rather than headers only, parses
text plus HTML, and stores both. The cache becomes a true mirror and FTS is unconditionally
complete.
- **Body size cap** (default 2 MB, configurable): a body over the cap is stored
  header-only with `body_skipped = 1`. This keeps the cache bounded against pathological
  messages. To preserve search honesty, `clerk_status` reports the count of `body_skipped`
  messages, and `clerk_read` **force-fetches** a skipped body on demand (ignoring the cap
  when the agent explicitly opens that message).

**Paging, no silent cap.** First or full sync pages the UID set (chunks of about 200),
fetching eager bodies per chunk and **advancing the watermark per chunk**. This removes the
silent 200-message truncation and makes a large or interrupted sync resumable (partial
progress survives), the cheap resilience win that does not require the deferred
retry/backoff layer.

**Two-phase incremental sync.** Each incremental sync does:
1. **Append:** fetch new UIDs above the watermark (eager bodies), store them.
2. **Reconcile:** detect flag/move/expunge changes to *already-cached* messages made on
   other devices.
   - Where the server advertises **CONDSTORE** (RFC 7162): use `HIGHESTMODSEQ` plus
     `FETCH ... (FLAGS) CHANGEDSINCE <modseq>`; where **QRESYNC** is available, use
     `VANISHED` for expunge detection.
   - **Fallback** (for example Greenmail, servers without CONDSTORE): bounded
     `UID FETCH FLAGS` over cached UIDs in the folder plus `UID SEARCH ALL` set-difference
     to find expunged UIDs.
   - Apply: update flag bitmasks, delete cache rows for expunged UIDs.

This is the **multi-device-drift fix**. The cache now learns about mail you read or filed on
your phone.

**Parse-failure dead-letter.** A UID whose message fails to parse is recorded in a
dead-letter set in `cache_meta` (with attempt count) and retried on subsequent syncs, rather
than the watermark stepping past it permanently (today's silent data loss). After a few attempts (default 3)
it is surfaced (logged plus counted in `clerk_status`) and skipped.

**Body-clobber fix (belt and suspenders).** `store_message` upserts via
`INSERT ... ON CONFLICT(account, folder, uid) DO UPDATE` that updates headers/flags/folder
but **preserves an existing non-null body** when the incoming row has none. Even with eager
bodies, this guarantees a re-sync never wipes a fetched body.

### 4.3 UID-keyed mutations (Workstream 3)

- Flag, move, and body-fetch operations use the stored `(folder, uid)` and issue
  `UID STORE` / `UID FETCH`, eliminating the `SEARCH HEADER Message-ID` round-trip per
  mutation and its `results[0]` ambiguity.
- **Atomic move:** use `UID MOVE` (RFC 6851) where the server advertises `MOVE`; otherwise
  `COPY` plus `UID STORE +FLAGS \Deleted` plus **`UID EXPUNGE`** (RFC 4315), so we never
  purge unrelated `\Deleted` messages in the folder (today's plain `EXPUNGE` can).

### 4.4 Send-safety coupling fix (Workstream 4)

The rate limiter counts `send_log` rows in the window, but `log_send` is best-effort *after*
SMTP succeeds, so a failed log silently raises the effective ceiling. Fix with a
**pre-send reservation**:
1. Insert a `send_log` row with `status = 'pending'` **before** calling SMTP.
2. On SMTP success, `UPDATE ... status = 'sent'`; on failure, `status = 'failed'`.
3. The rate limiter counts rows with `status IN ('pending', 'sent')` in the window.

This closes the ceiling hole and hardens against double-send. `failed` rows remain as an
audit trail of attempts. Draft deletion stays best-effort after a confirmed send. The
content-bound, single-use two-step send token is **unchanged** (it is the strongest part of
the codebase, see section 6).

### 4.5 Read-time honesty and small correctness (Workstream 5)

- **Staleness signal.** Persist `last_sync_at` per `(account, folder)` in `sync_state`,
  expose it in `clerk_status` with a fresh/stale verdict, and ensure it is `clerk_sql`-
  queryable so the agent can know the "as-of" time of what it reads.
- **Real HTML-to-text.** Replace the regex `html_to_text` with a **BeautifulSoup4**-based
  extractor (adds the `beautifulsoup4` dependency). Improves body readability for M365's
  HTML-only mail and removes a regex-over-structured-data smell.
- **Concurrency.** Enable `PRAGMA journal_mode=WAL` and a `busy_timeout` on every cache
  connection; route `DraftManager` through the `Cache` connection wrapper so there is a
  single connection discipline. This makes the existing "atomic for concurrent MCP clients"
  claim actually true.
- **Bounded growth.** Wire up `prune_old_messages`, **off by default** (clerk is a
  searchable archive). It runs only when `window_days` is configured, never touches
  `send_log`, and `clerk_status` reports cache size plus oldest-message date.

### 4.6 Doc honesty (Workstream 6)

- **README:** correct the tool inventory to the 10 real tools with real names (remove the
  fictional `clerk_inbox`/`clerk_search`/`clerk_mark_read` and the nonexistent
  `clerk attachment ... --save` command). State that attachments are not yet supported.
- **CLAUDE.md:** remove the stale `search.py` and search-operator (`from:`,
  `has:attachment`, `is:unread`) references in Key Modules; describe the actual read path
  (`clerk_sql` plus FTS5 plus `clerk://schema`).
- **`priorities` config:** keep the field; `clerk://config` explicitly documents it as
  **advisory** (clerk does not sort or filter by it; the agent applies it). Wiring it into a
  real VIP/priority-score hook is deferred to the ergonomics round.

### 4.7 Test plan (Workstream 7)

Per the project rule to add tests for new behavior and check coverage:

- **Regression test per bug:** body-clobber preservation; FTS completeness after eager-body
  sync; paging past the 200-cap (assert no truncation); multi-device reconcile (flags plus
  expunge) via the fallback path; parse-failure dead-letter retry; send reservation and
  rate-limit-holds-on-log-failure; UID-scoped expunge does not purge other `\Deleted`;
  migration preserves `drafts`/`send_log` and rebuilds `messages`.
- **`imap_client._parse_message` unit tests** (the largest untested, riskiest code) with
  fixtures: plain text, HTML-only Outlook, multipart plus attachment, missing Date, unusual
  charsets, bare display names.
- **The three untested send-safety layers:** blocked-recipients (including cc/bcc plus
  case-insensitivity), FROM/account-mismatch rejection, audit-log-failure-does-not-swallow-
  a-successful-send.
- **Rewrite the dead integration suite** against the current API (Greenmail), covering the
  real end-to-end IMAP/SMTP path. Confirm the CONDSTORE fallback is exercised (Greenmail
  likely lacks CONDSTORE, which gives good fallback coverage).
- **OAuth refresh** path (expired plus refresh-success; refresh-failure yields a clean
  re-auth error, never a browser flow inside connect).
- **Sync failure paths** (watermark not advanced on store failure; `sync_all` per-account
  error capture).
- **CI:** add a `--cov-fail-under` floor; repair or quarantine integration so the coverage
  number is honest.

---

## 5. Risks & Rollout

- **Heavier upgrade re-sync.** Eager bodies make the one-time post-upgrade full re-sync
  slower. Mitigated by paging plus per-chunk watermark (resumable) and the body-size cap.
  `drafts` and `send_log` are preserved across the rebuild, so nothing irreplaceable is lost.
- **CONDSTORE variance.** Servers differ; the bounded `FETCH FLAGS` plus set-difference
  fallback must be correct and is explicitly tested (the Greenmail path).
- **Flags-bitmask change** is the most invasive schema edit; rebuild-from-server (approach A)
  removes the need to migrate flag *data*, de-risking it.
- **WAL** creates `-wal`/`-shm` sidecar files in the data dir, expected and benign for a
  single-user tool.

## 6. What must NOT regress (preserve these)

- The **content-bound, single-use, restart-surviving two-step send token**, the strongest
  design in the codebase.
- **`clerk_sql` as the read interface**, the purest expression of the thesis and clerk's
  key advantage over rival servers that ship 47 to 200 tools (it sidesteps tool/token bloat
  entirely).
- **Server-first writes** (cache updated only after the server confirms).
- **Credential hygiene** (keyring-first, 0600 file-perm check, never echoing raw
  IMAP/SMTP/MSAL payloads to the agent).

## 7. Open Questions

None blocking. Disposition of `priorities` (advisory vs. eventual VIP hook) and the
address-table normalization are intentionally carried to the ergonomics round.
