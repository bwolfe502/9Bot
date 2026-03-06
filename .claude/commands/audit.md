Perform a thorough codebase audit. Review everything with fresh eyes and report findings.

Present findings to the user organized by category. For each finding, note severity
(critical / warning / suggestion) and the specific file:line. Be opinionated — flag
things that a senior engineer would push back on in code review.

Do NOT fix anything. Only report findings. The user will decide what to act on.

## Categories to audit

### 1. Code quality
- Dead code (unused imports, unreachable branches, unused functions/variables)
- Copy-paste duplication that should be consolidated
- Overly complex functions (too long, too many branches, hard to follow)
- Magic numbers without explanation
- Inconsistent patterns (doing the same thing different ways in different places)
- Error handling gaps (bare excepts, swallowed exceptions, missing error paths)
- Thread safety issues (shared mutable state without locks, race conditions)

### 2. Comments and naming
- Stale comments that no longer match the code
- Commented-out code that should be deleted
- Misleading variable/function names
- Missing context where the "why" isn't obvious

### 3. Documentation
- CLAUDE.md accuracy — does it match the actual code? Outdated sections?
- Missing or wrong docstrings on public functions
- Outdated information in any docs (README, TESTING, etc.)

### 4. File structure and organization
- Files that are too large and should be split
- Code in the wrong module (violates the dependency graph in CLAUDE.md)
- Circular or unexpected dependencies
- Files that don't belong or serve no purpose

### 5. Security and robustness
- Input validation gaps
- Hardcoded secrets or credentials (beyond the intentionally obfuscated ones)
- Injection risks (command injection via ADB, path traversal, XSS in web dashboard)
- Unsafe file operations

### 6. Best practices
- Python anti-patterns (mutable default args, late binding closures, etc.)
- Things that deviate from standard Python conventions (PEP 8, naming, project layout)
- Test coverage gaps — important code paths with no tests
- Dependencies that could be simplified or removed

### 7. Opportunities
- Patterns that would benefit from a small refactor
- Performance issues (unnecessary repeated work, N+1 patterns)
- Things the user might not have considered (edge cases, failure modes)

## How to audit

1. Read every Python file in the project (not tests — focus on production code)
2. Cross-reference CLAUDE.md against actual code
3. Check the dependency graph for violations
4. Look at recent git history for patterns (repeated fixes in same area = underlying issue)
5. Review test coverage at a high level (what's tested vs what isn't)

## Output format

Organize findings by category. Within each category, sort by severity (critical first).
Keep each finding concise — one or two lines plus the file:line reference. If a category
has no findings, skip it (don't say "no issues found").

At the end, give a brief overall assessment: what's in good shape and what areas need
the most attention.
