import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { isWellFormedKey, useAuth } from "../auth/AuthContext";
import { Logo } from "../components/Logo";
import { Button, ErrorBox, FIELD_CLASS } from "../components/ui";

export function LoginPage() {
  const { apiKey, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formatError, setFormatError] = useState(false);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (apiKey) return <Navigate to={from} replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!isWellFormedKey(value)) {
      setFormatError(true);
      return;
    }
    setFormatError(false);
    setBusy(true);
    try {
      await login(value, remember);
      setValue("");
      navigate(from, { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 px-4 py-10">
      <Logo />
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md space-y-5 rounded-card border border-line bg-surface p-8 shadow-card"
      >
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-navy">Welcome to Payo</h1>
          <p className="mt-1 text-sm text-muted">
            Demo merchant dashboard · Stripe test mode, synthetic data
          </p>
        </div>
        <label className="block text-sm font-medium text-ink">
          Merchant API key
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="pf_…"
            className={`${FIELD_CLASS} font-mono`}
          />
        </label>
        {formatError && (
          <p role="alert" className="text-sm text-rose-700">
            Invalid format: the key looks like pf_&lt;prefix&gt;_&lt;secret&gt;.
          </p>
        )}
        {error !== null && (
          <ErrorBox
            error={error}
          />
        )}
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" className="h-4 w-4 accent-primary" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
          Remember for this browser session (sessionStorage)
        </label>
        <Button type="submit" disabled={busy || value.trim() === ""} className="w-full">
          {busy ? "Checking…" : "Sign in"}
        </Button>
        <p className="text-xs text-muted">
          The key stays in this tab's memory and is never saved to localStorage. Create one with{" "}
          <code className="rounded bg-page px-1 text-ink">python -m app.merchants.cli create</code>.
        </p>
      </form>
    </main>
  );
}
