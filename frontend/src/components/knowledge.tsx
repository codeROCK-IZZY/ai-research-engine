import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Freshness, KnowledgeBase, Reliability } from "../api/types";
import { Badge, Select } from "./ui";

const REL_TONE = { high: "green", medium: "amber", low: "red" } as const;

export function ReliabilityBadge({ r }: { r: Reliability }) {
  return (
    <span title={`Rule-based indicator:\n${r.reasons.join("\n")}`}>
      <Badge tone={REL_TONE[r.level]}>{r.level} reliability</Badge>
    </span>
  );
}

export function FreshnessBadge({ f }: { f: Freshness }) {
  const age = f.age_days < 60 ? `${f.age_days} d` : `${Math.round(f.age_days / 30)} mo`;
  return (
    <span title={`Age based on the ${f.basis} date. Stale after ${f.stale_after_days} days (rule-based).`}>
      <Badge tone={f.stale ? "red" : "slate"}>{f.stale ? `stale · ${age}` : age}</Badge>
    </span>
  );
}

export function useKnowledgeBases() {
  return useQuery({ queryKey: ["knowledge-bases"], queryFn: () => api<KnowledgeBase[]>("/knowledge-bases") });
}

export function KbSelect({ value, onChange, label = "Knowledge base", allLabel = "All knowledge bases", editableOnly }: {
  value: string; onChange: (v: string) => void; label?: string; allLabel?: string | null; editableOnly?: boolean;
}) {
  const kbs = useKnowledgeBases();
  const list = (kbs.data ?? []).filter((k) => !editableOnly || k.role !== "reader");
  return (
    <Select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      {allLabel !== null && <option value="">{allLabel}</option>}
      {list.map((k) => <option key={k.id} value={k.id}>{k.workspace_name} / {k.name} ({k.ready_sources})</option>)}
    </Select>
  );
}
