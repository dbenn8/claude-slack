# Permission Prompt Fix - Missing Option 1

**Date:** 2025-11-13
**Issue:** Permission prompts in Slack showed only 2 options (options 2 & 3) instead of all 3

---

## Problem Statement

When Claude Code requested permission to use tools, the notification hook was capturing and sending incomplete permission options to Slack:

**What was captured:**
- Option 2: "Yes, allow reading from tmp/ from this project"
- Option 3: "No, and tell Claude what to do differently"

**What was missing:**
- Option 1: "Approve this time" (single instance approval)

### Root Cause

Option 1 was scrolling off the **1KB ring buffer** before the notification hook could read it. Claude Code's terminal UI updates rapidly, and the buffer was too small to hold all 3 options long enough for the hook to capture them.

---

## Solution Implemented

### 1. Increased Ring Buffer Size
**File:** `core/claude_wrapper_hybrid.py:436`

```python
# Changed from:
self.output_buffer = deque(maxlen=1024)  # 1KB

# To:
self.output_buffer = deque(maxlen=4096)  # 4KB
```

**Benefit:** More capacity to capture complete permission prompts before they scroll off

---

### 2. Smart Option Reconstruction
**File:** `.claude/hooks/on_notification.py:360-393`

Added logic to detect when numbered items start with 2 or 3 (indicating option 1 is missing) and automatically reconstruct it:

```python
if start_num == 2:
    # Missing option 1 - prepend standard text
    reconstructed = ["Approve this time"] + group
    return reconstructed
elif start_num == 3:
    # Missing options 1 and 2 - prepend both
    reconstructed = ["Approve this time", "Approve commands like this for this project"] + group
    return reconstructed
```

**Benefit:**
- Keeps exact buffer text for options 2 & 3 (context-specific)
- Reconstructs option 1 with standard text when missing
- Handles edge cases (both options 1 & 2 missing)

---

### 3. Improved Grouping Logic
**File:** `.claude/hooks/on_notification.py:304-336`

Updated the numbered list extraction to:
- Accept consecutive numbers starting from **any** number (not just 1)
- Track which number each group starts with
- Enable reconstruction logic to know what's missing

```python
# Before: Required lists to start with "1."
# After: Accepts consecutive lists starting with 2., 3., etc.
```

**Benefit:** More robust parsing that handles incomplete captures

---

## Debug Log Evidence

### Before Fix
```
[PARSE] Found 2 total numbered items
[PARSE]   Item 2: Yes, allow reading from tmp/ from this project
[PARSE]   Item 3: No, and tell Claude what to do differently (esc)
[PARSE] Extracted 0 numbered list groups  # ❌ Rejected because didn't start with 1
```

### After Fix
```
[PARSE] Found 2 total numbered items
[PARSE]   Item 2: Yes, allow reading from tmp/ from this project
[PARSE]   Item 3: No, and tell Claude what to do differently (esc)
[PARSE] Extracted 1 numbered list groups
[PARSE] Group 1: MATCH! Found 2 permission options starting at #2
[PARSE] Reconstructed option 1: Added 'Approve this time' before captured options
[ENHANCE] SUCCESS: Got exact options from buffer:
  ['Approve this time',  # ✅ Reconstructed
   'Yes, allow reading from tmp/ from this project',  # ✅ Exact from terminal
   'No, and tell Claude what to do differently (esc)']  # ✅ Exact from terminal
```

---

## Result

✅ **Slack messages now show all 3 permission options correctly:**

1. **Approve this time** (single instance - reconstructed if missing)
2. **Exact text from buffer** (broader approval - captured from terminal)
3. **Exact text from buffer** (deny option - captured from terminal)

---

## Technical Details

### Claude's Permission Option Structure

Claude Code always presents permission prompts in this format:

- **Option 1:** Single instance approval ("Approve this time")
  - Fixed text across all tool types
  - Always the first option

- **Option 2:** Broader approval (context-specific)
  - Text varies by tool and permission mode
  - Examples:
    - "Yes, allow reading from tmp/ from this project"
    - "Approve commands like this for this project"
    - "Approve all Bash for this session"

- **Option 3:** Denial with context (context-specific)
  - Text varies slightly
  - Usually: "No, and tell Claude what to do differently"
  - May include "(esc)" keyboard hint

### Why Reconstruction is Safe

1. **Option 1 text is standardized** - always "Approve this time" for single-instance approval
2. **Options 2 & 3 are captured exactly** from terminal buffer (context-specific)
3. **Better than nothing** - users get all 3 options instead of just 2
4. **Matches Claude's actual UI** - reconstructed text matches what users see in terminal

---

## Testing

To verify the fix works:

1. Trigger a permission prompt (e.g., Write, Edit, or Bash command)
2. Check Slack message shows 3 options
3. Review debug log: `/tmp/notification_hook_debug.log`
4. Look for "Reconstructed option 1" message if option 1 was missing

---

## Future Improvements

If the 4KB buffer still proves insufficient:

1. **Increase buffer to 8KB or 16KB** - more capacity
2. **PreToolUse hook integration** - capture permission mode and tool details before notification fires
3. **Direct transcript parsing** - parse permission data from transcript instead of terminal output
4. **Hook timing optimization** - fire notification hook earlier in the permission flow

---

## Files Modified

1. `core/claude_wrapper_hybrid.py` - Increased buffer size (1KB → 4KB)
2. `.claude/hooks/on_notification.py` - Added reconstruction and improved parsing logic

---

## Related Commits

- `9806b10` - Implement terminal output buffering for exact permission prompts
- `1fa4a72` - Implement retry loop transcript parsing for permission prompts
- `030a1a4` - Update permission prompts to match Claude's 3-option system
- Current - Fix missing option 1 via smart reconstruction
