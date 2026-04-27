# FUTURE PROJECT — OC4 EDC Build Trainer + RAG Retrieval Layer

**Created:** 24 April 2026
**Status:** PLANNING ONLY — Not started. Awaiting account move.
**Intended home:** Anthropic Enterprise account (currently on personal)
**Timeline:** RAG in 1-3 months, internal Trainer in ~6 months, productized Trainer in ~12 months

---

## 1. Vision

Build two related capabilities that leverage OpenClinica's corpus of historical
protocol-to-form mappings and turn it into both an internal accuracy multiplier
and a sellable customer-facing product.

### 1a. RAG retrieval layer (internal use, Phase 1)

At pipeline runtime, when a new protocol arrives, retrieve the 5-10 most
similar historical protocol-form pairs from an indexed corpus and inject them
into the `EDC_STRUCTURE_PROMPT` as few-shot examples. This shapes Claude's
form-building output to match OpenClinica's team conventions learned from
hundreds of past builds.

No model fine-tuning. No weight changes. Just retrieval + few-shot learning.

### 1b. OC4 EDC Build Trainer (productized, Phase 2-3)

A standalone tool customers run against THEIR historical protocol-form data.
Produces a personalized skill package that encodes their organization's style
preferences. Customer pipeline runs use their skill to generate forms that
match their in-house conventions without manual prompt engineering.

**Commercial shape:** ARO / CRO / small biotech as launch customers. Large
pharma is a stretch goal. OpenClinica likely offers initial-training as a
professional services engagement, then subscription-priced ongoing use.

---

## 2. Scoping answers (from 24 April 2026 conversation)

| # | Question | Answer |
|---|---|---|
| 1 | Where is historical data? | Protocols on clinicaltrials.gov. Forms/study designs in OpenClinica instances (exportable as XLSX or ODM XML) |
| 2 | Format of protocol-form pairs? | Protocol: PDF. Forms: XLSX or ODM XML or PDF. XLSX is easiest for comparison |
| 3 | Corpus size? | 100-200 pairs available for initial indexing |
| 4 | Therapeutic area? | Mixed TAs (harder retrieval, better coverage) |
| 5 | Target customer? | ARO, CRO, small biotech at launch. Large sponsors aspirational |
| 6 | Hosted or on-premise? | Both — needs flexibility |
| 7 | Pricing model? | Subscription for 80%+ of customers; one-time fees for ~20% |
| 8 | Deployment target? | Cloud-deployed |
| 9 | Embedding model (local vs API)? | TBD — needs more learning + engineering team input |
| 10 | Vector DB? | TBD — needs more learning + engineering team input |
| 11 | Sequencing? | Agreed: Phase 1 RAG internal → Phase 2 Trainer internal → Phase 3 Trainer productized |
| 12 | Timeline? | Phase 1: 1-3 months. Phase 2: ~6 months. Phase 3: ~12 months |

---

## 3. Architecture (draft — to be confirmed on enterprise account)

### 3.1 Phase 1: Internal RAG

```
oc-ai-pipeline/
  retrieval/                      NEW
    SKILL.md                      (reference doc for pipeline)
    build_corpus.py               (one-time + incremental indexing)
    retrieve.py                   (runtime retrieval)
    embed.py                      (embedding model wrapper)
    vector_store.py               (DB abstraction layer)
    corpus/                       (indexed data — gitignored, stored separately)
      metadata.json
      embeddings.db
  pipeline.py                     (modified to call retrieval)
  prompts.py                      (EDC_STRUCTURE_PROMPT gets {{retrieved_examples}} slot)
```

**Flow at runtime:**
1. Pipeline starts as usual → extracts study_meta from protocol
2. Retrieval queries: uses therapeutic_area, phase, indication, form list to find 5-10 similar past protocols
3. For each retrieved match, pulls the corresponding forms (trimmed to relevant sections)
4. Injects into EDC_STRUCTURE_PROMPT as few-shot examples before Claude generates
5. Everything else proceeds as today

**Ingest flow (one-time + incremental):**
- Scan OpenClinica instance via API, fetch study metadata + exported forms (XLSX or ODM)
- Fetch matching protocol from clinicaltrials.gov (API) or local files
- Parse both, create embedding vectors from protocol study_meta + form structural summary
- Store in vector DB with metadata

### 3.2 Phase 2: Internal Trainer

```
oc-trainer/                       NEW separate package
  __init__.py
  cli.py                          (command-line interface)
  scripts/
    build_corpus.py               (shared with Phase 1)
    review_diffs.py               (interactive feedback loop)
    diff_forms.py                 (XLSForm structural diff)
    pattern_extractor.py          (extracts learnable patterns from Q&A)
    knowledge_store.py            (YAML knowledge file read/write)
  knowledge/
    patterns.yaml                 (accumulated learnings)
```

**Flow:**
- Run `oc-trainer review --protocol <id>` — runs edc-builder, diffs against ground truth
- Interactive loop: Claude asks WHY for each substantive diff, user answers
- Pattern extractor structures the answer into a reusable rule
- Rules get stored in knowledge/patterns.yaml
- Rules get injected into edc-builder prompts (scoped by tags like therapeutic_area)

### 3.3 Phase 3: Productized Trainer

Customer receives `oc-trainer` as either:
- **Option A (on-prem):** Python package installed in their environment
- **Option B (cloud):** Web app they upload corpora to, run training, download exported skill

Training produces a custom-named skill package:
```
acme-corp-edc-style.skill.zip
  SKILL.md                        ("Build XLSForms in Acme Corp style")
  references/
    acme_patterns.yaml            (learned from their corpus)
    acme_examples.md              (top exemplars from their build history)
  scripts/
    build_xlsforms.py             (generic builder)
    style_overrides.py            (customer-specific logic)
```

Customer loads their skill into their OC pipeline instance. Runs pipeline as
usual. Outputs match their preferred patterns.

---

## 4. Open technical questions (to resolve on enterprise account)

### 4.1 Embedding model

**Options to evaluate:**
- OpenAI text-embedding-3-large (API, best quality, data leaves premises) — not viable on-prem
- Cohere embed-v3 (API, good quality, data leaves premises) — not viable on-prem
- BAAI/bge-large-en-v1.5 (local, excellent quality) — ideal for flexibility
- Anthropic (does not currently offer embeddings API — may change)

**Recommendation to validate:** Start with BAAI/bge-large-en-v1.5. Runs on
customer premises or in cloud. No third-party data exposure. Good quality for
our domain. Swap to API later if cost/ops justify.

### 4.2 Vector database

**Options to evaluate:**
- SQLite + sqlite-vec (zero infra, single-file, great for <1M vectors)
- ChromaDB (embedded, lightweight, good Python integration)
- pgvector (Postgres extension, durable, familiar for most ops teams)
- Qdrant (purpose-built, open source, self-hostable, great performance)
- Pinecone (hosted SaaS, best DX, per-vector cost)

**Recommendation to validate:** Start with SQLite + sqlite-vec for simplicity.
100-200 protocols × a few embeddings each = ~1-10K vectors. Overkill to run a
dedicated vector DB for that. Upgrade to Qdrant or pgvector if scale grows or
customers need multi-tenant isolation.

### 4.3 Protocol retrieval strategy

**Questions for engineering discussion:**
- Index at protocol-level, form-level, or both?
- What's the embedding input? Full protocol text? Study metadata summary?
  Form structural summary? All three?
- How do we handle form-to-form similarity WITHIN a single protocol (e.g.
  "this AE form is similar to the AE form in PRTK05")?
- Is retrieval pre-build (influence EDC_STRUCTURE_PROMPT) or post-build
  (refine Claude's draft output)?

### 4.4 Corpus sourcing

- clinicaltrials.gov API rate limits and licensing terms
- OpenClinica instance API access for each historical study
- Data cleanup: some historical forms may not be "gold standard"
- Do we curate the corpus (human review of each entry) or ingest everything
  and let retrieval surface the best matches?

---

## 5. Sequencing and milestones

### Phase 1: Internal RAG (target: 1-3 months after enterprise-account start)

- **Week 1-2:** Finalize embedding model + vector DB choice with engineering team
- **Week 2-4:** Build corpus ingest pipeline (protocols + forms → vectors)
- **Week 4-6:** Index initial 100-200 pairs
- **Week 6-8:** Add retrieval layer to pipeline.py, measure pre/post quality
- **Week 8-12:** Tune retrieval (k value, metadata filters, re-ranking) based on
  real pipeline outputs

**Success criterion:** EDC builds on 10 new test protocols produce measurably
better Study Specs than today's baseline (quantified via diff-count reduction
vs. team-reviewed ground truth).

### Phase 2: Internal Trainer (target: months 3-6)

- Re-use corpus infrastructure from Phase 1
- Add interactive review loop, knowledge storage, pattern extraction
- Goal: team uses trainer to add new patterns as they identify them in
  real-world builds; pipeline quality compounds over time

### Phase 3: Productized Trainer (target: months 6-12)

- Package trainer as installable Python tool
- Build exported-skill format spec
- Pick pilot customer (1 CRO or ARO with 20-50 historical builds)
- Charge professional services fee for initial training engagement
- Iterate on pricing model, UX, packaging based on pilot feedback

---

## 6. Decisions needed from Dan + OpenClinica team before code starts

1. **Engineering team review** of embedding model and vector DB recommendations
2. **Data access:** who on the team can export 100-200 historical studies from
   OC instances? Is there an existing pipeline or does one need building?
3. **Corpus curation policy:** ingest everything vs. human-approved only
4. **Budget approval:** embedding model costs (local hardware or API budget),
   vector DB hosting if cloud
5. **Pricing model for productized trainer:** pro services + subscription tiers
6. **Success metrics for Phase 1:** what quantitative measure proves RAG works

---

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Historical forms not "gold standard" | Medium | High | Curate corpus; tag confidence levels per entry |
| Protocol-form matches are noisy across TAs | Medium | Medium | Add metadata filters to retrieval (TA, phase) |
| Customer on-prem deploy complexity | High | Medium | Design for cloud first, factor for on-prem in Phase 3 |
| Embedding model cost at scale | Low | Medium | Local models eliminate ongoing cost |
| Customers unwilling to share historical data | Medium | Medium | On-prem trainer option addresses this |
| Fine-tuning better than RAG for this case | Low | Low | Architecture allows swap later |

---

## 8. Notes from the 24 April 2026 conversation

- Conversation started with user asking about feedback-loop approach
  (Option 1: all-in-context, Option 2: structured diff + Claude, Option 3:
  fine-tuning/RAG).
- Option 2 recommended for near-term but deferred in favor of jumping to 3b.
- Skill architecture question raised: "should this be a skill like edc-builder?"
  Answer: the generated per-customer package IS a skill, but the training tool
  itself is a standalone CLI/tool, not a skill.
- User wants to move to Anthropic Enterprise account before building this.
- User explicitly requested: "please don't start to build this now."
- Timeline preference: 1-3 months for RAG is ideal, 6 months for trainer is
  fine, 12 months for productized trainer is acceptable.

---

## 9. When to resume

**Trigger conditions to pick this up:**
- User's work has moved to the Anthropic Enterprise account
- User has scheduled time with engineering team to discuss embedding model and
  vector DB choices
- Initial corpus of 100-200 protocol-form pairs has been identified and
  extraction path from OC instances has been scoped

**First action when resuming:**
Open this document. Re-read the scoping answers. Confirm nothing has shifted.
Then schedule a planning session with engineering to close out the open
questions in §4. Only then start Phase 1 Week 1-2 tasks.
