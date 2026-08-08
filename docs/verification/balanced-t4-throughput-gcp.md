# Balanced T4 throughput verification on GCP

## Scope and chronology

- Authoritative session/control date: `2026-08-04`.
- Observed host-clock date during collection: `2026-08-08`.
- Reviewed balanced commit tested live: `0934d29ea5b8e5e197fa0cc543aea8b5775f8c1f`.
- Independently reviewed rollback commit deployed live:
  `2c4f52afd36ee059b003478234742940a5c0dc99`.
- Final outcome: balanced throughput rejected; conservative rollback activated and
  verified.

Publication-history note: the balanced and rollback identifiers above record the exact
pre-publication trees exercised on the GPU host. Before publication, only unpublished
Markdown history was rewritten to redact infrastructure addresses and SSH identity
metadata. Executable and configuration content was unchanged, so those identifiers
were intentionally not substituted and may not resolve in the published history.

The host clock was four calendar days ahead of the authoritative session/control date.
Host-clock timestamps and commit metadata are observations only; neither establishes
event sequencing or evidence freshness.

This evidence excludes complete public addresses, remote target and environment
contents or fingerprints, SSH key material, model identities, prompts, generated text,
embeddings, vectors, sampling commands, credentials, and secrets. Request and response
bodies were suppressed throughout live acceptance.

## Reviewed balanced deployment

The exact reviewed balanced commit was deployed from a tracked-clean detached checkout.
Only the `inference` profile was selected. The atomic deployment completed in `218.121`
seconds and passed the complete cold-generation `600 s`, warm-generation `120 s`, and
embedding `120 s` health gate before activation.

Independent post-deploy inspection proved:

- exact parallelism `2/4`, maximum loaded models `1/1`, context `8192/8192`, and
  keep-alive `30m/30m`;
- `2/2` containers running and healthy with `unless-stopped` restart policy;
- NVIDIA all-device requests and exact remote loopback binds on both services;
- exactly one resident approved model per endpoint, both fully GPU-backed;
- two positive host compute processes using `5,464 MiB` before measurement;
- preserved model volumes of `9,164 MiB` and `594 MiB`, with `16/16` files;
- zero container restarts, OOM kills, state/nonzero-exit errors, kernel OOM events, and
  NVIDIA Xids;
- data host still at exact `16/16` healthy non-inference services, with zero running
  CPU Ollama containers and zero data-host inference listener rows.

## Fixed benchmark protocol

The unchanged Task 14 harness used the same repository-managed inference tunnel, local
ports, client path, five warmed serial rounds, and five concurrent rounds for both the
accepted conservative baseline and reviewed balanced run. The schema and complete
protocol configuration matched exactly. Each run attempted `42` requests.

The acceptance thresholds derived without rounded comparisons from the accepted
baseline were:

- LLM aggregate throughput at least `20.73312120 tokens/s` and ratio at least `1.20`;
- embedding aggregate throughput at least `3.76462350 requests/s` and ratio at least
  `1.50`;
- LLM concurrent p95 at most `6.4791475 s`;
- embedding concurrent p95 at most `2.6539325 s`;
- `42/42` HTTP 200, zero request and sampling errors, and peak aggregate compute VRAM
  below `13,824 MiB`;
- both models fully GPU-backed and no restart, OOM, Xid, or nonzero-exit evidence.

## Results and decision

| Measurement | Conservative baseline | Balanced tuned | Exact tuned/baseline comparison | Verdict |
| --- | ---: | ---: | ---: | --- |
| LLM median aggregate evaluated throughput | `17.277601 tokens/s` | `19.303537 tokens/s` | `1.117257945706698516767460946` | Fail: below both absolute and `1.20` ratio thresholds |
| Embedding median aggregate throughput | `2.509749 requests/s` | `2.546512 requests/s` | `1.014648078353652098277556839` | Fail: below both absolute and `1.50` ratio thresholds |
| LLM concurrent p95 | baseline warmed serial p95 `2.591659 s` | `3.357382 s` | `1.295456693955493373163676240` | Pass |
| Embedding concurrent p95 | baseline warmed serial p95 `1.061573 s` | `1.808392 s` | `1.703502255615016583880712867` | Pass |
| Peak aggregate compute VRAM | `5,294 MiB` | `5,482 MiB` | below `13,824 MiB` | Pass |

Additional latency observations were:

| Endpoint/path | Conservative median / p95 | Balanced median / p95 |
| --- | ---: | ---: |
| LLM warmed serial | `2.548383 / 2.591659 s` | `2.446679 / 2.451019 s` |
| LLM concurrent | `3.342618 / 3.864692 s` | `3.198866 / 3.357382 s` |
| Embedding warmed serial | `1.021264 / 1.061573 s` | `0.775860 / 1.024596 s` |
| Embedding concurrent | `1.383332 / 1.626466 s` | `1.375344 / 1.808392 s` |

Both runs completed `42/42` HTTP 200 requests with zero HTTP, timeout, network,
validation, or other errors. The balanced run evaluated `480` LLM tokens, collected
`19` GPU samples with zero sampling errors, and retained `2/2` fully GPU-backed models,
two host compute processes, and zero restart/OOM/Xid/nonzero-exit evidence after the
benchmark.

The latency, request-integrity, GPU sampling, VRAM, and safety gates passed, but neither
throughput endpoint met its absolute or relative requirement. The reviewed balanced
release was therefore rejected. The optional more-than-five-minute balanced idle proof
was not run because the primary decision had already failed.

## Reviewed automatic rollback

The ignored deployment runtime was restored to exactly one `5m` assignment with
byte-equivalence proof. Independent review found no Critical or Important issue in the
normal rollback commit. The tracked-clean rollback checkout then deployed only
`inference`; atomic health completed in `196.509` seconds and activated release
`20260808T052258Z-2c4f52afd36e-87f3672972924a5da31313ffe8964177` only after the full
cold/warm/embedding gate passed.

Fresh final acceptance proved:

- exact conservative parallelism `1/1`, maximum loaded models `1/1`, context
  `8192/8192`, and keep-alive `5m/5m`;
- `2/2` healthy containers, NVIDIA requests, exact loopback binds, `2/2` fully
  GPU-backed resident models, and two positive compute processes using `5,266 MiB`;
- preserved `9,164 MiB` and `594 MiB` model volumes with `16/16` files;
- zero restart, OOM-kill, state/nonzero-exit, kernel OOM, and NVIDIA Xid evidence;
- exact `16/16` healthy data services, zero running CPU Ollama containers, and zero
  data-host inference listener rows.

A temporary repository inference tunnel had one SSH owner, both canonical ports,
loopback-only binds, zero wildcard rows, and TCP `2/2`. Output-silent bounded generation
and embedding calls completed in `1.828 s` and `1.067 s`, respectively, with request
keep-alive `5m`. The post-call state remained `2/2` healthy and fully GPU-backed at
`5,266 MiB`, with zero restart, OOM, or exit errors. The temporary launcher and captured
SSH child were stopped; final listener rows and captured owner processes were both zero.

During the final verification pass, the data Spot VM was found terminated after its
earlier accepted `16/16` state. The same existing VM restarted in `15.709 s`; no machine,
disk, profile, service, or repository deployment changed. Its new ephemeral address was
rediscovered only in memory. TCP 22 was reachable, and the initial strict SSH check
correctly rejected the previously unseen address. After GCP control-plane identity was
reconfirmed, one `accept-new` connection succeeded and an immediate strict-host-key
connection also succeeded. No ignored target file was edited.

The recovered guest then passed exact fresh checks: `16/16` expected containers,
`16/16` running and healthy, zero starting or unhealthy services, zero running CPU
Ollama containers, zero data-host inference listener rows, and full repository data
profile health. No data-service redeployment was performed.

An attempted redundant refresh of the already accepted final GPU audit then failed in
the local PowerShell parser before any SSH or live command was invoked. It caused no
remote request or mutation. The last completed GPU proof therefore remains the accepted
rollback state recorded above: exact conservative settings, `2/2` healthy and fully
GPU-backed, `5,266 MiB` compute use, and zero restart/OOM/Xid/nonzero-exit evidence.

The conservative rollback release above is the final live release verified by this
record. No persistent tunnel was created and no GitHub branch was pushed during this
task.
