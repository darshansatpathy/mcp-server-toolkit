# 4. The tool contract is a committed file, checked in CI

Status: accepted

## Context

Tool names, input schemas, output schemas, scopes and annotations are an API. Clients and prompts depend on them, and they change silently when a type hint or a docstring changes.

## Decision

`mcpkit contract` writes every tool's full definition, its scopes, and the resources and prompts to `schemas/contract.json`. CI runs `mcpkit contract --check`, which fails when the server and the file differ. A change to the contract is therefore a reviewed diff in the pull request.

## Consequences

Docstring edits show up as contract changes. That is intended: descriptions are what the model reads.
