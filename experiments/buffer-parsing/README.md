# Buffer Parsing Experiments

Experiments to improve CLI permission option extraction.

## Problem

The current 4KB ring buffer often loses Option 1 of permission prompts because:
1. Status updates fill the buffer
2. Option 1 scrolls off before the hook reads it

## Hypothesis

A line-based log (500 lines) will capture complete permission prompts more reliably than a byte-based buffer (4KB).

## Files

- `line_logger.py` - Monitors buffer and maintains line-based log
- `parse_line_log.py` - Parses line log for permission prompts
- `README.md` - This file

## How to Run

### Terminal 1: Start the line logger
```bash
cd experiments/buffer-parsing
python3 line_logger.py --latest
```

### Terminal 2: Trigger a permission prompt
In your Claude session, do something that requires permission:
```
# Example: run a command that needs approval
curl https://example.com
```

### Terminal 3: Analyze the captured data
```bash
cd experiments/buffer-parsing
python3 parse_line_log.py
```

## Output Files

- `~/.claude/slack/logs/experiment_line_log.txt` - Captured lines
- `~/.claude/slack/logs/experiment_debug.log` - Debug info

## Expected Results

With the line logger running, permission prompts should be fully captured:
```
FOUND PERMISSION PROMPT

Question (line 145):
  Claude wants to run this command

Options:
  1. Yes
  2. Yes, and always allow this command
  3. No
```

vs. current behavior:
```
Options:
  1. [MISSING - scrolled off buffer]
  2. Yes, and always allow this command
  3. No
```

## Comparing Approaches

| Approach | Size | Option 1 Captured | Notes |
|----------|------|-------------------|-------|
| Current (4KB bytes) | 4096 bytes | ~30% | Ring buffer, ANSI included |
| Line log (500 lines) | ~50KB | Expected higher | Clean text, line-based |

## Next Steps

1. Run experiments to measure Option 1 capture rate
2. If successful, consider integrating line-based approach
3. Measure memory/performance impact
