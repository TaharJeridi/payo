import { Link, useSearchParams } from "react-router-dom";

import { usePayments } from "../api/hooks";
import { PAYMENT_STATUSES, type PaymentStatus } from "../api/types";
import { Button, Card, ErrorBox, PageHeader, Spinner, StatusBadge } from "../components/ui";
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
      <PageHeader title="Payments" description="Explore transactions and filter them by status" />
      <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
        <FilterChip active={!status} onClick={() => update({ status: undefined })}>
          All
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
        {query.data && query.data.items.length === 0 && <p className="text-sm text-muted">No payments.</p>}
        {query.data && query.data.items.length > 0 && (
          <ul className="divide-y divide-line sm:hidden" aria-label="Payments">
            {query.data.items.map((payment) => (
              <li key={payment.id} className="flex items-start justify-between gap-3 py-3 text-sm">
                <div className="min-w-0">
                  <Link to={`/payments/${payment.id}`} className="font-mono text-primary hover:underline">
                    {payment.id.slice(0, 8)}…
                  </Link>
                  <p className="text-muted">
                    {payment.product_id} × {payment.quantity}
                  </p>
                  <p className="text-xs text-muted">{formatDateTime(payment.created_at)}</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span className="font-semibold">{formatMoney(payment.amount, payment.currency)}</span>
                  <StatusBadge status={payment.status} />
                </div>
              </li>
            ))}
          </ul>
        )}
        {query.data && query.data.items.length > 0 && (
          <div className="hidden overflow-x-auto sm:block">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-line text-xs font-semibold text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4">Payment</th>
                  <th scope="col" className="py-2 pr-4">Product</th>
                  <th scope="col" className="py-2 pr-4">Amount</th>
                  <th scope="col" className="py-2 pr-4">Status</th>
                  <th scope="col" className="py-2 pr-4">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {query.data.items.map((payment) => (
                  <tr key={payment.id}>
                    <td className="py-2 pr-4 font-mono">
                      <Link to={`/payments/${payment.id}`} className="text-primary hover:underline">
                        {payment.id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      {payment.product_id} × {payment.quantity}
                    </td>
                    <td className="py-2 pr-4 font-semibold">{formatMoney(payment.amount, payment.currency)}</td>
                    <td className="py-2 pr-4">
                      <StatusBadge status={payment.status} />
                    </td>
                    <td className="whitespace-nowrap py-2 pr-4 text-muted">{formatDateTime(payment.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {query.data && (
          <footer className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
            <span>
              {total} payments · page {page + 1} of {pages}
            </span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={page === 0} onClick={() => update({ page: page - 1 })}>
                Previous
              </Button>
              <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => update({ page: page + 1 })}>
                Next
              </Button>
            </div>
          </footer>
        )}
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
        active ? "bg-navy text-white ring-navy" : "bg-surface text-ink ring-field hover:ring-primary"
      }`}
    >
      {children}
    </button>
  );
}
