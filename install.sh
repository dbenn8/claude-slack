#!/bin/bash
#
# Claude-Slack Install Script
# Sets up launchd services and shell configuration
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES_DIR="$SCRIPT_DIR/templates"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
INSTALL_DIR="$HOME/.claude/claude-slack"
LOGS_DIR="$HOME/.claude/slack/logs"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Claude-Slack Installer${NC}"
echo "========================="
echo ""

# Pre-flight checks
echo -e "${BLUE}Running pre-flight checks...${NC}"

# Check Python 3.8+
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
if [ -z "$PYTHON_VERSION" ]; then
    echo -e "${RED}Error: Python 3 not found${NC}"
    exit 1
fi
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo -e "${RED}Error: Python 3.8+ required (found $PYTHON_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}  Python $PYTHON_VERSION${NC}"

# Check Claude Code is installed
if ! command -v claude &> /dev/null; then
    echo -e "${YELLOW}Warning: Claude Code CLI not found in PATH${NC}"
    echo -e "${YELLOW}  Install from: https://claude.ai/code${NC}"
fi

# Check for conflicting processes
EXISTING_LISTENER=$(pgrep -f "slack_listener.py" || true)
if [ -n "$EXISTING_LISTENER" ]; then
    echo -e "${YELLOW}Warning: Existing slack_listener process found (PID: $EXISTING_LISTENER)${NC}"
    echo -e "${YELLOW}  Will be replaced after installation${NC}"
fi

echo -e "${GREEN}  Pre-flight checks passed${NC}"
echo ""

# Step 1: Check for .env file
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo ""
    echo "Please create a .env file with your Slack tokens:"
    echo "  cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo "  # Then edit .env with your actual tokens"
    exit 1
fi

# Step 2: Source .env and validate required variables
echo -e "${BLUE}Reading configuration from .env...${NC}"
set -a
source "$SCRIPT_DIR/.env"
set +a

if [ -z "$SLACK_BOT_TOKEN" ] || [ "$SLACK_BOT_TOKEN" = "xoxb-your-bot-token-here" ]; then
    echo -e "${RED}Error: SLACK_BOT_TOKEN not configured in .env${NC}"
    exit 1
fi

if [ -z "$SLACK_APP_TOKEN" ] || [ "$SLACK_APP_TOKEN" = "xapp-your-app-token-here" ]; then
    echo -e "${RED}Error: SLACK_APP_TOKEN not configured in .env${NC}"
    exit 1
fi

SLACK_CHANNEL="${SLACK_CHANNEL:-#claude-code-sessions}"
echo -e "${GREEN}  SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN:0:20}...${NC}"
echo -e "${GREEN}  SLACK_APP_TOKEN: ${SLACK_APP_TOKEN:0:20}...${NC}"
echo -e "${GREEN}  SLACK_CHANNEL: $SLACK_CHANNEL${NC}"
echo ""

# Step 3: Create directories
echo -e "${BLUE}Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOGS_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"
echo -e "${GREEN}  Created $INSTALL_DIR${NC}"
echo -e "${GREEN}  Created $LOGS_DIR${NC}"
echo ""

# Step 4: Copy core files to install directory
echo -e "${BLUE}Installing core files...${NC}"
cp -r "$SCRIPT_DIR/core" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/bin" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/hooks" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/.env" "$INSTALL_DIR/"
echo -e "${GREEN}  Copied core/, bin/, hooks/, .env to $INSTALL_DIR${NC}"
echo ""

# Step 5: Set up Python virtual environment
echo -e "${BLUE}Setting up Python environment...${NC}"
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    python3 -m venv "$INSTALL_DIR/.venv"
    echo -e "${GREEN}  Created virtual environment${NC}"
fi
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo -e "${GREEN}  Installed dependencies${NC}"
echo ""

# Step 6: Generate plist files from templates
echo -e "${BLUE}Generating launchd configuration...${NC}"

# Listener plist
sed -e "s|{{HOME}}|$HOME|g" \
    -e "s|{{SLACK_BOT_TOKEN}}|$SLACK_BOT_TOKEN|g" \
    -e "s|{{SLACK_APP_TOKEN}}|$SLACK_APP_TOKEN|g" \
    -e "s|{{SLACK_CHANNEL}}|$SLACK_CHANNEL|g" \
    "$TEMPLATES_DIR/com.claude-slack.listener.plist.template" \
    > "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist"
chmod 600 "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist"
echo -e "${GREEN}  Generated com.claude-slack.listener.plist${NC}"

# Registry plist
sed -e "s|{{HOME}}|$HOME|g" \
    "$TEMPLATES_DIR/com.claude-slack.registry.plist.template" \
    > "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist"
chmod 600 "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist"
echo -e "${GREEN}  Generated com.claude-slack.registry.plist${NC}"
echo ""

# Step 7: Load launchd services
echo -e "${BLUE}Loading services...${NC}"

# Unload first if already loaded (ignore errors)
launchctl unload "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist" 2>/dev/null || true
launchctl unload "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist" 2>/dev/null || true

# Load services
launchctl load "$LAUNCH_AGENTS_DIR/com.claude-slack.registry.plist"
echo -e "${GREEN}  Loaded registry service${NC}"
sleep 1
launchctl load "$LAUNCH_AGENTS_DIR/com.claude-slack.listener.plist"
echo -e "${GREEN}  Loaded listener service${NC}"
echo ""

# Step 7.5: Configure Claude Code settings
echo -e "${BLUE}Configuring Claude Code settings...${NC}"
CLAUDE_SETTINGS="$HOME/.claude/settings.local.json"
TEMPLATE_SETTINGS="$INSTALL_DIR/hooks/settings.local.json.template"

if [ ! -f "$CLAUDE_SETTINGS" ]; then
    # Create Claude settings directory if needed
    mkdir -p "$HOME/.claude"
    # Copy template as-is (it uses $HOME which Claude Code expands)
    cp "$TEMPLATE_SETTINGS" "$CLAUDE_SETTINGS"
    echo -e "${GREEN}  Created $CLAUDE_SETTINGS${NC}"
else
    # Backup existing settings
    BACKUP="$CLAUDE_SETTINGS.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$CLAUDE_SETTINGS" "$BACKUP"
    echo -e "${YELLOW}  Backed up existing settings to $BACKUP${NC}"

    # Check if hooks already configured
    if grep -q "claude-slack" "$CLAUDE_SETTINGS" 2>/dev/null; then
        echo -e "${GREEN}  Claude Code settings already configured for claude-slack${NC}"
    else
        # Auto-merge hooks using jq
        if command -v jq &> /dev/null; then
            echo -e "${BLUE}  Merging claude-slack hooks into existing settings...${NC}"
            # Merge hooks from template into existing settings (template hooks take precedence)
            jq -s '.[0] * {hooks: (.[0].hooks // {}) * .[1].hooks}' \
                "$CLAUDE_SETTINGS" "$TEMPLATE_SETTINGS" > "$CLAUDE_SETTINGS.tmp" \
                && mv "$CLAUDE_SETTINGS.tmp" "$CLAUDE_SETTINGS"
            echo -e "${GREEN}  Merged hooks successfully${NC}"
        else
            echo -e "${YELLOW}  jq not found - manual merge required${NC}"
            echo -e "${YELLOW}  Install jq: brew install jq${NC}"
            echo -e "${YELLOW}  Then re-run installer, or manually merge:${NC}"
            echo -e "${YELLOW}    $TEMPLATE_SETTINGS into $CLAUDE_SETTINGS${NC}"
        fi
    fi
fi
echo ""

# Step 8: Configure shell
echo -e "${BLUE}Configuring shell...${NC}"
SHELL_CONFIG=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_CONFIG="$HOME/.bashrc"
fi

if [ -n "$SHELL_CONFIG" ]; then
    # Check if already configured
    if ! grep -q "claude-slack" "$SHELL_CONFIG"; then
        echo "" >> "$SHELL_CONFIG"
        echo "# Claude-Slack integration" >> "$SHELL_CONFIG"
        echo "export PATH=\"\$PATH:$SCRIPT_DIR/bin\"" >> "$SHELL_CONFIG"
        echo "alias claudes='claude-slack'" >> "$SHELL_CONFIG"
        echo -e "${GREEN}  Added PATH and alias to $SHELL_CONFIG${NC}"
        echo -e "${YELLOW}  Run 'source $SHELL_CONFIG' or open a new terminal${NC}"
    else
        echo -e "${GREEN}  Shell already configured${NC}"
    fi
else
    echo -e "${YELLOW}  No .zshrc or .bashrc found - manually add:${NC}"
    echo "    export PATH=\"\$PATH:$SCRIPT_DIR/bin\""
    echo "    alias claudes='claude-slack'"
fi
echo ""

# Step 9: Verify installation
echo -e "${BLUE}Verifying installation...${NC}"
sleep 2

LISTENER_PID=$(pgrep -f "slack_listener.py" || echo "")
REGISTRY_PID=$(pgrep -f "session_registry.py" || echo "")

if [ -n "$LISTENER_PID" ] && [ -n "$REGISTRY_PID" ]; then
    echo -e "${GREEN}  Listener running (PID: $LISTENER_PID)${NC}"
    echo -e "${GREEN}  Registry running (PID: $REGISTRY_PID)${NC}"
    echo ""
    echo -e "${GREEN}Installation complete!${NC}"
    echo ""
    echo "Usage:"
    echo "  claudes          - Start Claude with Slack integration"
    echo "  claudes resume   - Resume last session with Slack"
else
    echo -e "${YELLOW}  Services may still be starting...${NC}"
    echo "  Run 'claude-slack-diagnose' to check status"
fi
