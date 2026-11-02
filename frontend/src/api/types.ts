export type Role = "admin" | "member" | "viewer";
export type Access = "owner" | "edit" | "read";
export type VersionStatus = "queued" | "processing" | "ready" | "failed";
export type AnswerStatus = "answered" | "not_found" | "refused" | "error";

export interface User { id: string; email: string; full_name: string; role: Role; is_active: boolean; created_at: string }

export interface Stage { status: "running" | "done" | "failed"; ms?: number; error?: string; [k: string]: unknown }

export interface Version {
  id: string; version_no: number; filename: string; mime: string; size_bytes: number; page_count: number | null;
  language: string | null; status: VersionStatus; stage: string | null; stages: Record<string, Stage>;
  error: string | null; created_at: string; processed_at: string | null;
}

export type WorkspaceRole = "owner" | "editor" | "reader";

export interface Freshness { age_days: number; basis: "published" | "fetched" | "added"; reference_date: string;
  stale: boolean; stale_after_days: number; source: "rule-based" }

export interface Reliability { level: "high" | "medium" | "low"; score: number; reasons: string[]; source: "rule-based" }

export interface DocumentSummary {
  id: string; title: string; kb_id: string; kb_name: string; owner_id: string | null; owner_email: string | null;
  access: Access; source_kind: "upload" | "url" | "zip"; source_url: string | null; published_at: string | null;
  fetched_at: string | null; freshness: Freshness; reliability: Reliability;
  doc_type: string | null; doc_type_source: string | null;
  created_at: string; updated_at: string; current_version: Version | null;
}

export interface Workspace { id: string; name: string; description: string; role: WorkspaceRole; members: number;
  knowledge_bases: number; created_at: string }

export interface Member { user_id: string; email: string; full_name: string; role: WorkspaceRole }

export interface KnowledgeBase { id: string; workspace_id: string; workspace_name: string; name: string;
  description: string; stale_after_days: number; sources: number; ready_sources: number; role: WorkspaceRole;
  created_at: string }

export interface SavedAnswer { id: string; question: string | null; answer: string; note: string;
  saved_by: string | null; created_at: string; message_id: string }

export interface DocumentDetail extends DocumentSummary {
  versions: Version[];
  summary: { text: string; source: string } | null;
  fields: { kind: string; data: Record<string, unknown>; source: string;
            validation: { valid: boolean; missing_fields: string[]; issues: string[] } } | null;
  entities: { type: string; value: string; normalized: string | null; page_no: number }[];
}

export interface Page<T> { items: T[]; total: number }

export interface PageContent { page_no: number; page_count: number; text: string; ocr_used: boolean;
  ocr_confidence: number | null; tables: string[][][] }

export interface SearchHit { chunk_id: number; document_id: string; document_title: string; kb_id: string | null;
  doc_type: string | null; page_no: number; char_start: number; char_end: number; text: string; score: number;
  vector_similarity: number | null; keyword_rank: number | null; rerank_score: number | null }

export interface SearchOut { hits: SearchHit[]; mode: string; reranked: boolean; latency_ms: number }

export interface Synthesis {
  documents: { document_id: string; title: string; citations: number }[];
  agreement: "none" | "single_source" | "consistent" | "conflict";
  conflicts: { term: string; values: { document_id: string; title: string; values: string[] }[] }[];
  source: "rule-based";
}

export interface Citation { ordinal: number; chunk_id?: number; document_id: string; document_title: string;
  page_no: number; cited_text: string; char_start?: number; char_end?: number }

export interface AskResult {
  message_id: string; status: AnswerStatus; answer: string; provider: string;
  sentences: { text: string; citations: number[]; supported: boolean }[];
  citations: Citation[]; standalone_question: string; retrieved: number; latency_ms: number; notes: string[];
  synthesis?: Synthesis;
}

export interface HistoryMessage { id: string; role: "user" | "assistant"; content: string; status: AnswerStatus | null;
  provider: string | null; created_at: string; citations: Citation[] }

export interface Conversation { id: string; title: string; scope: { document_ids?: string[]; kb_ids?: string[] };
  created_at: string }

export interface GraphData { nodes: { id: string; label: string; sources: number; examples: string[] }[];
  edges: { source: string; target: string; weight: number }[]; concepts_total: number; shown: number }

export interface TopicsData { topics: { topic: number; label: string; keywords: string[];
  sources: { id: string; title: string }[] }[]; k?: number; note?: string }

export interface KbHealth { sources: number; stale: { id: string; title: string; age_days: number; basis: string }[];
  reliability: Record<"high" | "medium" | "low", number>;
  low_reliability: { id: string; title: string; reasons: string[] }[] }

export interface Duplicates { exact: { id: string; title: string }[][];
  near: { a: { id: string; title: string }; b: { id: string; title: string }; similarity: number }[]; threshold: number }

export interface SystemInfo { llm_mode: "claude" | "extractive"; llm_model: string | null; embedding_model: string;
  documents_leave_server: boolean }
