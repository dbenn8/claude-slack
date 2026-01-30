"""
Slack message formatter for Claude-Slack integration.

Converts queue events into Slack mrkdwn formatted messages.
Handles permissions, questions, thinking blocks, tool calls/results, and plans.
"""

import re
from typing import List, Optional, Dict, Any


class SlackFormatter:
    """
    Format Claude events into Slack mrkdwn messages.

    Supports both compact and expanded formats for different event types.
    Uses Slack mrkdwn syntax (NOT standard markdown).
    """

    # Emoji number mapping for response options
    EMOJI_NUMBERS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣']

    # Event type emojis
    EMOJI_PERMISSION = '🔐'
    EMOJI_QUESTION = '❓'
    EMOJI_THINKING = '💭'
    EMOJI_TOOL_CALL = '🔧'
    EMOJI_TOOL_SUCCESS = '✅'
    EMOJI_TOOL_ERROR = '❌'
    EMOJI_PLAN = '📋'

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize formatter with optional config.

        Args:
            config: Optional configuration dict for format preferences
        """
        self.config = config or {}

    def format_event(self, event: Dict[str, Any], format_type: str = 'expanded') -> str:
        """
        Format any event type into Slack mrkdwn.

        Args:
            event: Event dict with {"type": "permission", "payload": {...}}
            format_type: "compact" or "expanded"

        Returns:
            Formatted Slack mrkdwn string
        """
        event_type = event.get('type', '')
        payload = event.get('payload', {})
        expanded = (format_type == 'expanded')

        formatters = {
            'permission': self.format_permission,
            'question': self.format_question,
            'thinking': self.format_thinking,
            'tool_call': self.format_tool_call,
            'tool_result': self.format_tool_result,
            'plan': self.format_plan,
        }

        formatter = formatters.get(event_type)
        if formatter:
            return formatter(payload, expanded=expanded)

        # Fallback for unknown event types
        return f"Unknown event type: {event_type}"

    def format_permission(self, payload: Dict[str, Any], expanded: bool = True) -> str:
        """
        Format permission prompt.

        Args:
            payload: Permission event payload
            expanded: If True, show full format. If False, show compact.

        Returns:
            Formatted permission message
        """
        tool_name = payload.get('tool_name', 'Unknown Tool')
        command = payload.get('command', '')
        description = payload.get('description', '')
        options = payload.get('options', ['Allow', 'Deny', 'Always Allow'])

        if not expanded:
            # Compact format
            return f"{self.EMOJI_PERMISSION} Permission needed: *{tool_name}*"

        # Expanded format
        lines = [
            f"{self.EMOJI_PERMISSION} *Permission Required: {tool_name}*",
            "",
        ]

        if command:
            lines.append(f"*Command:* `{self.escape_mrkdwn(command)}`")

        if description:
            lines.append(f"*Description:* {self.escape_mrkdwn(description)}")

        if command or description:
            lines.append("")

        lines.append("*Options:*")
        for i, option in enumerate(options[:len(self.EMOJI_NUMBERS)]):
            emoji = self.EMOJI_NUMBERS[i]
            lines.append(f"{emoji} {option}")

        lines.append("")
        lines.append("_Reply with number to respond_")

        return "\n".join(lines)

    def format_question(self, payload: Dict[str, Any], expanded: bool = True) -> str:
        """
        Format AskUserQuestion event.

        Args:
            payload: Question event payload
            expanded: If True, show full format. If False, show compact.

        Returns:
            Formatted question message
        """
        question_text = payload.get('question', 'Claude needs your input')
        options = payload.get('options', [])

        if not expanded:
            # Compact format
            return f"{self.EMOJI_QUESTION} Question: {self.truncate_text(question_text, 50)}"

        # Expanded format
        lines = [
            f"{self.EMOJI_QUESTION} *Claude needs your input*",
            "",
            f"*{self.escape_mrkdwn(question_text)}*",
            "",
        ]

        for i, option in enumerate(options[:len(self.EMOJI_NUMBERS)]):
            emoji = self.EMOJI_NUMBERS[i]

            # Option can be a string or dict with label/description
            if isinstance(option, dict):
                label = option.get('label', f'Option {i+1}')
                description = option.get('description', '')
                if description:
                    lines.append(f"{emoji} *{label}* - {self.escape_mrkdwn(description)}")
                else:
                    lines.append(f"{emoji} *{label}*")
            else:
                lines.append(f"{emoji} {self.escape_mrkdwn(str(option))}")

        lines.append("")
        lines.append("_Reply with number_")

        return "\n".join(lines)

    def format_thinking(self, payload: Dict[str, Any], expanded: bool = False) -> str:
        """
        Format thinking block.

        Args:
            payload: Thinking event payload
            expanded: If True, show full text. If False, show preview.

        Returns:
            Formatted thinking message
        """
        text = payload.get('text', '')
        word_count = self.count_words(text)

        if not expanded:
            # Compact format - show preview
            preview = self.truncate_text(text, 60)
            return f"{self.EMOJI_THINKING} Thinking ({word_count} words): {preview}..."

        # Expanded format - show full text
        return f"{self.EMOJI_THINKING} *Thinking* ({word_count} words)\n\n{text}"

    def format_tool_call(self, payload: Dict[str, Any], expanded: bool = False) -> str:
        """
        Format single tool call.

        Args:
            payload: Tool call event payload
            expanded: If True, show details. If False, show compact.

        Returns:
            Formatted tool call message
        """
        tool_name = payload.get('tool_name', 'unknown')
        tool_input = payload.get('input', {})

        if not expanded:
            # Compact format
            return f"{self.EMOJI_TOOL_CALL} {tool_name}"

        # Expanded format - show tool name and formatted input
        lines = [f"{self.EMOJI_TOOL_CALL} *Tool: {tool_name}*"]

        # Format input parameters
        if tool_input:
            formatted_input = self._format_tool_input(tool_input)
            lines.append(formatted_input)

        return "\n".join(lines)

    def format_tool_batch(self, tools: List[Dict[str, Any]], duration_seconds: float) -> str:
        """
        Format batched tool calls (compact only).

        Args:
            tools: List of tool call dicts
            duration_seconds: Total execution duration

        Returns:
            Formatted tool batch message
        """
        # Count occurrences of each tool
        tool_counts: Dict[str, int] = {}
        for tool in tools:
            tool_name = tool.get('tool_name', 'unknown')
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Format as "tool1 x2, tool2 x3"
        tool_parts = []
        for tool_name, count in tool_counts.items():
            if count > 1:
                tool_parts.append(f"{tool_name} x{count}")
            else:
                tool_parts.append(tool_name)

        tools_str = ", ".join(tool_parts)
        duration_str = f"{duration_seconds:.1f}"

        return f"{self.EMOJI_TOOL_CALL} Tools ({duration_str}s): {tools_str}"

    def format_tool_result(self, payload: Dict[str, Any], expanded: bool = False) -> str:
        """
        Format tool result.

        Args:
            payload: Tool result event payload
            expanded: If True, show details. If False, show compact.

        Returns:
            Formatted tool result message
        """
        tool_name = payload.get('tool_name', 'unknown')
        is_error = payload.get('is_error', False)
        content = payload.get('content', '')

        if is_error:
            # Error result
            error_msg = self.truncate_text(content, 100)
            return f"{self.EMOJI_TOOL_ERROR} {tool_name} failed: {self.escape_mrkdwn(error_msg)}"

        # Success result
        if not expanded:
            # Compact format - show summary
            line_count = content.count('\n') + 1 if content else 0
            summary = self.truncate_text(content, 40)
            return f"{self.EMOJI_TOOL_SUCCESS} {tool_name} completed: `{self.escape_mrkdwn(summary)}` ({line_count} lines)"

        # Expanded format - could show more details
        return f"{self.EMOJI_TOOL_SUCCESS} *{tool_name}* completed\n```\n{content[:500]}\n```"

    def format_plan(self, payload: Dict[str, Any], expanded: bool = True) -> str:
        """
        Format plan approval.

        Args:
            payload: Plan event payload
            expanded: If True, show full format. If False, show compact.

        Returns:
            Formatted plan message
        """
        plan_summary = payload.get('summary', 'Claude has prepared a plan')
        options = payload.get('options', ['Auto-accept edits', 'Manual approval', 'Keep planning'])

        if not expanded:
            # Compact format
            return f"{self.EMOJI_PLAN} Plan ready"

        # Expanded format
        lines = [
            f"{self.EMOJI_PLAN} *Plan Ready for Approval*",
            "",
            self.escape_mrkdwn(plan_summary),
            "",
        ]

        for i, option in enumerate(options[:len(self.EMOJI_NUMBERS)]):
            emoji = self.EMOJI_NUMBERS[i]
            lines.append(f"{emoji} {option}")

        lines.append("")
        lines.append("_Reply with number_")

        return "\n".join(lines)

    def _format_tool_input(self, tool_input: Dict[str, Any]) -> str:
        """
        Format tool input parameters for display.

        Args:
            tool_input: Tool input parameters dict

        Returns:
            Formatted input string
        """
        lines = []
        for key, value in tool_input.items():
            # Truncate long values
            if isinstance(value, str) and len(value) > 100:
                value = self.truncate_text(value, 100)
            lines.append(f"• *{key}:* `{self.escape_mrkdwn(str(value))}`")

        return "\n".join(lines) if lines else "_(no parameters)_"

    @staticmethod
    def truncate_text(text: str, max_chars: int, suffix: str = '...') -> str:
        """
        Truncate text to max_chars, adding suffix if truncated.

        Args:
            text: Text to truncate
            max_chars: Maximum character length
            suffix: Suffix to add if truncated (default: '...')

        Returns:
            Truncated text
        """
        if len(text) <= max_chars:
            return text

        return text[:max_chars - len(suffix)] + suffix

    @staticmethod
    def count_words(text: str) -> int:
        """
        Count words in text.

        Args:
            text: Text to count words in

        Returns:
            Word count
        """
        return len(text.split())

    @staticmethod
    def escape_mrkdwn(text: str) -> str:
        """
        Escape special mrkdwn characters in user content.

        Prevents user input from being interpreted as formatting.
        Slack mrkdwn special characters: * _ ` < > & |

        Args:
            text: Text to escape

        Returns:
            Escaped text
        """
        # Escape characters that have special meaning in Slack mrkdwn
        # Note: We're being conservative and escaping common ones
        replacements = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
        }

        for char, escaped in replacements.items():
            text = text.replace(char, escaped)

        return text
