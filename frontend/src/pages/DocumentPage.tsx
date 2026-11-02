import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, download } from "../api/client";
import type { DocumentDetail, PageContent } from "../api/types";
import { FreshnessBadge, ReliabilityBadge } from "../components/knowledge";
import { Badge, Button, Card, Empty, ErrorBox, Loading, Select, SourceTag, Table } from "../components/ui";
import { bytes, dateTime, formatValue, humanize, STATUS_TONE } from "../lib/format";

const TYPES = ["invoice", "contract", "receipt", "resume", "report", "letter", "policy", "other"];
const STAGES = ["extract", "classify", "index", "understand", "deduplicate"];

export function DocumentPage() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const doc = useQuery({
    queryKey: ["document", id],
    queryFn: () => api<DocumentDetail>(`/documents/${id}`),
    refetchInterval: (q) => (["queued", "processing"].includes(q.state.data?.current_version?.status ?? "") ? 1500 : false),
  });
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`/documents/${id}`, { method: "PATCH", body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["document", id] }),
  });
  const remove = useMutation({
    mutationFn: () => api(`/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["documents"] }); navigate("/"); },
  });

  if (doc.isLoading) return <Loading />;
  if (doc.error) return <ErrorBox error={doc.error} />;
  const d = doc.data!;
  const v = d.current_version;
  const canEdit = d.access !== "read";
  const highlight = params.get("start") && params.get("end")
    ? { start: Number(params.get("start")), end: Number(params.get("end")) } : null;

  return (
    <>
      <nav className="mb-2 text-sm"><Link to="/" className="text-sky-700 hover:underline">← Sources</Link></nav>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{d.title}</h1>
          <p className="text-sm text-slate-500">
            {v?.filename} · {v ? bytes(v.size_bytes) : ""} · {v?.page_count ?? "?"} page(s) · language {v?.language ?? "unknown"} ·
            version {v?.version_no} · {d.kb_name} · {d.access === "read" ? "read only" : d.access === "edit" ? "you can edit" : "you are an owner"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => navigate(`/ask?doc=${d.id}`)} disabled={v?.status !== "ready"}>Ask about this source</Button>
          <Button variant="secondary" onClick={() => void download(`/documents/${d.id}/file`, v?.filename ?? "document")}>Download original</Button>
          {d.access !== "read" && (
            <Button variant="danger" busy={remove.isPending}
              onClick={() => { if (window.confirm(`Delete "${d.title}" and all its versions?`)) remove.mutate(); }}>Delete</Button>
          )}
        </div>
      </div>
      <ErrorBox error={update.error ?? remove.error} />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Processing">
          {v && <p className="mb-3"><Badge tone={STATUS_TONE[v.status]}>{v.status}</Badge></p>}
          <ol className="space-y-1.5 text-sm">
            {STAGES.map((s) => {
              const st = v?.stages[s];
              return (
                <li key={s} className="flex items-center justify-between gap-2">
                  <span className="capitalize">{s}</span>
                  <span className="text-xs text-slate-500">
                    {!st ? "pending" : st.status === "done" ? `✓ ${st.ms} ms` : st.status === "failed" ? <span className="text-red-700">failed</span> : "running…"}
                  </span>
                </li>
              );
            })}
          </ol>
          {v?.error && <p className="mt-3 rounded bg-red-50 p-2 text-xs text-red-800" role="alert">{v.error}</p>}
          {v?.stages.extract?.ocr_pages ? <p className="mt-3 text-xs text-slate-500">{String(v.stages.extract.ocr_pages)} page(s) read with OCR.</p> : null}
        </Card>

        <Card title="Summary" className="lg:col-span-2" actions={<SourceTag source={d.summary?.source} />}>
          {d.summary ? <p className="text-sm leading-relaxed text-slate-800">{d.summary.text}</p> : <Empty>Not available yet.</Empty>}
          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-slate-600">Type:</span>
            {canEdit ? (
              <Select aria-label="Document type" className="w-40" value={d.doc_type ?? ""}
                onChange={(e) => update.mutate({ doc_type: e.target.value })}>
                <option value="" disabled>—</option>
                {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            ) : <span className="capitalize">{d.doc_type ?? "—"}</span>}
            <SourceTag source={d.doc_type_source === "user" ? null : d.doc_type_source} />
            {d.doc_type_source === "user" && <span className="text-xs text-slate-500">(set manually)</span>}
          </div>
        </Card>

        {d.fields && <FieldsCard fields={d.fields} />}
        <PageViewer id={d.id} pageCount={v?.page_count ?? 0} initialPage={Number(params.get("page") ?? 1)} highlight={highlight} />
        <VersionsCard doc={d} canEdit={canEdit} />
        {d.access === "owner" && <SourceCard doc={d} />}
        {d.entities.length > 0 && (
          <Card title="Entities found" className="lg:col-span-3">
            <div className="flex flex-wrap gap-2">
              {d.entities.slice(0, 60).map((e, i) => (
                <span key={i} className="rounded border border-slate-200 px-2 py-1 text-xs" title={`page ${e.page_no}`}>
                  <span className="text-slate-500">{e.type}: </span>{e.normalized ?? e.value}
                </span>
              ))}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

function FieldsCard({ fields }: { fields: NonNullable<DocumentDetail["fields"]> }) {
  const { valid, missing_fields, issues } = fields.validation;
  const items = Array.isArray(fields.data.line_items) ? (fields.data.line_items as Record<string, unknown>[]) : [];
  return (
    <Card title={`Extracted ${fields.kind} fields`} className="lg:col-span-3"
      actions={<><SourceTag source={fields.source} /><Badge tone={valid ? "green" : "amber"}>{valid ? "validation passed" : "needs review"}</Badge></>}>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(fields.data).filter(([k]) => k !== "line_items" && k !== "clauses").map(([k, val]) => (
          <div key={k}><dt className="text-xs text-slate-500">{humanize(k)}</dt><dd className="font-medium">{formatValue(val)}</dd></div>
        ))}
      </dl>
      {(missing_fields.length > 0 || issues.length > 0) && (
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-sm text-amber-800">
          {missing_fields.length > 0 && <li>Missing: {missing_fields.map(humanize).join(", ")}</li>}
          {issues.map((i) => <li key={i}>{i}</li>)}
        </ul>
      )}
      {items.length > 0 && (
        <div className="mt-4"><Table>
          <thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead>
          <tbody>{items.map((it, i) => (
            <tr key={i}><td>{formatValue(it.description)}</td><td>{formatValue(it.quantity)}</td>
              <td>{formatValue(it.unit_price)}</td><td>{formatValue(it.amount)}</td></tr>
          ))}</tbody>
        </Table></div>
      )}
      <p className="mt-3 text-xs text-slate-500">Validation rules (totals add up, dates in order, required fields present) are deterministic and run on every extraction, whoever produced it.</p>
    </Card>
  );
}

function PageViewer({ id, pageCount, initialPage, highlight }: {
  id: string; pageCount: number; initialPage: number; highlight: { start: number; end: number } | null;
}) {
  const [page, setPage] = useState(Math.max(1, initialPage));
  const p = useQuery({ queryKey: ["page", id, page], queryFn: () => api<PageContent>(`/documents/${id}/pages/${page}`), enabled: pageCount > 0 });
  const h = page === initialPage ? highlight : null;
  return (
    <Card title="Extracted text" className="lg:col-span-2" actions={pageCount > 0 && (
      <div className="flex items-center gap-2 text-sm">
        <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">‹</Button>
        <span>Page {page} / {pageCount}</span>
        <Button variant="secondary" disabled={page >= pageCount} onClick={() => setPage(page + 1)} aria-label="Next page">›</Button>
      </div>)}>
      {p.isLoading ? <Loading /> : p.error ? <ErrorBox error={p.error} /> : !p.data ? <Empty>Not processed yet.</Empty> : (
        <>
          {p.data.ocr_used && <p className="mb-2 text-xs text-amber-700">This page was scanned and read with OCR (confidence {p.data.ocr_confidence?.toFixed(0)}%). Small recognition errors are possible.</p>}
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 font-sans text-sm leading-relaxed text-slate-800">
            {h ? <>{p.data.text.slice(0, h.start)}<mark className="rounded bg-yellow-200 px-0.5" ref={(el) => el?.scrollIntoView({ block: "center" })}>{p.data.text.slice(h.start, h.end)}</mark>{p.data.text.slice(h.end)}</> : p.data.text}
          </pre>
        </>
      )}
    </Card>
  );
}

function VersionsCard({ doc, canEdit }: { doc: DocumentDetail; canEdit: boolean }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [diffPair, setDiffPair] = useState<[number, number] | null>(null);
  const upload = useMutation({
    mutationFn: () => { const f = new FormData(); f.append("file", file!); return api(`/documents/${doc.id}/versions`, { form: f }); },
    onSuccess: () => { setFile(null); void qc.invalidateQueries({ queryKey: ["document", doc.id] }); },
  });
  const diff = useQuery({
    queryKey: ["diff", doc.id, diffPair],
    queryFn: () => api<{ similarity: number; added: number; removed: number; diff: string[] }>(
      `/documents/${doc.id}/diff?base=${diffPair![0]}&other=${diffPair![1]}`),
    enabled: !!diffPair,
  });
  return (
    <Card title="Versions">
      <ul className="space-y-2 text-sm">
        {doc.versions.map((v, i) => (
          <li key={v.id} className="flex items-center justify-between gap-2">
            <span>v{v.version_no} · {dateTime(v.created_at)} <Badge tone={STATUS_TONE[v.status]}>{v.status}</Badge></span>
            {i + 1 < doc.versions.length && (
              <button className="text-xs text-sky-700 hover:underline" onClick={() => setDiffPair([doc.versions[i + 1]!.version_no, v.version_no])}>
                diff with v{doc.versions[i + 1]!.version_no}
              </button>
            )}
          </li>
        ))}
      </ul>
      {canEdit && (
        <form className="mt-4 space-y-2" onSubmit={(e: FormEvent) => { e.preventDefault(); if (file) upload.mutate(); }}>
          <input type="file" aria-label="New version file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="block w-full text-xs" />
          <Button type="submit" variant="secondary" disabled={!file} busy={upload.isPending}>Upload new version</Button>
          <ErrorBox error={upload.error} />
        </form>
      )}
      {diff.data && (
        <div className="mt-4 text-xs">
          <p className="mb-1 text-slate-600">{(diff.data.similarity * 100).toFixed(1)}% similar · +{diff.data.added} / −{diff.data.removed} lines</p>
          <pre className="max-h-64 overflow-auto rounded bg-slate-900 p-2 text-slate-100">
            {diff.data.diff.map((l, i) => <div key={i} className={l.startsWith("+") ? "text-emerald-300" : l.startsWith("-") ? "text-red-300" : ""}>{l}</div>)}
          </pre>
        </div>
      )}
    </Card>
  );
}

function SourceCard({ doc }: { doc: DocumentDetail }) {
  const qc = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api<{ outcome: string }>(`/documents/${doc.id}/refresh`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["document", doc.id] }),
  });
  return (
    <Card title="Source" className="lg:col-span-2">
      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <dt className="text-slate-500">Knowledge base</dt><dd>{doc.kb_name}</dd>
        <dt className="text-slate-500">Origin</dt>
        <dd>{doc.source_url ? <a className="text-sky-700 hover:underline" href={doc.source_url} rel="noreferrer noopener" target="_blank">{doc.source_url}</a> : doc.source_kind === "zip" ? "ZIP archive" : "Uploaded file"}</dd>
        <dt className="text-slate-500">Published</dt><dd>{doc.published_at ? dateTime(doc.published_at) : "unknown"}</dd>
        {doc.fetched_at && <><dt className="text-slate-500">Last fetched</dt><dd>{dateTime(doc.fetched_at)}</dd></>}
        <dt className="text-slate-500">Freshness</dt><dd><FreshnessBadge f={doc.freshness} /></dd>
        <dt className="text-slate-500">Reliability</dt><dd><ReliabilityBadge r={doc.reliability} /></dd>
      </dl>
      <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">{doc.reliability.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      <p className="mt-1 text-[11px] text-slate-400">Freshness and reliability are rule-based indicators.</p>
      {doc.source_kind === "url" && doc.access !== "read" && (
        <div className="mt-3 flex items-center gap-2">
          <Button variant="secondary" busy={refresh.isPending} onClick={() => refresh.mutate()}>Re-fetch page</Button>
          {refresh.data && <span className="text-xs text-slate-600">{refresh.data.outcome === "unchanged" ? "No changes since the last fetch." : "Updated: a new version is being processed."}</span>}
        </div>
      )}
      <ErrorBox error={refresh.error} />
    </Card>
  );
}
