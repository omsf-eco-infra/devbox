# Plan: Migrate DevBox CLI Behind a Lambda in PR-Sized Phases

## Summary

- This file is the living handoff document for the migration.
- Goal: migrate the current CLI to a Lambda-backed architecture in a sequence of separate PRs, where each PR ends with a locally invokable, fully working command path against deployed AWS.
- Initial migration order: `status` -> `terminate` -> `launch` -> `new` -> `delete-project` -> full cleanup/cutover.
- CLI rollout model: hybrid by command. Migrated commands go remote; unmigrated commands keep current local behavior until their phase lands.

## Locked Decisions

- Transport: Lambda Function URL from day one.
- Discovery: CLI resolves the Function URL from SSM at `${param_prefix}/cli/functionUrl`.
- Auth: CLI signs HTTP requests with AWS SigV4.
- Response model: NDJSON event stream with `progress`, `warning`, `result`, `success`, and `error`.
- Python streaming implementation: Lambda Web Adapter.
- Packaging: the original shared-image plan is superseded for phase 1. The CLI Lambda gets its own image because Lambda Web Adapter would interfere with the existing handler-based Lambdas if we baked it into the current shared image.
- Protocol ownership: shared wire-format constants plus the action and event enums live in `src/devbox/cli_protocol.py`. Future actions should extend that shared module instead of re-declaring protocol strings in the CLI and Lambda separately.
- Lambda routing: the CLI Lambda router uses an explicit dispatch table keyed by the shared action enum. Future actions should add themselves to that dispatch table rather than branching on ad hoc string comparisons.
- Command-module layout: command-specific client-side and Lambda-side behavior now lives under `src/devbox/commands/<action>.py`. `cli.py` remains the Click entry point, `remote_client.py` remains generic transport, and `cli_lambda/app.py` remains the generic dispatch layer.
- IAM layout: keep one CLI Lambda IAM statement per action wherever practical so permissions can be reviewed incrementally as commands migrate.
- Phase 1 `status` Lambda permissions are intentionally EC2-only. The local CLI resolves the Function URL from SSM, and the Lambda-side `status` handler currently reuses `DevBoxManager.list_*` inventory helpers that call EC2 `Describe*` APIs only.
- Documentation authority: this file is authoritative for phase 1. Any older standalone CLI migration spec is advisory only until it is reconciled back into this document.
- Dependencies: `requests` may be added to runtime dependencies; `responses` may be added to test dependencies.
- End-of-phase validation: real local CLI invocation against deployed AWS, not just local mocks or container smoke tests.
- `new` is deferred but remains in scope, even though the current CLI references a missing implementation.

## Public Contract

- CLI request envelope:

```json
{
  "version": "v1",
  "action": "status",
  "request_id": "uuid",
  "param_prefix": "/devbox",
  "payload": {}
}
```

- Lambda event envelope:

```json
{
  "type": "progress|warning|result|success|error",
  "action": "status",
  "message": "human-readable text",
  "data": {}
}
```

- The CLI remains responsible for:
  - resolving the Function URL from SSM
  - signing requests
  - reading local files such as `--userdata-file`
  - rendering streamed output
  - keeping local confirmation prompts for destructive flows
- The Lambda remains responsible for:
  - validating the request envelope
  - performing AWS-side operations
  - emitting versioned event streams
  - enforcing server-side safety checks

## Phase 1: Foundation and `status`

Milestone: `devbox status [project]` runs from the local CLI through the deployed CLI Lambda and renders the same tables as today.

- [x] Create the authoritative wire-contract section in this file for `status`, including request payload, result payload, and terminal event rules.
- [x] Add runtime support for HTTP invocation in the CLI, using `requests` plus SigV4 signing helpers.
- [x] Add a shared remote-invocation layer in the CLI for:
  - [x] SSM Function URL lookup
  - [x] request envelope creation
  - [x] signed HTTP POST
  - [x] NDJSON event parsing
  - [x] terminal-event validation
  - [x] error mapping to current CLI exit behavior
- [x] Add a new CLI Lambda handler/router in a separate CLI Lambda image.
- [x] Add Lambda Web Adapter support to the CLI Lambda image build.
- [x] Add Terraform resources for the CLI Lambda, Function URL, IAM, log group, and SSM parameter publication.
- [x] Implement the remote `status` action in Lambda by reusing the existing `DevBoxManager` inventory logic rather than duplicating AWS queries.
- [x] Define and implement timestamp serialization in Lambda and timestamp rehydration in the CLI so existing console rendering still works.
- [x] Keep `status` as the only migrated command in this phase; all other commands remain local.
- [x] Add tests for:
  - [x] Function URL lookup
  - [x] signed request dispatch
  - [x] streamed NDJSON parsing
  - [x] successful `status` result rendering
  - [x] malformed stream handling
  - [x] HTTP/auth failure handling
  - [x] missing SSM parameter handling
  - [x] request envelope validation
  - [x] event encoding
  - [x] action dispatch and failure mapping
- [x] Run `pixi run -e dev python -m pytest` for touched tests.
- [x] Run `tofu fmt` for touched Terraform.
- [ ] Run `tofu validate` for touched Terraform.
- [ ] Record the exact local end-to-end validation command and observed outcome in this file.

### Phase 1 `status` Contract

- Request envelope:

```json
{
  "version": "v1",
  "action": "status",
  "request_id": "uuid",
  "param_prefix": "/devbox",
  "payload": {
    "project": null
  }
}
```

- `payload.project` may be a project name string or `null`.
- Success path event sequence:
  - exactly one `result` event with the full status payload
  - exactly one terminal `success` event
- `status` result payload shape:

```json
{
  "instances": [
    {
      "InstanceId": "i-0123456789abcdef0",
      "Project": "my-project",
      "PublicIpAddress": "54.0.0.1",
      "LaunchTime": "2026-03-30T20:30:00+00:00",
      "State": "running",
      "InstanceType": "t3.medium"
    }
  ],
  "volumes": [
    {
      "VolumeId": "vol-0123456789abcdef0",
      "Project": "my-project",
      "State": "in-use",
      "Size": 100,
      "AvailabilityZone": "us-east-1a",
      "IsOrphaned": false
    }
  ],
  "snapshots": [
    {
      "SnapshotId": "snap-0123456789abcdef0",
      "Project": "my-project",
      "Progress": "100%",
      "VolumeSize": 100,
      "StartTime": "2026-03-30T18:00:00+00:00",
      "IsOrphaned": false
    }
  ]
}
```

- The CLI must rehydrate `LaunchTime` and `StartTime` to `datetime` objects before calling `ConsoleOutput`.
- Phase 1 `status` does not render progress events; warnings and errors may still be surfaced immediately.

### Phase 1 Validation Log

- `2026-03-31`: targeted phase 1 pytest suite passed after the command-module refactor.
  - Command: `pixi run -e dev python -m pytest tests/commands/test_status.py tests/cli_lambda/test_contracts.py tests/cli_lambda/test_app.py tests/test_remote_client.py tests/test_cli.py -q`
  - Result: `84 passed, 1 warning`
- `2026-03-31`: Terraform formatting passed.
  - Command: `tofu fmt`
  - Result: passed
- `2026-03-31`: Terraform validation blocked by local AWS auth/backend state from `.local.tf`.
  - Commands attempted: `tofu validate`, `tofu init -backend=false`, `tofu init -backend=false -reconfigure`
  - Result: blocked before module validation because provider/backend initialization required valid AWS credentials in the local environment
- End-to-end commands: pending
- Observed outcome: pending

### Phase 1 Handoff Notes

- The shared wire contract now lives in `src/devbox/cli_protocol.py`. Extend `CliAction` and the shared protocol constants there first whenever a new remote command is added.
- `tests/cli_lambda/test_contracts.py` owns low-level request/event contract coverage. `tests/cli_lambda/test_app.py` owns dispatch and execution-failure behavior. Keep that split as more actions land.
- `tests/commands/test_status.py` now owns the `status` command semantics across both CLI-side and Lambda-side behavior. Keep command-specific tests near the command module instead of spreading them across transport and app test files.
- The CLI Lambda router in `src/devbox/cli_lambda/app.py` is organized around `ACTION_HANDLERS`. Future actions should wire themselves into that table and add parametrized coverage rather than growing one-off branching tests.
- `status` is the template command for the new `src/devbox/commands/<action>.py` layout. Future simple commands should follow that module pattern first; only extract a shared helper after a second migrated command proves the duplication is real.
- The CLI Lambda image now uses `python:3.13-slim` directly instead of the AWS public mirror `public.ecr.aws/docker/library/python:3.13-slim`, because the mirrored image returned `403 Forbidden` during `docker build` in a fresh account deployment.
- The CLI Lambda image now uses Lambda Web Adapter `1.0.0`, matching the current official README example for container images.
- The CLI Lambda `local-exec` build step now logs into Public ECR explicitly with `aws ecr-public get-login-password` before `docker build`, because the adapter image pull failed with expired or missing Public ECR auth in a fresh-account deployment.
- The CLI Lambda `local-exec` build step now uses a temporary `DOCKER_CONFIG` so Terraform does not depend on or mutate the operator's persistent Docker Desktop keychain state on macOS.
- The phase 1 IAM policy in `modules/cli-lambda/main.tf` is intentionally grouped per action. Keep that structure so later PRs can show exactly which permissions each migrated command added.
- Do not add SSM or DynamoDB permissions to the CLI Lambda just because `DevBoxManager` can use them elsewhere. Add them only when a migrated Lambda action actually reads those services.
- Remaining phase 1 blockers: real deployed-AWS end-to-end validation for `devbox status`, plus a successful `tofu validate` run in an environment where local AWS/provider initialization works.
- Next session starts here: rerun the deployed-AWS `devbox status` validation if credentials and infrastructure are available, append the exact commands/results here, and then start phase 2 `terminate` using the `src/devbox/commands/<action>.py` pattern established by `status`.

### Inter-Phase Refactor: Command Modules

Milestone: `status` command-specific behavior is co-located under `src/devbox/commands/status.py`, while `cli.py`, `remote_client.py`, and `cli_lambda/app.py` remain generic entrypoint, transport, and router layers.

- [x] Update this file to record the command-module layout decision and handoff guidance.
- [x] Add `src/devbox/commands` and package it for distribution.
- [x] Move `status` client-side and Lambda-side command behavior into `src/devbox/commands/status.py`.
- [x] Keep `src/devbox/remote_client.py` generic by removing `status`-specific helpers.
- [x] Keep `src/devbox/cli.py` as a thin Click wrapper that delegates to the `status` command module.
- [x] Keep `src/devbox/cli_lambda/app.py` as the generic router and dispatch `status` through the command module.
- [x] Move `status` command tests to `tests/commands/test_status.py`.
- [x] Run the targeted refactor validation suite and record the result here.

## Phase 2: `terminate`

Milestone: `devbox terminate <instance-id-or-project>` runs from the local CLI through the deployed CLI Lambda and preserves current success and failure behavior.

- [ ] Extend the wire contract for `terminate`, including request payload and result payload.
- [ ] Add the remote `terminate` action to the Lambda router.
- [ ] Reuse the existing termination logic behind a Lambda-safe interface rather than reimplementing termination rules in the HTTP layer.
- [ ] Expand CLI Lambda IAM only with the permissions required for termination.
- [ ] Migrate only `terminate` in the CLI to the remote path.
- [ ] Preserve current CLI syntax and current visible success/error messages as closely as practical.
- [ ] Add tests for:
  - [ ] terminate by instance ID
  - [ ] terminate by project name
  - [ ] not-found behavior
  - [ ] multiple-instance ambiguity behavior
  - [ ] terminal `error` event mapping
  - [ ] transport failure behavior
- [ ] Run `pixi run -e dev python -m pytest` for touched tests.
- [ ] Run `tofu fmt` and `tofu validate`.
- [ ] Record the local end-to-end termination validation steps and outcome in this file.

## Phase 3: `launch`

Milestone: `devbox launch ...` runs from the local CLI through the deployed CLI Lambda, including userdata handling and current DNS flags.

- [ ] Extend the wire contract for `launch`, including all current CLI options and the inline userdata payload shape.
- [ ] Refactor launch logic so Lambda can emit structured progress events instead of relying on raw `print` output.
- [ ] Preserve shared business logic; do not fork a second launch implementation just for the Lambda path.
- [ ] Keep local preprocessing in the CLI for:
  - [ ] reading `--userdata-file`
  - [ ] embedding file contents into the request payload
  - [ ] rejecting oversized request bodies before transmission if needed
- [ ] Add the remote `launch` action to the Lambda router.
- [ ] Expand CLI Lambda IAM only for launch-related operations.
- [ ] Migrate only `launch` in the CLI to the remote path.
- [ ] Preserve current flags for DNS behavior and ensure the request contract carries the same semantics.
- [ ] Add tests for:
  - [ ] payload construction for all launch options
  - [ ] userdata inlining
  - [ ] progress-event rendering
  - [ ] launch success and failure mapping
  - [ ] DNS option propagation
- [ ] Run `pixi run -e dev python -m pytest` for touched tests.
- [ ] Run `tofu fmt` and `tofu validate`.
- [ ] Record the local end-to-end launch validation steps and outcome in this file.

## Phase 4: `new`

Milestone: `devbox new ...` works end-to-end through the deployed CLI Lambda and is no longer dependent on a missing local implementation.

- [ ] Implement or restore the shared project-creation core that `new` needs.
- [ ] Decide the minimal shared interface for project creation so both CLI and Lambda paths use the same logic.
- [ ] Extend the wire contract for `new`.
- [ ] Add the remote `new` action to the Lambda router.
- [ ] Expand CLI Lambda IAM only for project-creation operations.
- [ ] Migrate only `new` in the CLI to the remote path.
- [ ] Add tests for:
  - [ ] project creation success
  - [ ] duplicate project behavior
  - [ ] invalid AMI behavior
  - [ ] invalid project name behavior
  - [ ] error propagation through the remote path
- [ ] Run `pixi run -e dev python -m pytest` for touched tests.
- [ ] Run `tofu fmt` and `tofu validate`.
- [ ] Record the local end-to-end `new` validation steps and outcome in this file.

## Phase 5: `delete-project`

Milestone: `devbox delete-project ...` completes the full confirmation and deletion flow through the deployed CLI Lambda.

- [ ] Extend the wire contract for a two-step delete flow:
  - [ ] `delete_project_preflight`
  - [ ] `delete_project_execute`
- [ ] Keep local confirmation prompts in the CLI.
- [ ] Perform authoritative safety checks in Lambda during preflight.
- [ ] Re-check destructive safety conditions again during execute.
- [ ] Add the remote delete actions to the Lambda router.
- [ ] Expand CLI Lambda IAM only for project deletion and AMI/snapshot cleanup operations.
- [ ] Migrate only `delete-project` in the CLI to the remote path.
- [ ] Preserve current user-facing confirmation semantics as closely as practical.
- [ ] Add tests for:
  - [ ] project not found
  - [ ] project in use
  - [ ] prompt cancellation after preflight
  - [ ] AMI cleanup accepted
  - [ ] AMI cleanup declined
  - [ ] execute-time race or safety failure
  - [ ] remote error propagation
- [ ] Run `pixi run -e dev python -m pytest` for touched tests.
- [ ] Run `tofu fmt` and `tofu validate`.
- [ ] Record the local end-to-end delete validation steps and outcome in this file.

## Phase 6: Full Cutover and Cleanup

Milestone: the primary CLI surface is fully Lambda-backed, and obsolete direct-AWS command paths are removed.

- [ ] Remove dead local command implementations that are no longer needed in the CLI.
- [ ] Consolidate shared helper code and eliminate temporary compatibility scaffolding.
- [ ] Review IAM for least privilege after all commands are migrated.
- [ ] Update README and any command documentation to describe the Lambda-backed CLI architecture.
- [ ] Update tests to remove now-obsolete local-path assumptions.
- [ ] Confirm the final acceptance matrix for:
  - [ ] `status`
  - [ ] `terminate`
  - [ ] `launch`
  - [ ] `new`
  - [ ] `delete-project`
- [ ] Run the relevant full test suite with `pixi run -e dev python -m pytest`.
- [ ] Run `tofu fmt` and `tofu validate`.
- [ ] Record final validation notes, remaining risks, and any explicitly deferred follow-up work in this file.

## Handoff Expectations for Every PR

- [ ] Update this file's phase checklist status.
- [ ] Add any newly locked decisions and why they were chosen.
- [ ] Add any contract changes made in the PR.
- [ ] Add the exact manual validation commands that were run and their results.
- [ ] Add any known gaps, risks, or follow-up items for the next session.
- [ ] Leave a short "next session starts here" note naming the next unchecked work item.

## Assumptions

- Each phase is intended to be small enough to fit in a single PR.
- Local validation means the real CLI running from a developer machine against deployed AWS infrastructure for the migrated command.
- Unmigrated commands remain local until their dedicated phase lands.
- Streaming remains in scope throughout the migration; if Lambda Web Adapter proves unworkable in practice, this file must be updated before implementation continues.
