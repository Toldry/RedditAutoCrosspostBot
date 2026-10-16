**The bot no longer restarts when the r/all comment listing comes back empty.** Reddit sometimes returns no comments
for the comment reader's head poll even when asked for 25. That raised an error that ended the process, so Docker
restarted the container over and over (71 times on the VM on 2026-10-16), and every restart also killed a
`docker compose exec bot python -m racb --process-one` run silently, with exit code 137. An empty listing now only
skips that poll, logged once per streak; the next poll queues everything it missed, so no comment is skipped.
