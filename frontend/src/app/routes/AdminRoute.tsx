/** Placeholder behind RoleGuard, so the role guard has something to protect. */
export default function AdminRoute() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-10">
      <h1 className="text-3xl font-semibold">Administration</h1>
      <p className="mt-3 text-muted-foreground">Staff tooling appears here.</p>
    </div>
  );
}
