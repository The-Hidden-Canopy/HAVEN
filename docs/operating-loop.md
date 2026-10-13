# The HAVEN operating loop

HAVEN is easiest to use when it is treated as a loop, not as a collection of
settings screens:

```text
observe -> understand household state -> predict or interpret ->
check authority -> act -> observe consequence -> remember
```

This is both the product model and the user workflow. The model may help
interpret a request, a provider may report what it sees, and a discovery scan
may find a candidate. HAVEN still decides what is allowed, asks a person when
needed, performs only an authorized action, and records what happened.

## What each part means

### 1. Observe

HAVEN gathers available evidence from the sources you chose: devices, folders,
email or calendar sources, people and presence declarations, local models, and
your own conversations. **Today**, **Home**, **Computer**, and
**Communications** are the practical places to see that evidence.

Observation is not permission. A discovered device is a candidate until you
review and enroll it. A file is readable only inside a folder you allowed.

### 2. Understand household state

HAVEN combines observations with the household declarations you made: people,
rooms, contexts, projects, tasks, and prior memory. Open **Memory** when you
need to see where a claim came from or how certain it is.

Fresh, stale, fallback, and unavailable evidence are different states. A
missing or unreachable provider must not become a fictional empty world.

### 3. Predict or interpret

HAVEN may use a deterministic interpreter, a local model, or another provider
to answer a question, propose an action, draft a rule, or ask for
clarification. These outputs are proposals or explanations. A model response
is never, by itself, authority to change a device, file, or household record.

### 4. Check authority

Before a consequential action, HAVEN checks the household scope, the person's
role, the action's risk, evidence freshness and confidence, active overrides,
and whether confirmation is required. An approved automation and a direct
request go through the same authority boundary.

This is where **Needs You** matters: the queue is the place for decisions that
need a human review, not a place to hide an automatic action behind a vague
status label.

### 5. Act

Only an authorized request reaches an execution provider. Read-only computer
access stays separate from file organization; enabling write capability does
not remove the authority or confirmation checks. The same principle applies
to home devices and other providers.

### 6. Observe the consequence

After an action, HAVEN observes the provider again when the integration allows
it and reports the result. Return to **Today**, the relevant domain page, or
the action detail to distinguish a requested action from a completed effect.

### 7. Remember

Projects, tasks, claims, rules, receipts, and installation state persist in the
local installation. That lets the next loop begin with continuity rather than
requiring you to repeat the context every time HAVEN restarts.

## A practical daily rhythm

- **Start with Today.** Read the temporal overview and the **Needs You** count.
- **Review before acting.** Open each waiting item, read its evidence, and
  approve, confirm, correct, or dismiss it deliberately.
- **Go deeper only when useful.** Open Home, Computer, Communications, People,
  Memory, Projects, Tasks, or Models to inspect a source or make a focused
  change.
- **Reconnect inline.** If a page says a source is not connected, use that
  page's connect action or the connection center. Settings is for system-level
  controls and diagnostics, not a required detour.
- **Finish by checking the consequence.** Return to Today or the source page
  and confirm that the visible state matches what actually happened.

## What HAVEN deliberately does not do

- It does not treat a model's suggestion as an approved action.
- It does not enroll every discovered device automatically.
- It does not turn stale or unavailable evidence into a confident answer.
- It does not imply that a blank result means a provider is healthy.
- It does not require a cloud account for the local-first core.
- It does not require a terminal to remain open during normal native use.

When you are unsure what to do next, return to **Today**, open **Needs You**
if present, and follow the evidence to the page that owns the source.

Thank you for being here, and have fun making HAVEN useful in your own way.
