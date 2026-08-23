/**
 * One smoke test per page: does it render without throwing?
 *
 * Every page that talks to the backend does so through `@/services/api`, so
 * the whole module is mocked here. That keeps the tests offline and makes the
 * API surface each page depends on explicit.
 */
import { renderWithProviders } from "@/test/utils";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  authApi: {
    register: vi.fn().mockResolvedValue({ user: null, tokens: {} }),
    login: vi.fn().mockResolvedValue({ user: null, tokens: {} }),
    logout: vi.fn().mockResolvedValue({}),
    refreshToken: vi.fn().mockResolvedValue({ access: "" }),
  },
  rentalsApi: {
    getAll: vi.fn().mockResolvedValue([]),
    getById: vi.fn().mockResolvedValue({
      id: "1",
      title: "Bedsitter near the main gate",
      description: "Water included.",
      address: "Kenyatta Road",
      city: "Nairobi",
      price: 9500,
      bedrooms: 1,
      bathrooms: 1,
      area: 300,
      images: [],
      landlord: { id: "2", username: "landlord", email: "l@example.co.ke" },
      created_at: new Date().toISOString(),
      is_available: true,
    }),
    create: vi.fn().mockResolvedValue({}),
    update: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue(undefined),
    getFeatured: vi.fn().mockResolvedValue([]),
    getRecent: vi.fn().mockResolvedValue([]),
    getTopRated: vi.fn().mockResolvedValue([]),
  },
  reviewsApi: {
    create: vi.fn().mockResolvedValue({}),
    getRentalStatistics: vi.fn().mockResolvedValue({}),
  },
  profileApi: {
    get: vi.fn().mockResolvedValue(null),
    update: vi.fn().mockResolvedValue(null),
  },
  adminApi: {
    getStatistics: vi.fn().mockResolvedValue({
      total_users: 0,
      total_rentals: 0,
      total_reviews: 0,
      average_rating: 0,
    }),
  },
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import AdminPage from "@/pages/AdminPage";
import CreateRentalPage from "@/pages/CreateRentalPage";
import DashboardPage from "@/pages/DashboardPage";
import FavoritesPage from "@/pages/FavoritesPage";
import HomePage from "@/pages/HomePage";
import LoginPage from "@/pages/LoginPage";
import NotFound from "@/pages/NotFound";
import ProfilePage from "@/pages/ProfilePage";
import RegisterPage from "@/pages/RegisterPage";
import RentalDetailPage from "@/pages/RentalDetailPage";
import RentalsPage from "@/pages/RentalsPage";

beforeEach(() => {
  localStorage.clear();
});

describe("page smoke tests", () => {
  it("renders HomePage", async () => {
    renderWithProviders(<HomePage />);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("renders LoginPage with an email and password field", () => {
    renderWithProviders(<LoginPage />, { route: "/login", path: "/login" });
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("renders RegisterPage", () => {
    renderWithProviders(<RegisterPage />, { route: "/register", path: "/register" });
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  });

  it("renders RentalsPage", async () => {
    const { container } = renderWithProviders(<RentalsPage />, {
      route: "/rentals",
      path: "/rentals",
    });
    expect(container).toBeTruthy();
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("renders RentalDetailPage for a given id", async () => {
    renderWithProviders(<RentalDetailPage />, {
      route: "/rentals/1",
      path: "/rentals/:id",
    });
    expect(
      await screen.findByText(/bedsitter near the main gate/i),
    ).toBeInTheDocument();
  });

  it("renders DashboardPage", async () => {
    const { container } = renderWithProviders(<DashboardPage />, {
      route: "/dashboard",
      path: "/dashboard",
    });
    expect(container).toBeTruthy();
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("renders ProfilePage", async () => {
    const { container } = renderWithProviders(<ProfilePage />, {
      route: "/profile",
      path: "/profile",
    });
    expect(container).toBeTruthy();
  });

  it("renders FavoritesPage with its empty state", () => {
    renderWithProviders(<FavoritesPage />, { route: "/favorites", path: "/favorites" });
    expect(screen.getByText(/no favorites yet/i)).toBeInTheDocument();
  });

  it("renders CreateRentalPage", () => {
    const { container } = renderWithProviders(<CreateRentalPage />, {
      route: "/rentals/create",
      path: "/rentals/create",
    });
    expect(container).toBeTruthy();
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
  });

  it("renders AdminPage", async () => {
    const { container } = renderWithProviders(<AdminPage />, {
      route: "/admin",
      path: "/admin",
    });
    expect(container).toBeTruthy();
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("renders NotFound", () => {
    renderWithProviders(<NotFound />, { route: "/nowhere", path: "*" });
    expect(screen.getByText("404")).toBeInTheDocument();
  });
});
