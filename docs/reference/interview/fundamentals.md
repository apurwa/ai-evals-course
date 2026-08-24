# Evals fundamentals

Starter questions on the ideas everything else builds on. Grows as we brainstorm.

## Q: What is the difference between model evals and product evals?

Model evals measure a model in isolation on general benchmarks — reasoning,
knowledge, coding. Product evals measure *your* application doing *its* job on
*your* data: did the support agent apply the refund policy correctly, respect
permissions, and stay on task. A model can top every benchmark and still fail
your product, because your product has requirements no benchmark encodes. Evals
that matter are almost always product evals.

## Q: Why do error analysis before choosing metrics?

Because choosing metrics first measures what is easy, not what is broken. Error
analysis means reading a diverse sample of real traces, writing free-text notes
on what went wrong, and clustering those notes into failure modes. The metrics
then come from the failure modes you actually found. This is the opposite of
starting with a dashboard of generic scores and hoping they catch your real
problems.

## Q: An LLM-as-a-judge gives you a number. Why not trust it directly?

Because a judge is itself a fallible model, and an unvalidated judge is just a
second opinion of unknown quality. You validate it against human labels:
measure its agreement (e.g. its true positive and true negative rates) on a set
you annotated by hand, and only then use it at scale — ideally correcting the
raw judge score for its known error rate. A judge you have not measured against
ground truth can be confidently, consistently wrong.

## Q: Why enforce authorization in code rather than in the prompt?

Because a prompt is not a boundary. A prompt injection can talk a model into
issuing a $5,000 refund; a permission check that runs in code the model cannot
reach still refuses it. Model-side guardrails are defense in depth, never the
security boundary. Prompt injection has no reliable detector, so the real
control has to live where the model cannot argue with it.
