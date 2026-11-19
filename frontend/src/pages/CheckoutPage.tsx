import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import type { CheckoutResult } from "../api/client";
import { useProducts } from "../api/hooks";
import type { CheckoutRequest } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, ErrorBox, FIELD_CLASS, PageHeader, Spinner, StatusBadge } from "../components/ui";
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
        onRetry: (attempt) => setNotice(`Outcome not confirmed yet, attempt ${attempt + 1} with the same key…`),
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
      <div className="space-y-4">
        <PageHeader title="New checkout" description="Create a test payment with Stripe in test mode" />
        <Card>
          <form onSubmit={onSubmit} className="space-y-4">
            <label className="block text-sm font-medium text-ink">
              Product
              <select
                value={selected?.id ?? ""}
                onChange={(event) => setProductId(event.target.value)}
                className={FIELD_CLASS}
              >
                {products.data.map((product) => (
                  <option key={product.id} value={product.id}>
                    {product.name} · {formatMoney(product.unit_amount, product.currency)}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-ink">
              Quantity
              <input
                type="number"
                min={1}
                max={MAX_QUANTITY}
                inputMode="numeric"
                value={quantityText}
                aria-invalid={!quantityValid}
                onChange={(event) => setQuantityText(event.target.value)}
                className={`${FIELD_CLASS} w-32`}
              />
            </label>
            <label className="block text-sm font-medium text-ink">
              Customer email (optional)
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className={FIELD_CLASS}
              />
            </label>
            {!quantityValid && (
              <p role="alert" className="text-sm text-rose-700">
                Quantity must be a whole number between 1 and {MAX_QUANTITY}.
              </p>
            )}
            {selected && quantityValid && (
              <p className="text-sm text-muted">
                Total: <strong className="text-ink">{formatMoney(selected.unit_amount * quantity, selected.currency)}</strong> · calculated
                by the backend from the catalogue
              </p>
            )}
            <Button type="submit" disabled={busy || !request}>
              {busy ? "Creating…" : "Create checkout"}
            </Button>
            {notice && (
              <p role="status" className="text-sm text-amber-700">
                {notice}
              </p>
            )}
          </form>
        </Card>
      </div>
      <div className="space-y-4 lg:pt-16">
        {error !== null && (
          <ErrorBox error={error} {...(request ? { onRetry: () => void submit() } : {})} />
        )}
        {result && (
          <Card title={result.replayed ? "Existing checkout (replay)" : "Checkout created"}>
            <div className="space-y-3 text-sm">
              <p className="flex items-center gap-2">
                Status: <StatusBadge status={result.response.status} />
              </p>
              <p>Amount: {formatMoney(result.response.amount, result.response.currency)}</p>
              {checkoutUrl && STRIPE_CHECKOUT.test(checkoutUrl) ? (
                <a
                  href={checkoutUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block rounded-lg bg-primary px-4 py-2 font-semibold text-white hover:bg-primary-strong"
                >
                  Open the Stripe payment page ↗
                </a>
              ) : (
                <p className="text-muted">No payment link is available for this status.</p>
              )}
              <p>
                <Link to={`/payments/${result.response.payment_id}`} className="text-primary underline">
                  View the payment
                </Link>
              </p>
              <p className="text-xs text-muted">Test card: 4242 4242 4242 4242, any future date and any CVC.</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
