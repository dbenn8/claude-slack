# PreToolUse Hook - Slack Message Examples

This document shows examples of the rich, detailed Slack messages you'll receive when Claude requests permission to use different tools.

## Before PreToolUse Hook (OLD)

**Message:**
```
Claude needs your permission to use Bash
```

**Problem:** You don't know WHAT bash command Claude wants to run. Could be harmless (`ls`) or dangerous (`rm -rf /`). No way to make an informed decision remotely.

---

## After PreToolUse Hook (NEW) ✨

### Example 1: Bash Command (3 Options - Standard)

**Message:**
```
⚠️ **Permission Required: Bash**

**Command:**
```bash
ls -la hooks/
```

**Description:** List files in hooks directory
**Directory:** `/Users/danielbennett/project`

**Expected Options (check terminal for exact choices):**
1️⃣ Allow this time
2️⃣ Allow always (for this tool/pattern)
3️⃣ Deny

⚠️ Inferred choices - actual options may vary

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Value:** You can see:
- Exact command being executed
- What it does (description)
- Where it's running (directory)
- **How many options you have (2 or 3)**
- **What each option means**
- Make informed security decision

---

### Example 1b: Dangerous Bash Command (2 Options Only)

**Message:**
```
⚠️ **Permission Required: Bash**

**Command:**
```bash
rm -rf /tmp/old_logs
```

**Description:** Clean up old log files from temporary directory
**Directory:** `/Users/danielbennett/project`

**Expected Options (check terminal for exact choices):**
1️⃣ Allow
2️⃣ Deny

⚠️ Inferred choices - actual options may vary

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Key Difference:** Dangerous commands (rm -rf, sudo, etc.) typically show **only 2 options** (Allow/Deny) with **no "Allow always" option** for safety.

---

### Example 2: File Write

**Message:**
```
⚠️ **Permission Required: Write**

**File:** `/Users/danielbennett/project/config.json`
**Content Preview:**
```
{
  "api_key": "sk-test-1234567890",
  "endpoint": "https://api.example.com",
  "timeout": 30000
}
```
**Size:** 145 characters

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Value:** You can see:
- What file is being created/overwritten
- Preview of the content being written
- Total size of the write operation

---

### Example 3: File Edit

**Message:**
```
⚠️ **Permission Required: Edit**

**File:** `/Users/danielbennett/project/package.json`
**Replace:** First occurrence

**Old:**
```
"version": "1.0.0",
```

**New:**
```
"version": "1.1.0",
```

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Value:** You can see:
- Which file is being edited
- What text is being replaced
- What the new text will be
- Whether it's replacing one or all occurrences

---

### Example 4: File Read

**Message:**
```
⚠️ **Permission Required: Read**

**File:** `/Users/danielbennett/project/.env`
**Action:** Read entire file

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Value:** You can see:
- Which file Claude wants to read
- Important for sensitive files (.env, credentials, etc.)

---

### Example 5: WebFetch

**Message:**
```
⚠️ **Permission Required: WebFetch**

**URL:** https://api.github.com/repos/user/repo/issues
**Query:** Get all open issues with their titles and descriptions

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Value:** You can see:
- What external URL Claude is accessing
- What data it's trying to fetch
- Privacy/security implications

---

### Example 6: Grep Search

**Message:**
```
⚠️ **Permission Required: Grep**

**Search Pattern:** `password|api_key|secret`
**Search Path:** `/Users/danielbennett/project`
**Output Mode:** files_with_matches

---
_Approve/deny this action in your Claude terminal._
_This notification helps you make informed decisions remotely._
```

**Value:** You can see:
- What pattern is being searched for
- Where the search is happening
- What kind of output will be returned

---

## Permission Choice Detection

The hook uses **multiple strategies** to show you permission choices:

### 1. **Permission Mode Inference** (Always Available)
- Analyzes the `permission_mode` field (default, plan, acceptEdits, bypassPermissions)
- Checks if the operation is dangerous (rm -rf, sudo, etc.)
- Infers likely choices based on Claude Code's permission system
- Shows **expected choices** with a disclaimer

### 2. **Transcript Parsing** (Best Effort)
- Attempts to read the transcript file after a brief delay
- Looks for actual permission prompt data
- If found, shows **verified choices** from terminal
- Falls back to inferred choices if transcript doesn't have data yet

### 3. **Smart Dangerous Command Detection**
Automatically detects dangerous patterns and adjusts expected choices:
- `rm -rf`, `sudo`, `chmod 777`, `mkfs`, `> /dev/`, `dd if=`
- Shows 2 options (Allow/Deny) instead of 3
- Helps you understand when Claude Code is being extra cautious

## Security Benefits

With the PreToolUse hook, you now have:

1. **Full Transparency** - See exactly what Claude wants to do before approving
2. **Remote Security** - Make informed decisions from your phone via Slack
3. **Audit Trail** - All permission requests logged in Slack thread
4. **Context Awareness** - Understand the "why" and "what" before the "yes"
5. **Choice Clarity** - Know whether you have 2 or 3 options (critical difference!)
6. **Option Meaning** - See what option 2 actually means (Allow always vs Deny)

## Implementation Details

The PreToolUse hook:
- Fires **before** Claude executes any tool
- Receives complete tool parameters from Claude Code
- Formats tool-specific messages (each tool type has custom formatting)
- Posts to your existing Slack thread
- Never blocks execution (exits with code 0)
- Works alongside existing Notification and Stop hooks

## Supported Tools

The hook provides rich formatting for:
- ✅ Bash (command, description, directory)
- ✅ Write (file path, content preview, size)
- ✅ Edit (file path, old/new text, replace mode)
- ✅ Read (file path, line range)
- ✅ Glob (pattern, search path)
- ✅ Grep (pattern, path, output mode)
- ✅ WebFetch (URL, query)
- ✅ WebSearch (query)
- ✅ Generic fallback for any other tool

## Next Steps

1. The hook is already installed at `hooks/on_pretooluse.py`
2. Configuration added to `hooks/settings.local.json.template`
3. Next time you run `claude-slack`, the PreToolUse hook will be active
4. You'll start receiving detailed permission requests in Slack immediately

## Future Enhancements

Potential future features:
- **Slack Reaction Approval** - React with ✅ or ❌ to approve/deny from Slack
- **Auto-block Patterns** - Define dangerous command patterns to auto-deny
- **Usage Analytics** - Track which tools are used most frequently
- **Custom Formatting** - User-defined message templates per tool
