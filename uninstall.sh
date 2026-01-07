#!/bin/bash
#
# Claude-Slack Uninstall Script
# Removes launchd services and cleans up installation
#

set -e

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
INSTALL_DIR="$HOME/.claude/claude-slack"
DATA_DIR="$HOME/.claude/slack"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Claude-Slack Uninstaller${NC}"
echo "==========================="
echo ""

# Step 1: Stop and unload services
echo -e "${BLUE}Stopping services...${NC}"
if [ -f "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist" ]; then
    launchctl unload "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist" 2>/dev/null || true
    rm "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist"
    echo -e "${GREEN}  Removed listener service${NC}"
fi

if [ -f "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist" ]; then
    launchctl unload "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist" 2>/dev/null || true
    rm "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist"
    echo -e "${GREEN}  Removed registry service${NC}"
fi
echo ""

# Step 2: Kill any remaining processes
echo -e "${BLUE}Cleaning up processes...${NC}"
pkill -f "slack_listener.py" 2>/dev/null || true
pkill -f "session_registry.py" 2>/dev/null || true
echo -e "${GREEN}  Stopped any remaining processes${NC}"
echo ""

# Step 2.5: Clean up legacy /tmp locations
if [ -d "/tmp/claude_sessions" ] || [ -d "/tmp/claude_socks" ]; then
    echo -e "${BLUE}Cleaning up legacy temp files...${NC}"
    rm -rf /tmp/claude_sessions 2>/dev/null || true
    rm -rf /tmp/claude_socks 2>/dev/null || true
    echo -e "${GREEN}  Removed legacy /tmp directories${NC}"
    echo ""
fi

# Step 3: Remove installed files (optional)
echo -e "${YELLOW}Remove installed files in $INSTALL_DIR? [y/N]${NC}"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo -e "${GREEN}  Removed $INSTALL_DIR${NC}"
fi

echo -e "${YELLOW}Remove data (logs, session history) in $DATA_DIR? [y/N]${NC}"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    rm -rf "$DATA_DIR"
    echo -e "${GREEN}  Removed $DATA_DIR${NC}"
fi
echo ""

# Step 4: Note about shell config
echo -e "${YELLOW}Note: Shell configuration was not removed.${NC}"
echo "To remove manually, edit ~/.zshrc or ~/.bashrc and remove:"
echo "  - The 'Claude-Slack integration' section"
echo "  - The PATH and alias lines for claude-slack"
echo ""

echo -e "${GREEN}Uninstall complete!${NC}"
