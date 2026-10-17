**The comment reader no longer stops reading after a bogus newest-comment ID.** On 2026-10-17 the r/all
listing returned a comment ID about 6 × 10^18 (real IDs are about 8 × 10^10); the reader skipped ahead to it,
never saw a higher head again, and scanned nothing for hours. Head IDs far from the rest of the listing, or
implausibly far past the last accepted head, are now ignored (counted as `rejected` in the heartbeat), a
reader position far ahead of every head resets itself after 6 polls, and the heartbeat logs a WARNING when
heads were found but nothing was read. Deploying this also recovers a reader stuck on such a cursor.
