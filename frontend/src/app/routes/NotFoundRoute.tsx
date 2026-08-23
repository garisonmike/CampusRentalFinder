import { Link } from "react-router-dom";

export default function NotFoundRoute() {
  return (
    <div className="mx-auto max-w-md px-4 py-16 text-center">
      <h1 className="text-3xl font-semibold">Page not found</h1>
      <p className="mt-3 text-muted-foreground">
        That page does not exist, or it has moved.
      </p>
      <Link
        to="/"
        className="mt-6 inline-block text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        Back to home
      </Link>
    </div>
  );
}
