# Master prompt — Axon OS UI overhaul

Paste everything below the line into a fresh agent session on the Axon-OS repository.

---

You are improving the user interface of Axon OS, an AI-native Linux desktop
(Ubuntu 24.04, GNOME Shell 45+ extension in JavaScript, GTK4/libadwaita apps in Python).

**Your plan is `docs/ui-overhaul-plan.md`.** It lives on branch
`claude/intelligent-faraday-10w54s`. If it's not in your checkout, run
`git fetch origin claude/intelligent-faraday-10w54s` and base your work on that branch.

How to work:

1. Read `AGENTS.md` (Axon-OS section), `docs/architecture.md`, `DEVELOPING.md` and the
   whole plan before changing anything. Then do Phase 0 (baseline QA run).
2. Work through the phases **in order** (1 → 5). Inside a phase, work top to bottom
   unless an item depends on a later one.
3. For each item: read the code it names, make the smallest change that does what the
   item says, add tests for any logic you can put in a pure function, then run
   `ruff check`, `ruff format --check`, `mypy` and `pytest` exactly as the plan's
   "Ground rules" lists them.
4. Commit each item (or small group of related items) separately with a conventional
   commit message, ticking its box in the plan in the same commit.
5. Push at the end of each phase. Don't open a pull request unless asked.
6. If something in the plan turns out to be wrong (the code has changed, a GNOME API
   doesn't exist, an item is already done), don't force it: adjust or skip it and write
   a one-line note under the item explaining why.

Hard rules:

- Never weaken command safety: the allowlist and the fail-closed confirmation in
  `services/service_utils.py` stay. A new confirmation UI must also treat timeout,
  error or a closed dialog as "no".
- Never claim the UI says "no cloud connection" or "local only" without checking the
  selected model. Cloud models (`@provider/model`) are supported.
- Don't delete apps or features, and don't touch other projects in this workspace.
- GNOME Shell can't run in your container. Syntax-check the JS, keep Shell logic you can
  test in small pure functions, and list clearly what you could not test in a real
  GNOME session.
- Keep CI green (ruff → mypy → pytest with ≥40% coverage → bandit).

When you stop (finished, blocked, or at a sensible phase boundary), add a dated entry
to the "Progress log" at the bottom of the plan, push, and reply with:

- which items are done, skipped (and why), or still open;
- anything that needs testing on a real Axon OS machine;
- any decision you need from me.
