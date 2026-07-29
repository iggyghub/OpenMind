---
name: tdd
description: Test-driven development with a red-green-refactor loop, one behavior at a time. Use when the user wants a feature or bug fix built test-first, mentions "TDD" or "red-green-refactor", or asks for integration-style tests before implementation.
kind: procedure
tools: [run_command, read_file, create_file]
---

# Test-driven development

## Philosophy

Tests verify behavior through public interfaces, not implementation details.
A good test reads like a spec -- "user can checkout with a valid cart" -- and
survives a refactor because it never inspects internal structure. A bad test
mocks internal collaborators or reaches around the interface (e.g. querying a
database directly instead of calling the API); it breaks on refactors even
when behavior hasn't changed.

## Anti-pattern: horizontal slices

Do not write all the tests first and then all the implementation. That
produces tests for *imagined* behavior instead of *actual* behavior, and they
go insensitive to real breakage. Use vertical slices instead: one test, one
minimal implementation, repeat. Each cycle is informed by what the previous
one taught you.

```
WRONG:  test1, test2, test3 -> impl1, impl2, impl3
RIGHT:  test1 -> impl1 -> test2 -> impl2 -> test3 -> impl3
```

## Workflow

### 1. Plan

Before writing any code, confirm with the user:
- What public interface is changing.
- Which behaviors matter most (you can't test everything -- prioritize
  critical paths and complex logic).

Use the project's own domain vocabulary (CONTEXT.md, ADRs) for test names and
interface shape so tests read as native to the codebase, not generic.

### 2. Tracer bullet

Write ONE test for ONE behavior. Run it with `run_command` (e.g.
`pytest path/to/test_x.py -q`) and confirm it fails for the right reason
(RED). Write the minimal code to pass it (GREEN). This proves the path works
end-to-end before you build anything else on top of it.

### 3. Incremental loop

For each remaining behavior: write the next test, watch it fail, write only
enough code to pass it, watch it pass. Don't anticipate future tests or add
speculative code the current test doesn't require.

### 4. Refactor

Only once all target tests are green: extract duplication, deepen modules
(small interface, most of the complexity hidden behind it), and re-run the
full suite after each refactor step. Never refactor while a test is RED --
get to GREEN first.

## Per-cycle checklist

- [ ] Test describes behavior, not implementation
- [ ] Test only touches the public interface
- [ ] Test would survive an internal refactor
- [ ] Code added is the minimum needed for this test
- [ ] No speculative/unrequested features slipped in
