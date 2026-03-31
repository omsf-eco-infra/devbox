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
- Packaging: add the CLI Lambda to the existing shared Lambda image rather than creating a separate image.
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

- [ ] Create the authoritative wire-contract section in this file for `status`, including request payload, result payload, and terminal event rules.
- [ ] Add runtime support for HTTP invocation in the CLI, using `requests` plus SigV4 signing helpers.
- [ ] Add a shared remote-invocation layer in the CLI for:
  - [ ] SSM Function URL lookup
  - [ ] request envelope creation
  - [ ] signed HTTP POST
  - [ ] NDJSON event parsing
  - [ ] terminal-event validation
  - [ ] error mapping to current CLI exit behavior
- [ ] Add a new CLI Lambda handler/router to the shared Lambda image.
- [ ] Add Lambda Web Adapter support to the image build.
- [ ] Add Terraform resources for the CLI Lambda, Function URL, IAM, log group, and SSM parameter publication.
- [ ] Implement the remote `status` action in Lambda by reusing the existing `DevBoxManager` inventory logic rather than duplicating AWS queries.
- [ ] Define and implement timestamp serialization in Lambda and timestamp rehydration in the CLI so existing console rendering still works.
- [ ] Keep `status` as the only migrated command in this phase; all other commands remain local.
- [ ] Add tests for:
  - [ ] Function URL lookup
  - [ ] signed request dispatch
  - [ ] streamed NDJSON parsing
  - [ ] successful `status` result rendering
  - [ ] malformed stream handling
  - [ ] HTTP/auth failure handling
  - [ ] missing SSM parameter handling
- [ ] Run `pixi run -e dev python -m pytest` for touched tests.
- [ ] Run `tofu fmt` and `tofu validate` for touched Terraform.
- [ ] Record the exact local end-to-end validation command and observed outcome in this file.

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
