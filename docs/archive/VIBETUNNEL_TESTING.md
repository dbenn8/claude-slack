# VibeTunnel Terminal Mode Testing

The wrapper supports multiple terminal modes for VibeTunnel compatibility.

## 🎯 Recommended Mode for VibeTunnel: Mode 4

**Mode 4 (Pass-through)** is specifically designed to fix terminal artifacts in VibeTunnel by:
- Disabling alternate screen buffer (prevents nested PTY interference)
- Removing all CR/NL mapping (lets xterm.js handle output natively)
- Using minimal terminal intervention

This addresses the root cause: the wrapper was adding a second PTY layer on top of VibeTunnel's existing PTY, causing double terminal state management and artifacts.

## What Changed in vt-mode-kimi Branch

### Solution 1: Alternate Screen Disabled for VibeTunnel
- The wrapper now skips alternate screen buffer commands when running in VibeTunnel
- Prevents nested PTY state management conflicts
- Both `enter_alternate_screen()` and `exit_alternate_screen()` check `is_vibetunnel()` and return early

### Solution 2: New Mode 4 (Pass-through)
- Completely new terminal mode designed for web terminals
- Removes ALL CR/NL mapping (ONLCR, ICRNL, INLCR flags cleared)
- Uses raw mode for responsive input, but lets xterm.js handle output formatting
- Default fallback for unknown modes changed from Mode 1 to Mode 4

### Key Technical Changes
These fixes apply **ONLY** when `VIBETUNNEL_COOKIE` environment variable is set (VibeTunnel scenarios only):

1. **Alternate Screen Buffer:** Skipped entirely in VibeTunnel
2. **CR/NL Mapping (Mode 4):** Explicitly removed instead of added
3. **Terminal State:** Minimal intervention - just raw input mode and no echo

## Quick Testing Guide

### Method 1: Just tell Claude which mode to test

Simply say: **"Claude, set VibeTunnel mode to 2"** (or 1 or 3)

I'll update the environment variable for you. Then you just need to restart the wrapper to test.

### Method 2: Set environment variable manually

```bash
# Set the mode (1, 2, or 3)
export VIBETUNNEL_MODE=2

# Then restart your wrapper session
# (exit current session and start new one)
```

## Available Modes

### Mode 4: Pass-through (⭐ RECOMMENDED for VibeTunnel)
**Environment:** `VIBETUNNEL_MODE=4`

- **Purpose:** Fix terminal artifacts by minimizing wrapper intervention
- Uses raw mode for responsive input
- **NO CR/NL mapping** - lets VibeTunnel's xterm.js handle all output
- **NO alternate screen buffer** - prevents nested PTY conflicts
- **Best for:** VibeTunnel and other web-based terminals
- **Fixes:** Pulsing status duplicates, blank lines, visual artifacts
- **Test:** Should see clean output, no duplicate lines from status updates

### Mode 1: Raw + CR/NL Mapping (Legacy)
**Environment:** `VIBETUNNEL_MODE=1` (default if not specified)

- Uses raw mode (character-by-character input)
- Adds CR/NL mapping for web terminal compatibility
- **Note:** This mode was an attempt to fix artifacts but actually made them worse
- **Best for:** Testing/debugging only - not recommended for normal use
- **Test:** Type characters - should appear immediately

### Mode 2: Canonical Mode (Legacy)
**Environment:** `VIBETUNNEL_MODE=2`

- Uses line-buffered input
- Minimal line editing features
- **Best for:** Testing alternative input handling
- **Test:** Type a line and press Enter - should send full line

### Mode 3: Default State (Legacy)
**Environment:** `VIBETUNNEL_MODE=3`

- No terminal mode changes (except echo disabled)
- Uses whatever VibeTunnel sets by default
- **Best for:** Testing minimal configuration
- **Test:** Most hands-off approach

### Mode 0: Ignore VibeTunnel
**Environment:** `VIBETUNNEL_MODE=0`

- Treats VibeTunnel like a standard terminal
- Ignores VibeTunnel detection completely
- **Best for:** Baseline comparison testing

## Testing Checklist

For each mode, test:
- [ ] Typing characters appears correctly
- [ ] Pressing Enter sends input
- [ ] No echo duplication
- [ ] No extra newlines
- [ ] Claude responds to input
- [ ] Interactive menus work (if applicable)
- [ ] Permission prompts work
- [ ] Can scroll terminal history

## Current Status

Check current mode with:
```bash
echo "Current mode: ${VIBETUNNEL_MODE:-1}"
```

Or look at the wrapper startup banner - it shows the mode being used.
