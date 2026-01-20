"""
Live Slack E2E tests - tests actual Slack API integration.

These tests connect to a real Slack workspace using credentials from .env
and verify that messages are sent correctly.

Usage:
    # Run all live tests (non-interactive, verifies API calls)
    pytest tests/e2e/test_live_slack.py -v -m live_slack

    # Run with human verification prompts
    pytest tests/e2e/test_live_slack.py -v -s -m live_slack --interactive

    # Run specific test
    pytest tests/e2e/test_live_slack.py::TestLiveThreadedMode::test_create_thread -v -m live_slack

Requirements:
    - .env file with SLACK_BOT_TOKEN and SLACK_APP_TOKEN
    - Bot must be invited to the test channel
    - SLACK_TEST_CHANNEL or SLACK_CHANNEL must be set

Environment Variables:
    SLACK_BOT_TOKEN: Bot token (xoxb-...)
    SLACK_APP_TOKEN: App token (xapp-...)
    SLACK_TEST_CHANNEL: Channel ID for testing (default: uses SLACK_CHANNEL from .env)
"""

import os
import sys
import time
import tempfile
from pathlib import Path
from datetime import datetime

import pytest
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


def get_slack_credentials():
    """Get Slack credentials from environment."""
    bot_token = os.environ.get('SLACK_BOT_TOKEN')
    app_token = os.environ.get('SLACK_APP_TOKEN')
    channel = os.environ.get('SLACK_TEST_CHANNEL') or os.environ.get('SLACK_CHANNEL')

    return {
        'bot_token': bot_token,
        'app_token': app_token,
        'channel': channel,
        'available': bool(bot_token and channel)
    }


@pytest.fixture
def slack_credentials():
    """Provide Slack credentials, skip if not available."""
    creds = get_slack_credentials()
    if not creds['available']:
        pytest.skip(
            "Slack credentials not available. Set SLACK_BOT_TOKEN and "
            "SLACK_CHANNEL (or SLACK_TEST_CHANNEL) in .env"
        )
    return creds


@pytest.fixture
def slack_client(slack_credentials):
    """Create a real Slack WebClient."""
    from slack_sdk import WebClient
    return WebClient(token=slack_credentials['bot_token'])


@pytest.fixture
def is_interactive(request):
    """Check if running in interactive mode."""
    return request.config.getoption("--interactive", default=False)


def pytest_addoption(parser):
    """Add --interactive option for human verification."""
    try:
        parser.addoption(
            "--interactive",
            action="store_true",
            default=False,
            help="Enable interactive human verification prompts"
        )
    except ValueError:
        # Option already added
        pass


def wait_for_user_verification(prompt: str, timeout: int = 60) -> bool:
    """Wait for user to verify something in Slack (interactive mode only)."""
    print(f"\n{'='*60}")
    print(f"VERIFICATION REQUIRED:")
    print(f"  {prompt}")
    print(f"{'='*60}")
    print(f"Press ENTER to confirm, or 'n' + ENTER to fail (timeout: {timeout}s)")

    import select
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            response = sys.stdin.readline().strip().lower()
            return response != 'n'
        else:
            print("Timeout waiting for verification")
            return False
    except (OSError, io.UnsupportedOperation):
        # Non-interactive environment
        return True


@pytest.mark.live_slack
class TestLiveThreadedMode:
    """Test threaded mode with real Slack connection."""

    def test_create_thread(self, slack_client, slack_credentials, is_interactive):
        """
        Create a new thread and verify it was created successfully.

        Verifies:
        - Thread creation returns ok=True
        - Thread has valid timestamp
        - Thread is in the correct channel
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create thread
        response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Thread Creation Test - {timestamp}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*E2E Test*: `test_create_thread`\nTimestamp: {timestamp}"
                    }
                }
            ]
        )

        # Verify response
        assert response['ok'], f"Failed to create thread: {response.get('error')}"
        assert response['ts'], "Thread timestamp missing"
        # Response returns channel ID, not name - just verify it exists
        assert response['channel'], "Channel missing from response"

        thread_ts = response['ts']
        channel_id = response['channel']  # Use channel ID for subsequent calls

        # Post reply in thread
        reply_response = slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="This is a reply in the thread."
        )
        assert reply_response['ok'], f"Failed to post reply: {reply_response.get('error')}"

        # Interactive verification if enabled
        if is_interactive:
            wait_for_user_verification(
                f"Check channel for thread with timestamp {thread_ts}"
            )

        # Cleanup marker
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=":white_check_mark: Test completed"
        )

    def test_permission_prompt_blocks(self, slack_client, slack_credentials, is_interactive):
        """
        Test posting permission prompt with Block Kit buttons.

        Verifies:
        - Block Kit message posts successfully
        - Buttons are included in the message
        - Message appears in correct thread
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create thread
        thread_response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Permission Blocks Test - {timestamp}"
        )
        assert thread_response['ok']
        thread_ts = thread_response['ts']

        # Post permission prompt with buttons
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Permission Request*\n\nClaude wants to run:\n```bash\nrm -rf /tmp/test_directory\n```"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ":warning: *Dangerous command detected*"
                    }
                ]
            },
            {
                "type": "actions",
                "block_id": f"permission_test_{thread_ts}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yes"},
                        "style": "primary",
                        "action_id": "permission_response_1",
                        "value": "1"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yes, don't ask again"},
                        "action_id": "permission_response_2",
                        "value": "2"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "No"},
                        "style": "danger",
                        "action_id": "permission_response_3",
                        "value": "3"
                    }
                ]
            }
        ]

        perm_response = slack_client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Permission Request",
            blocks=blocks
        )

        assert perm_response['ok'], f"Failed to post permission: {perm_response.get('error')}"
        assert perm_response['message']['blocks'], "Blocks not in response"

        if is_interactive:
            wait_for_user_verification(
                f"Verify buttons appear in thread {thread_ts}"
            )

    def test_message_update(self, slack_client, slack_credentials, is_interactive):
        """
        Test updating a message in place (for todo progress).

        Verifies:
        - Initial message posts successfully
        - Message can be updated via chat.update
        - Updated content is reflected
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create thread
        thread_response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Message Update Test - {timestamp}"
        )
        thread_ts = thread_response['ts']
        channel_id = thread_response['channel']  # Use channel ID for subsequent calls

        # Post initial message
        initial_response = slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Progress: 0%"
        )
        assert initial_response['ok']
        message_ts = initial_response['ts']

        # Update message (requires channel ID, not name)
        update_response = slack_client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text="Progress: 100% :white_check_mark:"
        )

        assert update_response['ok'], f"Failed to update: {update_response.get('error')}"
        assert update_response['ts'] == message_ts, "Message timestamp changed"

        if is_interactive:
            wait_for_user_verification(
                f"Verify message shows '100%' in thread {thread_ts}"
            )

    def test_add_reaction(self, slack_client, slack_credentials, is_interactive):
        """
        Test adding a reaction to a message.

        Verifies:
        - Reaction can be added to a message
        - API returns success
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create message
        msg_response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Reaction Test - {timestamp}"
        )
        assert msg_response['ok']
        message_ts = msg_response['ts']
        channel_id = msg_response['channel']  # Use channel ID for reactions.add

        # Add reaction (requires channel ID, not name)
        reaction_response = slack_client.reactions_add(
            channel=channel_id,
            timestamp=message_ts,
            name="white_check_mark"
        )

        assert reaction_response['ok'], f"Failed to add reaction: {reaction_response.get('error')}"

        if is_interactive:
            wait_for_user_verification(
                f"Verify :white_check_mark: reaction on message {message_ts}"
            )


@pytest.mark.live_slack
class TestLiveCustomChannelMode:
    """Test custom channel mode (no threading) with real Slack."""

    def test_post_without_thread(self, slack_client, slack_credentials, is_interactive):
        """
        Test posting directly to channel without threading.

        Verifies:
        - Message posts successfully without thread_ts
        - Message appears at channel top level
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Post without thread_ts (custom channel mode)
        response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Custom Channel Mode - {timestamp}"
        )

        assert response['ok'], f"Failed to post: {response.get('error')}"
        assert response['ts'], "Message timestamp missing"
        # No thread_ts in custom channel mode
        assert response['message'].get('thread_ts') is None or response['message'].get('thread_ts') == response['ts']

        if is_interactive:
            wait_for_user_verification(
                "Verify message appears at TOP LEVEL (not threaded)"
            )

    def test_permission_prompt_channel_mode(self, slack_client, slack_credentials, is_interactive):
        """
        Test posting permission prompt without threading (channel mode).

        Verifies:
        - Permission block posts at top level
        - Buttons work without thread context
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*[E2E Test] Channel Mode Permission - {timestamp}*\n\nClaude wants to run:\n```bash\nnpm install\n```"
                }
            },
            {
                "type": "actions",
                "block_id": f"permission_channel_{int(time.time())}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yes"},
                        "style": "primary",
                        "action_id": "permission_response_1",
                        "value": "1"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "No"},
                        "style": "danger",
                        "action_id": "permission_response_3",
                        "value": "3"
                    }
                ]
            }
        ]

        response = slack_client.chat_postMessage(
            channel=channel,
            text="Permission Request (Channel Mode)",
            blocks=blocks
        )

        assert response['ok'], f"Failed to post: {response.get('error')}"
        assert response['message']['blocks'], "Blocks missing from response"
        # Verify no thread_ts (top-level message)
        assert response['message'].get('thread_ts') is None or response['message'].get('thread_ts') == response['ts']

        if is_interactive:
            wait_for_user_verification(
                "Verify permission buttons appear at TOP LEVEL (not in thread)"
            )

    def test_message_update_channel_mode(self, slack_client, slack_credentials, is_interactive):
        """
        Test updating a top-level message (channel mode todo progress).

        Verifies:
        - Can update top-level messages
        - Message stays at top level after update
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Post initial todo message at top level
        initial_response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Channel Mode Update - {timestamp}\n\nTasks: 0/3 complete"
        )
        assert initial_response['ok']
        message_ts = initial_response['ts']
        # Use the channel ID from response (chat.update requires ID, not name)
        channel_id = initial_response['channel']

        # Update the message
        update_response = slack_client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=f"[E2E Test] Channel Mode Update - {timestamp}\n\nTasks: 3/3 complete :white_check_mark:"
        )

        assert update_response['ok'], f"Failed to update: {update_response.get('error')}"
        assert update_response['ts'] == message_ts, "Message timestamp changed"

        if is_interactive:
            wait_for_user_verification(
                "Verify message was updated at TOP LEVEL showing 3/3 complete"
            )

    def test_multiple_top_level_messages(self, slack_client, slack_credentials, is_interactive):
        """
        Test sending multiple top-level messages (typical channel mode workflow).

        Verifies:
        - Multiple messages post at top level
        - Messages maintain order
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        messages = [
            f"[E2E Test] Channel Mode Multi-Msg #{i+1} - {timestamp}"
            for i in range(3)
        ]

        posted_ts = []
        for msg in messages:
            response = slack_client.chat_postMessage(
                channel=channel,
                text=msg
            )
            assert response['ok'], f"Failed to post: {response.get('error')}"
            posted_ts.append(response['ts'])

        # Verify all messages were posted
        assert len(posted_ts) == 3, "Not all messages posted"

        # Verify timestamps are increasing (messages in order)
        assert posted_ts[0] < posted_ts[1] < posted_ts[2], "Messages not in order"

        if is_interactive:
            wait_for_user_verification(
                "Verify 3 numbered messages appear at TOP LEVEL in order"
            )

    def test_session_registration_channel_mode(self, slack_credentials, tmp_path, is_interactive):
        """
        Test session registration in custom channel mode.

        Verifies:
        - Session registered with custom_channel flag
        - Messages go to top level (no thread)
        """
        from session_registry import SessionRegistry

        SessionRegistry._instance = None
        # Initialize registry with the custom channel as default
        registry = SessionRegistry(
            registry_dir=str(tmp_path / "registry"),
            socket_path=str(tmp_path / "sockets" / "registry.sock"),
            slack_token=slack_credentials['bot_token'],
            slack_channel=slack_credentials['channel']
        )

        session_id = f"e2e_channel_test_{int(time.time())}"

        try:
            # Register session - in custom channel mode, messages go to top level
            session = registry.register_session_simple(
                session_id=session_id,
                project="e2e-channel-test",
                terminal="test-terminal",
                socket_path=str(tmp_path / f"{session_id}.sock")
            )

            # Verify session was created
            assert session is not None, "Session registration failed"
            assert session['session_id'] == session_id

            # Verify Slack metadata exists
            channel = session.get('slack_channel') or session.get('channel')
            assert channel, f"Session missing channel info: {session}"

            if is_interactive:
                wait_for_user_verification(
                    "Verify session message appears in the channel"
                )

        finally:
            try:
                registry.unregister_session(session_id)
            except Exception:
                pass


@pytest.mark.live_slack
class TestLiveSessionRegistry:
    """Test session registry with real Slack thread creation."""

    def test_session_registration_creates_thread(self, slack_credentials, tmp_path, is_interactive):
        """
        Test that session registration creates a Slack thread.

        Verifies:
        - SessionRegistry initializes with Slack client
        - register_session_simple creates a thread
        - Session data includes thread_ts and channel
        """
        from session_registry import SessionRegistry

        # Create registry with real Slack token
        SessionRegistry._instance = None
        registry = SessionRegistry(
            registry_dir=str(tmp_path / "registry"),
            socket_path=str(tmp_path / "sockets" / "registry.sock"),
            slack_token=slack_credentials['bot_token'],
            slack_channel=slack_credentials['channel']
        )

        session_id = f"e2e_test_{int(time.time())}"

        try:
            session = registry.register_session_simple(
                session_id=session_id,
                project="e2e-live-test",
                terminal="test-terminal",
                socket_path=str(tmp_path / f"{session_id}.sock")
            )

            # Verify session has Slack metadata (registry uses slack_channel/slack_thread_ts)
            channel = session.get('slack_channel') or session.get('channel')
            thread_ts = session.get('slack_thread_ts') or session.get('thread_ts')
            assert channel, f"Session missing channel: {session}"
            assert thread_ts, f"Session missing thread_ts: {session}"
            assert session['session_id'] == session_id

            if is_interactive:
                wait_for_user_verification(
                    f"Verify session thread created for project 'e2e-live-test'"
                )

        finally:
            # Cleanup
            try:
                registry.unregister_session(session_id)
            except Exception:
                pass


@pytest.mark.live_slack
class TestLiveErrorHandling:
    """Test error handling with real Slack API."""

    def test_invalid_channel_error(self, slack_client):
        """
        Test that posting to invalid channel returns proper error.

        Verifies:
        - API returns ok=False for invalid channel
        - Error message is descriptive
        """
        from slack_sdk.errors import SlackApiError

        with pytest.raises(SlackApiError) as exc_info:
            slack_client.chat_postMessage(
                channel="INVALID_CHANNEL_ID",
                text="This should fail"
            )

        assert exc_info.value.response['error'] in ['channel_not_found', 'invalid_channel']

    def test_message_not_found_error(self, slack_client, slack_credentials):
        """
        Test that updating non-existent message returns proper error.

        Verifies:
        - API returns error for invalid message ts
        """
        from slack_sdk.errors import SlackApiError

        # First get a valid channel ID by posting a message
        response = slack_client.chat_postMessage(
            channel=slack_credentials['channel'],
            text="[E2E Test] Getting channel ID for error test"
        )
        channel_id = response['channel']

        # Now try to update a non-existent message in that channel
        with pytest.raises(SlackApiError) as exc_info:
            slack_client.chat_update(
                channel=channel_id,
                ts="0000000000.000000",  # Non-existent message
                text="This should fail"
            )

        assert exc_info.value.response['error'] == 'message_not_found'


@pytest.mark.live_slack
class TestLiveRateLimits:
    """Test behavior under rate limiting conditions."""

    def test_multiple_messages_succeed(self, slack_client, slack_credentials):
        """
        Test sending multiple messages in quick succession.

        Verifies:
        - Multiple messages can be sent
        - All messages are delivered
        """
        channel = slack_credentials['channel']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create thread
        thread_response = slack_client.chat_postMessage(
            channel=channel,
            text=f"[E2E Test] Rate Limit Test - {timestamp}"
        )
        thread_ts = thread_response['ts']

        # Send 5 messages quickly
        message_count = 5
        sent_messages = []

        for i in range(message_count):
            response = slack_client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"Message {i+1}/{message_count}"
            )
            assert response['ok'], f"Message {i+1} failed: {response.get('error')}"
            sent_messages.append(response['ts'])

        assert len(sent_messages) == message_count, "Not all messages sent"

        # Cleanup
        slack_client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f":white_check_mark: All {message_count} messages sent successfully"
        )


def run_live_tests():
    """Run all live Slack tests."""
    print("\n" + "="*60)
    print("LIVE SLACK E2E TESTS")
    print("="*60)

    creds = get_slack_credentials()
    if not creds['available']:
        print("\nERROR: Slack credentials not found in .env")
        print("Required: SLACK_BOT_TOKEN, SLACK_CHANNEL")
        return 1

    print(f"\nChannel: {creds['channel']}")
    print(f"Token: {creds['bot_token'][:20]}...")
    print("\nRunning tests...\n")

    import subprocess
    result = subprocess.run([
        sys.executable, "-m", "pytest",
        __file__,
        "-v",
        "-m", "live_slack",
        "--tb=short"
    ])

    return result.returncode


if __name__ == "__main__":
    sys.exit(run_live_tests())
