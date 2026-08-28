import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import ListingsRoute from "./ListingsRoute";
import { API, page, propertySummary } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Search, and specifically the empty search.
 *
 * "No results — try adjusting your filters" is a shrug that makes the student
 * clear one box at a time and wait for the network between each. These tests
 * are mostly about the alternative: asking the server which filter is
 * responsible, and saying so.
 */

/** Answers the list endpoint by looking at the filters, the way the API does. */
function listingsRespondingTo(
  match: (params: URLSearchParams) => ReturnType<typeof propertySummary>[],
) {
  return http.get(`${API}/properties/`, ({ request }) => {
    const params = new URL(request.url).searchParams;
    return HttpResponse.json(page(match(params)));
  });
}

describe("results", () => {
  it("renders a page of listings", async () => {
    server.use(
      listingsRespondingTo(() => [
        propertySummary({ id: 1, name: "Wendani Court" }),
        propertySummary({ id: 2, name: "Githurai Heights", slug: "githurai-heights" }),
      ]),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings" });

    expect(await screen.findByText("Wendani Court")).toBeInTheDocument();
    expect(screen.getByText("Githurai Heights")).toBeInTheDocument();
    expect(screen.getByText("2 listings")).toBeInTheDocument();
  });

  it("announces loading, rather than only drawing a skeleton", async () => {
    // A skeleton is invisible to a screen reader. Without this the page is
    // silent for as long as the network takes, which on campus wifi is the
    // part of the experience that actually needs narrating.
    server.use(listingsRespondingTo(() => [propertySummary()]));

    renderWithProviders(<ListingsRoute />, { route: "/listings" });

    expect(screen.getByRole("status")).toHaveTextContent(/loading listings/i);
    await screen.findByText("Wendani Court");
  });
});

describe("the empty search", () => {
  it("blames the platform, not the student, when no filters are set", async () => {
    // Nothing has been filtered, so there is nothing to adjust. Telling them
    // to adjust their filters here would be blaming them for the platform
    // being empty near their campus.
    server.use(listingsRespondingTo(() => []));

    renderWithProviders(<ListingsRoute />, { route: "/listings" });

    expect(await screen.findByText(/no landlord has published a listing/i)).toBeInTheDocument();
    expect(screen.queryByText(/adjust/i)).not.toBeInTheDocument();
  });

  it("names the single filter that is hiding everything", async () => {
    server.use(
      listingsRespondingTo((params) => (params.get("max_rent") ? [] : [propertySummary()])),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=6000" });

    expect(await screen.findByText(/nothing is under KES 6,000/i)).toBeInTheDocument();
  });

  it("says how many listings dropping it would show", async () => {
    server.use(
      listingsRespondingTo((params) =>
        params.get("max_rent")
          ? []
          : [propertySummary({ id: 1 }), propertySummary({ id: 2, slug: "two" })],
      ),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=6000" });

    expect(await screen.findByText(/would show 2 listings/i)).toBeInTheDocument();
  });

  it("offers to drop exactly that filter, and re-searches without it", async () => {
    server.use(
      listingsRespondingTo((params) =>
        params.get("max_rent") ? [] : [propertySummary({ name: "Wendani Court" })],
      ),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=6000" });

    await userEvent.click(
      await screen.findByRole("button", { name: /drop the maximum rent filter/i }),
    );

    expect(await screen.findByText("Wendani Court")).toBeInTheDocument();
  });

  it("does not pick one at random when several are jointly responsible", async () => {
    // Naming the first would be a guess dressed as a diagnosis. Both are
    // listed, with what each would recover.
    server.use(
      listingsRespondingTo((params) =>
        params.get("max_rent") && params.get("has_wifi") ? [] : [propertySummary()],
      ),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=6000&has_wifi=true" });

    expect(await screen.findByText(/no single filter is responsible/i)).toBeInTheDocument();
    const causes = screen.getByRole("list", { name: /filters worth dropping/i });
    expect(within(causes).getByText("maximum rent")).toBeInTheDocument();
    expect(within(causes).getByText("wifi")).toBeInTheDocument();
  });

  it("says so plainly when dropping any one filter would not help", async () => {
    // The honest answer is that the search is empty for a deeper reason.
    // Suggesting an adjustment here, having checked that none works, would be
    // sending the student on an errand we know is pointless.
    server.use(listingsRespondingTo(() => []));

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=6000&has_wifi=true" });

    expect(
      await screen.findByText(/even with any one filter dropped/i),
    ).toBeInTheDocument();
  });

  it("still shows the empty state when a probe fails", async () => {
    // A probe is a hint. One failing must never turn an empty result into an
    // error page -- the student's actual search succeeded and returned zero.
    let first = true;
    server.use(
      http.get(`${API}/properties/`, () => {
        if (first) {
          first = false;
          return HttpResponse.json(page([]));
        }
        return HttpResponse.json({ error: { code: "server_error" } }, { status: 500 });
      }),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=6000" });

    expect(await screen.findByText(/no listings match this search/i)).toBeInTheDocument();
  });
});

describe("filters and the URL", () => {
  it("reads the filters out of the link it was opened with", async () => {
    server.use(listingsRespondingTo(() => [propertySummary()]));

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=9000&has_wifi=true" });

    await screen.findByText("Wendani Court");
    expect(screen.getByLabelText("Maximum rent")).toHaveValue(9000);
    expect(screen.getByRole("checkbox", { name: "Wifi" })).toBeChecked();
    expect(screen.getByText("2 filters applied")).toBeInTheDocument();
  });

  it("shows applied filters as chips that say what removing them does", async () => {
    // The panel collapses on a phone, and a filter you cannot see is one you
    // forget you set -- which is how somebody concludes there is nothing near
    // their campus with last term's rent cap still applied.
    server.use(listingsRespondingTo(() => [propertySummary()]));

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=9000" });

    await screen.findByText("Wendani Court");
    expect(
      screen.getByRole("button", { name: /remove the maximum rent filter/i }),
    ).toBeInTheDocument();
  });

  it("clears a filter when its chip is pressed", async () => {
    server.use(listingsRespondingTo(() => [propertySummary()]));

    renderWithProviders(<ListingsRoute />, { route: "/listings?max_rent=9000" });

    await screen.findByText("Wendani Court");
    await userEvent.click(screen.getByRole("button", { name: /remove the maximum rent filter/i }));

    await waitFor(() => expect(screen.getByLabelText("Maximum rent")).toHaveValue(null));
  });
});

describe("when the request fails", () => {
  it("says what happened and offers a retry", async () => {
    server.use(
      http.get(`${API}/properties/`, () =>
        HttpResponse.json(
          { error: { code: "server_error", message: "boom", request_id: "abc" } },
          { status: 500 },
        ),
      ),
    );

    renderWithProviders(<ListingsRoute />, { route: "/listings" });

    expect(await screen.findByRole("alert")).toHaveTextContent(/did not load/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations with results", async () => {
    server.use(listingsRespondingTo(() => [propertySummary()]));

    const { container } = renderWithProviders(<ListingsRoute />, { route: "/listings" });
    await screen.findByText("Wendani Court");

    expect(await axe(container)).toHaveNoViolations();
  });

  it("has no violations when empty", async () => {
    server.use(listingsRespondingTo(() => []));

    const { container } = renderWithProviders(<ListingsRoute />, { route: "/listings" });
    await screen.findByText(/no listings match/i);

    expect(await axe(container)).toHaveNoViolations();
  });
});
