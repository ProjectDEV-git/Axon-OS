# Top-level Makefile — builds C components, eBPF, and Rust crates
# Requires: gcc, mingw-w64, clang, libbpf-dev, linux-headers, cargo

PREFIX ?= /usr
BINDIR ?= $(PREFIX)/bin
LIBDIR ?= $(PREFIX)/lib
CONFDIR ?= /etc/win-sandbox-runner
WINE_DLLDIR ?= /usr/lib/wine/x86_64-windows
SYSTEMD_DIR ?= /etc/systemd/system
BINFMET_DIR ?= /etc/binfmt.d

VERSION ?= $(shell grep '^version' Cargo.toml | head -1 | sed 's/.*"\(.*\)"/\1/')

CC      := gcc
MINGW   := x86_64-w64-mingw32-gcc
CLANG   := clang
CFLAGS  := -Wall -Wextra -O2 -D_GNU_SOURCE
LDFLAGS :=

# --- Module 2: sys_netmp (Wine DLL) ---
SYS_NETMP_SRCS := csrc/sys_netmp/dllmain.c csrc/sys_netmp/ioctl.c csrc/sys_netmp/pipe.c
SYS_NETMP_OBJS := $(SYS_NETMP_SRCS:.c=.o)
SYS_NETMP_DLL  := csrc/sys_netmp/sys_netmp.dll

# --- Module 3: win-tap-bridge ---
TAP_SRCS := csrc/win-tap-bridge/win-tap-bridge.c \
            csrc/win-tap-bridge/tap.c \
            csrc/win-tap-bridge/socket.c \
            csrc/win-tap-bridge/bridge.c
TAP_OBJS := $(TAP_SRCS:.c=.o)
TAP_BIN  := csrc/win-tap-bridge/win-tap-bridge

# --- Module 4: win_tap_filter (eBPF) ---
EBPF_SRC    := csrc/win_tap_filter/win_tap_filter.c
EBPF_OBJ    := csrc/win_tap_filter/win_tap_filter.bpf.o
EBPF_LOADER := csrc/win_tap_filter/loader
EBPF_LOADER_SRC := csrc/win_tap_filter/loader.c

.PHONY: all clean install install-dll install-bridge install-ebpf install-binfmt install-systemd
.PHONY: cargo cargo-release cargo-test cargo-clippy cargo-install cargo-uninstall deb

all: $(SYS_NETMP_DLL) $(TAP_BIN) $(EBPF_OBJ) $(EBPF_LOADER)

# --- sys_netmp.dll (MinGW cross-compile) ---
csrc/sys_netmp/%.o: csrc/sys_netmp/%.c
	$(MINGW) -c -Wall -O2 -Icsrc/sys_netmp -o $@ $<

$(SYS_NETMP_DLL): $(SYS_NETMP_OBJS)
	$(MINGW) -shared -o $@ $^ -lws2_32 -lntdll

# --- win-tap-bridge (native Linux) ---
csrc/win-tap-bridge/%.o: csrc/win-tap-bridge/%.c
	$(CC) $(CFLAGS) -c -o $@ $<

$(TAP_BIN): $(TAP_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

# --- eBPF TC classifier ---
$(EBPF_OBJ): $(EBPF_SRC) csrc/win_tap_filter/win_tap_filter.h
	$(CLANG) -target bpf -D__TARGET_ARCH_x86 \
		-I/usr/include/bpf \
		-g -O2 -c -o $@ $<

# --- eBPF userspace loader ---
$(EBPF_LOADER): $(EBPF_LOADER_SRC) $(EBPF_OBJ)
	$(CC) $(CFLAGS) -o $@ $< -lbpf -lelf -lz

# --- Install targets ---
install: install-bridge install-ebpf install-dll install-binfmt install-systemd

install-dll: $(SYS_NETMP_DLL)
	install -Dm755 $(SYS_NETMP_DLL) $(DESTDIR)$(WINE_DLLDIR)/sys_netmp.dll

install-bridge: $(TAP_BIN)
	install -Dm755 $(TAP_BIN) $(DESTDIR)$(BINDIR)/win-tap-bridge

install-ebpf: $(EBPF_OBJ) $(EBPF_LOADER)
	install -Dm755 $(EBPF_LOADER) $(DESTDIR)$(BINDIR)/win_tap_filter-loader
	install -Dm644 $(EBPF_OBJ) $(DESTDIR)$(LIBDIR)/win_tap_filter/win_tap_filter.bpf.o

install-binfmt:
	@# Persist the definition in /etc/binfmt.d so it survives reboot. The string
	@# comes from register-binfmt.sh --print so there is exactly one place that
	@# knows the format (the missing-mask bug recurred 3x when it was duplicated).
	install -d $(DESTDIR)$(BINFMET_DIR)
	BINDIR=$(BINDIR) sh scripts/register-binfmt.sh --print \
		> $(DESTDIR)$(BINFMET_DIR)/apex-win.conf

install-systemd:
	install -Dm644 scripts/win-sandbox-runner.service $(DESTDIR)$(SYSTEMD_DIR)/win-sandbox-runner.service
	@if [ -f systemd/win-tap-bridge.service ]; then \
		install -Dm644 systemd/win-tap-bridge.service $(DESTDIR)$(SYSTEMD_DIR)/; \
	fi
	@if [ -f systemd/win_tap_filter.service ]; then \
		install -Dm644 systemd/win_tap_filter.service $(DESTDIR)$(SYSTEMD_DIR)/; \
	fi
	@systemctl daemon-reload 2>/dev/null || true

clean:
	rm -f csrc/sys_netmp/*.o csrc/sys_netmp/*.dll
	rm -f csrc/win-tap-bridge/*.o csrc/win-tap-bridge/win-tap-bridge
	rm -f csrc/win_tap_filter/*.o csrc/win_tap_filter/*.bpf.o csrc/win_tap_filter/loader

# --- Rust crates ---

cargo:
	cargo build --workspace

cargo-release:
	@if grep -q '^version = 4' Cargo.lock 2>/dev/null; then \
		echo "WARN: Cargo.lock is v4 (nightly format), downgrading to v3..."; \
		sed -i 's/^version = 4$$/version = 3/' Cargo.lock; \
	fi
	cargo build --release --workspace

cargo-test:
	cargo test --workspace

cargo-clippy:
	cargo clippy --workspace -- -D warnings

# Quick install: Rust binaries + systemd service + binfmt (no C components)
# Build runs as current user (cargo), install steps use sudo internally.
quick-install: cargo-release
	@# Clean up any old installs from wrong paths
	@sudo systemctl stop win-sandbox-runner 2>/dev/null || true
	@sudo rm -f /usr/local/bin/win-sandbox-runner /usr/local/bin/win-sandbox-gui 2>/dev/null || true
	@echo "Installing binaries and service (requires sudo)..."
	@sudo install -Dm755 target/release/win-sandbox-runner $(BINDIR)/win-sandbox-runner
	@sudo install -Dm755 target/release/win-sandbox-gui $(BINDIR)/win-sandbox-gui
	@sudo install -Dm644 scripts/register-binfmt.sh $(BINDIR)/register-binfmt.sh
	@sudo install -Dm644 scripts/win-sandbox-runner.service /etc/systemd/system/win-sandbox-runner.service
	@# Desktop MIME handler — makes double-click work in the file manager.
	@# Without this, GNOME/Nautilus resolves a MIME handler and never exec()s
	@# the file, so binfmt_misc alone is NOT enough for double-click.
	@sudo install -Dm644 scripts/apex-win.desktop /usr/share/applications/apex-win.desktop
	@sudo update-desktop-database /usr/share/applications 2>/dev/null || true
	@# Register as default for every MIME type a PE binary may be detected as.
	@# Run as the invoking user: xdg-mime writes per-user prefs, so running it
	@# under sudo would set root's associations and leave the desktop unchanged.
	@for m in application/vnd.microsoft.portable-executable application/x-ms-dos-executable application/x-msdownload application/x-msi; do \
		if [ -n "$$SUDO_USER" ]; then \
			sudo -u "$$SUDO_USER" xdg-mime default apex-win.desktop $$m 2>/dev/null || true; \
		else \
			xdg-mime default apex-win.desktop $$m 2>/dev/null || true; \
		fi; \
	done
	@echo "✓ Double-click handler registered for .exe files"
	@# Install config files
	@sudo mkdir -p /etc/win-sandbox-runner
	@sudo install -m 644 config/appdb.json /etc/win-sandbox-runner/appdb.json
	@sudo install -m 644 config/rules.json /etc/win-sandbox-runner/rules.json
	@sudo install -m 644 config/net-optimizer.json /etc/win-sandbox-runner/net-optimizer.json
	@# Register binfmt_misc
	@# Single source of truth for the handler definition (see the comment at
	@# the top of register-binfmt.sh for why this is not inlined here).
	@if [ -d /proc/sys/fs/binfmt_misc ]; then \
		BINDIR=$(BINDIR) sudo -E sh scripts/register-binfmt.sh >/dev/null 2>&1 && \
		echo "✓ binfmt_misc registered (.exe -> win-sandbox-runner)" || \
		echo "WARN: Failed to register binfmt handler"; \
	fi
	@sudo systemctl daemon-reload
	@echo ""
	@echo "✓ Installed! Start the daemon:"
	@echo "  sudo systemctl enable --now win-sandbox-runner"
	@echo ""
	@echo "Then run any .exe:"
	@echo "  /path/to/program.exe"

# Install Rust binaries + config + C components
cargo-install: cargo-release install-bridge install-ebpf install-dll install-binfmt install-systemd
	install -d $(DESTDIR)$(BINDIR)
	install -m 755 target/release/win-sandbox-runner $(DESTDIR)$(BINDIR)/
	install -m 755 target/release/win-sandbox-gui $(DESTDIR)$(BINDIR)/
	install -d $(DESTDIR)$(CONFDIR)
	install -m 644 config/rules.json $(DESTDIR)$(CONFDIR)/
	install -m 644 config/appdb.json $(DESTDIR)$(CONFDIR)/
	install -m 644 config/rules.schema.json $(DESTDIR)$(CONFDIR)/
	@# Register binfmt_misc via the single source of truth
	@if [ -d /proc/sys/fs/binfmt_misc ]; then \
		BINDIR=$(DESTDIR)$(BINDIR) sh scripts/register-binfmt.sh >/dev/null 2>&1 && \
		echo "binfmt_misc handler registered (.exe -> win-sandbox-runner)" || \
		echo "WARN: Failed to register binfmt handler (run register-binfmt.sh manually)"; \
	fi
	@echo "Installed win-sandbox-runner $(VERSION)"

cargo-uninstall:
	@if [ -f /proc/sys/fs/binfmt_misc/APEX-WIN ]; then \
		echo -1 > /proc/sys/fs/binfmt_misc/APEX-WIN 2>/dev/null || true; \
	fi
	-systemctl stop win-sandbox-runner 2>/dev/null || true
	-systemctl disable win-sandbox-runner 2>/dev/null || true
	rm -f /etc/systemd/system/win-sandbox-runner.service
	rm -f $(DESTDIR)$(BINDIR)/win-sandbox-runner
	rm -f $(DESTDIR)$(BINDIR)/win-sandbox-gui
	rm -rf $(DESTDIR)$(CONFDIR)
	@systemctl daemon-reload 2>/dev/null || true
	@echo "Removed. User config at ~/.config/win-sandbox/ preserved."

deb: cargo-release
	./scripts/build-deb.sh $(VERSION)
