# tau2-bench v1.0.1 half-duplex text evaluation contract

This document defines a source, secrecy, execution, and evidence contract for
the public tau2-bench v1.0.1 half-duplex text core. It does **not** report an
evaluation. No public candidate, model, agent, user simulator, or grader is
claimed to have run; no task reward, pass rate, external score, or usefulness
result exists under this contract.

The contract is deliberately result-blind. Committed upstream result files,
leaderboard data, plots, and scores are excluded from intake and from every
candidate-visible surface.

## Frozen upstream identity

The only accepted upstream source is
[`sierra-research/tau2-bench`](https://github.com/sierra-research/tau2-bench)
at annotated tag `v1.0.1`:

- annotated tag object
  `b711c1ead46f55111bf765cf44d5da8bacc2d28c`;
- peeled commit `fc0055dc4e0a316c3f83133267fbd6faaa770992`.

The tag name alone is not an identity. A source descriptor must bind the
repository URL, tag object, peeled commit, required Git blob identities, exact
bytes and sizes, and the hashes below. A commit hash is not a signature,
producer attestation, or license conclusion.

### Exact 278-row cohort

The cohort is the complete `base` split for three domains, in this explicitly
frozen cross-domain order:

1. `airline`;
2. `retail`;
3. `telecom` using the manual, not workflow, policy.

| Domain | Physical task rows | Selected `base` rows | Task path | Task byte count | Task SHA-256 | Split path | Split byte count | Split SHA-256 |
| --- | ---: | ---: | --- | ---: | --- | --- | ---: | --- |
| `airline` | 50 | 50 | `data/tau2/domains/airline/tasks.json` | 155,528 | `ccd8ba737b4cc371415af70151187788f728d6108d0916e73bb4317b40542052` | `data/tau2/domains/airline/split_tasks.json` | 1,443 | `b22ced4d9a9850ac9aea31c53bdcb6d6009058140bd9acc7db37c1d36222ba8b` |
| `retail` | 114 | 114 | `data/tau2/domains/retail/tasks.json` | 345,982 | `8e03ebce7901bd6218e7a7dc3105faa9324091a68058f7fe61c65262868812e8` | `data/tau2/domains/retail/split_tasks.json` | 3,263 | `ed0580ec52575b63fbf76568af42490da6ee7783ecb4aa81af46961291358f20` |
| `telecom` | 2,285 | 114 | `data/tau2/domains/telecom/tasks.json` | 13,977,063 | `37e562e1ae3242577407e1303b1548bc64e7ea68e37d36173e6747990ceaf8a4` | `data/tau2/domains/telecom/split_tasks.json` | 356,149 | `605b488bb9a6acb3c7f4505240a855fdc8681d09aadb16a8f38b2efcfc5c3aec` |

The total is exactly 278 unique selected task ids. The three `base` lists also
contain unique ids. No task may be sampled, replaced, reordered, or omitted.

Within a domain, actual loader order is the physical order of `tasks.json`
filtered by membership in the selected split. It is not split-file order and
must not be reconstructed by sorting task ids. This follows the pinned
[airline loader](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/airline/environment.py#L35-L45),
[retail loader](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/retail/environment.py#L35-L46),
and
[telecom loader](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/telecom/environment.py#L170-L180).
The cross-domain order above is a local protocol decision because upstream
does not define one combined three-domain sequence.

A verifier must independently replay the loader rule from the exact task and
split bytes, then bind every selected row's domain ordinal, SHA-256 of the exact
`domain<TAB>task_id` key, complete hidden-task hash, and aggregate ordered-list
hash. The committed descriptor must not serialize raw upstream task ids because
telecom ids encode fault and persona details. The raw id mapping and descriptor
remain coordinator-only. A future candidate-facing id is separately derived
with a secret random key. Opaque ids are still potentially relinkable from
public task order and observed interactions; they are not anonymity proof.

### Included mode and explicit exclusions

The included mode is normal interactive text evaluation:

- half-duplex, turn-based messages;
- standard `llm_agent` semantics, not a ground-truth or solo agent;
- standard LLM user simulator;
- `EvaluationType.ALL` reward-basis behavior;
- `airline`, `retail`, and manual-policy `telecom` only;
- `base` tasks only.

The following are outside this contract:

- full-duplex, streaming, voice, audio, and voice-task assets;
- `llm_agent_gt`, `llm_agent_solo`, oracle plans, tickets, and dummy-user mode;
- telecom workflow policy and workflow-specific comparisons;
- `mock`, `banking_knowledge`, retrieval, training, and non-base splits;
- committed result, leaderboard, plot, submission, and result-shaped fixture
  data;
- any upstream revision other than the peeled v1.0.1 commit.

The telecom policy is the exact runtime concatenation of `main_policy.md` and
`tech_support_manual.md`, including the upstream wrapper tags. The construction
is pinned in
[`get_environment`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/telecom/environment.py#L100-L140).

## Candidate-visible allowlist

Candidate visibility is an allowlist, not a list of known secrets to redact.
The serialized candidate request may contain only the following semantic
material.

### Transport envelope

The transport may expose:

- a schema and protocol version;
- an opaque task id used only for correlation;
- a bounded turn ordinal;
- the exact agent system prompt;
- ordered serialized agent tool schemas;
- the agent-view message history or next input message.

The opaque id need not be inserted into the model prompt. If the candidate
program can read the envelope, it is nevertheless candidate-visible and must
be treated that way.

### Agent system prompt and policy

The candidate receives the normal fixed `AGENT_INSTRUCTION` and the exact
domain policy in the normal system-prompt template. It receives no additional
task-specific system text. The construction is defined in the pinned
[`LLMAgent`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/llm_agent.py#L24-L82).

The system prompt and policy are intended visible inputs. Their exact bytes,
construction algorithm, and SHA-256 must be retained per domain so that a
coordinator cannot silently substitute a prompt while preserving the source
task identity.

### Tool schemas, calls, and results

The candidate receives an ordered array of assistant-tool schemas containing
only the OpenAI function projection:

- function name;
- function description;
- JSON-schema parameters.

This is the projection produced by pinned
[`Tool.openai_schema`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/environment/tool.py#L138-L149).
The candidate must never receive live `Tool` instances, Python callables,
predefined arguments, environment objects, return-model internals, exception
metadata, source files, or database handles.

Candidate-emitted tool calls may contain only a coordinator-assigned or
validated nonempty call id, a visible tool name, and a JSON object of
arguments. The coordinator executes valid calls and returns only the semantic
tool result that the normal model would see: role `tool`, result content, and
the matching tool-call id. Internal `requestor`, `error`, timestamp, database,
and exception-object fields remain hidden. An error string that is the normal
tool result content remains visible because it is part of the interaction.

### Agent-view messages

The exact semantic message projection is the pinned
[`to_litellm_messages`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py#L168-L208):

| Message | Candidate-visible fields |
| --- | --- |
| User text | `role=user`, `content` |
| Prior assistant text | `role=assistant`, `content` |
| Prior assistant tool request | `role=assistant`, call id, name, and JSON arguments |
| Assistant-requested tool result | `role=tool`, `content`, matching call id |

Multiple tool results are expanded into individual tool messages before the
next agent call, as in pinned
[`LLMAgent._generate_next_message`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/llm_agent.py#L115-L134).

The candidate does not receive timestamps, turn indexes, cost, usage, raw
provider response, generation timing, audio data, source metadata, user-tool
traffic, or messages addressed only to the simulator. The internal message
model contains many such fields, but the normal model projection drops them;
see pinned
[`ParticipantMessageBase`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/message.py#L192-L267)
and
[`ToolMessage`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/message.py#L554-L595).

User tool calls and their results are simulator-only. The normal agent history
accepts user text but excludes user tool calls and tool results requested by
the user; see
[`is_valid_agent_history_message`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/base_agent.py#L38-L46).

## Coordinator-, simulator-, and grader-only material

Everything not explicitly allowed above remains outside candidate bytes,
process arguments, environment variables, inherited handles, working
directories, mounts, caches, logs, error messages, and network reachability.

| Upstream material | Owner | Candidate treatment |
| --- | --- | --- |
| Original task id and domain/order mapping | Coordinator | Replace with opaque id; never serialize mapping or key |
| `description.{purpose,relevant_policies,notes}` | Evaluator/curation | Exclude |
| `user_scenario.{persona,instructions}` | User simulator | Exclude; only generated user messages become visible |
| `ticket` | Solo mode | Exclude; solo mode is out of scope |
| `initial_state.initialization_data` | Environment | Exclude |
| `initial_state.initialization_actions` | Environment | Exclude |
| `initial_state.message_history` | Coordinator | Exclude except for the exact agent-view projection if a future cohort contains history |
| `evaluation_criteria.actions` and arguments | Evaluator | Exclude |
| Environment assertions | Evaluator | Exclude |
| `communicate_info` | Evaluator | Exclude |
| Natural-language assertions | Grader | Exclude |
| `reward_basis` | Evaluator | Exclude |
| `issues`, `required_documents`, raw `annotations` | Curation/evaluator | Exclude |
| `user_tools` selection and live user tools | User simulator | Exclude |
| Initial/final DBs, hashes, replay environments | Coordinator/evaluator | Exclude |
| Simulator guidelines, prompt, model response, raw data | User simulator | Exclude |
| Grader prompt, model response, reasoning, reward | Grader | Exclude |
| Source descriptor, checkout, mirror, task JSON, result files | Coordinator | Never mount or copy to candidate |

The upstream Task model locates these fields in pinned
[`tasks.py`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/tasks.py#L15-L113),
[`EvaluationCriteria`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/tasks.py#L366-L437),
and
[`Task`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/tasks.py#L560-L622).

For this exact cohort, all 50 airline and 114 retail rows have no initial
state. All 114 telecom rows have hidden initialization actions. None of the
278 rows has initial message history. Consequently, no task-originated initial
message may appear in a candidate request for this core.

The simulator and environment are allowed to reveal information through normal
user messages and tool results. Those observations are the task interaction,
not authorization to expose the hidden source fields that generated them.

## Prohibition on the upstream in-process agent factory

Candidate code must not be registered or constructed through the upstream
in-process factory path.

Pinned
[`build_agent`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/build.py#L68-L125)
passes every registered factory both live environment-backed `Tool` objects and
the full `Task`, including simulator instructions and evaluator gold. The
registry accepts arbitrary agent factories; see pinned
[`register_agent_factory`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/registry.py#L106-L147).

The risk is concrete rather than hypothetical. Upstream `LLMGTAgent` consumes
expected actions and inserts resolution steps, optionally including their
arguments, into its system prompt: pinned
[`LLMGTAgent`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/llm_agent.py#L166-L218)
and
[`make_agent_instructions_from_actions`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/llm_agent.py#L262-L291).

A conforming controller instead launches an external candidate adapter and
gives it only the serialized allowlist. All tool execution remains in the
coordinator. The candidate process must not inherit the upstream checkout,
mirror, task/result directories, credentials, open handles, coordinator
environment variables, or grader state.

An external subprocess is only a serialization boundary. It is not by itself
filesystem, network, user, process, mount, or image isolation. Those claims
remain false until a separately verified sandbox manifest and runtime evidence
establish them.

## Communication protocol hardening

Upstream `TextRunConfig.enforce_communication_protocol` defaults to `false`;
see pinned
[`TextRunConfig`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py#L514-L567).
A conforming run descriptor must set it to `true`, and retained construction
evidence must show that the actual orchestrator received `true`.

Upstream enforcement rejects an empty message and a message containing both
text and tool calls; see pinned
[`_check_communication_error`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/orchestrator/orchestrator.py#L668-L733).
The external adapter must additionally fail closed on cases the internal type
model does not fully constrain:

- exactly one of non-whitespace text or a nonempty tool-call array;
- assistant role only;
- no unknown envelope or message fields;
- no audio, streaming, raw-provider, cost, usage, or timing fields;
- positive frozen limits for request, response, content, argument, tool-call,
  per-turn, total-turn, and total-capture sizes;
- unique, nonempty, bounded tool-call ids;
- a visible, frozen tool name;
- a JSON object of arguments satisfying the frozen parameter schema;
- no duplicate JSON keys, trailing data, non-finite numbers, or invalid
  Unicode;
- exact one-to-one call-id correspondence for coordinator-created tool results;
- deterministic rejection before any invalid call reaches the environment.

`tool_calls=[]` is not a valid tool message even though upstream's
`is_tool_call` treats any non-`None` list as a tool-call message. The adapter
must reject it rather than allow later empty-result indexing.

Normal `LLMAgent` does not define an agent stop token; its inherited `is_stop`
returns false. The adapter must not invent one. Standard termination comes
from the user simulator, the orchestrator's bounds, or an error. The pinned
default is in
[`HalfDuplexParticipant.is_stop`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/base/participant.py#L90-L103).

All execution settings must be explicit in a run descriptor. Defaults may be
recorded as upstream defaults, but a claim-bearing run may not infer its model,
provider, model arguments, trial count, seed, concurrency, retry policy,
maximum steps, maximum tool errors, or wall-clock timeout after the fact.

## User-simulator boundary and nondeterminism

The half-duplex user simulator receives global hidden guidelines and the full
hidden task scenario in its system prompt. The construction is pinned in
[`user_simulator.py`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/user/user_simulator.py#L87-L162).
Each user turn is an external LLM call, and the returned assistant-form message
is converted into a user message; see pinned
[`_generate_next_message`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/user/user_simulator.py#L198-L266).

The pinned defaults use `gpt-4.1-2025-04-14` with temperature `0.0`, and the
orchestrator forwards the run seed to the simulator. Neither temperature zero
nor a seed proves deterministic provider behavior, simulator faithfulness, or
absence of hallucination. The half-duplex path does not receive the voice
hallucination retry: the retry loop is conditioned on full-duplex mode in
[`runner/batch.py`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/batch.py#L689-L694).

For each attempted turn, coordinator evidence must retain and bind:

- simulator implementation and exact loaded-code identity;
- provider and exact model string;
- model arguments, seed, retry policy, and request ordinal;
- hidden prompt SHA-256 without exposing prompt text to the candidate;
- raw bounded provider request/response evidence or an independently
  authenticated equivalent;
- parsed user message, token/cost evidence when available, and failure status.

Missing, malformed, refused, timed-out, or unauthenticated simulator evidence
is a simulator failure, not a candidate failure and not a task score. A
rerun is a new stochastic attempt and may not overwrite the earlier attempt.
Simulator correctness, non-hallucination, and reproducibility remain false
until separately measured.

## Retail natural-language grader boundary

The 114 selected retail rows have the following exact reward-basis and
natural-language-assertion distribution:

| Reward basis | NL assertions per task | Tasks | Actual NL judge call |
| --- | ---: | ---: | --- |
| `DB` | 0 | 2 | No |
| `DB + NL_ASSERTION` | 0 | 72 | No; upstream returns neutral `1.0` |
| `DB + NL_ASSERTION` | 1 | 25 | Yes |
| `DB + NL_ASSERTION` | 2 | 10 | Yes |
| `DB + NL_ASSERTION` | 3 | 4 | Yes |
| `DB + NL_ASSERTION` | 4 | 1 | Yes |

Thus 112 retail rows include `NL_ASSERTION` in their reward basis, but only 40
rows make an NL judge call, covering 61 assertions. For context, all 50 airline
rows use `DB + COMMUNICATE`; their populated NL assertions are non-gating under
`EvaluationType.ALL`. Telecom uses `ENV_ASSERTION` for 94 rows and
`ENV_ASSERTION + ACTION` for 20.

The pinned NL evaluator uses `gpt-4.1-2025-04-14`, temperature `0.0`, no
explicit grader seed, and disabled LLM caching by default; see pinned
[`config.py`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/config.py#L15-L48).
That call is nondeterministic external evidence.

The upstream parser also has a fail-open structural defect. It requests
free-form JSON, applies bare `json.loads`, accepts `result_data.get("results",
[])`, does not reconcile returned outcomes to input assertions, and computes
`all(...)`. A missing or empty `results` array therefore passes vacuously.
Duplicate, omitted, extra, altered, or reordered `expectedOutcome` values are
not rejected. The relevant code is pinned in
[`NLAssertionsEvaluator`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py#L46-L135).
When NL is in the reward basis, its reward is multiplied into the combined
reward by pinned
[`evaluate_simulation`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator.py#L196-L275).

A hardened wrapper must preserve valid upstream judgments but refuse malformed
grader evidence. Before accepting a response, it must require:

- valid bounded JSON with the exact allowed response schema;
- exactly one result for each input assertion;
- no missing, duplicate, extra, altered, or unmatched `expectedOutcome`;
- exact multiset reconciliation and a frozen ordering rule;
- a boolean `metExpectation` and bounded string `reasoning` for every item;
- exact grader prompt, assertion-list, trajectory, model, arguments, and
  loaded-code hashes;
- retained raw bounded response bytes and parse disposition.

Any structural mismatch, transport failure, refusal, timeout, or missing raw
evidence is `grader_failed`; it is never a pass or an inferred zero. Tasks with
zero NL assertions preserve the upstream neutral result without making a
grader call. Even valid structurally hardened output remains an
external-judge-dependent result and must not be described as deterministic.

## Upstream committed-result exclusion

The pinned Git tree contains result-bearing material. Path-and-size inspection,
without opening payloads or scores, identifies:

- `data/tau2/results/**`: 53 blobs totaling 604,165,547 bytes;
- `web/leaderboard/**`: 144 blobs totaling 37,464,930 bytes;
- additional result-shaped fixtures under voice tests.

These paths are excluded from acquisition outputs used for evaluation and from
every candidate-accessible namespace. The exclusion also covers derived plots,
tables, summaries, submissions, caches, task-issue simulations, test result
fixtures, and filenames or indexes deliberately selected from their contents.

An upstream `Results` artifact is especially unsafe as candidate input because
it embeds `tasks: list[Task]`, not only trajectories or aggregate metrics. The
normal JSON writer serializes that model. See pinned
[`Results`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py#L1371-L1397)
and
[`Results.save`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py#L1501-L1532).

The candidate input must be built as a new allowlisted projection. A sparse
checkout is not a substitute: adjacent hidden task, DB, simulator, grader, and
result files can still be opened or reached through links. A verifier must scan
the resolved candidate filesystem, mounts, inherited handles, and archives for
excluded paths, symlinks, junctions, and other reparse points. If outbound
network access is not demonstrably denied, a no-leakage claim remains false
because the candidate could fetch the public upstream task and result data.

## Complete denominator and failure dispositions

For one trial, the physical denominator is exactly 278 rows. For `n` trials it
is exactly `278 * n` row-trials. Trial count and seed schedule must be frozen
before execution. No failure, refusal, retry, or missing grade may shrink the
physical denominator.

Every row-trial receives three separately totalized stage dispositions.

### Preparation

Exactly one of:

- `not_attempted`;
- `source_invalid`;
- `source_refused`;
- `ready`.

Source validation or preparation is not candidate execution and must not be
called a passed task.

### Execution

Exactly one of:

- `not_attempted`;
- `candidate_launch_failed`;
- `candidate_protocol_refused`;
- `candidate_process_failed`;
- `candidate_timeout`;
- `simulator_failed`;
- `tool_error_limit`;
- `max_steps`;
- `wallclock_timeout`;
- `agent_error`;
- `user_error`;
- `infrastructure_error`;
- `completed_user_stop`;
- `completed_agent_stop`.

Per-call tool errors are retained as trajectory events. They become the
terminal `tool_error_limit` only when the frozen limit terminates the
simulation. A protocol failure is not relabeled as a model answer, and an
infrastructure or simulator failure is not relabeled as candidate failure.

Upstream evaluation gives a reward of zero to a completed `SimulationRun` that
terminates for a reason other than user or agent stop; see pinned
[`evaluate_simulation`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator.py#L88-L135).
That rule may be applied only when the exact run and termination evidence
exists. A failure before a valid simulation result exists has no official
reward and must not be silently coerced to zero.

### Grading

Exactly one of:

- `not_attempted`;
- `not_eligible` because no valid gradeable simulation exists;
- `grader_failed`;
- `graded`.

Only `graded` may carry reward components. Grader failure is missing evidence,
not reward zero. Any official metric that excludes infrastructure rows must be
reported, if at all, alongside the full 278-row-trial physical denominator and
an exact exclusion count. It must not be labeled a complete-cohort score.

Retries append attempts. They never replace or erase the original disposition,
trajectory, capture, simulator response, or grader response. A separately
defined selection rule may identify a primary attempt, but all attempts and
the selection input remain replayable.

A score or usefulness field stays absent or false until every selected
row-trial expected by the frozen protocol reconciles, every included execution
and grade validates, and the score formula is replayed from retained evidence.

## Evidence required before any result claim

A claim-bearing implementation needs, at minimum:

1. A source descriptor binding the exact upstream objects, required file
   bytes, loader order, 278-row selection, hidden row hashes, policy bytes,
   ordered tool schemas, exclusions, and opaque-id derivation protocol.
2. A candidate-input verifier that independently rebuilds the projection from
   hidden source and proves recursive allowlist conformance.
3. Candidate code, executable, model, provider, prompt, token, tool-schema,
   controller, and sandbox identities bound before launch.
4. Bounded raw candidate stdout/stderr or equivalent authenticated transport
   captures, with complete ordinal and byte reconciliation.
5. Simulator prompt/model/configuration identity, raw response evidence, and
   all stochastic attempts.
6. Exact tool requests/results, environment initialization and replay
   evidence, full message trajectory, and termination evidence.
7. Raw grader request/response evidence plus fail-closed structural validation.
8. One preparation, execution, and grading disposition for every physical
   row-trial, followed by independent score replay.

Self-hashes detect accidental mutation only within their stated construction.
They do not authenticate the producer, prove which code was loaded, establish
process isolation, or prevent a producer from fabricating a new internally
consistent record.

## Required negative tests

A conforming implementation must pass at least these synthetic tests before a
public run is considered:

- place distinct canaries in every hidden Task, simulator, DB, grader, source
  id, result, and opaque-key field; assert that no canary or hidden field name
  occurs in candidate request bytes;
- prove a malicious registered upstream factory can observe the prohibited
  full Task/live tools, then prove the production path never invokes that
  factory interface;
- assert the candidate receives serialized tool schemas rather than Python
  objects or callable references;
- exercise user tool calls and prove neither those calls nor their results
  enter agent-view history;
- verify all 278 selected rows have no task-originated initial agent message;
- reproduce exact airline, retail, and telecom policy construction and ordered
  tool-schema hashes;
- refuse empty, mixed, empty-tool-array, malformed, oversized, unknown-tool,
  duplicate-id, schema-invalid, non-finite, invalid-Unicode, trailing-data, and
  late candidate output while retaining exact dispositions;
- scan candidate files, archives, links, junctions, mounts, handles, and
  environment for the suite descriptor, hidden source, DB, result, key, and
  grader material;
- feed the retail NL wrapper missing, empty, duplicate, extra, altered,
  reordered, non-boolean, malformed, and oversized responses; every case must
  become `grader_failed`;
- verify retained grader evidence without making another LLM call; a re-grade
  is new stochastic evidence, not replay;
- reconcile all 278 row-trials exactly once at every stage and reject
  denominator shrinkage, duplicate rows, and silent retry replacement;
- reject any result document that sets a score/usefulness/claim-ready field
  without all required upstream evidence.

## Explicit claim boundary

Source binding and a security contract do not establish external usefulness.
Until the corresponding retained public evidence exists and verifies, all of
the following claims remain false:

- a public candidate, model, or agent ran;
- the official user simulator or grader ran;
- candidate execution was isolated from the filesystem, network, credentials,
  other processes, task source, evaluator gold, or committed results;
- candidate/model/code identity, tokens, cost, or trajectory were
  independently authenticated;
- the user simulator was faithful, deterministic, or free of hallucination;
- the retail NL grader was deterministic, correct, or structurally sound;
- any task was resolved or received a valid reward;
- a pass rate, pass@k, pass^k, aggregate score, leaderboard-comparable result,
  or complete-cohort metric exists;
- the system is useful on tau2-bench or any other external suite;
- the system is better than another method or satisfies a public superiority
  threshold;
- the evidence is claim-ready;
- the pinned source identity establishes authenticity, redistribution rights,
  or a legal conclusion.

When evidence eventually exists, report only the narrowest statement directly
supported by the frozen descriptor, complete denominator, verified boundary,
and replayed grader records. Do not convert absence of detected leakage,
nondeterminism, or failure into proof that none occurred.
