# Resume-Derived User Preferences Knowledge Graph Plan

## Objective
Replace the current manual-only user preference capture with an automated, resume-informed preference graph that improves application matching quality while preserving explicit user control.

## Product Outcomes
- Increase match precision (top-N relevance) by combining inferred preferences from resumes with manual overrides.
- Reduce onboarding friction by pre-filling preferences from uploaded resume content.
- Preserve trust and controllability by making inferred preferences explainable and editable.

## Core Design Decision: Preference Representation
Use a hybrid model:
1. Typed property graph for explicit preference structure, provenance, confidence, and constraints.
2. Normalized relational projections for transactional reads/writes and API compatibility.
3. Embeddings/vector index for semantic similarity over free-text resume signals.

Why this is the best quality/operability tradeoff:
- Graph edges encode nuanced relationships (required vs preferred, hard exclusions, conditional rules).
- Relational tables keep existing backend/API interactions efficient and migration-safe.
- Vectors capture latent semantic fit (equivalent role titles, adjacent skills, related domain context).

## Knowledge Graph Schema (Conceptual)

### Primary Nodes
- `User`
- `Resume`
- `PreferenceProfile` (versioned)
- `RolePreference` (titles, seniority)
- `SkillPreference` (skills, proficiency, recency)
- `DomainPreference` (industry/domain)
- `LocationPreference` (onsite/remote/hybrid, geo constraints)
- `CompensationPreference` (currency, base range, bonus/equity flags)
- `WorkAuthPreference` (visa sponsorship, citizenship constraints)
- `CompanyPreference` (size, stage, exclusions)
- `Application`
- `JobPosting`

### Key Relationships
- `User -[HAS_PROFILE]-> PreferenceProfile`
- `PreferenceProfile -[PREFERS {weight, confidence, source, updated_at}]-> PreferenceNode`
- `Resume -[EVIDENCES {confidence, extractor_version, span_ref}]-> PreferenceNode`
- `User -[OVERRIDES {priority}]-> PreferenceNode`
- `Application/JobPosting -[REQUIRES | OFFERS]-> Skill/Role/Location/...`
- `PreferenceNode -[CONFLICTS_WITH]-> PreferenceNode` (for hard filters)

### Required Edge Metadata
- `source`: `manual`, `resume_parse`, `behavioral`, `import`
- `confidence`: float `[0.0, 1.0]`
- `weight`: normalized preference importance
- `hard_constraint`: boolean
- `valid_from` / `valid_to`
- `version`

## Matching Strategy

### Stage 1: Candidate Filtering (Hard Constraints)
Eliminate jobs violating non-negotiables:
- Work authorization constraints.
- Required remote/onsite constraints.
- Explicit excluded locations/domains/companies.
- Salary floor only when marked hard by user confirmation.

### Stage 2: Graph-Based Scoring
- Compute weighted overlap between `PreferenceProfile` and `JobPosting` entities.
- Penalize conflicts (`CONFLICTS_WITH`, missing required skills).
- Reward stronger evidence paths:
  - `manual + resume evidence` > `manual only` > `resume only`.

### Stage 3: Semantic Re-Ranking
Use embedding similarity between:
- Resume summary + experience narrative.
- Job description snippets.
- Preferred role/skill narrative text.

Blend semantic + graph scores:
- `final = alpha(graph_score) + beta(semantic_score) + gamma(recency_engagement_score)`

### Explainability Output
Persist top recommendation reasons:
- Matched skills/roles/locations.
- Satisfied constraints.
- Inferred preferences used + confidence.
- Manual override effects.

## Inference Pipeline (Resume -> Preference Graph)
1. Resume ingestion: parse structured fields and free text sections.
2. Entity extraction + normalization: role titles, skills, industries, geos, compensation hints.
3. Preference hypothesis generation: infer likely preferences with confidence and rationale.
4. Profile synthesis: merge manual + inferred preferences into versioned `PreferenceProfile`.
5. User confirmation loop: UI accept/edit/reject for inferred preferences.
6. Feedback capture: track accept/reject actions and recalibrate edge weights.

## Data Model Implementation Plan (Migration-Safe)

### Phase A: Additive Storage
Add new backend schema entities/tables:
- `preference_profile`
- `preference_node`
- `preference_edge`
- `preference_evidence`
- `preference_feedback`

Notes:
- Use additive Alembic migrations only.
- Keep existing manual preference fields as fallback source of truth.

### Phase B: Dual-Write
- On preference updates, write legacy fields and graph projection.
- Introduce read-path feature flag: `use_preference_graph_matching`.
- Maintain existing GraphQL and BFF response shape (no contract change).

### Phase C: Gradual Read Cutover
- Shadow-score recommendations with legacy and graph-hybrid pipelines.
- Track and compare precision@K, CTR, apply-through rate, and complaint rate.

### Phase D: Full Adoption
- Promote graph matcher to default after quality and stability gates pass.
- Retain legacy fields for backward compatibility until explicit deprecation.

## Repository Mapping (Execution Ownership)
- `backend/`: preference schema (Alembic + ORM), profile synthesis, graph projection, explainability persistence.
- `cloud_automation/`: large-scale inference, embedding generation/index refresh, async rescoring workers.
- `frontend/`: inferred preference confirmation UI, edit/reject controls, recommendation explanations.
- `tests/`: migration safety tests, inference unit tests, matcher/regression tests, feature-flag cutover tests.

## Quality Framework

### Offline Evaluation
Build labeled relevance set from historical applications/outcomes.

Metrics:
- Precision@5 / @10
- NDCG@10
- Hard-constraint violation rate (target ~0)
- Coverage (users with viable recommendations)

### Online Evaluation
A/B test legacy vs graph-hybrid matcher.

Success KPIs:
- Recommendation click-through
- Application submission rate
- Positive recommendation quality feedback
- Time-to-first-application

### Guardrails
- Never auto-apply hard constraints inferred from resume without user confirmation.
- Cap influence of low-confidence inferred preferences.
- Provide one-click reset to manual-only mode.

## Milestones and Estimated Timeline
1. Discovery (1 week)
   - Finalize ontology, taxonomies, confidence calibration.
2. Schema + ingestion MVP (1-2 weeks)
   - Additive schema and resume inference pipeline.
3. Scoring MVP (1 week)
   - Graph scoring and explainability persistence.
4. Hybrid semantic reranker (1 week)
   - Embedding reranking and blend tuning.
5. UI confirmation + feedback loop (1-2 weeks)
   - Editable inferred preferences and acceptance tracking.
6. Shadow + A/B evaluation (2+ weeks)
   - Monitoring, threshold tuning, and rollout gates.

## Risks and Mitigations
- Inference errors: require user confirmation for hard constraints; enforce confidence thresholds.
- Ontology drift: schedule taxonomy audits and alias management.
- Cold start sparse resumes: fall back to manual preferences and collaborative priors.
- Performance: precompute profile vectors/edge aggregates and cache top candidates.

## Immediate Next Steps
- Approve ontology draft and edge metadata schema.
- Select storage approach: native graph DB vs relational graph tables + indexes.
- Define first offline evaluation dataset and baseline metrics.
- Implement feature-flagged MVP with dual-write and shadow scoring.
