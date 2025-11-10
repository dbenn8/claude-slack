#!/bin/bash
# Check if stop hook is configured and working

# Auto-detect installation directory
INTEGRATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Get project directory from environment or detect from current location
# If installed in project/.claude/claude-slack, project is 2 levels up
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$INTEGRATION_DIR/../.." && pwd)}"

echo "=== Stop Hook Status Check ==="
echo "Integration directory: $INTEGRATION_DIR"
echo "Project directory: $PROJECT_DIR"
echo

echo "1. Hook file exists and is executable:"
HOOK_PATH="$INTEGRATION_DIR/hooks/on_stop.py"
if [ -x "$HOOK_PATH" ]; then
    echo "   ✓ Hook exists and is executable"
    ls -lh "$HOOK_PATH"
else
    echo "   ✗ Hook missing or not executable"
    ls -lh "$HOOK_PATH" 2>/dev/null || echo "   File not found"
fi
echo

echo "2. Settings configuration:"
SETTINGS_PATH="$PROJECT_DIR/.claude/settings.local.json"
if [ -f "$SETTINGS_PATH" ]; then
    echo "   ✓ settings.local.json exists"
    echo "   Stop hook configuration:"
    jq '.hooks.Stop' "$SETTINGS_PATH" 2>/dev/null || echo "   (Could not parse JSON)"
else
    echo "   ✗ settings.local.json not found"
fi
echo

echo "3. Debug log file:"
DEBUG_LOG="${STOP_HOOK_DEBUG_LOG:-/tmp/stop_hook_debug.log}"
if [ -f "$DEBUG_LOG" ]; then
    echo "   ✓ Debug log exists"
    echo "   File size: $(wc -c < "$DEBUG_LOG") bytes"
    echo "   Last modified: $(stat -f "%Sm" "$DEBUG_LOG")"
    echo
    echo "   Last 5 entries:"
    tail -5 "$DEBUG_LOG" | sed 's/^/   /'
else
    echo "   ✗ Debug log not found (hook has never run)"
fi
echo

echo "4. Watch debug log in real-time:"
echo "   tail -f $DEBUG_LOG"
echo

echo "5. Test hook manually:"
echo "   cd $PROJECT_DIR"
echo "   python3 $INTEGRATION_DIR/hooks/on_stop.py < test_input.json"
echo

echo "=== Troubleshooting ==="
echo "If hook is not firing automatically:"
echo "1. Check that Claude Code is running in this project directory"
echo "2. Verify settings.local.json has correct hook path"
echo "3. Watch debug log: tail -f $DEBUG_LOG"
echo "4. Check if hook is being called but failing (check log for errors)"
echo "5. Verify core modules are in: $INTEGRATION_DIR/core"
echo
