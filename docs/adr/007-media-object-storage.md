# ADR-007: Media on S3-compatible object storage

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — django-rq adopted; second private bucket added
**Deciders:** Tech lead

## Context

Photographs are the product. A student decides whether to visit a property from
its pictures, and a listing without them may as well not exist. Landlords will
upload several per unit, from phones, at whatever resolution the camera
produces — 4–8 MB each is normal.

The draft writes them to local disk: `MEDIA_ROOT = BASE_DIR / 'media'`, served
by Django's `static()` helper under `DEBUG`. Every failure mode of local disk
applies:

- The `docker-compose.yml` at audit time had **no media volume**, so uploads
  lived in the container's writable layer and were destroyed by
  `docker compose down`.
- Local disk cannot be shared between replicas: two backend containers means
  each holds half the photos and serves 404s for the other half.
- Django serving media in production is single-threaded, uncached and slow.
- Backups become a filesystem concern separate from the database.

## Decision

**All user-uploaded media goes to S3-compatible object storage via
`django-storages`. Never to local disk, in any environment.**

| Environment | Backend |
|---|---|
| Production | **Cloudflare R2** |
| Local Docker | **MinIO** (`minio/minio`), an extra compose service |
| Tests | `InMemoryStorage` — already configured in `config/settings/test.py` |

Both R2 and MinIO speak the S3 API, so the same `storages.backends.s3.S3Storage`
serves all three with different endpoint and credential values. Storage is
selected by environment variable, never by a code branch.

```python
STORAGES = {
    # Public listing photos and university logos.
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": config("S3_MEDIA_BUCKET"),
            "endpoint_url": config("S3_ENDPOINT_URL"),
            "region_name": config("S3_REGION", default="auto"),
            "querystring_auth": False,   # listing photos are public
            "file_overwrite": False,
            "default_acl": None,         # R2 does not implement ACLs
        },
    },
    # Verification documents. Never the CDN, never a predictable URL.
    "documents": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": config("S3_DOCUMENTS_BUCKET"),
            "endpoint_url": config("S3_ENDPOINT_URL"),
            "region_name": config("S3_REGION", default="auto"),
            "querystring_auth": True,    # signed URLs only
            "querystring_expire": 300,   # five minutes
            "file_overwrite": False,
            "default_acl": None,
        },
    },
    "staticfiles": {...},                # WhiteNoise stays as it is
}
```

**Image variants are generated asynchronously.** An upload is accepted and
stored at its original resolution; a background job then produces the derived
sizes and records them against the photo:

| Variant | Longest edge | Used by |
|---|---:|---|
| `thumb` | 400 px | Result cards, gallery strip |
| `medium` | 1024 px | Detail page main image |
| `large` | 1920 px | Lightbox |

All variants are WebP with a JPEG fallback. The original is retained so
variants can be regenerated when the sizes change.

`UnitPhoto` carries the storage keys for each variant plus a
`processing_status ∈ {pending, ready, failed}`; the API serves the original
until variants are ready, so a slow job degrades quality rather than breaking
the page.

### The queue is django-rq on the existing Redis — RESOLVED

Design review left the mechanism open and recommended Cloudflare Images if the
pricing worked, on the grounds that it would delete this subsystem entirely —
no queue, no worker, no variant bookkeeping.

**That argument no longer holds, and the decision goes the other way.**

By the time the other ADR amendments landed, a queue became mandatory
regardless of how images are handled:

| Job | Comes from |
|---|---|
| Auto-confirm tenancy claims past their deadline | ADR-004 |
| Route walking distance and time | ADR-002 |
| Delete verification documents after the retention window | ADR-003 |
| Generate image variants | this ADR |

Three of those four have nothing to do with images. Removing the image
subsystem therefore no longer removes the queue, which was Cloudflare Images'
main advantage — and adopting it would put a core asset behind per-image vendor
pricing for a benefit we no longer get.

**Resolved: `django-rq`, on the Redis instance already running.**

- Redis is already a dependency, for the cache and the readiness probe. The
  queue adds a worker process and no new infrastructure.
- `django-rq` is a thin layer: a job is a function, and the admin gives a queue
  view for free. Celery's extra machinery — its own result backend, beat, a
  broker abstraction we do not need — is not repaid at this size.
- Scheduled jobs (the confirmation deadline, the retention sweep) use
  `rq-scheduler`, which django-rq integrates.
- `UnitPhoto` **keeps** its variant key columns and `processing_status`.

### Two buckets, not one

ADR-003 introduced student ID document uploads, which are personal data under
Kenya's Data Protection Act 2019.

**Resolved: a second, private bucket with its own storage backend class.**

| Bucket | Contents | Access |
|---|---|---|
| `media` (public) | Listing photos, university logos | `querystring_auth = False`, served through the CDN |
| `documents` (private) | Student ID uploads, landlord ID documents | `querystring_auth = True`, short-lived signed URLs, no CDN |

These are **separate `STORAGES` entries with separate backend classes**, not a
key-prefix convention inside one bucket. A convention is one careless
`default_storage.save()` away from publishing someone's national ID; a
separate backend makes the public path unreachable from the document model's
field. The retention job in ADR-003 deletes from the private bucket only.

Static files are **not** affected: WhiteNoise continues to serve them from the
image. This decision is about user uploads only.

## Consequences

### What this buys us

- **Uploads survive everything** — container restarts, redeploys, scaling
  events, host replacement.
- **The backend becomes stateless**, so horizontal scaling is a replica count
  rather than a shared-filesystem problem.
- **R2 has zero egress fees**, which is the reason it is chosen over S3. For an
  image-heavy product serving students on metered mobile connections, egress is
  the dominant storage cost, and on S3 it would exceed the storage line item by
  a wide margin.
- Cloudflare's CDN sits in front of R2 by default, so images are served from an
  edge close to the user without a separate CDN contract.
- MinIO locally means the development path exercises the same code as
  production. Storage bugs surface on a laptop rather than in production, which
  is where the "works locally, 500s in prod" class of media bug comes from.
- Asynchronous variants keep the upload request fast. Resizing a 8 MB phone
  photo into three sizes takes seconds; doing it inline would make every upload
  feel broken.

### What it costs us

- **A job queue is now a hard dependency.** Resolved above as django-rq; the
  operational consequences are in the resolution section below. The failure mode
  to watch for in review is an "asynchronous" step quietly implemented inline
  because the worker was inconvenient to run locally.
- **A new compose service.** MinIO adds a container, a console, credentials and
  a bucket-creation step to the local setup. That is friction on `git clone`
  and must be scripted, or new developers will hit an opaque
  `NoSuchBucket` on their first upload.
- **An external dependency on the upload path.** R2 being unavailable means
  uploads fail. Acceptable — the alternative is worse — but the failure needs a
  clear user-facing message rather than a 500.
- **Local disk stays available accidentally.** `FileSystemStorage` remains
  Django's default, so any model field that forgets to name a storage backend
  silently writes to disk. Set the `default` storage as above so the fallback
  is object storage, not the other way round.

### Consequences of the queue and bucket resolutions

- **A worker process is now part of every environment.** docker-compose gains an
  `rq-worker` service and a scheduler; production gains a second deployable.
  A queue whose worker is not running fails silently — claims never auto-confirm
  (ADR-004), documents are never deleted (ADR-003), and nothing errors. Monitor
  queue depth and oldest-job age, not worker liveness alone.
- **Jobs must be idempotent.** RQ will retry, and the retention job in
  particular must tolerate the document already being gone.
- **Two buckets means two sets of credentials** and two lifecycle policies. The
  private bucket additionally needs its CDN binding explicitly *absent*; on
  Cloudflare, an R2 bucket with a public custom domain attached is public
  regardless of what `querystring_auth` says on our side. Verify this at
  provisioning time, not by inspection of the Django settings.
- **`default_storage` now points at the public bucket**, so any model field that
  forgets to name `storages["documents"]` writes a private document to a public
  place. Give the document model an explicit `storage=` argument and assert it
  in a test.
- **MinIO must model both buckets locally**, or the split is untested until
  production. The compose bootstrap creates both.
- **Orphaned objects remain a real problem.** Deleting a `UnitPhoto` row does
  not delete the object; `django-cleanup` or a periodic reconciliation job is
  needed, or the bucket accumulates files nothing references — invisibly,
  because nothing lists them.
- **Costs scale with content.** R2 storage is cheap and egress is free, but
  neither is zero. Enforce a per-file limit (5 MB) and a per-unit photo count
  (12) at the serializer, and reject non-image content by inspecting bytes
  rather than the declared type.
- **Nothing here says what happens to media when a university is deactivated.**
  Recommend a single public bucket with tenant-prefixed keys
  (`{university_subdomain}/properties/{id}/...`); per-tenant buckets multiply
  credentials and lifecycle rules for no isolation benefit, since those objects
  are public anyway.

## Alternatives considered

### Local disk with a Docker volume — rejected

What the compose file now does as a stopgap. Survives `docker compose down`,
but not host replacement, and it cannot be shared between replicas. It also
leaves Django serving media in production. Adequate for a single-container demo
and nothing beyond it.

### AWS S3 — rejected

The obvious choice, and technically interchangeable with R2 via the same
`django-storages` backend. Rejected on **egress cost**: for an image-heavy
product, data transfer out would be the largest storage-related line item, and
R2's zero-egress pricing removes it. Note the escape hatch is cheap — the same
backend class, three different environment variables — so this is a reversible
decision.

### Cloudflare Images / Cloudinary — considered, rejected

Upload, transform and CDN in one service, with resizing on the fly. This was the
leading candidate at review, on the strength of removing the queue entirely.

Rejected once the other ADR amendments made a queue mandatory for reasons
unrelated to images (see the resolution above). With the queue arriving anyway,
what remained was per-image vendor pricing on the platform's core asset and
originals living in a service with an export cost. The trade stopped being
worth it.

Still the right answer if the variant pipeline turns out to be a maintenance
burden in practice; it slots in behind the same `UnitPhoto` interface.

### Serving media through nginx from a shared volume — rejected

Fast and cheap, and it puts a network filesystem (NFS/EFS) in the critical path
of every upload. Shared filesystems fail in ways object storage does not, and
the operational burden is higher than R2's, not lower.
