# Testing Guide

This document provides a comprehensive overview of the test suite for claude-slack.

## Table of Contents

- [Quick Start](#quick-start)
- [Test Categories](#test-categories)
- [Running Tests](#running-tests)
- [Test Infrastructure](#test-infrastructure)
- [Unit Tests](#unit-tests)
- [Integration Tests](#integration-tests)
- [E2E Tests](#e2e-tests)
- [Live Slack Tests](#live-slack-tests)
- [Failure Modes](#failure-modes)
- [CI/CD](#cicd)
- [Writing New Tests](#writing-new-tests)

## Quick Start

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run all tests (excludes live Slack tests)
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=core --cov-report=html

# Run live Slack tests (requires .env credentials)
pytest tests/e2e/test_live_slack.py -v -m live_slack
```

## Test Categories

| Category | Count | Description |
|----------|-------|-------------|
| Unit | ~180 | Test individual functions/classes in isolation |
| Integration | ~30 | Test component interactions |
| E2E | ~25 | Test complete workflows |
| Live Slack | 13 | Test real Slack API (requires credentials) |
| Daemon Lifecycle | 12 | Test daemon start/stop, session attachment, channel mode |

**Total: 267+ tests** (242 offline + 25 live Slack)

## Running Tests

### Standard Test Run

```bash
# All tests (live_slack excluded by default)
pytest tests/ -v

# Specific category
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/e2e/ -v

# Specific file
pytest tests/unit/test_config.py -v

# Specific test
pytest tests/unit/test_config.py::TestGetSocketDir::test_get_socket_dir_default -v
```

### With Coverage

```bash
pytest tests/ --cov=core --cov=.claude/hooks --cov-report=html
open htmlcov/index.html
```

### Live Slack Tests

```bash
# Non-interactive (verifies API calls work)
pytest tests/e2e/test_live_slack.py -v -m live_slack

# Interactive (prompts for human verification)
pytest tests/e2e/test_live_slack.py -v -s -m live_slack --interactive
```

### Cross-Platform Testing

```bash
# Using devcontainer (Debian)
docker build -f .devcontainer/Dockerfile --build-arg BASE_IMAGE=debian -t claude-slack-test .
docker run claude-slack-test

# Using devcontainer (Fedora)
docker build -f .devcontainer/Dockerfile --build-arg BASE_IMAGE=fedora -t claude-slack-test .
docker run claude-slack-test
```

## Test Infrastructure

### Directory Structure

```
tests/
├── conftest.py                    # Shared fixtures
├── unit/
│   ├── test_config.py             # Configuration tests
│   ├── test_registry_db.py        # Database tests
│   ├── test_session_registry.py   # Session management tests
│   ├── test_transcript_parser.py  # Transcript parsing tests
│   ├── test_slack_listener.py     # Slack listener tests
│   └── hooks/
│       ├── test_on_notification.py
│       ├── test_on_stop.py
│       ├── test_on_pretooluse.py
│       └── test_on_posttooluse.py
├── integration/
│   ├── test_registry_listener.py  # Registry <-> Listener
│   ├── test_wrapper_registry.py   # Wrapper <-> Registry
│   └── test_hooks_registry.py     # Hooks <-> Registry
└── e2e/
    ├── test_session_lifecycle.py  # Full session workflows
    ├── test_permission_flow.py    # Permission handling
    ├── test_multi_session.py      # Multi-session routing
    ├── test_failure_recovery.py   # Error recovery
    ├── test_live_slack.py         # Real Slack API tests
    └── test_daemon_lifecycle.py   # Daemon start/stop tests
```

### Key Fixtures (conftest.py)

| Fixture | Description |
|---------|-------------|
| `mock_slack_client` | Mocked Slack WebClient |
| `temp_registry_db` | Temporary SQLite database |
| `temp_socket_dir` | Temporary directory for Unix sockets |
| `sample_session_data` | Sample session for threaded mode |
| `sample_session_data_custom_channel` | Sample session for custom channel mode |
| `mock_transcript_file` | Sample JSONL transcript |
| `clean_env` | Clears environment variables |

## Unit Tests

### test_config.py

Tests configuration loading and path resolution.

| Test | What It Tests |
|------|---------------|
| `test_get_socket_dir_default` | Default socket directory path |
| `test_get_socket_dir_env_override` | SLACK_SOCKET_DIR override |
| `test_get_registry_db_path_default` | Default database path |
| `test_get_claude_bin_autodetect` | Claude binary detection |
| `test_get_config_value_from_default` | Config value resolution |

**Failure Modes:**
- Environment variable not expanded correctly
- Path doesn't exist when expected
- Wrong default values

### test_registry_db.py

Tests SQLite database operations.

| Test | What It Tests |
|------|---------------|
| `test_create_session` | Session record creation |
| `test_get_session_exists` | Session retrieval by ID |
| `test_update_session` | Field updates |
| `test_get_by_thread` | Lookup by Slack thread_ts |
| `test_get_by_project_dir` | Lookup by project directory |
| `test_cleanup_old_sessions` | Stale session cleanup |
| `test_concurrent_reads` | WAL mode concurrency |
| `test_session_scope_rollback` | Transaction rollback |

**Failure Modes:**
- Database file can't be created
- WAL mode not enabled
- Concurrent access deadlock
- Schema migration fails

### test_session_registry.py

Tests the SessionRegistry singleton and socket server.

| Test | What It Tests |
|------|---------------|
| `test_init_creates_directories` | Directory creation on init |
| `test_init_singleton_pattern` | Only one instance exists |
| `test_register_session` | Session registration |
| `test_register_session_rejects_duplicates` | Duplicate session handling |
| `test_process_command_register` | Socket protocol REGISTER |
| `test_process_command_list` | Socket protocol LIST |
| `test_server_start_stop` | Socket server lifecycle |

**Failure Modes:**
- Directory creation fails (permissions)
- Socket already in use
- Database initialization before directory creation

### test_transcript_parser.py

Tests JSONL transcript parsing.

| Test | What It Tests |
|------|---------------|
| `test_load_valid_jsonl` | Basic transcript loading |
| `test_load_malformed_json` | Graceful handling of bad JSON |
| `test_get_latest_assistant_response` | Extract last response |
| `test_get_all_tool_calls` | Extract tool usage |
| `test_get_todo_status` | Parse TodoWrite results |
| `test_get_modified_files` | Extract Write/Edit targets |
| `test_get_rich_summary` | Composite summary |

**Failure Modes:**
- File not found
- Malformed JSON lines
- Missing expected fields
- Empty transcript

### test_slack_listener.py

Tests Slack event handling and message routing.

| Test | What It Tests |
|------|---------------|
| `test_get_socket_for_thread` | Thread -> socket lookup |
| `test_get_socket_for_channel` | Channel -> socket lookup |
| `test_send_response_registry_mode` | Send via registry socket |
| `test_send_response_file_fallback` | Fallback to file |
| `test_handle_message_threaded` | Threaded message routing |
| `test_handle_reaction_approve` | Reaction approval handling |
| `test_handle_permission_button` | Button click handling |

**Failure Modes:**
- Socket not found
- Session not in registry
- Message routing to wrong session

### Hook Tests

#### test_on_notification.py

| Test | What It Tests |
|------|---------------|
| `test_strip_ansi_codes` | ANSI escape removal |
| `test_split_message` | Message chunking for Slack |
| `test_parse_permission_prompt` | 2-option vs 3-option detection |
| `test_determine_context_dangerous` | Dangerous command detection |
| `test_extract_target_bash_sudo` | Extract sudo command target |
| `test_post_permission_card` | Block Kit button structure |

**Failure Modes:**
- ANSI codes not fully stripped
- Wrong option count detected
- Dangerous command not flagged

#### test_on_stop.py

| Test | What It Tests |
|------|---------------|
| `test_format_rich_summary` | Summary block generation |
| `test_post_response_chunked` | Long response splitting |
| `test_self_healing` | Missing metadata recovery |

#### test_on_pretooluse.py

| Test | What It Tests |
|------|---------------|
| `test_format_question_for_slack` | AskUserQuestion formatting |
| `test_format_multiselect` | Multi-select question handling |

#### test_on_posttooluse.py

| Test | What It Tests |
|------|---------------|
| `test_format_todo_progress` | Progress bar generation |
| `test_update_existing_message` | chat.update flow |
| `test_filter_todowrite_only` | Skip non-TodoWrite tools |

## Integration Tests

### test_registry_listener.py

Tests interaction between SessionRegistry and SlackListener.

| Test | What It Tests |
|------|---------------|
| `test_listener_queries_registry_by_thread` | Thread-based lookup |
| `test_listener_routes_to_correct_socket` | Message delivery |
| `test_multi_session_routing` | Correct session selection |

**Failure Modes:**
- Wrong session receives message
- Socket connection fails
- Registry returns stale data

### test_wrapper_registry.py

Tests interaction between wrapper scripts and SessionRegistry.

| Test | What It Tests |
|------|---------------|
| `test_wrapper_registers_session` | Session creation |
| `test_wrapper_registers_claude_uuid` | UUID session linking |
| `test_wrapper_health_check` | Socket ping/pong |
| `test_wrapper_persists_data_across_restarts` | Persistence |

**Failure Modes:**
- Session not persisted
- UUID not linked to wrapper
- Health check timeout

### test_hooks_registry.py

Tests interaction between hook scripts and SessionRegistry.

| Test | What It Tests |
|------|---------------|
| `test_hook_queries_session_by_id` | Metadata retrieval |
| `test_hook_stores_todo_message_ts` | Message TS storage |
| `test_hook_self_heals_from_wrapper` | Missing data recovery |

**Failure Modes:**
- Hook can't find session
- Message TS not persisted
- Self-healing fails

## E2E Tests

### test_session_lifecycle.py

| Test | What It Tests |
|------|---------------|
| `test_full_session_start_to_end` | Complete workflow |
| `test_session_cleanup_on_exit` | Socket/DB cleanup |
| `test_session_custom_channel` | Custom channel mode |
| `test_session_permissions_channel` | Separate permissions channel |

### test_permission_flow.py

| Test | What It Tests |
|------|---------------|
| `test_permission_prompt_appears` | Block Kit buttons |
| `test_permission_approve` | Yes button -> "1" |
| `test_permission_approve_remember` | Yes-remember -> "2" |
| `test_permission_deny` | No button -> "3" |
| `test_permission_via_reaction` | Emoji reactions |
| `test_multiple_permissions_sequence` | Sequential prompts |

### test_multi_session.py

| Test | What It Tests |
|------|---------------|
| `test_two_sessions_different_channels` | Channel isolation |
| `test_two_sessions_same_channel_threads` | Thread isolation |
| `test_three_concurrent_sessions` | Concurrency |
| `test_no_cross_contamination` | Message isolation |

### test_failure_recovery.py

| Test | What It Tests |
|------|---------------|
| `test_listener_restart_recovery` | Listener restart |
| `test_registry_restart_recovery` | Registry restart |
| `test_stale_socket_cleanup` | Orphan socket handling |
| `test_monitor_detects_idle_session` | Idle detection |

## Live Slack Tests

These tests require real Slack credentials in `.env`:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL=C...  # or SLACK_TEST_CHANNEL
```

### TestLiveThreadedMode

| Test | What It Tests |
|------|---------------|
| `test_create_thread` | Thread creation API |
| `test_permission_prompt_blocks` | Block Kit buttons |
| `test_message_update` | chat.update API |
| `test_add_reaction` | reactions.add API |

### TestLiveCustomChannelMode

| Test | What It Tests |
|------|---------------|
| `test_post_without_thread` | Top-level messages |
| `test_permission_prompt_channel_mode` | Permission buttons at top level |
| `test_message_update_channel_mode` | Updating top-level messages |
| `test_multiple_top_level_messages` | Multiple messages in sequence |
| `test_session_registration_channel_mode` | Custom channel session registration |

### TestLiveSessionRegistry

| Test | What It Tests |
|------|---------------|
| `test_session_registration_creates_thread` | Full registration |

### TestLiveErrorHandling

| Test | What It Tests |
|------|---------------|
| `test_invalid_channel_error` | API error handling |
| `test_message_not_found_error` | Update error handling |

### TestLiveRateLimits

| Test | What It Tests |
|------|---------------|
| `test_multiple_messages_succeed` | Rapid message sending |

## Daemon Lifecycle Tests

These tests verify daemon startup, session attachment, and shutdown behavior.

```bash
# Run daemon lifecycle tests
pytest tests/e2e/test_daemon_lifecycle.py -v -m live_slack
```

### TestDaemonStartup

| Test | What It Tests |
|------|---------------|
| `test_daemon_starts_successfully` | Daemon process starts and stays running |
| `test_daemon_can_be_detected` | pgrep can find the daemon process |

### TestDaemonSessionAttachment

| Test | What It Tests |
|------|---------------|
| `test_session_registers_with_daemon` | Session registration with running daemon |
| `test_multiple_sessions_with_daemon` | Multiple sessions attach to same daemon |

### TestDaemonShutdown

| Test | What It Tests |
|------|---------------|
| `test_daemon_graceful_shutdown` | Daemon responds to SIGTERM |
| `test_daemon_handles_sigint` | Daemon handles Ctrl+C (SIGINT) |

### TestDaemonFromSeparateDirectory

| Test | What It Tests |
|------|---------------|
| `test_daemon_accessible_from_different_directory` | Registry operations work from any cwd |

### TestDaemonSlackIntegration

| Test | What It Tests |
|------|---------------|
| `test_daemon_sends_slack_messages` | Sessions through daemon can post to Slack (threaded mode) |

### TestDaemonChannelModeIntegration

| Test | What It Tests |
|------|---------------|
| `test_daemon_channel_mode_post` | Post top-level messages from different directory |
| `test_daemon_channel_mode_update` | Update top-level messages (todo progress) |
| `test_daemon_channel_mode_permission_blocks` | Permission buttons at top level |
| `test_daemon_channel_mode_session_registration` | Session registration in channel mode |

## Failure Modes

### Common Failures

| Failure | Cause | Solution |
|---------|-------|----------|
| `unable to open database file` | Directory doesn't exist | Fixed in session_registry.py - directories created before DB |
| `channel_not_found` | Invalid channel ID | Verify SLACK_CHANNEL in .env |
| `not_in_channel` | Bot not invited | Invite bot to channel |
| `message_not_found` | Invalid message TS | Verify message exists |
| `socket connection refused` | Server not running | Start registry server |
| `Permission denied` | Socket permissions | Check socket directory permissions |

### Test-Specific Failures

| Test | Potential Failure | Fix |
|------|-------------------|-----|
| `test_concurrent_reads` | Deadlock | WAL mode should prevent |
| `test_session_scope_rollback` | No rollback | Transaction scope issue |
| `test_stale_socket_cleanup` | Socket not detected | Check file existence logic |
| `test_monitor_detects_idle_session` | Datetime comparison | Parse ISO string to datetime |

## CI/CD

### GitHub Actions

The `.github/workflows/test.yml` runs tests on:
- **Debian** (bookworm-slim container)
- **Fedora** (40 container)
- **Ubuntu** with Python 3.10, 3.11, 3.12

Coverage is uploaded for Python 3.11 builds.

### Local Docker Testing

```bash
# Debian
docker build -f .devcontainer/Dockerfile \
  --build-arg BASE_IMAGE=debian \
  -t claude-slack-debian .
docker run claude-slack-debian

# Fedora
docker build -f .devcontainer/Dockerfile \
  --build-arg BASE_IMAGE=fedora \
  -t claude-slack-fedora .
docker run claude-slack-fedora
```

## Writing New Tests

### Guidelines

1. **Use existing fixtures** from `conftest.py`
2. **Mock external services** (Slack API, file system)
3. **Test one thing per test**
4. **Use descriptive names**: `test_<what>_<expected>`
5. **Add docstrings** explaining what the test verifies

### Example

```python
def test_session_cleanup_removes_socket(self, temp_registry_db, temp_socket_dir):
    """
    Session cleanup removes the Unix socket file.

    Verifies:
    - Socket file exists before cleanup
    - Socket file is removed after cleanup
    - Database status is updated to 'ended'
    """
    # Setup
    socket_path = temp_socket_dir / "test.sock"
    socket_path.touch()

    session_data = {..., 'socket_path': str(socket_path)}
    temp_registry_db.create_session(session_data)

    # Action
    cleanup_session(session_data['session_id'])

    # Verify
    assert not socket_path.exists()
    session = temp_registry_db.get_session(session_data['session_id'])
    assert session['status'] == 'ended'
```

### Adding New Test Files

1. Create file in appropriate directory (`unit/`, `integration/`, `e2e/`)
2. Import fixtures from `conftest.py`
3. Add appropriate pytest markers (`@pytest.mark.e2e`, etc.)
4. Run tests to verify: `pytest tests/path/to/new_test.py -v`
