# VibeTunnel No-PTY Mode

## The Problem

**Before**: VibeTunnel + claude-slack created nested PTYs causing visual artifacts
```
VibeTunnel PTY (outer)
    └→ claude_wrapper_hybrid.py creates PTY (inner) ← NESTED PTY!
        └→ Claude Code
```

**Result**: Pulsing status updates, blank lines, dashes everywhere

## Root Cause

VibeTunnel already provides a PTY for terminal emulation in the browser. When claude-slack wrapper creates another PTY on top of that:
- Double terminal processing
- CR/NL handling conflicts
- ANSI escape sequence interference
- Alternate screen buffer incompatibility

## The Solution

**New Architecture** (vt-no-pty branch):
```
VibeTunnel PTY (only PTY)
    ├→ Claude Code (inherits VibeTunnel's stdin/stdout/stderr)
    └→ wrapper monitors and injects Slack input via TIOCSTI
```

**Key Changes**:
1. Detect VibeTunnel via `VIBETUNNEL_SESSION_ID` env var
2. Skip `pty.fork()` - just use regular `os.fork()` + `os.execvp()`
3. Claude inherits VibeTunnel's terminal file descriptors
4. Slack input injected via `fcntl.ioctl(sys.stdin, termios.TIOCSTI, byte)`

## Files Modified

### `core/claude_wrapper_hybrid.py`
- Added VibeTunnel detection at start of `run()` method (line 1060)
- Routes to `run_vibetunnel_mode()` if VibeTunnel detected
- Updated `socket_listener()` to use queue for VibeTunnel mode (line 735)

### `core/claude_wrapper_vibetunnel.py` (NEW)
- VibeTunnel-specific execution path
- No PTY creation - just fork/exec
- Uses `TIOCSTI` ioctl to inject Slack input into terminal buffer
- Claude reads input as if user typed it

## How It Works

1. **Startup**: Wrapper detects `VIBETUNNEL_SESSION_ID` environment variable
2. **Fork**: Simple `os.fork()` without creating PTY
3. **Child**: Exec Claude directly - it inherits VibeTunnel's stdin/stdout/stderr
4. **Parent**: Monitors Slack input queue
5. **Slack Input**: When message arrives, use `TIOCSTI` to inject bytes into terminal
6. **Claude Reads**: Claude sees injected input as normal terminal input
7. **Output**: Claude writes directly to VibeTunnel's PTY (no interference)

## Benefits

✅ **No nested PTY** - VibeTunnel's PTY is the only one
✅ **Clean output** - No double-processing of ANSI codes
✅ **Proper CR/NL handling** - VibeTunnel's xterm.js handles it natively
✅ **Slack input still works** - TIOCSTI injects into terminal buffer
✅ **Hooks still work** - Environment variables and paths unchanged

## Testing

```bash
# In VibeTunnel terminal:
cd /Users/danielbennett/codeNew/.claude/claude-slack
git checkout vt-no-pty
./claude-slack

# Should see in banner:
# "VibeTunnel Mode: Direct execution (no nested PTY)"

# Test:
# 1. Type in VibeTunnel web terminal - should work normally
# 2. Send message from Slack - should appear and execute
# 3. Look for artifacts - should be GONE!
```

## Fallback

Standard terminals (non-VibeTunnel) still use the original PTY-based approach - no changes to existing behavior.

## Next Steps

1. Test in VibeTunnel - verify no artifacts
2. Test Slack input injection - verify it works
3. Test hooks - verify permission prompts work
4. If successful, merge to main and update default behavior
