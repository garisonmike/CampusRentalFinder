import { lazy } from "react";
import { Route, Routes } from "react-router-dom";

import { AuthGuard, RoleGuard } from "@/app/guards";
import { RootLayout } from "@/app/layout/RootLayout";

// Route-level code splitting. Each route is its own chunk, so the initial
// bundle carries the shell and nothing else.
const HomeRoute = lazy(() => import("@/app/routes/HomeRoute"));
const ListingsRoute = lazy(() => import("@/app/routes/ListingsRoute"));
const PropertyRoute = lazy(() => import("@/app/routes/PropertyRoute"));
const UnitRoute = lazy(() => import("@/app/routes/UnitRoute"));
const LoginRoute = lazy(() => import("@/app/routes/LoginRoute"));
const DashboardRoute = lazy(() => import("@/app/routes/DashboardRoute"));
const SavedRoute = lazy(() => import("@/app/routes/SavedRoute"));
const PortalRoute = lazy(() => import("@/app/routes/PortalRoute"));
const VacancyRoute = lazy(() => import("@/app/routes/VacancyRoute"));
const PortalReviewsRoute = lazy(() => import("@/app/routes/PortalReviewsRoute"));
const AdminRoute = lazy(() => import("@/app/routes/AdminRoute"));
const ForbiddenRoute = lazy(() => import("@/app/routes/ForbiddenRoute"));
const NotFoundRoute = lazy(() => import("@/app/routes/NotFoundRoute"));

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route index element={<HomeRoute />} />
        <Route path="listings" element={<ListingsRoute />} />
        <Route path="listings/:slug" element={<PropertyRoute />} />
        <Route path="listings/:slug/units/:id" element={<UnitRoute />} />
        <Route path="login" element={<LoginRoute />} />

        <Route
          path="dashboard"
          element={
            <AuthGuard>
              <DashboardRoute />
            </AuthGuard>
          }
        />

        <Route
          path="saved"
          element={
            <AuthGuard>
              <SavedRoute />
            </AuthGuard>
          }
        />

        <Route
          path="portal"
          element={
            // Landlord OR assigned caretaker. A caretaker is not a landlord
            // (ADR-003), so `manages_properties` is what admits them.
            <RoleGuard roles={["manager"]}>
              <PortalRoute />
            </RoleGuard>
          }
        />

        {/* Where the vacancy prompt email lands. One click from the message
            to the screen that does the thing it asks for. */}
        <Route
          path="portal/vacancy"
          element={
            <RoleGuard roles={["manager"]}>
              <VacancyRoute />
            </RoleGuard>
          }
        />

        <Route
          path="portal/reviews"
          element={
            <RoleGuard roles={["manager"]}>
              <PortalReviewsRoute />
            </RoleGuard>
          }
        />

        <Route
          path="admin"
          element={
            <RoleGuard roles={["staff"]}>
              <AdminRoute />
            </RoleGuard>
          }
        />

        <Route path="forbidden" element={<ForbiddenRoute />} />
        <Route path="*" element={<NotFoundRoute />} />
      </Route>
    </Routes>
  );
}
