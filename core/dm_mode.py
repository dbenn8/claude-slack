"""
DM Mode for Claude Slack Integration

Provides commands for users to subscribe to session output in their DMs:
- /sessions - List active sessions
- /attach <session_id> [N] - Subscribe to session, optionally fetch last N messages
- /detach - Unsubscribe from current session
- /mode [plan|research|execute] - View or set interaction mode
"""

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class DMCommand:
    """Parsed DM command with command name and arguments."""
    command: str
    args: Dict[str, Any] = field(default_factory=dict)


def parse_dm_command(text: str) -> Optional[DMCommand]:
    """
    Parse a DM command from user input.

    Supported commands:
    - /sessions - List active sessions
    - /attach <session_id> [history_count] - Subscribe to session
    - /detach - Unsubscribe from current session
    - /mode [plan|research|execute] - View or set interaction mode

    Args:
        text: Raw message text from Slack DM

    Returns:
        DMCommand if valid command parsed, None if not a command or unknown command
    """
    if not text:
        return None

    # Strip whitespace and check for command prefix
    text = text.strip()
    if not text.startswith('/'):
        return None

    # Split into parts
    parts = text.split()
    if not parts:
        return None

    # Extract command (case-insensitive)
    cmd = parts[0][1:].lower()  # Remove leading '/'

    # Parse by command
    if cmd == 'sessions':
        return DMCommand(command='sessions', args={})

    elif cmd == 'attach':
        if len(parts) < 2:
            return DMCommand(
                command='error',
                args={'message': 'Usage: /attach <session_id> [history_count]'}
            )

        session_id = parts[1]
        history_count = None

        if len(parts) >= 3:
            try:
                history_count = int(parts[2])
                # Clamp to valid range
                history_count = max(1, min(25, history_count))
            except ValueError:
                history_count = None

        args = {'session_id': session_id}
        if history_count is not None:
            args['history_count'] = history_count

        return DMCommand(command='attach', args=args)

    elif cmd == 'detach':
        return DMCommand(command='detach', args={})

    elif cmd == 'mode':
        # /mode - show current mode
        # /mode <plan|research|execute> - set mode
        if len(parts) < 2:
            return DMCommand(command='mode', args={'action': 'show'})

        mode = parts[1].lower()
        valid_modes = {'plan', 'research', 'execute'}
        if mode not in valid_modes:
            return DMCommand(
                command='error',
                args={'message': f'Invalid mode: `{mode}`. Valid modes: plan, research, execute'}
            )

        return DMCommand(command='mode', args={'action': 'set', 'mode': mode})

    else:
        # Unknown command
        return None


def strip_ansi_codes(text: str) -> str:
    """
    Strip ANSI escape codes from terminal output.

    Args:
        text: String with potential ANSI codes

    Returns:
        Clean string without ANSI codes
    """
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def forward_to_dm_subscribers(db, session_id: str, message: str, slack_client) -> None:
    """
    Forward a message to all DM subscribers for a session.

    Args:
        db: RegistryDatabase instance
        session_id: Claude session ID
        message: Message text to forward
        slack_client: Slack WebClient instance
    """
    subscriptions = db.get_dm_subscriptions_for_session(session_id)

    if not subscriptions:
        return

    for sub in subscriptions:
        dm_channel = sub.get('dm_channel_id')
        if not dm_channel:
            continue

        try:
            slack_client.chat_postMessage(
                channel=dm_channel,
                text=message
            )
        except Exception as e:
            # Log error but continue to other subscribers
            print(f"[dm_mode] Error forwarding to {dm_channel}: {e}", file=sys.stderr)
            continue


def forward_terminal_output(db, session_id: str, buffer_path: str, slack_client) -> None:
    """
    Read terminal output buffer and forward to DM subscribers.

    Args:
        db: RegistryDatabase instance
        session_id: Claude session ID
        buffer_path: Path to terminal output buffer file
        slack_client: Slack WebClient instance
    """
    if not os.path.exists(buffer_path):
        return

    try:
        with open(buffer_path, 'r', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"[dm_mode] Error reading buffer {buffer_path}: {e}", file=sys.stderr)
        return

    if not content.strip():
        return

    # Strip ANSI codes
    clean_content = strip_ansi_codes(content)

    # Forward to subscribers
    forward_to_dm_subscribers(db, session_id, clean_content, slack_client)


def handle_session_end(db, session_id: str, slack_client) -> None:
    """
    Handle session end - notify subscribers and clean up.

    Args:
        db: RegistryDatabase instance
        session_id: Claude session ID that ended
        slack_client: Slack WebClient instance
    """
    # Get all subscribers before cleanup
    subscriptions = db.get_dm_subscriptions_for_session(session_id)

    if not subscriptions:
        return

    # Get session info for the notification
    session = db.get_session(session_id)
    project = session.get('project', 'unknown') if session else 'unknown'

    # Notify each subscriber
    end_message = f"🔚 *Session ended*\n\nThe session `{session_id}` ({project}) has ended. You've been automatically detached."

    for sub in subscriptions:
        dm_channel = sub.get('dm_channel_id')
        if not dm_channel:
            continue

        try:
            slack_client.chat_postMessage(
                channel=dm_channel,
                text=end_message
            )
        except Exception as e:
            print(f"[dm_mode] Error notifying subscriber {dm_channel} of session end: {e}", file=sys.stderr)
            continue

    # Clean up all subscriptions for this session
    db.cleanup_dm_subscriptions_for_session(session_id)


def list_active_sessions(db) -> list:
    """
    List all active Claude sessions.

    Args:
        db: RegistryDatabase instance

    Returns:
        List of session dicts with session_id, project, created_at
    """
    sessions = db.list_sessions(status='active')
    return [
        {
            'session_id': s['session_id'],
            'project': s.get('project', 'unknown'),
            'created_at': s.get('created_at'),
        }
        for s in sessions
    ]


def format_session_list_for_slack(db) -> str:
    """
    Format active sessions as a Slack message.

    Args:
        db: RegistryDatabase instance

    Returns:
        Formatted Slack message string
    """
    sessions = list_active_sessions(db)

    if not sessions:
        return "No active sessions\n\nStart a Claude session first, then use `/sessions` to see it here."

    lines = ["*Active Sessions:*\n"]

    for session in sessions:
        session_id = session['session_id']
        project = session['project']
        created = session.get('created_at', '')
        if created:
            # Format as relative time or just date portion
            created_short = created[:10] if len(created) >= 10 else created
        else:
            created_short = ''

        lines.append(f"• `{session_id}` - {project}")
        if created_short:
            lines.append(f"  _Started: {created_short}_")

    lines.append("\n💡 Use `/attach <session_id>` to subscribe to a session's output")

    return '\n'.join(lines)


def get_transcript_path_for_session(db, session_id: str) -> str:
    """
    Find the transcript JSONL file for a session.

    Args:
        db: RegistryDatabase instance
        session_id: Claude session ID

    Returns:
        Path to transcript file, or None if not found
    """
    session = db.get_session(session_id)
    if not session:
        return None

    project_dir = session.get('project_dir')
    if not project_dir:
        return None

    # Construct transcript path using same logic as TranscriptParser
    project_slug = project_dir.replace("/", "-")
    if project_slug.startswith("-"):
        project_slug = project_slug[1:]

    transcript_path = os.path.join(
        os.path.expanduser("~"),
        ".claude",
        "projects",
        f"-{project_slug}",
        f"{session_id}.jsonl"
    )

    if os.path.exists(transcript_path):
        return transcript_path

    return None


def attach_to_session(db, user_id: str, session_id: str, dm_channel_id: str, slack_client, history_count: int = 0) -> dict:
    """
    Attach a user to a session's DM output.

    Args:
        db: RegistryDatabase instance
        user_id: Slack user ID
        session_id: Claude session ID to subscribe to
        dm_channel_id: Slack DM channel ID
        slack_client: Slack WebClient instance
        history_count: Number of recent messages to send (0 = none)

    Returns:
        Dict with success: bool and message: str
    """
    # Verify session exists
    session = db.get_session(session_id)
    if not session:
        return {'success': False, 'message': f'Session `{session_id}` not found.'}

    if session.get('status') == 'ended':
        return {'success': False, 'message': f'Session `{session_id}` has ended.'}

    # Create subscription (replaces any existing)
    db.create_dm_subscription(user_id, session_id, dm_channel_id)

    # Send history if requested
    if history_count > 0:
        transcript_path = get_transcript_path_for_session(db, session_id)
        if transcript_path:
            try:
                from transcript_parser import TranscriptParser
                parser = TranscriptParser(transcript_path)
                if parser.load():
                    messages = parser.get_last_n_messages(n=history_count)
                    if messages:
                        # Format and send history
                        history_text = "*Recent messages:*\n"
                        for msg in messages:
                            role_emoji = "👤" if msg['role'] == 'user' else "🤖"
                            # Truncate long messages
                            text = msg['text'][:500] + '...' if len(msg['text']) > 500 else msg['text']
                            history_text += f"{role_emoji} {text}\n\n"

                        try:
                            slack_client.chat_postMessage(
                                channel=dm_channel_id,
                                text=history_text
                            )
                        except Exception as e:
                            print(f"[dm_mode] Error sending history: {e}", file=sys.stderr)
            except Exception as e:
                print(f"[dm_mode] Error loading transcript for history: {e}", file=sys.stderr)

    project = session.get('project', 'unknown')
    return {
        'success': True,
        'message': f"✅ Attached to session `{session_id}` ({project}). You'll receive all output in this DM."
    }


def detach_from_session(db, user_id: str, slack_client, dm_channel_id: str) -> dict:
    """
    Detach a user from their current session subscription.

    Args:
        db: RegistryDatabase instance
        user_id: Slack user ID
        slack_client: Slack WebClient instance
        dm_channel_id: Slack DM channel ID

    Returns:
        Dict with success: bool and message: str
    """
    # Check current subscription
    sub = db.get_dm_subscription_for_user(user_id)

    if not sub:
        return {
            'success': True,
            'message': "ℹ️ You're not currently attached to any session."
        }

    session_id = sub['session_id']

    # Remove subscription
    db.delete_dm_subscription(user_id)

    return {
        'success': True,
        'message': f"✅ Detached from session `{session_id}`. You'll no longer receive output."
    }


# ─────────────────────────────────────────────────────────────────────────────
# Mode Prompts - Appended to user messages based on their selected mode
# ─────────────────────────────────────────────────────────────────────────────

MODE_PROMPTS = {
    'research': """
---

You are in RESEARCH MODE.

Goal:
- Understand the codebase, constraints, and problem space.
- Identify risks, edge cases, and unknowns.

Rules:
- Do NOT propose implementation code.
- Do NOT modify files.
- Do NOT write tests yet.
- You may read files, summarize behavior, and ask clarifying questions.

Output:
- Brief summary of how the current system works (relevant parts only).
- Key assumptions and invariants.
- Risks or ambiguities that could affect implementation.
- Suggested test scenarios (inputs/outputs), without writing tests.
""",

    'plan': """
---

You are in PLAN MODE.

Goal:
- Design an implementation approach based on research findings.

Rules:
- Do NOT write implementation code yet.
- You may outline pseudocode or structure.
- Focus on approach, not implementation details.

Output:
- Step-by-step implementation plan.
- Key files and functions to modify.
- Potential risks and mitigations.
""",

    'execute': """
---

You are in EXECUTE MODE.

Goal:
- Implement the planned changes.

Rules:
- Follow the established plan.
- Write clean, tested code.
- Commit logical units of work.
"""
}


def get_mode_prompt(mode: str) -> str:
    """
    Get the system prompt for a given mode.

    Args:
        mode: Mode name (plan, research, execute)

    Returns:
        Mode prompt string, or empty string if mode not found
    """
    return MODE_PROMPTS.get(mode.lower(), '')


def handle_mode_command(db, user_id: str, action: str, mode: str = None) -> dict:
    """
    Handle /mode command - show or set user's interaction mode.

    Args:
        db: RegistryDatabase instance
        user_id: Slack user ID
        action: 'show' or 'set'
        mode: Mode to set (only required if action='set')

    Returns:
        Dict with success: bool and message: str
    """
    if action == 'show':
        current_mode = db.get_user_mode(user_id)
        mode_descriptions = {
            'research': 'Read-only exploration and analysis',
            'plan': 'Design approach without writing code',
            'execute': 'Implement changes (default)'
        }
        desc = mode_descriptions.get(current_mode, '')

        message = f"*Current mode:* `{current_mode}`\n_{desc}_\n\n"
        message += "*Available modes:*\n"
        message += "• `/mode research` - Read-only exploration and analysis\n"
        message += "• `/mode plan` - Design approach without writing code\n"
        message += "• `/mode execute` - Implement changes (default)"

        return {'success': True, 'message': message}

    elif action == 'set':
        try:
            db.set_user_mode(user_id, mode)
            mode_descriptions = {
                'research': 'Read-only exploration and analysis',
                'plan': 'Design approach without writing code',
                'execute': 'Implement changes'
            }
            desc = mode_descriptions.get(mode, '')
            return {
                'success': True,
                'message': f"✅ Mode set to `{mode}`\n_{desc}_"
            }
        except ValueError as e:
            return {'success': False, 'message': f"❌ {str(e)}"}

    return {'success': False, 'message': '❌ Invalid action'}
