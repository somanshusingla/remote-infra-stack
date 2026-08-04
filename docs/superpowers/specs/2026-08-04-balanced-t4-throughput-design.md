# Balanced T4 inference throughput design

Date: 2026-08-04

Status: approved by the operator (`balanced`)

## Context

The split-host rollout is accepted at commit `aca6f53`. The data VM runs exactly 16
healthy non-inference containers, the T4 VM runs the two healthy Ollama containers, and
the localhost endpoints remain `11440` for chat and `11441` for embeddings. Both models
were observed resident at approximately 3.37 GB and 0.68 GB of VRAM, while the host
reported approximately 5.3 GiB of total compute-process memory on a 15,360 MiB T4.

The conservative baseline gives each dedicated Ollama process one loaded model, one
parallel request, an 8192-token context, and a five-minute keep-alive. Live recovery also
showed that an idle model can add roughly 60 seconds of cold latency, and the original
fresh load took more than 120 seconds.

Ollama documents that `OLLAMA_NUM_PARALLEL` controls parallel requests per model and that
memory grows with parallelism multiplied by context length. This design therefore raises
parallelism independently for the two dedicated processes and measures the result rather
than assuming the observed free VRAM guarantees useful throughput.

Official reference: https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests

## Decision

Use the balanced profile:

| Setting | LLM container | Embedding container |
| --- | ---: | ---: |
| `OLLAMA_NUM_PARALLEL` | `2` | `4` |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | `1` |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | `8192` |
| `OLLAMA_KEEP_ALIVE` | `30m` | `30m` |

The parallel values are committed service settings, not unvalidated environment
overrides. The keep-alive remains the existing validated environment setting, but its
documented/default and active ignored `.env` value change from `5m` to `30m`. Model
identity, quantization, API shape, ports, volumes, memory caps, GPU reservations, and
health timeouts do not change.

`OLLAMA_MAX_LOADED_MODELS=1` remains appropriate because each container serves exactly
one approved model. The longer keep-alive deliberately retains both models between normal
development bursts; their accepted combined residency leaves material T4 headroom.

## Benchmark protocol

Benchmark the currently accepted `1/1`, five-minute release before changing code, then
the reviewed balanced release after atomic deployment. Use the same VM, models, tunnel,
context, prompts, response limits, and client harness for both runs.

The harness is temporary and ignored. It reads tracked model pins without printing them,
uses fixed non-sensitive inputs, discards generated text and vectors, and records only
counts, timings, status codes, and aggregate statistics.

After one output-silent warm-up per endpoint, collect at least five measured rounds:

- LLM: two simultaneous non-streaming requests, each capped at 32 generated tokens.
  Record total evaluated tokens, wall time, aggregate evaluated tokens/second, and
  per-request latency.
- Embeddings: four simultaneous one-input requests. Record wall time, requests/second,
  and per-request latency.
- Record medians plus p95 latency, HTTP/error counts, container health, peak host compute
  VRAM, GPU process count, and kernel/Docker OOM or NVIDIA Xid evidence.

Run no unrelated workload during either benchmark. A cold load is not a measured round;
the 30-minute request keep-alive is included in benchmark requests so all measured rounds
exercise resident models.

## Acceptance and rollback

The balanced release is accepted only when all of these hold:

1. Zero request failures, timeouts, 5xx/503 responses, container restarts, OOMs, killed
   processes, or NVIDIA Xids.
2. Both approved models remain fully GPU-backed, both containers retain NVIDIA device
   requests and loopback-only binds, and exactly the expected two inference containers
   remain healthy.
3. Peak host compute VRAM stays below 13,824 MiB (90 percent of the T4's reported total),
   with no observed CPU offload.
4. At concurrency two, median LLM aggregate evaluated tokens/second is at least 1.20 times
   the baseline value.
5. At concurrency four, median embedding requests/second is at least 1.50 times the
   baseline value.
6. Tuned p95 per-request latency for each endpoint is no more than 2.5 times its warmed
   serial baseline, and every individual request remains within 120 seconds.
7. The rendered/container environment reports the exact 30-minute keep-alive, and both
   models remain resident after an idle period longer than the prior five-minute setting
   before output-silent endpoint calls succeed. This observation is not included in the
   measured rounds.

If deployment health fails, the existing atomic release transaction retains/restores the
prior accepted release. If post-deployment benchmark or safety acceptance fails, create a
normal revert commit restoring `1/1` and `5m`, deploy that reviewed commit atomically, and
re-prove the baseline endpoints. Never delete model volumes, alter models, raise context,
or compensate by changing the VM, GPU, memory caps, or cloud settings.

## Evidence and documentation

Add a sanitized throughput evidence record under `docs/verification`. It includes tested
commits, settings, benchmark methodology, aggregate ratios, latency/VRAM/error summaries,
deployment outcome, and any rollback. It must not include public addresses, SSH material,
environment contents, model names, generated text, embeddings, or secrets.

Update operator documentation and Compose contract tests so the balanced values cannot
silently regress. Preserve the user's unrelated unstaged Ollama signal-handling edits.

## Non-goals

- More than two LLM or four embedding requests in parallel
- Multiple models per container
- A larger context window
- Model, quantization, API, port, volume, or host changes
- Autoscaling, request routing, batching proxies, or queue-policy tuning
- Treating lower single-request latency as the primary objective

## Success criteria

The change is complete when the balanced settings are committed and independently
reviewed, local contracts pass, atomic GPU deployment passes, the measured acceptance
thresholds pass (or the exact baseline is safely restored), sanitized evidence is
committed, the split tunnels remain usable, and the final reviewed repository state is
pushed to GitHub `master` without staging the two user-owned edits.
