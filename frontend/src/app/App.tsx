import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";

import { AppRoutes } from "@/app/router";
import { ErrorBoundary } from "@/app/ErrorBoundary";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuthStore } from "@/stores/auth";
import { TenantThemeProvider } from "@/theme/TenantThemeProvider";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
  },
});

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
