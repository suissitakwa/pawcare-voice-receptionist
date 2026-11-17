// Which business this page is for. A business's own pages live under /b/{slug}; the pages at
// the root serve the deployment's own business (the demo).

const SLUG_PATH = /^\/b\/([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)(?=\/|$)/;

export function businessSlug(pathname: string = window.location.pathname): string | null {
  return SLUG_PATH.exec(pathname)?.[1] ?? null;
}

/** The page path without the /b/{slug} prefix, e.g. "/staff" or "/". */
export function pagePath(pathname: string = window.location.pathname): string {
  const match = SLUG_PATH.exec(pathname);
  return match ? pathname.slice(match[0].length) || "/" : pathname;
}

/** Where this business's pages start: "/b/{slug}" or "" at the root. */
export function siteRoot(slug: string | null = businessSlug()): string {
  return slug ? `/b/${slug}` : "";
}

/** A public API path for this page's business, e.g. publicApi("/services"). */
export function publicApi(path: string, slug: string | null = businessSlug()): string {
  return slug ? `/api/v1/b/${slug}${path}` : `/api/v1${path}`;
}
