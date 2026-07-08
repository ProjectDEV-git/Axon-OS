# Pattern Matching Edge Cases & False Positives

**File:** `services/axon-brain/ai_router.py`
**Date:** 2026-07-02

---

## 1. Regex Pattern Inventory

### _SPEED_PATTERNS (6 patterns)
| # | Pattern | Intent |
|---|---------|--------|
| 1 | `^(yes\|no\|ok\|sure\|cancel\|stop\|next\|back\|close\|open)\s*$` | Single-word confirmations |
| 2 | `^(switch to\|go to\|open)\s+\w+` | Navigation commands |
| 3 | `^(run\|execute)\s+` | Shell execution |
| 4 | `^(what is\|what's)\s+\d+[\+\-\*\/]\d+` | Simple math |
| 5 | `^classif` | Classification tasks |
| 6 | `^(short\|brief\|quick\|one word)` | Length hints |

### _GENERAL_PATTERNS (4 patterns)
| # | Pattern | Intent |
|---|---------|--------|
| 1 | `(explain\|describe\|tell me about\|what is\|how does\|why does\|when did)` | Explanatory questions |
| 2 | `(summarize\|summarise\|review\|compare\|contrast)` | Content analysis |
| 3 | `(help\|assist\|suggest\|recommend\|advice)` | Assistance requests |
| 4 | `(write\|draft\|compose\|create)\s+(a \|an \|the )?(email\|letter\|message\|note\|document)` | Document writing |

### _CODE_PATTERNS (6 patterns)
| # | Pattern | Intent |
|---|---------|--------|
| 1 | `(write\|create\|generate\|implement\|build)\s+(a \|an )?(function\|class\|script\|program\|code\|module\|file)` | Code generation |
| 2 | `(fix\|debug\|debug\|patch\|refactor\|optimize\|rewrite)\s+(this\|the\|my)?\s*(code\|bug\|error\|function\|class)` | Code fixing |
| 3 | `(python\|javascript\|rust\|golang\|java\|c\+\+\|bash\|shell\|html\|css\|sql\|typescript)\s+(code\|function\|script)` | Language-specific |
| 4 | `(how to\|how do i\|how can i)\s+(implement\|write\|create\|build\|make)\s` | How-to code |
| 5 | `(refactor\|clean up\|restructure\|reorganize)\s` | Code restructuring |
| 6 | `` ``` `` | Inline code blocks |

### _EMBEDDING_KEYWORDS (10 keywords)
`search`, `find`, `locate`, `index`, `embed`, `vector`, `similar`, `semantic`, `relevant`, `matching`

---

## 2. Classification Flow (Critical Order)

The order in `classify_task()` is:
1. **Embedding** check (words intersection + len < 10)
2. **Code** check (score >= 1 AND word count > 5)
3. **Speed** check (regex match)
4. **General** check (score >= 1)
5. **Length-based fallback** (< 15 chars = speed, > 200 chars or > 2 questions = deep)
6. **Default** = general

---

## 3. Critical Bugs Found

### BUG 3.1: "explain this code" routes to SPEED (FALSE POSITIVE)
**Severity:** HIGH

The prompt "explain this code" is 3 words.
- Embedding check: `words & _EMBEDDING_KEYWORDS` = empty (no match). PASS.
- Code check: Pattern 6 `` ``` `` doesn't match. Other patterns: none match "explain this code". `code_score = 0`. PASS.
- Speed check: Pattern 1: "explain" not in the list. Pattern 6: "explain" doesn't start with "short/brief/quick/one word". But wait: **length fallback** kicks in after speed.
  - Actually wait. After speed patterns, general patterns are checked: `(explain|describe|...)` matches! `general_score = 1`. Returns **GENERAL**.

Let me re-trace: "explain this code"
1. Embedding: no keyword match. Skip.
2. Code: score = 0. word count = 3, which is NOT > 5. Skip.
3. Speed: No pattern matches "explain this code". Skip.
4. General: pattern 1 matches "explain". Returns **GENERAL**.

This is actually correct. The `explain this code` case is fine.

### BUG 3.2: "fix the bug" routes to SPEED (FALSE POSITIVE)
**Severity:** HIGH

"fix the bug" = 4 words.
1. Embedding: no keyword match. Skip.
2. Code: Pattern 2: `(fix|debug|...) (this|the|my)? (code|bug|...)` - matches "fix" + "the" + "bug". `code_score = 1`. But word count is 4, which is NOT > 5. **Code check fails!** Skip.
3. Speed: No pattern matches. Skip.
4. General: Pattern 3: `(help|assist|...)` - no match. Others don't match. `general_score = 0`. Skip.
5. Length fallback: `len("fix the bug") = 11`, which is `< 15`. Returns **SPEED**.

**Result:** "fix the bug" (a legitimate code task) routes to SPEED model. This is a **false positive**. The word count > 5 gate for code patterns is too strict for short code-fix requests.

### BUG 3.3: "find" alone routes to EMBEDDING (FALSE POSITIVE)
**Severity:** MEDIUM

"find" = 4 chars.
1. Embedding: `words = {"find"}`, `_EMBEDDING_KEYWORDS` contains "find". `words & keywords = {"find"}`. Length check: `len("find".split()) = 1`, which is `< 10`. Returns **EMBEDDING**.

**Result:** Typing "find" as a standalone word routes to embedding model. This would fail on Ollama if nomic-embed-text is expected to produce text. However, `GetEmbeddings` in brain_service.py only uses embedding models for vector ops, so in practice this routing only affects `select_model()` which returns the embed model name -- but that model name is then used for text generation, which would fail.

### BUG 3.4: "search" alone routes to EMBEDDING
**Severity:** MEDIUM

Same issue as 3.3. "search" is 6 chars, one word, contains an embedding keyword. Routes to EMBEDDING. The user probably wants a search action, not a vector embedding.

### BUG 3.5: Empty string input
**Severity:** LOW (safe)

`classify_task("")` after `.lower().strip()` returns `""`.
1. `words = set("".split()) = set()`. Empty intersection. Skip embedding.
2. Code score = 0. Skip.
3. Speed patterns: Pattern 1 `^(yes|no|...) ... $` - doesn't match empty. Pattern 5 `^classif` - no. None match.
4. General patterns: score = 0.
5. Length fallback: `len("") = 0 < 15`. Returns **SPEED**.

**Result:** Empty string routes to SPEED model. This is probably fine since `BrainService._validate_prompt()` catches empty prompts before they reach the router. But the router itself doesn't guard against it.

### BUG 3.6: Unicode and multibyte input
**Severity:** LOW

"日本語テスト" (Japanese test, 7 chars after strip):
1. Embedding: no keyword match. Skip.
2. Code: no regex match. Skip.
3. Speed: no regex match. Skip.
4. General: no regex match. Skip.
5. Length fallback: `len("日本語テスト") = 7 < 15`. Returns **SPEED**.

This is acceptable -- short non-English prompts defaulting to SPEED is reasonable.

### BUG 3.7: Very long prompts (10K chars)
**Severity:** LOW

`MAX_PROMPT_LEN = 10_000` in constants.py. `BrainService._validate_prompt()` rejects prompts longer than this. So the router never sees 10K+ prompts.

However, if it did: code patterns would likely match (`` ``` `` or language keywords), or the length fallback (`> 200` = DEEP) would trigger. This is correct behavior.

### BUG 3.8: Prompt injection via regex patterns
**Severity:** MEDIUM

An attacker could craft: `"yes\nrun rm -rf /"`
- `.lower().strip()` normalizes.
- Pattern 1: `^(yes|no|...) $` -- requires entire string to be the word (anchored to start/end). The newline means this won't match.
- Pattern 3: `^(run|execute) ` -- also anchored to start. Won't match if "yes" is first.
- Embedding keywords: none match.

**Mitigation present:** `MAX_PROMPT_LEN` limits input to 10K chars. `_validate_prompt()` checks length. However, there is **no sanitization of the prompt text itself** for the router -- it only sanitizes context via `_sanitize_context()`. Malicious prompts with embedded instructions (like "ignore previous instructions") would route to GENERAL or DEEP based on length/content, but the model itself is the attack vector, not the routing.

### BUG 3.9: Duplicate regex in _CODE_PATTERNS
**Severity:** LOW (code smell)

Pattern 2 contains `(fix|debug|debug|patch|...)` -- "debug" appears twice. Harmless but indicates copy-paste error.

### BUG 3.10: code_pattern threshold >= 1 with word count > 5 gate
**Severity:** HIGH

The `>= 1` threshold means ANY single code pattern match triggers deep routing, but ONLY if the prompt has > 5 words. This creates a gap:
- "fix this bug" (4 words): code pattern matches but blocked by word count gate. Falls through to SPEED via length fallback.
- "fix this bug in my code" (7 words): code pattern matches AND passes word gate. Routes to DEEP correctly.
- "refactor my API module" (4 words): code pattern 5 `(refactor|clean up|...) ` matches but word count gate blocks. Falls to SPEED.

**Recommendation:** Lower the word count gate to > 3, or remove it entirely and rely on the pattern matching.

---

## 4. False Positive Summary

| Prompt | Expected | Actual | Issue |
|--------|----------|--------|-------|
| "fix the bug" | DEEP | SPEED | Word count > 5 gate too strict |
| "refactor my API module" | DEEP | SPEED | Word count > 5 gate too strict |
| "find" | (ambiguous) | EMBEDDING | Single embedding keyword triggers vector routing |
| "search" | (ambiguous) | EMBEDDING | Single embedding keyword triggers vector routing |
| "yes, explain this" | SPEED | GENERAL | "yes" is in speed patterns but `^...$` requires exact match; "explain" catches it as GENERAL |

---

## 5. Recommendations

1. **Lower code gate from > 5 to > 3 words** to catch short code-fix requests like "fix the bug"
2. **Add embedding intent validation**: Require embedding keywords to appear with related context words (e.g., "search for", "find similar") rather than as bare keywords
3. **Remove duplicate "debug"** in _CODE_PATTERNS pattern 2
4. **Add empty/whitespace guard** at top of `classify_task()` to return SPEED explicitly
5. **Consider adding `re.DOTALL`** flag for multiline prompt handling (or strip newlines from input)
6. **Add context-aware overrides**: If context shows a code editor open, boost code score
