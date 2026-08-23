import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";

import { App } from "@/app/App";

/**
 * Accessibility and landmark structure of the shell.
 *
 * Asserted from the start rather than retrofitted: a skip link and correct
 * landmarks are cheap now and expensive once every page assumes the current
 * markup.
 */
describe("app shell", () => {
  it("renders the landmark structure", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /main/i })).toBeInTheDocument();
  });

  it("offers a skip link as the first focusable element", async () => {
    render(<App />);

    const skipLink = await screen.findByRole("link", { name: /skip to main content/i });
    expect(skipLink).toHaveAttribute("href", "#main");

    // The target must be focusable for the jump to move keyboard focus.
    await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("tabindex", "-1"));
  });

  it("has exactly one level-one heading", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    });
  });

  it("shows the sign-in link while anonymous and no dashboard link", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /sign in/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole("link", { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    const { container } = render(<App />);

    await waitFor(() => expect(screen.getByRole("main")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1),
    );

    // colour-contrast needs a real canvas to sample rendered pixels, which
    // jsdom does not provide. It is checked in the browser, not here.
    await expect(
      axe(container, { rules: { "color-contrast": { enabled: false } } }),
    ).resolves.toHaveNoViolations();
  }, 20_000);
});
