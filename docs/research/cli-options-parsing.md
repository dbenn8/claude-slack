# Research: CLI Permission Options Parsing

**Date:** 2026-01-19
**Status:** In Progress
**Goal:** Display full CLI permission options in Slack, matching exactly what the user sees in terminal

## Problem Statement

When Claude Code requests permission for a tool, the CLI displays options like:
```
Claude wants to edit this file

1. Yes
2. Yes, and always allow edits during this session (shift+tab)
3. No
```

Currently, the Slack integration often fails to capture these exact options and falls back to generic "Yes/No" buttons. We need to reliably extract and display the actual CLI text.

## Current Architecture

### Buffer System
- **Location:** `~/.claude/slack/logs/claude_output_{session_id}.txt`
- **Implementation:** 4KB ring buffer (`deque(maxlen=4096)`) in `claude_wrapper_hybrid.py:475-490`
- **Content:** Raw terminal output including ANSI escape sequences

### Hook Flow
1. `PermissionRequest` hook receives JSON with `tool_name`, `tool_input`, `permission_suggestions`
2. Hook attempts to read terminal buffer file
3. Parses buffer for numbered options
4. Falls back to formatted `tool_input` if parsing fails

### Current Parser
- **Location:** `hooks/on_notification.py:261-426` (`parse_permission_prompt_from_output`)
- **Approach:** Forward scan for `^\d+[\.\)]\s*(.+)` pattern
- **Reconstruction:** Adds "Approve this time" when option 1 is missing

## Key Findings

### 1. Buffer Content Analysis

**Raw buffer characteristics:**
- 4096 bytes exactly (ring buffer)
- ~33 ANSI ESC sequences per buffer
- CR (0x0d) occurs at ~323 byte intervals (screen width)
- Heavy use of cursor positioning, not clean newlines

**After ANSI stripping:**
- ~1400-2800 chars of readable text
- Contains status messages, horizontal lines, prompts
- Permission options appear as `2. Text` and `3. No`

### 2. Why Parsing Often Fails

**Timing issue:** The buffer is continuously updated with status messages:
```
✻ Working on task (1.7k tokens · thinking)
Checking for updates
Prestidigitating…
```

When the hook reads the buffer, the permission prompt may have been:
- Not yet rendered (hook fires too early)
- Overwritten by status updates (ring buffer overflow)
- Partially captured (option 1 scrolled off)

**Evidence from logs:**
```
[2026-01-19 09:00:26.111] Buffer exists but no permission prompt found
[2026-01-19 09:01:15.327] Buffer exists but no permission prompt found
```

### 3. Option 1 Missing Problem

From successful parses, we typically see:
```
Item 2: Yes, allow all edits during this session (shift+tab)
Item 3: No
```

Option 1 is missing because:
1. 4KB buffer isn't large enough to hold full terminal history
2. Status line updates push option 1 out of the buffer
3. Terminal UI overwrites lines for dynamic display

**Current workaround:** Reconstruct option 1 as hardcoded "Approve this time"

### 4. False Positive: Status Line Collision

The pattern `1.7k tokens` matches as option 1:
```python
'1.7k tokens · thinking)' -> num=1, text='7k tokens · thinking)'
```

**Current mitigation:** Skip lines containing "tokens", "thinking", "running", "waiting"

### 5. Successful Parse Examples

From `notification_hook_debug.log`:
```
[2026-01-17 22:31:14] SUCCESS: Got exact options from buffer:
  ['Approve this time', 'Yes, allow all edits during this session (shift+tab)', 'No']

[2026-01-17 22:36:37] SUCCESS: Got exact options from buffer:
  ['Approve this time', "Yes, and don't ask again for systemctl status commands...", 'No']
```

These successes occurred when the buffer happened to contain the permission prompt at read time.

### 6. AskUserQuestion Has Structured Data (MAJOR FINDING - 2026-01-19)

**Discovery:** The `AskUserQuestion` tool passes **complete structured option data** in the hook's `tool_input` - no terminal parsing needed!

**Hook payload example:**
```json
{
  "tool_name": "AskUserQuestion",
  "tool_input": {
    "questions": [{
      "question": "Which messaging platform should we implement native support for first?",
      "header": "Platform",
      "options": [
        {"label": "Microsoft Teams", "description": "Enterprise-focused, uses Bot Framework SDK..."},
        {"label": "Discord", "description": "Developer-friendly API, large community..."},
        {"label": "Signal", "description": "Privacy-focused, requires signal-cli bridge..."},
        {"label": "iMessage", "description": "Apple ecosystem only, requires macOS..."}
      ]
    }]
  }
}
```

**Current behavior:**
```
[2026-01-19 11:14:22.027] Buffer exists but no permission prompt found
[2026-01-19 11:14:22.288] Using 2-button layout (Yes, No)  <-- WRONG!
```

The hook falls back to generic "Yes/No" because the buffer parser doesn't recognize AskUserQuestion format.

**Solution:** Check `tool_name` first:
- If `tool_name == "AskUserQuestion"`: Extract options directly from `tool_input.questions[].options[]`
- If `tool_name` is Bash/Edit/etc: Parse terminal buffer for permission options

**Terminal capture also works:** The line logger successfully captured the AskUserQuestion prompt:
```
103: Which messaging platform should we implement native support for first?
104: ❯1.Microsoft Teams
105: Enterprise-focused, uses Bot Framework SDK, good for corporate environments
106: 2.Discord
...
```

**Implications:**
1. AskUserQuestion can be handled **without any terminal parsing**
2. Options, descriptions, and multi-select flags are all available in JSON
3. Slack buttons can be dynamically generated from the structured data
4. This is the **preferred approach** for AskUserQuestion tools

## Backward Parsing Approach

### Algorithm
1. Start from end of buffer (most recent content)
2. Scan backward for numbered options (`N. text`)
3. Stop when finding non-numbered line
4. Look backward for question/context

### Test Results

| Test Case | Input | Result |
|-----------|-------|--------|
| Normal prompt | `2. Yes, allow...\n3. No` | ✓ Parsed, reconstructed opt 1 |
| File listing | `1. main.py\n2. utils.py` | ✓ Rejected (no permission keywords) |
| Status collision | `1.7k tokens` in buffer | ✓ Skipped (contains "tokens") |
| All options | `1. Yes\n2. Yes always\n3. No` | ✓ Parsed all 3 |

### Prototype Code
See `/tmp/backward_parser.py` for implementation.

## Session Change Challenges

### `/resume` Command

When user runs `/resume` in CLI:
1. CLI switches to different session internally
2. No hook notification of session change
3. Buffer file path remains tied to original session
4. Registry not updated with new session info

**Impact:** Permission prompts after `/resume` may be orphaned or mis-routed.

### `/compact` Command (NEW FINDING - 2026-01-19)

When user runs `/compact` in CLI:
1. CLI creates a **new session ID** for the compacted conversation
2. No hook notification of session change
3. Old buffer file stops updating, new buffer file created
4. Registry still points to old session ID

**Evidence:**
```
e537eb3d... last modified 10:44:42 (pre-compact)
83643ab1... last modified 10:55:57 (post-compact, actively updating)
```

**Impact:** Same as `/resume` - Slack integration loses track of the session. Permission prompts after `/compact` may be orphaned.

### Implications for Slack Integration

Both `/resume` and `/compact` break the session tracking. Possible solutions:
1. Monitor for new buffer files appearing in logs directory
2. Use `--latest` approach dynamically instead of fixed session ID
3. Watch for session ID changes in hook payloads
4. Implement session discovery/recovery mechanism

## Proposed Experiments

### Experiment 1: Larger Buffer
Increase ring buffer from 4KB to 16KB or 32KB to capture more history.

**Hypothesis:** Larger buffer will retain option 1 more often.

**Risk:** More memory usage, more data to parse.

### Experiment 2: Persistent Line Log ✓ TESTED
Keep a separate log of last N lines (e.g., 500 lines) instead of byte-based ring buffer.

**Hypothesis:** Line-based storage will preserve complete options regardless of length.

**Approach:**
- Maintain `deque(maxlen=500)` of cleaned lines
- Write to separate file on each update
- Parse from end of line log

**Result (2026-01-19):** SUCCESS - Captured complete 2-option permission prompt:
```
Line 490: Do you want to proceed?
Line 491: ❯ 1. Yes
Line 492: 2. No
Line 493: Esc to cancel · Tab to add additional instructions
```
Both Option 1 and Option 2 were preserved in the 500-line log.

### Experiment 3: Snapshot on Hook Trigger
When PermissionRequest hook fires, immediately snapshot the buffer before any delays.

**Hypothesis:** Timing is critical; earlier read = better chance of capturing prompt.

## Test Tools Created

1. **`/tmp/analyze_buffer.py`** - Real-time buffer monitor with logging
2. **`/tmp/backward_parser.py`** - Backward parsing prototype
3. **`/tmp/capture_permission_buffer.sh`** - Snapshot capture script

## Next Steps

1. [x] Run experiments with larger buffer / line log - SUCCESS
2. [x] Capture live permission prompt data - SUCCESS
3. [ ] Measure timing between prompt display and hook execution
4. [x] Test backward parsing on real captured data - SUCCESS
5. [x] Investigate if Claude Code provides any additional structured data - **YES! AskUserQuestion has full structured data**

### New Action Items (2026-01-19)

6. [x] Implement `AskUserQuestion` handler that extracts options from `tool_input` ✅ COMPLETED
7. [ ] Add noise filtering to line logger (spinners, status messages, ANSI fragments)
8. [ ] Test with other tool types to see if they also have structured option data
9. [x] Consider hybrid approach: structured data first, terminal parsing as fallback ✅ IMPLEMENTED

### AskUserQuestion Implementation (2026-01-19)

**Status:** FULLY IMPLEMENTED

The AskUserQuestion Slack integration is now complete with:
- Emoji-based option selection (1️⃣ 2️⃣ 3️⃣ 4️⃣)
- Thread reply support for "Other" custom text
- Multi-question support (2-4 questions)
- Multi-select support
- File-based response protocol with atomic locking
- Comprehensive test coverage (317 tests)

**Key files:**
- `hooks/on_pretooluse.py` - Main hook implementation
- `core/slack_listener.py` - Reaction and reply handlers
- `tests/e2e/test_askuserquestion_flow.py` - E2E tests
- `docs/plans/askuserquestion-slack-hook.md` - Full implementation plan

## Experiment Results Summary

### Line-Based Logging (Experiment 2) - SUCCESS

**Test Date:** 2026-01-19

**Setup:**
- 500-line deque capturing cleaned terminal output
- Backward parsing from end of log
- Cursor prefix handling (❯)

**Captured:**
```
Question: "Do you want to proceed?"
Option 1: "Yes" ✓
Option 2: "No" ✓
```

**Key Difference from 4KB Buffer:**
- 4KB buffer: Often loses Option 1 due to status message overflow
- 500-line log: Retained complete prompt with both options

**Parser improvements needed:**
1. Strip cursor prefix (❯) before matching
2. Look for question in preceding lines
3. Handle "Esc to cancel" help text

## Recommendations

### Priority 1: Use Structured Data When Available
1. **Handle `AskUserQuestion` specially** - Extract options from `tool_input.questions[].options[]`
2. **Check other tools** for structured option data in `tool_input` or `permission_suggestions`
3. **Avoid terminal parsing** when structured data is available (more reliable)

### Priority 2: Improve Terminal Parsing as Fallback
4. **Implement line-based logging** in the wrapper alongside or replacing byte buffer
5. **Increase buffer retention** - 500 lines provides much more context than 4KB
6. **Add cursor prefix handling** - permission prompts may have ❯ prefix
7. **Filter noise aggressively** - spinners, status messages, ANSI fragments fill buffer fast
8. **Consider hook timing** - snapshot buffer immediately when hook fires

### Priority 3: Handle Session Changes
9. **Detect `/compact` and `/resume`** - these create new session IDs
10. **Use dynamic session discovery** - find most recently modified buffer file

## References

- Buffer implementation: `core/claude_wrapper_hybrid.py:475-490, 940-962`
- Current parser: `hooks/on_notification.py:261-426`
- Permission hook: `.claude/hooks/on_permission_request.py:146-187`
- Debug logs: `~/.claude/slack/logs/notification_hook_debug.log`
- Experiment code: `experiments/buffer-parsing/`
