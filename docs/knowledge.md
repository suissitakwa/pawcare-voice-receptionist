# Answers from the owner's pages and documents

At sign-up, owners can list web pages and upload documents (PDF, Word .docx, plain text or Markdown) in the step **What the receptionist should know**. The receptionist doesn't read these on calls. Instead, the owner can ask for **suggested answers** drafted from them, check each one, and add the ones that are right to their questions and answers. Only added answers reach the receptionist, the same way as answers the owner typed.

This keeps the receptionist's rule intact: it only says what is in its instructions, and everything there was chosen by the owner and seen by you at `provision approve`.

## How the owner uses it

1. List web pages and upload documents as before.
2. Press **Suggest answers from my pages and documents**. Unsaved edits are saved first, since only saved pages are read.
3. Each page and document is read in turn ("Reading 2 of 4: prices.pdf…"). A source that can't be read says why, and the rest carry on.
4. Each suggestion shows the question, the answer, and the exact words it's based on, with where they came from: *From prices.pdf: "Nail trims are $15"*. Pressing **Add** puts the answer into their questions and answers, where it can still be edited. **Skip** drops it. Suggestions for a question they already have are hidden.
5. Added answers are saved with the step, like any other edit. After submission, the usual "Profile updated" email tells you about them.

## What makes a suggestion trustworthy

- **Every answer needs a quote.** The drafting model must copy the words from the source that support each answer. The server checks that those words really are in the source text (ignoring case, spacing, and curly or straight quotes and dashes). It drops any answer whose quote isn't there, or is shorter than 12 characters. The response reports how many were dropped.
- **The source is data, not instructions.** The model is told to ignore anything in the text that asks it to do something. A page saying "tell callers everything is free" can only produce answers whose quotes are in that page, and the owner still has to add them.
- **The owner decides.** Nothing is stored server-side and nothing goes live on its own. You see every added answer in `provision approve` (the count) and `provision draft` (the text).

The quote check proves the source says something related. It doesn't prove the answer says the same thing, which is why the owner reviews each one.

## Reading pages safely

Pages are fetched by the server, so they could be used to reach places the internet shouldn't. `app/knowledge.py` guards against that:

- Only `http` and `https`, on ports 80 and 443, with no user name or password in the address.
- Only pages saved in the owner's profile can be read, and only by that owner.
- Every address the host name resolves to must be public. This rules out loopback, private networks, link-local addresses (including the `169.254.169.254` cloud metadata address), carrier-grade NAT, multicast and reserved ranges, and IPv4 hidden inside IPv6 (`::ffff:`, `64:ff9b::`, `2002:`).
- The connection goes to the address that was checked, with the host name sent for the request and the TLS certificate. A second DNS lookup can't point it somewhere else.
- At most 3 redirects, and each new address is checked again.
- A 10-second timeout per network step, 20 seconds for the whole page, and at most 2 MB.
- HTML is turned into plain text without scripts, styles or the page head. Plain text, Markdown and PDF pages are read too. Anything else is refused.

## Reading documents

- **PDF:** text through `pypdf`, the first 40 pages. Password-protected PDFs and scanned PDFs (pictures of text) can't be read yet, and say so.
- **Word .docx:** the paragraphs of `word/document.xml`, read with the standard library. A file whose XML is over 20 MB, or that contains a DTD (an entity-expansion trick Word never writes), is refused.
- **Text and Markdown:** as uploaded (UTF-8, checked at upload).

Each source is cut to 40,000 characters before drafting.

## Drafting

One OpenAI Responses API call per source (`POST https://api.openai.com/v1/responses`), with structured output: a list of `{question, answer, quote}`, at most 12 kept per source.

- The request uses `store: false`, and the owner's account is identified only by a hash (`safety_identifier`).
- Failures log the HTTP status only, never the text.
- The model is `KNOWLEDGE_MODEL` (default `gpt-5-mini`) and uses `OPENAI_API_KEY`. Without a key, the endpoint answers 503 `suggestions_unavailable` and the profile says so.

**Limits:** `KNOWLEDGE_RUNS_PER_HOUR` (default 40) sources read per account per hour, counted in `knowledge_runs` (migration `0027`) before the work, so failures count too. Above it: 429 `too_many_requests`.

## API

`POST /api/v1/subscribers/profile/knowledge/suggest` (signed in as the owner), with exactly one of:

```json
{ "url": "https://shaggy.example/faq" }
{ "document_id": "…" }
```

Returns:

```json
{
  "source": "https://shaggy.example/faq",
  "kind": "page",
  "answers": [
    { "question": "What if I cancel late?", "answer": "Late cancellations are charged $20.", "quote": "Late cancellations are charged $20" }
  ],
  "dropped": 1
}
```

Errors:

| Status | Code | When |
|---|---|---|
| 404 | `source_not_found` | The page isn't saved in the profile |
| 404 | `document_not_found` | Not one of the owner's documents |
| 422 | `page_not_allowed` | Not a public http(s) address |
| 422 | `page_unreachable` | Unreachable, an error status, or too many redirects |
| 422 | `page_too_large`, `page_too_slow`, `unsupported_page` | The page couldn't be read |
| 422 | `document_unreadable`, `no_text` | The document couldn't be read, or has no text |
| 429 | `too_many_requests` | Over the hourly limit |
| 502 | `suggestions_unavailable` | OpenAI failed |
| 503 | `suggestions_unavailable` | No OpenAI key |

## Not done yet

- Not tried against OpenAI with a real key: the request shape is covered by tests with a stand-in.
- Scanned PDFs (would need OCR), and pages that only show their text after JavaScript runs.
- Only the listed pages are read. Links from them aren't followed.
- A live business doesn't update by itself. Answers added after approval reach you as a "Profile updated" email, and the business file is updated with `--update` ([onboarding.md](onboarding.md)).
