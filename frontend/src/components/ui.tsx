import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import type { PaymentStatus } from "../api/types";
import { STATUS_LABEL, STATUS_TONE } from "../lib/format";

export function StatusBadge({ status }: { status: PaymentStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_TONE[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Card({ title, children, action }: { title?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-4">
          {title && <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label = "Caricamento…" }: { label?: string }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
      {label}
    </p>
  );
}

const ERROR_MESSAGES: Record<string, string> = {
  network_error: "L'API non è raggiungibile. Lo stack è avviato?",
  rag_unavailable: "L'assistente non è disponibile: Ollama o Qdrant non rispondono.",
  payment_outcome_unknown: "Stripe non ha confermato l'esito. Riprova: verrà usata la stessa chiave di idempotenza.",
  idempotency_operation_in_progress: "La richiesta precedente è ancora in corso. Riprova tra qualche secondo.",
  rate_limited: "Troppe richieste. Riprova tra un minuto.",
  payment_not_found: "Pagamento non trovato.",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return ERROR_MESSAGES[error.code] ?? error.message;
  return "Errore inatteso";
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 font-medium underline underline-offset-2">
          Riprova
        </button>
      )}
    </div>
  );
}

export function Button({
  children,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  const tone =
    variant === "primary"
      ? "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300"
      : "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:text-slate-400";
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-semibold shadow-sm transition disabled:cursor-not-allowed ${tone} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}
