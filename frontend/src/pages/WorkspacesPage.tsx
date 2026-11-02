import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { KnowledgeBase, Member, SavedAnswer, Workspace, WorkspaceRole } from "../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Select, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

const ROLE_TONE = { owner: "violet", editor: "sky", reader: "slate" } as const;

export function WorkspacesPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const ws = useQuery({ queryKey: ["workspaces"], queryFn: () => api<Workspace[]>("/workspaces") });
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api<Workspace>("/workspaces", { body: { name } }),
    onSuccess: (w) => { setName(""); setSelected(w.id); void qc.invalidateQueries({ queryKey: ["workspaces"] }); },
  });
  const current = ws.data?.find((w) => w.id === (selected ?? ws.data?.[0]?.id));
  return (
    <>
      <PageHeader title="Workspaces" subtitle="Membership decides which knowledge bases you can search and ask." />
      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <Card title="Your workspaces">
          {ws.isLoading ? <Loading /> : !ws.data?.length ? <Empty>None yet.</Empty> : (
            <ul className="space-y-1">
              {ws.data.map((w) => (
                <li key={w.id}>
                  <button onClick={() => setSelected(w.id)}
                    className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm ${current?.id === w.id ? "bg-sky-50 font-medium" : "hover:bg-slate-50"}`}>
                    <span>{w.name}</span><Badge tone={ROLE_TONE[w.role]}>{w.role}</Badge>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {can("member") && (
            <form className="mt-4 flex gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
              <Input aria-label="New workspace name" placeholder="New workspace…" value={name} onChange={(e) => setName(e.target.value)} />
              <Button type="submit" disabled={!name.trim()} busy={create.isPending}>Create</Button>
            </form>
          )}
          <ErrorBox error={create.error} />
        </Card>
        {current ? <WorkspaceDetail ws={current} /> : <Empty>Select or create a workspace.</Empty>}
      </div>
    </>
  );
}

function WorkspaceDetail({ ws }: { ws: Workspace }) {
  const qc = useQueryClient();
  const owner = ws.role === "owner";
  const kbs = useQuery({ queryKey: ["knowledge-bases", ws.id], queryFn: () => api<KnowledgeBase[]>(`/knowledge-bases?workspace_id=${ws.id}`) });
  const members = useQuery({ queryKey: ["members", ws.id], queryFn: () => api<Member[]>(`/workspaces/${ws.id}/members`) });
  const saved = useQuery({ queryKey: ["saved", ws.id], queryFn: () => api<SavedAnswer[]>(`/workspaces/${ws.id}/saved-answers`) });
  const [kbName, setKbName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("reader");
  const refresh = () => {
    for (const k of [["knowledge-bases"], ["members", ws.id], ["workspaces"]]) void qc.invalidateQueries({ queryKey: k });
  };
  const addKb = useMutation({ mutationFn: () => api(`/workspaces/${ws.id}/knowledge-bases`, { body: { name: kbName } }),
    onSuccess: () => { setKbName(""); refresh(); } });
  const addMember = useMutation({ mutationFn: () => api(`/workspaces/${ws.id}/members`, { method: "PUT", body: { email, role } }),
    onSuccess: () => { setEmail(""); refresh(); } });
  const removeMember = useMutation({ mutationFn: (id: string) => api(`/workspaces/${ws.id}/members/${id}`, { method: "DELETE" }),
    onSuccess: refresh });
  return (
    <div className="space-y-6">
      <Card title={`Knowledge bases in ${ws.name}`}>
        {kbs.isLoading ? <Loading /> : !kbs.data?.length ? <Empty>No knowledge bases yet.</Empty> : (
          <Table>
            <thead><tr><th>Name</th><th>Sources</th><th>Stale after</th><th>Created</th></tr></thead>
            <tbody>{kbs.data.map((k) => (
              <tr key={k.id}><td className="font-medium">{k.name}</td><td className="tabular-nums">{k.ready_sources} / {k.sources}</td>
                <td>{k.stale_after_days} days</td><td className="text-xs">{dateTime(k.created_at)}</td></tr>))}
            </tbody>
          </Table>
        )}
        {owner && (
          <form className="mt-3 flex gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); addKb.mutate(); }}>
            <Input aria-label="New knowledge base name" placeholder="New knowledge base…" value={kbName} onChange={(e) => setKbName(e.target.value)} />
            <Button type="submit" disabled={!kbName.trim()} busy={addKb.isPending}>Add</Button>
          </form>
        )}
        <ErrorBox error={addKb.error} />
      </Card>
      <Card title="Members">
        {members.isLoading ? <Loading /> : (
          <Table>
            <thead><tr><th>Email</th><th>Role</th><th /></tr></thead>
            <tbody>{members.data?.map((m) => (
              <tr key={m.user_id}><td>{m.email}</td><td><Badge tone={ROLE_TONE[m.role]}>{m.role}</Badge></td>
                <td>{owner && <Button variant="secondary" onClick={() => removeMember.mutate(m.user_id)}>Remove</Button>}</td></tr>))}
            </tbody>
          </Table>
        )}
        {owner && (
          <form className="mt-3 grid gap-2 sm:grid-cols-[1fr_140px_auto]" onSubmit={(e: FormEvent) => { e.preventDefault(); addMember.mutate(); }}>
            <Input type="email" aria-label="Member e-mail" placeholder="colleague@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            <Select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as WorkspaceRole)}>
              <option value="reader">Reader</option><option value="editor">Editor</option><option value="owner">Owner</option>
            </Select>
            <Button type="submit" disabled={!email} busy={addMember.isPending}>Add or update</Button>
          </form>
        )}
        <ErrorBox error={addMember.error ?? removeMember.error} />
        <p className="mt-2 text-xs text-slate-500">Readers search and ask · editors also add and remove sources · owners also manage members and knowledge bases.</p>
      </Card>
      <Card title="Saved answers">
        {!saved.data?.length ? <Empty>Answers pinned from the Ask page appear here for the whole workspace.</Empty> : (
          <ul className="space-y-3">{saved.data.map((s) => (
            <li key={s.id} className="rounded-lg border border-slate-200 p-3 text-sm">
              <p className="font-medium">{s.question}</p><p className="mt-1 text-slate-700">{s.answer}</p>
              {s.note && <p className="mt-1 text-xs text-slate-500">Note: {s.note}</p>}
              <p className="mt-1 text-xs text-slate-400">{s.saved_by} · {dateTime(s.created_at)}</p>
            </li>))}
          </ul>
        )}
      </Card>
    </div>
  );
}
