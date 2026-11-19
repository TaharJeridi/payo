import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useAsk } from "../api/hooks";
import type { RagAnswer } from "../api/types";
import { LogoMark } from "../components/Logo";
import { Button, Card, ErrorBox } from "../components/ui";
import { paymentIdFromSourceKey, splitCitations } from "../lib/format";

interface Exchange {
  id: number;
  question: string;
  answer: RagAnswer | null;
  error: unknown;
}

const SUGGESTIONS = [
  "How many payments are still awaiting payment?",
  "Why can a payment end up with an unknown outcome?",
  "How does reconciliation with Stripe work?",
];

export function AssistantPage() {
  const [params] = useSearchParams();
  const [question, setQuestion] = useState(params.get("q") ?? "");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const ask = useAsk();
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [exchanges]);

  function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || ask.isPending) return;
    const id = nextId.current++;
    setExchanges((current) => [...current, { id, question: trimmed, answer: null, error: null }]);
    setQuestion("");
    ask.mutate(trimmed, {
      onSuccess: (answer) => setExchanges((current) => current.map((e) => (e.id === id ? { ...e, answer } : e))),
      onError: (error) => setExchanges((current) => current.map((e) => (e.id === id ? { ...e, error } : e))),
    });
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    submit(question);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <header className="flex items-start gap-3">
        <LogoMark className="mt-1 h-9 w-9 shrink-0" />
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-navy">Ask Payo</h1>
          <p className="mt-1 text-sm text-muted">
            Ask Payo about your payments or how the platform works. Answers use only your data and the documentation,
            cite their sources, and are either calculated by the backend or written by the local Qwen model. Always
            check amounts and statuses on the payment page.
          </p>
        </div>
      </header>

      {exchanges.length === 0 && (
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => submit(suggestion)}
              className="rounded-full bg-surface px-3 py-1.5 text-sm text-navy ring-1 ring-inset ring-field hover:ring-primary"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <div className="space-y-4" aria-live="polite">
        {exchanges.map((exchange) => (
          <div key={exchange.id} className="space-y-2">
            <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-navy px-4 py-2 text-sm text-white">
              {exchange.question}
            </p>
            {exchange.answer && <AnswerView answer={exchange.answer} exchangeId={exchange.id} />}
            {exchange.error !== null && <ErrorBox error={exchange.error} />}
            {!exchange.answer && exchange.error === null && (
              <p role="status" className="text-sm text-muted">
                Payo is preparing the answer…
              </p>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={onSubmit}
        className="sticky bottom-4 flex gap-2 rounded-card border border-field bg-surface p-2 shadow-card focus-within:border-primary"
      >
        <label htmlFor="question" className="sr-only">
          Question
        </label>
        <input
          id="question"
          value={question}
          maxLength={1000}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask Payo about your payments…"
          className="min-w-0 flex-1 rounded-lg bg-transparent px-3 py-2 text-sm text-ink placeholder:text-muted focus:outline-none"
        />
        <Button type="submit" disabled={ask.isPending || question.trim() === ""}>
          Send
        </Button>
      </form>
    </div>
  );
}

function AnswerView({ answer, exchangeId }: { answer: RagAnswer; exchangeId: number }) {
  const anchor = (n: number) => `source-${exchangeId}-${n}`;
  const known = new Set(answer.sources.map((s) => s.number));
  return (
    <Card>
      <p className="whitespace-pre-wrap text-sm leading-relaxed" data-testid="answer">
        {splitCitations(answer.answer).map((part, index) =>
          part.citation !== null && known.has(part.citation) ? (
            <a
              key={index}
              href={`#${anchor(part.citation)}`}
              className="mx-0.5 rounded bg-primary/10 px-1 text-xs font-semibold text-primary"
            >
              {part.citation}
            </a>
          ) : (
            <span key={index}>{part.text}</span>
          ),
        )}
      </p>
      {answer.sources.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Sources</h3>
          <ol className="space-y-1 text-sm">
            {answer.sources.map((source) => {
              const paymentId = paymentIdFromSourceKey(source.source_key);
              return (
                <li key={source.number} id={anchor(source.number)} className={source.cited ? "" : "text-muted"}>
                  <span className="mr-2 font-semibold">[{source.number}]</span>
                  {paymentId ? (
                    <Link to={`/payments/${paymentId}`} className="text-primary underline">
                      {source.title}
                    </Link>
                  ) : (
                    <span>{source.title}</span>
                  )}
                  <span className="ml-2 text-xs text-muted">
                    {source.cited ? "cited" : "not cited"} · {source.score.toFixed(2)}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      {(answer.warnings ?? []).length > 0 && (
        <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800" data-testid="answer-warnings">
          Answer not verified by the backend: {(answer.warnings ?? []).map(warningLabel).join("; ")}.
        </p>
      )}
      {!answer.used_llm && answer.sources.length > 0 && (
        <p className="mt-2 text-xs text-muted">Answer calculated by the backend from Payo data, without Qwen.</p>
      )}
      {!answer.used_llm && answer.sources.length === 0 && (
        <p className="mt-2 text-xs text-muted">No relevant source: Qwen was not called.</p>
      )}
    </Card>
  );
}

const WARNING_LABELS: Record<string, string> = {
  uncited: "no source cited",
  invalid_citation: "citation of a source that does not exist",
  unknown_payment_id: "payment id not in the sources",
  amount_not_in_sources: "amount not in the cited sources",
  amount_wrong_payment: "amount attributed to the wrong payment",
  threshold_unverified: "threshold differs from the filter applied",
  aggregate_unverified: "total not calculated by the backend",
  contradicted_evidence: "the generated answer denied payments that were found and was replaced with backend data",
  personal_data: "contains an email address",
};

export function warningLabel(code: string): string {
  return WARNING_LABELS[code] ?? code;
}
