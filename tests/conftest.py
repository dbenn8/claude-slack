"""
Shared test fixtures for claude-slack tests.

Provides mock objects and temporary resources for testing
the claude-slack integration components.
"""

import json
import os
import sys
import tempfile
import socket
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest

# Add core directory to path for imports
CLAUDE_SLACK_DIR = Path(__file__).parent.parent
CORE_DIR = CLAUDE_SLACK_DIR / "core"
sys.path.insert(0, str(CORE_DIR))


# ============================================================
# Environment Fixtures
# ============================================================

@pytest.fixture
def clean_env(monkeypatch):
    """Clean environment without slack-related variables."""
    vars_to_remove = [
        'SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'SLACK_CHANNEL',
        'SLACK_SOCKET_DIR', 'REGISTRY_DB_PATH', 'SLACK_LOG_DIR',
        'CLAUDE_BIN', 'CLAUDE_SLACK_DIR', 'CLAUDE_TRANSCRIPT_PATH',
        'CLAUDE_SESSION_ID', 'CLAUDE_PROJECT_DIR'
    ]
    for var in vars_to_remove:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment with typical slack variables."""
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test-token')
    monkeypatch.setenv('SLACK_APP_TOKEN', 'xapp-test-token')
    monkeypatch.setenv('SLACK_CHANNEL', '#test-channel')
    return monkeypatch


# ============================================================
# Database Fixtures
# ============================================================

@pytest.fixture
def temp_db_path(tmp_path):
    """Temporary SQLite database path."""
    return str(tmp_path / "test_registry.db")


@pytest.fixture
def temp_registry_db(temp_db_path):
    """Temporary registry database instance."""
    from registry_db import RegistryDatabase
    db = RegistryDatabase(temp_db_path)
    yield db
    # Cleanup handled by tmp_path fixture


@pytest.fixture
def sample_session_data():
    """Sample session data for testing."""
    return {
        'session_id': 'test1234',
        'project': 'test-project',
        'project_dir': '/path/to/project',
        'terminal': 'test-terminal',
        'socket_path': '/tmp/test.sock',
        'thread_ts': '1234567890.123456',
        'channel': 'C123456',
        'permissions_channel': None,
        'slack_user_id': 'U123456',
    }


@pytest.fixture
def sample_session_data_custom_channel():
    """Sample session data for custom channel mode (no thread_ts)."""
    return {
        'session_id': 'cust5678',
        'project': 'custom-project',
        'project_dir': '/path/to/custom',
        'terminal': 'custom-terminal',
        'socket_path': '/tmp/custom.sock',
        'thread_ts': None,  # Custom channel mode
        'channel': 'test-custom-channel',
        'permissions_channel': 'test-permissions',
        'slack_user_id': 'U654321',
    }


# ============================================================
# Slack Client Fixtures
# ============================================================

@pytest.fixture
def mock_slack_client():
    """Mock Slack WebClient with common responses."""
    client = MagicMock()

    # Mock auth_test response
    client.auth_test.return_value = {
        'ok': True,
        'user_id': 'UBOT123',
        'team': 'Test Team',
        'url': 'https://test.slack.com'
    }

    # Mock chat_postMessage response
    client.chat_postMessage.return_value = {
        'ok': True,
        'ts': '1234567890.123456',
        'channel': 'C123456',
        'message': {'text': 'test'}
    }

    # Mock chat_update response
    client.chat_update.return_value = {
        'ok': True,
        'ts': '1234567890.123456',
        'channel': 'C123456'
    }

    # Mock chat_delete response
    client.chat_delete.return_value = {
        'ok': True,
        'ts': '1234567890.123456',
        'channel': 'C123456'
    }

    # Mock reactions_add response
    client.reactions_add.return_value = {'ok': True}

    # Mock conversations_info response
    client.conversations_info.return_value = {
        'ok': True,
        'channel': {
            'id': 'C123456',
            'name': 'test-channel',
            'is_channel': True
        }
    }

    # Mock conversations_history response
    client.conversations_history.return_value = {
        'ok': True,
        'messages': [
            {
                'ts': '1234567890.123456',
                'thread_ts': '1234567890.000001',
                'text': 'test message',
                'user': 'U123456'
            }
        ]
    }

    # Mock conversations_list response (for channel lookup/creation)
    client.conversations_list.return_value = {
        'ok': True,
        'channels': [
            {
                'id': 'C123456',
                'name': 'test-channel',
                'is_member': True,
                'is_channel': True
            },
            {
                'id': 'C789012',
                'name': 'default-channel',
                'is_member': True,
                'is_channel': True
            }
        ],
        'response_metadata': {
            'next_cursor': ''  # Empty string = no more pages
        }
    }

    # Mock conversations_create response (for auto-channel creation)
    client.conversations_create.return_value = {
        'ok': True,
        'channel': {
            'id': 'CNEW123',
            'name': 'new-channel',
            'is_channel': True
        }
    }

    # Mock conversations_join response (for joining channels)
    client.conversations_join.return_value = {
        'ok': True,
        'channel': {
            'id': 'C123456',
            'name': 'test-channel'
        }
    }

    return client


# ============================================================
# Transcript Fixtures
# ============================================================

@pytest.fixture
def sample_transcript_messages():
    """Sample transcript messages in JSONL format."""
    return [
        {
            'type': 'user',
            'timestamp': '2025-01-01T00:00:00Z',
            'sessionId': 'test-session-123',
            'message': {
                'content': [
                    {'type': 'text', 'text': 'Help me fix this bug'}
                ]
            }
        },
        {
            'type': 'assistant',
            'timestamp': '2025-01-01T00:00:05Z',
            'uuid': 'msg-uuid-123',
            'sessionId': 'test-session-123',
            'gitBranch': 'main',
            'message': {
                'model': 'claude-3-opus',
                'content': [
                    {'type': 'text', 'text': 'I can help you fix that bug.'},
                    {
                        'type': 'tool_use',
                        'id': 'tool-123',
                        'name': 'Read',
                        'input': {'file_path': '/path/to/file.py'}
                    }
                ],
                'usage': {
                    'input_tokens': 100,
                    'output_tokens': 50,
                    'cache_read_input_tokens': 20
                }
            }
        },
        {
            'type': 'tool_result',
            'tool_use_id': 'tool-123',
            'content': 'File content here...',
            'is_error': False
        },
        {
            'type': 'assistant',
            'timestamp': '2025-01-01T00:00:10Z',
            'uuid': 'msg-uuid-456',
            'sessionId': 'test-session-123',
            'gitBranch': 'main',
            'message': {
                'model': 'claude-3-opus',
                'content': [
                    {'type': 'text', 'text': 'I found the issue. Let me fix it.'},
                    {
                        'type': 'tool_use',
                        'id': 'tool-456',
                        'name': 'Edit',
                        'input': {
                            'file_path': '/path/to/file.py',
                            'old_string': 'bug',
                            'new_string': 'fix'
                        }
                    }
                ],
                'usage': {
                    'input_tokens': 150,
                    'output_tokens': 75
                }
            }
        }
    ]


@pytest.fixture
def sample_transcript_with_todos():
    """Sample transcript with TodoWrite calls."""
    return [
        {
            'type': 'user',
            'timestamp': '2025-01-01T00:00:00Z',
            'sessionId': 'test-session-123',
            'message': {
                'content': [
                    {'type': 'text', 'text': 'Implement feature X'}
                ]
            }
        },
        {
            'type': 'assistant',
            'timestamp': '2025-01-01T00:00:05Z',
            'uuid': 'msg-uuid-789',
            'sessionId': 'test-session-123',
            'message': {
                'model': 'claude-3-opus',
                'content': [
                    {'type': 'text', 'text': 'I will implement feature X.'},
                    {
                        'type': 'tool_use',
                        'id': 'tool-todo-1',
                        'name': 'TodoWrite',
                        'input': {
                            'todos': [
                                {'content': 'Create new file', 'status': 'completed', 'activeForm': 'Creating new file'},
                                {'content': 'Add function', 'status': 'in_progress', 'activeForm': 'Adding function'},
                                {'content': 'Write tests', 'status': 'pending', 'activeForm': 'Writing tests'}
                            ]
                        }
                    }
                ],
                'usage': {'input_tokens': 100, 'output_tokens': 50}
            }
        }
    ]


@pytest.fixture
def mock_transcript_file(tmp_path, sample_transcript_messages):
    """Create a temporary transcript JSONL file."""
    transcript_path = tmp_path / "transcript.jsonl"
    with open(transcript_path, 'w') as f:
        for msg in sample_transcript_messages:
            f.write(json.dumps(msg) + '\n')
    return str(transcript_path)


@pytest.fixture
def empty_transcript_file(tmp_path):
    """Create an empty transcript file."""
    transcript_path = tmp_path / "empty_transcript.jsonl"
    transcript_path.touch()
    return str(transcript_path)


# ============================================================
# Socket Fixtures
# ============================================================

@pytest.fixture
def temp_socket_dir(tmp_path):
    """Temporary directory for Unix sockets."""
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    return str(socket_dir)


@pytest.fixture
def mock_unix_socket(temp_socket_dir):
    """Create a mock Unix socket for testing."""
    socket_path = os.path.join(temp_socket_dir, "test.sock")

    # Create a simple echo server
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)
    server.setblocking(False)

    yield socket_path

    # Cleanup
    server.close()
    if os.path.exists(socket_path):
        os.unlink(socket_path)


# ============================================================
# Hook Input Fixtures
# ============================================================

@pytest.fixture
def sample_notification_hook_input():
    """Sample input for notification hook."""
    return {
        'session_id': 'test-session-123',
        'transcript_path': '/path/to/transcript.jsonl',
        'project_dir': '/path/to/project',
        'hook_event_name': 'Notification',
        'message': 'Claude needs permission to use Bash',
        'notification_type': 'permission_prompt'
    }


@pytest.fixture
def sample_stop_hook_input():
    """Sample input for stop hook."""
    return {
        'session_id': 'test-session-123',
        'transcript_path': '/path/to/transcript.jsonl',
        'project_dir': '/path/to/project',
        'hook_event_name': 'Stop'
    }


@pytest.fixture
def sample_pretooluse_hook_input():
    """Sample input for pre-tool use hook."""
    return {
        'session_id': 'test-session-123',
        'transcript_path': '/path/to/transcript.jsonl',
        'cwd': '/path/to/project',
        'permission_mode': 'default',
        'hook_event_name': 'PreToolUse',
        'tool_name': 'AskUserQuestion',
        'tool_input': {
            'questions': [
                {
                    'question': 'Which approach should we use?',
                    'header': 'Approach',
                    'multiSelect': False,
                    'options': [
                        {'label': 'Option A', 'description': 'Fast but risky'},
                        {'label': 'Option B', 'description': 'Slow but safe'}
                    ]
                }
            ]
        }
    }


@pytest.fixture
def sample_posttooluse_hook_input():
    """Sample input for post-tool use hook."""
    return {
        'session_id': 'test-session-123',
        'transcript_path': '/path/to/transcript.jsonl',
        'cwd': '/path/to/project',
        'permission_mode': 'default',
        'hook_event_name': 'PostToolUse',
        'tool_name': 'TodoWrite',
        'tool_input': {
            'todos': [
                {'content': 'Fix bug', 'status': 'completed', 'activeForm': 'Fixing bug'},
                {'content': 'Add tests', 'status': 'in_progress', 'activeForm': 'Adding tests'},
                {'content': 'Update docs', 'status': 'pending', 'activeForm': 'Updating docs'}
            ]
        },
        'tool_result': 'Todos have been modified successfully'
    }


# ============================================================
# ANSI Test Strings
# ============================================================

@pytest.fixture
def ansi_test_strings():
    """Strings with various ANSI escape codes for testing strip functions."""
    return {
        'bold': '\x1b[1mBold text\x1b[0m',
        'red': '\x1b[31mRed text\x1b[0m',
        'green_bg': '\x1b[42mGreen background\x1b[0m',
        'complex': '\x1b[1;31;42mComplex\x1b[0m formatting\x1b[34m here\x1b[0m',
        'cursor_move': '\x1b[2A\x1b[3CText after cursor move',
        'clear_line': '\x1b[2KCleared line',
        'no_ansi': 'Plain text without ANSI',
        'permission_prompt': '\x1b[1mClaude needs permission\x1b[0m\n1. \x1b[32mYes\x1b[0m\n2. \x1b[31mNo\x1b[0m'
    }


# ============================================================
# Registry Socket Protocol Fixtures
# ============================================================

@pytest.fixture
def registry_register_command(sample_session_data):
    """Sample REGISTER command for registry socket protocol."""
    return {
        'command': 'REGISTER',
        'data': sample_session_data
    }


@pytest.fixture
def registry_list_command():
    """Sample LIST command for registry socket protocol."""
    return {
        'command': 'LIST',
        'data': {'status': 'active'}
    }


@pytest.fixture
def registry_get_command():
    """Sample GET command for registry socket protocol."""
    return {
        'command': 'GET',
        'data': {'session_id': 'test1234'}
    }


# ============================================================
# AskUserQuestion Fixtures
# ============================================================

@pytest.fixture
def temp_response_dir(tmp_path):
    """Create temporary response directory for AskUserQuestion."""
    response_dir = tmp_path / "askuser_responses"
    response_dir.mkdir()
    return response_dir


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create temporary log directory."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


@pytest.fixture
def sample_askuser_hook_input():
    """Sample AskUserQuestion hook input."""
    return {
        'session_id': 'test-session-e2e',
        'transcript_path': '/path/to/transcript.jsonl',
        'cwd': '/test/project',
        'permission_mode': 'default',
        'hook_event_name': 'PreToolUse',
        'tool_name': 'AskUserQuestion',
        'tool_input': {
            'questions': [
                {
                    'question': 'Which database should we use?',
                    'header': 'Database',
                    'multiSelect': False,
                    'options': [
                        {'label': 'PostgreSQL', 'description': 'Relational, ACID compliant'},
                        {'label': 'MongoDB', 'description': 'Document store, flexible schema'},
                        {'label': 'Redis', 'description': 'In-memory, key-value store'}
                    ]
                }
            ]
        }
    }


@pytest.fixture
def sample_multiselect_hook_input():
    """Sample multi-select AskUserQuestion hook input."""
    return {
        'session_id': 'test-session-e2e',
        'transcript_path': '/path/to/transcript.jsonl',
        'cwd': '/test/project',
        'permission_mode': 'default',
        'hook_event_name': 'PreToolUse',
        'tool_name': 'AskUserQuestion',
        'tool_input': {
            'questions': [
                {
                    'question': 'Which features should we enable?',
                    'header': 'Features',
                    'multiSelect': True,
                    'options': [
                        {'label': 'Logging', 'description': 'Enable debug logging'},
                        {'label': 'Caching', 'description': 'Enable response caching'},
                        {'label': 'Metrics', 'description': 'Enable performance metrics'}
                    ]
                }
            ]
        }
    }


@pytest.fixture
def sample_multi_question_hook_input():
    """Sample multi-question AskUserQuestion hook input."""
    return {
        'session_id': 'test-session-e2e',
        'transcript_path': '/path/to/transcript.jsonl',
        'cwd': '/test/project',
        'permission_mode': 'default',
        'hook_event_name': 'PreToolUse',
        'tool_name': 'AskUserQuestion',
        'tool_input': {
            'questions': [
                {
                    'question': 'Which framework?',
                    'header': 'Framework',
                    'multiSelect': False,
                    'options': [
                        {'label': 'FastAPI', 'description': 'Modern Python web framework'},
                        {'label': 'Flask', 'description': 'Lightweight Python framework'}
                    ]
                },
                {
                    'question': 'Which database?',
                    'header': 'Database',
                    'multiSelect': False,
                    'options': [
                        {'label': 'PostgreSQL', 'description': 'SQL database'},
                        {'label': 'MongoDB', 'description': 'NoSQL database'}
                    ]
                }
            ]
        }
    }


@pytest.fixture
def sample_askuser_reaction_body():
    """Sample reaction event body for AskUserQuestion."""
    return {
        'event': {
            'type': 'reaction_added',
            'user': 'U123456',
            'reaction': 'one',  # 1️⃣
            'item': {
                'type': 'message',
                'channel': 'C123456',
                'ts': '1234567890.123456'
            },
            'event_ts': '1234567890.123457'
        }
    }


@pytest.fixture
def mock_ack():
    """Mock Slack ack function."""
    return MagicMock()


# ============================================================
# Slack Event Fixtures
# ============================================================

@pytest.fixture
def sample_slack_message_event():
    """Sample Slack message event."""
    return {
        'type': 'message',
        'user': 'U123456',
        'text': 'Hello Claude',
        'ts': '1234567890.123456',
        'channel': 'C123456',
        'channel_type': 'channel',
        'thread_ts': '1234567890.000001'
    }


@pytest.fixture
def sample_slack_dm_event():
    """Sample Slack direct message event."""
    return {
        'type': 'message',
        'user': 'U123456',
        'text': 'fix the bug',
        'ts': '1234567890.123456',
        'channel': 'D123456',
        'channel_type': 'im'
    }


@pytest.fixture
def sample_slack_reaction_event():
    """Sample Slack reaction event."""
    return {
        'type': 'reaction_added',
        'user': 'U123456',
        'reaction': 'one',
        'item': {
            'type': 'message',
            'channel': 'C123456',
            'ts': '1234567890.123456'
        },
        'event_ts': '1234567890.123457'
    }


@pytest.fixture
def sample_slack_button_event():
    """Sample Slack button click event."""
    return {
        'type': 'block_actions',
        'user': {
            'id': 'U123456',
            'name': 'testuser'
        },
        'channel': {
            'id': 'C123456',
            'name': 'test-channel'
        },
        'message': {
            'ts': '1234567890.123456',
            'thread_ts': '1234567890.000001'
        },
        'actions': [
            {
                'action_id': 'permission_response_1',
                'value': '1',
                'style': 'primary'
            }
        ]
    }
