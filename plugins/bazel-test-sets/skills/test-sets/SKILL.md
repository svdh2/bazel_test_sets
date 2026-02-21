---
name: test-sets
description: Work with the bazel_test_sets framework - setup projects, ask questions, author tests, and manage test lifecycle operations.
argument-hint: "[setup|ask|author|lifecycle] [details]"
---

# Test Sets Skill

Help the user work with the **bazel_test_sets** framework. Determine which capability they need and launch the appropriate agent.

## Routing

If the user provided `$ARGUMENTS`, use them to route directly:

- Starts with **setup** or mentions "set up", "integrate", "install", "new project", "configure" -> launch `setup` agent
- Starts with **ask** or is a question about bazel_test_sets (rules, macros, modes, logging, burn-in, etc.) -> launch `ask` agent
- Starts with **author** or mentions "create test", "write test", "add test", "test set", "structured logging" -> launch `author` agent
- Starts with **lifecycle** or mentions "burn-in", "deflake", "regression", "test-status", "build-graph", "re-judge", "run tests" -> launch `lifecycle` agent

If routing is ambiguous or no arguments provided, present the menu.

## Menu

Present these options to the user:

1. **Setup** -- Set up bazel_test_sets on an existing Bazel project or create a new project from scratch
2. **Ask** -- Ask questions about bazel_test_sets (rules, macros, execution modes, structured logging, burn-in, regression, reporting)
3. **Author** -- Author test sets, test_set_tests, and test implementations with structured logging and parameterization
4. **Lifecycle** -- Run burn-in, deflake, regression, test-status, build-graph, re-judge, and other lifecycle operations

## Launching Agents

Based on the user's choice, use the Task tool:

### Setup
```
subagent_type: "setup"
description: "Set up bazel_test_sets"
prompt: "Help the user set up bazel_test_sets in their project. [Forward any details from the user's message]"
```

### Ask
```
subagent_type: "ask"
description: "Answer test-sets question"
prompt: "Answer this question about bazel_test_sets: [Forward the user's question]"
```

### Author
```
subagent_type: "author"
description: "Author test sets"
prompt: "Help the user author test sets and tests. [Forward details about what they want to create]"
```

### Lifecycle
```
subagent_type: "lifecycle"
description: "Run lifecycle operation"
prompt: "Help the user with test lifecycle operations. [Forward the specific operation they want]"
```

## Example Interactions

```
User: /test-sets
-> Present the menu

User: /test-sets setup
-> Launch setup agent directly

User: /test-sets ask how does regression mode work?
-> Launch ask agent with "how does regression mode work?"

User: /test-sets author a payment test with structured logging
-> Launch author agent with "a payment test with structured logging"

User: /test-sets lifecycle burn-in my new tests
-> Launch lifecycle agent with "burn-in my new tests"
```
