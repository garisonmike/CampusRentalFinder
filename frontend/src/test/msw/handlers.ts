import { HttpResponse, http } from "msw";

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

/** Default handlers: an anonymous visitor at a branded tenant. */
export const handlers = [
  http.get(`${API}/tenant/config/`, () => HttpResponse.json(tenantConfig)),
  http.get(`${API}/auth/me/`, () => HttpResponse.json(anonymousUser, { status: 401 })),
];
