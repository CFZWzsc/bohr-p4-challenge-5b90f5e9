# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-5b90f5e9` (track: `literature-search`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable entry point and all supporting scripts. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `systematic-screening-audit`
- Summary: Use when a systematic or scoping review task supplies records plus explicit inclusion/exclusion criteria and needs title-abstract screening with per-record reason codes and verbatim snippets, duplicate/version handling, dual-reviewer agreement and conflict records, and a count-closed audit chain in a contract-preserving result.json.

## Contents

| File |
|------|
| `SKILL.md` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

The entry point is `SKILL.md`; input/output contracts, error handling and a minimal
usage example are documented there.

## Notes

- Pure Python standard library; no network access required.
- The result JSON keeps the input case id, the main results, per-item evidence,
  a run status and an uncertainty section.
