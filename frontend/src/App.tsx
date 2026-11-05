import { useEffect } from "react";
import { type Business, type Catalog, type Faq } from "./api";
import { formatDuration, formatPrice, formatTime, weekdayName } from "./format";
import BookingForm from "./BookingForm";
import { useSiteData } from "./useSiteData";

const NAV_LINKS = [
  { href: "#video", label: "Video" },
  { href: "#services", label: "Services" },
  { href: "#about", label: "About" },
  { href: "#faqs", label: "FAQs" },
  { href: "#contact", label: "Contact" },
];

function PawMark() {
  return (
    <svg className="paw" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <ellipse cx="16" cy="21" rx="6.5" ry="5.5" />
      <circle cx="8.5" cy="14.5" r="2.6" />
      <circle cx="13" cy="9.5" r="2.6" />
      <circle cx="19" cy="9.5" r="2.6" />
      <circle cx="23.5" cy="14.5" r="2.6" />
    </svg>
  );
}

function Actions() {
  return (
    <div className="actions">
      <a className="button button-primary" href="#assistant">
        Talk to our assistant
      </a>
      <a className="button button-secondary" href="#booking">
        Book an appointment
      </a>
    </div>
  );
}

function Hero({ tagline }: { tagline?: string }) {
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="container hero-grid">
        <div>
          <p className="eyebrow">Dog grooming, by appointment</p>
          <h1 id="hero-title">{tagline ?? "One groomer. One dog at a time."}</h1>
          <Actions />
        </div>
        <div className="hero-art" aria-hidden="true">
          <PawMark />
        </div>
      </div>
    </section>
  );
}

const VIDEO_TRANSCRIPT = [
  "This is Maya. She runs a one-groomer dog salon. All day long, both hands are busy.",
  "So when the phone rings, she can't pick up. And a customer who can't get through may book somewhere else.",
  "So we're building her some help. Meet Pip, a voice receptionist that lives on her website.",
  "A customer taps, Talk to our assistant, and just asks. Pip answers from Maya's approved prices and FAQs, never from a guess.",
  "Then Pip checks the real calendar, and only offers times that are actually free.",
  "The customer reviews the details and taps confirm. Only then is it booked, with a reference.",
  "A health question? Pip doesn't guess. It sets up a callback, so Maya can answer.",
  "Later, Maya sees every booking and callback in one place.",
  "Maya keeps grooming. Pip handles the front desk. PawCare is in development, and we're looking for our first pilot salon.",
  "On screen: Run a salon? Be our first pilot: suissitakwa@gmail.com",
];

function ExplainerVideo() {
  return (
    <div className="video-layout">
      <video
        className="explainer-video"
        controls
        playsInline
        preload="none"
        poster="/pawcare-explainer-poster.jpg"
        aria-label="PawCare concept explainer video"
      >
        <source src="/pawcare-explainer.mp4" type="video/mp4" />
        Your browser can't play this video. The transcript has the full story.
      </video>
      <div className="video-copy">
        <p className="lead">
          A one-minute look at the idea: a voice receptionist that answers questions and books
          appointments while the groomer's hands are busy.
        </p>
        <p className="notice">
          This is a concept animation with an AI-generated narrator. The voice assistant is still
          in development, and Maya's salon is fictional.
        </p>
        <details className="transcript">
          <summary>Read the transcript</summary>
          {VIDEO_TRANSCRIPT.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </details>
      </div>
    </div>
  );
}

function Section({
  id,
  title,
  tone,
  children,
}: {
  id: string;
  title: string;
  tone?: "tint";
  children: React.ReactNode;
}) {
  const headingId = `${id}-title`;
  return (
    <section id={id} className={tone ? `section section-${tone}` : "section"} aria-labelledby={headingId}>
      <div className="container">
        <h2 id={headingId}>{title}</h2>
        {children}
      </div>
    </section>
  );
}

function NotYetAvailable({ children }: { children: React.ReactNode }) {
  return <p className="notice">{children}</p>;
}

function ServicesList({ catalog }: { catalog: Catalog }) {
  const sizes = new Map(catalog.size_categories.map((size) => [size.slug, size]));
  const assessed = catalog.size_categories.filter((size) => size.requires_staff_assessment);
  return (
    <>
      <p className="lead">Prices are indicative demo prices and depend on your dog's size.</p>
      <div className="service-grid">
        {catalog.services.map((service) => (
          <article className="service-card" key={service.slug}>
            <h3>{service.name}</h3>
            <p>{service.description}</p>
            <table>
              <caption className="visually-hidden">{service.name} price and duration by dog size</caption>
              <thead>
                <tr>
                  <th scope="col">Size</th>
                  <th scope="col">Time</th>
                  <th scope="col" className="numeric">Price</th>
                </tr>
              </thead>
              <tbody>
                {service.options.map((option) => {
                  const size = sizes.get(option.size);
                  return (
                    <tr key={option.size}>
                      <th scope="row">
                        {size?.label ?? option.size}
                        {size && <span className="size-note">{size.description}</span>}
                      </th>
                      <td>{formatDuration(option.duration_minutes)}</td>
                      <td className="numeric">{formatPrice(option.price_cents)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </article>
        ))}
      </div>
      {assessed.map((size) => (
        <p className="assessment-note" key={size.slug}>
          <strong>{size.label}:</strong> {size.description}
        </p>
      ))}
    </>
  );
}

function FaqList({ faqs }: { faqs: Faq[] }) {
  return (
    <div className="faq-list">
      {faqs.map((faq) => (
        <details key={faq.slug}>
          <summary>{faq.question}</summary>
          <p>{faq.answer}</p>
        </details>
      ))}
    </div>
  );
}

function HoursList({ business }: { business: Business }) {
  const open = new Map(business.hours.map((hours) => [hours.weekday, hours]));
  return (
    <div>
      <h3>Hours</h3>
      <table className="hours-table">
        <caption className="visually-hidden">Opening hours, {business.timezone} time</caption>
        <tbody>
          {[0, 1, 2, 3, 4, 5, 6].map((weekday) => {
            const hours = open.get(weekday);
            return (
              <tr key={weekday}>
                <th scope="row">{weekdayName(weekday)}</th>
                <td>
                  {hours ? `${formatTime(hours.opens_at)} to ${formatTime(hours.closes_at)}` : "Closed"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SectionFallback({ failed }: { failed: boolean }) {
  return (
    <p className="muted" aria-live="polite">
      {failed ? "Not available right now." : "Loading…"}
    </p>
  );
}

function ContactDetails({ business }: { business: Business }) {
  return (
    <dl className="contact-list">
      <div>
        <dt>Email</dt>
        <dd>
          <a href={`mailto:${business.contact_email}`}>{business.contact_email}</a>
        </dd>
      </div>
      <div>
        <dt>Phone</dt>
        <dd>{business.contact_phone}</dd>
      </div>
      <div>
        <dt>Address</dt>
        <dd>{business.address}</dd>
      </div>
      <div>
        <dt>Time zone</dt>
        <dd>{business.timezone}</dd>
      </div>
    </dl>
  );
}

export default function App() {
  const { state, retry } = useSiteData();
  const data = state.status === "ready" ? state.data : undefined;
  const business = data?.business;
  const failed = state.status === "error";

  useEffect(() => {
    if (business) document.title = `${business.name} (demo)`;
  }, [business]);

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <p className="demo-banner" role="note">
        <strong>Demonstration site.</strong> This is a fictional business. Please do not enter
        sensitive or real personal information.
      </p>
      <header className="site-header">
        <div className="container header-row">
          <a className="brand" href="#main">
            <PawMark />
            <span>{business?.name ?? "Grooming demo"}</span>
          </a>
          <nav aria-label="Main">
            <ul className="nav-list">
              {NAV_LINKS.map((link) => (
                <li key={link.href}>
                  <a href={link.href}>{link.label}</a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>

      <main id="main" tabIndex={-1}>
        {failed && (
          <div className="container">
            <div className="alert" role="alert">
              <p>Business details could not be loaded. The service may be temporarily unavailable.</p>
              <button type="button" className="button button-secondary" onClick={retry}>
                Try again
              </button>
            </div>
          </div>
        )}

        <Hero tagline={business?.tagline} />

        <Section id="video" title="See the idea in one minute">
          <ExplainerVideo />
        </Section>

        <Section id="assistant" title="Talk to our assistant" tone="tint">
          <NotYetAvailable>
            The voice assistant is not connected in this build. Nothing you say is being recorded,
            and your microphone is not used.
          </NotYetAvailable>
        </Section>

        <Section id="services" title="Services and pricing">
          {data ? <ServicesList catalog={data.catalog} /> : <SectionFallback failed={failed} />}
        </Section>

        <Section id="about" title="About" tone="tint">
          {business ? <p className="lead">{business.about}</p> : <SectionFallback failed={failed} />}
        </Section>

        <Section id="faqs" title="Frequently asked questions">
          {data ? <FaqList faqs={data.faqs} /> : <SectionFallback failed={failed} />}
        </Section>

        <Section id="contact" title="Contact" tone="tint">
          {business ? (
            <div className="contact-grid">
              <ContactDetails business={business} />
              <HoursList business={business} />
            </div>
          ) : (
            <SectionFallback failed={failed} />
          )}
        </Section>

        <Section id="booking" title="Book an appointment">
          {data ? (
            <BookingForm business={data.business} catalog={data.catalog} />
          ) : (
            <SectionFallback failed={failed} />
          )}
        </Section>
      </main>

      <footer className="site-footer">
        <div className="container">
          <p>
            {business?.name ?? "This site"} is a fictional business used for product demonstration.
            No real grooming services are offered.
          </p>
          <p className="footer-links">
            <a href="/staff">Staff sign in</a>
          </p>
        </div>
      </footer>
    </>
  );
}
