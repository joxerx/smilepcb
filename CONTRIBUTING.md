# Contributing to SmilePCB

Small fixes, reproducible bug reports, tests, documentation and well-sourced
keyboard recipes are welcome. This repository contains curated source snapshots
for releases; development notes and generated outputs are outside its scope.
Open pull requests against `main`. A small, focused change does not require a
separate approval or an assigned issue.

## Report a bug

Search existing issues first, then use the Bug report form. Include:

- SmilePCB version or commit, OS/architecture, Python and KiCad versions.
- The command, fixture/profile, selected tool versions and relevant exit code.
- Expected behavior and actual behavior, including the first failing stage.
- A minimal synthetic or redistributable reproduction with license/provenance.
- Short sanitized logs; preserve reported ERC/DRC/parity/connectivity findings.

Never attach credentials, home-directory paths, customer material or a design
you cannot redistribute. Make a synthetic reproduction instead. Report
security-sensitive issues using [SECURITY.md](SECURITY.md).

A failed placement is not proof that a layout is impossible. Zero native
findings do not establish tested hardware. Keep these distinctions in reports.

## Propose a feature or recipe

Use Feature request for a user-visible capability. For a circuit, footprint,
placement or routing rule, use Recipe proposal. Link the exact public source
revision, datasheet and license; distinguish an assembled community design
from an untested suggestion. Include the mechanical/electrical constraints,
failure cases and how success can be checked.

For a broad interface change, new dependency, controller family or behavior
outside the current support matrix, open an issue first to agree on scope.
Please avoid bundling unrelated changes.

## Work on the code

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q tests/test_cold_fixture.py \
  tests/test_keyboard_process.py tests/test_seed_junctions.py \
  tests/test_keyboard_lookahead_policy.py \
  tests/test_staged_generated_pipeline.py tests/test_keyboard_phases.py
```

Without KiCad, 69 tests run and one symbol-library integration check is
skipped. Native integration needs the explicit toolchain and public sources
described in the
[STM32 guide](docs/workflows/stm32.md). Do not replace a required native
check with a mock and call the board verified.

Keep host code compatible with Python 3.11+. Native workers must also parse and
run in the tested KiCad-bundled Python 3.9 environment. Keep worker deadlines,
resource limits, source preservation and fail-closed acceptance intact.

Add a regression test for a behavior fix and tests for a new capability.
Documentation-only changes need relevant example/link checks. Record what you
actually ran and what requires native dependencies; do not claim unrun checks.

## Submit a pull request

Use the PR template. Explain the problem, resulting behavior and validation.
Reference the issue with `Fixes #123` when the PR resolves it. For a partial
change, use `Refs #123`. Use a short imperative title, optionally prefixed with
a scope such as `routing:`, `mcp:`, `docs:` or `tests:`.

For CAD/firmware behavior, include the affected fixture, rule/profile changes,
native findings, source-preservation result and matching firmware outcome.
Explain remaining warnings or unsupported cases explicitly. Screenshots help
with geometry; they do not replace native reports.

Check your diff for generated files, binaries, machine paths and secrets.
Keep output directories under ignored `.lab/`. Preserve upstream notices.
No CLA or commit signing is required. By contributing, you agree to license
your original contribution under the project's MIT license; separately
licensed third-party material keeps its own terms.

Follow the [code of conduct](CODE_OF_CONDUCT.md). Be respectful, specific and
constructive; discuss the change rather than attacking the person.
