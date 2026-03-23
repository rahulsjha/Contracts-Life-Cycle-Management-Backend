# 📋 CLM Backend (Django + DRF)

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.0-green?logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-3.14-red?logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-blue?logo=postgresql&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5.3-green?logo=celery&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-5.0-red?logo=redis&logoColor=white)

**Enterprise-grade Contract Lifecycle Management backend** built with Django, DRF, AI integrations, and real-time processing capabilities.

</div>

---

## 🎯 Overview

A production-ready Django REST Framework backend for contract management featuring:
- 🔐 **JWT-based authentication** with stateless token validation
- 🤖 **AI-powered features** (Gemini + VoyageAI for NLP/embeddings)
- ⚡ **Async task processing** with Celery
- 📊 **OpenTelemetry observability** + Prometheus metrics
- 🔍 **Advanced search** (semantic + full-text)
- 📝 **Multi-tenant architecture** with row-level isolation
- ☁️ **Cloud storage** (Cloudflare R2)
- 📄 **Auto-generated API docs** (Swagger/OpenAPI)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLM Backend                              │
│                     (Django + DRF API)                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────────┐
        │         Core Components                     │
        ├────────────────────────────────────────────┤
        │  • Authentication (JWT + OTP)              │
        │  • Contracts Management                    │
        │  • AI Features (NLP/Extraction)            │
        │  • Search (Semantic + Full-text)           │
        │  • Workflows & Approvals                   │
        │  • Calendar & Reviews                      │
        │  • Audit Logging                           │
        │  • Multi-tenant Isolation                  │
        └────────────────────────────────────────────┘
                              │
        ┌─────────────────────┴───────────────────────┐
        ▼                     ▼                     ▼
  ┌──────────┐       ┌──────────────┐      ┌──────────────┐
  │PostgreSQL│       │    Celery    │      │ Cloudflare   │
  │(Supabase)│       │   Workers    │      │      R2      │
  │          │       │              │      │   Storage    │
  │• pgvector│       │• Task Queue  │      │              │
  │• pg_trgm │       │• Redis Broker│      │• PDF/Docs    │
  └──────────┘       └──────────────┘      └──────────────┘
        │                     │
        ▼                     ▼
  ┌──────────┐       ┌──────────────┐
  │   AI     │       │    Redis     │
  │Services  │       │   Cache      │
  │          │       │              │
  │• Gemini  │       │• DRF Throttle│
  │• VoyageAI│       │• Sessions    │
  └──────────┘       └──────────────┘
```

---

## ✨ Key Features

### 🔐 Authentication & Security
- JWT-based stateless authentication
- OTP verification (email)
- Google OAuth integration
- Multi-tenant isolation middleware
- Role-based permissions
- PII protection logging

### 📄 Contract Management
- Full CRUD operations
- Template management
- Clause library
- PDF generation & processing
- Document version control
- OCR & redaction support

### 🤖 AI-Powered Features
- **Metadata extraction** (parties, dates, values)
- **Clause classification** (payment, liability, etc.)
- **Obligation extraction** from contracts
- **Semantic search** with pgvector + VoyageAI
- **Document summarization** (Gemini)
- **Risk analysis** & compliance checks

### 🔍 Search & Discovery
- Semantic search (vector embeddings)
- Full-text search (PostgreSQL)
- Faceted filtering
- Similar clause detection

### 🔄 Workflows & Approvals
- Custom approval workflows
- Multi-stage routing
- Email notifications
- Calendar integration
- Review & signing requests

### 📊 Observability
- Prometheus metrics endpoint
- OpenTelemetry instrumentation
- Request ID tracking
- Slow query logging
- Comprehensive audit logs

---

## 🧠 System-Level Backend Depth (Implemented in this repo)

This backend includes system-level CLM capabilities (not only endpoint-level features):

### 1) Database schema design (explicit domain modeling)

- **Core CLM entities** are modeled as first-class tables: `Contract`, `ContractVersion`, `ContractClause`, `WorkflowLog`, `ApprovalModel`, `Workflow`, `WorkflowInstance`, and `AuditLogModel`.
- **Tenant-first schema**: critical tables include `tenant_id` and indexes for tenant-scoped query performance.
- **Version-aware uniqueness constraints** exist across templates/clauses/contracts (for example: tenant+name+version, contract+version_number).
- **Hybrid relational + JSON design** supports strict entities plus flexible workflow/config payloads.
- **E-sign trace models** are also explicit (`Signer`, `SigningAuditLog`, `Firma*`, `Inhouse*`) for signature lifecycle state and compliance trails.

### 2) Contract version history + document traceability

- `Contract.current_version` tracks the current working revision.
- `ContractVersion` stores immutable snapshots with:
      - `version_number`
      - storage key (`r2_key`)
      - `template_version`
      - `change_summary`
      - integrity metadata (`file_hash`, `file_size`)
- `ContractClause` snapshots clause-level provenance per version (content + position + alternatives).
- `WorkflowLog` provides contract workflow events (`submitted`, `approved`, `rejected`, `version_created`, etc.).
- API/middleware audit trails are present through:
      - `audit_logs` app (`AuditLogModel`)
      - request-level audit middleware logging
      - e-sign provider-specific immutable signing audit logs.

### 3) Approvals workflow (multi-stage and policy-driven)

- `approvals` + `workflows` modules implement approval state and workflow instances.
- Contracts include approval fields: `approval_chain`, `approval_required`, `current_approvers`, `approved_by`, `approved_at`.
- `ApprovalWorkflowEngine` supports:
      - rule matching by entity conditions
      - configurable approval levels
      - timeouts/escalation flags
      - notification hooks (email + in-app)
      - approval analytics/statistics.

### 4) Role-based access and tenant isolation

- Stateless JWT auth carries tenant/user/admin claims (`tenant_id`, `is_admin`, `is_superadmin`).
- Custom permission classes enforce admin/superadmin authorization (`IsAdminUser`, `IsSuperAdminUser`).
- Global authenticated defaults + endpoint-level permissions across modules.
- Tenant isolation middleware injects tenant context at request time for scoped reads/writes.
- Tenant-aware throttling keys (`tenant_id:user_id`) reduce noisy-neighbor risk.

### 5) Production engineering for async OCR/AI-style workloads

- **Queue system**: Celery integrated with Redis broker/result backend.
- **Async job handling**: AI draft generation is queued and tracked via task records/status.
- **Retry/failure handling**: Celery task retry with backoff (`max_retries`, incremental `countdown`), explicit failed status, error persistence.
- **Task guardrails**: soft/hard execution time limits configured for workers.
- **Caching strategy**:
      - local/dev: `LocMemCache`
      - production: Redis cache backend
      - DRF throttling consumes this cache layer.
- **Degradation strategy**: fail-open throttling mixin prevents cache outages from taking APIs down.

### 6) API design signal (docs, versioning, behavior)

- **OpenAPI/Swagger** is built in via drf-spectacular:
      - `/api/schema/` (OpenAPI)
      - `/api/docs/` (Swagger UI)
- **Path versioning strategy** is already used (`/api/v1/...`) for core product APIs.
- **Auth + docs alignment**: Bearer auth schema is published for interactive testing.
- **Extensive API inventory** is maintained in `docs/BACKEND_API_DOCUMENTATION.md`.
- **Error shapes are documented** (`error`, `detail`, serializer validation maps) with HTTP status semantics.

### 7) Senior backend engineering signals

- **Scaling choices documented**: Supabase transaction pooler mode, connection aging strategy.
- **Infra separation**: Postgres for system-of-record, Redis for cache/queue, R2 for object storage.
- **Observability baseline**: Prometheus metrics, OpenTelemetry hooks, request correlation IDs, slow query logging.
- **Security controls**: strict mode toggles, hardened headers, JWT stateless auth, tenant isolation.
- **Tradeoff-aware defaults**: local developer ergonomics (locmem + optional strictness) with production-focused overrides via environment variables.

### 8) Architecture decisions (ADR-style rationale)

#### Redis + Celery for async task processing

**Decision**: Use Redis as broker + result store; Celery for task orchestration.

**Tradeoffs**:
- ✅ **Horizontal scalability**: workers can scale independently of the main API
- ✅ **Fail-safe**: explicit task retry with exponential backoff; persistence in result store
- ✅ **Dev parity**: tasks run synchronously in DEBUG mode (no Redis needed) via Celery test modes
- ❌ **Operational overhead**: requires Redis uptime; dead-letter handling needs external tooling
- ❌ **Alternative rejected**: long-polling or direct async (Django async views) — insufficient for multi-minute AI/OCR workloads

**Applied to**:
- AI draft generation (multi-step RAG → LLM → embeddings)
- Document OCR/redaction batch jobs
- E-signature status polling

---

#### Path-based API versioning (`/api/v1/...`)

**Decision**: Separate major versions by URL path segment; minor/patch within schemas.

**Tradeoffs**:
- ✅ **Explicit contract versioning**: client routes are permanent; breaking changes are obvious
- ✅ **Backward-compatible deprecation**: old API path stays live until sunsetting
- ✅ **Clear semantic**: consumers can pin stably
- ❌ **URL bloat**: namespace grows with major versions
- ❌ **Alternative rejected**: header-based versioning (Accept-Version) — harder for browser/curl/docs discovery

**Applied to**: all feature endpoints (`/api/v1/contracts/`, `/api/v1/ai/`, etc.)

---

#### Stateless JWT auth (no session DB lookup per-request)

**Decision**: Decode JWT claims into in-memory user context; validate signature only.

**Tradeoffs**:
- ✅ **Zero user state queries**: no DB round-trip per request
- ✅ **Scales to millions of concurrent users**: identity is self-contained
- ✅ **Multi-region/edge ready**: any backend replica can validate
- ❌ **Revocation lag**: token remains valid until expiry (mitigated by short lifetime)
- ❌ **Claim mutations**: role changes may not apply mid-session (acceptable for CLM workflows)

**Applied to**: JWT token carries `user_id`, `email`, `tenant_id`, `is_admin`, `is_superadmin`

---

#### Multi-tenant isolation via row-level scope + middleware injection

**Decision**: Include `tenant_id` in every model; middleware injects it; queries filter by it.

**Tradeoffs**:
- ✅ **Simple + auditable**: every row is explicitly tagged
- ✅ **Impossible to accidentally leak data**: SQL filters are deterministic
- ✅ **No RLS database feature needed**: portable to any SQL database
- ❌ **Developer burden**: `tenant_id` must be threaded everywhere
- ❌ **Alternative rejected**: database RLS (Postgres Row Security) — less portable; harder to test

**Applied to**: all core models include `tenant_id` index + filter in queryset definitions

---

#### Cloudflare R2 for document/artifact storage

**Decision**: S3-compatible object store for contract PDFs, signed documents, versions.

**Tradeoffs**:
- ✅ **Massive scale**: unlimited document count; per-put/get pricing
- ✅ **No server disk bloat**: separation of concerns (DB vs. files)
- ✅ **CDN-able**: public URLs optionally served via Cloudflare edge
- ❌ **Network latency**: every file fetch incurs round-trip to R2
- ❌ **Alternative rejected**: local filesystem (not suitable for multi-server); database BLOB storage (limits query performance)

**Applied to**: `/api/v1/upload-document/`, `/api/v1/contracts/{id}/download/`, e-signature PDFs

---

#### Supabase + PostGIS + pgvector (PostgreSQL + managed services)

**Decision**: Managed Postgres with vector extensions for semantic search; transaction pooler for connection limits.

**Tradeoffs**:
- ✅ **Full-featured SQL**: complex multi-join queries for contract/clause relationships
- ✅ **Vector extensions**: pgvector for semantic similarity without separate embedding DB
- ✅ **Managed operations**: automatic backups, monitoring, scaling
- ❌ **Vendor lock-in**: Supabase-specific pooler mode; migration to self-hosted is non-trivial
- ❌ **Alternative rejected**: MongoDB (weak for CLM relational schema); DynamoDB (overkill for transaction needs)

**Applied to**: all entity storage + semantic search queries

---

#### Prometheus + OpenTelemetry observability baseline

**Decision**: Export metrics to Prometheus; instrument requests with OTel for tracing.

**Tradeoffs**:
- ✅ **Standard observability**: Prometheus is industry-standard; integrates with Grafana
- ✅ **Request correlation**: X-Request-ID propagates through logs; OTel spans link events
- ✅ **Low-overhead**: no agent; client library is lightweight
- ❌ **Operational setup required**: Prometheus scraper + Grafana must be deployed separately
- ❌ **Alternative rejected**: ELK stack (heavyweight for current scale); Datadog (vendor lock-in + cost)

---

#### Emphasis on fail-open + graceful degradation

**Decision**: Cache/queue failures do not break API responses; throttling, rate limits layer.

**Tradeoffs**:
- ✅ **Resilience**: Redis down → requests still work (no cache, but service available)
- ✅ **Local dev friendly**: Redis optional for local debugging
- ✅ **Staged rollout**: can deploy cache layer incrementally
- ❌ **Stale data possible**: cache miss or outage means fresh compute (may be slow)
- ❌ **Harder to diagnose**: failures are silent (logged but not blocking)

**Applied to**: throttle mixin catches cache exceptions; returns 200 if backing cache fails

---

## 🛠️ Tech Stack

| Category | Technology |
|----------|------------|
| **Framework** | Django 5.0, Django REST Framework 3.14 |
| **Database** | PostgreSQL (Supabase) with pgvector |
| **Cache** | Redis 5.0 |
| **Task Queue** | Celery 5.3 |
| **AI/ML** | Google Gemini, VoyageAI |
| **Storage** | Cloudflare R2 (S3-compatible) |
| **Auth** | SimpleJWT, Google OAuth |
| **API Docs** | drf-spectacular (OpenAPI 3) |
| **Observability** | OpenTelemetry, Prometheus |

## Requirements

- Python **3.11.x** (see `runtime.txt`)
- A Supabase Postgres database (or set `SUPABASE_ONLY=False` for local Postgres)
- Optional for background jobs: Redis

## Quick start (local)

From the repo root:

```bash
cd CLM_Backend

# Create/activate venv (example)
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### 1) Configure environment

This project loads environment variables from:

1) `CLM_Backend/.env` (preferred)
2) `CLM_Backend/contracts/.env` (fallback; only fills missing vars)

Create `CLM_Backend/.env` with at least:

```dotenv
# Django
DEBUG=True
DJANGO_SECRET_KEY=change-me

# Database (Supabase)
SUPABASE_ONLY=True
DB_HOST=db.<project-ref>.supabase.co
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=... # never commit
DB_SSLMODE=require

# Optional: prefer transaction pooler to avoid max clients issues
# DB_HOST=aws-0-...pooler.supabase.com
# DB_POOLER_MODE=transaction
# DB_PORT=6543

# CORS
CORS_ALLOWED_ORIGINS_EXTRA=http://localhost:3000

# AI (optional depending on feature usage)
GEMINI_API_KEY=
VOYAGE_API_KEY=

# Email (optional unless SECURITY_STRICT=True)
GMAIL=
APP_PASSWORD=

# Redis / Celery (optional for background jobs)
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Cloudflare R2 (optional unless file features are used)
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=
R2_PUBLIC_URL=
```

Notes:

- **Supabase-only safety**: by default `SUPABASE_ONLY=True` will refuse non-Supabase DB hosts.
- If you need to run against local Postgres for development, set `SUPABASE_ONLY=False`.

### 2) Migrate + run

```bash
python manage.py migrate

# Use 11000 if you plan to use the included tooling/scripts
python manage.py runserver 0.0.0.0:11000
```

## Important endpoints

- Swagger UI: `GET /api/docs/`
- OpenAPI schema: `GET /api/schema/`
- Metrics: `GET /metrics`
- Admin: `GET /admin/`

Top-level routing is defined in `clm_backend/urls.py`.

## Background jobs (Celery)

If you use features that enqueue tasks, start Redis and a Celery worker.

```bash
# Terminal A (Redis)
redis-server

# Terminal B (Celery worker)
cd CLM_Backend
source .venv/bin/activate
celery -A clm_backend worker -l info
```

## Testing

- App/unit tests live alongside apps (e.g. `authentication/tests.py`, `audit_logs/test_audit_logging.py`).
- A production-style API validation suite exists under `tests/`.

Examples:

```bash
# Django test runner
python manage.py test

# Production suite runner (see tests/README_PRODUCTION_TESTS.md)
bash tests/run_production_tests.sh
```

## Troubleshooting

### “SUPABASE_ONLY is enabled but DB host is not a Supabase host”

- Set `DB_HOST` to your Supabase host (e.g. `db.<ref>.supabase.co`) or pooler host (`...pooler.supabase.com`).
- Or set `SUPABASE_ONLY=False` for local Postgres.

### Supabase pooler “max clients reached”

- Prefer transaction mode (`DB_POOLER_MODE=transaction`, commonly port `6543`).
- Keep Django connections short (`DB_CONN_MAX_AGE=0` is the default for poolers).

---

## 📁 Repository Structure

```
CLM_Backend/
├── 📂 clm_backend/          # Core Django project
│   ├── settings.py          # Configuration (DB, auth, CORS, AI)
│   ├── urls.py              # Main URL routing
│   ├── middleware.py        # Custom middleware (tenant, metrics, audit)
│   ├── celery.py            # Celery config
│   └── schema.py            # OpenAPI customization
│
├── 📂 authentication/       # User auth, JWT, OTP, OAuth
│   ├── models.py            # User model
│   ├── views.py             # Login, register, verify
│   ├── jwt_auth.py          # Stateless JWT authentication
│   └── middleware.py        # Auth-related middleware
│
├── 📂 contracts/            # Contract CRUD & templates
│   ├── models.py            # Contract, Clause, Template
│   ├── views.py             # API endpoints
│   ├── pdf_service.py       # PDF generation
│   └── clause_seed.py       # Initial clause data
│
├── 📂 ai/                   # AI-powered features
│   ├── views.py             # Metadata extraction, classification
│   ├── advanced_features.py # Summarization, obligation extraction
│   └── models.py            # AI result caching
│
├── 📂 search/               # Semantic & full-text search
│   ├── views.py             # Search endpoints
│   └── models.py            # Search indexes
│
├── 📂 workflows/            # Approval workflows
├── 📂 approvals/            # Workflow engine
├── 📂 calendar_events/      # Calendar integration
├── 📂 reviews/              # Document review
├── 📂 notifications/        # Email notifications
├── 📂 audit_logs/           # Comprehensive audit trail
├── 📂 tenants/              # Multi-tenant support
├── 📂 repository/           # File upload/storage
├── 📂 ocr/                  # OCR processing
├── 📂 redaction/            # Document redaction
│
├── 📂 docs/                 # Backend documentation
│   └── admin.md             # Admin features
│
├── 📂 tools/                # CLI utilities
│   ├── api_test_runner.py   # API testing tool
│   └── e2e_auth_signup_otp_flow.py
│
├── 📂 tests/                # Test suites
│   ├── README_PRODUCTION_TESTS.md
│   └── run_production_tests.sh
│
├── requirements.txt         # Python dependencies
├── runtime.txt              # Python 3.11.7
└── manage.py                # Django CLI
```

---

## 🔗 API Endpoints Overview

### 🔐 Authentication
```
POST   /api/auth/register/           # Register new user
POST   /api/auth/login/              # Login (get JWT)
POST   /api/auth/verify-otp/         # Verify OTP
POST   /api/auth/google/             # Google OAuth
GET    /api/auth/me/                 # Get current user
POST   /api/auth/refresh/            # Refresh JWT
```

### 📄 Contracts
```
GET    /api/v1/contracts/            # List contracts
POST   /api/v1/contracts/            # Create contract
GET    /api/v1/contracts/{id}/       # Get contract
PATCH  /api/v1/contracts/{id}/       # Update contract
DELETE /api/v1/contracts/{id}/       # Delete contract
```

### 🤖 AI Features
```
POST   /api/v1/ai/extract/metadata/       # Extract metadata
POST   /api/v1/ai/classify/               # Classify clause
POST   /api/v1/ai/extract/obligations/    # Extract obligations
POST   /api/v1/ai/summarize/              # Summarize document
```

### 🔍 Search
```
GET    /api/search/semantic/         # Semantic search
GET    /api/search/full-text/        # Full-text search
```

### 📊 Admin & Monitoring
```
GET    /api/docs/                    # Swagger UI
GET    /api/schema/                  # OpenAPI schema
GET    /metrics                      # Prometheus metrics
GET    /admin/                       # Django admin
```

---

## 🚀 Production Deployment

### Environment Variables (Production)

```bash
# Security
DEBUG=False
SECURITY_STRICT=True
DJANGO_SECRET_KEY=<strong-random-key>
ALLOWED_HOSTS=yourdomain.com,api.yourdomain.com

# Database (Supabase Transaction Pooler recommended)
DB_HOST=aws-0-...pooler.supabase.com
DB_PORT=6543
DB_POOLER_MODE=transaction
DB_CONN_MAX_AGE=0

# SSL & Security Headers
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000

# CORS
CORS_ALLOWED_ORIGINS_EXTRA=https://yourdomain.com
CSRF_TRUSTED_ORIGINS=https://yourdomain.com

# Required Services
REDIS_URL=redis://...
CELERY_BROKER_URL=redis://...
GEMINI_API_KEY=...
VOYAGE_API_KEY=...
R2_ACCESS_KEY_ID=...
```

### Performance Tuning

- Use **Supabase transaction pooler** (port 6543) to avoid connection limits
- Set `DB_CONN_MAX_AGE=0` for pooled connections
- Enable Redis caching for DRF throttling
- Run Celery workers for background tasks
- Monitor with Prometheus + OpenTelemetry

---

## 📚 Additional Documentation

- **Backend API Documentation**: `docs/BACKEND_API_DOCUMENTATION.md`
- **Admin Features**: `docs/admin.md`
- **Production Tests**: `tests/README_PRODUCTION_TESTS.md`
- **Feature Index**: `docs/FEATURES_INDEX.md`


---

## 🤝 Contributing

1. Follow Django/DRF best practices
2. Add tests for new features
3. Update OpenAPI schema annotations
4. Document environment variables
5. Run `python manage.py test` before committing

---

## 📄 License

Proprietary - Contract Lifecycle Management System

---

<div align="center">

**Built with ❤️ using Django & Django REST Framework**

[Backend API Docs](docs/) • [Frontend Repo](../CLM_Frontend/) • [Production Tests](tests/README_PRODUCTION_TESTS.md)

</div>
