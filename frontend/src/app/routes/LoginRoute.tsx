import { useState, type FormEvent } from "react";
import { useLocation, useNavigate, type Location } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { getErrorMessage } from "@/lib/errors";
import { useAuthStore } from "@/stores/auth";

interface FromState {
  from?: Location;
}

export default function LoginRoute() {
  const login = useAuthStore((state) => state.login);
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login({ email, password });
      const from = (location.state as FromState | null)?.from?.pathname ?? "/dashboard";
      navigate(from, { replace: true });
    } catch (caught) {
      setError(getErrorMessage(caught, "Sign in failed. Check your details and try again."));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mx-auto max-w-sm px-4 py-10">
      <h1 className="text-3xl font-semibold">Sign in</h1>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <div className="space-y-1">
          <label htmlFor="email" className="block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded-md border bg-background px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-md border bg-background px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          />
        </div>

        <Button type="submit" disabled={pending}>
          {pending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
