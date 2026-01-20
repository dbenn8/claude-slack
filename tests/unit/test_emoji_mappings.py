"""
Unit tests for emoji mapping consistency across modules.

Validates that emoji mappings used in on_pretooluse.py and slack_listener.py
are consistent and use the same indexing convention (0-indexed storage).

This prevents bugs where emoji-to-option mappings could get out of sync between
the hook that displays questions and the listener that processes responses.
"""

import sys
from pathlib import Path

import pytest

# Add core and hooks directories to path
core_path = Path(__file__).parent.parent.parent / "core"
hooks_path = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(core_path))
sys.path.insert(0, str(hooks_path))


class TestEmojiMappingConsistency:
    """Tests for consistency of emoji mappings across modules."""

    def test_slack_listener_emoji_map_structure(self):
        """Verify ASKUSER_EMOJI_MAP has the expected structure."""
        from slack_listener import ASKUSER_EMOJI_MAP

        # Should have both emoji name and unicode formats
        assert "one" in ASKUSER_EMOJI_MAP
        assert "two" in ASKUSER_EMOJI_MAP
        assert "three" in ASKUSER_EMOJI_MAP
        assert "four" in ASKUSER_EMOJI_MAP
        assert "1️⃣" in ASKUSER_EMOJI_MAP
        assert "2️⃣" in ASKUSER_EMOJI_MAP
        assert "3️⃣" in ASKUSER_EMOJI_MAP
        assert "4️⃣" in ASKUSER_EMOJI_MAP

    def test_slack_listener_emoji_map_values_are_strings(self):
        """Verify ASKUSER_EMOJI_MAP values are string indices (0-indexed)."""
        from slack_listener import ASKUSER_EMOJI_MAP

        for emoji, index in ASKUSER_EMOJI_MAP.items():
            assert isinstance(index, str), f"Index for emoji '{emoji}' should be string"
            assert index in ['0', '1', '2', '3'], f"Index '{index}' should be 0-3"

    def test_slack_listener_emoji_to_index_mapping(self):
        """Verify correct emoji-to-index mapping in slack_listener."""
        from slack_listener import ASKUSER_EMOJI_MAP

        # Verify 1-indexed display maps to 0-indexed storage
        assert ASKUSER_EMOJI_MAP['one'] == '0'
        assert ASKUSER_EMOJI_MAP['two'] == '1'
        assert ASKUSER_EMOJI_MAP['three'] == '2'
        assert ASKUSER_EMOJI_MAP['four'] == '3'

        # Verify unicode emoji variants map the same way
        assert ASKUSER_EMOJI_MAP['1️⃣'] == '0'
        assert ASKUSER_EMOJI_MAP['2️⃣'] == '1'
        assert ASKUSER_EMOJI_MAP['3️⃣'] == '2'
        assert ASKUSER_EMOJI_MAP['4️⃣'] == '3'

    def test_emoji_numbers_constants_in_pretooluse(self):
        """Verify EMOJI_NUMBERS constant exists in on_pretooluse."""
        # This is a functional test that calls the format_question_for_slack function
        # and verifies the emoji numbers are present
        from on_pretooluse import format_question_for_slack

        test_question = {
            "question": "Which option?",
            "options": [
                {"label": "Option 1", "description": "First option"},
                {"label": "Option 2", "description": "Second option"},
                {"label": "Option 3", "description": "Third option"},
            ],
            "multiSelect": False
        }

        output = format_question_for_slack(test_question, 0, 1)

        # Should contain emoji numbers in the output
        assert "1️⃣" in output
        assert "2️⃣" in output
        assert "3️⃣" in output

    def test_emoji_display_order_matches_index_mapping(self):
        """Verify that displayed emoji order matches index mapping."""
        from slack_listener import ASKUSER_EMOJI_MAP
        from on_pretooluse import format_question_for_slack

        # The emoji numbers displayed should match the mapping
        test_question = {
            "question": "Which?",
            "options": [
                {"label": "A", "description": ""},
                {"label": "B", "description": ""},
            ],
            "multiSelect": False
        }

        output = format_question_for_slack(test_question, 0, 1)

        # Extract lines to find emoji usage
        lines = output.split('\n')
        emoji_lines = [line for line in lines if line.startswith(('1️⃣', '2️⃣', '3️⃣', '4️⃣'))]

        # Should have emoji lines matching options count
        assert len(emoji_lines) >= 2, "Should have emoji lines for each option"

        # First emoji should be 1️⃣ (displays option 1, which is index 0)
        assert emoji_lines[0].startswith('1️⃣'), "First option should show 1️⃣"

        # Second emoji should be 2️⃣ (displays option 2, which is index 1)
        if len(emoji_lines) > 1:
            assert emoji_lines[1].startswith('2️⃣'), "Second option should show 2️⃣"

    def test_emoji_map_covers_supported_options(self):
        """Verify emoji map supports up to 4 options."""
        from slack_listener import ASKUSER_EMOJI_MAP

        # Should support options 0-3 (displayed as 1-4)
        expected_indices = {'0', '1', '2', '3'}
        actual_indices = set(ASKUSER_EMOJI_MAP.values())

        assert expected_indices == actual_indices, \
            f"Emoji map should cover indices 0-3, got {actual_indices}"

    def test_emoji_name_and_unicode_variants_consistent(self):
        """Verify emoji name and unicode variants map to same index."""
        from slack_listener import ASKUSER_EMOJI_MAP

        # Name and unicode variants should map identically
        emoji_pairs = [
            ('one', '1️⃣'),
            ('two', '2️⃣'),
            ('three', '3️⃣'),
            ('four', '4️⃣'),
        ]

        for name_emoji, unicode_emoji in emoji_pairs:
            name_index = ASKUSER_EMOJI_MAP.get(name_emoji)
            unicode_index = ASKUSER_EMOJI_MAP.get(unicode_emoji)

            assert name_index == unicode_index, \
                f"Emoji variants mismatch: '{name_emoji}' -> {name_index}, " \
                f"'{unicode_emoji}' -> {unicode_index}"

    def test_no_duplicate_indices(self):
        """Verify each index appears only once (no mapping conflicts)."""
        from slack_listener import ASKUSER_EMOJI_MAP

        indices = list(ASKUSER_EMOJI_MAP.values())
        unique_indices = set(indices)

        # We expect 4 indices (0, 1, 2, 3) but 8 emoji entries (name + unicode variants)
        assert len(unique_indices) == 4, \
            f"Should have 4 unique indices, got {len(unique_indices)}"

        # Count how many emojis map to each index
        for idx in ['0', '1', '2', '3']:
            count = sum(1 for v in indices if v == idx)
            assert count == 2, \
                f"Each index should have 2 emoji variants (name + unicode), " \
                f"index {idx} has {count}"


class TestEmojiIndexingConvention:
    """Tests for the 1-indexed display vs 0-indexed storage convention."""

    def test_option_numbering_is_1_indexed_for_display(self):
        """User sees option 1, 2, 3, 4 (1-indexed)."""
        from on_pretooluse import format_question_for_slack

        test_question = {
            "question": "Pick one:",
            "options": [
                {"label": "Option A", "description": ""},
                {"label": "Option B", "description": ""},
            ],
            "multiSelect": False
        }

        output = format_question_for_slack(test_question, 0, 1)

        # Should display "Option 1" and "Option 2" (1-indexed)
        assert "1️⃣" in output
        assert "2️⃣" in output

    def test_response_storage_is_0_indexed(self):
        """Responses stored as 0, 1, 2, 3 (0-indexed)."""
        from slack_listener import ASKUSER_EMOJI_MAP

        # When user reacts with 1️⃣, it should be stored as '0' (0-indexed)
        # This is validated by the mapping structure
        for emoji_str, index in ASKUSER_EMOJI_MAP.items():
            if emoji_str in ['1️⃣', 'one']:
                assert index == '0', "First option should map to index 0"
            elif emoji_str in ['2️⃣', 'two']:
                assert index == '1', "Second option should map to index 1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
