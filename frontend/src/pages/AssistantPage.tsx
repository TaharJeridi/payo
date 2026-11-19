import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useAsk } from "../api/hooks";
import type { RagAnswer } from "../api/types";
import { Button, Card, ErrorBox } from "../components/ui";
import { paymentIdFromSourceKey, splitCitations } from "../lib/format";

interface Exchange {
  id: number;
  question: string;
  answer: RagAnswer | null;
  error: unknown;
}

const SUGGESTIONS = [
  "Quanti pagamenti sono ancora in attesa?",
  "Perché un pagamento può risultare con esito sconosciuto?",
  "Come funziona la riconciliazione con Stripe?",
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
      <div>
        <h1 className="text-2xl font-bold">Assistente</h1>
        <p className="text-sm text-slate-600">
          Qwen risponde solo con i dati dei tuoi pagamenti e la documentazione PayFlow, citando le fonti. Verifica sempre
          importi e stati nella pagina del pagamento.
        </p>
      </div>

      {exchanges.length === 0 && (
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => submit(suggestion)}
              className="rounded-full bg-white px-3 py-1 text-sm text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <div className="space-y-4" aria-live="polite">
        {exchanges.map((exchange) => (
          <div key={exchange.id} className="space-y-2">
            <p className="ml-auto w-fit max-w-[85%] rounded-2xl bg-indigo-600 px-4 py-2 text-sm text-white">
              {exchange.question}
            </p>
            {exchange.answer && <AnswerView answer={exchange.answer} exchangeId={exchange.id} />}
            {exchange.error !== null && <ErrorBox error={exchange.error} />}
            {!exchange.answer && exchange.error === null && (
              <p role="status" className="text-sm text-slate-500">
                Qwen sta rispondendo…
              </p>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form onSubmit={onSubmit} className="sticky bottom-4 flex gap-2 rounded-xl border bg-white p-2 shadow-sm">
        <label htmlFor="question" className="sr-only">
          Domanda
        </label>
        <input
          id="question"
          value={question}
          maxLength={1000}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Chiedi qualcosa sui tuoi pagamenti…"
          className="flex-1 rounded-lg px-3 py-2 text-sm focus:outline-none"
        />
        <Button type="submit" disabled={ask.isPending || question.trim() === ""}>
          Invia
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
              className="mx-0.5 rounded bg-indigo-100 px-1 text-xs font-semibold text-indigo-700"
            >
              {part.citation}
            </a>
          ) : (
            <span key={index}>{part.text}</span>
          ),
        )}
      </p>
      {answer.sources.length > 0 && (
        <div className="mt-4 border-t pt-3">
          <h3 className="mb-2 text-xs font-semibold uppercase text-slate-500">Fonti</h3>
          <ol className="space-y-1 text-sm">
            {answer.sources.map((source) => {
              const paymentId = paymentIdFromSourceKey(source.source_key);
              return (
                <li key={source.number} id={anchor(source.number)} className={source.cited ? "" : "text-slate-500"}>
                  <span className="mr-2 font-semibold">[{source.number}]</span>
                  {paymentId ? (
                    <Link to={`/payments/${paymentId}`} className="text-indigo-700 underline">
                      {source.title}
                    </Link>
                  ) : (
                    <span>{source.title}</span>
                  )}
                  <span className="ml-2 text-xs text-slate-400">
                    {source.cited ? "citata" : "non citata"} · {source.score.toFixed(2)}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      {(answer.warnings ?? []).length > 0 && (
        <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800" data-testid="answer-warnings">
          Risposta non verificata dal backend: {(answer.warnings ?? []).map(warningLabel).join("; ")}.
        </p>
      )}
      {!answer.used_llm && answer.sources.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">Risposta scritta dal backend con i dati PayFlow, senza Qwen.</p>
      )}
      {!answer.used_llm && answer.sources.length === 0 && (
        <p className="mt-2 text-xs text-slate-500">Nessuna fonte pertinente: Qwen non è stato interpellato.</p>
      )}
    </Card>
  );
}

const WARNING_LABELS: Record<string, string> = {
  uncited: "nessuna fonte citata",
  invalid_citation: "citazione a una fonte inesistente",
  unknown_payment_id: "id di pagamento non presente nelle fonti",
  amount_not_in_sources: "importo non presente nelle fonti citate",
  amount_wrong_payment: "importo attribuito al pagamento sbagliato",
  threshold_unverified: "soglia diversa dal filtro applicato",
  aggregate_unverified: "totale non calcolato dal backend",
  contradicted_evidence: "la risposta generata negava pagamenti trovati ed è stata sostituita dai dati del backend",
  personal_data: "contiene un indirizzo email",
};

export function warningLabel(code: string): string {
  return WARNING_LABELS[code] ?? code;
}
