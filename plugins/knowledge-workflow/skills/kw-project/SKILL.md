---
name: kw-project
description: Read or establish generic project boundaries and the logical private-knowledge binding. Use when onboarding a repository or adapting the workflow to another project.
---

# Project adaptation

The nearest AGENTS.md owns project instructions. The optional
.knowledge-workflow/project.json describes editable and read-only directories,
operations requiring review, verification commands, and the logical knowledge_ref.
Private path bindings live in the user's local registry and must not be committed.

Use the installer-provided absolute command or local kw launcher for project-init.
Start with its dry-run, choose generic, python or embedded from real project facts,
then apply within the user's onboarding authorization. Do not initialize Git,
stage files, run builds, or invoke device tools as a side effect of onboarding.

Generic projects do not need chip families, SDK layouts or hardware fields.
Embedded templates supply examples of boundaries, not facts about a connected board.
Read the actual project before using verification commands; they are declarations,
not permission to execute dangerous build or post-build actions.

Retain surrounding AGENTS content and all user modifications. A modified managed
block or profile is a conflict to inspect, not a reason to overwrite it.
Without a profile, read-only investigation can continue; do not invent hardware
operations or treat unspecified directories as automatically safe to modify.
