# GitIgnore Fix - Prevent .claude/ Stash Conflicts

**Date:** 2025-11-13
**Issue:** `.claude/` directory getting stashed by git, causing catch-22 where hooks are missing when needed

---

## Problem Statement

When using `git stash` in a project with claude-slack configured, the `.claude/` directory gets included in the stash. This creates a catch-22:

1. User runs `git stash` → `.claude/` gets stashed
2. User runs `claude-slack` → hooks get reinstalled
3. User tries `git stash pop` → Hook fires but fails because files don't exist
4. OR: `git stash pop` runs → `.claude/` gets restored, overwriting fresh hooks

**Error message:**
```
PreToolUse:Bash hook error: [python3 $CLAUDE_PROJECT_DIR/.claude/hooks/on_pretooluse.py]:
/Library/Frameworks/Python.framework/Versions/3.10/bin/python3: can't open file
'/Users/danielbennett/codeNew/Burrfect_backend/.claude/hooks/on_pretooluse.py':
[Errno 2] No such file or directory
```

---

## Root Cause

The `.claude/` directory was not in `.gitignore`, so git tracked it and included it in stashes.

---

## Solution

Updated `bin/claude-slack` to automatically add `.claude/` to `.gitignore` on every run.

### Implementation (lines 37-55)

```bash
# Step 2a: Ensure .claude/ is in .gitignore (prevent stash conflicts)
PROJECT_GITIGNORE="$PROJECT_DIR/.gitignore"
if [ -f "$PROJECT_GITIGNORE" ]; then
    if ! grep -q "^\.claude/" "$PROJECT_GITIGNORE" && ! grep -q "^\.claude$" "$PROJECT_GITIGNORE"; then
        echo -e "${YELLOW}Adding .claude/ to .gitignore...${NC}"
        echo "" >> "$PROJECT_GITIGNORE"
        echo "# Claude Code local configuration (auto-managed by claude-slack)" >> "$PROJECT_GITIGNORE"
        echo ".claude/" >> "$PROJECT_GITIGNORE"
        echo -e "${GREEN}  ✓ .claude/ added to .gitignore${NC}"
    else
        echo -e "${GREEN}  ✓ .claude/ already in .gitignore${NC}"
    fi
else
    # No .gitignore exists, create one with .claude/
    echo -e "${YELLOW}Creating .gitignore with .claude/...${NC}"
    echo "# Claude Code local configuration (auto-managed by claude-slack)" > "$PROJECT_GITIGNORE"
    echo ".claude/" >> "$PROJECT_GITIGNORE"
    echo -e "${GREEN}  ✓ .gitignore created${NC}"
fi
```

### How It Works

1. **Checks for .gitignore** - Looks for `.gitignore` in project root
2. **Checks for .claude/ entry** - Uses grep to see if already present
3. **Adds if missing** - Appends `.claude/` with a helpful comment
4. **Creates if no .gitignore** - Creates new `.gitignore` with `.claude/` entry

---

## Additional Safeguard: Always Reinstall Missing Hooks

The existing hook installation logic (lines 101-114) already handles missing hooks:

```bash
for hook in "${HOOKS_TO_INSTALL[@]}"; do
    TEMPLATE_HOOK="$HOOKS_TEMPLATE_DIR/$hook"
    PROJECT_HOOK="$PROJECT_HOOKS_DIR/$hook"

    if [ ! -f "$PROJECT_HOOK" ]; then
        # Hook doesn't exist, copy from template
        cp "$TEMPLATE_HOOK" "$PROJECT_HOOK"
        chmod +x "$PROJECT_HOOK"
        HOOKS_INSTALLED=$((HOOKS_INSTALLED + 1))
    fi
done
```

This ensures that even if `.claude/hooks/` exists but files are missing, they will be reinstalled on every `claude-slack` run.

---

## Why .claude/ Should Be Local

The `.claude/` directory contains:

### Should NOT be versioned:
1. **settings.local.json** - Local permissions and hook configurations
   - May contain user-specific paths
   - Permission preferences vary by developer

2. **hooks/*.py** - Hook implementations
   - Managed by claude-slack installation
   - Can be different versions across developers
   - Updated independently from project code

### Why gitignore is better:
- Each developer runs `claude-slack` to install their own `.claude/` setup
- Avoids conflicts when different developers use different claude-slack versions
- Prevents stash/merge conflicts
- Follows standard practice (like `.vscode/`, `.idea/`)

---

## Migration Guide

### For Existing Projects

If `.claude/` is already tracked by git in your project:

1. **Add to .gitignore** (claude-slack now does this automatically)
   ```bash
   echo ".claude/" >> .gitignore
   ```

2. **Remove from git tracking**
   ```bash
   git rm -r --cached .claude/
   git commit -m "Remove .claude/ from version control (now in gitignore)"
   ```

3. **Next time you run claude-slack**, it will recreate `.claude/` locally

### For New Projects

Just run `claude-slack` - it will automatically:
1. Add `.claude/` to `.gitignore` (or create `.gitignore` if needed)
2. Create `.claude/` directory structure
3. Install all hook files

---

## Testing

To verify the fix works:

1. **Run claude-slack in a project**
   ```bash
   cd /path/to/your/project
   claude-slack
   ```

2. **Check .gitignore**
   ```bash
   cat .gitignore | grep -A1 "Claude Code"
   # Should show:
   # # Claude Code local configuration (auto-managed by claude-slack)
   # .claude/
   ```

3. **Verify hooks are installed**
   ```bash
   ls -la .claude/hooks/
   # Should show: on_stop.py, on_notification.py, on_pretooluse.py
   ```

4. **Test git stash**
   ```bash
   git stash
   # .claude/ should NOT be included in stash
   git stash pop
   # Should work without hook errors
   ```

---

## Files Modified

- `bin/claude-slack` (lines 37-55) - Added .gitignore management

---

## Related Issues

- HOOKS_INSTALL_FIX.md - Missing on_pretooluse.py hook
- PERMISSION_PROMPT_FIX.md - Missing option 1 in permission prompts

---

## Future Considerations

If developers need to share some Claude Code settings:

1. **Option 1:** Use a separate template file
   - Keep `.claude/settings.template.json` in git
   - Copy to `.claude/settings.local.json` during setup
   - Still gitignore the `.local.json` file

2. **Option 2:** Document recommended settings
   - Keep settings in README.md
   - Developers manually configure their local `.claude/`

3. **Option 3:** Project-specific defaults
   - Store defaults in `docs/claude-settings.md`
   - claude-slack could read and apply project defaults
