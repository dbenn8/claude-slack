# Claude-Slack Integration

Slack integration for Claude Code sessions - enables bidirectional communication between Claude terminal sessions and Slack.  I've found vibetunnel + tailscale super helpful for using claude-code on the go, but have found the UI lacking.  Especially as sessions get longer, VT can get bogged down and difficult to use.  Slack has the benefits of notifying the user when claude-code finishes generating a response and also a much better UI for consuming and generating responses while on the go (STT especially!).  

## Overview

This integration allows Claude Code sessions to:
- Send a claude-code session specific message to a slack channel to seed a new slack thread.
- Receive act on and respond to messages added to the thread to the original message
- Support multiple concurrent Claude sessions across different projects
- Maintain conversation history and context

## Architecture

This is a **UNIVERSAL** installation that serves all Claude projects on your machine:
- Single installation at `~/.claude/claude-slack`
- One Slack bot (socket mode enabled) serves all projects
- Central session registry tracks active sessions
- Hook templates are copied to each project that needs Slack integration

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
- Send an @ mention to your bot (e.g., `@claudebot your message here`)
- The @ mention "wakes up" the listener and re-establishes communication
- After the @ mention, regular messages should work again

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

## Known Limitations

- Socket starvation issue requires @ mention workaround
- Notifications from Claude (questions to the user with single digit answer shortcuts) aren't printing full content, only a single line.  We hope to tackle this soon.

## License

MIT License - see LICENSE file for details

## Support

- Report issues: [GitHub Issues](https://github.com/YOUR_USERNAME/claude-claude-slack/issues)
- Slack API docs: https://api.slack.com
- Claude Code docs: https://claude.ai

## Credits

Created for use with Anthropic's Claude Code CLI.
