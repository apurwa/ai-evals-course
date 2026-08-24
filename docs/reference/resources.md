# Resources

The books, courses, and tools this repository draws on. For each: what it is,
what it is best for, and which lessons it maps to. These are pointers and short
summaries, not reproductions — go to the source for the real thing.

## AI Evals for Engineers & PMs — Hamel Husain & Shreya Shankar

The method this whole course teaches. A [Maven course](https://github.com/hamelsmu)
and a forthcoming O'Reilly book, *Evals for AI Engineers*. If you want the
primary source rather than an independent implementation of it, start here.

Their companion skills are installable and used directly in Track B:

```
/plugin marketplace add hamelsmu/evals-skills
/plugin install evals-skills@hamelsmu-evals-skills
```

**Maps to:** the entire course. Skills line up with L3 (`generate-synthetic-data`),
L4 (`error-analysis`, `build-review-interface`), L5 (`write-judge-prompt`,
`validate-evaluator`), and L6/L8 (`eval-audit`).

## Comprehensive Study Guide — enriched notes on the Husain & Shankar method

A long, code-heavy walkthrough that follows the same method and adds
platform-specific examples for Arize Phoenix and Langfuse:
[ai_evals_comprehensive_study_guide.md](https://github.com/ombharatiya/ai-system-design-guide/blob/main/ai_evals_comprehensive_study_guide.md).

Fourteen chapters, from setting up observability through error analysis,
code-based and LLM-as-judge evaluators, RAG and multi-step and multi-turn
evaluation, production safety and monitoring, statistical correction of judge
error, and closing the loop back into system improvements. Reach for it when you
want a worked, end-to-end technical treatment of a step.

**Maps to:** L2 (observability), L4 (error analysis), L5 (evaluators), L7 (CI),
L8 (safety), L9 (cost). Its statistical-correction chapter pairs with L5's true
positive rate work.

## AI Evals for Everyone — Aishwarya Naresh Reganti & Kiriti Badam

A beginner-friendly 101 that clears up the vocabulary before you go deep:
[free_courses/ai_evals_for_everyone](https://github.com/aishwaryanr/awesome-generative-ai-guide/blob/main/free_courses/ai_evals_for_everyone/README.md).
Ten short chapters plus a video series. Best for the distinction that trips
teams up early — model evaluations versus product evaluations — and for building
a first reference dataset.

**Maps to:** L1 (foundations), L2 (evaluability), L5 (metrics and judges).

## Langfuse — open-source tracing backend

The observability platform this course wires up in L2:
[github.com/langfuse/langfuse](https://github.com/langfuse/langfuse). Self-hosted
with `make trace-up`, no API key, costs nothing. Use it to see real spans,
denials, and refund amounts from the agent's own control loop.

**Maps to:** L2 (tracing and observability).

## At a glance

| Resource | Best for | Lessons |
| --- | --- | --- |
| Husain & Shankar (course + book) | The method, first-hand | All |
| Comprehensive study guide | Worked technical depth per step | L2, L4, L5, L7, L8, L9 |
| AI Evals for Everyone | Vocabulary and first datasets | L1, L2, L5 |
| Langfuse | Seeing real traces | L2 |
