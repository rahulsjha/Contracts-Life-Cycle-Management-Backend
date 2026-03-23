<div align="center">

# CLM Backend

[![CI](https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend/actions/workflows/ci.yml/badge.svg)](https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.0-green?logo=django&logoColor=white)](https://djangoproject.com)
[![DRF](https://img.shields.io/badge/DRF-3.14-red?logo=django&logoColor=white)](https://www.django-rest-framework.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-blue?logo=postgresql&logoColor=white)](https://supabase.com)
[![Celery](https://img.shields.io/badge/Celery-5.3-green?logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![Redis](https://img.shields.io/badge/Redis-5.0-red?logo=redis&logoColor=white)](https://redis.io)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey)](#)

**Enterprise-grade Contract Lifecycle Management API** — Django 5 + DRF + pgvector semantic search + Celery async tasks + OpenTelemetry observability + multi-tenant row-level isolation + AI-powered contract analysis via Gemini and VoyageAI.

---

### 🚀 Live Deployments

| Service | URL | Status |
|---------|-----|--------|
| **API (Production)** | `https://api.YOUR_DOMAIN.com` | [![API](https://img.shields.io/badge/API-live-brightgreen)](https://api.YOUR_DOMAIN.com/api/docs/) |
| **Swagger UI** | `https://api.YOUR_DOMAIN.com/api/docs/` | [![Docs](https://img.shields.io/badge/docs-swagger-85EA2D)](https://api.YOUR_DOMAIN.com/api/docs/) |
| **Prometheus Metrics** | `https://api.YOUR_DOMAIN.com/metrics` | [![Metrics](https://img.shields.io/badge/metrics-prometheus-E6522C)](https://api.YOUR_DOMAIN.com/metrics) |
| **Frontend App** | `https://verdant-douhua-1148be.netlify.app` | [![Frontend](https://img.shields.io/badge/frontend-netlify-00C7B7)](https://verdant-douhua-1148be.netlify.app) |

### 📦 Repositories

| Repo | Description | Stack |
|------|-------------|-------|
| **[Backend (this repo)](https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend)** | REST API, AI, async tasks, multi-tenancy | Python / Django |
| **[Frontend](https://github.com/vk93102/Contracts-Life-Cycle-Management-Frontend)** | Contract editor, PDF viewer, approval UI | Next.js 16 / React 19 |

### 🎬 Resources

[📹 **Live Demo Video (Loom)**](https://www.loom.com/share/694dee3f381545b2a17f2dc1831c5bd0) · [📖 **API Reference**](docs/BACKEND_API_DOCUMENTATION.md) · [🔬 **Feature Index**](docs/FEATURES_INDEX.md) · [🐳 **Docker Setup**](#docker-local-development)

</div>

---

## Table of contents

- [Overview](#overview)
- [System architecture](#system-architecture)
  - [High-level overview](#high-level-overview)
  - [Request lifecycle](#request-lifecycle)
  - [Async task pipeline](#async-task-pipeline)
  - [Database schema design](#database-schema-design)
  - [Multi-tenant isolation model](#multi-tenant-isolation-model)
  - [AI pipeline](#ai-pipeline)
- [Tech stack](#tech-stack)
- [Key features](#key-features)
- [Quick start](#quick-start)
- [Docker local development](#docker-local-development)
- [Environment variables](#environment-variables)
- [CI/CD pipeline](#cicd-pipeline)
- [Testing](#testing)
- [API reference](#api-reference)
- [Authentication flows](#authentication-flows)
- [Security model](#security-model)
- [Architecture decisions](#architecture-decisions)
- [Production deployment](#production-deployment)
- [Repository structure](#repository-structure)
- [Documentation index](#documentation-index)
- [Contributing](#contributing)

---

## Overview

A production-ready Django REST Framework backend powering the full contract lifecycle: creation, immutable versioning, AI-powered analysis, multi-stage approval workflows, digital signatures, semantic search, and a comprehensive audit trail — all with strict multi-tenant isolation.

**What separates this from a typical Django backend:**

| Signal | Detail |
|--------|--------|
| **pgvector + VoyageAI law-2** | Semantic contract search in the same Postgres transaction as relational data — no external vector DB, fully ACID |
| **OpenTelemetry from day one** | Distributed tracing + Prometheus metrics built in, not retrofitted |
| **Multi-tenant row-level isolation** | `tenant_id` injected at middleware; cross-tenant data leaks are structurally impossible |
| **Fail-open degradation** | Redis outages degrade performance but never break API responses |
| **10-layer security model** | Transport → auth → RBAC → rate limiting → CSRF → tenant isolation → input validation → PII → audit → dependency scanning |
| **15 Django apps** | Modular by domain: `authentication`, `contracts`, `ai`, `search`, `approvals`, `audit_logs`, `ocr`, `redaction`, `tenants`, and more |

---

## System architecture

### High-level overview

```
╔══════════════════════════════════════════════════════════════════════╗
║                         CLIENTS                                      ║
║   Next.js Frontend          Mobile          CLI / API consumers      ║
╚══════════════════════╤══════════════════════════════╤════════════════╝
                       │  HTTPS                       │  HTTPS
                       ▼                              ▼
╔══════════════════════════════════════════════════════════════════════╗
║                    EDGE / REVERSE PROXY                              ║
║              Cloudflare (SSL termination, DDoS, CDN)                ║
╚══════════════════════╤═══════════════════════════════════════════════╝
                       │
                       ▼
╔══════════════════════════════════════════════════════════════════════╗
║                  CLM BACKEND  (Django 5 + DRF 3.14)                 ║
║                                                                      ║
║  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────┐  ║
║  │  Gunicorn   │  │  Middleware  │  │  DRF Views  │  │  OpenAPI │  ║
║  │  (WSGI)     │  │  • Tenant    │  │  • Auth     │  │  Swagger │  ║
║  │  4 workers  │  │  • Audit     │  │  • Contracts│  │  ReDoc   │  ║
║  │             │  │  • Metrics   │  │  • AI       │  │          │  ║
║  └─────────────┘  │  • CORS      │  │  • Search   │  └──────────┘  ║
║                   │  • Security  │  │  • Approvals│                 ║
║                   └──────────────┘  └─────────────┘                 ║
╚══════════╤═══════════════════════════════╤════════════════╤══════════╝
           │                               │                │
           ▼                               ▼                ▼
╔════════════════╗              ╔═══════════════════╗  ╔════════════╗
║   PostgreSQL   ║              ║  Redis            ║  ║Cloudflare  ║
║   (Supabase)   ║              ║                   ║  ║    R2      ║
║                ║              ║  ┌─────────────┐  ║  ║            ║
║  • pgvector    ║              ║  │Celery broker│  ║  ║ • PDFs     ║
║  • pg_trgm     ║              ║  │& result     │  ║  ║ • Signed   ║
║  • pgcrypto    ║              ║  │backend      │  ║  ║   docs     ║
║                ║              ║  └─────────────┘  ║  ║ • Versions ║
║  tenant_id on  ║              ║  ┌─────────────┐  ║  ║            ║
║  every table   ║              ║  │DRF throttle │  ║  ╚════════════╝
║                ║              ║  │cache        │  ║
╚════════════════╝              ║  └─────────────┘  ║
                                ╚═══════════════════╝
                                         │
                                         ▼
                               ╔═══════════════════╗
                               ║  Celery Workers   ║
                               ║                   ║
                               ║  • AI extraction  ║
                               ║  • OCR / redact   ║
                               ║  • Email dispatch ║
                               ║  • Embedding gen  ║
                               ║  • Webhook retry  ║
                               ╚═══════════════════╝
                                         │
                          ┌──────────────┼──────────────┐
                          ▼              ▼              ▼
                    ╔══════════╗  ╔══════════╗  ╔══════════╗
                    ║  Gemini  ║  ║VoyageAI  ║  ║   SMTP   ║
                    ║  (NLP /  ║  ║(law-2    ║  ║  (email  ║
                    ║  summ.)  ║  ║embed.)   ║  ║  notifs) ║
                    ╚══════════╝  ╚══════════╝  ╚══════════╝
```

---

### Request lifecycle

Every HTTP request passes through four middleware layers before hitting a DRF view.

```
HTTP Request
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. SecurityMiddleware                                           │
│     • HTTPS redirect (prod)  • HSTS headers  • CSP headers      │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. TenantMiddleware                                             │
│     • Decode JWT → extract tenant_id                            │
│     • Set request.tenant  (all downstream queries auto-scoped)  │
│     • Reject if tenant inactive or suspended                    │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. AuditMiddleware                                              │
│     • Generate X-Request-ID (UUID)                              │
│     • Start request timer                                       │
│     • Log: method, path, tenant_id, user_id, IP                 │
│     • On response: log status code + duration                   │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. MetricsMiddleware (OpenTelemetry)                            │
│     • Start OTel span with X-Request-ID                         │
│     • Attach tenant_id + user_id as span attributes             │
│     • End span + record Prometheus histogram on response        │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│  DRF View                                                        │
│     • IsAuthenticated permission check                          │
│     • Role-based permission check (IsAdminUser / HasRole)       │
│     • Tenant-aware throttle check (Redis)                       │
│     • Serializer validates input                                │
│     • ORM query → filtered by tenant_id (automatic)            │
│     • Business logic → service layer                            │
│     • Serializer serializes output                              │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
                          HTTP Response
                    (X-Request-ID, rate-limit headers,
                     security headers auto-attached)
```

---

### Async task pipeline

AI extraction, OCR, and email dispatch are decoupled from the request thread via Celery.

```
  API View                   Redis (broker)              Celery Worker
     │                            │                            │
     │  1. Enqueue task           │                            │
     │  ──────────────────────►  │                            │
     │                            │  2. Worker polls queue     │
     │  3. Return 202 Accepted    │  ◄──────────────────────  │
     │  { "task_id": "abc123" }   │                            │
     │                            │  4. Execute task           │
     │                            │  ┌─────────────────────────┤
     │                            │  │ AI draft generation:    │
     │                            │  │  a. Fetch from R2       │
     │                            │  │  b. Extract text (pypdf)│
     │                            │  │  c. Send to Gemini      │
     │                            │  │  d. Generate embeddings │
     │                            │  │     (VoyageAI law-2)    │
     │                            │  │  e. Store in pgvector   │
     │                            │  │  f. Upload result to R2 │
     │                            │  │  g. Notify via email    │
     │                            │  └─────────────────────────┤
     │                            │                            │
     │                            │  5. Store result in Redis  │
     │                            │  ◄────────────────────────│
     │                            │                            │
     │  6. Client polls status    │                            │
     │  GET /api/v1/tasks/abc123/ │                            │
     │  ──────────────────────►  │                            │
     │  ◄──────────────────────  │                            │
     │  { "status": "SUCCESS",   │                            │
     │    "result": { ... } }    │                            │

Retry strategy on failure:
  Attempt 1 → wait  60s
  Attempt 2 → wait 120s
  Attempt 3 → wait 240s
  Attempt 4 → FAILED — stored in Redis, alert fired
```

---

### Database schema design

```
┌─────────────────────────────────────────────────────────────────────┐
│  TENANTS                                                             │
│  id (PK) · name · plan · is_active · created_at                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  tenant_id FK (on every table below)
          ┌────────────────────┼─────────────────────┐
          │                    │                      │
          ▼                    ▼                      ▼
┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────┐
│  USERS           │  │  CONTRACTS       │  │  CONTRACT_VERSIONS    │
│  id (PK, UUID)   │  │  id (PK, UUID)   │  │  id (PK, UUID)        │
│  tenant_id (FK)  │  │  tenant_id (FK)  │  │  contract_id (FK)     │
│  email           │  │  name            │  │  version_number       │
│  password_hash   │  │  status          │  │  r2_key               │
│  roles[]         │  │  contract_type   │  │  file_hash            │
│  is_admin        │  │  current_version │  │  file_size            │
│  is_superadmin   │  │  parties[]       │  │  template_version     │
│  created_at      │  │  effective_date  │  │  change_summary       │
└──────────────────┘  │  expiry_date     │  │  created_by (FK)      │
                      │  approval_chain[]│  │  created_at           │
                      │  approved_by     │  └───────────────────────┘
                      │  signed_at       │
                      │  created_at      │  ┌───────────────────────┐
                      └──────────────────┘  │  CONTRACT_CLAUSES     │
                               │            │  id (PK, UUID)        │
                               │            │  contract_id (FK)     │
                               │            │  version_id (FK)      │
                               │            │  clause_type          │
                               │            │  content (TEXT)       │
                               │            │  position             │
                               │            │  embedding (vector)   │
                               │            │  alternatives[]       │
                               │            └───────────────────────┘
          ┌────────────────────┼─────────────────────┐
          ▼                    ▼                      ▼
┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────┐
│  AUDIT_LOGS      │  │  WORKFLOW_LOGS   │  │  AI_RESULTS           │
│  id (PK, UUID)   │  │  id (PK, UUID)   │  │  id (PK, UUID)        │
│  tenant_id (FK)  │  │  contract_id (FK)│  │  contract_id (FK)     │
│  user_id (FK)    │  │  event_type      │  │  analysis_type        │
│  action          │  │  from_status     │  │  result (JSONB)       │
│  resource_type   │  │  to_status       │  │  model_used           │
│  resource_id     │  │  actor_id (FK)   │  │  tokens_used          │
│  old_values JSONB│  │  notes           │  │  created_at           │
│  new_values JSONB│  │  created_at      │  └───────────────────────┘
│  ip_address      │  └──────────────────┘
│  timestamp       │
└──────────────────┘

Performance indexes:
  contracts:        (tenant_id, status)
                    (tenant_id, expiry_date)
                    (tenant_id, created_at DESC)
  contract_clauses: (tenant_id, clause_type)
                    embedding vector_cosine_ops  ← pgvector HNSW index
  audit_logs:       (tenant_id, timestamp DESC)
                    (tenant_id, resource_type, resource_id)
```

---

### Multi-tenant isolation model

```
HTTP Request: GET /api/v1/contracts/
Authorization: Bearer eyJ...{tenant_id: "acme-corp", user_id: "u1"}
                                │
                                ▼
                     TenantMiddleware
                     ┌─────────────────────────────────┐
                     │  1. Decode JWT                  │
                     │  2. Validate tenant is active   │
                     │  3. Set request.tenant_id       │
                     │  4. Set request.user            │
                     └─────────────────────────────────┘
                                │
                                ▼
                     ContractListView.get_queryset()
                     ┌─────────────────────────────────┐
                     │  return Contract.objects.filter(│
                     │    tenant_id=request.tenant_id  │
                     │  )  ← AUTOMATICALLY APPLIED     │
                     └─────────────────────────────────┘
                                │
                                ▼
             SQL: SELECT * FROM contracts
                  WHERE tenant_id = 'acme-corp'
                       ────────────────────────
                       Other tenants' rows
                       never appear in results

Tenant A (acme-corp):   [Contract 1] [Contract 2] [Contract 3]
Tenant B (globex):                   [Contract 4] [Contract 5]
Tenant C (initech):                               [Contract 6]
                        ↑──────────────────────────────────────
                        Each tenant only ever sees their own rows
```

---

### AI pipeline

```
User uploads contract PDF
         │
         ▼
POST /api/v1/ai/extract/metadata/ → 202 { task_id: "abc" }
         │
         │  Celery enqueues: ai_extraction_task
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CELERY WORKER                                                      │
│                                                                     │
│  Step 1: Fetch PDF from R2                                          │
│    bytes = r2_client.get_object(key=contract.r2_key)               │
│                                                                     │
│  Step 2: Extract text                                               │
│    text = pypdf.PdfReader(bytes).extract_text()                     │
│    # Fallback for scanned docs:                                     │
│    text = pytesseract.image_to_string(rasterized_page)             │
│                                                                     │
│  Step 3: Chunk for LLM context window                              │
│    chunks = split_by_clause_boundary(text, max_tokens=8192)        │
│                                                                     │
│  Step 4: Send to Gemini for metadata extraction                     │
│    for chunk in chunks:                                             │
│        response = gemini.generate_content(                         │
│            METADATA_PROMPT.format(text=chunk)                      │
│        )                                                            │
│        → parties[], effective_date, expiry_date, value, jurisdiction│
│                                                                     │
│  Step 5: Generate clause embeddings (VoyageAI law-2)               │
│    embeddings = voyageai.embed(                                     │
│        clauses, model="voyage-law-2"                               │
│    )  # Returns 1024-dimensional vectors                           │
│                                                                     │
│  Step 6: Store embeddings in pgvector                              │
│    ContractClause.objects.bulk_create([                            │
│        ContractClause(embedding=vec, content=text, ...)            │
│        for vec, text in zip(embeddings, clauses)                   │
│    ])                                                               │
│                                                                     │
│  Step 7: Cache result in AIResult                                   │
│  Step 8: Notify user via email + in-app                            │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
GET /api/v1/tasks/abc/ → { status: "SUCCESS", result: { parties: [...] } }

─────────────────────────────────────────────────────────────────────

Semantic Search: POST /api/search/semantic/
{ "query": "indemnification clauses over $1M" }
         │
         ▼
  1. Embed query:
     query_vec = voyageai.embed(query, model="voyage-law-2")[0]
         │
         ▼
  2. pgvector cosine similarity query:
     SELECT id, content,
            1 - (embedding <=> query_vec::vector) AS similarity
     FROM contract_clauses
     WHERE tenant_id = 'acme-corp'          ← tenant isolation
     ORDER BY embedding <=> query_vec::vector
     LIMIT 20
         │
         ▼
  3. Post-filter by relational fields:
     .filter(contract__status='active',
             contract__value__gte=1_000_000)
         │
         ▼
  Response: ranked clause matches with similarity scores 0.0–1.0
```

---

## Tech stack

| Category | Technology | Version | Why chosen |
|---|---|---|---|
| Framework | Django + DRF | 5.0 / 3.14 | Mature ORM, serializer security, admin, migrations |
| Language | Python | 3.11 | Stable, typed, great ecosystem |
| Database | PostgreSQL (Supabase) | 16 | Complex joins, pgvector, pg_trgm, managed ops |
| Vector search | pgvector + VoyageAI law-2 | — | In-DB similarity, no extra network hop, ACID |
| Cache + queue broker | Redis | 5.0 | Celery broker + DRF throttle backend |
| Task queue | Celery | 5.3 | Retry, backoff, task introspection, monitoring |
| AI / NLP | Google Gemini | gemini-pro | Extraction, summarization, classification |
| Embeddings | VoyageAI | voyage-law-2 | Legal-domain fine-tuned embeddings |
| Object storage | Cloudflare R2 | — | S3-compatible, zero egress fees |
| Auth | SimpleJWT + Google OAuth | 5.3 | Stateless JWT, no session DB lookups |
| API docs | drf-spectacular | 0.27.2 | Zero-config OpenAPI 3 from DRF views |
| Tracing | OpenTelemetry | 1.29.0 | Distributed traces across services |
| Metrics | Prometheus client | 0.20.0 | Request latency, error rates, queue depth |
| PDF | PyPDF2 + pypdf + pdf2image | — | Text extraction + rasterization |
| OCR | Tesseract + pytesseract | — | Scanned document text extraction |
| PDF generation | ReportLab + Jinja2 | — | Contract PDF rendering |
| Server | Gunicorn | 21.2 | Production WSGI, 4 workers |

---

## Key features

### Contract management
- Full CRUD with tenant-scoped access control and optimistic locking
- **Immutable version snapshots** — `ContractVersion` stores `r2_key`, `file_hash`, `file_size`, `change_summary` per revision
- Clause library with variable interpolation and drag-and-drop ordering
- PDF generation (ReportLab) and Tiptap HTML → PDF conversion
- OCR for scanned documents, document redaction for PII
- Digital signatures workflow with `Signer`, `SigningAuditLog`, immutable e-sign trace

### AI-powered analysis (async via Celery)
- **Metadata extraction** — parties, effective/expiry dates, contract value, jurisdiction (Gemini)
- **Clause classification** — payment, liability, indemnification, IP assignment, NDA, termination
- **Obligation extraction** — structured delivery, payment, notice obligations with deadlines
- **Semantic search** — cosine similarity via pgvector + VoyageAI law-2 (legal-domain model)
- **Document summarization** — executive summary + risk flag list
- **Similar clause detection** — surfaces matching clauses across the entire corpus

### Multi-stage approval workflows
- Configurable approval chains with role-based routing
- `ApprovalWorkflowEngine` — rule matching, escalation timeouts, notification hooks
- Contract state machine: `draft → submitted → in_review → approved → signed → archived`
- Email + in-app + webhook notification delivery with retry

### Authentication & RBAC
- Stateless JWT: 15-minute access + 7-day refresh, embedded `tenant_id` + `roles`
- OTP email verification for high-security operations (signing)
- Google OAuth 2.0 integration
- Five-role hierarchy: `superadmin / admin / approver / editor / viewer`

### Observability
- OpenTelemetry distributed tracing — spans correlated via `X-Request-ID`
- Prometheus metrics at `/metrics` — request latency, error rate, queue depth
- Immutable `AuditLogModel` — every mutation logged: user, tenant, action, IP, timestamp
- PII masking in logs (`email@domain.com → ***@***.com`)
- Slow query logging (> 100ms logged with full query)

---

## Quick start

### Option A — Docker (recommended)

Zero manual dependencies. Starts Postgres (with pgvector), Redis, Django, and Celery in one command.

```bash
git clone https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend.git
cd Contracts-Life-Cycle-Management-Backend

cp .env.local.example .env.local
# Edit .env.local — defaults work for Docker, add AI keys if needed

docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Open:
- `http://localhost:11000/api/docs/` → Swagger UI
- `http://localhost:11000/metrics` → Prometheus metrics
- `http://localhost:11000/admin/` → Django admin

### Option B — Manual setup

```bash
git clone https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend.git
cd Contracts-Life-Cycle-Management-Backend

python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt

cp .env.local.example .env.local
# Set: DB_HOST, DB_USER, DB_PASSWORD, REDIS_URL at minimum

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:11000
```

### Start Celery workers (required for AI features)

```bash
# Terminal A — Celery worker
source .venv/bin/activate
celery -A clm_backend worker -l info -Q default,ai,notifications

# Terminal B — Celery beat scheduler (expiry checks, reminders)
celery -A clm_backend beat -l info \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

---

## Docker local development

### docker-compose.yml

```yaml
version: '3.9'

services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: clm_dev
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  web:
    build:
      context: .
      dockerfile: Dockerfile
      target: development
    command: python manage.py runserver 0.0.0.0:11000
    volumes:
      - .:/app
    ports:
      - "11000:11000"
    env_file:
      - .env.local
    environment:
      SUPABASE_ONLY: "False"
      DB_HOST: db
      DB_PORT: "5432"
      DB_NAME: clm_dev
      DB_USER: postgres
      DB_PASSWORD: postgres
      DB_SSLMODE: disable
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  celery_worker:
    build:
      context: .
      dockerfile: Dockerfile
      target: development
    command: celery -A clm_backend worker -l info -Q default,ai,notifications
    volumes:
      - .:/app
    env_file:
      - .env.local
    environment:
      SUPABASE_ONLY: "False"
      DB_HOST: db
      DB_PORT: "5432"
      DB_NAME: clm_dev
      DB_USER: postgres
      DB_PASSWORD: postgres
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  celery_beat:
    build:
      context: .
      dockerfile: Dockerfile
      target: development
    command: celery -A clm_backend beat -l info
    volumes:
      - .:/app
    env_file:
      - .env.local
    environment:
      SUPABASE_ONLY: "False"
      DB_HOST: db
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/0
    depends_on:
      - db
      - redis

volumes:
  postgres_data:
  redis_data:
```

### Dockerfile (multi-stage)

```dockerfile
# ── Base ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev libpoppler-cpp-dev poppler-utils \
    tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Development ────────────────────────────────────────────────────────
FROM base AS development
COPY requirements-dev.txt requirements.txt ./
RUN pip install -r requirements-dev.txt
COPY . .
EXPOSE 11000
CMD ["python", "manage.py", "runserver", "0.0.0.0:11000"]

# ── Production ─────────────────────────────────────────────────────────
FROM base AS production
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Non-root user
RUN addgroup --system django && adduser --system --group django
USER django

EXPOSE 11000
CMD ["gunicorn", "clm_backend.wsgi:application", \
     "--bind", "0.0.0.0:11000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
```

### Common Docker commands

```bash
# Start full stack
docker compose up -d

# View logs
docker compose logs -f web
docker compose logs -f celery_worker

# Run migrations
docker compose exec web python manage.py migrate

# Run tests inside Docker
docker compose exec web pytest --cov=. --cov-report=term-missing

# Django shell
docker compose exec web python manage.py shell_plus

# Rebuild after dependency change
docker compose build web && docker compose up -d web

# Full reset (deletes DB data)
docker compose down -v
```

---

## Environment variables

Copy `.env.local.example` to `.env.local`. **Never commit secrets.**

```dotenv
# ── Django core ─────────────────────────────────────────────────────
DEBUG=True
DJANGO_SECRET_KEY=change-me-to-50-plus-random-characters
ALLOWED_HOSTS=localhost,127.0.0.1

# ── Database ────────────────────────────────────────────────────────
SUPABASE_ONLY=False
DB_HOST=localhost          # Docker: use service name 'db'
DB_PORT=5432
DB_NAME=clm_dev
DB_USER=postgres
DB_PASSWORD=postgres
DB_SSLMODE=disable

# Supabase transaction pooler (production):
# SUPABASE_ONLY=True
# DB_HOST=aws-0-REGION.pooler.supabase.com
# DB_PORT=6543
# DB_POOLER_MODE=transaction
# DB_CONN_MAX_AGE=0
# DB_SSLMODE=require

# ── Redis + Celery ──────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# ── AI services (optional — features degrade gracefully) ────────────
GEMINI_API_KEY=
VOYAGE_API_KEY=

# ── CORS ────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS_EXTRA=http://localhost:3000

# ── Email ───────────────────────────────────────────────────────────
GMAIL=
APP_PASSWORD=

# ── Cloudflare R2 ───────────────────────────────────────────────────
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
R2_PUBLIC_URL=https://pub-HASH.r2.dev

# ── Production additions ────────────────────────────────────────────
# DEBUG=False
# SECURITY_STRICT=True
# SECURE_SSL_REDIRECT=True
# SESSION_COOKIE_SECURE=True
# CSRF_COOKIE_SECURE=True
# SECURE_HSTS_SECONDS=31536000
# CORS_ALLOWED_ORIGINS_EXTRA=https://yourdomain.com
```

---

## CI/CD pipeline

### Pipeline stages

```
TRIGGER: push to main/develop  OR  pull_request to main
               │
    ┌──────────┼──────────┬────────────┐
    ▼          ▼          ▼            ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────────┐
│  LINT  │ │ TYPES  │ │SECURITY  │ │              │
│        │ │        │ │          │ │  (run in     │
│flake8  │ │ mypy   │ │ bandit   │ │  parallel)   │
│black   │ │django- │ │ safety   │ │              │
│isort   │ │ stubs  │ │ CVE scan │ │              │
└───┬────┘ └───┬────┘ └────┬─────┘ └──────────────┘
    └──────────┴───────────┘
                  │ all 3 pass
                  ▼
        ┌─────────────────────────────────────┐
        │  TEST JOB                           │
        │  Services: pgvector:pg16 + redis    │
        │  1. pip install requirements-dev    │
        │  2. enable pgvector extension       │
        │  3. manage.py migrate               │
        │  4. pytest --cov --cov-fail-under=60│
        │  5. upload coverage artifact        │
        └──────────────────┬──────────────────┘
                           │ tests pass
                           ▼
        ┌─────────────────────────────────────┐
        │  DOCKER BUILD (main only)           │
        │  1. docker build --target production│
        │  2. push to registry                │
        │  3. tag as :latest on main          │
        └──────────────────┬──────────────────┘
                           │
                           ▼
        ┌─────────────────────────────────────┐
        │  DEPLOY (main + manual gate)        │
        │  1. Deploy to staging               │
        │  2. Run smoke tests                 │
        │  3. Verify /api/docs/ + /metrics    │
        │  4. ── MANUAL APPROVAL GATE ──      │
        │  5. Deploy to production            │
        │  6. Monitor 30 min                  │
        │  7. Auto-rollback if errors > 1%    │
        └─────────────────────────────────────┘
```

### Full `.github/workflows/ci.yml`

```yaml
name: CI Quality Gate

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  PYTHON_VERSION: '3.11'
  DJANGO_SETTINGS_MODULE: clm_backend.settings

jobs:

  lint:
    name: Lint & Format
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: black --check --diff .
      - run: isort --check-only --diff .
      - run: |
          flake8 . --count --max-line-length=120 \
            --extend-ignore=E203,W503 \
            --exclude=venv,.venv,migrations \
            --statistics

  typecheck:
    name: mypy
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: mypy . --ignore-missing-imports --exclude 'venv|migrations|\.venv'

  security:
    name: Security Scan
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      - run: pip install bandit safety
      - name: bandit — code scan
        run: |
          bandit -r . \
            --exclude ./venv,./.venv,./tests \
            --severity-level high \
            --confidence-level medium
      - name: safety — CVE check
        run: safety check -r requirements.txt --full-report
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: security-reports
          path: "*.json"
          retention-days: 14

  test:
    name: Tests & Coverage
    runs-on: ubuntu-latest
    needs: [lint, typecheck, security]
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_DB: clm_test
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DEBUG: "False"
      SUPABASE_ONLY: "False"
      DB_HOST: localhost
      DB_PORT: 5432
      DB_NAME: clm_test
      DB_USER: postgres
      DB_PASSWORD: postgres
      DB_SSLMODE: disable
      REDIS_URL: redis://localhost:6379/0
      CELERY_BROKER_URL: redis://localhost:6379/0
      CELERY_ALWAYS_EAGER: "True"
      DJANGO_SECRET_KEY: ci-test-key-not-for-production
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY || 'placeholder' }}
      VOYAGE_API_KEY: ${{ secrets.VOYAGE_API_KEY || 'placeholder' }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      - run: pip install -r requirements-dev.txt
      - name: Enable pgvector
        run: |
          PGPASSWORD=postgres psql \
            -h localhost -U postgres -d clm_test \
            -c "CREATE EXTENSION IF NOT EXISTS vector;"
      - run: python manage.py migrate --noinput
      - name: Run tests
        run: |
          pytest \
            --cov=. \
            --cov-report=xml \
            --cov-report=term-missing \
            --cov-fail-under=60 \
            --ignore=venv --ignore=.venv \
            -v --tb=short
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: coverage-report
          path: coverage.xml
          retention-days: 14

  docker:
    name: Docker Build
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - name: Build production image
        run: |
          docker build \
            --target production \
            --tag clm-backend:${{ github.sha }} \
            .
      - name: Verify Django system check
        run: |
          docker run --rm \
            -e DEBUG=False \
            -e DJANGO_SECRET_KEY=test-key-ci \
            -e DB_HOST=localhost \
            clm-backend:${{ github.sha }} \
            python manage.py check --deploy || true
```

---

## Testing

### Test pyramid

```
                        ╔═══════════════════╗
                        ║   Smoke / E2E     ║  5%  — post-deploy only
                        ║  production_      ║       Full lifecycle
                        ║  tests.py         ║       Real credentials
                        ╠═══════════════════╣
                   ╔════╩═══════════════════╩════╗
                   ║   Integration Tests          ║  30%  — API + DB + Redis
                   ║   API endpoints end-to-end   ║        Celery ALWAYS_EAGER
                   ║   Real Postgres (pgvector)   ║        Mocked AI APIs
                   ╠═════════════════════════════╣
              ╔════╩═════════════════════════════╩════╗
              ║         Unit Tests                     ║  65%  — isolated, fast
              ║   Business logic, serializers,          ║         No DB
              ║   validators, service classes           ║         All externals mocked
              ╚═══════════════════════════════════════╝
```

### Test coverage targets by app

| App | Test file | Target |
|-----|-----------|--------|
| `authentication` | `authentication/tests.py` | > 90% |
| `contracts` | `contracts/tests.py` | > 85% |
| `ai` | `ai/test_advanced_features.py` | > 80% |
| `approvals` | `approvals/tests.py` | > 85% |
| `audit_logs` | `audit_logs/test_audit_logging.py` | > 90% |
| `search` | `search/tests.py` | > 80% |
| `tenants` | `tenants/tests.py` | > 95% |

### pytest.ini

```ini
[pytest]
DJANGO_SETTINGS_MODULE = clm_backend.settings
python_files = tests.py test_*.py *_tests.py
python_classes = Test*
python_functions = test_*
addopts =
    --strict-markers
    --tb=short
    -q
    --no-header
markers =
    unit: pure unit tests — no DB, no external services
    integration: requires Postgres + Redis
    slow: takes > 5s
    smoke: post-deploy production smoke tests
```

### conftest.py

```python
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from contracts.models import Contract
from tenants.models import Tenant

User = get_user_model()


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="Test Corp", plan="enterprise")


@pytest.fixture
def user(db, tenant):
    return User.objects.create_user(
        email="test@testcorp.com",
        password="StrongPass123!",
        tenant=tenant,
        roles=["editor"],
    )


@pytest.fixture
def admin_user(db, tenant):
    return User.objects.create_user(
        email="admin@testcorp.com",
        password="AdminPass123!",
        tenant=tenant,
        is_admin=True,
        roles=["admin"],
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def auth_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def admin_client(api_client, admin_user):
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def contract(db, tenant, user):
    return Contract.objects.create(
        tenant=tenant,
        name="Test Service Agreement",
        status="draft",
        created_by=user,
    )


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """Run Celery tasks synchronously in all tests."""
    settings.CELERY_ALWAYS_EAGER = True
    settings.CELERY_EAGER_PROPAGATES_EXCEPTIONS = True
```

### Mocking AI services

```python
from unittest.mock import patch, MagicMock

@pytest.mark.django_db
class TestAIExtraction:
    @patch('ai.tasks.genai.GenerativeModel')
    @patch('ai.tasks.voyageai.Client')
    def test_metadata_extraction_enqueues_task(
        self, mock_voyage, mock_gemini, auth_client, contract
    ):
        mock_gemini.return_value.generate_content.return_value = MagicMock(
            text='{"parties": ["Acme Corp", "Globex"], "effective_date": "2026-01-01"}'
        )
        mock_voyage.return_value.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024]
        )

        response = auth_client.post(
            '/api/v1/ai/extract/metadata/',
            {'contract_id': str(contract.id)},
            format='json',
        )

        assert response.status_code == 202
        assert 'task_id' in response.data
```

### Running tests

```bash
# Full suite with coverage
pytest --cov=. --cov-report=html --cov-report=term-missing

# Unit tests only (fast, < 30s)
pytest -m unit -x

# Integration tests
pytest -m integration

# Single app
pytest contracts/ -v

# Open HTML coverage report
pytest --cov=. --cov-report=html && open htmlcov/index.html

# Production smoke suite (needs live env vars)
bash tests/run_production_tests.sh
```

---

## API reference

Full reference: [`docs/BACKEND_API_DOCUMENTATION.md`](docs/BACKEND_API_DOCUMENTATION.md)
Interactive: [`/api/docs/`](http://localhost:11000/api/docs/) (Swagger) · [`/api/redoc/`](http://localhost:11000/api/redoc/) (ReDoc)

### Authentication

```
POST  /api/auth/register/       Register new user + tenant
POST  /api/auth/login/          Login → { access, refresh, user }
POST  /api/auth/verify-otp/     Verify 6-digit OTP
POST  /api/auth/google/         Google OAuth 2.0 → JWT tokens
GET   /api/auth/me/             Current user profile
POST  /api/auth/refresh/        Rotate access token
POST  /api/auth/request-otp/    Request OTP for high-security operation
POST  /api/auth/logout/         Blacklist refresh token
```

### Contracts

```
GET    /api/v1/contracts/                   List (paginated, tenant-scoped)
POST   /api/v1/contracts/                   Create draft
GET    /api/v1/contracts/{id}/              Get + metadata
PATCH  /api/v1/contracts/{id}/              Update (auto-creates version snapshot)
DELETE /api/v1/contracts/{id}/              Soft-delete
POST   /api/v1/contracts/{id}/submit/       Submit for approval
GET    /api/v1/contracts/{id}/versions/     Version history
GET    /api/v1/contracts/{id}/diff/{v1}/{v2}/ Diff two versions
GET    /api/v1/contracts/{id}/download/     Download PDF from R2
POST   /api/v1/upload-document/             Upload PDF/DOCX to R2
```

### AI features

```
POST  /api/v1/ai/extract/metadata/     Async — extract parties, dates, value
POST  /api/v1/ai/classify/             Classify clause type
POST  /api/v1/ai/extract/obligations/  Async — extract structured obligations
POST  /api/v1/ai/summarize/            Async — executive summary + risk flags
POST  /api/v1/ai/similar-clauses/      Find similar clauses (pgvector cosine)
GET   /api/v1/tasks/{task_id}/         Poll async task status + result
```

### Search

```
GET  /api/search/semantic/      ?q=...   Cosine similarity (VoyageAI + pgvector)
GET  /api/search/full-text/     ?q=...   pg_trgm + ts_vector
GET  /api/search/saved/                  List saved searches
POST /api/search/saved/                  Save a search query
```

### Standard response shapes

```json
// List endpoint
{
  "count": 150,
  "next": "https://api/v1/contracts/?page=2",
  "results": [{ "id": "uuid", "name": "...", "status": "approved" }]
}

// Async task enqueued (202)
{ "task_id": "abc-123", "status": "PENDING", "poll_url": "/api/v1/tasks/abc-123/" }

// Task complete
{ "task_id": "abc-123", "status": "SUCCESS", "result": { "parties": ["..."] } }

// Error
{ "error": "INVALID_CONTRACT_STATE", "detail": "Must be draft to submit.", "status_code": 400 }

// Validation error
{ "error": "VALIDATION_ERROR", "fields": { "name": ["Required."] }, "status_code": 400 }

// Rate limited
{ "error": "THROTTLED", "detail": "Retry after 60 seconds.", "status_code": 429 }
```

---

## Authentication flows

### JWT token lifecycle

```
  Client                          Backend
    │                                │
    │  POST /api/auth/login/         │
    │  { email, password }  ───────► │
    │                                │  Verify credentials
    │                                │  Generate tokens:
    │                                │    access:  15-min
    │                                │    refresh: 7-day, rotated on use
    │  ◄──────────────────────────── │
    │  { access, refresh, user }     │
    │                                │
    │  GET /api/v1/contracts/        │
    │  Authorization: Bearer {access}│
    │  ────────────────────────────► │  Decode JWT (zero DB lookup)
    │                                │  Inject tenant_id → ORM auto-scoped
    │  ◄──────────────────────────── │
    │  { count: 5, results: [...] }  │
    │                                │
    │  [15 min later — token expired]│
    │                                │
    │  POST /api/auth/refresh/       │
    │  { refresh: "eyJ..." } ──────► │  Blacklist old refresh token
    │                                │  Issue new access + refresh
    │  ◄──────────────────────────── │
    │  { access: "eyJ...",           │
    │    refresh: "eyJ..." }         │
```

### OTP high-security flow

```
  Client               Backend              Email
    │                     │                   │
    │  POST /auth/request-otp/                │
    │  { email, purpose }  ──────────────────►│
    │                     │  Generate OTP      │
    │                     │  ────────────────►│  6-digit code sent
    │  { message: "sent" }│                   │
    │  ◄────────────────── │                  │
    │                     │                   │
    │  POST /auth/verify-otp/                 │
    │  { email, otp, purpose } ─────────────► │
    │                     │  Verify + mark used│
    │  { otp_token }      │  (single-use)      │
    │  ◄────────────────── │                  │
    │                     │                   │
    │  POST /api/v1/contracts/sign/           │
    │  Authorization: Bearer {otp_token}      │
    │  ────────────────────────────────────── │
    │                     │  Signed ✓         │
```

---

## Security model

```
INCOMING REQUEST
     │
     ▼  Layer 1 — Transport
        Cloudflare SSL · HTTPS redirect · HSTS 1yr · CSP · X-Frame-Options: DENY
     │
     ▼  Layer 2 — Authentication
        Verify HMAC-SHA256 JWT · Check expiry · Reject missing/malformed → 401
     │
     ▼  Layer 3 — Tenant validation
        Extract tenant_id from JWT · Verify tenant active · Inject request.tenant_id
     │
     ▼  Layer 4 — Authorization (DRF permission classes)
        IsAuthenticated · IsTenantMember · IsAdminUser · HasRole('approver')
     │
     ▼  Layer 5 — Rate limiting (TenantAwareScopedThrottle)
        Key: tenant_id:user_id:scope
        Auth: 5/min · AI: 20/min · Default: 1000/hr
        Redis-backed (fail-open — cache outage allows requests through)
     │
     ▼  Layer 6 — CSRF protection
        Required for POST/PATCH/DELETE · Double-submit cookie pattern
     │
     ▼  Layer 7 — Input validation (DRF serializers)
        Type coercion · Field validation · Custom business-rule validators
        ORM parameterized queries prevent SQL injection
     │
     ▼  Layer 8 — Row-level tenant isolation
        get_queryset() always filters by request.tenant_id
        Object-level check on detail views
        Cannot access another tenant's data even with valid JWT
     │
     ▼  Layer 9 — PII protection + audit logging
        Every mutation → immutable AuditLogModel (user, action, IP, timestamp)
        Email masked in logs (***@***.com)
        Sensitive fields excluded from serializer output
     │
     ▼  Layer 10 — Dependency security (CI)
        bandit scans code on every PR
        safety checks requirements.txt against CVE database
        Dependabot alerts enabled
     │
     ▼
RESPONSE
```

---

## Architecture decisions

### Redis + Celery for async tasks

AI operations run 5–30 seconds. Blocking the request thread would make the API unusable.

| | |
|---|---|
| **Decision** | Celery + Redis for all variable-latency workloads |
| ✅ Horizontal scale | Workers scale independently of the API server |
| ✅ Retry with backoff | `max_retries=4`, exponential `countdown` per task |
| ✅ Dev parity | `CELERY_ALWAYS_EAGER=True` runs tasks synchronously in tests |
| ❌ Overhead | Requires Redis uptime; dead-letter needs Flower |
| **Rejected** | Django async views (no retry), threads (silent failure) |

### pgvector over Pinecone / Weaviate / Qdrant

Vector search results must join against contract metadata. External vector DB = two network hops per search. pgvector = one ACID query.

| | |
|---|---|
| **Decision** | pgvector on primary Postgres instance |
| ✅ Zero-latency joins | Vector + relational in one SQL statement |
| ✅ ACID | Embeddings and contract rows commit atomically |
| ❌ Scale ceiling | Tops out ~100M vectors; dedicated DB wins at hyperscale |

### Multi-tenancy via row-level scope

Separate-DB multi-tenancy = N migrations per schema change + O(tenants) connection pool growth.

| | |
|---|---|
| **Decision** | `tenant_id` FK on every model; middleware injects from JWT |
| ✅ Portable | Works on any SQL DB; no Postgres RLS dependency |
| ✅ Auditable | Every row explicitly tagged |
| ❌ Developer burden | Must remember `tenant_id` on every new model |
| **Rejected** | Postgres RLS (harder to test with Django ORM) |

### Cloudflare R2 over AWS S3

Contract PDFs are read-heavy. S3 charges per-GB egress — this compounds with document volume.

| | |
|---|---|
| **Decision** | Cloudflare R2 (zero egress fees, S3-compatible) |
| ✅ Cost | Zero egress fees for read-heavy document store |
| ✅ Portable | `boto3` works unchanged |
| **Rejected** | S3 (egress cost), local filesystem (not multi-server safe) |

### Stateless JWT

Session DB lookup on every request = latency that compounds under load.

| | |
|---|---|
| **Decision** | Stateless JWT with embedded `tenant_id` + `roles` claims |
| ✅ Zero DB cost per request | Identity validated by HMAC signature only |
| ✅ Multi-region ready | Any replica validates without shared session store |
| ❌ Revocation lag | Token valid until expiry (mitigated by 15-min access window) |

### Prometheus + OpenTelemetry from day one

Adding observability retroactively after an incident is expensive.

| | |
|---|---|
| **Decision** | Prometheus metrics + OTel distributed tracing |
| ✅ Request correlation | `X-Request-ID` propagates through all logs and spans |
| ✅ Industry standard | Any Grafana/Alertmanager setup works |
| **Rejected** | ELK stack (heavyweight), Datadog (vendor lock-in + cost) |

---

## Production deployment

### Prerequisites

- [ ] PostgreSQL 16 with `pgvector` and `pg_trgm` (Supabase provides both)
- [ ] Redis (Upstash or self-hosted)
- [ ] Cloudflare R2 bucket
- [ ] Gemini API key + VoyageAI API key
- [ ] SMTP credentials
- [ ] Domain + SSL (Cloudflare handles termination)

### Deploy to Railway / Render (zero-infra)

```bash
# Railway
railway login && railway init
railway add --database postgresql
railway add --database redis
railway up
railway variables set DEBUG=False
railway variables set DJANGO_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(50))")
railway variables set SUPABASE_ONLY=False
railway run python manage.py migrate
```

### Deploy with Docker on VPS / EC2

```bash
git clone https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend.git
cd Contracts-Life-Cycle-Management-Backend
cp .env.local.example .env.production
# Fill .env.production with production values

docker compose -f docker-compose.prod.yml up -d
docker compose exec web python manage.py migrate --noinput
docker compose exec web python manage.py createsuperuser
```

### Gunicorn production settings

```bash
gunicorn clm_backend.wsgi:application \
  --workers $((2 * $(nproc) + 1)) \
  --worker-class gthread \
  --threads 4 \
  --timeout 120 \
  --bind 0.0.0.0:11000 \
  --access-logfile - \
  --error-logfile -
```

### Post-deployment checklist

```bash
curl https://api.yourdomain.com/api/docs/      # → 200 Swagger UI
curl https://api.yourdomain.com/metrics         # → 200 Prometheus data
bash tests/run_production_tests.sh              # smoke suite
```

---

## Repository structure

```
.
├── clm_backend/                  # Django project root
│   ├── settings.py               # DB, auth, CORS, AI, observability, security
│   ├── urls.py                   # Top-level routing + /metrics
│   ├── middleware.py             # TenantMiddleware, AuditMiddleware, MetricsMiddleware
│   ├── celery.py                 # Celery app config + task queues
│   ├── throttling.py             # TenantAwareScopedThrottle (fail-open)
│   └── schema.py                 # OpenAPI customization + Bearer auth
│
├── authentication/               # JWT, OTP, Google OAuth, RBAC
├── contracts/                    # Core CLM entities + versioning
├── ai/                           # AI analysis + Celery tasks
├── search/                       # Semantic + full-text search
├── approvals/                    # Approval workflow engine
├── workflows/                    # Workflow definitions + instances
├── audit_logs/                   # Immutable audit trail
├── tenants/                      # Multi-tenant management
├── notifications/                # Email + in-app + webhook
├── calendar_events/              # Contract milestone calendar
├── reviews/                      # Document review + annotation
├── repository/                   # R2 file upload/download
├── ocr/                          # Tesseract OCR
├── redaction/                    # PII document redaction
├── nda/                          # NDA-specific workflows
├── metadata/                     # Extracted metadata persistence
├── rules/                        # Business rule engine
│
├── docs/                         # Documentation
│   ├── BACKEND_API_DOCUMENTATION.md
│   ├── FEATURES_INDEX.md
│   ├── authentication.md
│   ├── workflows.md
│   ├── ai.md
│   └── admin.md
│
├── tests/                        # Integration + smoke tests
│   ├── production_tests.py
│   ├── run_production_tests.sh
│   └── README_PRODUCTION_TESTS.md
│
├── tools/                        # CLI utilities
│
├── .github/
│   ├── workflows/ci.yml          # Full CI/CD pipeline
│   └── pull_request_template.md
│
├── Dockerfile                    # Multi-stage: development + production
├── docker-compose.yml            # Local: Postgres, Redis, Web, Celery, Beat
├── requirements.txt              # Pinned production deps
├── requirements-dev.txt          # Dev + test + lint deps
├── pytest.ini                    # pytest config
├── pyproject.toml                # black, isort, bandit, mypy config
├── .pre-commit-config.yaml       # Pre-commit hooks
├── runtime.txt                   # python-3.11.7
├── .python-version               # 3.11
└── .env.local.example            # Environment template
```

---

## Documentation index

| Document | Contents |
|---|---|
| [`docs/BACKEND_API_DOCUMENTATION.md`](docs/BACKEND_API_DOCUMENTATION.md) | Complete endpoint reference — schemas, examples, error codes |
| [`docs/FEATURES_INDEX.md`](docs/FEATURES_INDEX.md) | All implemented features + status |
| [`docs/authentication.md`](docs/authentication.md) | JWT lifecycle, OTP, OAuth, RBAC deep-dive |
| [`docs/workflows.md`](docs/workflows.md) | Approval chains, state transitions, escalation |
| [`docs/ai.md`](docs/ai.md) | AI pipeline, model selection, embedding strategy |
| [`docs/admin.md`](docs/admin.md) | Admin endpoints, tenant management |
| [`tests/README_PRODUCTION_TESTS.md`](tests/README_PRODUCTION_TESTS.md) | Smoke tests, post-deploy runbook |

---

## Contributing

```bash
git checkout -b feat/your-feature     # or fix/, chore/, docs/

pip install -r requirements-dev.txt
pre-commit install

# Make changes, add tests

python manage.py test
pytest --cov=. --cov-report=term-missing
black --check . && isort --check-only .
bandit -r . --exclude ./venv -l

git commit -m "feat: add semantic clause similarity endpoint"
```

**Commit prefixes:** `feat:` · `fix:` · `chore:` · `refactor:` · `test:` · `docs:` · `ci:` · `security:` · `migration:` · `perf:`

PRs require CI Quality Gate (all 5 jobs) + one reviewer approval.

---

<div align="center">

**CLM Backend** · Django 5 · DRF 3.14 · pgvector · VoyageAI · Celery · OpenTelemetry · Cloudflare R2

[📹 Demo](https://www.loom.com/share/694dee3f381545b2a17f2dc1831c5bd0) · [🖥 Frontend](https://verdant-douhua-1148be.netlify.app) · [📖 API Docs](docs/BACKEND_API_DOCUMENTATION.md) · [🐛 Issues](https://github.com/vk93102/Contracts-Life-Cycle-Management-Backend/issues)

</div>