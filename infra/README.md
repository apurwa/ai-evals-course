# The L2 tracing backend

Self-hosted [Langfuse](https://github.com/langfuse/langfuse), for Lesson 2.

**None of this is required.** The trace model in `agent/tracing.py` is what the
course is actually about, and every browser lab reads traces that were produced
without any of these containers running. This exists so that you can see what a
real backend adds on top of a well-designed trace, and what it does not.

## Run it

```bash
# in a virtualenv, not your base environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-tracing.txt

make trace-up      # six containers, first run pulls ~2GB
make trace-demo    # five real traces, no API key, no spend
```

Then open <http://localhost:3000> and sign in:

| | |
|---|---|
| email | `evals@wayfarer.local` |
| password | `wayfarer-local-dev` |

The org, the project, and the API keys are all created on first boot by the
`LANGFUSE_INIT_*` variables in `docker-compose.yml`, so there is no signup form
and nothing to copy into a config file.

## The demo costs nothing, and the traces are still real

`make trace-demo` runs the agent selftest, which drives the **real** control
loop, the **real** permission layer, and the **real** database using a scripted
client instead of a model. So no API key is needed and nothing is spent, but the
spans, the denials, and the computed refund amounts are all genuine.

One thing the demo cannot show you is latency. The durations are real
measurements, but they are all under about three milliseconds, because a
scripted client returns instantly and there is no network call to wait on. The
*shape* of the trace is honest; the *timings* are not representative of
anything. To see a waterfall worth reading, run the corpus against a real model:

```bash
LANGFUSE_TRACING=1 python scripts/run_corpus.py --limit 10   # needs an API key
```

Five traces land. The two worth opening first:

- **`selftest-02`** attempts a $5,000 refund and is refused. The denial is a
  span at level `WARNING`, not an error and not a silence. That distinction is
  the whole point: a blocked attack and an attack that never happened must not
  look the same.
- **`selftest-06`** runs out of its step budget. It has no final answer and an
  `max_steps_exhausted` event. An agent that gave up and one that finished
  should never produce the same shape.

## Switching it on

One environment variable, and only one:

```bash
LANGFUSE_TRACING=1 python agent/support_agent.py --selftest
LANGFUSE_TRACING=1 python scripts/run_corpus.py --limit 10   # needs an API key
```

Nothing else turns it on. Tracing that activates because credentials happen to
be present in an environment is how customer data ends up somewhere nobody
decided to send it.

## What it does to your data

`agent/langfuse_sink.py` masks emails and card-shaped digit runs before
anything leaves the process. That mask is crude on purpose, and L2 lists the
trap it half-answers. Read it before pointing this at anything real.

## Ports

Change these in `docker-compose.yml`, or export them before `make trace-up`.

| Service | Host port | Why |
|---|---|---|
| langfuse-web | 3000 | `LANGFUSE_PORT` |
| minio | 9090 | `MINIO_PORT` |
| clickhouse | 8123 | `CLICKHOUSE_PORT` |
| postgres | **5433** | `POSTGRES_PORT`, off-default because 5432 is usually taken |
| redis | 6379 | `REDIS_PORT` |

## Cleaning up

```bash
make trace-down    # stop, keep the traces
make trace-nuke    # stop and delete the volumes
make trace-logs    # when it will not come up
```

## The secrets in here are fake

`docker-compose.yml` and `langfuse.env` contain committed credentials. That is
deliberate: it is what makes `make trace-up` work on a clean clone. Everything
binds to localhost. Do not put this on a network and do not reuse the values.
