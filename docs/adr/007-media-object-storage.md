# ADR-007: Media on S3-compatible object storage

**Status:** Accepted
**Date:** 2026-08-23
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
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": config("S3_BUCKET"),
            "endpoint_url": config("S3_ENDPOINT_URL"),
            "region_name": config("S3_REGION", default="auto"),
            "querystring_auth": False,   # listing photos are public
            "file_overwrite": False,
            "default_acl": None,         # R2 does not implement ACLs
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

- **A job queue is now a hard dependency**, and this ADR does not name one. See
  the open question below. Until it is resolved, "asynchronous" is aspirational
  and the implementation will quietly become synchronous — which is the failure
  mode to watch for in review.
- **A new compose service.** MinIO adds a container, a console, credentials and
  a bucket-creation step to the local setup. That is friction on `git clone`
  and must be scripted, or new developers will hit an opaque
  `NoSuchBucket` on their first upload.
- **An external dependency on the upload path.** R2 being unavailable means
  uploads fail. Acceptable — the alternative is worse — but the failure needs a
  clear user-facing message rather than a 500.
- **`querystring_auth = False` makes every object public to anyone with the
  URL.** Correct for listing photos, which are public content. It would be
  wrong for anything else: if verification documents (student IDs, title deeds)
  are ever stored, they need a **separate bucket** with signed URLs. Do not put
  them in this one. Enforce it with distinct storage classes rather than a
  convention about key prefixes.
- **Costs scale with content.** R2 storage is cheap and egress is free, but
  neither is zero, and an unbounded upload size is an unbounded bill. Enforce a
  per-file limit (5 MB) and a per-unit photo count (12) at the serializer, and
  reject non-image content types by inspecting the file, not the extension.
- **Orphaned objects.** Deleting a `UnitPhoto` row does not delete the object.
  `django-cleanup` or a periodic reconciliation job is needed, or the bucket
  accumulates files nothing references — and they are invisible, because
  nothing lists them.
- **Local disk stays available accidentally.** `FileSystemStorage` remains
  Django's default, so any model field that forgets to name a storage backend
  silently writes to disk. Set the `default` storage as above so the fallback
  is object storage, not the other way round.

### Flaws and gaps worth stating plainly

**1. "Image variants generated asynchronously" specifies an outcome, not a
mechanism, and the choice is not free.** Three realistic options:

| Option | Cost | Fit |
|---|---|---|
| **Celery + Redis** | A worker process, a beat scheduler if periodic jobs are wanted, real operational surface | The default answer; heavier than this workload needs |
| **django-rq** or **RQ** | One worker, Redis only, far simpler API | Good fit — Redis is already a dependency for cache and readiness |
| **Cloudflare Images** | No queue at all: upload once, request `?width=400` variants from the CDN | Removes the entire problem; costs per image and per delivery, and couples us to Cloudflare |

**Recommendation: Cloudflare Images if the numbers work, `django-rq` if not.**
Cloudflare Images deletes this whole subsystem — no queue, no worker, no
variant bookkeeping, no orphan reconciliation for derived files — and we are
already on Cloudflare for R2. If the per-image pricing does not suit, `django-rq`
is the smallest thing that satisfies the ADR as written. **This needs a decision
before the schema rewrite**, because `UnitPhoto`'s shape depends on it: with
Cloudflare Images the variant key columns and `processing_status` disappear
entirely.

**2. R2 has no built-in image resizing**, unlike Cloudflare Images or imgproxy.
The ADR pairs "never local disk" with "generate variants ourselves", which is
the combination that forces the queue. Worth noticing that the two halves of
this ADR are independent decisions.

**3. Nothing here says what happens to media when a university is
deactivated**, or whether buckets are shared across tenants. Recommend a single
bucket with tenant-prefixed keys (`{university_subdomain}/properties/{id}/...`)
— per-tenant buckets multiply credentials and lifecycle rules for no isolation
benefit, since the objects are public anyway.

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

### Cloudinary / imgkit — rejected as the primary store

Upload, transform and CDN in one service, with resizing on the fly. Genuinely
attractive and it would remove the queue question. Rejected as the *store* of
record because it means originals live in a proprietary service with an
export cost; retained as a candidate *in front of* R2. Cloudflare Images is the
same idea, closer to where the data already is.

### Serving media through nginx from a shared volume — rejected

Fast and cheap, and it puts a network filesystem (NFS/EFS) in the critical path
of every upload. Shared filesystems fail in ways object storage does not, and
the operational burden is higher than R2's, not lower.
