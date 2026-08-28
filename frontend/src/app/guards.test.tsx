import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useAuthStore, type Capabilities, NO_CAPABILITIES } from "@/stores/auth";

import { AuthGuard, RoleGuard } from "./guards";

/**
 * A real route table, not a catch-all. A guard that redirects has to land
 * somewhere other than itself, or the redirect re-mounts the guard and loops.
 */
function renderGuarded(guarded: ReactNode) {
  return render(
    <MemoryRouter initialEntries={["/protected"]}>
      <Routes>
        <Route path="/protected" element={guarded} />
        <Route path="/login" element={<p>login page</p>} />
        <Route path="/forbidden" element={<p>forbidden page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const SECRET = "secret content";

function Protected() {
  return <p>{SECRET}</p>;
}

function signIn(capabilities: Partial<Capabilities> = {}) {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: 1,
      email: "a@b.test",
      first_name: "A",
      last_name: "B",
      capabilities: { ...NO_CAPABILITIES, ...capabilities },
    },
  });
}

describe("AuthGuard", () => {
  it("shows a pending state, not the children, while the session resolves", () => {
    useAuthStore.setState({ status: "loading", user: null });

    renderGuarded(
      <AuthGuard>
        <Protected />
      </AuthGuard>,
    );

    // The previous implementation rendered children on the first pass while
    // user was still null, so the role check ran against undefined.
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
    expect(screen.getByText(/checking your session/i)).toBeInTheDocument();
  });

  it("waits in the idle state too", () => {
    useAuthStore.setState({ status: "idle", user: null });

    renderGuarded(
      <AuthGuard>
        <Protected />
      </AuthGuard>,
    );

    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("redirects an anonymous visitor to the login page", async () => {
    useAuthStore.setState({ status: "anonymous", user: null });

    renderGuarded(
      <AuthGuard>
        <Protected />
      </AuthGuard>,
    );

    expect(await screen.findByText("login page")).toBeInTheDocument();
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("renders children for an authenticated user", () => {
    signIn({ is_student: true });

    renderGuarded(
      <AuthGuard>
        <Protected />
      </AuthGuard>,
    );

    expect(screen.getByText(SECRET)).toBeInTheDocument();
  });
});

describe("RoleGuard", () => {
  it("waits rather than deciding while the session is loading", () => {
    useAuthStore.setState({ status: "loading", user: null });

    renderGuarded(
      <RoleGuard roles={["staff"]}>
        <Protected />
      </RoleGuard>,
    );

    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
    expect(screen.getByText(/checking your access/i)).toBeInTheDocument();
  });

  it("sends an anonymous visitor to login, not to forbidden", async () => {
    useAuthStore.setState({ status: "anonymous", user: null });

    renderGuarded(
      <RoleGuard roles={["staff"]}>
        <Protected />
      </RoleGuard>,
    );

    expect(await screen.findByText("login page")).toBeInTheDocument();
  });

  it("admits a user holding the required capability", () => {
    signIn({ is_staff: true });

    renderGuarded(
      <RoleGuard roles={["staff"]}>
        <Protected />
      </RoleGuard>,
    );

    expect(screen.getByText(SECRET)).toBeInTheDocument();
  });

  it("refuses a signed-in user without it", async () => {
    signIn({ is_student: true });

    renderGuarded(
      <RoleGuard roles={["staff"]}>
        <Protected />
      </RoleGuard>,
    );

    expect(await screen.findByText("forbidden page")).toBeInTheDocument();
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("admits when any one of several roles matches", () => {
    signIn({ is_landlord: true });

    renderGuarded(
      <RoleGuard roles={["staff", "landlord"]}>
        <Protected />
      </RoleGuard>,
    );

    expect(screen.getByText(SECRET)).toBeInTheDocument();
  });

  it("denies when the backend sends no capability block at all", async () => {
    useAuthStore.setState({
      status: "authenticated",
      user: {
        id: 1,
        email: "a@b.test",
        first_name: "A",
        last_name: "B",
        capabilities: undefined as never,
      },
    });

    renderGuarded(
      <RoleGuard roles={["staff"]}>
        <Protected />
      </RoleGuard>,
    );

    // An unknown capability must deny, not allow.
    expect(await screen.findByText("forbidden page")).toBeInTheDocument();
  });
});
