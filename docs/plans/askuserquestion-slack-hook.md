# Plan: AskUserQuestion Slack Hook Implementation

**Date:** 2026-01-19
**Status:** COMPLETED ✅
**Implementation Date:** 2026-01-19

## Overview

Implement interactive AskUserQuestion handling via Slack, allowing users to respond to Claude's questions without needing terminal access.

## Problem Statement

When Claude uses `AskUserQuestion`, the current `on_pretooluse.py` hook posts a formatted message to Slack but:
1. Users cannot respond interactively - they must type in the terminal
2. Slack buttons are limited to 75 characters, too short for option labels+descriptions
3. Multi-select questions need special handling
4. The hook doesn't block/wait for a response like `on_permission_request.py` does

## Architecture Decision

**Approach:** Emoji-based selection with file-based response polling (matching `on_permission_request.py` pattern)

**Why not buttons?**
- 75 char limit truncates meaningful option text
- Variable number of options (2-4) per question
- Multi-select would need complex button state management

**Why emojis?**
- 1️⃣ 2️⃣ 3️⃣ 4️⃣ are universally understood
- No character limit on the message text explaining each option
- Multi-select: users can add multiple emoji reactions
- Consistent with existing reaction-based permission responses

## Data Flow

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Claude Code    │──────▶  PreToolUse Hook │──────▶  Slack Message  │
│ AskUserQuestion │      │ (on_pretooluse)  │      │  with options   │
└─────────────────┘      └──────────────────┘      └────────┬────────┘
                                                            │
                                                            ▼
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Claude Code    │◀─────│  Hook reads      │◀─────│  User reacts    │
│  receives       │      │  response file   │      │  with emoji     │
│  {answers: {}}  │      └──────────────────┘      └─────────────────┘
                                │
                                ▼
                         ┌──────────────────┐
                         │  Slack Listener  │
                         │  writes response │
                         │  file on react   │
                         └──────────────────┘
```

## Files to Modify

| File | Change Type | Description |
|------|-------------|-------------|
| `hooks/on_pretooluse.py` | **Major Modify** | Add blocking response wait, structured answer output |
| `core/slack_listener.py` | **Modify** | Add reaction handler for AskUserQuestion responses |
| `tests/unit/hooks/test_on_pretooluse.py` | **Modify** | Add tests for new functionality |
| `tests/unit/test_slack_listener.py` | **Modify** | Add tests for reaction handling |
| `tests/conftest.py` | **Modify** | Add fixtures for AskUserQuestion scenarios |

## Tickets

### Ticket 1: Format AskUserQuestion for Slack with Emoji Options
**Dependencies:** None
**Estimated Scope:** Small

**Goal:** Enhance message formatting to display numbered options with emoji indicators.

**Tests to write first:**
```python
# tests/unit/hooks/test_on_pretooluse.py

class TestFormatAskUserQuestionWithEmojis:
    """Test emoji-based option formatting."""

    def test_format_single_question_with_emojis(self):
        """Format question with 1️⃣ 2️⃣ 3️⃣ 4️⃣ indicators."""
        # Input: single question with 3 options
        # Expected: message with emoji numbers, full descriptions
        # Verify: "React with 1️⃣ 2️⃣ or 3️⃣"

    def test_format_multiselect_question(self):
        """Format multi-select with instruction to add multiple reactions."""
        # Input: question with multiSelect=True
        # Expected: "React with one or more: 1️⃣ 2️⃣ 3️⃣ 4️⃣"

    def test_format_question_with_descriptions(self):
        """Include full descriptions under each option."""
        # Input: options with label + description
        # Expected: "1️⃣ **Label**\n   _Description text_"

    def test_format_multiple_questions(self):
        """Handle 2-4 questions in one AskUserQuestion call."""
        # Input: questions array with 2 items
        # Expected: numbered questions with separate emoji sets
        # Q1: 1️⃣ 2️⃣  Q2: 1️⃣ 2️⃣ 3️⃣

    def test_format_includes_other_option(self):
        """Always include 'Other' option for custom text."""
        # Input: any question
        # Expected: includes "💬 Other (reply in thread)"
```

**Implementation steps:**
1. Modify `format_question_for_slack()` to use emoji numbers (1️⃣ etc.)
2. Add instruction text about reacting vs replying
3. Handle multiSelect flag in instructions
4. Add "Other" option indicator

**Definition of done:**
- All tests pass
- Slack messages show emoji-numbered options
- Multi-select questions have appropriate instructions

---

### Ticket 2: Implement Response File Protocol for AskUserQuestion
**Dependencies:** None (can parallel with Ticket 1)
**Estimated Scope:** Small

**Goal:** Define response file format and directory, matching PermissionRequest pattern.

**Tests to write first:**
```python
# tests/unit/hooks/test_on_pretooluse.py

class TestAskUserQuestionResponseProtocol:
    """Test response file read/write protocol."""

    def test_response_file_path_generation(self):
        """Generate unique response file path."""
        # Input: session_id, request_id
        # Expected: ~/.claude/slack/askuser_responses/{session}_{request}.json

    def test_response_file_format_single_select(self):
        """Response format for single selection."""
        # Input: user selected option 2
        # Expected: {"question_0": "1", "user_id": "U123", "timestamp": ...}
        # Note: "1" is 0-indexed option index as string

    def test_response_file_format_multi_select(self):
        """Response format for multiple selections."""
        # Input: user selected options 1 and 3
        # Expected: {"question_0": ["0", "2"], ...}

    def test_response_file_format_other_text(self):
        """Response format for 'Other' text input."""
        # Input: user replied with custom text
        # Expected: {"question_0": "other", "question_0_text": "custom text", ...}

    def test_response_file_format_multiple_questions(self):
        """Response format for multi-question prompts."""
        # Expected: {"question_0": "1", "question_1": "0", ...}

    def test_cleanup_response_file(self):
        """Response file deleted after reading."""
```

**Implementation steps:**
1. Define `ASKUSER_RESPONSE_DIR = ~/.claude/slack/askuser_responses/`
2. Create `get_response_file(session_id, request_id)` function
3. Define JSON response schema
4. Create `cleanup_response_file()` function

**Definition of done:**
- Response file path generation works
- Response schema handles single, multi, and "other" cases
- Cleanup function removes files after read

---

### Ticket 3: Add Reaction Handler to Slack Listener
**Dependencies:** Ticket 2 (needs response file format)
**Estimated Scope:** Medium

**Goal:** Handle emoji reactions on AskUserQuestion messages and write response files.

**Tests to write first:**
```python
# tests/unit/test_slack_listener.py

class TestAskUserQuestionReactionHandler:
    """Test reaction handling for AskUserQuestion."""

    def test_reaction_maps_emoji_to_option_index(self):
        """Map 1️⃣ 2️⃣ 3️⃣ 4️⃣ to option indices."""
        # Input: reaction "one" on AskUserQuestion message
        # Expected: option index 0

    def test_reaction_identifies_askuser_message(self):
        """Distinguish AskUserQuestion from permission messages."""
        # Need: way to identify message type (block_id prefix?)
        # Input: reaction on askuser_Q1_abc123 block
        # Expected: handled as AskUserQuestion, not permission

    def test_reaction_extracts_request_metadata(self):
        """Extract session_id, request_id from message."""
        # Metadata stored in message blocks or action values

    def test_reaction_writes_response_file(self):
        """Write response file on valid reaction."""
        # Input: 2️⃣ reaction
        # Expected: response file created with {"question_0": "1"}

    def test_reaction_ignores_invalid_emoji(self):
        """Ignore non-number emojis."""
        # Input: 👍 reaction on AskUserQuestion message
        # Expected: no response file created

    def test_reaction_handles_multiselect_accumulation(self):
        """Accumulate multiple reactions for multiSelect."""
        # Input: 1️⃣ then 3️⃣ reactions
        # Expected: response file with ["0", "2"]
        # Challenge: when to "submit"? Timeout? Explicit confirm?

    def test_updates_message_on_selection(self):
        """Update Slack message to show selection."""
        # Expected: message edited to show "Selected: Option 2"
```

**Implementation steps:**
1. Add new reaction handler for AskUserQuestion emoji pattern
2. Store message metadata (session_id, request_id, question index) in block_id
3. Map emoji names to option indices
4. Write response file on reaction
5. Update Slack message to show selection
6. Handle multi-select accumulation (with timeout or confirm reaction)

**Definition of done:**
- Emoji reactions write response files
- Multi-select accumulates selections
- Slack message updates to show selection
- Invalid emojis ignored

---

### Ticket 4: Add Thread Reply Handler for "Other" Option
**Dependencies:** Ticket 2 (needs response file format)
**Estimated Scope:** Small

**Goal:** Handle thread replies as "Other" custom text responses.

**Tests to write first:**
```python
# tests/unit/test_slack_listener.py

class TestAskUserQuestionThreadReply:
    """Test thread reply handling for 'Other' responses."""

    def test_thread_reply_to_askuser_message(self):
        """Thread reply treated as 'Other' response."""
        # Input: reply "Use a different approach" in AskUser thread
        # Expected: response file with question_0_text: "Use a different approach"

    def test_thread_reply_extracts_metadata_from_parent(self):
        """Get session/request ID from parent message."""
        # Need to fetch parent message to get block metadata

    def test_thread_reply_after_emoji_replaces_selection(self):
        """Thread reply overrides previous emoji selection."""
        # Input: user reacted 2️⃣, then replied with text
        # Expected: response file updated to "other" + text
```

**Implementation steps:**
1. In `handle_message()`, detect replies to AskUserQuestion threads
2. Fetch parent message to extract metadata
3. Write response file with "other" type and text content
4. Update message to show "Other: {text preview}"

**Definition of done:**
- Thread replies create "other" responses
- Previous emoji selections can be overridden
- Message updates to show custom response

---

### Ticket 5: Implement Blocking Wait in PreToolUse Hook
**Dependencies:** Tickets 1, 2
**Estimated Scope:** Medium

**Goal:** Make the hook wait for user response and return structured answer to Claude.

**Tests to write first:**
```python
# tests/unit/hooks/test_on_pretooluse.py

class TestAskUserQuestionBlockingWait:
    """Test blocking behavior and response handling."""

    def test_hook_waits_for_response_file(self):
        """Hook polls for response file."""
        # Setup: no response file initially
        # Action: hook starts waiting
        # Then: response file created
        # Expected: hook reads and returns response

    def test_hook_timeout_passes_through(self):
        """Hook exits 0 on timeout (pass to terminal)."""
        # Setup: no response file ever
        # Expected: after timeout, sys.exit(0)

    def test_hook_returns_structured_answer(self):
        """Hook returns answer in Claude's expected format."""
        # Input: response file with {"question_0": "1"}
        # Expected output JSON:
        # {
        #   "hookSpecificOutput": {
        #     "hookEventName": "PreToolUse",
        #     "output": {
        #       "decision": "answered",
        #       "answers": {"question_0": "Option B label"}
        #     }
        #   }
        # }

    def test_hook_returns_multiselect_answers(self):
        """Hook returns multiple selections."""
        # Input: response with ["0", "2"]
        # Expected: answers with both selected option labels

    def test_hook_returns_other_text(self):
        """Hook returns custom 'Other' text."""
        # Input: response with type "other" and text
        # Expected: answers with the custom text

    def test_hook_cleans_up_response_file(self):
        """Response file deleted after reading."""

    def test_hook_deletes_slack_message_on_response(self):
        """Clean up Slack message after user responds."""
```

**Implementation steps:**
1. Add response directory constant and creation
2. Generate unique request_id on each AskUserQuestion
3. Post to Slack with request metadata in blocks
4. Enter polling loop (similar to `on_permission_request.py`)
5. On response: parse file, build Claude output format, cleanup
6. On timeout: exit 0 to pass through to terminal
7. Delete/update Slack message on completion

**Definition of done:**
- Hook blocks until response or timeout
- Returns properly formatted answer to Claude
- Response file cleaned up
- Slack message cleaned up

---

### Ticket 6: Handle Multi-Question Prompts
**Dependencies:** Tickets 1-5
**Estimated Scope:** Medium

**Goal:** Support AskUserQuestion calls with 2-4 questions.

**Tests to write first:**
```python
# tests/unit/hooks/test_on_pretooluse.py

class TestMultiQuestionHandling:
    """Test handling of multiple questions in one prompt."""

    def test_format_multiple_questions_separately(self):
        """Each question gets its own section with emoji options."""
        # Input: 2 questions
        # Expected: Q1 header + options, divider, Q2 header + options

    def test_response_aggregates_all_answers(self):
        """Response file contains answers for all questions."""
        # Input: user answers Q1=2, Q2=1
        # Expected: {"question_0": "1", "question_1": "0"}

    def test_partial_response_waits_for_completion(self):
        """Don't return until all questions answered."""
        # Input: only Q1 answered
        # Expected: continue waiting for Q2

    def test_message_shows_progress(self):
        """Update message to show which questions answered."""
        # After Q1 answered: "✓ Question 1 | ○ Question 2"
```

**Implementation steps:**
1. Format questions with clear visual separation
2. Use distinct block_ids for each question (askuser_Q0_xxx, askuser_Q1_xxx)
3. Track answered questions in response file
4. Only return when all questions have answers
5. Update message to show progress

**Definition of done:**
- Multi-question prompts formatted clearly
- All questions must be answered before returning
- Progress shown in message updates

---

### Ticket 7: E2E Integration Tests
**Dependencies:** Tickets 1-6
**Estimated Scope:** Medium

**Goal:** Verify complete flow works end-to-end.

**Tests to write first:**
```python
# tests/e2e/test_askuserquestion_flow.py

class TestAskUserQuestionE2E:
    """End-to-end tests for AskUserQuestion via Slack."""

    def test_single_question_emoji_response(self):
        """Complete flow: hook → Slack → reaction → response → Claude."""
        # 1. Simulate PreToolUse hook with AskUserQuestion input
        # 2. Verify Slack message posted
        # 3. Simulate emoji reaction
        # 4. Verify response file created
        # 5. Verify hook returns correct output

    def test_multiselect_multiple_reactions(self):
        """Multi-select with multiple emoji reactions."""

    def test_other_thread_reply(self):
        """'Other' response via thread reply."""

    def test_timeout_falls_back_to_terminal(self):
        """Timeout results in pass-through to terminal."""

    def test_multi_question_complete_flow(self):
        """Multiple questions all answered."""
```

**Implementation steps:**
1. Create test fixtures for complete scenarios
2. Mock Slack API responses
3. Simulate reaction/reply events
4. Verify file creation and cleanup
5. Verify hook output format

**Definition of done:**
- All E2E tests pass
- Flow works without real Slack connection
- Edge cases (timeout, partial, other) covered

---

## Open Questions

### Q1: Multi-select Submission Timing
**Problem:** When does user "submit" multi-select answers?
**Options:**
- A) Timeout (e.g., 10s after last reaction)
- B) Explicit "done" reaction (✅)
- C) Immediate on each reaction (update response file progressively)

**Recommendation:** Option C with timeout - each reaction updates response, but hook only reads after configurable quiet period.

### Q2: Message Cleanup
**Problem:** Should we delete or update the message after response?
**Options:**
- A) Delete message entirely
- B) Update to show "Answered: Option X"
- C) Update and collapse (show summary only)

**Recommendation:** Option B - Update to show selection, keep for audit trail.

### Q3: Concurrent Questions
**Problem:** What if Claude asks another question before first is answered?
**Options:**
- A) Queue questions, handle one at a time
- B) Allow concurrent, track separately
- C) Reject new questions while pending

**Recommendation:** Option B - Each request has unique ID, can handle concurrently.

## Test Commands

```bash
# Run unit tests
cd /var/home/perry/.claude/claude-slack
python -m pytest tests/unit/hooks/test_on_pretooluse.py -v

# Run with coverage
python -m pytest tests/unit/hooks/test_on_pretooluse.py --cov=hooks --cov-report=term-missing

# Run E2E tests (after implementation)
python -m pytest tests/e2e/test_askuserquestion_flow.py -v
```

## Definition of Done (Overall)

- [ ] All unit tests pass
- [ ] All E2E tests pass
- [ ] Single-question prompts work via emoji reaction
- [ ] Multi-select prompts work via multiple reactions
- [ ] "Other" responses work via thread reply
- [ ] Multi-question prompts work
- [ ] Timeout falls back to terminal gracefully
- [ ] Slack messages cleaned up after response
- [ ] Response files cleaned up
- [ ] Documentation updated in `docs/research/cli-options-parsing.md`
