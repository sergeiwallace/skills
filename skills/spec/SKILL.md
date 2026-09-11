---
name: spec
description: Present an implementation plan for review before making changes
license: Apache-2.0
---

# spec

Research and analyze the request, then present a plan for user approval.

This skill is an interactive plan-then-approve gate: it presents a plan in chat and produces **no
persistent document**.

**Usage:** `/spec <task description>`

## Workflow

1. **Analyze** the request — read relevant files, search the codebase, research as needed
2. **Present a plan** with:
   - What will change (files, functions, configs)
   - Why (rationale for each change)
   - Any trade-offs or alternatives considered
3. **Wait for approval** — do not proceed until the user says to go ahead
4. After approval, implement the plan as presented
