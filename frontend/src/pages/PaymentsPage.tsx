import { Link, useSearchParams } from "react-router-dom";

import { usePayments } from "../api/hooks";
import { PAYMENT_STATUSES, type PaymentStatus } from "../api/types";
import { Button, Card, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { STATUS_LABEL, formatDateTime, formatMoney } from "../lib/format";

export const PAGE_SIZE = 20;

function parseStatus(value: string | null): PaymentStatus | undefined {
  return PAYMENT_STATUSES.find((status) => status === value);
}

export function PaymentsPage() {
  const [params, setParams] = useSearchParams();
  const status = parseStatus(params.get("status"));
  const page = Math.max(0, Number(params.get("page") ?? "0") || 0);
  const query = usePayments({ status, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function update(next: { status?: PaymentStatus | undefined; page?: number }) {
    const merged = new URLSearchParams();
    const nextStatus = "status" in next ? next.status : status;
    if (nextStatus) merged.set("status", nextStatus);
    const nextPage = next.page ?? 0;
    if (nextPage > 0) merged.set("page", String(nextPage));
    setParams(merged);
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Pagamenti</h1>
      <div role="group" aria-label="Filtra per stato" className="flex flex-wrap gap-2">
        <FilterChip active={!status} onClick={() => update({ status: undefined })}>
          Tutti
        </FilterChip>
        {PAYMENT_STATUSES.map((value) => (
          <FilterChip key={value} active={status === value} onClick={() => update({ status: value })}>
            {STATUS_LABEL[value]}
          </FilterChip>
        ))}
      </div>
      <Card>
        {query.isPending && <Spinner />}
        {query.isError && <ErrorBox error={query.error} onRetry={() => void query.refetch()} />}
        {query.data && query.data.items.length === 0 && <p className="text-sm text-slate-500">Nessun pagamento.</p>}
        {query.data && query.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr>
                  <th className="py-2 pr-4">Pagamento</th>
                  <th className="py-2 pr-4">Prodotto</th>
                  <th className="py-2 pr-4">Importo</th>
                  <th className="py-2 pr-4">Stato</th>
                  <th className="py-2 pr-4">Creato</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {query.data.items.map((payment) => (
                  <tr key={payment.id}>
                    <td className="py-2 pr-4 font-mono">
                      <Link to={`/payments/${payment.id}`} className="text-indigo-700 hover:underline">
                        {payment.id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      {payment.product_id} × {payment.quantity}
                    </td>
                    <td className="py-2 pr-4">{formatMoney(payment.amount, payment.currency)}</td>
                    <td className="py-2 pr-4">
                      <StatusBadge status={payment.status} />
                    </td>
                    <td className="py-2 pr-4 text-slate-500">{formatDateTime(payment.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <footer className="mt-4 flex items-center justify-between text-sm text-slate-600">
          <span>
            {total} pagamenti · pagina {page + 1} di {pages}
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => update({ page: page - 1 })}>
              Precedente
            </Button>
            <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => update({ page: page + 1 })}>
              Successiva
            </Button>
          </div>
        </footer>
      </Card>
    </div>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset ${
        active ? "bg-indigo-600 text-white ring-indigo-600" : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50"
      }`}
    >
      {children}
    </button>
  );
}
