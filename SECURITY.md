# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in this project, please report it responsibly:

1. **DO NOT** create a public GitHub issue
2. Email the details to: [SECURITY_EMAIL]
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide a detailed response within 5 business days.

## Security Best Practices

### Token Management

#### Never Commit Tokens
- Slack tokens (`xoxb-*`, `xapp-*`) are authentication credentials
- NEVER commit tokens to git repositories
- Always use `.env` files for local token storage
- Use environment variables in production

#### Token Storage
```bash
# Good - tokens in .env file (gitignored)
SLACK_BOT_TOKEN=xoxb-your-token-here

# Bad - tokens in code
bot_token = "xoxb-actual-token-value"  # NEVER DO THIS
```

#### Token Rotation
If tokens are exposed:
1. Immediately revoke tokens at https://api.slack.com/apps
2. Generate new tokens
3. Update `.env` file
4. Restart all services
5. Audit logs for unauthorized access

### File Security

#### Protected Files
The following files should NEVER be committed:
- `.env` - Contains actual tokens
- `*.log` - May contain sensitive data
- `*.db` - Contains session data
- Any file with actual tokens

#### Safe Files
These files are safe to commit:
- `.env.example` - Template with placeholders
- Source code (after security audit)
- Documentation (without real tokens)

### Installation Security

#### Secure Installation Steps
1. Clone repository to local machine
2. Copy `.env.example` to `.env`
3. Add tokens to `.env`
4. Verify `.gitignore` includes `.env`
5. Set appropriate file permissions:
   ```bash
   chmod 600 ~/.claude/claude-slack/.env
   chmod 755 ~/.claude/claude-slack/bin/*
   ```

#### Permissions
- `.env` file: `600` (owner read/write only)
- Scripts: `755` (owner all, others read/execute)
- Logs: `644` (owner write, others read)

### Runtime Security

#### Process Isolation
- Each Claude session runs in its own process
- Sessions communicate via Unix sockets
- No direct network exposure

#### Data Sanitization
- Tokens are automatically redacted from logs
- User messages are not logged by default
- Session IDs are randomized UUIDs

#### Network Security
- Uses Slack's secure WebSocket connection
- All Slack communication is over TLS
- No custom network protocols

### Audit and Monitoring

#### Log Monitoring
Check logs regularly for:
- Unauthorized access attempts
- Unusual message patterns
- Socket connection errors
- Token exposure (should be redacted)

```bash
# Check for exposed tokens in logs
grep -E "xoxb-|xapp-" /tmp/*.log
# Should return nothing or show redacted tokens only
```

#### Active Session Monitoring
```bash
# List active sessions
sqlite3 /tmp/claude_sessions/registry.db "SELECT * FROM sessions;"

# Check for stale sessions
sqlite3 /tmp/claude_sessions/registry.db "SELECT * FROM sessions WHERE updated_at < datetime('now', '-24 hours');"
```

### Development Security

#### Pre-commit Checks
Before committing code:
1. Run secret scanning:
   ```bash
   # Search for tokens
   grep -r "xoxb-" . --exclude-dir=.git
   grep -r "xapp-" . --exclude-dir=.git
   ```

2. Verify .gitignore:
   ```bash
   git status  # Should not show .env or *.log
   git check-ignore -v .env  # Should show it's ignored
   ```

3. Review changes:
   ```bash
   git diff --cached  # Review what will be committed
   ```

#### Code Review Guidelines
- No hardcoded tokens
- No user-specific paths
- No sensitive data in comments
- Proper error handling
- Input validation

### Dependency Security

#### Python Dependencies
- Keep dependencies updated
- Review dependency licenses
- Monitor for security advisories

```bash
# Check for outdated packages
pip list --outdated

# Audit dependencies
pip-audit  # If pip-audit is installed
```

### Incident Response

#### If Tokens Are Exposed
1. **Immediate Actions**:
   - Revoke compromised tokens
   - Generate new tokens
   - Update all installations

2. **Investigation**:
   - Review access logs
   - Check for unauthorized usage
   - Identify exposure source

3. **Remediation**:
   - Remove tokens from exposed location
   - Update security procedures
   - Notify affected users

4. **Prevention**:
   - Add additional checks
   - Update documentation
   - Review security practices

### Security Checklist

#### Before Deployment
- [ ] Tokens in .env only
- [ ] .env in .gitignore
- [ ] No tokens in code
- [ ] No tokens in logs
- [ ] Permissions set correctly
- [ ] Dependencies updated
- [ ] Security scan completed

#### Regular Maintenance
- [ ] Weekly: Check logs for anomalies
- [ ] Monthly: Review active sessions
- [ ] Quarterly: Update dependencies
- [ ] Annually: Security audit

### Tools and Resources

#### Security Scanning Tools
```bash
# Gitleaks - scan for secrets
brew install gitleaks
gitleaks detect --source .

# TruffleHog - find secrets
pip install truffleHog
trufflehog --regex --entropy=False .
```

#### Useful Commands
```bash
# Find files with potential secrets
find . -type f -exec grep -l "xoxb\|xapp\|secret\|token\|key" {} \;

# Check file permissions
ls -la ~/.claude/claude-slack/.env

# Monitor real-time log changes
tail -f /tmp/slack_listener.log | grep -v "xoxb\|xapp"
```

## Security Contact

For security concerns, contact: [SECURITY_EMAIL]

## Acknowledgments

We appreciate responsible disclosure of security vulnerabilities.