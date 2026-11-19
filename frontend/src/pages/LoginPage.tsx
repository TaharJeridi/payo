import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { isWellFormedKey, useAuth } from "../auth/AuthContext";
import { Button, ErrorBox } from "../components/ui";

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
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={onSubmit} className="w-full max-w-md space-y-5 rounded-xl border bg-white p-8 shadow-sm">
        <div>
          <h1 className="text-2xl font-bold text-indigo-700">PayFlow AI</h1>
          <p className="mt-1 text-sm text-slate-600">Dashboard merchant · Stripe TEST mode</p>
        </div>
        <label className="block text-sm font-medium text-slate-700">
          API key del merchant
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="pf_…"
            className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        {formatError && (
          <p role="alert" className="text-sm text-rose-700">
            Formato non valido: la chiave è del tipo pf_&lt;prefisso&gt;_&lt;segreto&gt;.
          </p>
        )}
        {error !== null && (
          <ErrorBox
            error={error}
          />
        )}
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
          Ricorda per questa sessione del browser (sessionStorage)
        </label>
        <Button type="submit" disabled={busy || value.trim() === ""} className="w-full">
          {busy ? "Verifica…" : "Accedi"}
        </Button>
        <p className="text-xs text-slate-500">
          La chiave resta in memoria in questa scheda e non viene mai salvata in localStorage. Si crea con{" "}
          <code className="rounded bg-slate-100 px-1">python -m app.merchants.cli create</code>.
        </p>
      </form>
    </div>
  );
}
