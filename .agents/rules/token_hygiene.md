# Antigravity Rule: Architect-Builder Token Discipline & Hygiene

## 1. Zero Cloud Token Waste
- **Local Builder Execution**: All code generation, iterative bug fixing, test running, compiler diagnostics, and file diff applications must be executed locally on the RTX 5090 at $0 cloud token cost.
- **Cloud Architect Role**: The Cloud Architect model (Gemini Pro) is reserved strictly for high-level system decomposition, milestone specs, and final architectural review. Never send high-churn code diff loops to cloud models.

## 2. Context Pruning & Token Hygiene
- **No Cat Dumps**: Never dump large raw files or whole-directory outputs into conversation turns. Use AST symbol lookups, grep/ripgrep, and targeted slicing.
- **Terminal Clamping**: Terminal and compiler logs exceeding 60 lines are automatically clamped by CascadeGateway down to the head (15 lines) and tail (30 lines).
- **Buffer Deduplication**: Full file buffers identical to preceding turns are automatically de-duplicated by CascadeGateway and replaced with turn references.

## 3. Subagent Routing Convention
- `@architect`, `[architect]`, `/plan` $\rightarrow$ Cloud Architect Lane.
- `@coder`, `@builder`, `@test_runner`, `[builder]` $\rightarrow$ Local Builder Lane (RTX 5090).
