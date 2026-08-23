import { create } from "zustand";

import { get, post, setSessionExpiredHandler } from "@/api/client";
import { clearTokens, setTokens } from "@/api/tokens";

/**
 * Capabilities, as the backend reports them.
 *
 * ADR-003 replaces the `user_type` string with object-level roles, and the
 * /auth/me/ response is expected to carry an explicit capability set so the
 * client never re-derives authorization from raw model shapes. Until that
 * endpoint exists, the fields below are optional and default to false, which
 * is the safe direction: an unknown capability denies rather than allows.
 */
export interface Capabilities {
  is_student: boolean;
  is_landlord: boolean;
  is_staff: boolean;
  manages_properties: number[];
}

export interface CurrentUser {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  capabilities: Capabilities;
}

export type Role = "student" | "landlord" | "staff";

type Status = "idle" | "loading" | "authenticated" | "anonymous";

interface AuthState {
  user: CurrentUser | null;
  status: Status;
  login: (credentials: { email: string; password: string }) => Promise<void>;
  logout: () => Promise<void>;
  loadSession: () => Promise<void>;
  hasRole: (role: Role) => boolean;
}

const NO_CAPABILITIES: Capabilities = {
  is_student: false,
  is_landlord: false,
  is_staff: false,
  manages_properties: [],
};

/** Tolerate a backend that does not yet send a capability block. */
function normaliseUser(raw: Record<string, unknown>): CurrentUser {
  const capabilities = (raw.capabilities as Partial<Capabilities> | undefined) ?? {};
  return {
    id: Number(raw.id),
    email: String(raw.email ?? ""),
    first_name: String(raw.first_name ?? ""),
    last_name: String(raw.last_name ?? ""),
    capabilities: { ...NO_CAPABILITIES, ...capabilities },
  };
}

export const useAuthStore = create<AuthState>((set, getState) => ({
  user: null,
  status: "idle",

  login: async (credentials) => {
    set({ status: "loading" });
    try {
      const data = await post<{ tokens: { access: string; refresh: string } }>(
        "/auth/login/",
        credentials,
        { _skipAuthRefresh: true } as never,
      );
      setTokens({ access: data.tokens.access, refresh: data.tokens.refresh });
      await getState().loadSession();
    } catch (error) {
      clearTokens();
      set({ user: null, status: "anonymous" });
      throw error;
    }
  },

  logout: async () => {
    try {
      await post("/auth/logout/", {}, { _skipAuthRefresh: true } as never);
    } catch {
      // The server-side blacklist is best-effort; clearing locally is what
      // actually ends this session.
    } finally {
      clearTokens();
      set({ user: null, status: "anonymous" });
    }
  },

  loadSession: async () => {
    set({ status: "loading" });
    try {
      const raw = await get<Record<string, unknown>>("/auth/me/");
      set({ user: normaliseUser(raw), status: "authenticated" });
    } catch {
      clearTokens();
      set({ user: null, status: "anonymous" });
    }
  },

  hasRole: (role) => {
    const capabilities = getState().user?.capabilities;
    if (!capabilities) return false;
    if (role === "student") return capabilities.is_student;
    if (role === "landlord") return capabilities.is_landlord;
    return capabilities.is_staff;
  },
}));

// A failed refresh means the session is unrecoverable; drop it here rather
// than letting each call site guess.
setSessionExpiredHandler(() => {
  useAuthStore.setState({ user: null, status: "anonymous" });
});
