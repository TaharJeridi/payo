import { useApiHealth, useRagHealth } from "../api/hooks";
import { Button, Card, PageHeader } from "../components/ui";

function Indicator({ label, state }: { label: string; state: "ok" | "down" | "pending" }) {
  const tone = state === "ok" ? "bg-emerald-500" : state === "down" ? "bg-rose-500" : "bg-field";
  const text = state === "ok" ? "operativo" : state === "down" ? "non disponibile" : "verifica…";
  return (
    <li className="flex items-center justify-between py-2">
      <span className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${tone}`} aria-hidden />
        {label}
      </span>
      <span className="text-sm text-muted" data-testid={`state-${label}`}>
        {text}
      </span>
    </li>
  );
}

export function StatusPage() {
  const api = useApiHealth();
  const rag = useRagHealth();
  const apiState = api.isSuccess ? "ok" : api.isError ? "down" : "pending";
  const ragState = (value: string | undefined) =>
    rag.isError ? "down" : value === undefined ? "pending" : value === "ok" ? "ok" : "down";

  return (
    <div className="max-w-xl space-y-4">
      <PageHeader title="Stato sistema" description="Servizi da cui dipendono la dashboard e Ask Payo" />
      <Card
        action={
          <Button variant="secondary" onClick={() => void Promise.all([api.refetch(), rag.refetch()])}>
            Aggiorna
          </Button>
        }
      >
        <ul className="divide-y divide-line">
          <Indicator label="API" state={apiState} />
          <Indicator label="Ollama" state={ragState(rag.data?.ollama)} />
          <Indicator label="Qdrant" state={ragState(rag.data?.qdrant)} />
        </ul>
        {api.data && <p className="mt-3 text-xs text-muted">Stripe: modalità {api.data.stripe_mode}</p>}
      </Card>
    </div>
  );
}
