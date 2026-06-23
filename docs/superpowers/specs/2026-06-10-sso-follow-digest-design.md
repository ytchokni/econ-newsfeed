# SSO + Follow Researchers + Weekly Email Digest

**Date:** 2026-06-10
**Status:** Approved

## Overview

Add Google SSO so users can create accounts, follow researchers, and receive a weekly email digest of new publications from their followed researchers. Uses NextAuth.js on the frontend, JWT verification on the backend, and Resend for email delivery.

## 1. Authentication (NextAuth.js + Google OAuth)

### Frontend (Next.js)

- Add `next-auth` with the Google provider
- NextAuth API route at `app/api/auth/[...nextauth]/route.ts`
- JWT session strategy (stateless — no session DB needed on the Next.js side)
- The JWT contains `{ sub: google_id, email, name, picture }`
- UI: "Sign in with Google" button in the header; when signed in, show avatar + dropdown with "My Feed" / "Sign out"

### Backend (FastAPI)

- New dependency: `python-jose[cryptography]` for JWT verification
- A `get_current_user` dependency that reads the `Authorization: Bearer <token>` header, verifies the JWT signature using the shared `NEXTAUTH_SECRET`, and returns the user record
- On first valid JWT, lazily create the user row in MySQL (`users` table)
- Protected endpoints (follow/unfollow, notification prefs) require this dependency; public endpoints (newsfeed, researchers) remain unauthenticated

### Env vars

- `NEXTAUTH_SECRET` — shared between frontend and backend for JWT signing/verification
- `GOOGLE_CLIENT_ID` — from Google Cloud Console OAuth credentials
- `GOOGLE_CLIENT_SECRET` — from Google Cloud Console OAuth credentials

## 2. Database Schema

```sql
-- User accounts (created on first Google sign-in)
CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  google_id     VARCHAR(255) UNIQUE NOT NULL,
  email         VARCHAR(255) NOT NULL,
  name          VARCHAR(255),
  picture_url   TEXT,
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Which researchers a user follows
CREATE TABLE user_follows (
  user_id       INT NOT NULL,
  researcher_id INT NOT NULL,
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, researcher_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Email notification preferences
CREATE TABLE user_notification_prefs (
  user_id           INT PRIMARY KEY,
  digest_enabled    BOOLEAN DEFAULT TRUE,
  last_digest_sent  DATETIME,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### Design notes

- `user_follows` uses a composite PK to prevent duplicate follows
- `user_notification_prefs` is 1:1 with users, created lazily (default: digest enabled)
- `last_digest_sent` tracks when we last emailed each user — digest query is "feed events since `last_digest_sent` for followed researchers"
- All FKs cascade on delete, consistent with existing schema conventions

## 3. API Endpoints

### Follow/unfollow (all require auth)

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/users/follow/{researcher_id}` | Add follow (idempotent) |
| `DELETE` | `/api/users/follow/{researcher_id}` | Remove follow (idempotent) |
| `GET` | `/api/users/following` | List followed researcher IDs |

### Notification preferences (require auth)

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/api/users/notifications` | Get digest prefs |
| `PATCH` | `/api/users/notifications` | Update prefs (`{ "digest_enabled": bool }`) |

### User profile (requires auth)

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/api/users/me` | Return name, email, picture, created_at |

### Unsubscribe (no auth — signed URL)

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/api/users/unsubscribe?token=...` | HMAC-signed token disables digest |

### Behavior notes

- 401 if no valid JWT on protected endpoints
- Follow endpoints return 404 if researcher doesn't exist
- Public endpoints (newsfeed, researchers) remain unchanged — except `/api/publications` gains optional auth: when `?preset=following` is passed with a valid JWT, results are filtered to followed researchers; without a JWT the preset is ignored (no 401)
- A `get_optional_user` dependency (returns `None` if no JWT) supports this pattern

## 4. Frontend UX

### Header

- Signed out: "Sign in with Google" button in top nav
- Signed in: user avatar + name, dropdown with "My Follows" and "Sign out"

### Follow buttons

- `/researchers` directory: small "Follow" / "Following" toggle on each researcher card
- `/researchers/[id]` detail: prominent follow button near researcher name
- Optimistic UI updates via SWR mutation

### "My Feed" view

- New filter on the main newsfeed: "My Feed" toggle that filters to followed researchers only
- Implemented as query param `?preset=following`, backend resolves via JWT
- Hidden when not signed in

### Notification preferences

- Accessible from user dropdown or "My Follows" page
- Single toggle: "Send me a weekly email digest" (on by default)
- No separate settings page

## 5. Weekly Digest Email

### Sending service

Resend (Python SDK `resend`). Free tier: 100 emails/day, 3000/month.

### Digest job

A scheduled job in `scheduler.py`, runs weekly (Monday 8am UTC):

1. For each user where `digest_enabled = TRUE`:
   a. Query `feed_events` since `last_digest_sent` (or `created_at` if never sent) for researchers in `user_follows`
   b. Skip if zero events (no empty emails)
   c. Render HTML email grouped by researcher — each paper shows title, status, link to detail page
   d. Send via Resend API
   e. Update `last_digest_sent`

### Email content

- **Subject:** "Econ Newsfeed — Weekly Digest (June 10–17)"
- **Body:** grouped by researcher, each paper with title, status (working paper / published), link to paper detail page
- **Footer:** "Manage your follows" link + one-click unsubscribe

### Unsubscribe

- HMAC-signed URL with user ID
- Hits `GET /api/users/unsubscribe?token=...` — sets `digest_enabled = FALSE` without login
- Required for email deliverability

### Env vars

- `RESEND_API_KEY` — from Resend dashboard
- `DIGEST_FROM_EMAIL` — e.g. `digest@econ-newsfeed.com` (requires domain verification in Resend)

## 6. New Dependencies

### Python (pyproject.toml)

- `python-jose[cryptography]` — JWT verification
- `resend` — email sending

### Frontend (package.json)

- `next-auth` — OAuth + session management

## 7. Infrastructure Changes

### Env vars to add

| Var | Where | Purpose |
|-----|-------|---------|
| `NEXTAUTH_SECRET` | Vercel + Lightsail | JWT signing |
| `NEXTAUTH_URL` | Vercel | Canonical URL for callbacks |
| `GOOGLE_CLIENT_ID` | Vercel | OAuth |
| `GOOGLE_CLIENT_SECRET` | Vercel | OAuth |
| `RESEND_API_KEY` | Lightsail | Email sending |
| `DIGEST_FROM_EMAIL` | Lightsail | Sender address |

### Docker / deployment

- Add `RESEND_API_KEY` and `DIGEST_FROM_EMAIL` to `docker-compose.prod.yml` environment whitelist
- Add `NEXTAUTH_SECRET` to `docker-compose.prod.yml` if backend needs it (for JWT verification)
- No new containers needed — digest job runs inside the existing API process via APScheduler

### Google Cloud Console

- Create OAuth 2.0 credentials (Web application type)
- Add authorized redirect URIs: `https://econ-newsfeed.vercel.app/api/auth/callback/google` and `http://localhost:3000/api/auth/callback/google`

### Resend

- Verify sending domain
- Generate API key
