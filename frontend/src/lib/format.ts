import type { PaymentStatus } from "../api/types";

export function formatMoney(amountMinor: number, currency: string): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: currency.toUpperCase() }).format(
    amountMinor / 100,
  );
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
}

export const STATUS_LABEL: Record<PaymentStatus, string> = {
  creating: "Preparing",
  open: "Awaiting payment",
  creation_unknown: "Outcome unknown",
  paid: "Paid",
  failed: "Failed",
  expired: "Expired",
};

export const STATUS_TONE: Record<PaymentStatus, string> = {
  creating: "bg-sky-100 text-sky-800 ring-sky-200",
  open: "bg-amber-100 text-amber-800 ring-amber-200",
  creation_unknown: "bg-violet-100 text-violet-800 ring-violet-200",
  paid: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  failed: "bg-rose-100 text-rose-800 ring-rose-200",
  expired: "bg-slate-200 text-slate-700 ring-slate-300",
};

/** Solid colour of each status in the overview bars: same hue family as STATUS_TONE. */
export const STATUS_BAR: Record<PaymentStatus, string> = {
  creating: "bg-sky-500",
  open: "bg-amber-500",
  creation_unknown: "bg-violet-500",
  paid: "bg-emerald-500",
  failed: "bg-rose-500",
  expired: "bg-slate-400",
};

export const SOURCE_LABEL: Record<string, string> = {
  api: "API",
  webhook: "Stripe webhook",
  reconciliation: "Reconciliation",
  migration: "Migration",
};

export function paymentIdFromSourceKey(sourceKey: string): string | null {
  const match = /^payment:([0-9a-f-]{36})$/i.exec(sourceKey);
  return match?.[1] ?? null;
}

export interface AnswerPart {
  text: string;
  citation: number | null;
}

export function splitCitations(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = [];
  const pattern = /\[(\d{1,2})\]/g;
  let last = 0;
  for (const match of answer.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: answer.slice(last, index), citation: null });
    parts.push({ text: match[0], citation: Number(match[1]) });
    last = index + match[0].length;
  }
  if (last < answer.length) parts.push({ text: answer.slice(last), citation: null });
  return parts;
}
