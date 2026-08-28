import { HttpResponse, http } from "msw";

import type { Paginated, PropertySummary } from "@/api/types";

export const API = "http://api.test/api/v1";

export const tenantConfig = {
  subdomain: "kyu",
  name: "Kenyatta University",
  display_name: "KyU",
  logo_url: null,
  favicon_url: null,
  theme: {
    primary: "210 90% 40%",
    secondary: "30 50% 40%",
    accent: "210 90% 95%",
  },
};

export const anonymousUser = { detail: "Authentication credentials were not provided." };

export const studentUser = {
  id: 1,
  email: "wanjiku@students.ku.ac.ke",
  first_name: "Wanjiku",
  last_name: "Kamau",
  capabilities: {
    is_student: true,
    is_landlord: false,
    is_staff: false,
    manages_properties: [],
  },
};

export const staffUser = {
  ...studentUser,
  id: 2,
  email: "ops@example.test",
  capabilities: { ...studentUser.capabilities, is_staff: true },
};

/**
 * A property as the search endpoint sends it.
 *
 * Built from the generated schema type, so a backend field rename breaks the
 * fixtures at compile time rather than leaving every test green against a
 * shape the API stopped sending.
 */
export function propertySummary(
  overrides: Partial<PropertySummary> = {},
): PropertySummary {
  return {
    id: 1,
    name: "Wendani Court",
    slug: "wendani-court",
    property_type: "bedsitter",
    county: "nairobi",
    town: "Kahawa",
    estate: "Kahawa Wendani",
    landmark: "opposite Naivas",
    latitude: null,
    longitude: null,
    has_water_tank: true,
    has_borehole: false,
    has_backup_power: false,
    has_perimeter_wall: true,
    has_security_guard: true,
    has_wifi: true,
    caretaker_on_site: true,
    published_at: "2026-06-01T09:00:00Z",
    cheapest_rent_kes: "8500.00",
    cover_photo_url: null,
    ...overrides,
  };
}

/** One page of results, with the whole envelope. */
export function page<T>(results: T[], overrides: Partial<Paginated<T>> = {}): Paginated<T> {
  return {
    count: results.length,
    page: 1,
    page_size: 20,
    total_pages: results.length === 0 ? 0 : 1,
    next: null,
    previous: null,
    results,
    ...overrides,
  };
}

/** Default handlers: an anonymous visitor at a branded tenant. */
export const handlers = [
  http.get(`${API}/tenant/config/`, () => HttpResponse.json(tenantConfig)),
  http.get(`${API}/auth/me/`, () => HttpResponse.json(anonymousUser, { status: 401 })),
];
