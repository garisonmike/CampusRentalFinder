import { Suspense } from "react";
import { Outlet } from "react-router-dom";

import { ErrorBoundary } from "@/app/ErrorBoundary";
import { Skeleton } from "@/components/ui/skeleton";

import { Footer } from "./Footer";
import { Header } from "./Header";
import { SkipLink } from "./SkipLink";

export function RootLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink />
      <Header />
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        <ErrorBoundary>
          <Suspense fallback={<RouteFallback />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>
      <Footer />
    </div>
  );
}

function RouteFallback() {
  return (
    <output aria-live="polite" className="mx-auto block max-w-6xl space-y-3 p-8">
      <span className="sr-only">Loading</span>
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-1/2" />
    </output>
  );
}
