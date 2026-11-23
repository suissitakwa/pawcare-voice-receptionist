# Filling in the profile from the owner's website

When an owner gives their website on the first sign-up step (**Your business**), it's read in the background as they move on. The next steps then offer what the website says, and the owner chooses whether to use it:

| Step | What's offered |
|---|---|
| Your business | The phone number, if they haven't typed one |
| Where and when | Opening hours for each day, plus whatever of these is still empty: the time zone (from the address's state), "we're mobile" and the area served (groomers only), and the address (not for a mobile groomer). One **Use these** fills them all |
| Services and prices | Services they don't have yet, with prices and lengths where the website gives them |
| Your team | People who take appointments, if not already listed |
| What the receptionist should know | Questions the website answers (cancellations, deposits, payment, vaccinations, what to bring, parking, policies), each with its answer. **Add** one at a time, or **Add all** |

Each offer shows the words it was taken from ("From your website: 'Tuesday to Saturday 9am to 5pm'"). It has a button to use it and **Not these** to dismiss it. Using it only changes the form. It's saved with the step, like anything typed, so the owner still sees and can change every value before going on.

The website is read when the first step is saved with a website address, as long as hours or services are still empty. It's read once per address. If it can't be read, the next steps say why and offer **Try reading it again**. Nothing found is stored on the server; after a page reload, saving the first step with a new address reads it again.

## What's read

The home page, plus up to 4 of the site's own pages whose link looks useful: words like services, prices, menu, hours, contact, about, team, staff, book or FAQ in the address or the link text.

- Other sites, images and media files aren't read. `www.` and the bare domain count as the same site.
- Every page is fetched with the same guards as the owner's listed pages ([knowledge.md](knowledge.md#reading-pages-safely)): public addresses only, the connection pinned to the checked address, redirects checked, 20 seconds and 2 MB per page.
- The home page must be readable. Other pages that fail are skipped, and extra pages stop after 45 seconds in all.
- At most 60,000 characters of text are used.

## Only what the website says

One OpenAI call (`KNOWLEDGE_MODEL`, strict structured output, `store: false`) picks out the phone, address, hours, services and team. The model is told the text is data, never instructions, and to copy the exact words behind each item. The server then checks everything against the pages' text:

- **Every item** needs a quote of at least 6 characters that is on the site. Case, spacing, and curly or straight quotes and dashes don't matter.
- **Phone:** must be a valid phone number whose last 7 digits are in the quote.
- **Address:** every number in it (house number, ZIP code) must be in the quote.
- **Hours:** the opening and closing hour must both appear in the quote, in 12- or 24-hour form ("9am", "17:00", "noon"). A valid time is also needed, closing after opening, with one entry per day.
- **Services:** the name must be in the quote ("&" and "and" count as the same). A price is kept only if its dollar amount is in the quote, and a length only if its minutes are. Otherwise it's left blank for the owner. Price rows follow the business type: dog sizes for groomers, one price for salons and barbershops.
- **Team:** each name must be in the quote.
- **Mobile and area:** "mobile" needs a quote saying so, and is only kept for pet groomers. The area must be in its own quote word for word, and is only kept with "mobile".
- **Time zone:** not asked of the model. It comes from the state at the end of the checked address ("…, San Jose, CA 95113" → Pacific). A state split between zones gets the one most of its people use, so the owner should check it. An address without a US state gives none.
- **Answers:** checked like [suggested answers](knowledge.md#what-makes-a-suggestion-trustworthy): each needs a quote of at least 12 characters that is on the site. At most 12 are kept.

Items that fail are dropped, and the reply says how many. Blank lengths and prices are shown blank in the form. A price row is saved only once it has both, as before.

## How long it takes

Reading the pages takes a second or two; drafting the details is most of the wait. The call asks for minimal reasoning (`KNOWLEDGE_REASONING_EFFORT`, default `minimal`; leave it empty for a model without reasoning). Picking facts out of text needs little, and at its default effort a reasoning model can take minutes on a whole website. Each call logs how long it took and its token counts ("Structured profile_details reply in …"), and an unfinished reply logs why.

The steps say "This can take up to a minute; you can keep going meanwhile". If there's no answer after 150 seconds, they say it took too long and offer **Try reading it again**. An answer that still arrives later is used.

## Limits

Reading the website counts as one run against `KNOWLEDGE_RUNS_PER_HOUR` (40 per owner per hour, shared with suggested answers). It needs `OPENAI_API_KEY`; without one, the endpoint answers 503 and the steps say reading isn't available.

## API

`POST /api/v1/subscribers/profile/website/read`, signed in as the owner, no body. It reads the profile's saved `website_url` and returns:

```json
{
  "source": "https://shaggy.example/",
  "pages": ["https://shaggy.example/", "https://shaggy.example/services"],
  "business_phone": { "value": "(408) 555-0134", "quote": "Call us at (408) 555-0134" },
  "address": null,
  "hours": [{ "weekday": 1, "opens_at": "09:00", "closes_at": "17:00", "quote": "Tuesday to Saturday 9am to 5pm" }],
  "services": [
    { "name": "Nail Trim", "description": "", "prices": [{ "size": "small", "duration_minutes": null, "price_cents": 1500 }], "quote": "Nail Trim $15" }
  ],
  "team": [{ "name": "Sam", "quote": "Your groomers: Sam and Jo" }],
  "mobile": null,
  "service_area": null,
  "timezone": null,
  "faqs": [{ "question": "How much notice do I need to cancel?", "answer": "24 hours; late cancellations are $20.", "quote": "We ask for 24 hours' notice to cancel" }],
  "dropped": 0
}
```

| Status | Code | When |
|---|---|---|
| 404 | `no_website` | No website saved in the profile |
| 422 | `page_not_allowed`, `page_unreachable`, `page_too_large`, `page_too_slow`, `unsupported_page`, `no_text` | The home page couldn't be read |
| 429 | `too_many_requests` | Over the hourly limit |
| 502 | `suggestions_unavailable` | OpenAI failed |
| 503 | `suggestions_unavailable` | No OpenAI key |

## Not done yet

- Not tried against OpenAI with a real key or on real business websites. The request is checked against a stand-in.
- Pages that show their text only after JavaScript runs, and booking widgets (Square, Vagaro, Fresha) whose menus live on another site.
- Per-person hours and breaks: everyone works the opening hours, as with typed hours.
