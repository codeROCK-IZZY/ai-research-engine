import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { DocumentSummary, Page } from "../api/types";
import { FreshnessBadge, KbSelect, ReliabilityBadge, useKnowledgeBases } from "../components/knowledge";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Table } from "../components/ui";
import { dateTime, humanize, STATUS_TONE } from "../lib/format";

const PAGE = 25;
const KIND_LABEL = { upload: "File", url: "Web page", zip: "From ZIP" } as const;

export function LibraryPage() {
  const navigate = useNavigate();
  const kbs = useKnowledgeBases();
  const [q, setQ] = useState("");
  const [kb, setKb] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (q) params.set("q", q);
  if (kb) params.set("kb_id", kb);
  const docs = useQuery({
    queryKey: ["documents", params.toString()],
    queryFn: () => api<Page<DocumentSummary>>(`/documents?${params}`),
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => ["queued", "processing"].includes(d.current_version?.status ?? "")) ? 2000 : false,
  });
  const canEdit = (kbs.data ?? []).some((k) => k.role !== "reader");
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-5)));

  if (kbs.data && kbs.data.length === 0) {
    return (
      <>
        <PageHeader title="Sources" />
        <Empty>
          You are not in any workspace yet.{" "}
          <Link to="/workspaces" className="text-sky-700 hover:underline">Create a workspace</Link> or ask a workspace
          owner to add you.
        </Empty>
      </>
    );
  }
  return (
    <>
      <PageHeader
        title="Sources"
        subtitle={docs.data ? `${docs.data.total} sources in your knowledge bases` : undefined}
        actions={
          <>
            <Button variant="secondary" disabled={!selected.length}
              onClick={() => navigate(`/ask?${selected.map((s) => `doc=${s}`).join("&")}`)}>
              Ask about selected ({selected.length})
            </Button>
            <Button variant="secondary" disabled={selected.length < 2}
              onClick={() => navigate(`/analysis?${selected.map((s) => `doc=${s}`).join("&")}`)}>Compare</Button>
          </>
        }
      />
      {canEdit && <AddSources />}
      <Card className="mt-6">
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <Input aria-label="Search titles" placeholder="Search titles…" value={q}
            onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
          <KbSelect value={kb} onChange={(v) => { setKb(v); setOffset(0); }} />
        </div>
        {docs.isLoading ? <Loading /> : docs.error ? <ErrorBox error={docs.error} /> : !docs.data!.items.length ? (
          <Empty>No sources yet.{canEdit ? " Add files, a web page or a ZIP archive above." : ""}</Empty>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <th><span className="sr-only">Select</span></th><th>Title</th><th>Knowledge base</th><th>Kind</th>
                  <th>Status</th><th>Freshness</th><th>Reliability</th><th>Added</th>
                </tr>
              </thead>
              <tbody>
                {docs.data!.items.map((d) => {
                  const v = d.current_version;
                  return (
                    <tr key={d.id} className="hover:bg-slate-50">
                      <td><input type="checkbox" aria-label={`Select ${d.title}`} checked={selected.includes(d.id)}
                        onChange={() => toggle(d.id)} /></td>
                      <td className="max-w-xs">
                        <Link to={`/documents/${d.id}`} className="font-medium text-sky-700 hover:underline">{d.title}</Link>
                        <div className="truncate text-xs text-slate-500">{d.source_url ?? v?.filename}</div>
                      </td>
                      <td className="text-xs">{d.kb_name}</td>
                      <td className="text-xs">{KIND_LABEL[d.source_kind]}</td>
                      <td>{v && <Badge tone={STATUS_TONE[v.status]}>
                        {v.status === "processing" ? `processing: ${humanize(v.stage)}` : v.status}</Badge>}</td>
                      <td><FreshnessBadge f={d.freshness} /></td>
                      <td><ReliabilityBadge r={d.reliability} /></td>
                      <td className="whitespace-nowrap text-slate-600">{dateTime(d.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
              <span>{offset + 1}–{Math.min(offset + PAGE, docs.data!.total)} of {docs.data!.total}</span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>Previous</Button>
                <Button variant="secondary" disabled={offset + PAGE >= docs.data!.total}
                  onClick={() => setOffset(offset + PAGE)}>Next</Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

type Result = { name: string; ok: boolean; message: string };
type Tab = "files" | "url" | "zip";
const TAB_LABEL: Record<Tab, string> = { files: "Files", url: "Web page", zip: "ZIP archive" };

function AddSources() {
  const qc = useQueryClient();
  const [kb, setKb] = useState("");
  const [tab, setTab] = useState<Tab>("files");
  const [files, setFiles] = useState<File[]>([]);
  const [url, setUrl] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const failure = (e: unknown) => (e instanceof ApiError ? e.message : "failed");
  const add = useMutation({
    mutationFn: async (): Promise<Result[]> => {
      if (tab === "url") {
        try {
          const r = await api<{ title: string }>(`/knowledge-bases/${kb}/sources:url`, { body: { url } });
          return [{ name: url, ok: true, message: `added "${r.title}", processing…` }];
        } catch (e) {
          return [{ name: url, ok: false, message: failure(e) }];
        }
      }
      const out: Result[] = [];
      for (const f of files) {
        const form = new FormData();
        form.append("file", f);
        try {
          if (tab === "zip") {
            const r = await api<{ added: string[]; skipped: { file: string; reason: string }[] }>(
              `/knowledge-bases/${kb}/sources:zip`, { form });
            const skipped = r.skipped.map((s) => `${s.file}: ${s.reason}`).join("; ");
            out.push({ name: f.name, ok: true,
              message: `${r.added.length} files added, ${r.skipped.length} skipped${skipped ? ` (${skipped})` : ""}` });
          } else {
            form.append("kb_id", kb);
            const r = await api<{ duplicate_of: string | null }>("/documents", { form });
            out.push({ name: f.name, ok: true,
              message: r.duplicate_of ? "added — identical to an existing source" : "added, processing…" });
          }
        } catch (e) {
          out.push({ name: f.name, ok: false, message: failure(e) });
        }
      }
      return out;
    },
    onSuccess: (out) => {
      setResults(out);
      setFiles([]);
      setUrl("");
      void qc.invalidateQueries({ queryKey: ["documents"] });
      void qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(); };
  const ready = !!kb && (tab === "url" ? url.startsWith("https://") : files.length > 0);
  return (
    <Card title="Add sources" actions={
      <div role="tablist" className="flex gap-1 text-sm">
        {(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`rounded px-2 py-1 ${tab === t ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>}>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-[260px_1fr_auto]">
        <KbSelect value={kb} onChange={setKb} label="Add to knowledge base" allLabel="Choose a knowledge base…" editableOnly />
        {tab === "url" ? (
          <Input type="url" aria-label="Page URL" placeholder="https://docs.example.com/policy" value={url}
            onChange={(e) => setUrl(e.target.value)} />
        ) : (
          <input type="file" multiple={tab === "files"} aria-label={tab === "zip" ? "ZIP archive" : "Files"}
            accept={tab === "zip" ? ".zip" : ".pdf,.docx,.txt,.md,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff"}
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            className="block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm" />
        )}
        <Button type="submit" disabled={!ready} busy={add.isPending}>Add</Button>
      </form>
      <p className="mt-2 text-xs text-slate-500">
        {tab === "url" ? "One public HTTPS page is fetched (no crawling). Internal and private addresses are blocked."
          : tab === "zip" ? "PDF, Word, text, Markdown and HTML files inside the archive are added; other files are skipped."
            : "PDF, Word, text, Markdown, HTML or images (scans are OCR'd). Max 25 MB per file."}
      </p>
      {results.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm" aria-live="polite">
          {results.map((r, i) => <li key={i} className={r.ok ? "text-emerald-700" : "text-red-700"}>{r.name}: {r.message}</li>)}
        </ul>
      )}
    </Card>
  );
}
