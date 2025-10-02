// src/store/authStore.ts
import { authApi, profileApi } from "@/services/api";
import type { LoginCredentials, RegisterData, User } from "@/types";
import { toast } from "sonner";
import { create } from "zustand";

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (credentials: LoginCredentials) => Promise<void>;
  register: (data: RegisterData) => Promise<void>;
  logout: () => Promise<void>;
  fetchUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: !!localStorage.getItem("access_token"),
  isLoading: false,

  login: async (credentials: LoginCredentials) => {
    try {
      set({ isLoading: true });
      const { user, tokens } = await authApi.login(credentials);

      localStorage.setItem("access_token", tokens.access);
      localStorage.setItem("refresh_token", tokens.refresh);

      set({ user, isAuthenticated: true, isLoading: false });
      toast.success("Welcome back!");
    } catch (error: any) {
      set({ isLoading: false });
      const errorMessage =
        error.response?.data?.message ||
        error.response?.data?.detail ||
        JSON.stringify(error.response?.data) ||
        "Login failed. Please try again.";
      toast.error(errorMessage);
      throw error;
    }
  },

  register: async (data: RegisterData) => {
    try {
      set({ isLoading: true });
      const { user, tokens } = await authApi.register(data);

      localStorage.setItem("access_token", tokens.access);
      localStorage.setItem("refresh_token", tokens.refresh);

      set({ user, isAuthenticated: true, isLoading: false });
      toast.success("Account created successfully!");
    } catch (error: any) {
      set({ isLoading: false });
      const errorMessage =
        error.response?.data?.message ||
        error.response?.data?.detail ||
        JSON.stringify(error.response?.data) ||
        "Registration failed. Please try again.";
      toast.error(errorMessage);
      throw error;
    }
  },

  logout: async () => {
    try {
      const refresh = localStorage.getItem("refresh_token");
      if (refresh) {
        // ✅ now we send refresh token
        await authApi.logout({ refresh });
      }
    } catch (error: any) {
      console.error("Logout error:", error);
      toast.error("Logout failed, but tokens cleared locally.");
    } finally {
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      set({ user: null, isAuthenticated: false });
      toast.success("Logged out successfully");
    }
  },

  fetchUser: async () => {
    try {
      set({ isLoading: true });
      const user = await profileApi.get();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch (error) {
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },
}));
