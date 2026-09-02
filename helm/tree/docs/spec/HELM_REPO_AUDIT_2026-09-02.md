# HELM repo audit — 02.09.2026

Repo: `compas-psy/compas-ops`

Audit target: `claude/ai-agents-server-deployment-xdp77a`

Observed HEAD: `c300fb205b60e17d71a7e7524f6ed55fd7752d27`

Branch state at audit: **271 commits ahead / 32 behind `main`**, branch unprotected.

## Executive conclusion

The project is not lost. A substantial amount of solid infrastructure exists: Control Plane, deployments, RLS/tenancy,
safe ZIP ingest, local embeddings, GigaAM, local rephrase, Micro-Memory, original-file lifecycle, etc.

The principal failure is narrower but fundamental: the Knowledge subsystem has been implemented as a good **L1 RAG pipeline**
plus an experimental L2 atomizer, while the product requires L2 semantic memory to be a first-class mandatory layer.

The correct rescue is **not to rewrite HELM**. Preserve L0/L1 and replace/fix the semantic-v1 layer before full backfill.

## Critical findings

### F1 — Atomizer sees only the first 4,000 characters of each source

`helm_core/knowledge/atomizer.py`:

```text
MAX_INPUT_CHARS = 4000
prompt = text[:MAX_INPUT_CHARS]
```

One call is made per whole SOURCE. Any semantic facts after the first 4k characters are invisible to L2.

Severity: BLOCKER.

### F2 — Hard cap of 20 atoms per whole source

`MAX_ATOMS_PER_CALL = 20` and only one whole-source call. Long documents silently lose semantic units.

Severity: BLOCKER.

### F3 — Model output only has untyped `links`

Current structure:

```text
slug / type / text / links
```

Then `relations.py` converts Wikilinks to `relation_type=relates_to`.

This cannot faithfully represent:

```text
visit --INVOLVES(role=doctor)--> person
person --HAS_ROLE--> specialty
meeting --ABOUT--> project
fact --REASON_FOR--> decision
```

Severity: BLOCKER for structured queries.

### F4 — Machine-generated links are semantically mislabeled as explicit links

Atomizer generates Markdown/Wikilinks, then generic relation parser stores `evidence_type=explicit_link`.
That label was originally designed for owner-written Wikilinks. Machine extraction must be `EXTRACTED`.

Severity: HIGH (provenance/trust semantics).

### F5 — Same slug across sources appends prose into one growing file

`store_notes()` unique `(knowledge_user_id, slug)` and `_write_note_file(... is_new=False)` appends body text.

This conflates:
- canonical entity identity;
- source-scoped facts/events;
- same-name people/entities.

Statement-level provenance is lost inside the Markdown body.

Severity: BLOCKER.

### F6 — Entity resolution is exact slug only

The atomizer docstring itself says `Иванов` and `врач Иванов А.С.` become two notes, while same exact slug can be merged even if namesakes are different.

Severity: HIGH.

### F7 — Date was removed as node but no structured event time replaces it

Current atomizer types drop DATE as a node, which is reasonable only if EVENT/FACT has structured occurred_at/validity fields.
It does not. Therefore `в этом году` / `в августе` cannot be reliably executed as graph filters.

Severity: BLOCKER for temporal aggregation.

### F8 — Probe does not query semantic notes/relations at all

`probe.py` currently routes Micro-Memory, then lexical chunks, then vector chunks and optional rephrase.
No semantic node/edge query exists. ADR-019 also says Knowledge Router is not implemented yet.

Therefore the user's bad answer is expected from current architecture.

Severity: BLOCKER.

### F9 — FTS ranking fixes are treating a symptom

The repo contains multiple scripts and comments around `врачи`, chunk length and `ts_rank` normalization.
These may improve free-text retrieval, but cannot solve an aggregate relation question correctly.

Severity: architectural drift.

### F10 — Semantic fail-open is invisible to the user

`atomize_or_empty()` returns `[]` on failure; `worker.process_job()` later sets ingest job DONE.
The document can therefore be announced as parsed even when L2 semantic memory is absent.

Severity: HIGH.

### F11 — 596-style unit-test counts do not prove semantic quality

`test_knowledge_atomizer.py` mostly mocks `atomize_or_empty()` and verifies storage/parsing mechanics.
The latest live failure was precisely model-output quality/shape — a class mocked tests do not prove.

Severity: HIGH process flaw.

### F12 — Atomizer model choice is not a semantic extraction benchmark

`gemma2:2b` was selected earlier because it produced a reasonable Russian **style rephrase**. The atomizer reuses it as a temporary RAM convenience.
Semantic extraction requires a separate benchmark.

Severity: HIGH.

### F13 — KnowledgeGraphify does not exist yet

The repository has `tools/graphify.py` and `graph/ops/*` for **repo/code navigation**.
ADR-019 correctly admits the per-user Knowledge Graphify layer is not implemented.

This naming collision caused repeated misunderstanding.

Severity: HIGH product gap; LOW risk if names are separated.

### F14 — Health legacy data are still physically public

`scripts/atomizer-dryrun-health-check.sh` states that all **90 health sources' chunk text** are in `public.knowledge_chunks`,
loaded before health isolation, and the existing migration moved only original filenames.

This must be repaired before health semantic backfill.

Severity: CRITICAL privacy debt.

### F15 — Health L2 Markdown path would currently be common filesystem space

`atomizer._note_file_path()` uses generic `vault_root/<type>/<slug>.md`.
Health DB writes route to `health.*`, but `_write_note_file()` still writes plaintext Markdown through the generic filesystem path.
A private DB schema alone is therefore insufficient for semantic notes.

Severity: CRITICAL before live atomization/backfill.

### F16 — Documentation is stale/conflicting

Examples:
- `docs/KNOWLEDGE_RETRIEVAL.md` still describes retrieval as lexical-only while current `probe.py` has dense vectors/rephrase;
- `docs/KNOWLEDGE.md` carries older status lines;
- full authoritative HELM spec is absent from repo;
- ADR-019 is evolving while implementation is already moving.

This is a direct cause of agent confusion.

Severity: HIGH governance flaw.

### F17 — Working branch is very far from main

At audit the implementation branch is 271 commits ahead and 32 behind `main`, unprotected.
Deploy workflow can intentionally deploy a supplied `code_ref`, so production can be coherent even while Git history is divergent,
but this increases the chance of docs/code/status disagreement.

Do not rebase during emergency semantic rescue; reconcile after a stable semantic checkpoint.

## What is worth keeping

Do NOT rewrite these just because Knowledge semantic layer is broken:

- Control Plane and action policy architecture
- deploy workflow / live checks
- ZIP safe batch + durable jobs
- per-tenant SHA dedup
- GigaAM pipeline
- MarkItDown/Docling
- local embedding service + pgvector
- Micro-Memory
- Knowledge users/RLS/quotas
- original file return
- health public-envelope/private-payload concept
- local rephrase style path

## Rescue direction

Replace only the missing/broken middle:

```text
L1 SOURCE/chunks
      ↓
FULL semantic windowing
      ↓
canonical nodes + mentions + typed edges + dates/provenance
      ↓
Markdown micro-notes + stable Wikilinks
      ↓
KnowledgeGraphify
      ↓
structured query router
```

The detailed normative plan is in `HELM_FINAL_v4.0_RESCUE_2026-09-02.md`.


## Governance rescue

The codebase contains enough stale/conflicting narrative that another agent can rationally make the wrong choice while
still citing a repo document. The v4 rescue therefore requires:
- full spec committed under `docs/spec`;
- one `CURRENT.md`;
- ADR-019 revised/superseded;
- Knowledge docs regenerated from v4 + actual implementation state;
- roadmap treated as execution journal, not product authority.
