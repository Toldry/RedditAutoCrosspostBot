**Empty r/all head polls no longer flood the log.** Reddit returned an empty r/all comment listing on about
every other head poll after 3.1.1 was deployed, and each streak logged a WARNING and an INFO line, roughly
every 30 seconds. The head poll now asks for 100 comments instead of 25, which should make empty listings
rarer. Empty polls are counted in the heartbeat (`head: N polls (M empty)`), and a WARNING is logged only
after 6 empty polls in a row, since only a streak that long noticeably delays reading. No comments are lost
either way: the next successful poll queues every block up to the newest comment.
