import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import VacancyRoute from "./VacancyRoute";
import { API, propertyDetail, unitSummary } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";
import { NO_CAPABILITIES, useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * The page the prompt email lands on.
 *
 * The job was held for a round because this screen did not exist. What is
 * asserted here is the property that made it worth holding: a landlord who
 * arrives from that email can fix the thing it asked about without hunting
 * for it.
 */

function serve(properties: Schemas["PropertyDetail"][]) {
  server.use(http.get(`${API}/properties/manage/`, () => HttpResponse.json(properties)));
}

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: 2,
      email: "grace@example.test",
      first_name: "Grace",
      last_name: "Njoroge",
      capabilities: { ...NO_CAPABILITIES, is_landlord: true },
    },
  });
});

describe("what it puts first", () => {
  it("leads with the counts the email complained about", async () => {
    // A landlord arriving from that message is here to fix three rooms, not
    // to browse their portfolio.
    serve([
      propertyDetail({
        units: [
          unitSummary({ id: 1, label: "Fresh block", vacancy_freshness: "fresh", vacancy_age_days: 1 }),
          unitSummary({ id: 2, label: "Stale block", vacancy_freshness: "stale", vacancy_age_days: 120 }),
        ],
      }),
    ]);

    renderWithProviders(<VacancyRoute />);

    const headings = await screen.findAllByRole("heading", { level: 2 });
    expect(headings[0]).toHaveTextContent("Worth updating first");

    const stale = screen.getByRole("heading", { name: /stale block/i }).closest("article");
    expect(stale).toHaveTextContent(/last confirmed 4 months ago/i);
  });

  it("says nothing is out of date rather than showing an empty box", async () => {
    serve([
      propertyDetail({
        units: [unitSummary({ vacancy_freshness: "fresh", vacancy_age_days: 1 })],
      }),
    ]);

    renderWithProviders(<VacancyRoute />);

    expect(await screen.findByText(/nothing is out of date/i)).toBeInTheDocument();
  });

  it("distinguishes never-confirmed from confirmed long ago", async () => {
    serve([
      propertyDetail({
        units: [unitSummary({ vacancy_freshness: "unknown", vacancy_age_days: null })],
      }),
    ]);

    renderWithProviders(<VacancyRoute />);

    expect(await screen.findByText("Never confirmed")).toBeInTheDocument();
  });
});

describe("confirming", () => {
  it("offers a button for the unchanged case, which is the common one", async () => {
    // A page offering only "update" leaves the landlord with nothing to press
    // when nothing has changed -- and "still 6 free" is new information: it is
    // a fresh statement of the same number.
    serve([
      propertyDetail({
        units: [unitSummary({ vacant_count: 6, vacancy_freshness: "stale", vacancy_age_days: 90 })],
      }),
    ]);

    renderWithProviders(<VacancyRoute />);

    expect(await screen.findByRole("button", { name: "Still 6 free" })).toBeInTheDocument();
  });

  it("switches to update once the number changes", async () => {
    serve([
      propertyDetail({
        units: [unitSummary({ vacant_count: 6, total_count: 40, vacancy_freshness: "stale" })],
      }),
    ]);

    renderWithProviders(<VacancyRoute />);

    const input = await screen.findByLabelText(/rooms free of 40/i);
    await userEvent.clear(input);
    await userEvent.type(input, "3");

    expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
  });

  it("sends the count to the one endpoint that stamps it", async () => {
    let body: unknown = null;
    serve([
      propertyDetail({
        slug: "wendani-court",
        units: [unitSummary({ id: 10, vacant_count: 6, vacancy_freshness: "stale" })],
      }),
    ]);
    server.use(
      http.patch(
        `${API}/properties/manage/wendani-court/units/10/vacancy/`,
        async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({ vacant_count: 6 });
        },
      ),
    );

    renderWithProviders(<VacancyRoute />);

    await userEvent.click(await screen.findByRole("button", { name: "Still 6 free" }));

    await waitFor(() => expect(body).toEqual({ vacant_count: 6 }));
  });

  it("confirms in words that the count is now current", async () => {
    serve([
      propertyDetail({
        slug: "wendani-court",
        units: [unitSummary({ id: 10, vacant_count: 6, vacancy_freshness: "stale" })],
      }),
    ]);
    server.use(
      http.patch(`${API}/properties/manage/wendani-court/units/10/vacancy/`, () =>
        HttpResponse.json({ vacant_count: 6 }),
      ),
    );

    renderWithProviders(<VacancyRoute />);

    await userEvent.click(await screen.findByRole("button", { name: "Still 6 free" }));

    expect(await screen.findByText(/students will see it as current/i)).toBeInTheDocument();
  });

  it("says what went wrong when the API refuses", async () => {
    serve([
      propertyDetail({
        slug: "wendani-court",
        units: [unitSummary({ id: 10, vacant_count: 6, vacancy_freshness: "stale" })],
      }),
    ]);
    server.use(
      http.patch(`${API}/properties/manage/wendani-court/units/10/vacancy/`, () =>
        HttpResponse.json(
          { error: { code: "permission_denied", message: "Not yours." } },
          { status: 403 },
        ),
      ),
    );

    renderWithProviders(<VacancyRoute />);

    await userEvent.click(await screen.findByRole("button", { name: "Still 6 free" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("with nothing listed", () => {
  it("says what to do first", async () => {
    serve([]);

    renderWithProviders(<VacancyRoute />);

    expect(await screen.findByText(/no rooms listed yet/i)).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve([propertyDetail({ units: [unitSummary({ vacancy_freshness: "stale" })] })]);

    const { container } = renderWithProviders(<VacancyRoute />);
    await screen.findByRole("heading", { name: /worth updating first/i });

    expect(await axe(container)).toHaveNoViolations();
  });
});
