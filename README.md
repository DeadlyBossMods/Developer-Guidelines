# DBM Developer Guidelines

Shared development and AI-agent guidance for DeadlyBossMods projects: code style,
efficient feature lifecycles, LuaLS validation, secret-safe APIs, localization,
DB2 scaffolding, timeline routing, and screenshot-log analysis.

## What is included

- [Workspace entry-point template](bundle/.github/copilot-instructions.md.template).
- [Nine topic templates](bundle/.github/instructions/), loaded through the entry
  point's task-specific links rather than putting every rule in every request.
- [Canonical three-pass OCR pipeline](<bundle/Agent Tools/ocr_logs.py>).

The bundle preserves the existing DBM rules, with portability edits for workspace
layout, personal configuration, and historical provenance. Personal instructions,
private memories/session plans, screenshots, caches, and encounter replay runners
are not included. Recorded test results remain historical, not newly verified
claims. This repository is not an addon and does not belong in `Interface/AddOns`.

## Why templates?

Instruction files end in `.template` here, not `.instructions.md`, and the entry
point is not named `.github/copilot-instructions.md` in this checkout. Merely
adding this repository to a workspace therefore does not provide a second set of
conventionally discovered instructions.

The Markdown links inside templates intentionally target their **installed**
locations. They are not links to the template files in this repository.
Agents can still read templates through an explicit request or ordinary search;
distribution content is not an additional active policy for an existing workspace.
Do not configure custom instruction discovery to include the bundle.

## Recommended workspace layout

Install into the **parent development workspace**, outside individual addon
repositories:

```text
DBM Workspace/
  .github/
    copilot-instructions.md
    instructions/
      dbm-development.instructions.md
      ...eight other topic files...
  Agent Tools/
    ocr_logs.py
  DBM-Retail/            # checkout of DeadlyBossMods/DeadlyBossMods
  DBM-Dungeons/          # dungeon checkout, when needed
  Developer-Guidelines/  # this repository; distribution source only
```

Open the parent directory as the workspace when possible. With multi-root
workspaces or different agents, explicitly verify discovery; do not assume a
parent folder's instructions are loaded merely because its addon checkouts are
open. Other checkout names in examples, such as `DBM-Archaeology-GH`, are optional.
Map examples to the actual local checkout rather than cloning unrelated projects.

For a single-addon checkout, the rules still apply, but sibling-repository examples
must be resolved against the available files. Do not install another active copy
inside an addon that already inherits the parent workspace's guidance.

## Install

The full installation below targets DBM work. For other projects, use the
scope-aware setup request below to select and adapt relevant guidance instead
of copying the entire bundle.

1. Clone or download this repository into the development workspace.
2. Inspect existing workspace and user instructions. If guidance is already
   installed, follow **Updating** below instead of overwriting it.
3. Copy the contents of `bundle/` into the parent workspace, removing **only** the
   trailing `.template` from each instruction filename:
   - `bundle/.github/copilot-instructions.md.template` becomes
     `.github/copilot-instructions.md`.
   - `bundle/.github/instructions/*.instructions.md.template` become
     `.github/instructions/*.instructions.md`.
   - `bundle/Agent Tools/ocr_logs.py` becomes `Agent Tools/ocr_logs.py`, unchanged.
4. Preserve existing settings, personal instructions, and agent-specific loaders.
   Reconcile conflicting guidance with the developer rather than silently
   replacing it. Do not create a duplicate full-policy `AGENTS.md` or user-level
   copy; agents without Copilot instruction discovery can be directed to the
   installed entry point explicitly.
5. Verify discovery in a new agent conversation as described below.

No installer, background updater, or automatic overwrite is included.

### Copyable agent setup request

```text
Set up developer guidance from the local Developer-Guidelines repository.
Treat its bundle as installation templates, not a second active policy.

First identify the target project, development workspace, and existing
instructions and tooling. Determine whether this is DBM development or
another project; ask if the intended scope is unclear.

For DBM development, install the applicable DBM guidance and required tooling.

For other projects, selectively adapt only guidance relevant to that project.
Do not assume all WoW addons use DBM's architecture, APIs, helpers, testing
infrastructure, or development workflows. Exclude DBM-specific module,
encounter, timeline, and historical implementation instructions unless the
project actually uses them. Exclude screenshot OCR guidance and tooling unless
that workflow is explicitly wanted.

Retain applicable general principles, such as efficient feature lifecycles,
matching surrounding code, evidence-based API usage, secret-safe handling,
localization boundaries, and validation appropriate to the target project.
Adapt project-specific examples only when a verified equivalent exists;
otherwise omit them rather than inventing replacements.

Before making changes, summarize what you propose to retain, adapt, and omit,
and obtain approval for the scope.

Inspect existing instructions before installation. Compare and ask before
replacing or merging conflicting content; do not install duplicates.
Install only the approved templates, removing trailing .template suffixes.
Update the installed entry point and cross-references so they describe only
included guidance, with no dangling links or requirements for omitted tools.

Preserve personal configuration and project source code. Resolve paths against
the actual workspace layout. Verify the selected files, links, and required
tooling, then explain how to verify instruction discovery in my agent.

Do not modify the source distribution bundle, commit, push, or change agent
settings without approval.
```

## Verify instruction discovery

For a full DBM installation, start a new conversation in the development
workspace and ask:

```text
Without changing files, identify the active DBM workspace instruction entry point.
Read its linked guidance for localization and screenshot-log analysis.
Explain the display-versus-trigger translation rule and the required OCR command.
Report missing files or tools; do not read distribution templates as active policy.
```

Confirm that the agent reports the **installed** entry point and topics, the ban
on inventing translated client-text triggers, and the canonical three-pass OCR
command. Check the agent's instruction-loading diagnostics where available;
correct answers alone do not prove automatic discovery.

For an adapted installation, verify the installed entry point and selected
topics instead. Test a relevant retained rule and confirm that omitted DBM/OCR
workflows are not required by the installed guidance.

File-based guidance is not deterministic enforcement. This package includes no
lifecycle hooks, and does not claim identical discovery behavior across agents.

## OCR tooling

The included script uses Python **3.10 or newer**, Python's standard library, and
the external `tesseract` executable with English (`eng`) language data. There are
no pip dependencies. Install missing tools through the developer's normal package
manager; verify `python3 --version`, `tesseract --version`, and
`tesseract --list-langs` before a new OCR run.

From the parent workspace, target actual pull directories:

```sh
python3 "Agent Tools/ocr_logs.py" --folder "/path/to/pull1" "/path/to/pull2"
```

Keep `log1.txt`, `log2.txt`, `log3.txt`, `log.txt`, and `ocr_issues.txt` with each
pull. The targeted command preserves an existing canonical cache and refreshes
its audit. Review implicated images, repair the canonical cache from evidence,
then rerun the targeted command. Do not routinely use `--force`.

Use explicit paths when logs are outside the workspace. Untargeted modes search
the script's relative `12.0 Logs` and `12.1 Logs` roots. Do not upload screenshots
or caches containing personal account names to this repository.

Encounter regression runners and their private corpora are not included. Obtain
compatible tooling/evidence from maintainers, or reconstruct the documented
cases, and report missing coverage rather than claiming prior tests were rerun.

## Updating and contributing

- This repository is the shared distribution source. Installed workspace copies
  do not update automatically.
- Update the checkout with a normal fast-forward pull, then compare templates
  against installed files before applying changes. Remove the template suffix
  only in the installed copy; account for local additions and path mappings.
- Keep exactly one active entry point and one active set of topic files per
  development workspace. Never replace personal instructions or reviewed OCR
  caches during a guideline update.
- Propose shared changes here through review, updating the relevant existing
  topic and its entry-point link when needed. Keep encounter-specific findings
  scoped and distinguish historical results from fresh evidence.
- Do not edit another developer's active workspace merely to synchronize the
  distribution. Compare and obtain approval where policies conflict.
- A DBM Wiki page can link here for discovery; avoid duplicating the full rules
  into Wiki snippets that would need separate maintenance.

The initial bundle was prepared from the existing workspace guidance on
2026-11-02. Packaging and link checks do not certify live-client behavior or
rerun the historical encounter validations.
