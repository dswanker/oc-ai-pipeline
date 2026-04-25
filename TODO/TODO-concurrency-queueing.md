# TODO: Concurrency / Queueing for the oc-ai-pipeline

## Problem
The Railway backend currently processes monday webhooks synchronously in the
request handler. If two sales reps set "Send to AI" on different items within
the same ~5-10 minute window, the second webhook will start processing while
the first is still running, which can cause:

1. **Shared workspace collisions** — tempdirs with the same name, race
   conditions writing to /tmp/, etc.
2. **Anthropic rate limit hits** — running 2 full extractions in parallel
   burns through tokens fast and may fail with 429 errors.
3. **Monday API rate limits** — especially if both items upload ~16 files each.
4. **OpenClinica board-import race** — two concurrent `/api/importStudy` calls
   against the same study could corrupt state.
5. **Railway container memory** — running two pipelines simultaneously may
   exceed the 512MB-2GB container limit.

## Current state (zero protection)
- `pipeline.py` uses `asyncio.gather` for INTERNAL parallelism within one
  item (Chains A/B/C/D) but has NO cross-item queueing.
- Webhooks hit `/webhook/monday` which starts processing immediately in the
  same request.
- No semaphore, lock, or queue exists.

## Options (ordered by effort)

### Option 1: Simple in-process lock (minimal effort)
Add an `asyncio.Lock` or `asyncio.Semaphore(1)` around the pipeline call.
- Pros: 10 lines of code. Prevents concurrent execution on a single
  Railway container.
- Cons: Doesn't help if Railway scales horizontally to multiple
  containers. Doesn't preserve request order. No visibility into queue
  depth. A webhook that has to wait 5 minutes will hit monday's timeout.

### Option 2: In-process queue with HTTP 202 response (moderate effort)
Immediately respond to monday webhook with `202 Accepted`, then enqueue the
work to a Python background task that processes one at a time.
- Pros: Monday webhook returns fast (no timeout). FIFO processing. Small
  code change (~30 lines).
- Cons: Queue lives in memory — lost if Railway restarts. Still
  single-container only.

### Option 3: Railway-native queue (e.g. Railway Redis + a Python worker)
Two Railway services: the webhook receiver (just enqueues) and a worker
(pulls from queue).
- Pros: Survives restarts. Scales workers if needed. Queue depth visible.
- Cons: Added infrastructure cost (~$5/mo for Redis). ~100-200 lines of
  code. Monitoring overhead.

### Option 4: monday.com-native state machine
Use a monday column like "Queue Status" (queued / running / done) and
poll from worker. Same architecture as Option 3 but uses monday as the
queue store.
- Pros: No new infrastructure. Queue state visible in monday itself.
- Cons: Polling overhead. monday API rate limits. Less reliable for FIFO
  ordering.

## Recommendation for when we tackle this
Start with Option 2 (in-process queue + 202 response). It's the smallest
change that prevents the immediate collision problem. If usage ever grows
beyond ~5 concurrent users, upgrade to Option 3.

## Additional considerations when implementing

- Idempotency: what happens if monday retries a webhook? Use the
  monday `pulseId` + `columnId` + timestamp as a dedup key so the
  same trigger doesn't run twice.

- Status visibility: while queued, set the monday status column to
  "Queued — waiting behind N jobs" so sales reps know why it's not
  running yet.

- Timeout behavior: if a pipeline run exceeds some max duration
  (say 15 min), kill it and release the lock so the queue doesn't
  deadlock.

- Crash recovery: if the worker crashes mid-run, the in-flight item
  should return to the queue OR be marked as failed in monday.

- Cancel support: sales rep should be able to change status away from
  "Send to AI" and have a queued-but-not-started job be cancelled.
