import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ErrorBoundary } from "@/app/ErrorBoundary";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TenantThemeProvider } from "@/theme/TenantThemeProvider";

interface Options extends Omit<RenderOptions, "wrapper"> {
  /** Initial URL. */
  route?: string;
  /** Route pattern, so components reading useParams get real values. */
  path?: string;
  /** Skip the tenant provider when a test drives it directly. */
  withTenant?: boolean;
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", path = "*", withTenant = true, ...options }: Options = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  const Wrapper = ({ children }: { children: ReactNode }) => {
    const tree = (
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={children} />
        </Routes>
      </MemoryRouter>
    );

    return (
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          {withTenant ? (
            <TenantThemeProvider>
              <TooltipProvider>{tree}</TooltipProvider>
            </TenantThemeProvider>
          ) : (
            <TooltipProvider>{tree}</TooltipProvider>
          )}
        </QueryClientProvider>
      </ErrorBoundary>
    );
  };

  return render(ui, { wrapper: Wrapper, ...options });
}
