import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { get } from "@/api/client";

import { applyTokens, clearTokens, type TenantTheme } from "./tokens";

export interface TenantConfig {
  subdomain: string;
  name: string;
  display_name: string;
  logo_url: string | null;
  favicon_url: string | null;
  theme: TenantTheme;
}

type TenantStatus = "loading" | "ready" | "default";

interface TenantContextValue {
  config: TenantConfig | null;
  status: TenantStatus;
}

const TenantContext = createContext<TenantContextValue>({ config: null, status: "loading" });

const CACHE_PREFIX = "tenant-config:";

/**
 * ADR-005 wants the tokens applied before first paint. A blocking network
 * fetch would mean a blank page on a slow connection, so the cached config for
 * this host is applied synchronously and revalidated in the background:
 * stale-by-seconds brand colour is invisible, a white flash is not.
 */
function cacheKey(): string {
  return `${CACHE_PREFIX}${typeof window === "undefined" ? "" : window.location.host}`;
}

function readCache(): TenantConfig | null {
  try {
    const raw = window.localStorage.getItem(cacheKey());
    return raw ? (JSON.parse(raw) as TenantConfig) : null;
  } catch {
    // Private mode, disabled site data, or a corrupt entry. Not fatal.
    return null;
  }
}

function writeCache(config: TenantConfig): void {
  try {
    window.localStorage.setItem(cacheKey(), JSON.stringify(config));
  } catch {
    // Storage full or unavailable; the config still applies for this session.
  }
}

function prefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

export function TenantThemeProvider({ children }: { children: ReactNode }) {
  const cached = useMemo(readCache, []);
  const [config, setConfig] = useState<TenantConfig | null>(cached);
  const [status, setStatus] = useState<TenantStatus>(cached ? "ready" : "loading");

  // Apply the cached palette synchronously on the first render pass, before
  // the browser paints, rather than in an effect after it.
  useState(() => {
    if (cached) applyTokens(cached.theme, prefersDark());
    return null;
  });

  useEffect(() => {
    let cancelled = false;

    get<TenantConfig>("/tenant/config/")
      .then((fresh) => {
        if (cancelled) return;
        applyTokens(fresh.theme, prefersDark());
        writeCache(fresh);
        setConfig(fresh);
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        // No tenant, or the endpoint is unavailable. Fall back to the neutral
        // palette in index.css rather than erroring: an unbranded page is a
        // working page.
        if (!cached) {
          clearTokens();
          setStatus("default");
        }
      });

    return () => {
      cancelled = true;
    };
    // `cached` is captured once on mount by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (config?.display_name) {
      document.title = `${config.display_name} · CampusRentalFinder`;
    }
  }, [config?.display_name]);

  const value = useMemo(() => ({ config, status }), [config, status]);

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenant(): TenantContextValue {
  return useContext(TenantContext);
}
