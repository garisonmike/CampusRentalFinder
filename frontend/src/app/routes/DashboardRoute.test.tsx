import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import DashboardRoute from "./DashboardRoute";
import { API, page } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";
import { NO_CAPABILITIES, useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * The student's own dashboard, which is where the tenancy-currency contract
 * note has to hold or the page lies about where somebody lives.
 */

function tenancy(overrides: Partial<Schemas["Tenancy"]> = {}): Schemas["Tenancy"] {
  return {
    id: 1,
    unit: 10,
    unit_label: "Bedsitters",
    property_name: "Wendani Court",
    property_slug: "wendani-court",
    tenant_name: "Wanjiku Kamau",
    start_date: "2026-01-10",
    end_date: null,
    monthly_rent_kes: "8500.00",
    status: "confirmed",
    currency: "current",
    terminated_early: false,
    confirmation_source: "landlord",
    confirmed_at: "2026-01-11T08:00:00Z",
    created_at: "2026-01-10T08:00:00Z",
    review_eligible_at: "2026-02-09T08:00:00Z",
    ...overrides,
  } as Schemas["Tenancy"];
}

function serve({
  tenancies = [tenancy()],
  applications = [] as Schemas["Application"][],
  inquiries = [] as Schemas["Inquiry"][],
}: {
  tenancies?: Schemas["Tenancy"][];
  applications?: Schemas["Application"][];
  inquiries?: Schemas["Inquiry"][];
} = {}) {
  server.use(
    http.get(`${API}/tenancies/`, ({ request }) => {
      // The route must ask by query parameter -- currency is derived, not
      // stored, so a request that does not ask gets everything.
      const currency = new URL(request.url).searchParams.get("currency");
      return HttpResponse.json(page(currency === "current" ? tenancies : []));
    }),
    http.get(`${API}/tenancies/applications/`, () => HttpResponse.json(page(applications))),
    http.get(`${API}/engagement/inquiries/`, () => HttpResponse.json(page(inquiries))),
  );
}

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: 1,
      email: "wanjiku@students.ku.ac.ke",
      first_name: "Wanjiku",
      last_name: "Kamau",
      capabilities: { ...NO_CAPABILITIES, is_student: true },
    },
  });
});

describe("current tenancy", () => {
  it("asks for currency by query parameter", async () => {
    // If it filtered on a status value meaning "active" -- there is none --
    // the API would answer with an empty page rather than an error, and the
    // student's own home would silently vanish from their dashboard.
    serve();

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText("Wendani Court")).toBeInTheDocument();
  });

  it("renders a null end date as open-ended, not as unknown", async () => {
    // The most likely misread in the whole contract, which is why the API
    // states it three times. "Unknown" would suggest a record with a gap in
    // it; the truth is an arrangement with no agreed end.
    serve({ tenancies: [tenancy({ end_date: null })] });

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText(/no agreed end date/i)).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("shows a real end date when there is one", async () => {
    serve({ tenancies: [tenancy({ end_date: "2026-11-30" })] });

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText(/30 Nov 2026/)).toBeInTheDocument();
  });

  it("explains an empty state rather than leaving a blank", async () => {
    serve({ tenancies: [] });

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText(/no current tenancy on record/i)).toBeInTheDocument();
  });
});

describe("questions asked", () => {
  it("says who replied", async () => {
    // The student is owed the knowledge that a person answered, and which:
    // a caretaker and the owner are different answers to the same question.
    serve({
      inquiries: [
        {
          id: 1,
          unit: 10,
          unit_label: "Bedsitters",
          property_name: "Wendani Court",
          property_slug: "wendani-court",
          sender_name: "Wanjiku K.",
          message: "Is it still free?",
          preferred_move_in_date: null,
          status: "answered",
          response: "Yes, two rooms.",
          responded_by_name: "Joseph, the caretaker",
          responded_at: "2026-08-02T09:00:00Z",
          created_at: "2026-08-01T09:00:00Z",
        },
      ],
    });

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText(/Joseph, the caretaker replied/)).toBeInTheDocument();
  });

  it("does not imply an answer is coming", async () => {
    serve({
      inquiries: [
        {
          id: 1,
          unit: 10,
          unit_label: "Bedsitters",
          property_name: "Wendani Court",
          property_slug: "wendani-court",
          sender_name: "Wanjiku K.",
          message: "Is it still free?",
          preferred_move_in_date: null,
          status: "sent",
          response: "",
          responded_by_name: null,
          responded_at: null,
          created_at: "2026-08-01T09:00:00Z",
        },
      ],
    });

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText(/not obliged to answer/i)).toBeInTheDocument();
  });
});

describe("one failing section", () => {
  it("does not take the others down with it", async () => {
    server.use(
      http.get(`${API}/tenancies/`, () => HttpResponse.json(page([tenancy()]))),
      http.get(`${API}/tenancies/applications/`, () =>
        HttpResponse.json({ error: { code: "server_error" } }, { status: 500 }),
      ),
      http.get(`${API}/engagement/inquiries/`, () => HttpResponse.json(page([]))),
    );

    renderWithProviders(<DashboardRoute />);

    expect(await screen.findByText("Wendani Court")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve();

    const { container } = renderWithProviders(<DashboardRoute />);
    await screen.findByText("Wendani Court");

    expect(await axe(container)).toHaveNoViolations();
  });
});
