import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuthStore, type Role } from "@/stores/auth";

/**
 * Requires a session.
 *
 * Renders nothing while the session is still resolving. The previous
 * implementation rendered its children on the first pass while `user` was
 * still null, so the role check ran against undefined and let everyone
 * through. Waiting is the whole point of a guard.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  if (status === "idle" || status === "loading") {
    return <PendingScreen label="Checking your session" />;
  }

  if (status === "anonymous") {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

/**
 * Requires at least one of `roles`, on top of a session.
 *
 * Capabilities come from the backend (ADR-003); the client never derives them
 * from a model shape. An unknown capability denies.
 */
export function RoleGuard({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const status = useAuthStore((state) => state.status);
  const hasRole = useAuthStore((state) => state.hasRole);

  if (status === "idle" || status === "loading") {
    return <PendingScreen label="Checking your access" />;
  }

  if (status === "anonymous") {
    return <Navigate to="/login" replace />;
  }

  if (!roles.some((role) => hasRole(role))) {
    return <Navigate to="/forbidden" replace />;
  }

  return <>{children}</>;
}

function PendingScreen({ label }: { label: string }) {
  return (
    <output aria-live="polite" className="block p-8 text-muted-foreground">
      {label}…
    </output>
  );
}
