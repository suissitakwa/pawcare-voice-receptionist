const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface BusinessHours {
  weekday: number;
  opens_at: string;
  closes_at: string;
}

export interface Business {
  name: string;
  tagline: string;
  about: string;
  timezone: string;
  contact_email: string;
  contact_phone: string;
  address: string;
  is_demo: boolean;
  hours: BusinessHours[];
}

export interface SizeCategory {
  slug: string;
  label: string;
  description: string;
  requires_staff_assessment: boolean;
}

export interface ServiceOption {
  size: string;
  duration_minutes: number;
  price_cents: number;
}

export interface Service {
  slug: string;
  name: string;
  description: string;
  options: ServiceOption[];
}

export interface Catalog {
  size_categories: SizeCategory[];
  services: Service[];
}

export interface Faq {
  slug: string;
  question: string;
  answer: string;
}

export interface SiteData {
  business: Business;
  catalog: Catalog;
  faqs: Faq[];
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json();
}

export async function fetchSiteData(signal?: AbortSignal): Promise<SiteData> {
  const [business, catalog, faqs] = await Promise.all([
    getJson<Business>("/api/v1/business", signal),
    getJson<Catalog>("/api/v1/services", signal),
    getJson<Faq[]>("/api/v1/faqs", signal),
  ]);
  // A misconfigured host can answer unknown paths with a 200 HTML page or other shapes.
  if (!Array.isArray(catalog.services) || !Array.isArray(catalog.size_categories) || !Array.isArray(faqs)) {
    throw new Error("Unexpected response shape from the API");
  }
  return { business, catalog, faqs };
}
