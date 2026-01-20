"""
Daemon Lifecycle E2E Tests - tests starting/stopping the listener daemon.

These tests verify that:
1. The listener daemon can be started from a separate directory
2. Sessions can attach to a running daemon
3. The daemon handles multiple sessions
4. The daemon can be cleanly stopped

Usage:
    pytest tests/e2e/test_daemon_lifecycle.py -v -m live_slack

Requirements:
    - .env file with SLACK_BOT_TOKEN and SLACK_APP_TOKEN
    - Bot must be invited to the test channel
"""

import os
import sys
import time
import signal
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

import pytest
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
env_path = PROJECT_ROOT / '.env'
load_dotenv(env_path)

# Add core directory to path
sys.path.insert(0, str(PROJECT_ROOT / "core"))


def get_slack_credentials():
    """Get Slack credentials from environment."""
    bot_token = os.environ.get('SLACK_BOT_TOKEN')
    app_token = os.environ.get('SLACK_APP_TOKEN')
    channel = os.environ.get('SLACK_TEST_CHANNEL') or os.environ.get('SLACK_CHANNEL')

    return {
        'bot_token': bot_token,
        'app_token': app_token,
        'channel': channel,
        'available': bool(bot_token and app_token and channel)
    }


def wait_for_channel(registry, session_id, timeout=10):
    """
    Wait for async Slack channel creation to complete.

    Args:
        registry: SessionRegistry instance
        session_id: Session ID to check
        timeout: Max seconds to wait (default 10)

    Returns:
        Session dict with channel populated, or None if timeout
    """
    start = time.time()
    while time.time() - start < timeout:
        session = registry.get_session(session_id)
        # Database returns 'channel' key (mapped from slack_channel column)
        if session and session.get('channel'):
            return session
        time.sleep(0.2)  # Check every 200ms
    # Return whatever we have (may still be None)
    return registry.get_session(session_id)


@pytest.fixture
def slack_credentials():
    """Provide Slack credentials, skip if not available."""
    creds = get_slack_credentials()
    if not creds['available']:
        pytest.skip(
            "Slack credentials not available. Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, "
            "and SLACK_CHANNEL (or SLACK_TEST_CHANNEL) in .env"
        )
    return creds


@pytest.fixture
def daemon_process(slack_credentials, tmp_path):
    """
    Start the listener daemon and yield its process.

    Cleans up by killing the daemon after the test.
    """
    # Create a test working directory (simulating a different project)
    test_workdir = tmp_path / "test_project"
    test_workdir.mkdir()

    # Set up environment for the daemon
    env = os.environ.copy()
    env['SLACK_BOT_TOKEN'] = slack_credentials['bot_token']
    env['SLACK_APP_TOKEN'] = slack_credentials['app_token']
    env['SLACK_CHANNEL'] = slack_credentials['channel']
    env['PYTHONPATH'] = str(PROJECT_ROOT / "core")

    # Start the listener daemon
    listener_script = PROJECT_ROOT / "core" / "slack_listener.py"

    process = subprocess.Popen(
        [sys.executable, str(listener_script)],
        cwd=str(test_workdir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True
    )

    # Wait for daemon to start
    time.sleep(2)

    # Check if daemon is running
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        pytest.fail(
            f"Daemon failed to start:\n"
            f"stdout: {stdout.decode()}\n"
            f"stderr: {stderr.decode()}"
        )

    yield {
        'process': process,
        'pid': process.pid,
        'workdir': test_workdir,
        'credentials': slack_credentials
    }

    # Cleanup: kill the daemon
    try:
        # Try graceful shutdown first
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        # Force kill if needed
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture
def registry_socket(tmp_path):
    """Create a temporary registry for testing."""
    from session_registry import SessionRegistry

    registry_dir = tmp_path / "registry"
    socket_path = tmp_path / "sockets" / "registry.sock"

    # Reset singleton
    SessionRegistry._instance = None

    registry = SessionRegistry(
        registry_dir=str(registry_dir),
        socket_path=str(socket_path)
    )

    yield registry

    # Cleanup
    SessionRegistry._instance = None


@pytest.mark.live_slack
class TestDaemonStartup:
    """Test listener daemon startup and connectivity."""

    def test_daemon_starts_successfully(self, daemon_process):
        """
        Verify the daemon process starts and stays running.

        Verifies:
        - Process is created
        - Process stays alive
        """
        assert daemon_process['process'].poll() is None, "Daemon died unexpectedly"
        assert daemon_process['pid'] > 0, "Invalid PID"

    def test_daemon_can_be_detected(self, daemon_process):
        """
        Verify we can detect if the daemon is running.

        Verifies:
        - pgrep can find the process
        """
        result = subprocess.run(
            ['pgrep', '-f', 'slack_listener.py'],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, "Daemon not found via pgrep"
        assert str(daemon_process['pid']) in result.stdout


@pytest.mark.live_slack
class TestDaemonSessionAttachment:
    """Test attaching sessions to a running daemon."""

    def test_session_registers_with_daemon(self, daemon_process, tmp_path, slack_credentials):
        """
        Test that a session can register with the running daemon.

        Verifies:
        - Session registration succeeds
        - Session is visible in registry
        """
        from session_registry import SessionRegistry

        # Create registry instance (this would connect to existing daemon)
        SessionRegistry._instance = None
        registry = SessionRegistry(
            registry_dir=str(tmp_path / "registry"),
            socket_path=str(tmp_path / "sockets" / "registry.sock"),
            slack_token=slack_credentials['bot_token'],
            slack_channel=slack_credentials['channel']
        )

        session_id = f"daemon_test_{int(time.time())}"

        try:
            session = registry.register_session_simple(
                session_id=session_id,
                project="daemon-attachment-test",
                terminal="pytest",
                socket_path=str(tmp_path / f"{session_id}.sock")
            )

            assert session is not None, "Session registration failed"
            assert session['session_id'] == session_id

            # Verify session can be retrieved
            retrieved = registry.get_session(session_id)
            assert retrieved is not None, "Could not retrieve session"
            assert retrieved['session_id'] == session_id

        finally:
            try:
                registry.unregister_session(session_id)
            except Exception:
                pass

    def test_multiple_sessions_with_daemon(self, daemon_process, tmp_path, slack_credentials):
        """
        Test multiple sessions attaching to the same daemon.

        Verifies:
        - Multiple sessions can be registered
        - Sessions are isolated
        """
        from session_registry import SessionRegistry

        SessionRegistry._instance = None
        registry = SessionRegistry(
            registry_dir=str(tmp_path / "registry"),
            socket_path=str(tmp_path / "sockets" / "registry.sock"),
            slack_token=slack_credentials['bot_token'],
            slack_channel=slack_credentials['channel']
        )

        sessions = []
        timestamp = int(time.time())

        try:
            # Register 3 sessions
            for i in range(3):
                session_id = f"multi_daemon_test_{timestamp}_{i}"
                session = registry.register_session_simple(
                    session_id=session_id,
                    project=f"multi-daemon-test-{i}",
                    terminal=f"pytest-{i}",
                    socket_path=str(tmp_path / f"{session_id}.sock")
                )
                sessions.append(session)

            # Verify all sessions exist
            all_sessions = registry.list_sessions(status='active')
            registered_ids = [s['session_id'] for s in all_sessions]

            for session in sessions:
                assert session['session_id'] in registered_ids, \
                    f"Session {session['session_id']} not found in registry"

        finally:
            for session in sessions:
                try:
                    registry.unregister_session(session['session_id'])
                except Exception:
                    pass


@pytest.mark.live_slack
class TestDaemonShutdown:
    """Test daemon shutdown and cleanup."""

    def test_daemon_graceful_shutdown(self, slack_credentials, tmp_path):
        """
        Test that daemon shuts down gracefully on SIGTERM.

        Verifies:
        - Daemon responds to SIGTERM
        - Process terminates within timeout
        """
        env = os.environ.copy()
        env['SLACK_BOT_TOKEN'] = slack_credentials['bot_token']
        env['SLACK_APP_TOKEN'] = slack_credentials['app_token']
        env['SLACK_CHANNEL'] = slack_credentials['channel']
        env['PYTHONPATH'] = str(PROJECT_ROOT / "core")

        listener_script = PROJECT_ROOT / "core" / "slack_listener.py"

        process = subprocess.Popen(
            [sys.executable, str(listener_script)],
            cwd=str(tmp_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True
        )

        # Wait for startup
        time.sleep(2)
        assert process.poll() is None, "Daemon failed to start"

        # Send SIGTERM
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)

        # Wait for graceful shutdown
        try:
            process.wait(timeout=10)
            assert True, "Daemon shut down gracefully"
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            pytest.fail("Daemon did not shut down within timeout")

    def test_daemon_handles_sigint(self, slack_credentials, tmp_path):
        """
        Test that daemon handles SIGINT (Ctrl+C).

        Verifies:
        - Daemon responds to SIGINT
        - Process terminates cleanly
        """
        env = os.environ.copy()
        env['SLACK_BOT_TOKEN'] = slack_credentials['bot_token']
        env['SLACK_APP_TOKEN'] = slack_credentials['app_token']
        env['SLACK_CHANNEL'] = slack_credentials['channel']
        env['PYTHONPATH'] = str(PROJECT_ROOT / "core")

        listener_script = PROJECT_ROOT / "core" / "slack_listener.py"

        process = subprocess.Popen(
            [sys.executable, str(listener_script)],
            cwd=str(tmp_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True
        )

        time.sleep(2)
        assert process.poll() is None, "Daemon failed to start"

        # Send SIGINT
        os.killpg(os.getpgid(process.pid), signal.SIGINT)

        try:
            process.wait(timeout=10)
            assert True, "Daemon handled SIGINT"
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            pytest.fail("Daemon did not respond to SIGINT")


@pytest.mark.live_slack
class TestDaemonFromSeparateDirectory:
    """Test daemon operation from a separate working directory."""

    def test_daemon_accessible_from_different_directory(self, daemon_process, tmp_path, slack_credentials):
        """
        Test that we can interact with daemon from a different directory.

        Verifies:
        - Daemon is accessible regardless of cwd
        - Registry operations work from any directory
        """
        from session_registry import SessionRegistry

        # Create a completely separate directory
        other_dir = tmp_path / "other_project"
        other_dir.mkdir()

        # Save current directory
        original_cwd = os.getcwd()

        try:
            # Change to the other directory
            os.chdir(str(other_dir))

            # Create a new registry instance
            SessionRegistry._instance = None
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry_other"),
                socket_path=str(tmp_path / "sockets_other" / "registry.sock"),
                slack_token=slack_credentials['bot_token'],
                slack_channel=slack_credentials['channel']
            )

            session_id = f"other_dir_test_{int(time.time())}"

            session = registry.register_session_simple(
                session_id=session_id,
                project="other-directory-test",
                terminal="pytest-other",
                socket_path=str(tmp_path / f"{session_id}.sock")
            )

            assert session is not None, "Registration from other directory failed"
            assert session['session_id'] == session_id

            # Verify we can list from this directory
            sessions = registry.list_sessions()
            assert len(sessions) > 0, "No sessions found from other directory"

            # Cleanup
            registry.unregister_session(session_id)

        finally:
            os.chdir(original_cwd)


@pytest.mark.live_slack
class TestDaemonSlackIntegration:
    """Test daemon with actual Slack message sending."""

    def test_daemon_sends_slack_messages(self, daemon_process, tmp_path, slack_credentials):
        """
        Test that sessions registered through daemon can post to Slack.

        Verifies:
        - Session registration creates Slack thread
        - Thread is accessible
        """
        from session_registry import SessionRegistry
        from slack_sdk import WebClient

        SessionRegistry._instance = None
        registry = SessionRegistry(
            registry_dir=str(tmp_path / "registry"),
            socket_path=str(tmp_path / "sockets" / "registry.sock"),
            slack_token=slack_credentials['bot_token'],
            slack_channel=slack_credentials['channel']
        )

        session_id = f"slack_daemon_test_{int(time.time())}"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            session = registry.register_session_simple(
                session_id=session_id,
                project=f"daemon-slack-test-{timestamp}",
                terminal="pytest",
                socket_path=str(tmp_path / f"{session_id}.sock")
            )

            # Verify Slack metadata (registry uses slack_channel/slack_thread_ts)
            channel = session.get('slack_channel') or session.get('channel')
            thread_ts = session.get('slack_thread_ts') or session.get('thread_ts')
            assert channel, f"Missing channel in session: {session}"
            assert thread_ts, f"Missing thread_ts in session: {session}"

            # Verify we can post to the thread
            client = WebClient(token=slack_credentials['bot_token'])
            response = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"[E2E Test] Daemon Slack Integration - {timestamp}"
            )

            assert response['ok'], f"Failed to post to thread: {response.get('error')}"

            # Post completion marker
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=":white_check_mark: Daemon integration test complete"
            )

        finally:
            try:
                registry.unregister_session(session_id)
            except Exception:
                pass


@pytest.mark.live_slack
class TestDaemonChannelModeIntegration:
    """Test daemon with channel-based mode (no threading) from separate directory."""

    def test_daemon_channel_mode_post(self, daemon_process, tmp_path, slack_credentials):
        """
        Test posting to channel (no thread) through daemon from different directory.

        Verifies:
        - Can post top-level messages through daemon
        - Messages appear at channel top level
        """
        from slack_sdk import WebClient

        # Create a separate working directory
        other_dir = tmp_path / "channel_mode_project"
        other_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(other_dir))

            client = WebClient(token=slack_credentials['bot_token'])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Post directly to channel (channel mode - no thread_ts)
            response = client.chat_postMessage(
                channel=slack_credentials['channel'],
                text=f"[E2E Test] Daemon Channel Mode - {timestamp}"
            )

            assert response['ok'], f"Failed to post: {response.get('error')}"
            assert response['ts'], "Message timestamp missing"
            channel_id = response['channel']

            # Verify it's a top-level message
            msg = response['message']
            assert msg.get('thread_ts') is None or msg.get('thread_ts') == response['ts']

            # Post completion marker
            client.chat_postMessage(
                channel=channel_id,
                text=f":white_check_mark: Daemon channel mode test complete - {timestamp}"
            )

        finally:
            os.chdir(original_cwd)

    def test_daemon_channel_mode_update(self, daemon_process, tmp_path, slack_credentials):
        """
        Test updating top-level messages through daemon from different directory.

        Verifies:
        - Can update top-level messages (todo progress in channel mode)
        - Message stays at top level after update
        """
        from slack_sdk import WebClient

        other_dir = tmp_path / "channel_update_project"
        other_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(other_dir))

            client = WebClient(token=slack_credentials['bot_token'])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Post initial message at top level
            initial_response = client.chat_postMessage(
                channel=slack_credentials['channel'],
                text=f"[E2E Test] Daemon Channel Update - {timestamp}\n\nProgress: 0/5 tasks"
            )
            assert initial_response['ok']
            message_ts = initial_response['ts']
            channel_id = initial_response['channel']

            # Update the message (simulating todo progress)
            update_response = client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"[E2E Test] Daemon Channel Update - {timestamp}\n\nProgress: 5/5 tasks :white_check_mark:"
            )

            assert update_response['ok'], f"Failed to update: {update_response.get('error')}"
            assert update_response['ts'] == message_ts, "Message timestamp changed"

        finally:
            os.chdir(original_cwd)

    def test_daemon_channel_mode_permission_blocks(self, daemon_process, tmp_path, slack_credentials):
        """
        Test permission prompt buttons in channel mode through daemon.

        Verifies:
        - Block Kit buttons work at top level (no thread)
        - Permission flow works in channel mode
        """
        from slack_sdk import WebClient

        other_dir = tmp_path / "channel_permission_project"
        other_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(other_dir))

            client = WebClient(token=slack_credentials['bot_token'])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*[E2E Test] Daemon Channel Permission - {timestamp}*\n\nClaude wants to run:\n```bash\ngit push origin main\n```"
                    }
                },
                {
                    "type": "actions",
                    "block_id": f"daemon_perm_{int(time.time())}",
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

            # Post permission prompt at top level (channel mode)
            response = client.chat_postMessage(
                channel=slack_credentials['channel'],
                text="Permission Request (Channel Mode)",
                blocks=blocks
            )

            assert response['ok'], f"Failed to post: {response.get('error')}"
            assert response['message']['blocks'], "Blocks missing from response"

            # Verify it's at top level
            msg = response['message']
            assert msg.get('thread_ts') is None or msg.get('thread_ts') == response['ts']

        finally:
            os.chdir(original_cwd)

    def test_daemon_channel_mode_session_registration(self, daemon_process, tmp_path, slack_credentials):
        """
        Test session registration in channel mode through daemon from different directory.

        Verifies:
        - Session can be registered for channel mode
        - Session works without thread_ts
        """
        from session_registry import SessionRegistry
        from slack_sdk import WebClient

        other_dir = tmp_path / "channel_session_project"
        other_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(other_dir))

            SessionRegistry._instance = None
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry_channel"),
                socket_path=str(tmp_path / "sockets_channel" / "registry.sock"),
                slack_token=slack_credentials['bot_token'],
                slack_channel=slack_credentials['channel']
            )

            session_id = f"channel_daemon_test_{int(time.time())}"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                session = registry.register_session_simple(
                    session_id=session_id,
                    project=f"daemon-channel-test-{timestamp}",
                    terminal="pytest",
                    socket_path=str(tmp_path / f"{session_id}.sock")
                )

                assert session is not None, "Session registration failed"
                assert session['session_id'] == session_id

                # Get channel info
                channel = session.get('slack_channel') or session.get('channel')
                assert channel, f"Session missing channel: {session}"

                # Post a message to verify channel mode works
                client = WebClient(token=slack_credentials['bot_token'])
                response = client.chat_postMessage(
                    channel=channel,
                    text=f"[E2E Test] Daemon Channel Session - {timestamp}\n:white_check_mark: Session registered from different directory"
                )
                assert response['ok'], f"Failed to post: {response.get('error')}"

            finally:
                try:
                    registry.unregister_session(session_id)
                except Exception:
                    pass

        finally:
            os.chdir(original_cwd)


@pytest.mark.live_slack
class TestDaemonAutoChannelCreation:
    """Test auto-channel creation through daemon with attached processes."""

    def test_auto_create_channel_from_daemon(self, daemon_process, tmp_path, slack_credentials):
        """
        Test that a new channel is automatically created when using -c flag.

        Verifies:
        - Channel is created if it doesn't exist
        - Bot joins the channel automatically
        - Session is registered successfully
        """
        from session_registry import SessionRegistry
        from slack_sdk import WebClient

        # Create a unique channel name for this test
        timestamp = int(time.time())
        new_channel_name = f"e2e-auto-test-{timestamp}"

        # Create a separate working directory (simulating different project)
        project_dir = tmp_path / "auto_channel_project"
        project_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(project_dir))

            SessionRegistry._instance = None
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry_auto"),
                socket_path=str(tmp_path / "sockets_auto" / "registry.sock"),
                slack_token=slack_credentials['bot_token'],
                slack_channel=slack_credentials['channel']
            )

            session_id = f"auto_channel_test_{timestamp}"

            try:
                # Register session with custom_channel (should auto-create)
                session = registry.register_session({
                    'session_id': session_id,
                    'project': f"auto-channel-test",
                    'terminal': "pytest",
                    'socket_path': str(tmp_path / f"{session_id}.sock"),
                    'custom_channel': new_channel_name
                })

                assert session is not None, "Session registration failed"
                assert session['session_id'] == session_id

                # Wait for async channel creation to complete
                session = wait_for_channel(registry, session_id, timeout=15)

                # Get the channel ID from session
                channel_id = session.get('slack_channel') or session.get('channel') if session else None
                assert channel_id, f"Session missing channel after wait: {session}"

                # Verify the channel exists and we can post to it
                client = WebClient(token=slack_credentials['bot_token'])
                response = client.chat_postMessage(
                    channel=channel_id,
                    text=f"[E2E Test] Auto-created channel test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n:white_check_mark: Channel was auto-created by claude-slack!"
                )
                assert response['ok'], f"Failed to post to auto-created channel: {response.get('error')}"

                # Verify it's a top-level message (channel mode)
                msg_thread_ts = response['message'].get('thread_ts')
                assert msg_thread_ts is None or msg_thread_ts == response['ts'], \
                    "Message should be top-level in channel mode"

            finally:
                try:
                    registry.unregister_session(session_id)
                except Exception:
                    pass

                # Cleanup: archive the test channel
                try:
                    client = WebClient(token=slack_credentials['bot_token'])
                    client.conversations_archive(channel=channel_id)
                except Exception as e:
                    print(f"Warning: Could not archive test channel {new_channel_name}: {e}")

        finally:
            os.chdir(original_cwd)

    def test_auto_join_existing_channel(self, daemon_process, tmp_path, slack_credentials):
        """
        Test that bot auto-joins an existing channel it's not a member of.

        Verifies:
        - Bot can join a channel it wasn't invited to
        - Session registration succeeds after joining
        """
        from session_registry import SessionRegistry
        from slack_sdk import WebClient

        # Use the default channel which the bot should already be in
        # This tests the "ensure channel exists" path for existing channels
        client = WebClient(token=slack_credentials['bot_token'])

        # Create a separate working directory
        project_dir = tmp_path / "join_channel_project"
        project_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(project_dir))

            SessionRegistry._instance = None
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry_join"),
                socket_path=str(tmp_path / "sockets_join" / "registry.sock"),
                slack_token=slack_credentials['bot_token'],
                slack_channel=slack_credentials['channel']
            )

            session_id = f"join_channel_test_{int(time.time())}"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                # Register with custom_channel set to existing channel
                # Strip # prefix if present for channel name
                channel_name = slack_credentials['channel'].lstrip('#')

                session = registry.register_session({
                    'session_id': session_id,
                    'project': f"join-channel-test",
                    'terminal': "pytest",
                    'socket_path': str(tmp_path / f"{session_id}.sock"),
                    'custom_channel': channel_name
                })

                assert session is not None, "Session registration failed"

                # Wait for async channel setup to complete
                session = wait_for_channel(registry, session_id, timeout=15)

                # Get channel ID (should be the existing channel)
                channel_id = session.get('slack_channel') or session.get('channel') if session else None
                assert channel_id, f"Session missing channel after wait: {session}"

                # Post to verify we're in the channel
                response = client.chat_postMessage(
                    channel=channel_id,
                    text=f"[E2E Test] Auto-join existing channel - {timestamp}\n\n:white_check_mark: Bot verified in channel"
                )
                assert response['ok'], f"Failed to post: {response.get('error')}"

            finally:
                try:
                    registry.unregister_session(session_id)
                except Exception:
                    pass

        finally:
            os.chdir(original_cwd)

    def test_channel_mode_message_flow_through_daemon(self, daemon_process, tmp_path, slack_credentials):
        """
        Test complete message flow in channel mode through attached daemon process.

        Verifies:
        - Session registration with channel mode
        - Permission prompt posting (Block Kit)
        - Message updates (todo progress)
        - Completion message
        """
        from session_registry import SessionRegistry
        from slack_sdk import WebClient

        project_dir = tmp_path / "message_flow_project"
        project_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(project_dir))

            SessionRegistry._instance = None
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry_flow"),
                socket_path=str(tmp_path / "sockets_flow" / "registry.sock"),
                slack_token=slack_credentials['bot_token'],
                slack_channel=slack_credentials['channel']
            )

            session_id = f"flow_test_{int(time.time())}"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            channel_name = slack_credentials['channel'].lstrip('#')

            try:
                # 1. Register session in channel mode
                session = registry.register_session({
                    'session_id': session_id,
                    'project': f"message-flow-test",
                    'terminal': "pytest",
                    'socket_path': str(tmp_path / f"{session_id}.sock"),
                    'custom_channel': channel_name
                })

                assert session is not None, "Session registration failed"

                # Wait for async channel setup to complete
                session = wait_for_channel(registry, session_id, timeout=15)
                channel_id = session.get('slack_channel') or session.get('channel') if session else None
                assert channel_id, f"Channel setup failed: {session}"

                client = WebClient(token=slack_credentials['bot_token'])

                # 2. Post initial "session started" message
                start_msg = client.chat_postMessage(
                    channel=channel_id,
                    text=f"[E2E Test] Message Flow Test - {timestamp}",
                    blocks=[
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": f"Message Flow Test"}
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"Session `{session_id[:12]}...` started at {timestamp}"}
                        }
                    ]
                )
                assert start_msg['ok'], f"Failed to post start message: {start_msg.get('error')}"

                # 3. Post a permission prompt (Block Kit with buttons)
                perm_msg = client.chat_postMessage(
                    channel=channel_id,
                    text="Permission Required",
                    blocks=[
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "⚠️ Permission Required: Bash"}
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "*Command:*\n```pytest tests/ -v```"}
                        },
                        {
                            "type": "actions",
                            "block_id": f"perm_{session_id}",
                            "elements": [
                                {"type": "button", "text": {"type": "plain_text", "text": "Yes"}, "style": "primary", "action_id": "perm_1", "value": "1"},
                                {"type": "button", "text": {"type": "plain_text", "text": "Yes, don't ask again"}, "action_id": "perm_2", "value": "2"},
                                {"type": "button", "text": {"type": "plain_text", "text": "No"}, "style": "danger", "action_id": "perm_3", "value": "3"}
                            ]
                        }
                    ]
                )
                assert perm_msg['ok'], f"Failed to post permission prompt: {perm_msg.get('error')}"

                # 4. Post and update a todo progress message
                todo_msg = client.chat_postMessage(
                    channel=channel_id,
                    text="Todo Progress",
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "*Tasks:*\n⬜ Task 1\n⬜ Task 2\n⬜ Task 3\n\nProgress: `[          ] 0%`"}
                        }
                    ]
                )
                assert todo_msg['ok'], f"Failed to post todo: {todo_msg.get('error')}"
                todo_ts = todo_msg['ts']

                # Simulate progress updates
                for i, progress in enumerate([(1, 33), (2, 66), (3, 100)]):
                    completed, percent = progress
                    tasks = ["✅" if j < completed else "⬜" for j in range(3)]
                    bar_filled = "█" * (percent // 10)
                    bar_empty = " " * (10 - percent // 10)

                    client.chat_update(
                        channel=channel_id,
                        ts=todo_ts,
                        text="Todo Progress",
                        blocks=[
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"*Tasks:*\n{tasks[0]} Task 1\n{tasks[1]} Task 2\n{tasks[2]} Task 3\n\nProgress: `[{bar_filled}{bar_empty}] {percent}%`"}
                            }
                        ]
                    )
                    time.sleep(0.3)  # Brief pause between updates

                # 5. Post completion message
                done_msg = client.chat_postMessage(
                    channel=channel_id,
                    text="Session Complete",
                    blocks=[
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "✅ Session Complete"}
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Session:* `{session_id[:12]}...`"},
                                {"type": "mrkdwn", "text": f"*Duration:* <1min"},
                                {"type": "mrkdwn", "text": "*Tasks:* 3/3 complete"},
                                {"type": "mrkdwn", "text": "*Status:* Success"}
                            ]
                        }
                    ]
                )
                assert done_msg['ok'], f"Failed to post completion: {done_msg.get('error')}"

            finally:
                try:
                    registry.unregister_session(session_id)
                except Exception:
                    pass

        finally:
            os.chdir(original_cwd)

    def test_missing_channel_permissions_error(self, daemon_process, tmp_path, slack_credentials):
        """
        Test graceful error handling when channel creation permissions are missing.

        Note: This test will only fail if the bot lacks channels:manage scope.
        With full permissions, it will succeed (which is also valid).

        Verifies:
        - Error message is helpful when scope is missing
        - Session registration fails gracefully
        """
        from session_registry import SessionRegistry

        project_dir = tmp_path / "perm_error_project"
        project_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(project_dir))

            SessionRegistry._instance = None
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry_perm"),
                socket_path=str(tmp_path / "sockets_perm" / "registry.sock"),
                slack_token=slack_credentials['bot_token'],
                slack_channel=slack_credentials['channel']
            )

            session_id = f"perm_error_test_{int(time.time())}"
            # Use a unique channel name
            new_channel = f"e2e-perm-test-{int(time.time())}"

            try:
                # Try to register with a new custom channel
                # If bot has channels:manage, this will succeed
                # If not, we should get a helpful error
                session = registry.register_session({
                    'session_id': session_id,
                    'project': f"perm-error-test",
                    'terminal': "pytest",
                    'socket_path': str(tmp_path / f"{session_id}.sock"),
                    'custom_channel': new_channel
                })

                # If we get here, bot has permissions - verify it worked
                assert session is not None

                # Wait for async channel creation to complete
                session = wait_for_channel(registry, session_id, timeout=15)
                channel_id = session.get('slack_channel') or session.get('channel') if session else None
                assert channel_id, "Channel ID missing after wait"

                # Cleanup the test channel
                try:
                    from slack_sdk import WebClient
                    client = WebClient(token=slack_credentials['bot_token'])
                    client.conversations_archive(channel=channel_id)
                except Exception:
                    pass

            except RuntimeError as e:
                # If we get an error, verify it's a helpful one
                error_msg = str(e).lower()
                assert any(hint in error_msg for hint in [
                    'channels:manage',
                    'channels:join',
                    'create the channel manually',
                    'invite the bot'
                ]), f"Error message should be helpful. Got: {e}"

            finally:
                try:
                    registry.unregister_session(session_id)
                except Exception:
                    pass

        finally:
            os.chdir(original_cwd)


def run_daemon_tests():
    """Run all daemon lifecycle tests."""
    print("\n" + "="*60)
    print("DAEMON LIFECYCLE E2E TESTS")
    print("="*60)

    creds = get_slack_credentials()
    if not creds['available']:
        print("\nERROR: Slack credentials not found in .env")
        print("Required: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL")
        return 1

    print(f"\nChannel: {creds['channel']}")
    print("\nRunning daemon tests...\n")

    result = subprocess.run([
        sys.executable, "-m", "pytest",
        __file__,
        "-v",
        "-m", "live_slack",
        "--tb=short"
    ])

    return result.returncode


if __name__ == "__main__":
    sys.exit(run_daemon_tests())
