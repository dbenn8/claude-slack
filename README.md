# Slack Integration for Claude Code

Bidirectional Slack integration allowing you to:
- **Claude → Slack**: Automatic notifications when Claude Code events occur
- **Slack → Claude**: Send responses from mobile and use them in Claude Code

## 📌 Choose Your Phase

- **Phase 1 (This Guide)**: Manual `/check` command - Simple, stable, works immediately
- **Phase 2 (Auto-Injection)**: Fully hands-free - See [PHASE2_README.md](PHASE2_README.md)

## Architecture

```
┌──────────────────┐        Hooks          ┌───────────────┐
│  Claude Code     │────────────────────────>│     Slack     │
│                  │    (auto-notify)        │   Workspace   │
│                  │                         │               │
│                  │<────────────────────────│               │
│                  │  /check (manual read)   └───────────────┘
└──────────────────┘                                │
                                                    │ WebSocket
                                                    │ (Socket Mode)
                                                    ▼
                                         ┌─────────────────────┐
                                         │  slack_listener.py  │
                                         │  (runs separately)  │
                                         │  Saves to:          │
                                         │  slack_response.txt │
                                         └─────────────────────┘
```

## Features

### MVP (Phase 1) - Implemented ✅
- ✅ Claude Code automatically sends messages to Slack on key events
- ✅ You reply in Slack (mobile-friendly)
- ✅ Type `/check` in Claude Code to retrieve response
- ✅ Works with simple text responses: "1", "2", "3", or custom text
- ✅ Slack bot acknowledges receipt with ✅ reaction

### Phase 2 - Auto-Injection ✅
- ✅ Automatic response injection (no manual `/check` needed)
- ✅ Sub-second latency from Slack → Claude
- ✅ Fully hands-free mobile control
- 📖 See [PHASE2_README.md](PHASE2_README.md) for setup

### Phase 2.5 - Multi-Session Support ✅
- ✅ Unlimited concurrent Claude sessions
- ✅ Thread-based organization in Slack
- ✅ Session picker UI for mobile
- ✅ Auto-routing by thread
- ✅ VibeTunnel compatibility
- ✅ Backward compatible with Phase 2
- 📖 See [PHASE25_README.md](PHASE25_README.md) for full guide

**Quick Start Phase 2.5**:
```bash
# Start registry + Slack bot (one time)
cd slack_bot && ./start-all

# Start multiple Claude sessions (separate terminals)
cd /path/to/project1 && ./claude-slack
cd /path/to/project2 && ./claude-slack
cd /path/to/project3 && ./claude-slack

# Use from mobile: Select thread in Slack, reply
```

### Future: Phase 3 🚧
- Interactive buttons (1️⃣ 2️⃣ 3️⃣), session pause/resume, team collaboration

## Prerequisites

1. **Slack Workspace**: You must have admin access to create apps
2. **Python 3.8+**: For running the Slack bot
3. **Claude Code**: Installed and working

## Setup Instructions

### Step 1: Create Slack App (10 minutes)

1. **Go to Slack API**: https://api.slack.com/apps
2. **Create New App**:
   - Click "Create New App"
   - Choose "From scratch"
   - App Name: "Claude Code Bot" (or any name)
   - Select your workspace
   - Click "Create App"

3. **Enable Socket Mode**:
   - In left sidebar → Settings → Socket Mode
   - Toggle "Enable Socket Mode" → ON
   - Click "Generate an app-level token"
   - Token Name: "Socket Mode Token"
   - Scope: `connections:write`
   - Click "Generate"
   - **COPY THE TOKEN** (starts with `xapp-`) → Save for later
   - Click "Done"

4. **Add Bot Token Scopes**:
   - In left sidebar → Features → OAuth & Permissions
   - Scroll to "Scopes" → "Bot Token Scopes"
   - Click "Add an OAuth Scope" and add these:
     - `app_mentions:read` - Read @mentions
     - `channels:history` - Read channel messages
     - `channels:read` - List channels
     - `chat:write` - Send messages
     - `im:history` - Read DMs
     - `im:read` - Access DM info
     - `im:write` - Send DMs
     - `reactions:write` - Add emoji reactions

5. **Install App to Workspace**:
   - Scroll up on same page → "OAuth Tokens for Your Workspace"
   - Click "Install to Workspace"
   - Review permissions → Click "Allow"
   - **COPY THE BOT TOKEN** (starts with `xoxb-`) → Save for later

6. **Subscribe to Events**:
   - In left sidebar → Features → Event Subscriptions
   - Toggle "Enable Events" → ON
   - Scroll to "Subscribe to bot events"
   - Click "Add Bot User Event" and add:
     - `app_mention` - When someone @mentions your bot
     - `message.channels` - Messages in channels
     - `message.im` - Direct messages
   - Click "Save Changes"

7. **Create Channel** (optional but recommended):
   - In Slack, create a new channel: `#btcbot-claude`
   - Invite the bot: Type `/invite @Claude Code Bot` in the channel
   - This will be your dedicated channel for Claude notifications

### Step 2: Configure Slack Bot (5 minutes)

1. **Navigate to project**:
   ```bash
   cd /Users/danielbennett/codeNew/btcbot/slack_bot
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Create .env file**:
   ```bash
   cp .env.example .env
   ```

4. **Edit .env file** with your tokens:
   ```bash
   # Open in your editor
   nano .env
   # Or
   code .env
   ```

   Replace with your actual tokens:
   ```
   SLACK_BOT_TOKEN=xoxb-YOUR-ACTUAL-BOT-TOKEN
   SLACK_APP_TOKEN=xapp-YOUR-ACTUAL-APP-TOKEN
   SLACK_CHANNEL=#btcbot-claude
   ```

5. **Test the bot**:
   ```bash
   python3 slack_listener.py
   ```

   You should see:
   ```
   🚀 Starting Slack bot...
   📁 Response file: /Users/danielbennett/codeNew/btcbot/slack_response.txt
   ✅ Slack bot is running!
      Listening for:
      - @mentions in channels
      - Direct messages
      - Channel messages starting with / or !
      - Single digit responses (1, 2, 3)

      Press Ctrl+C to stop
   ```

6. **Test in Slack**:
   - Go to `#btcbot-claude` channel
   - Type: `@Claude Code Bot test`
   - Bot should:
     - Add ✅ reaction to your message
     - Reply: "✅ Got it! Saved response: test"
   - Check terminal: Should show "📝 Saved mention..."
   - Check file exists: `ls -la ../slack_response.txt`

7. **Keep bot running**:
   - Open a new terminal tab/window for the bot
   - Or use `tmux`/`screen` to run in background
   - Or use `nohup python3 slack_listener.py &`

### Step 3: Configure Claude Code Hooks (5 minutes)

1. **Navigate to project root**:
   ```bash
   cd /Users/danielbennett/codeNew/btcbot
   ```

2. **Create settings file**:
   ```bash
   cp .claude/settings.local.json.example .claude/settings.local.json
   ```

3. **Edit settings** with your bot token:
   ```bash
   nano .claude/settings.local.json
   # Or
   code .claude/settings.local.json
   ```

   Replace `xoxb-your-bot-token-here` with your actual bot token:
   ```json
   {
     "env": {
       "SLACK_BOT_TOKEN": "xoxb-YOUR-ACTUAL-BOT-TOKEN",
       "SLACK_CHANNEL": "#btcbot-claude"
     },
     "hooks": { ... }
   }
   ```

4. **Verify hook is executable**:
   ```bash
   ls -la .claude/hooks/slack_bidirectional.py
   ```

   Should show `-rwxr-xr-x` (x = executable)

   If not:
   ```bash
   chmod +x .claude/hooks/slack_bidirectional.py
   ```

### Step 4: Test End-to-End (5 minutes)

1. **Start Claude Code**:
   ```bash
   claude
   ```

2. **Send a test prompt**:
   ```
   You: "Test Slack integration"
   ```

3. **Check Slack**:
   - Go to `#btcbot-claude` channel
   - You should see a message like:
     ```
     📝 UserPromptSubmit
     ```Session: abc12345
     Project: btcbot

     Prompt: Test Slack integration```

     _Reply with: 1, 2, 3, or custom text_
     ```

4. **Reply in Slack** (from mobile or desktop):
   ```
   yes, proceed
   ```

5. **Check for response in Claude Code**:
   ```
   You: /check
   ```

   You should see:
   ```
   ============================================================
   📱 SLACK RESPONSE:
   ============================================================

   yes, proceed

   ============================================================

   You can now type this response in Claude Code (or just press Enter to accept it)
   ```

6. **Type response in Claude Code**:
   ```
   You: yes, proceed
   ```

7. **Success!** 🎉 You now have bidirectional Slack integration

## Usage Workflow

### Normal Usage (Mobile Control)

1. **Start Claude Code session** (on laptop):
   ```bash
   cd /Users/danielbennett/codeNew/btcbot
   claude
   ```

2. **Work with Claude** - Claude will auto-notify Slack on:
   - Every prompt you send (`UserPromptSubmit` event)
   - When Claude finishes (`Stop` event)

3. **Claude asks a question**:
   ```
   Claude: "Should I proceed with this analysis? (y/n)"
   ```

4. **Check Slack on mobile** - You'll see:
   ```
   ✅ Stop
   Session: abc12345
   Project: btcbot

   Claude has finished processing.

   Reply with: 1, 2, 3, or custom text
   ```

5. **Reply in Slack** (mobile):
   - Type `1` (yes)
   - Or `2` (always allow)
   - Or `3` (no)
   - Or any custom text: "yes, use RSI 30"

6. **Back at laptop**, check for response:
   ```
   You: /check
   ```

7. **See response**:
   ```
   ============================================================
   📱 SLACK RESPONSE:
   ============================================================

   1

   ============================================================
   ```

8. **Type response in Claude**:
   ```
   You: 1
   ```
   (Or just type the custom text)

9. **Claude continues** with your response

### Quick Commands

- `/check` - Check for Slack responses
- `1` - Common response (yes/proceed)
- `2` - Common response (always allow)
- `3` - Common response (no/decline)

## Troubleshooting

### Bot not receiving messages

**Check bot is running**:
```bash
# In slack_bot directory
ps aux | grep slack_listener
```

If not running:
```bash
cd slack_bot
python3 slack_listener.py
```

**Check bot is in channel**:
- Type `/invite @Claude Code Bot` in the Slack channel
- Bot must be explicitly invited to channels

**Check permissions**:
- Slack App → OAuth & Permissions → verify all scopes listed above

### Claude not sending to Slack

**Check token in settings**:
```bash
cat .claude/settings.local.json
# Verify SLACK_BOT_TOKEN is set correctly
```

**Check hook is executable**:
```bash
ls -la .claude/hooks/slack_bidirectional.py
chmod +x .claude/hooks/slack_bidirectional.py  # If needed
```

**Check slack_sdk is installed**:
```bash
pip install slack-sdk
```

**Test hook manually**:
```bash
echo '{"session_id": "test123", "project_dir": "/test"}' | \
  HOOK_EVENT_TYPE=UserPromptSubmit \
  SLACK_BOT_TOKEN=xoxb-YOUR-TOKEN \
  SLACK_CHANNEL=#btcbot-claude \
  python3 .claude/hooks/slack_bidirectional.py
```

### /check shows no response

**Check file exists**:
```bash
ls -la slack_response.txt
cat slack_response.txt
```

**Check bot wrote to file**:
- Send test message in Slack
- Check if file was created/updated
- Check bot terminal for error messages

**File permissions**:
```bash
# Ensure file is writable
chmod 644 slack_response.txt  # If it exists
```

### Slack bot crashes

**Check logs**:
- Look at terminal output where bot is running
- Common issues:
  - Token expired/invalid
  - Network connection lost
  - Rate limiting

**Restart bot**:
```bash
cd slack_bot
python3 slack_listener.py
```

**Check Slack App status**:
- https://api.slack.com/apps
- Your App → Settings → Basic Information
- Verify "Install to Workspace" is active

## Files Overview

```
btcbot/
├── slack_bot/
│   ├── slack_listener.py        # Main Slack bot (runs continuously)
│   ├── requirements.txt         # Python dependencies
│   ├── .env.example            # Template for configuration
│   ├── .env                    # Your tokens (gitignored)
│   └── README.md               # This file
│
├── .claude/
│   ├── hooks/
│   │   └── slack_bidirectional.py  # Claude Code hook
│   ├── commands/
│   │   └── check.md            # /check slash command
│   ├── settings.local.json.example  # Template
│   └── settings.local.json     # Your config (gitignored)
│
└── slack_response.txt          # Shared file for responses (gitignored)
```

## Security Notes

- **Never commit tokens**: `.env` and `settings.local.json` are gitignored
- **Rotate tokens periodically**: Recommended every 90 days
- **Use separate bot token**: Don't use your personal user token
- **Limit channel access**: Only invite bot to channels that need it
- **Monitor usage**: Check Slack App dashboard for suspicious activity

## Next Steps (Future Phases)

### Phase 2: Auto-Injection Wrapper
- Build `claude_wrapper.py` to proxy stdin/stdout
- Automatically inject Slack responses (no manual `/check`)
- Estimated time: 3-4 hours

### Phase 3: Rich Slack UI
- Interactive buttons (1️⃣ Yes  2️⃣ Always  3️⃣ No)
- Threaded messages for context
- Button 4️⃣ for additional context
- Rich formatting, code blocks
- Estimated time: 2-3 hours

## Support

For issues:
1. Check troubleshooting section above
2. Verify all setup steps completed
3. Check bot terminal logs
4. Check Claude Code output for hook errors

## License

Same as parent project (btcbot)
