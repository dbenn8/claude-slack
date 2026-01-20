# Timing Instrumentation Quick Reference

## What Was Implemented

Timing instrumentation to measure the delay between permission prompt display and buffer read by the notification hook.

## Key Files

| File | Purpose | Lines Modified |
|------|---------|----------------|
| `core/claude_wrapper_hybrid.py` | Buffer write timing | 934-966 |
| `hooks/on_notification.py` | Buffer read timing | 887-914 |
| `tests/unit/test_timing_instrumentation.py` | Test suite | New file (8 tests) |

## Log Format

```
[TIMING] session_id=abc12345 buffer_write=1768857980.850224 hook_read=1768857980.925500 delta_ms=75.28
```

## Where to Find Logs

```bash
# Wrapper logs (buffer writes)
~/.claude/slack/logs/wrapper_{session_id}.log

# Hook logs (buffer reads)
~/.claude/slack/logs/notification_hook_debug.log
```

## Quick Analysis

```bash
# Show all timing logs
grep '\[TIMING\]' ~/.claude/slack/logs/*.log

# Calculate average delta
grep '\[TIMING\]' ~/.claude/slack/logs/*.log | \
  grep -o 'delta_ms=[0-9.]*' | cut -d= -f2 | \
  awk '{ sum += $1; n++ } END { print "Avg:", sum/n, "ms" }'

# Find slow reads (> 200ms)
grep '\[TIMING\]' ~/.claude/slack/logs/*.log | \
  awk -F'delta_ms=' '{ if ($2 > 200) print }'
```

## Run Tests

```bash
cd /var/home/perry/.claude/claude-slack
python -m pytest tests/unit/test_timing_instrumentation.py -v
```

## Run Demo

```bash
cd /var/home/perry/.claude/claude-slack
python experiments/buffer-parsing/demo_timing_instrumentation.py
```

## Expected Values

| Delay Range | Classification | Meaning |
|-------------|----------------|---------|
| < 50ms | Fast | Buffer ready immediately |
| 50-150ms | Normal | Typical delay |
| 150-300ms | Slow | Retry loop or buffer fill delay |
| > 300ms | Problem | Potential race condition |

## Metadata File

Created alongside buffer file: `claude_output_{session_id}.meta`

```json
{
  "buffer_write_time": 1768857980.850224,
  "session_id": "abc12345-67890-full-uuid"
}
```

## Test Coverage

✅ 8/8 tests passing
- Buffer write timestamp logging
- Timestamp precision (microseconds)
- Buffer read timing delta
- Missing metadata handling
- Log format parsing
- Session ID tracking
- Realistic timing values
- End-to-end flow

## Documentation

Full documentation: `TIMING_INSTRUMENTATION.md`
Implementation summary: `IMPLEMENTATION_SUMMARY.md`
