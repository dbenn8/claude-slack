# Hooks Installation Fix - Missing on_pretooluse.py

**Date:** 2025-11-13
**Issue:** Running claude-slack in a new project failed with error about missing `.claude/hooks/on_pretooluse.py`

---

## Problem Statement

When running `claude-slack` in a new project, users encountered this error:

```
I'm encountering a hook configuration issue that's preventing Git commands from running.
There's a pre-tool-use hook configured that's trying to execute a Python script at
.claude/hooks/on_pretooluse.py which doesn't exist.
```

### Root Cause

The `bin/claude-slack` script was only copying 2 out of 3 required hook files to the project's `.claude/hooks/` directory:

✅ Installed:
- `on_stop.py`
- `on_notification.py`

❌ Missing:
- `on_pretooluse.py`

---

## Solution

Updated `bin/claude-slack` at lines 73-77 to include all 3 hooks:

**Before:**
```bash
# Step 2b: Install hook files
HOOKS_TO_INSTALL=(
    "on_stop.py"
    "on_notification.py"
)
```

**After:**
```bash
# Step 2b: Install hook files
HOOKS_TO_INSTALL=(
    "on_stop.py"
    "on_notification.py"
    "on_pretooluse.py"
)
```

---

## How It Works

### Hook Installation Process

When you run `claude-slack` in a project directory:

1. **Create directories** (if needed):
   - `$PROJECT_DIR/.claude/`
   - `$PROJECT_DIR/.claude/hooks/`

2. **Copy settings template**:
   - From: `hooks/settings.local.json.template`
   - To: `$PROJECT_DIR/.claude/settings.local.json`

3. **Copy hook files**:
   - From: `hooks/on_*.py` (templates)
   - To: `$PROJECT_DIR/.claude/hooks/on_*.py`
   - Make executable: `chmod +x`

### Hook Discovery

The hooks don't need core files copied to the project because they use **dynamic discovery**:

```python
def find_claude_slack_dir():
    """Find claude-slack directory using standard discovery patterns."""
    # 1. Check $CLAUDE_SLACK_DIR environment variable
    # 2. Search upward from current directory for .claude/claude-slack/
    # 3. Fall back to ~/.claude/claude-slack/
```

This allows hooks in any project to find and import:
- `registry_db.py`
- `transcript_parser.py`
- `config.py`
- Other core modules

---

## Three Hook Files Explained

### 1. on_stop.py (Stop Hook)
**Triggered:** When Claude Code session ends

**Purpose:**
- Clean up session data
- Update registry database
- Post session summary to Slack

---

### 2. on_notification.py (Notification Hook)
**Triggered:** When Claude sends notifications (permission prompts, idle messages)

**Purpose:**
- Extract permission prompt details
- Parse terminal output buffer for exact option text
- Reconstruct missing options if needed
- Post notification to Slack thread

**Recent fix:** Now captures all 3 permission options (see PERMISSION_PROMPT_FIX.md)

---

### 3. on_pretooluse.py (PreToolUse Hook)
**Triggered:** Before Claude executes certain tools (e.g., AskUserQuestion)

**Purpose:**
- Capture tool details before execution
- Provide additional context for hooks
- Track permission mode (default, acceptEdits, plan)

**Why it was missing:** Simply forgotten in the HOOKS_TO_INSTALL array

---

## Files Modified

- `bin/claude-slack` - Added `on_pretooluse.py` to HOOKS_TO_INSTALL array

---

## Testing

To verify the fix works:

1. Navigate to a new project directory
2. Run `claude-slack`
3. Verify all 3 hooks are installed:
   ```bash
   ls -la .claude/hooks/
   # Should show: on_stop.py, on_notification.py, on_pretooluse.py
   ```
4. Run git commands - should not get hook errors

---

## Prevention

All future hooks should be:
1. Created as templates in `hooks/` directory
2. Added to `HOOKS_TO_INSTALL` array in `bin/claude-slack`
3. Made executable (`chmod +x`)
4. Include `find_claude_slack_dir()` for core module discovery

---

## Related Files

- `bin/claude-slack` - Main launcher script (installs hooks)
- `hooks/on_stop.py` - Template for stop hook
- `hooks/on_notification.py` - Template for notification hook
- `hooks/on_pretooluse.py` - Template for pre-tool-use hook
- `hooks/settings.local.json.template` - Settings template (references all hooks)
