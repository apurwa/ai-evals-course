# Error analysis comes before metrics

The first move in evaluating an LLM product is not to pick a metric. It is to
read a sample of real traces and write down, in your own words, what went wrong.
The metrics come after, and they come *from* what you read.

## Why this order

If you choose metrics first, you measure what is easy to measure and miss what
actually breaks. Reading traces first inverts that: the failure modes you
discover decide what is worth measuring. This is the core discipline in the
Husain & Shankar method, and it is the reason this repository makes you annotate
by hand in L4 before it lets you write a judge in L5.

## The loop, in short

- **Sample** a diverse set of traces — not just the failures you already know
  about, and not just the happy path.
- **Open-code** each one: write a short, free-text note on what happened.
  Resist categories at this stage; you do not know them yet.
- **Cluster** those notes into failure modes. The categories are discovered,
  not assumed.
- **Count.** Now you know which failure modes are common enough to be worth an
  evaluator, and which are rare enough to ignore for now.

## What this buys you

A taxonomy you can defend, because every category traces back to a real example
you read. When you later build a judge for one of these modes, you can measure
its true positive rate against the human labels you produced here — which is
exactly what makes the judge trustworthy rather than just plausible.

> The failure distribution in this repo's lab corpus was *planted* for teaching.
> On your own product, this loop is how you find the real one.

## See also

- Lesson 4, Error Analysis, and its lab (annotate).
- The `error-analysis` and `build-review-interface` skills from Husain &
  Shankar's plugin.
