import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useApiHealth, usePayments, useRagHealth, useStats } from "../api/hooks";
import { PAYMENT_STATUSES } from "../api/types";
import { Card, ErrorBox, PageHeader, Spinner, StatusBadge } from "../components/ui";
import { STATUS_BAR, STATUS_LABEL, formatDateTime, formatMoney } from "../lib/format";

export function OverviewPage() {
  const stats = useStats();
  const recent = usePayments({ status: undefined, limit: 5, offset: 0 });
  const api = useApiHealth();
  const rag = useRagHealth();
  const total = stats.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader title="Panoramica" description="I tuoi pagamenti in sintesi" />

      {stats.isPending && <Spinner />}
      {stats.isError && <ErrorBox error={stats.error} onRetry={() => void stats.refetch()} />}
      {stats.data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <section className="rounded-card bg-navy p-5 text-white shadow-card">
            <h2 className="text-sm font-semibold text-accent">Incassato</h2>
            {stats.data.paid_totals.length === 0 ? (
              <p className="mt-3 text-3xl font-bold tracking-tight">{formatMoney(0, "eur")}</p>
            ) : (
              stats.data.paid_totals.map((paid) => (
                <p key={paid.currency} className="mt-3 text-3xl font-bold tracking-tight" data-testid="paid-total">
                  {formatMoney(paid.amount, paid.currency)}
                </p>
              ))
            )}
            <p className="mt-1 text-xs text-blue-100">pagamenti completati</p>
          </section>
          <Kpi title="Pagamenti" hint="totale registrato">
            <span data-testid="total-count">{stats.data.total}</span>
          </Kpi>
          <Kpi title="Pagati" hint="esito confermato" valueClass="text-emerald-700">
            {stats.data.by_status.paid}
          </Kpi>
          <Kpi title="Da risolvere" hint="in preparazione o con esito sconosciuto" valueClass="text-violet-700">
            {stats.data.by_status.creating + stats.data.by_status.creation_unknown}
          </Kpi>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card
            title="Transazioni recenti"
            action={
              <Link to="/payments" className="text-sm font-medium text-primary hover:text-primary-strong">
                Vedi tutte →
              </Link>
            }
          >
            {recent.isPending && <Spinner />}
            {recent.isError && <ErrorBox error={recent.error} />}
            {recent.data && recent.data.items.length === 0 && (
              <p className="text-sm text-muted">
                Nessun pagamento.{" "}
                <Link to="/checkout" className="text-primary underline">
                  Crea un checkout
                </Link>
                .
              </p>
            )}
            <ul className="divide-y divide-line">
              {recent.data?.items.map((payment) => (
                <li key={payment.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3 text-sm">
                  <Link to={`/payments/${payment.id}`} className="font-mono text-primary hover:underline">
                    {payment.id.slice(0, 8)}…
                  </Link>
                  <span className="font-semibold text-ink">{formatMoney(payment.amount, payment.currency)}</span>
                  <StatusBadge status={payment.status} />
                  <span className="ml-auto text-muted">{formatDateTime(payment.created_at)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-4">
          <Card title="Pagamenti per stato">
            <ul className="space-y-3 text-sm">
              {PAYMENT_STATUSES.map((status) => {
                const count = stats.data?.by_status[status];
                const share = count !== undefined && total > 0 ? Math.round((count / total) * 100) : 0;
                return (
                  <li key={status}>
                    <div className="flex justify-between">
                      <span>{STATUS_LABEL[status]}</span>
                      <span className="font-semibold">{count ?? "—"}</span>
                    </div>
                    <div className="mt-1 h-1.5 rounded-full bg-page" aria-hidden="true">
                      <div className={`h-1.5 rounded-full ${STATUS_BAR[status]} ${widthClass(share)}`} />
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>
          <Card
            title="Sistema"
            action={
              <Link to="/status" className="text-sm font-medium text-primary hover:text-primary-strong">
                Dettagli →
              </Link>
            }
          >
            <ul className="space-y-1 text-sm">
              <li>API: {api.isSuccess ? "ok" : api.isError ? "non raggiungibile" : "…"}</li>
              <li>
                Ask Payo:{" "}
                {rag.data ? (rag.data.status === "ok" ? "ok" : "degradato") : rag.isError ? "non raggiungibile" : "…"}
              </li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Kpi({
  title,
  hint,
  valueClass = "text-ink",
  children,
}: {
  title: string;
  hint: string;
  valueClass?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-card border border-line bg-surface p-5 shadow-card">
      <h2 className="text-sm font-semibold text-muted">{title}</h2>
      <p className={`mt-3 text-3xl font-bold tracking-tight ${valueClass}`}>{children}</p>
      <p className="mt-1 text-xs text-muted">{hint}</p>
    </section>
  );
}

/*
 * Bar widths as static class names (the strict CSP forbids inline styles), rounded to 5 %.
 * Every class is written out literally so Tailwind generates it.
 */
const WIDTH_STEPS = ["w-0", "w-[5%]", "w-[10%]", "w-[15%]", "w-[20%]", "w-[25%]", "w-[30%]", "w-[35%]", "w-[40%]", "w-[45%]", "w-[50%]", "w-[55%]", "w-[60%]", "w-[65%]", "w-[70%]", "w-[75%]", "w-[80%]", "w-[85%]", "w-[90%]", "w-[95%]", "w-full"];

function widthClass(percent: number): string {
  return WIDTH_STEPS[Math.round(percent / 5)] ?? "w-0";
}
