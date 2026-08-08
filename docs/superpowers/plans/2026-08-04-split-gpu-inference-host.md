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

## Task 11: Preserve a coherent vendor-held NVIDIA toolkit

**Discovery:** Task 8's second live bootstrap passed the T4 guard and installed Docker, then APT refused to upgrade `nvidia-container-toolkit`. The Deep Learning VM coherently preinstalls and holds `nvidia-container-toolkit`, `nvidia-container-toolkit-base`, `libnvidia-container-tools`, and `libnvidia-container1` at `1.17.8-1`; the stable repository advertises all four at `1.19.1-1`. Our top-level-only install selected the newer toolkit while APT correctly retained its held dependencies. No NVIDIA package, runtime configuration, deployment, cutover, tunnel, or Spot action occurred.

**Files:**

- Modify: `scripts/remote/bootstrap-host.sh`
- Modify: `tests/test_bootstrap.py`

**Contract:** Before any host mutation in GPU install mode, classify the official four-package toolkit set. If all four packages are installed at one identical nonempty version and `nvidia-ctk` exists, preserve that vendor-managed set and skip package installation without changing APT holds. If none are installed, install all four packages together from the validated stable repository. If the set is partial, version-skewed, malformed, or lacks `nvidia-ctk`, fail before mutation with an actionable error. Never run `apt-mark unhold`, `--allow-change-held-packages`, or a forced toolkit upgrade. In every accepted path, retain conditional Docker runtime configuration and the pinned CUDA container's exact-one-T4 validation.

### Step 1: Add failing toolkit-state regressions

Extend the command fakes to represent installed package versions and cover:

- a coherent held `1.17.8-1` quartet skips NVIDIA package installation but still configures and validates the runtime;
- a completely absent quartet installs all four packages in one APT invocation;
- partial installation, mismatched versions, malformed/blank query records, and missing `nvidia-ctk` fail before any mutation;
- unrelated held packages such as `nccl-gib` remain untouched;
- dry-run and repeated install behavior retain their no-mutation/idempotence guarantees.

Run the focused test first and confirm RED for the missing state handling.

### Step 2: Implement the fail-closed package classifier

Use exact package names and `dpkg-query` status/version output without lossy command-substitution parsing. Require four and only four classified records. Branch only to `reuse` (all installed, same version, CLI present) or `install` (all absent); reject every mixed or ambiguous state before the first `run_root` mutation. Do not inspect or alter unrelated holds.

For the absent path, retain the signed stable repository setup and install these packages together with `--no-install-recommends`:

```text
nvidia-container-toolkit
nvidia-container-toolkit-base
libnvidia-container-tools
libnvidia-container1
```

### Step 3: Verify focused and aggregate behavior

Run Bash syntax plus the bootstrap suite under WSL, then the normal Windows aggregate. Require zero failures and verify the original two user-owned Ollama files are the only unrelated unstaged changes.

### Step 4: Commit and review

```powershell
git add scripts/remote/bootstrap-host.sh tests/test_bootstrap.py
git commit -m "fix: preserve vendor-held NVIDIA toolkit"
```

Obtain an independent Task 11 review before recreating Task 8's detached deployment checkout at the repaired commit. Task 8 must retry bootstrap from the beginning and must not manually unhold or upgrade the live toolkit.

---

## Task 12: Recognize healthy held-package status records

**Discovery:** Task 8's third live bootstrap retried from approved commit `7bc3d02` and failed closed before mutation. All four coherent toolkit packages report exact `dpkg-query ${Status}` value `hold ok installed` at `1.17.8-1`; `dpkg --audit` is empty and `nvidia-ctk` exists. Task 11's supposedly held fixture incorrectly emitted `install ok installed`, so the classifier rejected the vendor-held state it was designed to preserve. No runtime configuration, deployment, cutover, tunnel, or Spot action occurred.

**Files:**

- Modify: `scripts/remote/bootstrap-host.sh`
- Modify: `tests/test_bootstrap.py`

**Contract:** Treat a package as installed only when its exact three-part status is either `install ok installed` or `hold ok installed`. The selection token may differ across the quartet, but the error/status tokens must remain exactly `ok installed`; reject `deinstall`, `purge`, `unknown`, `reinstreq`, `config-files`, malformed, missing, or extra values. Retain exact package identity, one-record-per-query, coherent-version, real-exit-status, `nvidia-ctk`, zero-mutation failure, no-unhold, runtime configuration, and pinned CUDA exact-T4 guarantees.

### Step 1: Correct the fixture and capture RED

Make the coherent vendor-held fixture emit `hold ok installed` for every toolkit package and add table-driven malformed/unhealthy selection/error/status cases. Add a mixed healthy `install`/`hold` quartet to prove selection state does not trigger package mutation. Run the focused tests against current production and capture RED specifically because the real held status is rejected.

### Step 2: Implement the exact healthy-status predicate

Change only the installed-state predicate to accept the two complete healthy values. Do not use substring, suffix-only, or whitespace-normalizing matches. Keep every other Task 11 classifier and mutation boundary unchanged.

### Step 3: Verify, commit, and review

Run focused RED/GREEN, Bash syntax, the complete WSL bootstrap suite, and the unrestricted normal Windows aggregate. Commit only the two Task 12 files:

```powershell
git add scripts/remote/bootstrap-host.sh tests/test_bootstrap.py
git commit -m "fix: recognize held NVIDIA toolkit packages"
```

Obtain an independent Task 12 review before Task 8 recreates its detached checkout and retries bootstrap from the beginning.

---

## Task 13: Bound cold model load separately from warm inference

**Discovery:** Task 8's first inference deployment from approved commit `cd42cab` pulled both models and reached healthy containers, positive host compute VRAM, one model per volume, and GPU device requests. The first non-streaming one-token LLM request connected but returned zero bytes before the hardcoded 120-second limit. Atomic `current` was never created and rollback removed both containers while preserving volumes. The CPU backend remains 2/2 healthy. Offline evidence shows an approximately 8B Q4 model with a 9.61 GB GGUF below the T4's 15,360 MiB capacity, 26.02 GB host RAM available, and zero kernel/Docker OOMs, kills, Xids, segfaults, or nonzero container exits. `ollama show` does not load the model, so this first generation is also the cold-load trigger.

**Files:**

- Modify: `scripts/remote/health.sh`
- Modify: `tests/test_remote_runtime.py`
- Modify only if the lifecycle fake needs parity: `tests/test_release_lifecycle.py`

**Contract:** Keep generation bounded and output-silent, but separate the one-time cold-load budget from steady-state acceptance. The first one-token, non-streaming generation may take at most 600 seconds. After it succeeds, require the approved model to be resident with positive VRAM, then issue the same bounded generation again with the original 120-second limit to prove warm inference. The embedding request remains at 120 seconds. Do not expose a free-form timeout environment override, change model/context/parallelism, weaken JSON validation, print response/model data, activate a release before both generations and all existing GPU checks pass, or change rollback behavior.

### Step 1: Add failing cold/warm acceptance tests

Extend the HTTP fake/call assertions to require, in order:

1. LLM generation with `--max-time 600` and the existing one-token payload;
2. positive approved-model VRAM residency;
3. a second LLM generation with `--max-time 120` and the same bounded payload;
4. the existing embedding request with `--max-time 120`.

Add distinct cold-generation and warm-generation failure cases. Prove neither response body is emitted and that either failure makes health nonzero. Capture RED because current code has only one 120-second generation.

### Step 2: Implement one reusable output-silent generation check

Factor the existing request/JSON validation into a small helper taking only the fixed timeout and a non-sensitive phase label. Invoke it at 600 seconds before residency and at 120 seconds after residency. Keep the request payload constructed with `jq`, piped directly, and discarded after strict validation.

### Step 3: Verify, commit, and review

Run focused RED/GREEN under WSL, Bash syntax, the relevant remote-runtime/lifecycle suites, and the unrestricted normal Windows aggregate. Commit only Task 13 files:

```powershell
git add scripts/remote/health.sh tests/test_remote_runtime.py tests/test_release_lifecycle.py
git commit -m "fix: allow bounded Ollama cold load"
```

Obtain an independent Task 13 review before Task 8 recreates its detached checkout and retries the inference-only deployment. Task 8 must reuse the preserved volumes, keep CPU inference live, and repeat the full GPU acceptance gate; it must not tune throughput in this repair.

---

## Task 14: Capture the accepted `1/1` throughput baseline

**Approved design:** `docs/superpowers/specs/2026-08-04-balanced-t4-throughput-design.md`

**Files (ignored operational artifacts only):**

- Create: `.superpowers/sdd/2026-08-04-split-gpu-inference-host/throughput-benchmark.py`
- Create: `.superpowers/sdd/2026-08-04-split-gpu-inference-host/throughput-baseline.json`
- Create: `.superpowers/sdd/2026-08-04-split-gpu-inference-host/task-14-report.md`

**Contract:** Measure the currently accepted GPU release before any throughput code or
active `.env` change. The harness must be deterministic, standard-library only, and
output-silent for model data. It may read tracked model pins and ignored target details
into memory, but output/artifacts may contain only phase names, request counts, HTTP/error
counts, timing/token aggregates, boolean GPU/offload checks, and MiB totals. It must never
record model names, response text, embeddings, public targets, identity paths, or secrets.

### Step 1: Verify the live baseline and establish a temporary tunnel

Prove the active release is the accepted `aca6f53` lineage, both containers are healthy,
their exact environment is parallel `1/1`, loaded models `1/1`, context `8192`, and
keep-alive `5m`, with loopback binds and NVIDIA requests. Rediscover the GPU VM address
and change only ignored `REMOTE_HOST` if necessary. Start an inference-only tunnel and
verify both local ports without disturbing the data tunnel or remote containers.

### Step 2: Build and self-test the ignored benchmark harness

The Python harness accepts a phase (`baseline` or `tuned`), local endpoint ports, tracked
versions file, round count, and an optional remote sampling command assembled by the
operator. It performs:

- one output-silent warm-up per endpoint with `keep_alive: 30m`;
- five warmed serial requests per endpoint for latency reference;
- five LLM rounds of two concurrent non-streaming requests capped at 32 tokens;
- five embedding rounds of four concurrent single-input requests;
- 120-second per-request timeouts and strict JSON/status/shape validation;
- median and p95 latency, median LLM aggregate evaluated tokens/second, and median
  embedding requests/second;
- optional concurrent remote GPU-memory sampling that reports only peak aggregate MiB.

Unit-test parsing/statistics/output redaction inside the ignored artifact before using it.

### Step 3: Run and record the baseline

Run the harness through the accepted tunnel. Independently verify both models are fully
GPU-backed, no container restarts/OOM/Xid/nonzero exits occurred, and endpoint health
still passes. Store sanitized JSON and a concise ignored report. Stop only the temporary
tunnel if it is separate from an operator handoff tunnel. Do not commit a baseline-only
artifact and do not change Compose or `.env`.

---

## Task 15: Implement the balanced Compose settings

**Files:**

- Modify: `compose.yaml` - set the two literal service parallel values and the common
  keep-alive fallback while preserving every other service property.
- Modify: `.env.example` - change the generated/operator keep-alive default to `30m`.
- Modify: `README.md` - keep the existing ignored-configuration upgrade block consistent
  with the new `30m` default.
- Modify: `docs/operations.md` - update the upgrade value and document the measured
  T4-specific concurrency and memory boundary.
- Modify: `tests/fixtures/stack.env` - render the balanced `30m` test configuration.
- Modify: `tests/test_compose_inference.py` - enforce the exact per-service environment,
  non-overridable parallel values, fallback, and non-inference isolation.
- Modify: `tests/test_documentation.py` - update the existing both-guides configuration
  value contract without adding a brittle explanatory-prose assertion.
- Modify: `tests/test_env_generation.py` - require generated configuration to default to
  `30m`.

**Contract:** Set LLM parallelism to exactly `2`, embedding parallelism to exactly `4`,
and the documented/default keep-alive to exactly `30m`. Keep context `8192`, loaded models
`1`, service-specific memory caps, GPU reservations, profiles, ports, volumes, health,
and every non-inference service unchanged. Parallelism remains a committed literal per
service, not a free-form environment override. Keep the historical
`docs/superpowers/specs/2026-08-02-data-and-inference-profiles-design.md` record unchanged.

### Step 1: Add failing exact-value contracts

Before changing Compose, defaults, fixtures, or documentation, update
`tests/test_compose_inference.py` so its contract helper accepts a literal expected
parallel value and asserts each service's complete environment map: LLM parallel `2`,
embedding parallel `4`, common keep-alive `30m`, context `8192`, and loaded models `1`.
Add these behavioral cases using real Compose rendering:

- render with `OLLAMA_KEEP_ALIVE` present but empty and require both services to receive
  the Compose fallback `30m`;
- poison `OLLAMA_NUM_PARALLEL`, `OLLAMA_LLM_NUM_PARALLEL`, and
  `OLLAMA_EMBEDDING_NUM_PARALLEL` in the calling environment, then require the rendered
  values to remain the committed literals `2` and `4`;
- render all profiles and require every non-inference service environment to omit the
  Ollama context, keep-alive, loaded-model, model, and parallel keys.

Change `tests/test_env_generation.py` to expect `OLLAMA_KEEP_ALIVE=30m`. Change only the
existing both-guides configuration-value contract in `tests/test_documentation.py` from
`5m` to `30m`; do not add an exact-sentence or regex test for the new explanatory prose.
That prose is manually inspected in Step 3, as required by the plan-wide preflight
correction.

Run:

```powershell
python -m unittest tests.test_compose_inference tests.test_env_generation.EnvGenerationTests.test_example_declares_data_and_inference_resource_defaults tests.test_documentation.DocumentationContractTests.test_both_guides_document_ignored_env_upgrade_keys -v
```

Capture RED specifically because Compose still renders literal parallel `1/1` and a
`5m` empty-value fallback, `.env.example` still declares `5m`, and both current guides
still publish `5m`. The non-inference isolation case already describes a preserved
boundary and may remain green during this RED run.

### Step 2: Make the minimal Compose and documentation change

In `compose.yaml`, change only the LLM parallel literal to `2`, the embedding parallel
literal to `4`, and both keep-alive fallbacks to `30m`. Change `OLLAMA_KEEP_ALIVE` to
`30m` in `.env.example` and `tests/fixtures/stack.env`. Update the existing upgrade blocks
in both `README.md` and `docs/operations.md` to `30m`; explicitly tell operators who
already have `OLLAMA_KEEP_ALIVE=5m` to replace that one non-secret assignment rather than
regenerate their ignored `.env`.

In `docs/operations.md`, explain that the committed T4 layout uses two LLM requests and
four embedding requests in parallel, retains one loaded model per dedicated container,
keeps context at `8192`, and is accepted only for the measured T4 layout. Cite
`https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests` for the documented
parallelism-times-context memory behavior. Manually review this explanation for semantic
coverage; do not add a test that merely matches its wording. Do not change the historical
design spec, model pins, API examples, ports, volumes, memory caps, GPU reservations,
health timeouts, or non-inference services.

### Step 3: Verify and commit

Render inference and every non-inference profile with a redacted temporary `.env`. Run
the focused Compose/documentation/repository suites, `git diff --check`, and confirm only
the eight authorized Task 15 files plus the two preserved user edits were present before
commit.

Run:

```powershell
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile inference config --quiet
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector --profile dynamodb --profile search --profile observability --profile tools config --quiet
python -m unittest tests.test_compose_inference tests.test_compose_invariants tests.test_env_generation tests.test_documentation tests.test_repository_contract -v
git diff --check
git status --short
```

Manually inspect the rendered inference environment for exact `2/4`, `1/1`, `8192`, and
`30m`; inspect the non-inference render for absence of Ollama settings; and review the
operator explanation for the T4-only scope, exact concurrency, unchanged context/model
count, memory-scaling warning, and official Ollama reference without relying on a prose
grep test.

```powershell
git add compose.yaml .env.example README.md docs/operations.md tests/fixtures/stack.env tests/test_compose_inference.py tests/test_documentation.py tests/test_env_generation.py
git commit -m "feat: balance T4 inference concurrency"
```

Obtain an independent Task 15 review before any live deployment.

---

## Task 16: Verify the balanced tree locally

**Files:**

- Modify: `docs/verification/split-gpu-inference-local.md`

Run the complete accepted local matrix on the reviewed Task 15 commit:

- all shell syntax checks;
- inference plus all non-inference Compose renders with no secret output;
- focused Compose/documentation/repository tests;
- WSL/Git Bash operator, remote-runtime, lifecycle, and tunnel parity suites;
- exact unrestricted Windows aggregate with the canonical local ports temporarily free.

If current operator tunnels occupy canonical ports, stop only their captured local
launcher/SSH processes for the aggregate, then relaunch and revalidate them. Do not change
remote services. Update the local evidence with the exact commit/counts and balanced
render contract, commit only that document, and obtain independent review.

```powershell
git add docs/verification/split-gpu-inference-local.md
git commit -m "test: verify balanced T4 contracts locally"
```

---

## Task 17: Deploy, benchmark, and accept or revert balanced throughput

**Files:**

- Local ignored: `.env`, `remote.gpu.env`, Task 14 harness/baseline artifacts
- Create: `docs/verification/balanced-t4-throughput-gcp.md`

### Step 1: Prepare a clean reviewed deployment checkout

Refresh the detached deployment worktree to the reviewed Task 16 `HEAD`. Copy ignored
configuration without displaying it. Update exactly one existing `.env` assignment,
`OLLAMA_KEEP_ALIVE`, from `5m` to `30m`; verify no other byte-level assignment changed.
Keep both remote target files ignored and rediscover current ephemeral addresses.

### Step 2: Deploy atomically to the GPU host

Deploy only `inference`. Require the complete cold-600/warm-120/embed-120 health gate,
both containers healthy, exact `2/4`, `1/1`, `8192`, and `30m` environment, NVIDIA device
requests, loopback binds, fully GPU-backed models, positive host compute memory, and no
OOM/Xid/nonzero exit. The data host remains at 16 non-inference services with CPU Ollama
stopped.

If deployment health fails, prove the prior release remains/restores `current` and stop.

### Step 3: Run the tuned benchmark and compare

Start an inference tunnel and run the unchanged Task 14 harness with phase `tuned`. Verify
its schema/configuration fingerprint matches the baseline artifact. Compute and record:

- tuned/baseline median LLM aggregate evaluated tokens/second ratio;
- tuned/baseline median embedding requests/second ratio;
- tuned p95 latency divided by warmed serial baseline p95 for each endpoint;
- errors/timeouts/statuses, peak compute VRAM, GPU process count, full-GPU booleans,
  container restarts, OOM/Xid/nonzero-exit counts.

Accept only all seven criteria in the approved balanced design. After the primary
decision, leave both endpoints idle for more than five minutes, prove models remain
resident with the exact `30m` environment, and repeat output-silent bounded calls. This
idle observation is not included in throughput ratios.

### Step 4: Automatic rollback on any failed criterion

If any post-deployment criterion fails:

1. preserve sanitized failure evidence;
2. create a normal revert commit that restores exact `1/1` and `5m` contracts/docs;
3. independently review that revert;
4. deploy the revert commit atomically to the same GPU host;
5. re-prove the baseline health/endpoints and leave the reverted release current.

Do not alter models, volumes, context, memory caps, GPU/VM/cloud settings, or data-host
profiles to rescue a failed benchmark.

### Step 5: Record evidence, restore tunnels, and commit

Create sanitized throughput evidence containing settings, methodology, aggregate metrics,
ratios, safety checks, idle-residency result, accepted/reverted outcome, and tested commit.
Exclude complete public addresses, target/env contents, key material, model identities,
response text, and vectors. Leave root-owned foreground data and GPU tunnel sessions
running and independently verify all 15 local ports plus stable endpoint calls.

```powershell
git add docs/verification/balanced-t4-throughput-gcp.md
git commit -m "test: verify balanced T4 throughput on GCP"
```

Obtain independent review of the live evidence and final state.

---

## Final review and handoff

After Task 17:

1. Run a fresh final specification review against the approved design and every success criterion.
2. Run a fresh quality/security review over the full diff from `87a6003` through `HEAD`.
3. Resolve all critical, important, and in-scope minor findings through the same test-first task workflow.
4. Invoke `verification-before-completion` and rerun every required command from a clean committed task state.
5. Invoke `finishing-a-development-branch`; preserve the user's two unrelated edits. The
   operator has explicitly authorized integration to GitHub `master` after all work, so
   update local `master` without staging those edits and push the final reviewed commit to
   `origin/master`. Do not delete a worktree while it is needed by live tunnel sessions.
