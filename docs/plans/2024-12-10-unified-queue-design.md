# Unified Output Queue Design Document

**Date:** 2024-12-10
**Branch:** `queue`
**Status:** Ready for Implementation

---

## Executive Summary

Build a unified queue system to capture ALL Claude Code output types and relay them to Slack with configurable filtering. This replaces the current permission-only capture with a comprehensive solution.

---

## Design Decisions (Incorporating Review Feedback)

### 1. Queue Storage: File-Based JSON (Not SQLite)

**Decision:** Stick with file-based JSON queues (same as existing PermissionDetector)

**Rationale:**
- Proven to work in existing `permission_detector.py`
- Atomic writes via temp file + `os.replace()`
- No cross-process locking complexity
- SQLite would require `fcntl` locking or WAL mode considerations

**Implementation:**
```python
# Queue file per session
/tmp/claude_output_queue_{session_id}.json

# Structure:
{
    "session_id": "abc123",
    "events": [
        {
            "id": "evt_001",
            "type": "permission",
            "priority": 100,
            "payload": {...},
            "created_at": "2024-12-10T14:30:00Z",
            "consumed": false,
            "slack_ts": null
        }
    ]
}
```

### 2. Transcript Monitoring: Hybrid Approach

**Decision:** Reuse existing patterns where possible, add lightweight monitoring for non-hook events

**The Problem:**
- Hooks fire for: permissions (on_notification), questions/plans (on_pretooluse)
- NO hook fires for: thinking blocks, auto-approved tool calls, tool results
- We need SOME proactive mechanism to detect these

**Solution - Tiered Approach:**

1. **Hook-triggered events** (permission, question, plan):
   - Use existing hook flow
   - Reuse `retry_parse_transcript()` for context enrichment
   - No new monitoring needed

2. **Non-hook events** (thinking, tool_call, tool_result):
   - Option A: **File watcher** using `watchdog` library - efficient, event-driven
   - Option B: **Piggyback on existing hooks** - when any hook fires, also scan for recent thinking/tools
   - Option C: **Lightweight poller** - only when session active, 1-2s interval

**Recommendation:** Start with Option B (piggyback). When `on_notification` or `on_pretooluse` fires, also check for recent thinking/tool events in transcript. This adds minimal complexity and covers the common case where thinking/tools precede blocking events.

**Implementation:**
```python
def on_notification_hook():
    # Existing: handle permission
    permission = parse_permission(...)

    # NEW: also check for recent non-blocking events
    recent_events = scan_transcript_for_recent_events(
        types=['thinking', 'tool_use', 'tool_result'],
        since_last_check=True
    )
    for event in recent_events:
        queue.enqueue(event)

    # Continue with permission handling...
```

**Trade-off:** Some thinking/tool events might be delayed until next hook fires. For truly real-time monitoring, we'd need Option A (file watcher) in Phase 2.

### 3. VibeTunnel Mode: Full PTY Capture (Accept Visual Trade-offs)

**Decision:** Use PTY capture in VibeTunnel mode to get full permission support

**Rationale:**
- The whole premise of this work is to fully support permissions in VibeTunnel sessions
- Permission option text (Yes/No/etc.) is ONLY available in terminal output
- Edit file TUI diffs are ONLY in terminal output
- We MUST capture PTY output to support these use cases

**Trade-off Accepted:**
- PTY capture in VibeTunnel may cause some visual artifacts (double echo, cursor issues)
- We'll use non-raw mode PTY approach to minimize visual weirdness
- Some visual quirks are acceptable in exchange for full Slack functionality
- This was the original reason for the "hybrid" approach - now we commit to it

**Implementation:**
```python
# VibeTunnel sessions get PTY capture just like regular sessions
# The wrapper will use pty.fork() with non-raw mode settings
# to minimize visual artifacts while still capturing output

class ClaudeWrapperVibeTunnel:
    def __init__(self, session_id):
        # PTY capture enabled for ALL modes
        self.permission_detector = PermissionDetector(session_id)
        self.capture_mode = 'hybrid'  # PTY + transcript

        # Use non-raw PTY mode to reduce visual weirdness
        # Accept some artifacts in exchange for full permission capture
```

**Why This Is Correct:**
- User's primary use case is mobile/AFK monitoring via VibeTunnel
- Without permission capture, Slack integration is severely limited
- Visual artifacts are a minor inconvenience vs. missing critical prompts
- We can iterate on reducing artifacts later, but functionality comes first

### 4. Priority System: FIFO for Blocking Events

**Decision:** All blocking events (permission, question, plan) share priority 100, processed FIFO

**Rationale:**
- Claude only waits for ONE blocking thing at a time
- If waiting for permission, won't ask question. If waiting for question, won't hit permission.
- They're mutually exclusive, so relative priority is moot
- FIFO ensures we process in the order Claude expects responses

**Key Insight:** We can only respond to whatever Claude is waiting for NOW. The queue must respect Claude's expected response order.

**Priority Table:**
| Event Type | Priority | Reason |
|------------|----------|--------|
| `permission` | 100 | Blocks Claude - FIFO with other blocking |
| `question` | 100 | Blocks Claude - FIFO with other blocking |
| `plan` | 100 | Blocks Claude - FIFO with other blocking |
| `tool_result` | 50 | Important context |
| `tool_call` | 40 | Informational |
| `thinking` | 20 | Verbose only |
| `output` | 10 | General text |

### 5. Slack Formatting: Slack mrkdwn + Emoji Numbers

**Decision:** Use Slack mrkdwn syntax, emoji numbers (1️⃣ 2️⃣ 3️⃣) for options, Block Kit buttons for Phase 2

**Rationale:**
- Standard markdown (`**bold**`) renders as literal asterisks in Slack - use `*bold*`
- Emoji numbers ARE good: more visible on mobile, clear visual scanning
- Users type `1` to respond, emoji `1️⃣` reinforces which option they're selecting
- Block Kit buttons for mobile can be Phase 2

**Formatting Rules:**
- Bold: `*text*` (not `**text**`)
- Code: `` `code` `` inline only
- Options: Emoji numbers `1️⃣` `2️⃣` `3️⃣`
- Message splitting: Reuse existing `split_message()` from `hooks/on_pretooluse.py` for 40K limit

### 6. Rate Limiting: Global + Per-Type

**Decision:** Global rate limiter (30/minute) PLUS per-type intervals

**Rationale:**
- Per-type intervals don't prevent Slack API rate limit errors
- Need global cap to stay under Slack's tier limits
- Blocking events bypass rate limit (always send immediately)

**Implementation:**
```python
class RateLimiter:
    def __init__(self, max_per_minute=30):
        self.max_per_minute = max_per_minute
        self.recent_sends = deque()

    def can_send(self, priority: int) -> bool:
        # Blocking events (priority >= 100) always send
        if priority >= 100:
            return True

        # Check global rate limit
        now = time.time()
        while self.recent_sends and self.recent_sends[0] < now - 60:
            self.recent_sends.popleft()

        if len(self.recent_sends) >= self.max_per_minute:
            return False

        self.recent_sends.append(now)
        return True
```

---

## Configuration System

### File Locations
- Global: `~/.claude/slack/queue_config.json`
- Project: `{project}/.claude/slack/queue_config.json`

### Config Structure (Revised)

```json
{
    "version": "1.0",

    "enabled_types": {
        "permission": true,
        "question": true,
        "plan": true,
        "tool_call": true,
        "tool_result": false,
        "thinking": false,
        "output": false
    },

    "priority": {
        "permission": 100,
        "question": 100,
        "plan": 90,
        "tool_result": 50,
        "tool_call": 40,
        "thinking": 20,
        "output": 10
    },

    "format": {
        "permission": "expanded",
        "question": "expanded",
        "plan": "expanded",
        "tool_call": "compact",
        "tool_result": "compact",
        "thinking": "compact",
        "output": "compact"
    },

    "rate_limits": {
        "global_max_per_minute": 30,
        "thinking_min_interval_ms": 5000,
        "tool_call_min_interval_ms": 1000,
        "output_min_interval_ms": 10000
    },

    "tool_batching": {
        "enabled": true,
        "window_ms": 5000,
        "min_tools": 2,
        "max_batch_size": 10
    },

    "thinking": {
        "max_preview_chars": 200,
        "upload_threshold_chars": 500
    }
}
```

**Note:** Message splitting for Slack's 40K limit is handled by existing `split_message()` function in `hooks/on_pretooluse.py`. We'll move this to a shared location (`core/slack_utils.py`) and reuse it.

### Loading Logic (Precedence High to Low)
1. Environment variables (`QUEUE_ENABLE_THINKING=0`)
2. Project config (`{project}/.claude/slack/queue_config.json`)
3. Global config (`~/.claude/slack/queue_config.json`)
4. Hardcoded defaults

### Config Validation

```python
class ConfigValidator:
    VALID_FORMATS = ["compact", "expanded"]
    VALID_TYPES = ["permission", "question", "plan", "tool_call",
                   "tool_result", "thinking", "output"]

    @staticmethod
    def validate(config: Dict) -> List[str]:
        errors = []

        # Validate format values
        for t, f in config.get("format", {}).items():
            if f not in ConfigValidator.VALID_FORMATS:
                errors.append(f"Invalid format '{f}' for {t}")

        # Validate rate limits are non-negative
        for key, val in config.get("rate_limits", {}).items():
            if isinstance(val, int) and val < 0:
                errors.append(f"Rate limit {key} cannot be negative")

        return errors
```

---

## Component Architecture

### Files to CREATE

1. **`core/output_queue.py`** - Queue operations
   - `OutputQueue` class
   - File-based JSON storage (atomic writes)
   - `enqueue(event_type, priority, payload)`
   - `dequeue_by_priority()`
   - `mark_consumed(event_id, slack_ts)`

2. **`core/queue_config.py`** - Configuration management
   - `QueueConfig` class
   - Load/merge config from multiple sources
   - Environment variable overrides
   - Validation layer

3. **`core/slack_formatter.py`** - Message formatting
   - `SlackFormatter` class
   - Event → Slack mrkdwn conversion
   - Compact vs expanded formats
   - Length truncation with file upload fallback

4. **`core/transcript_monitor.py`** - Transcript parsing for non-permission events
   - Extends existing `transcript_parser.py` patterns
   - Detect thinking blocks (`type: "thinking"`)
   - Detect tool calls (`type: "tool_use"`)
   - Detect tool results (`type: "tool_result"`)

5. **`core/rate_limiter.py`** - Slack API rate limiting
   - Global rate limiter (30/minute)
   - Per-type interval enforcement
   - Blocking events bypass

6. **`core/slack_utils.py`** - Shared Slack utilities
   - Move `split_message()` from `hooks/on_pretooluse.py`
   - Any other shared Slack helpers

### Files to MODIFY

1. **`core/claude_wrapper_hybrid.py`**
   - Initialize OutputQueue on session start
   - Pass queue reference to permission_detector
   - Add cleanup on session end

2. **`core/claude_wrapper_vibetunnel.py`**
   - Add PTY capture using non-raw mode (accept visual trade-offs)
   - Initialize PermissionDetector for full permission support
   - Mirror hybrid.py's output capture approach
   - This is the KEY change: VibeTunnel now captures PTY output

3. **`core/permission_detector.py`**
   - Write to OutputQueue instead of standalone JSON
   - Add `event_type='permission'` to enqueued items

4. **`.claude/hooks/on_notification.py`**
   - Check OutputQueue first (instant)
   - Fallback to buffer parsing (existing behavior)
   - Add transcript parsing for thinking/tool events
   - Apply rate limiting before Slack API calls
   - Use SlackFormatter for message construction

5. **`.claude/hooks/on_pretooluse.py`**
   - Enqueue questions to OutputQueue
   - Enqueue plan approvals to OutputQueue

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PRODUCERS                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  All Modes (including VibeTunnel):                                   │
│  ┌──────────────────┐                                               │
│  │PermissionDetector│ ──> PTY buffer ──> permission events          │
│  └──────────────────┘     (with full option text)                   │
│                                                                      │
│  ┌──────────────────┐                                               │
│  │ TranscriptMonitor│ ──> JSONL file ──> thinking, tool_use,        │
│  └──────────────────┘                    tool_result events          │
│                                                                      │
│  Hook-Triggered:                                                     │
│  ┌──────────────────┐                                               │
│  │   PreToolUse     │ ──> Hook event ──> question, plan events      │
│  └──────────────────┘                                               │
│                                                                      │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │      OUTPUT QUEUE        │
                    │  /tmp/claude_output_     │
                    │    queue_{session}.json  │
                    │                          │
                    │  - Atomic writes         │
                    │  - Priority ordering     │
                    │  - Consumed tracking     │
                    └──────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CONSUMER                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐    ┌──────────────┐    ┌─────────────────┐   │
│  │ on_notification  │ ──>│ RateLimiter  │ ──>│ SlackFormatter  │   │
│  │ hook             │    │              │    │                 │   │
│  └──────────────────┘    │ - Global cap │    │ - mrkdwn syntax │   │
│                          │ - Per-type   │    │ - Truncation    │   │
│                          │ - Bypass for │    │ - File upload   │   │
│                          │   blocking   │    └─────────────────┘   │
│                          └──────────────┘              │            │
│                                                        ▼            │
│                                                 ┌─────────────┐     │
│                                                 │  Slack API  │     │
│                                                 └─────────────┘     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Message Formats (Slack mrkdwn)

### Permission Prompt (Expanded)
```
:lock: *Permission Required: Bash*

*Command:* `mkdir test_dir`
*Description:* Create test directory

*Options:*
1️⃣ Yes
2️⃣ Yes, don't ask again for mkdir
3️⃣ No

_Reply with number to respond_
```

### Question (Expanded)
```
:question: *Claude needs your input*

*Which database should we use?*

1️⃣ *PostgreSQL* - Robust, full-featured
2️⃣ *SQLite* - Lightweight, serverless
3️⃣ *MongoDB* - Flexible NoSQL

_Reply with number_
```

### Thinking (Compact)
```
:thought_balloon: Thinking (245 words): Analyzing the codebase structure to find the best location...
```

### Tool Calls - Batched (Compact)
```
:wrench: Tools (5s): Read x3, Grep x2, Bash x1
```

### Tool Result (Compact)
```
:white_check_mark: Bash completed: `git status` (12 lines)
```

### Plan Mode (Expanded)
```
:clipboard: *Plan Ready for Approval*

1. Create new module
2. Add tests
3. Update docs

1️⃣ Auto-accept edits
2️⃣ Manual approval
3️⃣ Keep planning

_Reply with number_
```

### Error State
```
:x: *Tool Failed: Bash*
*Command:* `git push origin main`
*Error:* fatal: Could not read from remote repository
```

---

## Error Handling

### Slack API Failures
```python
def post_to_slack(event):
    try:
        response = client.chat_postMessage(...)
        queue.mark_consumed(event['id'], response['ts'])
    except SlackApiError as e:
        if e.response['error'] == 'rate_limited':
            # Re-queue with backoff
            queue.requeue_with_delay(event['id'], delay_seconds=60)
        else:
            log_error(f"Slack error: {e}")
            queue.mark_failed(event['id'], str(e))
```

### Queue File Corruption
```python
def load_queue():
    try:
        return json.loads(queue_path.read_text())
    except json.JSONDecodeError:
        log_error("Queue corrupted, starting fresh")
        return {"session_id": session_id, "events": []}
```

---

## Implementation Phases

### Phase 1: Core Queue (Files 1-3)
- `output_queue.py` - File-based queue operations
- `queue_config.py` - Config loading/validation
- Modify `permission_detector.py` to use OutputQueue

### Phase 2: Formatting & Rate Limiting (Files 4-5)
- `slack_formatter.py` - Slack mrkdwn formatting
- `rate_limiter.py` - Global + per-type limiting
- Modify `on_notification.py` to use formatter and limiter

### Phase 3: Transcript Monitoring
- `transcript_monitor.py` - Detect thinking/tools from JSONL
- Integrate with existing transcript_parser.py
- Add tool_result detection

### Phase 4: Hook Integration
- Modify `on_pretooluse.py` for questions/plans
- Add VibeTunnel transcript-only mode
- End-to-end testing

---

## Testing Strategy

### Unit Tests
- Queue atomic writes don't corrupt on concurrent access
- Config validation catches invalid values
- Slack formatter produces valid mrkdwn
- Rate limiter enforces global cap

### Integration Tests
- Permission detected → queued → formatted → posted to Slack
- Question from hook → queued → posted to Slack
- Tool batch window groups multiple tools
- VibeTunnel mode falls back to transcript-only

### Manual Testing
- Run Claude Code session with verbose mode
- Verify all event types appear in Slack
- Test on mobile Slack app
- Verify rate limiting under heavy load

---

## Open Questions (For Implementation)

1. **Should tool batches link to individual details?**
   - Option A: Thread replies with expansion
   - Option B: File attachment with details
   - Recommendation: Start simple, add later

2. **Message state after response?**
   - Update permission message to show "Approved" ✓
   - Requires storing `slack_ts` and using `chat_update`
   - Recommendation: Phase 2 enhancement

3. **Hot-reload config?**
   - Watch config file for changes
   - Requires watchdog or polling
   - Recommendation: Skip for v1, add later

---

## Next Steps

1. Use `superpowers:writing-plans` to create detailed implementation tasks
2. Dispatch agents to implement each phase
3. Code review after each phase
4. Integration testing before merge
