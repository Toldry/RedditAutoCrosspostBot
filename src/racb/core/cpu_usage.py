"""Measures the bot's CPU use for the heartbeat, so a CPU-starved VM shows up in the log.

Two numbers per heartbeat interval:
- The process's CPU time (all threads) as a percentage of one core. On the Always Free e2-micro, the
  VM is entitled to about a quarter of a core over time, so sustained values near 25% mean the bot is
  using up what the VM gets.
- Steal: the share of the VM's CPU time the hypervisor gave to other machines while the VM wanted to run
  (`/proc/stat`). High steal means the VM is being throttled, whatever the bot itself uses. In a Docker
  container, `/proc/stat` shows the whole VM, which is what matters here. Elsewhere (macOS, Windows) it is
  not available, and only the process's own share is reported.
"""

import time

PROC_STAT_PATH = '/proc/stat'

# Field positions on the `cpu` line of /proc/stat: user nice system idle iowait irq softirq steal ...
STEAL_FIELD_INDEX = 7


def read_steal_and_total_ticks(path=PROC_STAT_PATH):
    """Returns (steal, total) CPU ticks of the whole machine since boot, or None where /proc/stat is unavailable."""
    try:
        with open(path, encoding='ascii') as proc_stat:
            first_line = proc_stat.readline()
    except OSError:
        return None
    fields = first_line.split()
    if not fields or fields[0] != 'cpu' or len(fields) <= STEAL_FIELD_INDEX + 1:
        return None
    # Only the first 8 counters add up to the total; guest time is already counted in user time
    ticks = [int(value) for value in fields[1:STEAL_FIELD_INDEX + 2]]
    return ticks[STEAL_FIELD_INDEX], sum(ticks)


class CpuUsage:
    """Reports CPU use since the previous `sample()` (or since creation, for the first one)."""

    def __init__(self, read_ticks=read_steal_and_total_ticks):
        self._read_ticks = read_ticks
        self._last_wall = time.monotonic()
        self._last_process = time.process_time()
        self._last_ticks = read_ticks()

    def sample(self):
        """Returns (process percent of one core, steal percent or None) since the previous sample."""
        wall, process, ticks = time.monotonic(), time.process_time(), self._read_ticks()
        elapsed = wall - self._last_wall
        process_percent = 100 * (process - self._last_process) / elapsed if elapsed > 0 else 0.0
        steal_percent = None
        if ticks is not None and self._last_ticks is not None:
            total_delta = ticks[1] - self._last_ticks[1]
            if total_delta > 0:
                steal_percent = 100 * (ticks[0] - self._last_ticks[0]) / total_delta
        self._last_wall, self._last_process, self._last_ticks = wall, process, ticks
        return process_percent, steal_percent
