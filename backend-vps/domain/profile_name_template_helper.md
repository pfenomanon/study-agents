# Profile Template Helper

Purpose:
- Guide an agent to build `domain/profiles/<profile_name>.json` from research evidence.
- Keep profile creation consistent, auditable, and domain-accurate.
- Prevent low-quality or unsupported field population.

Core principles:
- Evidence-first: every domain-specific inclusion should be supported by researched sources.
- Authority-weighted: prefer primary/official sources over blogs/opinion pages.
- Operational utility: profile fields should improve extraction, reasoning, and workflow outputs.
- Safety: avoid unsupported claims, broad legal/medical advice framing, and overconfident language.

## Required JSON schema

```json
{
  "schema_version": 1,
  "profile_name": "string",
  "domain_name": "string",
  "assistant_role": "string",
  "domain_expertise": ["string", "..."],
  "entity_types": ["string", "..."],
  "relationship_types": ["string", "..."],
  "relationship_priorities": ["string", "..."],
  "topic_priorities": ["string", "..."],
  "vision_focus_areas": ["string", "..."],
  "examples": ["string", "..."],
  "forbidden_terms": ["string", "..."],
  "allow_legacy_terms": false
}
```

Validation rules:
- `schema_version`: integer.
- `profile_name`, `domain_name`, `assistant_role`: non-empty strings.
- All list fields must be arrays of strings.
- Non-empty required lists:
  - `domain_expertise`
  - `entity_types`
  - `relationship_types`
  - `relationship_priorities`
  - `topic_priorities`
  - `vision_focus_areas`
  - `examples`
- `forbidden_terms` may be empty.
- `entity_types` and `relationship_types` should avoid duplicates.
- Do not use the phrase `exam helper`; use `subject-matter-expert`.

## Field-by-field guidance

`domain_name`
- Human-readable domain scope.
- Include region/jurisdiction only if the domain depends on it.
- Keep concise (roughly 3-12 words).

`assistant_role`
- Should describe practitioner-facing expertise.
- Must align with domain and expected workflows.
- Should avoid legal claims of licensure unless explicit and necessary.

`domain_expertise`
- 4-8 lines.
- Include: terminology boundaries, decision criteria, workflow logic, risk/compliance context.
- Must be practical for context-grounded reasoning.

`entity_types`
- Include object classes that appear in documents/processes for this domain.
- Keep stable category names (not sentence-like values).
- Ensure coverage for: documents, requirements, roles, processes, risks, timelines.

`relationship_types`
- Include reusable edge verbs for graph reasoning.
- Keep concise (`requires`, `defines`, `part_of`, etc.).
- Avoid overly domain-locked verbs unless necessary.

`relationship_priorities`
- Describe which relationships matter most for retrieval and decision quality.
- Focus on prerequisites, authority, dependencies, conflicts, and exceptions.

`topic_priorities`
- High-value topics users will query most often.
- Include constraints, responsibilities, sequencing, and edge cases.

`vision_focus_areas`
- How screenshot QA should prioritize interpretation.
- Include correctness, actionable guidance, and limits/assumptions.

`examples`
- 5+ domain examples that anchor language and extraction patterns.
- Should be concrete and representative.

`forbidden_terms`
- Terms that should be avoided unless intentionally needed.
- Use for removing unwanted legacy/domain drift.

`allow_legacy_terms`
- `false` for new domains unless migration compatibility is required.

## Research quality gate

Minimum recommended before finalizing profile:
- At least 6 usable sources.
- At least 3 authoritative sources (for example: `.gov`, `.edu`, official standards bodies, official product docs).
- At least 4 unique domains.
- Evidence snippets covering:
  - terminology
  - process/workflow
  - requirements/constraints
  - risks/exceptions

If these are not met:
- Run additional research passes.
- Expand query terms with regulation, glossary, standards, workflow, and best practices.

## Evidence mapping requirement

For each major field, maintain references to source IDs used:
- `domain_expertise`
- `entity_types`
- `relationship_types`
- `relationship_priorities`
- `topic_priorities`
- `vision_focus_areas`
- `examples`

Store this mapping in a report file alongside the generated profile.
