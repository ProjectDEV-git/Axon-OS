# Troubleshooting Guide for Axon OS

This guide helps diagnose and resolve common issues with Axon OS.

## Table of Contents
- [Service Issues](#service-issues)
- [GPU & Hardware](#gpu--hardware)
- [Ollama Connection](#ollama-connection)
- [D-Bus Communication](#d-bus-communication)
- [Performance](#performance)
- [Development Issues](#development-issues)

---

## Service Issues

### Brain Service Not Starting

**Symptoms:** `org.axonos.Brain` service unavailable, AI features don't work.

**Diagnosis:**
```bash
# Check if service is running
systemctl --user status axon-brain

# View recent logs
journalctl --user -u axon-brain -n 50

# Check if name is already claimed
dbus-send --session --print-reply \
    --dest=org.freedesktop.DBus \
    /org/freedesktop/DBus \
    org.freedesktop.DBus.ListNames | grep axonos
```

**Solutions:**

1. **Service file missing or misconfigured:**
   ```bash
   # Ensure service file exists
   cat ~/.config/systemd/user/axon-brain.service
   
   # Reload systemd
   systemctl --user daemon-reload
   systemctl --user restart axon-brain
   ```

2. **Port conflict (Ollama running on different port):**
   ```bash
   # Check OLLAMA_BASE_URL in brain_service.py
   grep "OLLAMA_BASE_URL" services/axon-brain/brain_service.py
   
   # Verify Ollama is on localhost:11434
   curl http://localhost:11434/api/tags
   ```

3. **Permission issues:**
   ```bash
   # Check if .axon directory is writable
   ls -la ~/.axon/
   
   # Ensure user can read config
   chmod 755 ~/.axon/
   ```

### Context Service Not Responding

**Symptoms:** Window tracking not working, desktop context not available.

**Diagnosis:**
```bash
# Check Context service
systemctl --user status axon-context
journalctl --user -u axon-context -n 50

# Test D-Bus method
dbus-send --session --print-reply \
    --dest=org.axonos.Context \
    /org/axonos/Context \
    org.axonos.Context.GetActiveContext
```

**Solutions:**

1. **Shell extension not running:**
   ```bash
   # Check GNOME Shell extension
   gnome-extensions list | grep axon
   
   # Enable extension if disabled
   gnome-extensions enable axon-spaces
   
   # Restart shell (Alt+F2, type 'r')
   ```

2. **D-Bus interface mismatch:**
   ```bash
   # Verify interface definition
   cat services/axon-context/org.axonos.Context.conf
   
   # Check D-Bus permissions
   ls -la /etc/dbus-1/system.d/
   ```

---

## GPU & Hardware

### GPU Not Detected

**Symptoms:** Hardware profiler shows no GPU, Ollama uses CPU only, slow inference.

**Diagnosis:**
```bash
# Check GPU availability
lspci | grep -E "(NVIDIA|AMD|Intel)" | grep -i vga

# Check NVIDIA driver
nvidia-smi  # For NVIDIA
rocm-smi    # For AMD

# Run hardware profiler directly
python3 services/axon-brain/hardware_profiler.py
```

**Solutions:**

1. **NVIDIA GPU not recognized:**
   ```bash
   # Install NVIDIA drivers
   sudo apt install nvidia-driver-<version>
   
   # Verify driver loaded
   sudo modprobe nvidia
   nvidia-smi
   
   # Reinstall Ollama (will detect CUDA)
   curl -fsSL https://ollama.com/install.sh | sh
   ```

2. **AMD GPU not supported:**
   - Check [Ollama ROCm documentation](https://github.com/ollama/ollama/blob/main/docs/linux.md)
   - May require ROCM installation: `sudo apt install rocm-opencl-runtime`

3. **Intel Arc GPU:**
   - Requires Intel Graphics Compiler: `sudo apt install intel-compute-runtime`
   - Supported in recent Ollama versions

4. **Fix hardware profile cache:**
   ```bash
   # Delete cached profile to force redetection
   rm ~/.axon/config.toml
   
   # Restart Brain Service
   systemctl --user restart axon-brain
   ```

---

## Ollama Connection

### Ollama HTTP 500 Error

**Symptoms:** Brain Service reports "Ollama error", model inference fails.

**Diagnosis:**
```bash
# Test Ollama API directly
curl http://localhost:11434/api/tags

# Check Ollama daemon
systemctl status ollama
journalctl -u ollama -n 50

# Monitor Ollama logs
tail -f ~/.ollama/logs/server.log
```

**Solutions:**

1. **Ollama not running:**
   ```bash
   # Start Ollama
   ollama serve &
   
   # Or use systemd
   systemctl start ollama
   ```

2. **Model not found:**
   ```bash
   # List available models
   ollama list
   
   # Pull missing model
   ollama pull mistral:latest
   ```

3. **Out of memory:**
   ```bash
   # Check available VRAM
   nvidia-smi  # or rocm-smi
   
   # Reduce model size or allocate more VRAM
   # Edit ~/.ollama/modelfile or Ollama config
   ```

4. **Port already in use:**
   ```bash
   # Find process using port 11434
   lsof -i :11434
   
   # Change port in Ollama config (if needed)
   OLLAMA_HOST=localhost:11435 ollama serve
   ```

### Model Inference Too Slow

**Symptoms:** AI responses take 30+ seconds, even for simple queries.

**Diagnosis:**
```bash
# Check which model is running
ollama list

# Monitor system resources during inference
watch -n 1 nvidia-smi  # or rocm-smi

# Check if model is in VRAM
nvidia-smi --query-gpu=memory.used --format=csv
```

**Solutions:**

1. **Model doesn't fit in VRAM:**
   - Use smaller model for speed tier: `ollama pull mistral:7b`
   - Or increase VRAM allocation if available
   - Check model parameters: `ollama show mistral:latest`

2. **CPU fallback (GPU not detected):**
   - See [GPU Not Detected](#gpu-not-detected) section
   - CPU inference is 10-100x slower than GPU

3. **Disk I/O bottleneck:**
   ```bash
   # Check disk performance
   iostat -x 1 5
   
   # Ensure model cache on fast disk (not USB)
   ls -lah ~/.ollama/models/
   ```

---

## D-Bus Communication

### D-Bus Method Call Fails with "Timeout"

**Symptoms:** "Did not receive a reply. Possible causes include: the remote application did not send a reply, the message bus security policy blocked the reply"

**Diagnosis:**
```bash
# Check D-Bus daemon status
systemctl --user status dbus

# Monitor D-Bus traffic
dbus-monitor --session | grep -i axonos
```

**Solutions:**

1. **Method taking too long:**
   - Set longer timeout in client: `dbus_proxy.Timeout = 30000  # 30 seconds`
   - Optimize server-side method implementation

2. **D-Bus permission denied:**
   ```bash
   # Check D-Bus policy
   cat /etc/dbus-1/session.d/org.axonos.Brain.conf
   cat ~/.local/share/dbus-1/services/org.axonos.Brain.service
   
   # Ensure XML policy is correct
   ```

3. **Server crash during method execution:**
   ```bash
   # Check service logs
   journalctl --user -u axon-brain -n 100
   
   # Look for Python traceback
   ```

### Signal Not Received

**Symptoms:** `ContextChanged` signal or `ModelDownloadProgress` signals not triggering.

**Solutions:**

1. **Check signal emission:**
   ```bash
   # Monitor signals
   dbus-monitor --session interface='org.axonos.Brain'
   
   # Or test with dbus-send
   dbus-send --session --print-reply \
       --dest=org.axonos.Brain \
       /org/axonos/Brain \
       org.freedesktop.DBus.Properties.GetAll \
       string:"org.axonos.Brain"
   ```

2. **Client not subscribed:**
   - Verify client code uses `@dbus.service.signal()` decorator
   - Check subscription happens before signal emission

3. **D-Bus session not shared between processes:**
   ```bash
   # Verify all processes use same session
   echo $DBUS_SESSION_BUS_ADDRESS
   ```

---

## Performance

### High CPU Usage

**Symptoms:** Axon processes using 50%+ CPU constantly.

**Diagnosis:**
```bash
# Identify hotspot
top -p $(pidof python3) -H

# Profile with cProfile
python3 -m cProfile -s cumtime services/axon-brain/brain_service.py

# Use memory_profiler
python3 -m memory_profiler services/axon-brain/brain_service.py
```

**Common Causes & Fixes:**

1. **Infinite polling loop:**
   - Use event-based updates instead of polling
   - Use `GLib.timeout_add()` with appropriate intervals

2. **Unbounded cache growth:**
   - Ensure TTLCache is clearing expired entries
   - Monitor `.cache` dict size: `len(cache.cache)`

3. **Synchronous blocking operations:**
   - Move long operations to threads: `threading.Thread(..., daemon=True)`
   - Use async/await where possible

### High Memory Usage

**Diagnosis:**
```bash
# Check process memory
ps aux | grep python3 | grep axon

# Detailed memory breakdown
python3 -c "import psutil; p = psutil.Process(); print(p.memory_info())"

# Check for memory leaks
valgrind --leak-check=full python3 services/axon-brain/brain_service.py
```

**Solutions:**

1. **Conversation store unbounded growth:**
   ```bash
   # Implement conversation pruning
   # Add to ConversationStore: delete conversations older than N days
   ```

2. **Model cache not releasing memory:**
   - Check TTLCache cleanup
   - Verify model stays in Ollama, not in Python process

3. **Circular references in D-Bus objects:**
   - Ensure proper cleanup in `__del__()` methods
   - Use weak references where appropriate

---

## Development Issues

### Type Checking Errors

**Symptoms:** `mypy` reports type errors that don't make sense.

**Solutions:**

1. **D-Bus types not recognized:**
   ```bash
   # Install type stubs
   pip install types-PyGObject
   
   # Update mypy config in pyproject.toml
   [tool.mypy]
   plugins = ["mypy_dbus_python_plugin"]
   ```

2. **GLib/GTK types missing:**
   ```bash
   # These require system packages to get stubs
   sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
   ```

### Tests Not Running

**Symptoms:** `pytest` fails to import modules, permission errors.

**Solutions:**

1. **Install development dependencies:**
   ```bash
   pip install -r requirements-dev.txt
   ```

2. **D-Bus not available in test environment:**
   - Use `unittest.mock.patch()` to mock D-Bus
   - See `tests/test_services_enhanced.py` for examples
   - Run integration tests only with `@pytest.mark.skip` or in CI with systemd

3. **Python path issues:**
   ```bash
   # Add to test file
   sys.path.insert(0, str(Path(__file__).parent.parent))
   
   # Or set PYTHONPATH
   export PYTHONPATH=/home/user/Axon-OS:$PYTHONPATH
   ```

### Pre-commit Hooks Failing

**Symptoms:** `git commit` fails with formatting/linting errors.

**Solutions:**

1. **Automatic fixes:**
   ```bash
   # Let pre-commit fix issues automatically
   pre-commit run --all-files
   
   # Then commit
   git add .
   git commit -m "message"
   ```

2. **Skip checks for specific files:**
   ```bash
   # Add to .git/info/exclude or .gitignore
   echo "path/to/file" >> .git/info/exclude
   ```

3. **Temporarily disable hook:**
   ```bash
   git commit --no-verify
   ```

---

## Debug Logging

### Enable Verbose Logging

**For all services:**
```bash
# Set environment variable
export AXON_LOG_LEVEL=DEBUG

# Restart services
systemctl --user restart axon-brain axon-context

# View logs
journalctl --user -f
```

**Per-service:**
```python
# In brain_service.py or context_service.py
logger = configure_app_logger(__name__, level=logging.DEBUG)
```

### Capture Full Stack Traces

**In systemd service file:**
```ini
[Service]
Environment="PYTHONDONTWRITEBYTECODE=1"
ExecStart=python3 -u service.py  # -u for unbuffered output
StandardOutput=journal
StandardError=journal
```

Then view with:
```bash
journalctl --user -u axon-brain -o cat --no-tail
```

---

## Getting Help

If issues persist:

1. **Check logs for errors:**
   ```bash
   journalctl --user -xe | grep -i error
   ```

2. **Search GitHub issues:**
   - https://github.com/kaorii-ako/Axon-OS/issues

3. **Enable debug mode and report:**
   ```bash
   # Capture debug output
   export AXON_LOG_LEVEL=DEBUG
   systemctl --user restart axon-brain
   journalctl --user -u axon-brain > debug.log
   
   # Include in GitHub issue
   ```

4. **Check compatibility:**
   - Ubuntu 24.04 LTS
   - Python 3.10+
   - GNOME 46+ (or compatible shell)
