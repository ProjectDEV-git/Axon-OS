# Architecture Decision Records (ADRs)

This document records architectural decisions made during Axon OS development.

## ADR-001: D-Bus for Centralized Service Architecture

**Status:** Accepted

### Context
Axon OS needs a way for multiple applications (Terminal, Files, Settings, AI Panel, etc.) to communicate with centralized AI services. The system needs to support:
- Multiple simultaneous clients
- Asynchronous, long-running operations (model inference)
- Event notifications (context changes, model downloads)
- Privilege separation (some operations need root, others need user-level access)

### Decision
Use D-Bus (Desktop Bus) as the primary IPC (Inter-Process Communication) mechanism, specifically the session bus for user-level services (`org.axonos.Brain`, `org.axonos.Context`) and system bus for privileged operations.

### Rationale
- **Industry standard**: Widely used in GNOME, KDE, systemd ecosystems
- **Language-agnostic**: JavaScript (Shell extension), Python (services), GTK/libadwaita apps
- **Built-in event system**: Signals and properties for reactive updates
- **Well-integrated with systemd**: Services auto-restart on failure
- **Type-safe method signatures**: Explicit D-Bus signatures prevent bugs

### Alternatives Considered
- **gRPC**: Over-engineered for local-only communication, adds HTTP/2 overhead
- **Unix sockets + custom protocol**: Would require reimplementing service discovery, error handling
- **REST API**: Excessive for local communication, adds network stack complexity
- **Shared memory**: Unsafe without careful synchronization primitives

### Consequences
- **Positive**: 
  - Decoupled architecture enables independent app development
  - Systemd integration provides automatic service lifecycle management
  - Python/GObject bindings are mature and well-documented
- **Negative**:
  - D-Bus adds 10-50ms latency vs function calls
  - Requires understanding of D-Bus concepts (signals, properties, method signatures)
  - Error handling across process boundaries is more complex

---

## ADR-002: Ollama for Local Inference

**Status:** Accepted

### Context
Axon OS requires fully local AI inference with no cloud dependencies. We need to support:
- GPU acceleration (NVIDIA, AMD, Intel)
- Model hotswapping without restart
- Multiple model sizes for different latency/quality tradeoffs
- Easy model downloads and updates

### Decision
Use Ollama as the local inference engine via HTTP API (`localhost:11434`).

### Rationale
- **Simple HTTP API**: No need to link C++ libraries directly
- **Active development**: Rapid GPU support (CUDA, ROCm, Metal)
- **Model management**: Built-in model pull/push, no manual binary management
- **Hardware profiling**: Ships with GPU detection
- **Community models**: Easy integration with Hugging Face

### Alternatives Considered
- **LM Studio**: GUI-focused, harder to integrate programmatically
- **vLLM**: Higher throughput but more complex setup, less portable
- **llama.cpp**: Lower-level, more control but steeper learning curve
- **Hugging Face Transformers**: Heavy dependencies, less optimized inference

### Consequences
- **Positive**:
  - Loose coupling via HTTP means Ollama can be upgraded independently
  - HTTP timeout/retry logic is straightforward
  - Model selection and download is user-friendly
- **Negative**:
  - External process dependency adds startup delay
  - Network stack overhead for every inference call (mitigated by localhost)
  - Ollama crashes require systemd to restart service

---

## ADR-003: Three-Tier Model Strategy (Speed/General/Deep)

**Status:** Accepted

### Context
Different tasks have different latency/quality requirements:
- Speed tier: Intent parsing, command suggestions (< 100ms)
- General tier: Chat, code completion (1-5 seconds)
- Deep tier: Complex reasoning, code review (5-30 seconds)

### Decision
Hardware profiler auto-recommends three models at install time and stores in config.

### Rationale
- **Matches user hardware**: Profiles RAM, VRAM, CPU to recommend models that fit
- **Simple user mental model**: Three tiers are easy to understand vs. 10+ model choices
- **Runtime flexibility**: Apps choose tier, not specific model

### Alternatives Considered
- **Single large model**: Wastes GPU on simple tasks, poor UX for latency
- **Dynamic model selection**: Complex routing logic, hard to debug/tune
- **User choice per task**: Too much configuration burden

### Consequences
- **Positive**:
  - Users get optimized defaults without configuration
  - Apps are decoupled from specific model details
- **Negative**:
  - Hardware profiler must be accurate
  - Users can't override globally (must edit config manually)

---

## ADR-004: Python for Desktop Services and Apps

**Status:** Accepted

### Context
Axon OS consists of multiple services and applications. Language choice affects:
- Development velocity
- Runtime performance
- Debugging experience
- Maintainability

### Decision
Use Python 3.10+ for D-Bus services, apps, and utilities. Shell extension in JavaScript.

### Rationale
- **GObject bindings mature**: 15+ years of Python + GObject integration
- **Rapid development**: Faster iteration than Rust/C++
- **System availability**: Python 3.10+ ships with Ubuntu 22.04+
- **Rich ecosystem**: NumPy for hardware profiling, SQLite for conversations
- **Easy testing**: pytest ecosystem is best-in-class

### Alternatives Considered
- **Rust**: Better performance/safety but steeper learning curve, slower development
- **C++**: Direct integration but manual memory management, slower dev cycle
- **JavaScript (Node.js)**: Good performance but awkward for system integration

### Consequences
- **Positive**:
  - Developers familiar with Python can contribute easily
  - Type hints enable LSP support for IDE intelligence
  - Fast iteration enables rapid feature development
- **Negative**:
  - Startup time 100-500ms (acceptable for daemon services)
  - Runtime memory overhead vs. compiled languages
  - Requires Python environment on user system

---

## ADR-005: SQLite for Conversation Persistence

**Status:** Accepted

### Context
Axon OS maintains conversation history across app restarts. Requirements:
- ACID transactions (conversations don't corrupt on crash)
- Full-text search (users search old conversations)
- Structured schema (conversations have multiple messages)

### Decision
Use SQLite with schema versioning for conversation storage.

### Rationale
- **Zero setup**: Embedded, no separate server process
- **ACID reliability**: Transactions ensure consistency
- **Full-text search**: FTS5 module enables natural language queries
- **Proven**: Billions of devices use SQLite

### Alternatives Considered
- **PostgreSQL**: Overkill for single-machine use, adds daemon complexity
- **JSON files**: Risk of corruption, no transactions, poor search performance
- **MongoDB**: Not appropriate for single-machine workload

### Consequences
- **Positive**:
  - Fast local queries (10-100ms for full-text search)
  - Natural ACID guarantees
- **Negative**:
  - Schema migration must be handled manually
  - Concurrent multi-process writes require careful WAL mode setup

---

## ADR-006: Systemd Services for Daemon Lifecycle

**Status:** Accepted

### Context
Axon Brain and Axon Context must be auto-started and auto-restarted. Options:
- systemd user services
- Launch at login via GNOME Session
- Manual startup scripts

### Decision
Use systemd user services (`~/.config/systemd/user/`) for daemon lifecycle.

### Rationale
- **Integrated**: systemd handles dependency management, ordering, restarts
- **Reliable**: Built-in watchdog, automatic respawn on crash
- **Logging**: journalctl integration for debugging
- **Discovery**: systemd activatable services reduce startup overhead

### Alternatives Considered
- **GNOME Session .desktop files**: Limited control, no restart logic
- **Manual bash scripts**: Fragile, error-prone lifecycle management

### Consequences
- **Positive**:
  - Automatic restart on failure (exponential backoff available)
  - journalctl provides centralized logging
- **Negative**:
  - systemd is Linux-specific (no macOS/Windows support)
  - Users must enable/manage services manually (not automatic)

---

## ADR-007: Type Checking with Mypy (Strict Mode for New Code)

**Status:** Accepted

### Context
Python's dynamic typing enables fast development but causes runtime bugs. Options:
- No type checking
- Gradual typing with mypy
- Strict mypy mode

### Decision
Use Mypy with `check_untyped_defs = true` for D-Bus services. Strict mode for new code, gradual migration for legacy.

### Rationale
- **Catches errors early**: Many bugs caught at development time
- **Self-documenting**: Type hints serve as inline documentation
- **IDE support**: Enables autocomplete, jump-to-definition

### Alternatives Considered
- **Runtime typing (Pydantic)**: Slower, adds serialization overhead
- **Full type ignore**: No benefit
- **Pyright vs Mypy**: Mypy more mature, integrates with pre-commit hooks

### Consequences
- **Positive**:
  - Developers get fast IDE feedback
  - Fewer runtime type-related bugs
- **Negative**:
  - Requires learning Python type annotation syntax
  - Some libraries have incomplete type stubs

