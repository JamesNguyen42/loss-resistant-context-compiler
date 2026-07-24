# Related work

This page positions the project against primary sources available on
2026-07-23. The systems solve overlapping but non-identical problems, so their
reported numbers are not head-to-head results.

> **Evidence boundary:** the recorded certificate is `local-bundled-only`.
> No named external system has been run in this repository, so no external
> state-of-the-art or "50% better than most" claim is supported.

## ACON

[ACON: Optimizing Context Compression for Long-horizon LLM Agents
(arXiv:2510.00615)](https://arxiv.org/abs/2510.00615) optimizes natural-language
compression guidelines from trajectories where full context succeeds and
compressed context fails, then distills the optimized compressor into smaller
models. The paper reports 26–54% reductions in peak token use while largely
preserving task performance, and up to 46% improvement when enhancing smaller
models as long-horizon agents. The
[official Microsoft implementation](https://github.com/microsoft/acon) covers
AppWorld, OfficeBench, and multi-objective QA pipelines.

ACON focuses on learned, task-performance-driven compression policy. This
project focuses on a model-agnostic typed ledger, exact character-span
provenance, full deterministic rule recovery, protected-only coverage
certification, clause atomization, revocation-aware temporal state, and
explicit invariant failure. Its optional model adapter emits only exact
complete atomic source spans rather than paraphrased summaries. Those are
complementary directions. No experiment in this repository compares them
directly.

## FoldAgent

[Scaling Long-Horizon LLM Agent via Context-Folding
(arXiv:2510.11967)](https://arxiv.org/abs/2510.11967) gives an agent a learned
operation for branching into a sub-trajectory and folding completed work back
into a concise outcome. Its FoldGRPO training procedure encourages task
decomposition and context management. The paper reports matching or exceeding
ReAct baselines on its long-horizon research and software-engineering settings
with 10x smaller active context. Code is available in the
[FoldAgent repository](https://github.com/sunnweiwei/FoldAgent).

FoldAgent changes agent policy and trajectory structure. This compiler is an
external transformation that can sit beside an otherwise unchanged agent and
underlying LLM. Folded summaries and typed, provenance-linked memory could be
combined, but that integration has not been evaluated here.

## AMA-Bench and AMA-Agent

[AMA-Bench: Evaluating Long-Horizon Memory for Agentic Applications
(arXiv:2602.22769)](https://arxiv.org/abs/2602.22769) evaluates memory over real
agent trajectories with expert-curated QA and synthetic trajectories that can
scale to arbitrary horizons. The paper argues that causality and objective
information are important weaknesses in similarity-based retrieval, and
introduces AMA-Agent with a causality graph and tool-augmented retrieval. The
[official benchmark repository](https://github.com/AMA-Bench/AMA-Bench)
provides a common construction/retrieval interface.

AMA-Bench is a much broader downstream memory evaluation than this project’s
local LRCBench. LRCBench isolates explicit commitment recall, literal
preservation, provenance, authority, compression, and budget behavior on
deterministic synthetic histories. A credible external evaluation of this
compiler should add it as a method in AMA-Bench rather than treating LRCBench
as a substitute.

## MemIR

[Mitigating Provenance-Role Collapse in Long-Term Agents via Typed Memory
Representation (arXiv:2605.25869)](https://arxiv.org/abs/2605.25869) identifies
source-monitoring failures in flat long-term memory and proposes MemIR, a typed
memory intermediate representation. It separates evidence, retrieval cues,
and truth-bearing claims, restricts factual authorization to supported claim
atoms, and uses provenance-scoped retrieval and utilization.

MemIR is the closest conceptual neighbor listed here: both approaches make
type and provenance structural rather than relying on prose summaries. This
implementation is narrower and code-oriented. It provides portable source
spans, exact literal kinds, conservative correction/conflict resolution, a
local append-only archive, prompt/JSON rendering, and a deterministic verifier.
The repositories have not been evaluated against one another, and similarity
of design goals is not evidence of comparative performance.

## Comparison at a glance

| Work | Primary focus | Learned component | Provenance emphasis | Evaluation style |
| --- | --- | --- | --- | --- |
| ACON | Optimize compression policy for long-horizon task success | Yes; guideline optimization and distillation | Concise informative condensations | AppWorld, OfficeBench, multi-objective QA |
| FoldAgent | Agent-controlled branching and folding | Yes; reinforcement learning | Folded sub-trajectory outcomes | Deep research and software-engineering tasks |
| AMA-Bench / AMA-Agent | Benchmark and causal/tool-augmented long-term memory | AMA-Agent uses structured retrieval | Objective and causal information | Real and scalable synthetic trajectories with QA |
| MemIR | Typed long-term memory and source monitoring | Representation and retrieval architecture | Central: grounded atoms and factual authorization | LoCoMo and BEAM-100K |
| This project | Loss-resistant active-context compilation | Optional exact-span model extractor; deterministic core | Exact source spans and independent checks | 306 tests plus local synthetic LRCBench |

## What can be claimed today

The primary literature supports the motivation for compression, active context
management, long-horizon memory evaluation, and typed provenance. It does not
support a claim that this implementation is superior to those systems. The
bundled benchmark can establish a narrow local frontier over three simple
baselines only. See [Benchmarking](BENCHMARKING.md) for the preregistered bar
required before making a “50% better than most related technology” claim.
