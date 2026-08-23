import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { API, tenantConfig } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";

import { TenantThemeProvider, useTenant } from "./TenantThemeProvider";

function Probe() {
  const { config, status } = useTenant();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="name">{config?.display_name ?? "none"}</span>
    </div>
  );
}

const readToken = (name: string) => document.documentElement.style.getPropertyValue(name);

describe("TenantThemeProvider", () => {
  it("applies the fetched palette to :root", async () => {
    render(
      <TenantThemeProvider>
        <Probe />
      </TenantThemeProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));

    expect(readToken("--primary")).toBe("210 90% 40%");
    expect(readToken("--accent")).toBe("210 90% 95%");
    // Derived, not fetched.
    expect(readToken("--ring")).toBe("210 90% 40%");
    expect(readToken("--primary-foreground")).toBe("0 0% 100%");
  });

  it("exposes the tenant's display name", async () => {
    render(
      <TenantThemeProvider>
        <Probe />
      </TenantThemeProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("KyU"));
  });

  it("falls back to the stylesheet defaults when there is no tenant", async () => {
    server.use(
      http.get(`${API}/tenant/config/`, () =>
        HttpResponse.json({ detail: "No tenant" }, { status: 404 }),
      ),
    );

    render(
      <TenantThemeProvider>
        <Probe />
      </TenantThemeProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("default"));

    // No inline override, so index.css wins. An unbranded page is a working page.
    expect(readToken("--primary")).toBe("");
  });

  it("paints from cache on the next visit rather than flashing unthemed", async () => {
    localStorage.setItem(`tenant-config:${window.location.host}`, JSON.stringify(tenantConfig));

    render(
      <TenantThemeProvider>
        <Probe />
      </TenantThemeProvider>,
    );

    // Applied during the first render pass, before any network call resolves.
    expect(readToken("--primary")).toBe("210 90% 40%");
    expect(screen.getByTestId("status")).toHaveTextContent("ready");

    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("KyU"));
  });

  it("survives a corrupt cache entry", async () => {
    localStorage.setItem(`tenant-config:${window.location.host}`, "{not json");

    render(
      <TenantThemeProvider>
        <Probe />
      </TenantThemeProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
  });
});
