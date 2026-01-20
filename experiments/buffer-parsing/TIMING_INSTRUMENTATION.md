# Timing Instrumentation for Buffer Read Race Condition

## Overview

This instrumentation measures the delay between when Claude writes a permission prompt to the output buffer and when the notification hook reads it. This data helps diagnose race conditions where the hook may read the buffer before it's fully populated.

## Implementation

### 1. Buffer Write Instrumentation (`claude_wrapper_hybrid.py`)

**Location:** `add_to_output_buffer()` method (lines 934-966)

**What it does:**
- Captures timestamp when buffer is written: `buffer_write_time = time.time()`
- Writes timing metadata to companion file: `claude_output_{session_id}.meta`
- Logs timing event: `[TIMING] session_id=abc12345 buffer_write=1234567.890123`

**Metadata File Format:**
```json
{
  "buffer_write_time": 1234567.890123,
  "session_id": "abc12345-67890-full-uuid"
}
```

### 2. Buffer Read Instrumentation (`on_notification.py`)

**Location:** `enhance_notification_message()` function (lines 887-914)

**What it does:**
- Reads timing metadata from companion file
- Captures timestamp when buffer is read: `hook_read_time = time.time()`
- Calculates delta: `delta_ms = (hook_read_time - buffer_write_time) * 1000`
- Logs complete timing metrics: `[TIMING] session_id=abc12345 buffer_write=1234567.890123 hook_read=1234567.950456 delta_ms=60.33`

## Log Format

All timing logs follow this structured format for easy parsing:

```
[TIMING] session_id=<8-char-id> buffer_write=<timestamp> hook_read=<timestamp> delta_ms=<milliseconds>
```

### Example Log Entry
```
[TIMING] session_id=abc12345 buffer_write=1768857980.850224 hook_read=1768857980.925500 delta_ms=75.28
```

### Parsing the Log

Python example:
```python
import re

log_entry = "[TIMING] session_id=abc12345 buffer_write=1234567.890123 hook_read=1234567.950456 delta_ms=60.33"

# Extract values
session_match = re.search(r'session_id=([a-zA-Z0-9]+)', log_entry)
write_match = re.search(r'buffer_write=([\d.]+)', log_entry)
read_match = re.search(r'hook_read=([\d.]+)', log_entry)
delta_match = re.search(r'delta_ms=([\d.]+)', log_entry)

session_id = session_match.group(1)  # 'abc12345'
buffer_write = float(write_match.group(1))  # 1234567.890123
hook_read = float(read_match.group(1))  # 1234567.950456
delta_ms = float(delta_match.group(1))  # 60.33
```

## Where to Find Timing Logs

Timing logs are written to:
- **Wrapper logs:** `~/.claude/slack/logs/wrapper_{session_id}.log`
- **Hook logs:** `~/.claude/slack/logs/notification_hook_debug.log`

### Filtering Timing Logs

```bash
# From wrapper logs
grep '\[TIMING\]' ~/.claude/slack/logs/wrapper_*.log

# From hook logs
grep '\[TIMING\]' ~/.claude/slack/logs/notification_hook_debug.log

# Get all timing data sorted by delta
grep '\[TIMING\]' ~/.claude/slack/logs/*.log | \
  grep -o 'delta_ms=[0-9.]*' | \
  cut -d= -f2 | \
  sort -n
```

## Analysis Examples

### Calculate Average Delta
```bash
grep '\[TIMING\]' ~/.claude/slack/logs/*.log | \
  grep -o 'delta_ms=[0-9.]*' | \
  cut -d= -f2 | \
  awk '{ sum += $1; n++ } END { if (n > 0) print "Average:", sum/n, "ms" }'
```

### Find Slowest Reads (> 200ms)
```bash
grep '\[TIMING\]' ~/.claude/slack/logs/*.log | \
  awk -F'delta_ms=' '{ if ($2 > 200) print }'
```

### Distribution by Time Range
```bash
grep '\[TIMING\]' ~/.claude/slack/logs/*.log | \
  grep -o 'delta_ms=[0-9.]*' | \
  cut -d= -f2 | \
  awk '{
    if ($1 < 50) fast++
    else if ($1 < 100) medium++
    else if ($1 < 200) slow++
    else very_slow++
  }
  END {
    print "< 50ms:", fast
    print "50-100ms:", medium
    print "100-200ms:", slow
    print "> 200ms:", very_slow
  }'
```

## Expected Values

Based on testing:
- **Typical delay:** 50-150ms
- **Fast reads:** < 50ms (buffer ready immediately)
- **Slow reads:** 150-300ms (buffer fill delay or retry loop)
- **Problem threshold:** > 300ms (indicates potential race condition)

## Testing

Tests are located in: `/tests/unit/test_timing_instrumentation.py`

Run tests:
```bash
python -m pytest tests/unit/test_timing_instrumentation.py -v
```

Test coverage:
- ✅ Buffer write logs timestamp
- ✅ Timestamp has microsecond precision
- ✅ Buffer read logs timing delta
- ✅ Graceful handling of missing metadata
- ✅ Log format is parseable
- ✅ Log includes session ID
- ✅ Works with realistic timing values
- ✅ End-to-end timing flow

## Demo

Run the demo script to see timing instrumentation in action:
```bash
python experiments/buffer-parsing/demo_timing_instrumentation.py
```

This demonstrates:
1. Buffer write with timestamp
2. Simulated race condition delay
3. Buffer read with delta calculation
4. Log parsing and verification
5. Multiple delay scenarios

## Troubleshooting

### No timing logs appearing

1. Check that DEBUG logging is enabled for wrapper:
   ```bash
   export DEBUG_WRAPPER=1
   ```

2. Verify log files exist:
   ```bash
   ls -lh ~/.claude/slack/logs/
   ```

3. Check log level in wrapper (should include DEBUG):
   ```python
   # In claude_wrapper_hybrid.py setup_logging()
   logger.setLevel(logging.DEBUG)  # Should be DEBUG, not INFO
   ```

### Metadata file missing

- The `.meta` file is created alongside the buffer `.txt` file
- If buffer writes are failing, metadata writes will also fail
- Check wrapper logs for "Failed to write output buffer" errors

### Timestamps seem wrong

- Timestamps are Unix epoch time (seconds since 1970-01-01)
- Use `date -d @<timestamp>` to convert to human-readable format
- Example: `date -d @1768857980.850224`

## Next Steps

This timing instrumentation provides data to:
1. Measure actual race condition frequency
2. Validate buffer retry loop effectiveness
3. Tune retry delays and max attempts
4. Identify edge cases causing slow buffer reads
5. Guide buffer optimization strategies
