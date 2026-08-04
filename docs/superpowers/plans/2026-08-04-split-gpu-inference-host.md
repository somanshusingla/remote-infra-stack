# Split GPU Inference Host Implementation Plan

> **Execution requirement:** Use the `subagent-driven-development` skill to execute this plan task by task in the existing feature worktree. Use a fresh implementer for every task, require specification and quality review before advancing, and finish with the `verification-before-completion` and `finishing-a-development-branch` skills.

**Goal:** Move the two Ollama services to the dedicated NVIDIA T4 VM, require and prove real GPU execution, preserve the data VM's non-inference stack, and verify recovery after a Spot stop/start without changing the local inference endpoints.

**Architecture:** Keep one repository, one Compose model, and the existing atomic release format. Deploy non-inference profiles to the data VM through `remote.data.env` and only `inference` to the T4 VM through `remote.gpu.env`, selected by `STACK_REMOTE_ENV`. Both Ollama containers reserve the same T4, retain separate processes/volumes/ports, and make bounded chat/embed plus positive per-model VRAM checks part of release health so a CPU-fallback release cannot become current.

**Tech stack:** Bash 4+, PowerShell 7/Windows PowerShell, Docker Engine and Compose, NVIDIA Container Toolkit, NVIDIA T4, Ollama HTTP API, Python `unittest`, fake-command integration tests, GCP Compute Engine, OpenSSH.

**Approved design:** `docs/superpowers/specs/2026-08-04-split-gpu-inference-host-design.md`

**Pinned CUDA validation input:** `docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df` (`linux/amd64` image manifest, verified 2026-08-04 from the official NVIDIA Docker Hub repository with `docker buildx imagetools inspect`).

**Existing-worktree constraint:** Preserve the user's uncommitted changes in `config/ollama/bootstrap.sh` and `tests/test_ollama_bootstrap.py`. No task may restore, rewrite, stage, or commit those files unless a later user instruction explicitly expands scope.

**Execution corrections from plan preflight:** Do not add new tests that merely grep human documentation for exact prose; behavior-test the ignore and operator interfaces, manually review the published commands/endpoints, and update legacy documentation expectations only when required to keep the pre-existing suite coherent. Because the primary worktree intentionally contains the two user-owned edits, Task 8 must deploy `HEAD` from a temporary detached clean worktree with copied ignored configuration rather than stashing, staging, committing, or otherwise disturbing those edits.

---

## Task 1: Pin and validate the GPU bootstrap input

**Files:**

- Modify: `versions.env`
- Modify: `tests/fixtures/verified-manifests.json`
- Modify: `tests/test_repository_contract.py`

**Contract:**

- Add exactly one `NVIDIA_CUDA_IMAGE` entry.
- Keep a human-readable, non-`latest` CUDA tag and an immutable digest.
- Record the reference as a `linux/amd64` single-platform manifest rather than falsely labelling it a manifest list.
- Keep all existing image pins byte-for-byte unchanged.

### Step 1: Write the failing repository-contract tests

Update `test_versions_match_verified_manifest_inventory` to expect 18 image variables and 18 inventory entries. Replace the blanket `kind == "manifest-list"` assertion with this exact invariant:

```python
self.assertIn(record["kind"], {"manifest", "manifest-list"}, name)
self.assertEqual(["linux/amd64"], record["verified_platforms"], name)
if name == "NVIDIA_CUDA_IMAGE":
    self.assertEqual("manifest", record["kind"])
    self.assertRegex(record["reference"], r"nvidia/cuda:12\.9\.1-base-ubuntu24\.04@sha256:")
    self.assertNotIn(":latest@", record["reference"])
```

Also assert `NVIDIA_CUDA_IMAGE` is not referenced by any Compose service, because it is a bootstrap/preflight validation input rather than a long-running stack service.

### Step 2: Run the focused test and confirm RED

Run:

```powershell
python -m unittest tests.test_repository_contract -v
```

Expected: failure because `NVIDIA_CUDA_IMAGE` is absent and the inventory still contains 17 images.

### Step 3: Add the verified pin and inventory record

Append to `versions.env`:

```dotenv
NVIDIA_CUDA_IMAGE=docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df
```

Add this inventory record without changing existing records:

```json
"NVIDIA_CUDA_IMAGE": {
  "reference": "docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df",
  "kind": "manifest",
  "verified_platforms": ["linux/amd64"]
}
```

Update `verified_at` to `2026-08-04T00:00:00Z` and extend `verification_method` to note that both the manifest list and the linux/amd64 child manifest were inspected.

### Step 4: Run the focused tests and confirm GREEN

Run:

```powershell
python -m unittest tests.test_repository_contract tests.test_compose_invariants -v
```

Expected: all tests pass; Compose still contains the same 18 services.

### Step 5: Commit

```powershell
git add versions.env tests/fixtures/verified-manifests.json tests/test_repository_contract.py
git commit -m "build: pin NVIDIA CUDA validation image"
```

---

## Task 2: Make inference GPU-required in Compose

**Files:**

- Modify: `compose.yaml`
- Modify: `tests/test_compose_inference.py`
- Modify: `tests/test_compose_invariants.py`

**Contract:**

- Both `ollama-llm` and `ollama-embedding` request the NVIDIA GPU.
- Model, port, volume, memory, health, network, and concurrency contracts stay unchanged.
- Rendering only a non-inference profile does not select either Ollama service and does not perform host GPU discovery.

### Step 1: Write the failing GPU reservation assertions

Extend `assert_inference_service_contract` to require the normalized Compose device reservation:

```python
self.assertEqual(
    [{"capabilities": ["gpu"], "count": -1, "driver": "nvidia"}],
    service["deploy"]["resources"]["reservations"]["devices"],
)
```

Add a mutation that removes the reservation and prove the contract rejects it. Add an invariant rendering `core vector dynamodb search observability tools` that asserts neither Ollama service is selected and that configuration succeeds on the local CPU-only development machine.

### Step 2: Run the focused tests and confirm RED

Run:

```powershell
python -m unittest tests.test_compose_inference tests.test_compose_invariants -v
```

Expected: both inference services fail the new GPU reservation assertion.

### Step 3: Add identical NVIDIA reservations to both services

Under each Ollama service add:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

Do not add a global runtime, a CPU fallback profile, service dependencies, cross-host networking, or public binds.

### Step 4: Render and test every affected profile

Run:

```powershell
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile inference config --quiet
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector --profile dynamodb --profile search --profile observability --profile tools config --quiet
python -m unittest tests.test_compose_inference tests.test_compose_invariants -v
```

Expected: both renders and all tests pass.

### Step 5: Commit

```powershell
git add compose.yaml tests/test_compose_inference.py tests/test_compose_invariants.py
git commit -m "feat: require GPU for Ollama inference"
```

---

## Task 3: Add idempotent NVIDIA host bootstrap

**Files:**

- Modify: `scripts/remote/bootstrap-host.sh`
- Modify: `tests/test_bootstrap.py`
- Modify/Create as required: command fakes under `tests/fakes/`

**Contract:**

- New grammar: `bootstrap-host.sh --check|--install [--gpu --cuda-image IMAGE]`.
- `--gpu` requires exactly one `NVIDIA T4`, a nonempty digest-pinned CUDA image argument, and the Deep Learning VM's working host driver.
- CPU behavior and output remain unchanged when `--gpu` is absent.
- GPU installation follows NVIDIA's official apt repository flow, installs `nvidia-container-toolkit`, configures Docker with `nvidia-ctk runtime configure --runtime=docker`, restarts Docker only when `/etc/docker/daemon.json` changes, and verifies a disposable container sees exactly one T4.
- Dry run prints all NVIDIA mutations and validations without executing privileged changes.

### Step 1: Add failing parser and prerequisite tests

Add tests for:

- `--gpu` without `--cuda-image` fails before mutation.
- `--cuda-image` without `--gpu` fails.
- a CUDA image containing `:latest`, lacking `@sha256:`, or containing whitespace/newlines fails.
- missing `nvidia-smi` fails with an actionable Deep Learning VM driver message.
- zero, two, or a non-T4 GPU fails before apt changes.
- normal CPU `--check` and `--install` never invoke NVIDIA tools or repository URLs.

Use fake `nvidia-smi --query-gpu=name --format=csv,noheader` output of exactly `NVIDIA T4` for the happy path.

### Step 2: Run focused tests and confirm RED

Run:

```powershell
python -m unittest tests.test_bootstrap -v
```

Expected: new GPU cases fail because the parser rejects the new arguments.

### Step 3: Implement strict option parsing and pre-mutation validation

Parse the mode first, then consume only `--gpu --cuda-image VALUE`. Reject duplicates, missing values, unknown options, newline-bearing values, unpinned references, and `latest`. In GPU mode:

```bash
command -v nvidia-smi >/dev/null 2>&1 ||
  die "GPU mode requires the NVIDIA driver and nvidia-smi from the Deep Learning VM image"
mapfile -t gpu_names < <(nvidia-smi --query-gpu=name --format=csv,noheader)
[[ ${#gpu_names[@]} -eq 1 && ${gpu_names[0]} == "NVIDIA T4" ]] ||
  die "GPU mode requires exactly one NVIDIA T4"
```

Keep these checks before any apt or file mutation.

### Step 4: Add failing installation, idempotency, and dry-run tests

Cover:

- NVIDIA keyring and stable apt source creation.
- installation of `nvidia-container-toolkit` only in GPU mode.
- first configuration changes `daemon.json` and restarts Docker once.
- second configuration produces identical `daemon.json` and does not restart Docker.
- dry run prints keyring/source/package/`nvidia-ctk`/restart/container-test commands but fake mutation logs stay empty.
- validation runs `docker run --rm --gpus all IMAGE nvidia-smi --query-gpu=name --format=csv,noheader`.
- wrong container GPU output and failed Docker service health are hard failures.

### Step 5: Implement official repository, conditional configure, and validation

Use these official repository inputs:

```text
https://nvidia.github.io/libnvidia-container/gpgkey
https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list
```

Write the dearmored key to `/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg`, transform the list so its `deb` entry uses that `signed-by`, and write `/etc/apt/sources.list.d/nvidia-container-toolkit.list`. Install `nvidia-container-toolkit` with `--no-install-recommends`.

For idempotent runtime configuration:

1. hash or copy `/etc/docker/daemon.json` before configuration, treating absence explicitly;
2. execute `nvidia-ctk runtime configure --runtime=docker`;
3. compare the post-configuration bytes/hash;
4. restart Docker only when the file changed;
5. require Docker active and run the pinned container validation;
6. require container output to be exactly one `NVIDIA T4`.

All privileged operations must continue through `run_root`; all generated files must continue through the repository's safe root-file helper.

### Step 6: Verify syntax and tests

Run:

```powershell
bash -n scripts/remote/bootstrap-host.sh
python -m unittest tests.test_bootstrap -v
```

Expected: syntax valid and all bootstrap tests pass.

### Step 7: Commit

```powershell
git add scripts/remote/bootstrap-host.sh tests/test_bootstrap.py tests/fakes
git commit -m "feat: bootstrap NVIDIA container runtime"
```

---

## Task 4: Expose GPU bootstrap through Bash and PowerShell

**Files:**

- Modify: `scripts/bootstrap.sh`
- Modify: `scripts/bootstrap.ps1`
- Modify: `tests/test_bash_operator.py`
- Modify: `tests/test_powershell_operator.py`

**Contract:**

- Bash accepts no arguments or exactly `--gpu`.
- PowerShell accepts the `[switch]$Gpu` parameter.
- GPU mode reads `NVIDIA_CUDA_IMAGE` from repository-root `versions.env`, validates one exact assignment, and forwards `--install --gpu --cuda-image VALUE` to the uploaded remote script without shell interpolation.
- Normal invocation forwards only `--install` exactly as before.
- Both entry points continue honoring an absolute `STACK_REMOTE_ENV` for target selection.

### Step 1: Add failing forwarding and validation tests

In both operator suites assert:

- default bootstrap remote arguments remain `sudo bash REMOTE_SCRIPT --install`;
- GPU bootstrap adds the exact four tokens `--gpu --cuda-image PINNED_REFERENCE` after `--install`;
- an absent, duplicate, malformed, or unpinned `NVIDIA_CUDA_IMAGE` fails before SCP/SSH mutation;
- `STACK_REMOTE_ENV` selects a second fixture target without changing any committed default;
- unknown Bash options and unexpected PowerShell positional arguments fail.

### Step 2: Run focused tests and confirm RED

Run:

```powershell
python -m unittest tests.test_bash_operator tests.test_powershell_operator -v
```

Expected: GPU invocation/forwarding tests fail.

### Step 3: Implement symmetric option handling

For Bash, parse exactly:

```bash
gpu_mode=false
case $# in
  0) ;;
  1) [[ $1 == --gpu ]] || common_die "usage: bootstrap.sh [--gpu]"; gpu_mode=true ;;
  *) common_die "usage: bootstrap.sh [--gpu]" ;;
esac
```

For PowerShell use:

```powershell
[CmdletBinding()]
param([switch]$Gpu)
```

In each shell, read the pin as data rather than sourcing/evaluating `versions.env`; require exactly one `NVIDIA_CUDA_IMAGE=` line and pass it as one SSH argument. Keep cleanup behavior unchanged on every error path.

### Step 4: Verify both shells

Run:

```powershell
bash -n scripts/bootstrap.sh
python -m unittest tests.test_bash_operator tests.test_powershell_operator -v
```

Expected: all tests pass.

### Step 5: Commit

```powershell
git add scripts/bootstrap.sh scripts/bootstrap.ps1 tests/test_bash_operator.py tests/test_powershell_operator.py
git commit -m "feat: expose GPU bootstrap operators"
```

---

## Task 5: Gate inference releases on real T4 execution

**Files:**

- Modify: `scripts/remote/preflight.sh`
- Modify: `scripts/remote/health.sh`
- Modify: `scripts/remote/deploy-release.sh` only if dependency injection is required for lifecycle tests
- Modify: `tests/test_remote_runtime.py`
- Modify: `tests/test_release_lifecycle.py`
- Modify/Create as required: command fakes and fixtures under `tests/fakes/` and `tests/fixtures/`

**Contract:**

- GPU checks run only when `inference` is selected.
- Preflight proves the host and Docker container runtime see exactly one T4 using the pinned catalog image before Compose mutation.
- Health confirms both running Ollama containers have NVIDIA GPU device requests, performs one bounded request against each API, and requires positive VRAM for that service's approved model.
- A failure occurs before the release's `current` symlink changes; volumes remain preserved.
- Health output contains no generated model text, embedding vectors, secrets, or complete runtime environment.

### Step 1: Add failing inference preflight tests

Extend the runtime fake harness to test:

- `core` alone does not invoke `nvidia-smi` or `docker run --gpus`.
- `inference` requires host `nvidia-smi` output exactly `NVIDIA T4`.
- `inference` reads `NVIDIA_CUDA_IMAGE` from the release `versions.env` using a strict non-evaluating parser.
- `inference` runs the pinned disposable container and requires exactly `NVIDIA T4` from it.
- missing/malformed pin, missing/wrong host GPU, and failed/wrong container output fail before Compose `config`.
- existing Docker-root disk and memory checks remain intact.

### Step 2: Implement inference-only preflight

Add overridable `NVIDIA_SMI_BIN` for tests, strict pin parsing from `$release_dir/versions.env`, and exact host/container GPU validation. The container command is:

```bash
docker run --rm --gpus all "$cuda_image" \
  nvidia-smi --query-gpu=name --format=csv,noheader
```

Do not run it for any selection lacking `inference`.

### Step 3: Add failing health and release-activation tests

Replace the old “health never generates or embeds” expectation with exact bounded acceptance assertions:

- POST `http://127.0.0.1:11440/api/generate` with `stream:false`, `num_predict:1`, and the runtime LLM model.
- POST `http://127.0.0.1:11441/api/embed` with a one-string `input` and the runtime embedding model.
- GET `/api/ps` from each service after its request.
- require the matching model's `size_vram` to parse as a number greater than zero.
- inspect both containers and require an NVIDIA device request with `Driver=nvidia`, GPU capability, and all devices.
- require host `nvidia-smi --query-compute-apps=used_gpu_memory --format=csv,noheader,nounits` to contain positive numeric memory while both keep-alive windows are active.

Add subtests for chat failure, embedding failure, invalid JSON, missing model, zero VRAM, missing device request on either container, and zero host compute memory. In `test_release_lifecycle.py`, make each such health failure preserve the previous `current` symlink and leave Ollama named volumes untouched.

### Step 4: Implement bounded requests and positive-VRAM checks

Load `OLLAMA_LLM_MODEL` and `OLLAMA_EMBEDDING_MODEL` strictly from `versions.env`. Generate request JSON with `jq -n --arg` so model names are data. Use curl flags `--fail --silent --show-error --max-time 120 --header 'Content-Type: application/json' --data-binary @-`; capture only enough JSON to validate the response and discard it afterward.

For `/api/ps`, require:

```jq
any(.models[]?;
  ((.name == $model or .model == $model) and
   ((.size_vram // 0 | tonumber) > 0)))
```

Inspect device requests through the existing Compose/Docker project identity without accepting an unrelated container. Do not print response bodies.

### Step 5: Run focused runtime and lifecycle tests

Run:

```powershell
bash -n scripts/remote/preflight.sh
bash -n scripts/remote/health.sh
python -m unittest tests.test_remote_runtime tests.test_release_lifecycle -v
```

Expected: syntax valid, GPU failures prevent activation, and all tests pass.

### Step 6: Commit

```powershell
git add scripts/remote/preflight.sh scripts/remote/health.sh scripts/remote/deploy-release.sh tests/test_remote_runtime.py tests/test_release_lifecycle.py tests/fakes tests/fixtures
git commit -m "feat: gate inference releases on GPU use"
```

---

## Task 6: Document and enforce the two-target operator workflow

**Files:**

- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `tests/test_repository_contract.py`
- Modify: `tests/test_documentation.py`

**Contract:**

- `remote.data.env` and `remote.gpu.env` are ignored.
- Documentation makes split-host deployment the canonical workflow and no longer describes CPU inference as active.
- Exact Bash and PowerShell commands use absolute `STACK_REMOTE_ENV` paths.
- Spot recovery changes only `REMOTE_HOST`; it does not redeploy an unchanged release.
- Old CPU model volumes are preserved but not advertised as fallback.
- Both tunnel processes keep inference at `127.0.0.1:11440` and `127.0.0.1:11441`.

### Step 1: Write the failing ignore behavior assertion

Add a repository-contract test that invokes `git check-ignore` against temporary repository-root names and requires:

- `.gitignore` matches `remote.data.env`, `remote.gpu.env`, `.env`, and `remote.env`.
- it does not ignore `remote.env.example` or an unrelated tracked-style name.

Do not add exact-prose assertions for README or operations documentation. Human prose is reviewed manually. Update pre-existing documentation test expectations only where the approved split-host commands make their old expectation false.

### Step 2: Run focused tests and confirm RED

Run:

```powershell
python -m unittest tests.test_repository_contract tests.test_documentation -v
```

Expected: the new ignore behavior assertion fails because the two target names are not yet ignored.

### Step 3: Update ignore rules and operator docs

Use `remote.*.env` as the narrow ignore rule. Document:

- deriving both files from `remote.env.example`;
- the accepted shared `.env` secret trade-off;
- independent bootstrap/check/deploy/tunnel commands;
- GPU acceptance failure behavior;
- first model-download wait and cache reuse;
- data-host inference stop only after GPU acceptance;
- loopback-only endpoints and two simultaneous tunnel terminals;
- Spot stop/start, ephemeral IP refresh, automatic container restart, and persistent volumes;
- the daily Backup and DR plan as recovery media, not an application-level backup guarantee.

Do not commit either real target file or any complete public IP.

### Step 4: Manually review documentation, then run existing contracts

Read both rendered Markdown files end to end and verify the approved target names, shell commands, profile split, endpoints, cutover order, and Spot recovery instructions. This manual review is the acceptance for human prose; record it in the task report.

Run:

```powershell
python -m unittest tests.test_repository_contract tests.test_documentation tests.test_tunnels -v
```

Expected: all tests pass and tunnel mappings remain unchanged.

### Step 5: Commit

```powershell
git add .gitignore README.md docs/operations.md tests/test_repository_contract.py tests/test_documentation.py
git commit -m "docs: add split-host GPU operations"
```

---

## Task 7: Run the complete local verification suite

**Files:**

- Create: `docs/verification/split-gpu-inference-local.md`

### Step 1: Prove the worktree scope is clean except for preserved user edits

Run:

```powershell
git status --short
git diff -- config/ollama/bootstrap.sh tests/test_ollama_bootstrap.py
```

Expected: only the two known user-owned files remain unstaged; every task change is committed.

### Step 2: Run syntax, Compose, Python, and PowerShell verification

Run:

```powershell
Get-ChildItem scripts -Recurse -Filter *.sh | ForEach-Object { bash -n $_.FullName }
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector --profile dynamodb --profile search --profile observability --profile tools config --quiet
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile inference config --quiet
python -m unittest discover -s tests -v
```

If a shell-specific test is skipped because the executable is unavailable, record the exact skip and run the available parity path; do not report it as passed.

### Step 3: Record sanitized local evidence

Record the commit, command list, counts, timestamps, Compose version, Python version, PowerShell version, and pass/fail summaries. Do not copy secrets, target files, or model output.

### Step 4: Commit

```powershell
git add docs/verification/split-gpu-inference-local.md
git commit -m "test: verify split GPU contracts locally"
```

---

## Task 8: Bootstrap, deploy, cut over, and exercise Spot recovery

**Files:**

- Local ignored files only: `remote.data.env`, `remote.gpu.env`
- Create after verification: `docs/verification/split-gpu-inference-gcp-smoke.md`

**Known targets at plan time:**

- Data VM: `<public-ip>`, SSH user `<ssh-principal>`.
- GPU VM: instance `nvidia-t4-26-gb-us-central-1`, zone `us-central1-f`, initial external address `<public-ip>`, SSH user `<ssh-principal>`.
- Dedicated identity: `<identity-file>`.

Treat addresses as ephemeral observations. Discover the current values before every connection and never commit a complete public IP.

### Step 1: Establish SSH access without overwriting metadata

1. Confirm the private key mode/fingerprint and test batch SSH to the GPU address.
2. If the key is not accepted, read instance and project SSH metadata first.
3. Merge `<ssh-principal>:<public-key>` into instance metadata without deleting another principal's key.
4. Retry batch SSH and record only the key fingerprint and redacted address.

Do not rotate the existing data VM key and do not disable project-wide keys as part of this task.

### Step 2: Create a temporary detached clean deployment worktree

Create a second, temporary worktree detached at the reviewed feature `HEAD`. Verify it is clean and points at the exact commit that passed Task 7. Copy the existing ignored `.env` into that worktree without displaying it. Perform all Task 8 operator commands from the detached worktree. Remove the temporary worktree only after smoke evidence is committed from the primary feature worktree.

This is an operational cleanliness boundary, not a new development branch. Never stash or clean the primary worktree.

### Step 3: Create the two ignored target files safely

Copy `remote.env.example` into `remote.data.env` and `remote.gpu.env`; set only the target-specific host/user/port/key/root values. Preserve the existing local port map. Verify:

```powershell
git check-ignore remote.data.env remote.gpu.env
git status --short
```

Expected: both files are ignored and absent from Git status.

### Step 4: Check and bootstrap the GPU host

Run:

```powershell
$env:STACK_REMOTE_ENV = (Resolve-Path .\remote.gpu.env)
.\scripts\check.ps1 inference
.\scripts\bootstrap.ps1 -Gpu
.\scripts\check.ps1 inference
```

Reconnect after group/runtime changes. On the host verify Docker, Compose, `nvidia-smi`, the exact T4 name, toolkit configuration, and the pinned CUDA container test. Confirm ports 11440/11441 are not publicly listening.

### Step 5: Deploy only inference and wait for atomic acceptance

Run:

```powershell
$env:STACK_REMOTE_ENV = (Resolve-Path .\remote.gpu.env)
.\scripts\deploy.ps1 inference
```

The first pull may approach the 90-minute health window. Report progress from sanitized remote container state at least once per minute. Require:

- only `ollama-llm` and `ollama-embedding` in the selected project;
- both healthy;
- bounded chat and embedding responses;
- positive `size_vram` for each approved model while both are loaded;
- positive host compute-process VRAM;
- NVIDIA device requests on both containers;
- published binds exactly `127.0.0.1:11440` and `127.0.0.1:11441`;
- the GPU release becomes `current` only after all checks pass.

Redeploy the same commit once and confirm the second deployment reuses model volumes without downloading fresh model data.

### Step 6: Verify the local inference tunnel

Start `scripts/tunnel.ps1 inference` as a hidden background process, wait for both local ports, issue one bounded chat request and one embedding request to the unchanged localhost endpoints, then stop only that local SSH process. Do not print response bodies or vectors.

### Step 7: Cut inference off the data host

Only after Steps 5 and 6 pass:

```powershell
$env:STACK_REMOTE_ENV = (Resolve-Path .\remote.data.env)
.\scripts\stack.ps1 stop inference
.\scripts\stack.ps1 status
```

Verify no Ollama container is running on the data VM, both old named volumes still exist, and every one of the 16 non-inference containers is running and healthy. Probe every loopback endpoint through the data tunnel without printing secrets.

### Step 8: Exercise actual Spot stop/start recovery

1. Capture the active GPU release identifier, volume names/sizes, model identities, and current ephemeral address in memory only.
2. Stop the GCP instance and wait until `TERMINATED`.
3. Start it and wait until `RUNNING` with SSH available.
4. Discover the new ephemeral address and change only `REMOTE_HOST` in ignored `remote.gpu.env`.
5. Do not redeploy.
6. Verify Docker auto-started both containers from the same active release, both named volumes/models persisted, both services pass the complete GPU health checks, local ports remain loopback-only, and the renewed local tunnel serves bounded chat and embedding requests.

If Spot capacity prevents restart, retry with bounded backoff and record the exact cloud condition. Do not change machine type, disk, accelerator, zone, Spot status, or create a replacement VM without new user authority.

### Step 9: Restore both split-host tunnels for the operator

After recovery acceptance, start one hidden long-lived data-host tunnel for `core vector dynamodb search observability tools` and one hidden long-lived GPU-host tunnel for `inference`. Verify every local endpoint is bound by exactly the intended SSH process and that both SSH targets use the dedicated identity. Leave both tunnel processes running for the returning operator.

### Step 10: Record sanitized GCP evidence

Create `docs/verification/split-gpu-inference-gcp-smoke.md` containing:

- date/time and commit;
- redacted host identities and instance/zone names;
- driver/toolkit/Docker/Compose versions;
- pinned CUDA image and exact T4 observations;
- release activation and cache-reuse evidence;
- positive per-model/host VRAM numbers without model output;
- loopback bind and tunnel results;
- data-host non-inference health and preserved stopped volumes;
- before/after Spot state, redacted address change, same release/volume confirmation;
- backup plan observation;
- any bounded retry or remaining operational limitation.

Never include secrets, complete IP addresses, SSH public-key material, model response text, embedding vectors, or unredacted environment files.

### Step 11: Commit evidence and run final verification

```powershell
git add docs/verification/split-gpu-inference-gcp-smoke.md
git commit -m "test: verify split GPU inference on GCP"
python -m unittest discover -s tests -v
git status --short
```

Expected: complete suite passes; only the two preserved user-owned Ollama edits remain unstaged.

---

## Task 9: Accept the T4 label reported by the GCP driver

**Discovery:** Task 8's first live bootstrap failed before mutation because driver 580.173.02 reports the one approved accelerator as `Tesla T4`, while the reviewed scripts accepted only `NVIDIA T4`. GCP independently reports one `nvidia-tesla-t4`. This compatibility repair must complete before Task 8 resumes.

**Files:**

- Modify: `scripts/remote/bootstrap-host.sh`
- Modify: `scripts/remote/preflight.sh`
- Modify: `tests/test_bootstrap.py`
- Modify: `tests/test_remote_runtime.py`

**Contract:** Require exactly one GPU whose complete `nvidia-smi` name is either `Tesla T4` (the observed GCP driver label) or `NVIDIA T4` (the already-supported label). Do not use substring matching and do not accept any other Tesla/NVIDIA product. Apply the same exact predicate to host and disposable-container inventory in bootstrap and preflight. Keep every pre-mutation, inference-only, exit-status, blank-record, and strict-count guarantee unchanged.

### Step 1: Add the live-label failing regressions

Add focused tests that make host and container `nvidia-smi` return exactly `Tesla T4` for bootstrap and preflight. Run them against the current code and confirm RED only because the exact label is rejected. Add table cases proving both accepted labels pass and `Tesla V100`, `NVIDIA A100`, `NVIDIA T4 extra`, an empty row, and two T4 rows fail.

### Step 2: Implement one exact predicate in each standalone script

Use an exact `case`/comparison accepting only `Tesla T4|NVIDIA T4`. Retain the exact-one-record check before applying the predicate. Update the actionable error to state that `nvidia-smi` may label an NVIDIA T4 as `Tesla T4`. Do not weaken command-status handling or image pin validation.

### Step 3: Verify focused and lifecycle behavior

Run under WSL so Bash paths execute:

```powershell
wsl.exe -d Ubuntu -- bash -lc "cd /mnt/c/Users/Somanshu/Documents/code/agents/remote-infra-stack/.worktrees/remote-infra-stack && bash -n scripts/remote/bootstrap-host.sh && bash -n scripts/remote/preflight.sh && python3 -m unittest tests.test_bootstrap tests.test_remote_runtime tests.test_release_lifecycle -v"
```

Expected: all tests pass, including both aliases and every wrong/count/status failure.

### Step 4: Commit

```powershell
git add scripts/remote/bootstrap-host.sh scripts/remote/preflight.sh tests/test_bootstrap.py tests/test_remote_runtime.py
git commit -m "fix: accept GCP Tesla T4 driver label"
```

---

## Task 10: Refresh local verification after the live-label repair

**Files:**

- Modify: `docs/verification/split-gpu-inference-local.md`

Run the complete Task 7 verification matrix again on the Task 9 commit: all script syntax, both Compose renders, the normal unrestricted Windows aggregate, and the complementary WSL/Git Bash focused suites. Update the evidence timestamp, tested commit, aggregate counts, and focused POSIX counts without removing the prior diagnostic explanation. Commit only the evidence refresh:

```powershell
git add docs/verification/split-gpu-inference-local.md
git commit -m "test: refresh split GPU local evidence"
```

Task 8 then removes or recreates its old detached deployment checkout at this new reviewed `HEAD` and restarts from SSH/target verification. It must not reuse the old release archive.

---

## Final review and handoff

After Task 8:

1. Run a fresh final specification review against the approved design and every success criterion.
2. Run a fresh quality/security review over the full diff from `87a6003` through `HEAD`.
3. Resolve all critical, important, and in-scope minor findings through the same test-first task workflow.
4. Invoke `verification-before-completion` and rerun every required command from a clean committed task state.
5. Invoke `finishing-a-development-branch`; present the branch/worktree status and integration choices without merging, pushing, deleting the worktree, or committing the user's two unrelated edits unless explicitly authorized.
