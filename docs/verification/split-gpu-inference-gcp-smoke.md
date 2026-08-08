# Split GPU inference host - GCP smoke verification

## Scope and safety boundary

- Evidence completed: `2026-08-04T16:07:59+05:30`.
- Reviewed deployment commit: `783a5cd73d436de7c5b31d4a9b906192471d5fd5`.
- GCP project: `remote-infra-stack`.
- Data target: `high-mem-64-gb-us-east-1`, zone `us-east1-c`, Spot provisioning,
  termination action `Stop`; address changed from `136.108.14.xxx` to
  `35.237.34.xxx` during the final verification window.
- GPU target: `nvidia-t4-26-gb-us-central-1`, zone `us-central1-f`,
  `n1-highmem-4`, Spot provisioning, termination action `Stop`.

Publication-history note: the deployment and release identifiers below record the
exact pre-publication trees exercised on the hosts. Before publication, only
unpublished Markdown history was rewritten to redact infrastructure addresses and SSH
identity metadata. Executable and configuration content was unchanged, so those
identifiers were intentionally not substituted and may not resolve in the published
history.

All operator commands used a clean detached worktree at the reviewed commit. The
primary feature worktree retained the user's unstaged edits in
`config/ollama/bootstrap.sh` and `tests/test_ollama_bootstrap.py`; neither file was
staged, overwritten, or committed by this smoke run. No throughput setting was tuned.

This record deliberately omits complete public addresses, environment-file contents,
credentials, SSH public-key material, model names, generated text, embedding vectors,
and response bodies.

## Access observation and deviation

- The data target used the dedicated Ed25519 identity, fingerprint
  `<ssh-key-fingerprint>`, with guest principal
  `<ssh-principal>`.
- The GPU target rejected that identity. The requested instance-only metadata merge
  was not approved by the execution control, so no instance/project SSH metadata or
  OS Login setting was changed.
- GPU access therefore used the already project-authorized RSA identity, fingerprint
  `<ssh-key-fingerprint>`, with existing guest principal
  `<ssh-principal>`.

The GPU access path is an explicit remaining deviation from the requirement that both
targets use the dedicated identity. It avoided destructive metadata replacement and
was freshly proven with batch-mode SSH before deployment and after the Spot restart.

## GPU bootstrap and host gates

- Docker Engine `29.7.1`; Docker Compose `5.4.0`.
- Exactly one `Tesla T4`, driver `580.173.02`, 15,360 MiB VRAM.
- Digest-pinned CUDA validation image:
  `docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df`.
- The pinned CUDA container also observed exactly one `Tesla T4`.
- NVIDIA container runtime present and usable.
- Exactly five NVIDIA-related packages remained held. The toolkit quartet reported
  exact dpkg state `hold ok installed` and a coherent version `1.17.8-1`; no mixed
  toolkit upgrade was forced.
- Host memory gate: 27,308,662,784 bytes observed versus 19,327,352,832 bytes required
  including host overhead.

The live compatibility repairs accepted the driver's exact T4 label, preserved the
coherent vendor-held toolkit set, recognized documented held-package dpkg state, and
allowed a fixed 600-second first cold request. An earlier 120-second cold request
failed closed with no release activation; CPU inference stayed available on the data
host. The corrected bound changed no throughput or model configuration.

## Atomic deployment and cache reuse

The first accepted inference-only deployment completed in 228.4 seconds and activated
release
`20260804T083740Z-783a5cd73d43-82275f3d2e384a87adab0cb0da3a25b5` only after both
services passed all health gates. Fresh post-activation checks found:

- exactly two selected containers, both healthy;
- one warm bounded generation in 1.081 seconds and one embedding request in
  0.283 seconds, with bodies suppressed;
- per-model resident VRAM of 3,367,921,253 and 681,417,113 bytes;
- two positive host compute processes totaling 5,266 MiB VRAM;
- NVIDIA device requests on both containers;
- exact remote binds `127.0.0.1:11440` and `127.0.0.1:11441`, with no public
  inference listener.

The same commit was deployed a second time without deleting model storage. It completed
in 237.1 seconds and atomically activated
`20260804T084358Z-783a5cd73d43-fa32bcd9f21d4b1e9e78f60ed0421b4d`. During acceptance,
the prior release remained `current`. The following named-volume observations were
unchanged before and after the redeploy:

| Volume | Observed bytes |
| --- | ---: |
| `remote-infra-stack-ollama-llm-data` | 9,608,353,210 |
| `remote-infra-stack-ollama-embedding-data` | 621,878,441 |

Both approved model inventories remained present, and no fresh model download was
required. Model identities were compared but are intentionally not recorded here.

## Data-host cutover

Before cutover, a temporary data-only tunnel established all 13 configured local
ports. Dual-stack loopback produced 26 listener rows owned by one captured SSH process;
no wildcard address was accepted. Acceptance included 13/13 TCP connections, native
PostgreSQL and Redis checks, a signed DynamoDB check, and 11/11 labeled HTTP checks,
including authenticated OpenSearch and OpenSearch Dashboards. Secrets and response
bodies were suppressed.

Only after that pass, the data-host inference profile was stopped. Fresh cutover state
was:

- exact expected 16 non-inference containers, 16/16 running and healthy;
- zero running Ollama containers and both former Ollama containers stopped;
- zero public bind rows and zero data-host inference listeners;
- both old model volumes preserved at the same names and byte counts shown above.

Those old CPU-host volumes are preservation evidence, not an advertised fallback.
There is no automatic CPU failover after cutover.

## Actual Spot stop/start recovery

The GPU instance was stopped and observed in `TERMINATED`, then the same Spot instance
started successfully on the first attempt and returned to `RUNNING`. No machine type,
disk, accelerator, zone, provisioning model, or cloud SSH metadata was changed. The
ephemeral address changed from `34.55.183.xxx` to `34.133.214.xxx`; only the ignored
`REMOTE_HOST` assignment in `remote.gpu.env` changed, with zero non-target environment
changes. No deployment was run after restart.

Post-restart verification proved:

- the active release was still
  `20260804T084358Z-783a5cd73d43-fa32bcd9f21d4b1e9e78f60ed0421b4d`;
- Docker automatically restored exactly two inference containers, both healthy, with
  restart policy `unless-stopped`;
- both model inventories and both exact named-volume byte counts were unchanged;
- both NVIDIA device requests and both exact loopback binds remained present, with
  zero public inference binds;
- cold generation completed in 60.554 seconds, warm generation in 0.839 seconds, and
  embedding in 6.770 seconds, all within the fixed bounds and with bodies suppressed;
- resident VRAM was again 3,367,921,253 and 681,417,113 bytes; two host compute
  processes totaled 5,286 MiB.

A renewed temporary inference tunnel then produced four IPv4/IPv6 loopback listener
rows owned by one captured SSH process, passed both TCP checks, served chat in
1.514 seconds and embedding in 1.080 seconds, and cleaned up to zero listeners.

## Unplanned data-host Spot recovery

The final test cycle paused only the local tunnel processes so the repository's tunnel
tests could bind the canonical ports; remote services were not stopped. When the data
tunnel was reopened, the data VM was discovered in `TERMINATED`. Live scheduling data
confirmed Spot provisioning, termination action `Stop`, and automatic restart disabled.

The same existing data VM was started without changing its machine, disk, zone, or
provisioning settings. Its ephemeral address changed as redacted above, and only the
ignored `REMOTE_HOST` assignment in `remote.data.env` was updated. Batch SSH recovered
on the second bounded attempt. Immediately after boot, 15 services were healthy; the
normal startup sequence then reached the exact accepted state:

- exact expected service names, 16 running and 16 healthy;
- zero running CPU Ollama containers and both prior Ollama containers still stopped;
- preserved model volumes of 9,608,353,210 and 621,878,441 bytes;
- zero public bind rows and zero data-host inference listeners.

## Final split tunnels and current state

The final data and GPU tunnels are held by separate hidden, noninteractive PowerShell
launchers in foreground handoff cells `272` and `258`. At the final acceptance
checkpoint:

| Target | Launcher PID | SSH PID | Listener rows |
| --- | ---: | ---: | ---: |
| Data | 25,492 | 19,580 | 26 |
| GPU | 16,440 | 21,428 | 4 |

The PIDs are transient operational evidence. The two SSH owners were distinct; all 30
listener rows were IPv4/IPv6 loopback, wildcard rows were zero, and every one of the 15
configured ports accepted TCP. The four dedicated live diagnostic logs were empty.
Through these exact persistent processes, 10/10 stable data HTTP endpoints passed,
including authenticated OpenSearch and Dashboards, followed by bounded GPU chat in
60.328 seconds and embedding in 4.265 seconds. Bodies remained suppressed. The earlier
native/signed 3/3 and full data HTTP 11/11 results remain the datastore-level evidence.

Current intended service state is therefore 16/16 healthy non-inference services on
the data target, with its two Ollama containers stopped, and 2/2 healthy inference
services on the GPU target. Both tunnel cells must remain running for the local
endpoints to stay available.

## Final repository verification

- Documentation contracts: 21/21 passed after the evidence review corrections.
- Exact unrestricted aggregate: 270 tests in 286.322 seconds, 98 capability skips,
  zero failures.
- The restricted diagnostic aggregate was rejected: its ACL failures came from the
  filesystem sandbox, and its tunnel failures came from the intentionally occupied
  operator ports. The acceptance aggregate ran with those local tunnels briefly paused,
  while remote services remained untouched; both split tunnels were then restored and
  reaccepted as recorded above.
- The evidence commit contains only this file. The user's two pre-existing Ollama edits
  remain unstaged.

## Backup observation and limitations

The GPU boot disk had one attached snapshot resource policy,
`default-schedule-1`. Live inspection reported a daily schedule with 14-day retention.
Snapshot existence, recency, and restorability were not verified. This is a disk-level
snapshot policy, not an application-level backup guarantee; replacement infrastructure
and service-specific restores remain manual operations outside this repository.

Additional operational limitations:

- Spot capacity was available on the first restart, so capacity-exhaustion backoff was
  not exercised.
- The final tunnels are session-scoped operator processes, not a reconnecting service.
  If either cell exits or the SSH connection drops, reopen the corresponding tunnel.
- The GPU still uses the approved project RSA/principal deviation described above.
- Preserved CPU model volumes are not a supported failover path.

## Rejected harness evidence

The following observations were diagnosed as acceptance-harness defects and are not
counted as product failures:

- an initial listener assertion rejected valid duplicate IPv4/IPv6 loopback rows;
- an anonymous Dashboards request correctly received 401 before the probe was fixed to
  use the same authentication as production health;
- a scriptblock TLS callback produced a local OpenSearch status-0 false negative before
  replacement with the process-local .NET certificate validator;
- a final wrapper incorrectly required unavailable local `psql.exe`, and another added
  an empty body to GET requests; neither indicated a remote service failure;
- a redundant hand-built SigV4 probe returned 400 and was discarded rather than used
  to override the already successful native/signed acceptance and remote health;
- detached launcher processes later exited together with address-free diagnostics, so
  they were not handed off. The final tunnels were relaunched in persistent foreground
  cells and independently reverified as recorded above.

Temporary tunnel processes and harness files from rejected attempts were removed after
their listeners closed. The accepted tunnels were cycled only for the required aggregate
port isolation; final handoff cells `272` and `258` were not stopped.
