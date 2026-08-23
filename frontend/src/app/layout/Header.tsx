import { Link, NavLink } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth";
import { useTenant } from "@/theme/TenantThemeProvider";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "rounded-md px-3 py-2 text-sm font-medium",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
    isActive ? "text-primary" : "text-foreground hover:text-primary",
  ].join(" ");

export function Header() {
  const { config } = useTenant();
  const status = useAuthStore((state) => state.status);
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);

  return (
    <header className="border-b">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
        <Link
          to="/"
          className="flex items-center gap-2 font-semibold text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {config?.logo_url ? (
            <img src={config.logo_url} alt="" aria-hidden="true" className="h-7 w-auto" />
          ) : null}
          <span>{config?.display_name ?? "CampusRentalFinder"}</span>
        </Link>

        <nav aria-label="Main">
          <ul className="flex items-center gap-1">
            <li>
              <NavLink to="/" end className={navLinkClass}>
                Home
              </NavLink>
            </li>
            <li>
              <NavLink to="/listings" className={navLinkClass}>
                Listings
              </NavLink>
            </li>
            {status === "authenticated" ? (
              <>
                <li>
                  <NavLink to="/dashboard" className={navLinkClass}>
                    Dashboard
                  </NavLink>
                </li>
                <li>
                  <Button variant="ghost" size="sm" onClick={() => void logout()}>
                    Sign out
                    <span className="sr-only"> ({user?.email})</span>
                  </Button>
                </li>
              </>
            ) : (
              <li>
                <NavLink to="/login" className={navLinkClass}>
                  Sign in
                </NavLink>
              </li>
            )}
          </ul>
        </nav>
      </div>
    </header>
  );
}
