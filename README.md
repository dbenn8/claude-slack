# Claude-Slack Integration

Slack integration for Claude Code sessions - enables bidirectional communication between Claude terminal sessions and Slack.  I've found vibetunnel + tailscale super helpful for using claude-code on the go, but have found the UI lacking.  Especially as sessions get longer, VT can get bogged down and difficult to use.  Slack has the benefits of notifying the user when claude-code finishes generating a response and also a much better UI for consuming and generating responses while on the go (STT especially!).  

## Overview

This integration allows Claude Code sessions to:
- Send a claude-code session specific message to a slack channel to seed a new slack thread.
- Receive,act on, and respond to messages added to the session specific thread
- Support multiple concurrent Claude sessions across different projects (as separate slack threads)
- Maintain conversation history and context

## Architecture

This installation can serve all Claude projects on your machine:
- Single installation at `~/.claude/claude-slack`
- One Slack bot (socket mode enabled) serves all projects
- Central session registry tracks active sessions
- Hook templates are copied to each project that needs Slack integration
- **WARNING**: This hasn't been tested for scenarios where on_stop and/or on_notification hooks already exist for your slack project.  They MIGHT OVERWRITE YOUR EXISTING HOOK FILES (SO BACK THEM UP IN ADVANCE), or more likely, you might need to manually copy the relevant content from the hook templates into your existing hooks if you have them. 

## Quick Start

### 1. Prerequisites

- Python 3.8+
- Slack workspace with admin access to create apps
- Claude Code installed

### 2. Create Slack App

1. Go to https://api.slack.com/apps and click "Create New App"
2. Choose "From an app manifest"
3. Select your workspace
4. Paste this manifest:

```yaml
display_information:
  name: Claude Code Bot
  description: Bidirectional communication with Claude Code sessions
  background_color: "#000000"
features:
  bot_user:
    display_name: Claude Code Bot
    always_online: true
oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - reactions:read
      - reactions:write
      - users:read
      - groups:history
      - groups:read
      - im:history
      - im:read
      - mpim:history
      - mpim:read
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - message.im
      - message.mpim
  interactivity:
    is_enabled: false
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

5. Click "Create"
6. Go to "OAuth & Permissions" and install the app to your workspace
7. Copy the "Bot User OAuth Token" (starts with `xoxb-`)
8. Go to "Basic Information" > "App-Level Tokens"
9. Click "Generate Token and Scopes"
10. Name: "Socket Mode Token", add scope: `connections:write`
11. Copy the token (starts with `xapp-`)

### 3. Installation

```bash
# Clone this repository
git clone https://github.com/YOUR_USERNAME/claude-claude-slack.git ~/.claude/claude-slack

# Navigate to the directory
cd ~/.claude/claude-slack

# Copy environment template
cp .env.example .env

# Edit .env with your tokens
nano .env  # or use your preferred editor
```

Add your tokens to `.env`:
```bash
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here
SLACK_CHANNEL=#your-channel-name
```

### 4. Add to PATH (optional but recommended)

```bash
echo 'export PATH="$HOME/.claude/claude-slack/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### 5. Test the Installation

```bash
# Start the Slack listener
claude-slack-listener

# In another terminal, test sending a message
claude-slack-test
```

## Usage

### Starting a New Claude Session with Slack

```bash
# Navigate to your project
cd /path/to/your/project

# Initialize Slack integration for this project
claude-slack

# You should receive a new message in the slack channel you added to your .env file
# You can reply 'as a thread' to the message to communicate with the claude session that sent the initial message
# If your reply doesn't automatically get a green checkmark emoji applied to it, you need to @mention your claud bot to wake it back up and try your message again.
# Claude code should receive your message as terminal input, generate it's response, and send it back to slack automatically.  You can continue the conversation as needed.

```


## Available Commands

After adding `~/.claude/claude-slack/bin` to your PATH:

- `claude-slack` - Initialize Slack for current project
- `claude-slack-listener` - Start the Slack listener daemon
- `claude-slack-test` - Test Slack connection
- `claude-slack-ensure` - Ensure listener is running
- `claude-slack-sessions` - List active sessions
- `claude-slack-cleanup` - Clean up stale sessions

## Troubleshooting

### Socket Starvation Issue

**Symptom**: Message sent to Slack but no green checkmark appears, Claude doesn't respond

**Root Cause**: Socket communication starvation - the connection between Slack listener and Claude session becomes unresponsive

**Workaround**:
- Send an @ mention to your bot.  Make the @ mention the FIRST word in your message (e.g., `@claudebot your message here`)
- The @ mention "wakes up" the listener and re-establishes communication
- After the @ mention, regular messages should work again
- Occasionally, its flakey enough that you need to copy paste your message and send it again to wake it up.

**Long-term solution**: Under investigation

### Checking Logs

```bash
# Check listener logs
tail -f /tmp/slack_listener.log

# Check hook execution logs
tail -f /tmp/stop_hook_debug.log

# Check session registry
sqlite3 /tmp/claude_sessions/registry.db "SELECT * FROM sessions;"
```

### Common Issues

1. **No response from Claude**:
   - Check if listener is running: `ps aux | grep slack_listener`
   - Try @ mentioning the bot to wake it up
   - Check logs for errors

2. **Duplicate messages**:
   - Multiple listeners may be running
   - Run `claude-slack-cleanup` to clean up

3. **Session not found**:
   - Session may have expired (24 hour timeout)
   - Check registry: `claude-slack-sessions`

4. **Permission denied**:
   - Ensure scripts are executable: `chmod +x ~/.claude/claude-slack/bin/*`

## Project Structure

```
~/.claude/claude-slack/
├── core/                 # Core Python modules
│   ├── slack_listener.py      # Main Slack event listener
│   ├── session_registry.py    # Session management
│   ├── claude_wrapper_multi.py # Multi-session Claude wrapper
│   ├── transcript_parser.py   # Parse Claude transcripts
│   └── config.py              # Configuration management
├── hooks/                # Claude Code hook templates
│   ├── on_pretooluse.py      # Permission requests with full context (NEW!)
│   ├── on_stop.py            # Response completion hook
│   ├── on_notification.py    # User notification hook
│   └── settings.local.json.template
├── bin/                  # Executable scripts
│   ├── claude-slack          # Project initialization
│   ├── claude-slack-listener # Start listener daemon
│   └── ...
├── .env.example          # Environment template
└── README.md            # This file
```

## Security

- **NEVER** commit `.env` file to git
- Slack tokens are sensitive - rotate immediately if exposed
- Use `.gitignore` to exclude sensitive files
- See SECURITY.md for detailed security practices

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Test thoroughly
4. Submit a pull request

## Hooks Explained

This integration uses three Claude Code hooks:

### 1. **PreToolUse Hook** (on_pretooluse.py) - NEW! ✨
- **Fires:** Before Claude executes any tool (Bash, Write, Edit, Read, etc.)
- **Purpose:** Sends detailed permission requests to Slack with FULL context
- **What you see:**
  - Actual bash commands before execution
  - File paths being written/edited/read
  - Search patterns and parameters
  - Everything Claude wants to do, before it happens
- **Why it's important:** Allows you to make informed security decisions remotely

### 2. **Notification Hook** (on_notification.py)
- **Fires:** When Claude sends generic notifications (idle prompts, auth messages)
- **Purpose:** Keeps you informed about Claude's status
- **Note:** This hook has limited context by design (generic alerts only)

### 3. **Stop Hook** (on_stop.py)
- **Fires:** When Claude finishes generating a response
- **Purpose:** Sends complete responses to Slack thread
- **What you see:** Full AI responses with code, explanations, and context

## Known Limitations

- Socket starvation issue requires @ mention workaround
- ~~Notifications from Claude aren't printing full content~~ **SOLVED!** ✅
  - PreToolUse hook now provides complete context for all permission requests
  - See actual bash commands, file contents, and tool parameters before approving

## License

MIT License - see LICENSE file for details

## Support

- Report issues: [GitHub Issues](https://github.com/YOUR_USERNAME/claude-claude-slack/issues)
- Slack API docs: https://api.slack.com
- Claude Code docs: https://claude.ai

## Credits

Created for use with Anthropic's Claude Code CLI.
