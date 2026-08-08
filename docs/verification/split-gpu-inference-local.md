# Split GPU inference host - local verification

## Scope and environment

- Authoritative session/control date: `2026-08-04`
- Observed host-clock time: `2026-08-08T09:13:35+05:30`
- Reviewed tree under test: `38217aca40f962455a317c03ed47a1f48c3af69a`
- Docker Compose: `v5.3.1`
- Python: `3.10.11`
- PowerShell: `5.1.26100.8972`
- Preserved, unstaged user edits: `config/ollama/bootstrap.sh` and
  `tests/test_ollama_bootstrap.py`

The host clock was four calendar days ahead of the authoritative session/control date.
The observed host-clock timestamp and evidence-commit metadata are retained as host-clock
observations only; they are not used to establish event sequencing or evidence freshness.

All commands were local-only. No tunnel or remote service was changed. All 15 canonical
local ports were free before the aggregate, so no process required stopping or relaunching.
Environment-file values, generated transport payloads, remote targets, credentials, public
addresses, model identities, response text, embeddings, and vectors are intentionally
omitted.

## Balanced render contract

The inference and all non-inference profiles rendered successfully with the repository's
two tracked OpenSearch sources encoded only in the child-process environment. Sanitized
inspection of the real Compose JSON proved:

```text
inference_render=2/4 parallel,1/1 loaded,8192/8192 context,30m/30m keep-alive
non_inference_ollama_environment_keys=0
```

Parallelism remained a committed literal for each inference service. The rendered values
were LLM `2`, embedding `4`, one loaded model per container, context `8192`, and keep-alive
`30m`; no Ollama environment key reached a non-inference service.

## Direct checks

| Check | Command | Result |
| --- | --- | --- |
| Shell syntax | `Get-ChildItem scripts -Recurse -Filter *.sh \| ForEach-Object { & 'C:\Program Files\Git\bin\bash.exe' -n $_.FullName }` | All 13 scripts passed using unrestricted Git Bash. |
| Inference Compose render | `docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile inference config --quiet` | Passed with process-local tracked payload setup. |
| Non-inference Compose render | `docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector --profile dynamodb --profile search --profile observability --profile tools config --quiet` | Passed with process-local tracked payload setup. |
| Focused contracts | `python -m unittest tests.test_compose_inference tests.test_compose_invariants tests.test_env_generation tests.test_documentation tests.test_repository_contract -v` | 57 total: 52 passed, 5 capability skips, 0 failures/errors, 6.007 s. |
| Windows aggregate | `python -m unittest discover -s tests -v` | 273 total: 175 passed, 98 capability skips, 0 failures/errors, 311.111 s. |

## Complementary capability evidence

The unrestricted Windows aggregate correctly reported unavailable POSIX paths as
capability skips. Fresh exact-tree runs covered the required parity paths:

- WSL bootstrap, remote-runtime, and release-lifecycle suites: 112/112 passed in
  178.695 seconds.
- Git Bash operator suite: 38 total, 37 passed and one expected directory-symlink
  privilege skip, zero failures/errors, in 89.872 seconds.
- Git Bash tunnel suite: 15/15 passed in 21.746 seconds.

The five focused-suite skips and aggregate POSIX skips are platform/capability outcomes,
not failing product checks. The executed WSL and Git Bash suites provide the required
complementary coverage. A sandboxed Git Bash syntax attempt was rejected by the host's
signal-pipe permission boundary, and a sandboxed WSL invocation was denied access to the
WSL service; neither ran repository acceptance logic. Their unrestricted reruns above are
the accepted evidence.

## Workspace result

Before the evidence edit, Git status contained exactly the two protected user files and
no staged path. The primary checkout and detached deployment worktree were tracked-clean.
No ignored environment file, Compose file, script, test, remote target, deployment, or
cloud resource changed during Task 16.
