# 0004: Staff authentication

**Status:** Accepted. Implemented in migration 0005, `app/staff.py`, and `app/staff_api.py`.

## Context

Staff need a protected dashboard. The website (Netlify) and the API (Railway) are on different sites, and browsers increasingly block cookies sent across sites, so a session cookie would fail in Safari and other privacy-focused browsers.

## Decision

- **Opaque bearer tokens.** Sign-in returns a random 256-bit token. The server stores only its SHA-256 hash, with an 8-hour expiry, and sign-out revokes it. A copied token stops working after sign-out or expiry.
- **Argon2id password hashing** through `argon2-cffi`, with automatic rehashing if the parameters change.
- **No passwords in configuration.** The staff account is created at startup from `STAFF_EMAIL` and `STAFF_PASSWORD_HASH`. The hash is generated locally with `python -m app.staff hash`, so the password never reaches Railway, logs, or the repository.
- **Lockout.** Five failed sign-ins lock the account for 15 minutes. Unknown emails and wrong passwords return the same response after the same work, so the API does not reveal which staff emails exist.
- **Server-side scoping.** Every staff query filters by the signed-in staff member's business. No request parameter selects a business.
- **Token kept in session storage.** It survives a page reload but not closing the tab.

## Tradeoffs

| | Bearer token (chosen) | Session cookie |
|---|---|---|
| Works across Netlify and Railway | Yes | Blocked by several browsers unless the API is proxied through Netlify |
| Readable by page scripts | Yes, so a script injection could steal it | No, with `HttpOnly` |
| Cross-site request forgery | Not applicable, since browsers never attach it automatically | Needs `SameSite` or tokens |

The main risk is script injection. The site renders no user-supplied HTML, and React escapes text by default. Proxying the API through Netlify and switching to an `HttpOnly` cookie remains an option if the risk profile changes.

## Not included

Multi-factor authentication, password reset, and multiple roles. One staff account per business is enough for the demonstration and a first pilot.
