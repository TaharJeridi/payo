import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import type { PaymentStatus } from "../api/types";
import { STATUS_LABEL, STATUS_TONE } from "../lib/format";

export function StatusBadge({ status }: { status: PaymentStatus }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_TONE[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Card({ title, children, action }: { title?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="rounded-card border border-line bg-surface p-5 shadow-card">
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-4">
          {title && <h2 className="text-sm font-semibold text-navy">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label = "Caricamento…" }: { label?: string }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-primary" aria-hidden="true" />
      {label}
    </p>
  );
}

const ERROR_MESSAGES: Record<string, string> = {
  network_error: "L'API non è raggiungibile. Lo stack è avviato?",
  rag_unavailable: "Ask Payo non è disponibile: Ollama o Qdrant non rispondono.",
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

/** Shared look of text inputs and selects: visible >= 3:1 border, blue focus. */
export const FIELD_CLASS =
  "mt-1 block w-full rounded-lg border border-field bg-surface px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30";

/** Page title with an optional one-line description. */
export function PageHeader({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-navy">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {children}
    </header>
  );
}

export function Button({
  children,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  const tone =
    variant === "primary"
      ? "bg-accent text-ink hover:bg-accent-strong disabled:bg-accent/40 disabled:text-ink/50"
      : "bg-surface text-navy ring-1 ring-inset ring-field hover:bg-page disabled:text-muted/60 disabled:ring-line";
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed ${tone} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}
