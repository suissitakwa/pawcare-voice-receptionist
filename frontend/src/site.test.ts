import { describe, expect, it } from "vitest";
import { businessSlug, pagePath, publicApi, siteRoot } from "./site";

describe("site addresses", () => {
  it("reads the business from /b/{slug}", () => {
    expect(businessSlug("/b/happy-tails")).toBe("happy-tails");
    expect(businessSlug("/b/happy-tails/staff")).toBe("happy-tails");
    expect(businessSlug("/")).toBeNull();
    expect(businessSlug("/staff")).toBeNull();
    expect(businessSlug("/b/")).toBeNull();
    expect(businessSlug("/b/Not_A_Slug")).toBeNull();
    expect(businessSlug("/bakery")).toBeNull();
  });

  it("gives the page path without the business prefix", () => {
    expect(pagePath("/b/happy-tails")).toBe("/");
    expect(pagePath("/b/happy-tails/")).toBe("/");
    expect(pagePath("/b/happy-tails/staff")).toBe("/staff");
    expect(pagePath("/staff")).toBe("/staff");
  });

  it("builds API paths and links for the business, or the demo at the root", () => {
    expect(publicApi("/services", "happy-tails")).toBe("/api/v1/b/happy-tails/services");
    expect(publicApi("/services", null)).toBe("/api/v1/services");
    expect(siteRoot("happy-tails")).toBe("/b/happy-tails");
    expect(siteRoot(null)).toBe("");
  });
});
