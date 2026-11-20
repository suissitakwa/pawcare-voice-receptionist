/**
 * Each kind of business gets its own look: a mark (a paw, scissors, a barber pole), a colour
 * scheme from styles.css (`[data-industry]`), a browser-tab icon, and a default headline.
 * Pet grooming is the original look and the fallback.
 */

const MARKS: Record<string, React.ReactNode> = {
  pet_grooming: (
    <>
      <ellipse cx="16" cy="21" rx="6.5" ry="5.5" />
      <circle cx="8.5" cy="14.5" r="2.6" />
      <circle cx="13" cy="9.5" r="2.6" />
      <circle cx="19" cy="9.5" r="2.6" />
      <circle cx="23.5" cy="14.5" r="2.6" />
    </>
  ),
  // Open scissors: two rings and crossed blades.
  hair_salon: (
    <>
      <circle cx="9" cy="23" r="4.2" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <circle cx="23" cy="23" r="4.2" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <path d="M11.8 20.2 L24.5 4.5 L26 5.8 L13.9 21.7 Z" />
      <path d="M20.2 20.2 L7.5 4.5 L6 5.8 L18.1 21.7 Z" />
      <circle cx="16" cy="14.6" r="1.4" />
    </>
  ),
  // A barber pole: cap, striped tube, base.
  barbershop: (
    <>
      <rect x="11" y="3" width="10" height="3" rx="1.5" />
      <rect x="11" y="26" width="10" height="3" rx="1.5" />
      <rect x="12.5" y="7" width="7" height="18" rx="1" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12.5 10 L19.5 7 L19.5 9.5 L12.5 12.5 Z" />
      <path d="M12.5 15.5 L19.5 12.5 L19.5 15 L12.5 18 Z" />
      <path d="M12.5 21 L19.5 18 L19.5 20.5 L12.5 23.5 Z" />
    </>
  ),
};

// The tab icon's background, matching each scheme's --brand.
const ICON_BACKGROUNDS: Record<string, string> = {
  pet_grooming: "#14534f",
  hair_salon: "#5b2a4a",
  barbershop: "#1d2a44",
};

const HEADLINES: Record<string, string> = {
  pet_grooming: "One groomer. One dog at a time.",
  hair_salon: "Cuts, color and care, by appointment.",
  barbershop: "Sharp cuts, no waiting around.",
};

function known(industry: string | undefined): string {
  return industry && industry in MARKS ? industry : "pet_grooming";
}

export function IndustryMark({ industry }: { industry?: string }) {
  return (
    <svg className="paw" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      {MARKS[known(industry)]}
    </svg>
  );
}

export function defaultHeadline(industry?: string): string {
  return HEADLINES[known(industry)];
}

/** Applies the colour scheme and tab icon for this kind of business. */
export function applyIndustryLook(industry?: string): void {
  const key = known(industry);
  document.documentElement.dataset.industry = key;
  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!icon) return;
  if (key === "pet_grooming") {
    icon.href = "/favicon.svg";
    return;
  }
  // Drawn from the same mark as the page, so the tab matches what's on screen.
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  const shapes = document.querySelector(".brand .paw")?.innerHTML ?? "";
  svg.innerHTML = `<rect width="32" height="32" rx="8" fill="${ICON_BACKGROUNDS[key]}"/><g fill="#ffffff" color="#ffffff">${shapes}</g>`;
  icon.href = `data:image/svg+xml,${encodeURIComponent(svg.outerHTML)}`;
}
