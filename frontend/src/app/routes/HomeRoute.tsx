import { useTenant } from "@/theme/TenantThemeProvider";

/**
 * Placeholder. Feature pages arrive after the schema rewrite settles the API
 * contract; this exists so the shell has something to render.
 */
export default function HomeRoute() {
  const { config } = useTenant();

  return (
    <div className="mx-auto max-w-6xl px-4 py-10">
      <h1 className="text-3xl font-semibold">
        Find a place near {config?.display_name ?? "your campus"}
      </h1>
      <p className="mt-3 max-w-prose text-muted-foreground">
        Browse verified listings from landlords and caretakers, with reviews from
        students who actually lived there.
      </p>
    </div>
  );
}
