# APEX-WIN upgrade plan

**Planning baseline:** 2026-09-17, main at `15c0e49` plus uncommitted hardening.
This is a roadmap, not a statement that these capabilities already ship.
[README.md](README.md) describes current mechanisms; [HANDOFF.md](HANDOFF.md)
separates source-reviewed behavior, local test evidence and documented intent.

## Goal and boundaries

Deliver a dependable desktop Wine launcher whose requested policy, enforced
permissions and displayed guarantees agree. Linux is primary; macOS uses
Seatbelt with explicitly different guarantees and compatibility gates.

- No VM-level security claims, malware-analysis appliance or kernel anti-cheat
  compatibility promise. Wine and the host kernel remain trusted dependencies.
- No Wine fork or custom Windows implementation. Integrate supported upstream
  Wine/runtime variants and report unsupported applications honestly.
- Reuse the existing Rust workspace and GTK4/libadwaita GUI. No new GUI framework
  or replacement desktop stack; macOS CLI/launcher remains separately scoped.
- Flatpak is deferred until host integration, Wine runtimes, device access and
  sandbox nesting have a coherent design. Do not make it a release prerequisite.
- Privileged setup should be narrow and auditable, not an excuse to execute
  untrusted applications or dependency installers in a long-lived root service.
- Network tuning and TAP/eBPF components are optional integrations, not evidence
  that networking is isolated or performance improves on every connection.

## Schedule and dependency order

The task breakdown is **M0 baseline plus six delivery milestones (M1–M6)**.
The working estimate is **22–35 engineering weeks**, not a fixed release date.
The per-milestone allocation below is a planning assumption pending lead review;
actual scope gates, hardware access and compatibility results drive scheduling.
It assumes sustained engineering time, existing libraries/frameworks, access to
Linux GPU/display test hosts and a Mac, and no Wine fork or new GUI stack.
A small team may overlap work, but shared review and acceptance gates remain;
part-time work, unavailable hardware or upstream regressions extend elapsed time.

| Milestone | Estimate | Deliverable |
|-----------|----------|-------------|
| M0 | 1–2 weeks | Trustworthy baseline and claims inventory |
| M1 | 4–6 weeks | Correct policy and unprivileged launch architecture |
| M2 | 4–6 weeks | Verified isolation and disposable sessions |
| M3 | 3–5 weeks | Reliable prefixes and app profiles |
| M4 | 4–6 weeks | Complete desktop workflow |
| M5 | 3–5 weeks | Compatibility matrix and measured performance |
| M6 | 3–5 weeks | Distribution and release engineering |
| **Total** | **22–35 weeks** | **Gate-based release, not a security certification** |

```text
M0 baseline
  └─> M1 policy + privilege boundary
        ├─> M2 isolation + disposable sessions ─┐
        └─> M3 prefix/profile reliability ─────┤
                                              └─> M4 desktop product
                                                    └─> M5 compatibility/performance
                                                          └─> M6 release
```

M3 design and M4 UI prototypes can proceed while M2 is tested; desktop completion
requires both M2 and M3 acceptance. Packaging prototypes can start early, but M6
release approval depends on M5 evidence. Re-estimate after each milestone; do not
trade away a policy or containment gate to preserve a calendar date.

## M0 — Baseline trust (1–2 weeks)

**Work items**
- Establish repeatable Rust 1.96.x build/test/clippy/fmt results from a clean
  checkout; identify stale binaries and record exact revision/toolchain evidence.
- Inventory claims against dispatch, Linux tiers, Seatbelt, prefix setup and
  entrypoints. Separate CI configuration from actual passing CI runs.
- Close baseline policy bugs: trust defaults to Tier 0 but explicit `--tier`
  wins; side-effect-free launch preview; component-aware untrusted-path matching;
  explicit Tier 3 refusal at preflight and runtime; version/option probe alignment.
- Record preview output (match, tier/provenance, network, prefix and refusal),
  known limitations and a prioritized defect list without silently editing
  unrelated app profiles.

**Exit criteria**
- Local and CI validation artifacts identify the tested revision, host and
  commands. Trust/override, preview and refusal regressions have tests.
- Preview leaves prefixes, rules and system network configuration unchanged,
  including combined launch flags and known-game profiles.
- No documentation claims Tier 3 is Tier 2 plus overlay, Linux Tier 2 currently
  blocks networking, or a feature probe proves a successful mount.

**Boundary:** baseline hardening is not implementation of a new overlay backend
or proof that a Wine app is safely contained. T1/T2 status lives in the handoff.

## M1 — Policy correctness and unprivileged execution (4–6 weeks)

**Work items**
- Resolve a single launch plan before side effects: tier, provenance, network,
  filesystem grants, display/devices, runtime, prefix and compatibility exceptions.
  Make CLI, desktop and daemon semantics agree; explicitly confirm any downgrade.
- Audit consumption of rule/profile fields, network defaults and environment
  precedence. Fix unconditional Tier 2 host-network sharing and broad writable
  binds; expose exceptions instead of labelling them isolated defaults.
- Move ordinary execution and prefix/dependency provisioning to the invoking
  user. Limit elevation to audited registration/device/network setup. Review
  daemon/FIFO identity, request validation, permissions and concurrency boundaries.
- Align capability reporting with the backend that will actually execute;
  unsupported explicit guarantees refuse, without suggesting a newer version
  alone makes an unimplemented backend work.

**Exit criteria**
- Table-driven policy tests cover launch sources, trust/override combinations,
  invalid input, missing capabilities and downgrade consent.
- Tests on an ordinary-user account prove UID/GID/ownership across Wine and helper
  processes; no normal app launch requires root.
- Effective network/filesystem grants match reported policy, with negative tests
  and no unconditional host-network sharing hidden behind `network=false`.

**Boundary:** retain existing mechanisms and a narrow setup boundary; do not add
an unaudited setuid mount shortcut or redesign Wine itself.

## M2 — Verified isolation and disposable sessions (4–6 weeks)

**Work items**
- Implement a genuinely unprivileged disposable backend composed with the
  intended namespace/filesystem boundary, instead of privileged mount plus
  unrestricted Wine. Define exactly what is discarded and what may be exported.
- Verify filesystem, network, display and device restrictions using controlled
  allow/deny tests, including subprocesses and wineserver. Missing nested display
  support must not silently turn an isolation request into host display access.
- Own the whole session lifecycle: isolated prefix, resource bounds, cancellation,
  crash recovery, child cleanup and removal of mounts/scratch state.
- Exercise Linux Landlock/bubblewrap failure modes and macOS Seatbelt separately.
  Network-denial tests need a successful unsandboxed control; skipped tests do
  not count as verified containment.

**Exit criteria**
- Unprivileged integration tests show allowed actions succeed, denied actions
  fail and protected host data stays unchanged, on documented supported hosts.
- Disposable-session tests prove prefix changes disappear after clean exit,
  failure and cancellation; intentionally exported files are distinguished.
- No stranded Wine children/mounts or cross-session state in repeated/concurrent
  runs. Failure to establish an explicit boundary refuses execution.
- Publish mechanism-specific guarantees and test evidence, not a single numeric
  tier label implying equal Linux/macOS isolation.

**Boundary:** no VM-level claims or macOS Tier 3 promise. A copied temporary prefix
alone is not an equivalent containment boundary. Resource controls and lifecycle
work remain targets, not existing capabilities.

## M3 — Prefix and app-profile reliability (3–5 weeks)

**Work items**
- Make prefix creation, dependency installation and state updates transactional,
  idempotent and lock-safe. Handle interruption, repair, backup/reset and migration.
- Resolve the same prefix/runtime across tiers. Audit hash-based prefix identity
  when an app updates and avoid silently losing or sharing user state.
- Validate app-profile schema and every supported field; surface unsupported
  settings. Distinguish advisory filename matches from explicit hash-based trust.
- Track runtime/DXVK/winetricks versions and provenance; support diagnostics and
  retries without declaring a failed install complete or running installers as root.

**Exit criteria**
- Concurrent first launches cannot corrupt prefixes/state. Interrupted setup
  recovers predictably; repeat setup does not reinstall completed dependencies.
- Tests and representative apps cover update, repair, reset and profile/runtime
  changes with preserved user data and clear ownership.
- Profile claims correspond to consumed settings and documented compatibility
  results; a recommendation is never presented as a security attestation.

**Boundary:** integrate upstream Wine and existing dependency tools; no Wine fork,
arbitrary automatic trust expansion or commitment to every Windows application.

## M4 — Complete desktop product (4–6 weeks)

**Work items**
- Connect the existing GTK4/libadwaita GUI to the shared launch/policy workflow:
  first-launch decisions, effective permissions, trust management and app library.
- Add visible setup/launch progress, cancellation, actionable errors, logs and
  prefix/profile management. Explain unsupported tiers and compatibility exceptions
  before execution; do not silently grant permissions to make a window appear.
- Make MIME double-click, explicit CLI and optional binfmt/daemon paths consistent.
  Test paths with spaces, multiple files and repeated launches; avoid blocking
  background requests on terminal input.
- Finish user-facing diagnostics, accessibility/keyboard navigation, onboarding
  and recovery documentation. Maintain the separate macOS launcher/TTY experience
  without implying a native macOS GTK product exists.

**Exit criteria**
- A fresh user can install, open a supported app, understand/choose permissions,
  cancel setup, inspect failures, relaunch and remove/reset it without manual
  config edits. No terminal is required for the supported Linux desktop flow.
- GUI actions and CLI equivalents produce the same effective launch plan.
- End-to-end desktop tests and manual accessibility review cover success,
  unsupported capability, interrupted setup and refusal states.

**Boundary:** use the existing GUI framework; no rewrite in a new toolkit or
macOS GUI expansion that displaces core correctness work.

## M5 — Compatibility and measured performance (3–5 weeks)

**Work items**
- Build a reproducible application matrix spanning installers, office tools,
  games and device-dependent applications, with versions and known failures.
- Cover supported Linux kernels/distros, AMD/Intel/NVIDIA, X11/Wayland and
  relevant audio/input/serial access. Validate macOS Wine under actual Seatbelt
  profiles, not merely generated profile text or generic shell commands.
- Benchmark startup, prefix provisioning, memory, CPU, frame-time and relevant
  network behavior against direct Wine on the same host/runtime. Record methods,
  sample counts and variance; distinguish cold and warm runs.
- Turn reproduced regressions into tests/profile fixes. Publish bounded support
  tiers, unsupported anti-cheat/driver cases and security-impacting workarounds.

**Exit criteria**
- Every claimed supported configuration has a reproducible result with revision,
  runtime, host and relevant permissions recorded. Failures/skips are visible.
- Performance claims use measured baselines and accepted regression budgets,
  not "native" or "near-native" labels inferred from the design.
- macOS compatibility claims require real Wine/application launch evidence;
  profile-denial tests alone cannot satisfy this gate.

**Boundary:** no universal Windows compatibility, kernel anti-cheat guarantee,
Wine fork or unconditional claim that network tuning improves latency.

## M6 — Distribution and release engineering (3–5 weeks)

**Work items**
- Produce versioned, reproducible release artifacts using supported Rust 1.96.x
  and recorded dependency/runtime provenance. Align package versions, scripts,
  docs and installed paths; verify licenses, checksums and supply-chain metadata.
- Test Linux packages/installers, upgrade/rollback/uninstall, binfmt/MIME/service
  registration and optional C/TAP/eBPF dependencies on clean hosts. Preserve user
  data and remove owned registrations without damaging other Wine installations.
- Define supported macOS architectures; test bundles/LaunchAgent handling and
  complete signing/notarization if shipping downloaded binaries. Document any
  unsigned/local-build-only support boundary rather than implying it is solved.
- Establish CI release gates, changelog/support procedures, diagnostics guidance
  and a security-reporting/update policy. Archive acceptance evidence for the
  exact shipped artifacts.

**Exit criteria**
- Clean-machine install, upgrade, rollback and uninstall pass on the supported
  platform matrix; checksums/provenance and explicit limitations accompany release.
- Artifact-to-source traceability, required CI results and M1–M5 acceptance
  evidence are available for the release candidate; no skipped containment gate
  is presented as a pass.
- Release documentation names supported Wine/OS/backend versions and known
  limitations, with a maintainable issue triage and update process.

**Boundary:** Flatpak remains deferred. macOS signing requires the appropriate
credentials and hardware access; neither is assumed available merely because
packaging scripts exist. Distribution does not confer a security certification.

## Completion rule

A milestone closes only when its exit criteria have evidence and unresolved
exceptions are accepted explicitly. Keep intended behavior in this plan and
verified behavior in the handoff; never promote a feature-presence probe, unit
test, displayed window or successful downgrade into an isolation guarantee.


