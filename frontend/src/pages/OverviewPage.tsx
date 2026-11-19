import { Link } from "react-router-dom";

import { useApiHealth, usePayments, useRagHealth, useStats } from "../api/hooks";
import { PAYMENT_STATUSES } from "../api/types";
import { Card, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { STATUS_LABEL, formatDateTime, formatMoney } from "../lib/format";

export function OverviewPage() {
  const stats = useStats();
  const recent = usePayments({ status: undefined, limit: 5, offset: 0 });
  const api = useApiHealth();
  const rag = useRagHealth();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Panoramica</h1>

      {stats.isPending && <Spinner />}
      {stats.isError && <ErrorBox error={stats.error} onRetry={() => void stats.refetch()} />}
      {stats.data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card title="Incassato">
            {stats.data.paid_totals.length === 0 ? (
              <p className="text-2xl font-semibold">{formatMoney(0, "eur")}</p>
            ) : (
              stats.data.paid_totals.map((total) => (
                <p key={total.currency} className="text-2xl font-semibold" data-testid="paid-total">
                  {formatMoney(total.amount, total.currency)}
                </p>
              ))
            )}
          </Card>
          <Card title="Pagamenti">
            <p className="text-2xl font-semibold" data-testid="total-count">
              {stats.data.total}
            </p>
          </Card>
          <Card title="Pagati">
            <p className="text-2xl font-semibold text-emerald-700">{stats.data.by_status.paid}</p>
          </Card>
          <Card title="Da risolvere">
            <p className="text-2xl font-semibold text-violet-700">
              {stats.data.by_status.creating + stats.data.by_status.creation_unknown}
            </p>
            <p className="text-xs text-slate-500">in preparazione o con esito sconosciuto</p>
          </Card>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card title="Ultimi pagamenti" action={<Link to="/payments" className="text-sm text-indigo-600">Tutti →</Link>}>
            {recent.isPending && <Spinner />}
            {recent.isError && <ErrorBox error={recent.error} />}
            {recent.data && recent.data.items.length === 0 && (
              <p className="text-sm text-slate-500">
                Nessun pagamento. <Link to="/checkout" className="text-indigo-600 underline">Crea un checkout</Link>.
              </p>
            )}
            <ul className="divide-y divide-slate-100">
              {recent.data?.items.map((payment) => (
                <li key={payment.id} className="flex items-center justify-between gap-4 py-2 text-sm">
                  <Link to={`/payments/${payment.id}`} className="font-mono text-indigo-700 hover:underline">
                    {payment.id.slice(0, 8)}…
                  </Link>
                  <span>{formatMoney(payment.amount, payment.currency)}</span>
                  <StatusBadge status={payment.status} />
                  <span className="hidden text-slate-500 sm:inline">{formatDateTime(payment.created_at)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-4">
          <Card title="Per stato">
            <ul className="space-y-1 text-sm">
              {PAYMENT_STATUSES.map((status) => (
                <li key={status} className="flex justify-between">
                  <span>{STATUS_LABEL[status]}</span>
                  <span className="font-medium">{stats.data?.by_status[status] ?? "—"}</span>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Sistema" action={<Link to="/status" className="text-sm text-indigo-600">Dettagli →</Link>}>
            <ul className="space-y-1 text-sm">
              <li>API: {api.isSuccess ? "ok" : api.isError ? "non raggiungibile" : "…"}</li>
              <li>Assistente: {rag.data ? (rag.data.status === "ok" ? "ok" : "degradato") : rag.isError ? "non raggiungibile" : "…"}</li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
