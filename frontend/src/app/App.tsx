import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";

import { createQueryClient } from "@/api/queries";
import { AppRoutes } from "@/app/router";
import { ErrorBoundary } from "@/app/ErrorBoundary";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuthStore } from "@/stores/auth";
import { TenantThemeProvider } from "@/theme/TenantThemeProvider";

// One configuration, in `api/queries.ts`, with the retry policy derived from
// the error contract. Configured inline here previously, which meant the
// retry rule and the codes it should respect lived in two places.
const queryClient = createQueryClient();

export function App() {
  const loadSession = useAuthStore((state) => state.loadSession);

  useEffect(() => {
    // Resolve the session once at startup. The guards render a pending state
    // until this settles, so nothing is decided on a null user.
    void loadSession();
  }, [loadSession]);

  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <TenantThemeProvider>
          <TooltipProvider>
            <BrowserRouter>
              <AppRoutes />
            </BrowserRouter>
            <Toaster />
          </TooltipProvider>
        </TenantThemeProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
