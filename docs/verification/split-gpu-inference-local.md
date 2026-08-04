# Split GPU inference host - local verification

## Scope and environment

- Evidence collected: `2026-08-04T11:59:26+05:30`
- Tree under test: `0d8b3cd2fb9294d5745743de7c6b8404744e749b`
- Docker Compose: `v5.3.1`
- Python: `3.10.11`
- PowerShell: `5.1.26100.8972`
- Preserved, unstaged user edits: `config/ollama/bootstrap.sh` and
  `tests/test_ollama_bootstrap.py`.  No task files were left uncommitted.

All commands below were run locally.  Output, environment-file values, remote
targets, credentials, and model responses are intentionally omitted.

## Direct checks

| Check | Command | Result |
| --- | --- | --- |
| Shell syntax | `Get-ChildItem scripts -Recurse -Filter *.sh \| ForEach-Object { bash -n $_.FullName }` | Passed for all 13 scripts using unrestricted Git Bash. |
| Core/data Compose render | `docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector --profile dynamodb --profile search --profile observability --profile tools config --quiet` | Passed. |
| Inference Compose render | `docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile inference config --quiet` | Passed. |
| Python aggregate | `python -m unittest discover -s tests -v` | Passed in the normal unrestricted Windows environment: 269 tests, 172 passed, 97 capability skips, 0 failures/errors, 326.259 s. |

The two Compose renders received the verified OpenSearch transport payloads
generated from the checked-in sources; the payloads themselves were not
recorded.

## Capability evidence

The normal Windows aggregate correctly marked unavailable POSIX capabilities
as skips.  Fresh complementary executed-shell evidence on this repaired tree
covers those paths:

- WSL bootstrap, remote runtime, and release lifecycle (the Task 9 suite) -
  105/105 passed in 174.507 s.
- Unrestricted Git Bash operator - 37 passed and one directory-symlink
  capability skip (38 total) in 103.989 s.
- Git Bash tunnels - 15/15 passed in 26.103 s.

An attempted aggregate run with Git Bash forced first on `PATH` is
non-acceptance diagnostic evidence only: it encountered the Git Bash platform
limitation `realpath: /proc/self/fd/10: No such file or directory`, producing
50 failures and 28 errors.  The normal Windows aggregate above is the
acceptance run; it completed `OK` with platform-incompatible paths reported as
capability skips.
