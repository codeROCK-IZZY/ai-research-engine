import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Duplicates, GraphData, KbHealth, TopicsData } from "../api/types";
import { KbSelect, useKnowledgeBases } from "../components/knowledge";
import { Badge, Card, Empty, Loading, PageHeader, Stat } from "../components/ui";

function useKbView<T>(kbId: string, path: string) {
  return useQuery({ queryKey: ["kb", kbId, path], queryFn: () => api<T>(`/knowledge-bases/${kbId}/${path}`), enabled: !!kbId });
}

export function InsightsPage() {
  const kbs = useKnowledgeBases();
  const [kb, setKb] = useState("");
  const id = kb || kbs.data?.[0]?.id || "";
  const graph = useKbView<GraphData>(id, "graph");
  const topics = useKbView<TopicsData>(id, "topics");
  const dups = useKbView<Duplicates>(id, "duplicates");
  const health = useKbView<KbHealth>(id, "health");
  if (kbs.data && !kbs.data.length) return <Empty>You have no knowledge bases yet.</Empty>;
  return (
    <>
      <PageHeader title="Insights" subtitle="Structure of a knowledge base: concepts, topics, duplicates and source health. All rule-based."
        actions={<div className="w-80"><KbSelect value={id} onChange={setKb} allLabel={null} /></div>} />
      {health.data && (
        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat label="Sources" value={health.data.sources} />
          <Stat label="Stale" value={health.data.stale.length} sub="older than the freshness limit" />
          <Stat label="High reliability" value={health.data.reliability.high} />
          <Stat label="Low reliability" value={health.data.reliability.low} />
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Concept graph" className="lg:col-span-2">
          {graph.isLoading ? <Loading /> : !graph.data?.nodes.length ? <Empty>Not enough shared concepts yet.</Empty> : <ConceptGraph g={graph.data} />}
        </Card>
        <Card title="Topics">
          {topics.isLoading ? <Loading /> : !topics.data?.topics.length ? <Empty>{topics.data?.note ?? "No topics."}</Empty> : (
            <ul className="space-y-3">{topics.data.topics.map((t) => (
              <li key={t.topic}><p className="text-sm font-medium">{t.label}</p>
                <p className="text-xs text-slate-600">{t.sources.map((s, i) => <span key={s.id}>{i > 0 && " · "}<Link className="hover:underline" to={`/documents/${s.id}`}>{s.title}</Link></span>)}</p>
              </li>))}
            </ul>
          )}
        </Card>
        <Card title="Duplicates">
          {dups.isLoading ? <Loading /> : !dups.data || (!dups.data.exact.length && !dups.data.near.length) ? <Empty>No duplicates found.</Empty> : (
            <ul className="space-y-2 text-sm">
              {dups.data.exact.map((g, i) => <li key={`e${i}`}><Badge tone="red">identical</Badge> {g.map((d) => d.title).join(" = ")}</li>)}
              {dups.data.near.map((n, i) => <li key={`n${i}`}><Badge tone="amber">{Math.round(n.similarity * 100)}% similar</Badge> {n.a.title} ≈ {n.b.title}</li>)}
            </ul>
          )}
        </Card>
        <Card title="Stale and low-reliability sources" className="lg:col-span-2">
          {!health.data ? <Loading /> : !health.data.stale.length && !health.data.low_reliability.length ? <Empty>All sources look current.</Empty> : (
            <ul className="space-y-1 text-sm">
              {health.data.stale.map((s) => <li key={s.id}><Badge tone="red">stale</Badge> <Link className="hover:underline" to={`/documents/${s.id}`}>{s.title}</Link> <span className="text-xs text-slate-500">({s.age_days} days since {s.basis})</span></li>)}
              {health.data.low_reliability.map((s) => <li key={`l${s.id}`}><Badge tone="amber">low reliability</Badge> {s.title} <span className="text-xs text-slate-500">— {s.reasons.join("; ")}</span></li>)}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

/** Circular layout: deterministic, readable for up to ~60 nodes, no graph library needed. */
function ConceptGraph({ g }: { g: GraphData }) {
  const size = 560, r = 230, c = size / 2;
  const pos = new Map(g.nodes.map((n, i) => {
    const a = (2 * Math.PI * i) / g.nodes.length - Math.PI / 2;
    return [n.id, { x: c + r * Math.cos(a), y: c + r * Math.sin(a), a }];
  }));
  const maxW = Math.max(1, ...g.edges.map((e) => e.weight));
  return (
    <figure>
      <svg viewBox={`-120 -20 ${size + 240} ${size + 40}`} className="mx-auto w-full max-w-3xl" role="img"
        aria-label={`Concept graph with ${g.nodes.length} concepts and ${g.edges.length} connections`}>
        {g.edges.map((e) => { const a = pos.get(e.source)!, b = pos.get(e.target)!;
          return <line key={`${e.source}-${e.target}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#0369a1" strokeOpacity={0.15 + 0.6 * (e.weight / maxW)} strokeWidth={0.5 + 2 * (e.weight / maxW)} />; })}
        {g.nodes.map((n) => { const p = pos.get(n.id)!; const right = Math.cos(p.a) >= 0;
          return (
            <g key={n.id}><circle cx={p.x} cy={p.y} r={3 + Math.min(8, n.sources)} fill="#0ea5e9"><title>{`${n.label}: in ${n.sources} sources (${n.examples.join(", ")})`}</title></circle>
              <text x={p.x + (right ? 12 : -12)} y={p.y + 4} fontSize={11} textAnchor={right ? "start" : "end"} fill="#334155">{n.label}</text></g>);
        })}
      </svg>
      <figcaption className="text-center text-xs text-slate-500">{g.shown} of {g.concepts_total} concepts (those found in at least two sources). Lines join concepts that appear in the same sources.</figcaption>
    </figure>
  );
}
