import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, Table } from "../components/ui";
import { formatValue, humanize } from "../lib/format";

interface Ref { id: string; title: string }
interface CompareResult {
  documents: (Ref & { doc_type: string | null })[];
  pairs: { a: Ref; b: Ref; text_similarity: number; changed_lines: string[]; field_differences: { field: string; a: unknown; b: unknown }[] | null }[];
  method: string;
}
interface Duplicates { exact: Ref[][]; near: { a: Ref; b: Ref; similarity: number }[]; threshold: number }

export function AnalysisPage() {
  const [params] = useSearchParams();
  const ids = params.getAll("doc");
  const compare = useMutation({ mutationFn: () => api<CompareResult>("/compare", { body: { document_ids: ids } }) });
  const dups = useQuery({ queryKey: ["duplicates"], queryFn: () => api<Duplicates>("/duplicates") });
  useEffect(() => { if (ids.length >= 2 && !compare.data && !compare.isPending) compare.mutate(); }, [ids.length, compare]);
  return (
    <>
      <PageHeader title="Compare & duplicates" subtitle="Deterministic comparison: text differences and extracted-field differences." />
      {ids.length < 2 ? (
        <Card><Empty>Select two to five documents in the Library and choose “Compare”.</Empty></Card>
      ) : compare.isPending ? <Loading /> : compare.error ? <ErrorBox error={compare.error} /> : compare.data && (
        <div className="space-y-4">
          {compare.data.pairs.map((p, i) => (
            <Card key={i} title={<>{p.a.title} <span className="text-slate-400">vs</span> {p.b.title}</>}
              actions={<Badge tone={p.text_similarity > 0.9 ? "amber" : "slate"}>{(p.text_similarity * 100).toFixed(1)}% similar text</Badge>}>
              {p.field_differences && (p.field_differences.length ? (
                <Table>
                  <thead><tr><th>Field</th><th>{p.a.title}</th><th>{p.b.title}</th></tr></thead>
                  <tbody>{p.field_differences.map((d) => (
                    <tr key={d.field}><td className="font-medium">{humanize(d.field)}</td><td>{formatValue(d.a)}</td><td>{formatValue(d.b)}</td></tr>
                  ))}</tbody>
                </Table>
              ) : <p className="text-sm text-emerald-700">All extracted fields are identical.</p>)}
              {p.changed_lines.length > 0 && (
                <details className="mt-3 text-xs"><summary className="cursor-pointer text-slate-600">{p.changed_lines.length} changed line(s)</summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded bg-slate-900 p-2 text-slate-100">
                    {p.changed_lines.map((l, j) => <div key={j} className={l.startsWith("+") ? "text-emerald-300" : "text-red-300"}>{l}</div>)}
                  </pre>
                </details>
              )}
            </Card>
          ))}
        </div>
      )}
      <Card title="Duplicates in your library" className="mt-6" actions={<Button variant="secondary" onClick={() => void dups.refetch()}>Refresh</Button>}>
        {dups.isLoading ? <Loading /> : dups.data && (
          <>
            <h3 className="mb-1 text-sm font-semibold">Exact duplicates (identical files)</h3>
            {dups.data.exact.length ? (
              <ul className="mb-4 list-disc space-y-1 pl-5 text-sm">
                {dups.data.exact.map((g, i) => <li key={i}>{g.map((d, j) => <span key={d.id}>{j > 0 && " = "}<Link className="text-sky-700 hover:underline" to={`/documents/${d.id}`}>{d.title}</Link></span>)}</li>)}
              </ul>
            ) : <p className="mb-4 text-sm text-slate-500">None.</p>}
            <h3 className="mb-1 text-sm font-semibold">Near-duplicates (≥ {dups.data.threshold * 100}% estimated text overlap)</h3>
            {dups.data.near.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {dups.data.near.map((n, i) => (
                  <li key={i}><Link className="text-sky-700 hover:underline" to={`/documents/${n.a.id}`}>{n.a.title}</Link> ≈{" "}
                    <Link className="text-sky-700 hover:underline" to={`/documents/${n.b.id}`}>{n.b.title}</Link>{" "}
                    <span className="text-slate-500">({(n.similarity * 100).toFixed(0)}%)</span>{" "}
                    <Link className="text-xs text-sky-700 hover:underline" to={`/analysis?doc=${n.a.id}&doc=${n.b.id}`}>compare</Link></li>
                ))}
              </ul>
            ) : <p className="text-sm text-slate-500">None.</p>}
          </>
        )}
      </Card>
    </>
  );
}
