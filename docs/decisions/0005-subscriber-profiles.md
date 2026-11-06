# 0005: Subscriber accounts and business profiles

## Context

The receptionist has to be configurable by the businesses that use it, not only for the demo business. A business owner needs to sign up, describe their business, and choose how their receptionist behaves. The first businesses supported are pet groomers, hair salons, and barbershops.

## Decision

- **Separate accounts.** Subscriber accounts are their own table, apart from staff accounts, because a subscriber is not staff of the demo business. They reuse the same protections: Argon2id hashes, hashed bearer sessions lasting 8 hours, and a 15-minute lockout after 5 failed sign-ins. Sign-ups are limited to 5 per visitor address per hour, and the address is stored only as a hash.
- **One profile per account.** It is created empty at sign-up, saved section by section as a draft, and submitted once every required section is filled in. A submitted profile can still be edited but cannot become incomplete. The database rejects a submitted profile without a name, type, and submission time.
- **Business name and type are columns; the rest is validated JSON.** The type is checked by the database and the API, so a new type is a deliberate change to both. The other details are validated by a strict schema on every save and stored as JSONB. They are still changing shape, and nothing queries them yet. They move to proper tables when a profile starts driving calls.
- **Documents are stored, not yet read.** PDF, Word (.docx), and UTF-8 text files up to 5 MB, at most 10 per profile. The type is checked against the file's first bytes, the name is stripped of any path, and a repeated upload of the same file is stored once. They live in PostgreSQL for now, because there is no object storage yet and the volume is small.
- **Short on purpose.** The profile asks only for the business name, type, and phone number, an optional website, the calendar used, at least one knowledge source (web pages, documents, or questions and answers), a female or male voice, and agreement to AI disclosure. Further options (hours, services, greeting, notifications, plans) are added when businesses ask for them. Saved details that a later version no longer has are dropped when a profile is loaded, so older drafts still open.
- **Collected first, connected later.** Website import, reading documents, calendar connection, the phone line, and billing are separate steps. The profile records what each one needs: web addresses, documents, the calendar used, and the business phone number.

## Consequences

- One account manages one business. Several locations or several staff logins per business need a change to the model.
- Callers are told they are speaking with an AI assistant. The subscriber agrees to this before submitting.
- The sign-up asks the subscriber to accept terms and a privacy policy, which have to be written before real businesses sign up.
- Healthcare businesses are left out on purpose. Their callers share health information, which brings legal obligations this product does not meet.
