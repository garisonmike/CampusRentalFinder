import { useTenant } from "@/theme/TenantThemeProvider";

export function Footer() {
  const { config } = useTenant();

  return (
    <footer className="border-t">
      <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-muted-foreground">
        <p>
          {config?.name ? `${config.name} · ` : ""}CampusRentalFinder ·{" "}
          {new Date().getFullYear()}
        </p>
      </div>
    </footer>
  );
}
