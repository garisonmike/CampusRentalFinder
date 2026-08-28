import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import PortalRoute from "./PortalRoute";
import { API, page } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";
import { NO_CAPABILITIES, useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * The portal. Two things here are consequences rather than features, and both
 * are stated before the button that causes them: silence confirms a claim, and
 * accepting an application creates the tenancy outright.
 */

function signInAs(capabilities: Partial<Schemas["User"]["capabilities"]>) {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: 2,
      email: "grace@example.test",
      first_name: "Grace",
      last_name: "Njoroge",
      capabilities: { ...NO_CAPABILITIES, ...capabilities },
    },
  });
}

function claim(overrides: Partial<Schemas["TenancyClaim"]> = {}): Schemas["TenancyClaim"] {
  return {
    id: 1,
    unit: 10,
    unit_label: "Bedsitters",
    property_name: "Wendani Court",
    claimant_name: "Wanjiku Kamau",
    start_date: "2026-01-10",
    end_date: null,
    monthly_rent_kes: "8500.00",
    status: "pending",
    confirmation_deadline: "2026-09-10T00:00:00Z",
    created_at: "2026-08-27T09:00:00Z",
    ...overrides,
  } as Schemas["TenancyClaim"];
}

function application(
  overrides: Partial<Schemas["Application"]> = {},
): Schemas["Application"] {
  return {
    id: 5,
    unit: 10,
    unit_label: "Bedsitters",
    property_name: "Wendani Court",
    property_slug: "wendani-court",
    applicant_name: "Wanjiku Kamau",
    move_in_date: "2026-09-01",
    intended_months: 8,
    message: "I am starting second year.",
    status: "submitted",
    decision_note: "",
    decided_at: null,
    created_at: "2026-08-20T09:00:00Z",
    ...overrides,
  } as Schemas["Application"];
}

function inquiry(overrides: Partial<Schemas["Inquiry"]> = {}): Schemas["Inquiry"] {
  return {
    id: 7,
    unit: 10,
    unit_label: "Bedsitters",
    property_name: "Wendani Court",
    property_slug: "wendani-court",
    sender_name: "Wanjiku K.",
    message: "Is the water reliable?",
    preferred_move_in_date: null,
    status: "sent",
    response: "",
    responded_by_name: null,
    responded_at: null,
    created_at: "2026-08-25T09:00:00Z",
    ...overrides,
  };
}

function serve({
  claims = [claim()],
  applications = [application()],
  inquiries = [inquiry()],
}: {
  claims?: Schemas["TenancyClaim"][];
  applications?: Schemas["Application"][];
  inquiries?: Schemas["Inquiry"][];
} = {}) {
  server.use(
    http.get(`${API}/tenancies/claims/`, () => HttpResponse.json(page(claims))),
    http.get(`${API}/tenancies/applications/`, () => HttpResponse.json(page(applications))),
    http.get(`${API}/engagement/inquiries/`, () => HttpResponse.json(page(inquiries))),
  );
}

beforeEach(() => signInAs({ is_landlord: true }));

describe("claims", () => {
  it("says silence will confirm, before the buttons", async () => {
    // A landlord who ignores this page for two weeks has agreed to every
    // claim on it. A page that let them find that out afterwards would have
    // made the decision for them.
    serve();

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByText(/silence is a signal, not a veto/i)).toBeInTheDocument();
    expect(await screen.findByText(/confirmed automatically after/i)).toBeInTheDocument();
  });

  it("offers typed dispute reasons rather than a free-text box", async () => {
    // An untyped dispute cannot be routed and can therefore only go to a
    // human, which is the queue this exists to keep short.
    serve();

    renderWithProviders(<PortalRoute />);

    const select = await screen.findByLabelText(/or dispute it/i);
    expect(select).toHaveValue("");
    expect(
      screen.getByRole("option", { name: /this person never lived here/i }),
    ).toBeInTheDocument();
  });

  it("will not send a dispute with no reason chosen", async () => {
    serve();

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByRole("button", { name: /^dispute$/i })).toBeDisabled();
  });

  it("confirms a stay", async () => {
    serve();
    let confirmed = false;
    server.use(
      http.post(`${API}/tenancies/claims/1/confirm/`, () => {
        confirmed = true;
        return HttpResponse.json({}, { status: 201 });
      }),
    );

    renderWithProviders(<PortalRoute />);

    await userEvent.click(await screen.findByRole("button", { name: /yes, they lived here/i }));

    expect(confirmed).toBe(true);
  });
});

describe("applications", () => {
  it("says accepting creates the tenancy outright, before the button", async () => {
    // No confirmation window and no dispute surface behind it (ADR-004
    // §1.1). Discovering that afterwards is discovering it too late.
    serve();

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByText(/no confirmation step after this/i)).toBeInTheDocument();
  });

  it("asks for a reason, and says why one is owed", async () => {
    serve();

    renderWithProviders(<PortalRoute />);

    expect(
      await screen.findByText(/gives them nothing to act on/i),
    ).toBeInTheDocument();
  });
});

describe("replying to a question", () => {
  it("states the contact-details rule in this direction too", async () => {
    // "Call me on 07..." from a landlord is the same leak with the same
    // consequence, and it is the reply people reach for first.
    serve();

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByText(/rejected here too/i)).toBeInTheDocument();
    expect(screen.getByText(/invite them to apply instead/i)).toBeInTheDocument();
  });

  it("will not send an empty reply", async () => {
    serve();

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByRole("button", { name: /send reply/i })).toBeDisabled();
  });
});

describe("a caretaker", () => {
  it("is told what they are and what they cannot do", async () => {
    // Better than discovering it from a 403 on the one action they cannot
    // take. A caretaker can confirm a stay; speaking for the business in
    // public is the owner's own act (ADR-003).
    signInAs({ is_landlord: false, manages_properties: [1] });
    serve();

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByText(/you are a caretaker here/i)).toBeInTheDocument();
    expect(screen.getByText(/owner's own act/i)).toBeInTheDocument();
  });

  it("can still confirm a stay", async () => {
    signInAs({ is_landlord: false, manages_properties: [1] });
    serve();

    renderWithProviders(<PortalRoute />);

    expect(
      await screen.findByRole("button", { name: /yes, they lived here/i }),
    ).toBeEnabled();
  });
});

describe("nothing waiting", () => {
  it("says so rather than showing three empty boxes", async () => {
    serve({ claims: [], applications: [], inquiries: [] });

    renderWithProviders(<PortalRoute />);

    expect(await screen.findByText("Nobody is waiting on you.")).toBeInTheDocument();
    expect(screen.getByText("No applications yet.")).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve();

    const { container } = renderWithProviders(<PortalRoute />);
    await screen.findByText(/silence is a signal/i);

    expect(await axe(container)).toHaveNoViolations();
  });
});
