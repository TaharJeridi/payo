import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import type { CheckoutResult } from "../api/client";
import { useProducts } from "../api/hooks";
import type { CheckoutRequest } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { IdempotencyKeys, checkoutWithRetry, type RetryOptions } from "../lib/idempotency";
import { formatMoney } from "../lib/format";

const STRIPE_CHECKOUT = /^https:\/\/checkout\.stripe\.com\//;
const MAX_QUANTITY = 10;

export function CheckoutPage({ retryOptions }: { retryOptions?: RetryOptions }) {
  const { client } = useAuth();
  const queryClient = useQueryClient();
  const products = useProducts();
  const keys = useRef(new IdempotencyKeys());
  const [productId, setProductId] = useState("");
  const [quantityText, setQuantityText] = useState("1");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<CheckoutResult | null>(null);

  const parsedQuantity = Number.parseInt(quantityText, 10);
  const quantityValid = Number.isInteger(parsedQuantity) && parsedQuantity >= 1 && parsedQuantity <= MAX_QUANTITY;
  const quantity = quantityValid ? parsedQuantity : 1;
  const selected = products.data?.find((p) => p.id === (productId || products.data?.[0]?.id));
  const request = useMemo<CheckoutRequest | null>(() => {
    if (!selected || !quantityValid) return null;
    const trimmed = email.trim();
    return { product_id: selected.id, quantity, ...(trimmed ? { customer_email: trimmed } : {}) };
  }, [selected, quantity, quantityValid, email]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  async function submit() {
    if (!request) return;
    const key = keys.current.keyFor(request);
    setBusy(true);
    setError(null);
    setResult(null);
    setNotice(null);
    try {
      const outcome = await checkoutWithRetry(client, request, key, {
        ...retryOptions,
        onRetry: (attempt) => setNotice(`Esito non ancora confermato, nuovo tentativo ${attempt + 1} con la stessa chiave…`),
      });
      keys.current.completed();
      setResult(outcome);
      void queryClient.invalidateQueries({ queryKey: ["payments"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setNotice(null);
    }
  }

  if (products.isPending) return <Spinner />;
  if (products.isError) return <ErrorBox error={products.error} onRetry={() => void products.refetch()} />;

  const checkoutUrl = result?.response.checkout_url;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <h1 className="mb-4 text-2xl font-bold">Nuovo checkout</h1>
        <Card>
          <form onSubmit={onSubmit} className="space-y-4">
            <label className="block text-sm font-medium text-slate-700">
              Prodotto
              <select
                value={selected?.id ?? ""}
                onChange={(event) => setProductId(event.target.value)}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                {products.data.map((product) => (
                  <option key={product.id} value={product.id}>
                    {product.name} · {formatMoney(product.unit_amount, product.currency)}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Quantità
              <input
                type="number"
                min={1}
                max={MAX_QUANTITY}
                inputMode="numeric"
                value={quantityText}
                aria-invalid={!quantityValid}
                onChange={(event) => setQuantityText(event.target.value)}
                className="mt-1 block w-32 rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Email del cliente (facoltativa)
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </label>
            {!quantityValid && (
              <p role="alert" className="text-sm text-rose-700">
                La quantità deve essere un numero intero tra 1 e {MAX_QUANTITY}.
              </p>
            )}
            {selected && quantityValid && (
              <p className="text-sm text-slate-600">
                Totale: <strong>{formatMoney(selected.unit_amount * quantity, selected.currency)}</strong> · calcolato
                dal backend sul catalogo
              </p>
            )}
            <Button type="submit" disabled={busy || !request}>
              {busy ? "Creazione…" : "Crea checkout"}
            </Button>
            {notice && (
              <p role="status" className="text-sm text-amber-700">
                {notice}
              </p>
            )}
          </form>
        </Card>
      </div>
      <div className="space-y-4 lg:pt-12">
        {error !== null && (
          <ErrorBox error={error} {...(request ? { onRetry: () => void submit() } : {})} />
        )}
        {result && (
          <Card title={result.replayed ? "Checkout esistente (replay)" : "Checkout creato"}>
            <div className="space-y-3 text-sm">
              <p className="flex items-center gap-2">
                Stato: <StatusBadge status={result.response.status} />
              </p>
              <p>Importo: {formatMoney(result.response.amount, result.response.currency)}</p>
              {checkoutUrl && STRIPE_CHECKOUT.test(checkoutUrl) ? (
                <a
                  href={checkoutUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white hover:bg-emerald-500"
                >
                  Apri la pagina di pagamento Stripe ↗
                </a>
              ) : (
                <p className="text-slate-500">Nessun link di pagamento disponibile per questo stato.</p>
              )}
              <p>
                <Link to={`/payments/${result.response.payment_id}`} className="text-indigo-600 underline">
                  Vedi il pagamento
                </Link>
              </p>
              <p className="text-xs text-slate-500">Carta di test: 4242 4242 4242 4242, qualsiasi data futura e CVC.</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
