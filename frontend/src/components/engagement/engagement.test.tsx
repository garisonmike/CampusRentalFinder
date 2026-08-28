import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import { InquiryForm } from "./InquiryForm";
import { SaveButton } from "./SaveButton";
import { API, page } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";
import { NO_CAPABILITIES, useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * Saving and asking, from a student's side.
 *
 * Both are the first thing on the site that writes something, so both are
 * about what happens when it does not work.
 */

function signIn() {
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
}

function signOut() {
  useAuthStore.setState({ status: "anonymous", user: null });
}

function savedProperty(
  overrides: Partial<Schemas["SavedProperty"]> = {},
): Schemas["SavedProperty"] {
  return {
    id: 1,
    property_slug: "wendani-court",
    property_name: "Wendani Court",
    property_town: "Kahawa",
    note: "",
    created_at: "2026-08-01T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => signOut());

describe("saving a listing", () => {
  it("offers a sign-in link rather than a button that will fail", async () => {
    // A button that appears to work and fails after the tap loses the listing
    // behind a redirect, which is the moment the student was closest to
    // deciding.
    renderWithProviders(<SaveButton slug="wendani-court" name="Wendani Court" />);

    expect(screen.getByRole("link", { name: /sign in to save/i })).toHaveAttribute(
      "href",
      "/login",
    );
  });

  it("saves, and says so", async () => {
    signIn();
    let saved: Schemas["SavedProperty"][] = [];
    server.use(
      http.get(`${API}/engagement/saved/`, () => HttpResponse.json(page(saved))),
      http.post(`${API}/engagement/saved/`, () => {
        saved = [savedProperty()];
        return HttpResponse.json(savedProperty(), { status: 201 });
      }),
    );

    renderWithProviders(<SaveButton slug="wendani-court" name="Wendani Court" />);

    await userEvent.click(await screen.findByRole("button", { name: /save wendani court/i }));

    expect(
      await screen.findByRole("button", { name: /remove wendani court/i }),
    ).toBeInTheDocument();
  });

  it("names the direction of the press, not just the state", async () => {
    // "Saved" on a button that unsaves is a distinction only a sighted user
    // catches, from an icon.
    signIn();
    server.use(
      http.get(`${API}/engagement/saved/`, () => HttpResponse.json(page([savedProperty()]))),
    );

    renderWithProviders(<SaveButton slug="wendani-court" name="Wendani Court" />);

    expect(
      await screen.findByRole("button", { name: /remove wendani court from your saved/i }),
    ).toBeInTheDocument();
  });

  it("says what went wrong instead of silently doing nothing", async () => {
    signIn();
    server.use(
      http.get(`${API}/engagement/saved/`, () => HttpResponse.json(page([]))),
      http.post(`${API}/engagement/saved/`, () =>
        HttpResponse.json(
          { error: { code: "throttled", message: "Too many requests." } },
          { status: 429 },
        ),
      ),
    );

    renderWithProviders(<SaveButton slug="wendani-court" name="Wendani Court" />);

    await userEvent.click(await screen.findByRole("button", { name: /save wendani court/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("asking the landlord", () => {
  it("states the contact-details rule before anything is typed", async () => {
    // Learning it from a red box under a paragraph you have just written
    // reads as an obstruction. Said up front, with the reason, it reads as
    // what it is.
    signIn();

    renderWithProviders(<InquiryForm unitId={10} unitLabel="Bedsitters" />);

    expect(screen.getByText(/do not include a phone number/i)).toBeInTheDocument();
    expect(screen.getByText(/confirm your stay later/i)).toBeInTheDocument();
  });

  it("shows the API's own field error against the field", async () => {
    signIn();
    server.use(
      http.post(`${API}/engagement/inquiries/`, () =>
        HttpResponse.json(
          {
            error: {
              code: "validation_error",
              message: "Invalid.",
              field_errors: { message: ["Remove the phone number before sending."] },
            },
          },
          { status: 400 },
        ),
      ),
    );

    renderWithProviders(<InquiryForm unitId={10} unitLabel="Bedsitters" />);

    await userEvent.type(screen.getByLabelText(/ask about bedsitters/i), "Call me on 0712");
    await userEvent.click(screen.getByRole("button", { name: /send question/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/remove the phone number/i);
  });

  it("promises only what happened", async () => {
    // Not "the landlord will reply shortly" -- a promise made on somebody
    // else's behalf by a platform with no way to keep it.
    signIn();
    server.use(
      http.post(`${API}/engagement/inquiries/`, () => HttpResponse.json({}, { status: 201 })),
    );

    renderWithProviders(<InquiryForm unitId={10} unitLabel="Bedsitters" />);

    await userEvent.type(screen.getByLabelText(/ask about bedsitters/i), "Is it still free?");
    await userEvent.click(screen.getByRole("button", { name: /send question/i }));

    await waitFor(() =>
      expect(screen.getByText("Your question was sent.")).toBeInTheDocument(),
    );
    expect(screen.getByText(/not obliged to answer/i)).toBeInTheDocument();
  });

  it("asks a signed-out visitor to sign in", async () => {
    renderWithProviders(<InquiryForm unitId={10} unitLabel="Bedsitters" />);

    expect(screen.getByRole("link", { name: /sign in/i })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    signIn();

    const { container } = renderWithProviders(
      <InquiryForm unitId={10} unitLabel="Bedsitters" />,
    );

    expect(await axe(container)).toHaveNoViolations();
  });
});
