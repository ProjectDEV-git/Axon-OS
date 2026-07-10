# Security Hardening Guide for Axon OS

This document provides security best practices for Axon OS deployment and development.

## Table of Contents
- [Input Validation](#input-validation)
- [D-Bus Security](#d-bus-security)
- [Service Isolation](#service-isolation)
- [Data Protection](#data-protection)
- [Network Security](#network-security)
- [Dependency Management](#dependency-management)

---

## Input Validation

### Model Name Validation

**Risk:** Malicious model names could cause code injection or resource exhaustion.

**Implementation:**
```python
def _validate_model_name(model_name: str) -> bool:
    """Validate model name to prevent injection."""
    if not model_name or len(model_name) > 256:
        return False
    # Allow alphanumeric, dash, dot, colon
    return bool(re.match(r'^[a-zA-Z0-9:._-]+$', model_name))
```

**Audit:**
- ✅ Length check (max 256 chars)
- ✅ Regex whitelist (no special chars or spaces)
- ✅ No shell metacharacters (`$`, `;`, `|`, backticks)
- ✅ No path traversal (`../`)

### Prompt Validation

**Risk:** Unbounded prompts could cause DoS (out-of-memory) or expensive inference.

**Implementation:**
```python
def _validate_prompt(prompt: str, max_length: int = 10000) -> bool:
    """Validate prompt to prevent DoS."""
    return bool(prompt and len(prompt) <= max_length)
```

**Audit:**
- ✅ Non-empty check
- ✅ Length limit (10KB default)
- ✅ No binary data check (should be UTF-8)

### Context Validation

**Risk:** Malicious context injection could trick AI into exposing sensitive data.

**Best Practice:**
```python
def _sanitize_context(context: dict) -> dict:
    """Remove sensitive fields from context."""
    SENSITIVE_KEYS = {'password', 'token', 'secret', 'api_key', 'ssh_key'}
    sanitized = {}
    for key, value in context.items():
        if any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS):
            continue
        if isinstance(value, str) and len(value) < 1000:
            sanitized[key] = value
    return sanitized
```

---

## D-Bus Security

### Method Signature Strictness

**Current:** Methods use type-safe signatures (`sss`, `b`, etc.)

**Audit Checklist:**
- ✅ All D-Bus methods have explicit in/out signatures
- ✅ No `as` (array of strings) without length limit
- ✅ No `a{sv}` (dictionary of variants) without schema

### Rate Limiting

**Implementation:**
```python
class RateLimiter:
    """Token bucket rate limiter for D-Bus methods."""
    def allow(self, identifier: str) -> bool:
        """Allow request if under rate limit."""
        # Implement cleanup of old requests
        # Return False if limit exceeded
```

**Configuration:**
```python
self.rate_limiter = RateLimiter(
    rate=1000,           # 1000 requests
    window_seconds=60    # per minute
)
```

**Recommended Limits per Method:**
- `Generate()`: 100 req/min per client (prevent model spam)
- `ListModels()`: 1000 req/min (cheap operation)
- `PullModel()`: 10 req/min (long-running, rate-limit heavily)
- `CreateConversation()`: 100 req/min

### D-Bus Policy Configuration

**File:** `/etc/dbus-1/system.d/org.axonos.Brain.conf` or `~/.local/share/dbus-1/services/`

**Hardened Example:**
```xml
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <!-- Only allow user's own session -->
  <policy user="@SERVICEDIR@">
    <allow own="org.axonos.Brain"/>
    <allow receive_sender="org.freedesktop.DBus"
           receive_interface="org.freedesktop.DBus.Properties"/>
    <allow send_interface="org.freedesktop.DBus.Properties"/>
  </policy>
  
  <!-- Restrict method calls -->
  <policy context="default">
    <allow user="@USERNAME@" send_destination="org.axonos.Brain"
           send_interface="org.axonos.Brain"
           send_member="Generate" />
    <allow user="@USERNAME@" send_destination="org.axonos.Brain"
           send_interface="org.axonos.Brain"
           send_member="ListModels" />
    <!-- Deny other methods for now -->
    <deny send_destination="org.axonos.Brain" />
  </policy>
</busconfig>
```

**Audit:**
- ✅ Deny-by-default (explicit allowlist)
- ✅ User-restricted (no system-wide access)
- ✅ Method-level ACLs
- ✅ No wildcard permissions

---

## Service Isolation

### Systemd Service Hardening

**File:** `~/.config/systemd/user/axon-brain.service`

**Hardened Configuration:**
```ini
[Unit]
Description=Axon Brain AI Service
Documentation=https://github.com/ProjectDEV-git/Axon-OS/docs

[Service]
Type=dbus
BusName=org.axonos.Brain
ExecStart=python3 /usr/local/bin/axon-brain-service.py

# Security hardening
NoNewPrivileges=true
PrivateTmp=yes
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths=%h/.axon

# Resource limits
MemoryLimit=512M
CPUQuota=50%

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=axon-brain

[Install]
WantedBy=default.target
```

**Explanation:**
- `NoNewPrivileges`: Prevents privilege escalation
- `PrivateTmp`: Each service gets isolated `/tmp`
- `ProtectHome=read-only`: Can't modify home except `.axon`
- `ProtectSystem=strict`: Most of `/usr`, `/etc` read-only
- `MemoryLimit`: Prevents resource exhaustion DoS
- `CPUQuota`: Limits CPU to 50% to prevent hogging

### Process Capabilities

**Recommended:** Drop all Linux capabilities (not needed for AI inference)

```bash
# Verify capabilities
getcap /usr/local/bin/axon-brain-service.py

# Should output: (no capabilities set)
```

### Firewall Rules

**If running Ollama locally, no action needed** (localhost-only).

**If exposing Ollama (not recommended):**
```bash
# Only allow Brain Service's UID
sudo ufw allow from <brain-service-uid> to 127.0.0.1 port 11434

# Or block externally
sudo ufw default deny incoming
sudo ufw allow 22/tcp  # SSH only
```

---

## Data Protection

### Conversation Storage

**Encryption at Rest:**

✅ **Currently:** SQLite database at `~/.axon/conversations.db`

❌ **Risk:** Plaintext on disk if filesystem not encrypted

**Recommendation:**
```python
from cryptography.fernet import Fernet

# Enable user-level encryption
def _get_cipher():
    """Get Fernet cipher for conversation encryption."""
    key_file = AXON_DIR / "encryption.key"
    if not key_file.exists():
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        key_file.chmod(0o600)  # Read-only by user
    return Fernet(key_file.read_bytes())

# Encrypt before storing
cipher = _get_cipher()
encrypted = cipher.encrypt(prompt.encode())
```

**Audit:**
- Key file permissions: `chmod 600` (user-only)
- Encryption before SQLite storage
- Decryption on read

### Temporary Files

**Risks:**
- `/tmp` is world-readable by default
- Prompts/responses leak to other users

**Best Practice:**
```python
import tempfile

# Create secure temp file (mode 0o600)
with tempfile.NamedTemporaryFile(
    dir=AXON_DIR,
    mode='w+',
    delete=False
) as f:
    f.write(sensitive_data)
    temp_file = f.name

# Clean up explicitly
os.unlink(temp_file)
```

### Clipboard Integration

**Risk:** Clipboard contains sensitive data, accessible to any app.

**Best Practice:**
- ⚠️ Limit clipboard context sharing to current workspace only
- Document that clipboard is not encrypted
- Let users opt-in to clipboard sharing

```python
def get_clipboard_safe():
    """Get clipboard only if user has enabled it."""
    if not config.get("enable_clipboard_context", False):
        return ""
    # ... get clipboard
```

---

## Network Security

### Ollama HTTP Security

**Current:** `http://localhost:11434` (unencrypted, localhost-only)

**Audit:**
- ✅ Localhost-only (not exposed to network)
- ✅ No authentication needed (assumes single user)
- ❌ No HTTPS encryption

**If external access needed:**

```bash
# Create self-signed certificate
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365

# Configure Ollama with TLS
OLLAMA_HTTPS=1 ollama serve
```

**Or use reverse proxy with mTLS:**
```bash
# nginx with client certificate verification
```

### API Rate Limiting

**Implementation:**
```python
# Per-method limits (see D-Bus Security section)
# Example: Limit Generate() to 100 calls/min per user

def _check_rate_limit(self, method: str, caller: str) -> bool:
    key = f"{caller}:{method}"
    if not self.rate_limiter.allow(key):
        logger.warning("Rate limit exceeded: %s", key)
        raise dbus.exceptions.DBusException(
            f"Rate limit exceeded for {method}"
        )
    return True
```

---

## Dependency Management

### Pinned Versions

**File:** `requirements.txt` and `requirements-dev.txt`

**Audit:**
- ✅ All packages have exact pinned versions (e.g., `dbus-python==1.3.2`)
- ✅ No floating versions (`>=`, `~=`)
- ✅ Regular security updates (monthly review)

**Check for vulnerabilities:**
```bash
# Install safety
pip install safety

# Scan dependencies
safety check --json > security_report.json
```

### Dependency Licensing

**Compliance:**
- ✅ All dependencies GPL-3.0 compatible
- ✅ No LGPL (different license type)
- ❌ No proprietary dependencies

**Audit:**
```bash
# List licenses
pip-licenses --format=csv > licenses.csv
```

### Supply Chain Security

**Best Practices:**
- ✅ Use `pip install --require-hashes` to verify packages
- ✅ Regular `pip audit` for known vulnerabilities
- ❌ Don't install from untrusted mirrors

```bash
# Generate hashes for requirements
pip install pip-tools
pip-compile --generate-hashes requirements.in
```

---

## Vulnerability Reporting

**Security issues should not be disclosed publicly.** Please email:
- projectdev.hq@gmail.com
- Or use GitHub's [Security Advisory](https://docs.github.com/en/code-security/security-advisories) feature

**Include:**
1. Description of vulnerability
2. Steps to reproduce
3. Impact assessment
4. Proposed fix (optional)

---

## Security Checklist

### Before Production Release

- [ ] All user inputs validated against regex whitelist
- [ ] Rate limiting implemented on all D-Bus methods
- [ ] D-Bus policy file configured (deny-by-default)
- [ ] systemd service hardened (ProtectSystem, MemoryLimit, etc.)
- [ ] Conversation database encrypted at rest (if storing sensitive data)
- [ ] No hardcoded credentials or API keys
- [ ] Third-party dependencies scanned with `pip audit`
- [ ] No debugging code or verbose logging in production
- [ ] CHANGELOG documents security patches
- [ ] Security contact information provided
- [ ] README mentions "no cloud dependency" and privacy model

### Regular Maintenance

- [ ] Weekly: `pip audit` for new vulnerabilities
- [ ] Monthly: Dependency version review
- [ ] Quarterly: Security audit of D-Bus interfaces
- [ ] Quarterly: Review system resource limits

