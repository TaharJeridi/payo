import { Link, useParams } from "react-router-dom";

import { usePayment } from "../api/hooks";
import { Card, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { SOURCE_LABEL, STATUS_LABEL, formatDateTime, formatMoney } from "../lib/format";

export function PaymentDetailPage() {
  const { paymentId = "" } = useParams();
  const query = usePayment(paymentId);

  if (query.isPending) return <Spinner />;
  if (query.isError) return <ErrorBox error={query.error} onRetry={() => void query.refetch()} />;
  const payment = query.data;

  const facts: [string, string][] = [
    ["Prodotto", `${payment.product_id} × ${payment.quantity}`],
    ["Importo", formatMoney(payment.amount, payment.currency)],
    ["Prezzo unitario", formatMoney(payment.unit_amount, payment.currency)],
    ["Motivo", payment.failure_reason ?? "—"],
    ["Creato", formatDateTime(payment.created_at)],
    ["Pagato", formatDateTime(payment.paid_at)],
    ["Scadenza checkout", formatDateTime(payment.checkout_expires_at)],
    ["Checkout Session", payment.stripe_checkout_session_id ?? "—"],
    ["PaymentIntent", payment.stripe_payment_intent_id ?? "—"],
  ];

  return (
    <div className="space-y-4">
      <Link to="/payments" className="text-sm text-indigo-600">
        ← Pagamenti
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-mono text-xl font-bold">{payment.id}</h1>
        <StatusBadge status={payment.status} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Dettagli">
          <dl className="grid grid-cols-[auto,1fr] gap-x-6 gap-y-2 text-sm">
            {facts.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-slate-500">{label}</dt>
                <dd className="break-all">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Storico">
          <ol className="relative space-y-4 border-l border-slate-200 pl-5" aria-label="Storico del pagamento">
            {payment.history.map((event, index) => (
              <li key={`${event.created_at}-${index}`} className="text-sm">
                <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-white bg-indigo-500" />
                <p className="font-medium">
                  {event.from_status ? `${STATUS_LABEL[event.from_status]} → ` : "Creato come "}
                  {STATUS_LABEL[event.to_status]}
                </p>
                <p className="text-slate-500">
                  {formatDateTime(event.created_at)} · {SOURCE_LABEL[event.source] ?? event.source}
                  {event.reason ? ` · ${event.reason}` : ""}
                </p>
              </li>
            ))}
          </ol>
        </Card>
      </div>
      <Link
        to={`/assistant?q=${encodeURIComponent(`Che cosa è successo al pagamento ${payment.id}?`)}`}
        className="inline-block text-sm font-medium text-indigo-600"
      >
        Chiedi all'assistente →
      </Link>
    </div>
  );
}
