import { useAuthStore } from "@/stores/auth";

/** Placeholder behind AuthGuard, so the guard has something to protect. */
export default function DashboardRoute() {
  const user = useAuthStore((state) => state.user);

  return (
    <div className="mx-auto max-w-6xl px-4 py-10">
      <h1 className="text-3xl font-semibold">Dashboard</h1>
      <p className="mt-3 text-muted-foreground">Signed in as {user?.email}.</p>
    </div>
  );
}
