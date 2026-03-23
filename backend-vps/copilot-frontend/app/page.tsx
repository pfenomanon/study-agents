"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

const API_BASE = "/api/copilot";
const CAPTURE_API = "/api/copilot-capture";
const CAPTURE_IMAGE_API = "/api/copilot-capture-image";
const FILE_API = "/api/research-files";
const UPLOAD_API = "/api/uploads";
const PREP_MARKDOWN_API = "/api/prepare-markdown";
const DOC_API = "/api/documents";
const PROFILE_API = "/api/profiles";
const DOMAIN_WIZARD_API = "/api/domain-wizard";
const DOMAIN_WIZARD_HISTORY_API = "/api/domain-wizard-history";
const RAG_CAG_PIPELINE_API = "/api/rag-cag-pipeline";
const GRAPH_PREVIEW_API = "/api/graph-preview";
const RESEARCH_ROOT = "/app/data/output/research";
const PANEL_WIDTH_STORAGE_KEY = "copilot_ui_left_panel_width";

type Message = {
  role: "user" | "assistant" | "system";
  text: string;
};

type FileEntry = {
  path: string;
};

type ProfileEntry = {
  profile_id: string;
  name: string;
  summary: string;
  status: string;
  doc_count: number;
  artifact_count: number;
  last_activity?: string | null;
};

type AnswerSections = {
  answer: string;
  rationale: string;
  citations: string;
};

type DomainWizardRun = {
  ok?: boolean;
  profile_id?: string;
  prompt_profile_name?: string;
  command?: string;
  exit_code?: number;
  stderr?: string;
  stdout?: string;
};

type DomainWizardHistoryItem = {
  artifact_id?: string;
  created_at?: string;
  run_id?: string;
  path?: string;
  exit_code?: number | null;
  rolled_back?: boolean;
  generated_targets?: Record<string, string>;
};

type ProfileArtifact = {
  artifact_id?: string;
  agent?: string;
  artifact_type?: string;
  path?: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
};

type ProfilePurgeTableResult = {
  profile_scope_count?: number;
  group_scope_count?: number;
  deleted_profile_scope?: number;
  deleted_group_scope?: number;
  remaining_profile_scope?: number;
  remaining_group_scope?: number;
};

type ProfilePurgeResponse = {
  dry_run?: boolean;
  profile_id?: string;
  tables?: Record<string, ProfilePurgeTableResult>;
  summary?: {
    candidate_rows_by_profile_scope?: number;
    candidate_rows_by_group_scope?: number;
    deleted_rows_by_profile_scope?: number;
    deleted_rows_by_group_scope?: number;
    remaining_rows_by_profile_scope?: number;
    remaining_rows_by_group_scope?: number;
  };
  expected_confirm_text?: string;
};

type ProfileDeleteResponse = {
  db_report?: {
    profile_id?: string;
    dry_run?: boolean;
    db_summary?: {
      candidate_rows?: number;
      deleted_rows?: number;
      remaining_rows?: number;
    };
    purge_report?: {
      summary?: {
        candidate_rows_by_profile_scope?: number;
        candidate_rows_by_group_scope?: number;
        deleted_rows_by_profile_scope?: number;
        deleted_rows_by_group_scope?: number;
      };
    };
  };
  local_report?: {
    summary?: {
      candidate_paths?: number;
      deleted_paths?: number;
      failed_paths?: number;
    };
  };
  active_profile_cleared?: boolean;
  expected_confirm_text?: string;
};

type GraphPreviewNode = {
  id: string;
  title: string;
  type: string;
};

type GraphPreviewEdge = {
  src: string;
  dst: string;
  rel: string;
};

function isNoiseFragment(input: string): boolean {
  const line = (input || "").trim();
  if (!line) return true;
  if (/^[\-–—]?\s*\d{1,2}:\d{2}\s*(AM|PM)?$/i.test(line)) return true;
  if (/^[\-–—]?\s*\d{1,2}\s*(AM|PM)$/i.test(line)) return true;
  if (/\bsubmit\s*answer\b/i.test(line)) return true;
  if (/\b(?:tempstorise|temporise)\b/i.test(line)) return true;
  if (/\b\d+\s*x\s*\d+\b/i.test(line)) return true;
  if (/\b\d+(\.\d+)?\s*(KB|MB|GB)\b/i.test(line)) return true;
  if (/\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/.test(line)) return true;
  if (/^[\-–—]?\s*\d{1,3}\s*°\s*[FC](\s+\w+)?$/i.test(line)) return true;
  return false;
}

function normalizeOptionLine(raw: string): string {
  let line = (raw || "").trim();
  if (!line) return "";
  line = line
    .replace(/^\s*[-•]?\s*\[\s*[xX ]?\s*\]\s*/, "")
    .replace(/^\s*[-•]\s*/, "")
    .replace(/^(?:[A-Da-d]|\d{1,2})[\)\.\-:]\s*/, "")
    .replace(/\bq\s*search\b/gi, "")
    .replace(/\s*(?:tomorrow\s+)?[qgo0]uestion\s+\d+\s*(?:of|\/)\s*\d+\b.*$/i, "")
    .replace(/\bsubmit\s*answer\b.*$/i, "")
    .replace(/\b(?:tempstorise|temporise)\b.*$/i, "")
    .replace(
      /\s+(?:submit\s*answer|tempstorise|temporise|next\s+\w+|\d{1,2}:\d{2}\s*(?:am|pm)|\d{1,2}\s*(?:am|pm)|\d{1,3}\s*°\s*[fc]|q\s*search).*$/i,
      "",
    )
    .replace(/\s+/g, " ")
    .trim();
  if (/^of\s+\d{1,3}(?:\s+of\s+\d{1,3})?$/i.test(line)) return "";
  if (!line || isNoiseFragment(line)) return "";
  return line;
}

function cleanQuestionText(input: string): string {
  if (!input) return "";

  const noiseMarkers = [
    "expert insurance adjuster console",
    "submit structured scenarios",
    "workflow-aligned questions",
    "grounded answers",
  ];

  const progressPattern = /\s*(?:tomorrow\s+)?[qgo0]uestion\s+\d+\s*(?:of|\/)\s*\d+\b.*$/i;
  const qsearchPattern = /\bqsearch\b.*$/i;

  const lines = input
    .split(/\r?\n/)
    .map((raw) => raw.trim())
    .filter(Boolean);

  const cleaned = lines.map((line) =>
    line
      .replace(/^\s*#+\s*/, "")
      .replace(/^\s*question\s*:\s*/i, "")
      .replace(/^\s*question\s+\d+\s*(?:[\)\.\-:]?\s*)/i, "")
      .replace(/^\s*\d{2,3}\s*[\)\.\-:]\s*/, "")
      .replace(/\bq\s*search\b/gi, "")
      .replace(/\bsubmit\s*answer\b/gi, "")
      .replace(/^\s*\d{1,3}\s*°\s*[FC]\b/i, "")
      .replace(/^\s*(?:sunny|cloudy|rainy|stormy|windy|snowy|clear|overcast)\b/i, "")
      .replace(progressPattern, "")
      .replace(qsearchPattern, "")
      .replace(/\s+/g, " ")
      .trim(),
  );

  let inOptions = false;
  const questionLines: string[] = [];
  const options: string[] = [];
  for (const line of cleaned) {
    if (!line) continue;
    if (/^(options?|choices?)\s*:?$/i.test(line)) {
      inOptions = true;
      continue;
    }
    const lower = line.toLowerCase();
    if (noiseMarkers.some((marker) => lower.includes(marker)) && !line.includes("?")) {
      continue;
    }
    if (inOptions || /^\s*[-•]?\s*(?:\[\s*[xX ]?\s*\]\s*)?(?:[A-Da-d]|\d{1,2})[\)\.\-:]/.test(line)) {
      const normalized = normalizeOptionLine(line);
      if (normalized) options.push(normalized);
      continue;
    }
    if (isNoiseFragment(line)) continue;
    questionLines.push(line);
  }

  const stem = questionLines.join(" ").replace(/\s+/g, " ").trim();
  const cleanedStem = stem
    .replace(/\bsubmit\s*answer\b.*$/i, "")
    .replace(/\b(?:tempstorise|temporise)\b.*$/i, "")
    .replace(/\s+/g, " ")
    .trim();
  if (cleanedStem && options.length) {
    const deduped: string[] = [];
    const seen = new Set<string>();
    for (const opt of options) {
      const key = opt.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push(opt);
    }
    return `${cleanedStem}\nOptions:\n${deduped.map((opt) => `- ${opt}`).join("\n")}`;
  }

  return cleanedStem || cleaned.filter((line) => !isNoiseFragment(line)).join("\n").trim();
}

function extractAnswerSections(rawText: string): AnswerSections {
  const sections: AnswerSections = { answer: "", rationale: "", citations: "" };
  let current: keyof AnswerSections | null = null;
  const valueAfterFirstColon = (line: string): string => {
    const idx = line.indexOf(":");
    return idx >= 0 ? line.slice(idx + 1).trim() : "";
  };

  for (const rawLine of (rawText || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const lower = line.toLowerCase();

    if (lower.startsWith("answer:")) {
      current = "answer";
      sections.answer = valueAfterFirstColon(line);
      continue;
    }
    if (lower.startsWith("rationale:")) {
      current = "rationale";
      sections.rationale = valueAfterFirstColon(line);
      continue;
    }
    if (lower.startsWith("citations:")) {
      current = "citations";
      sections.citations = valueAfterFirstColon(line);
      continue;
    }
    if (current) {
      sections[current] = `${sections[current]} ${line}`.trim();
    }
  }

  if (!sections.answer) sections.answer = rawText.trim() || "n/a";
  if (!sections.rationale) sections.rationale = "N/A";
  if (!sections.citations) sections.citations = "NONE";
  return sections;
}

function truncateLabel(value: string, maxLen = 24): string {
  const text = String(value || "");
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen - 1)}…`;
}

function normalizeGraphCandidate(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (/^(rag|research|cag|vision)\s*:/i.test(text)) return "";
  if (/^answer\s*:/i.test(text)) return text.replace(/^answer\s*:/i, "").trim();
  return text;
}

function getLastQuestionLikeMessage(messages: Message[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role !== "user") continue;
    const normalized = normalizeGraphCandidate(msg.text);
    if (normalized) return normalized;
  }
  return "";
}

function buildVisionCaptureGraphQuery(captureResult: any): string {
  const question = cleanQuestionText(String(captureResult?.question || "")).trim();
  const sections = extractAnswerSections(String(captureResult?.answer || captureResult?.error || ""));
  const answer = String(sections?.answer || "").trim();

  if (question && answer && !/^n\/a$/i.test(answer)) {
    return `${question}\nAnswer: ${answer}`;
  }
  if (question) return question;
  if (answer && !/^n\/a$/i.test(answer)) return answer;
  return "";
}

function getLastGraphQuery(messages: Message[], captureResult: any): string {
  const vision = buildVisionCaptureGraphQuery(captureResult).trim();
  if (vision) return vision;
  return getLastQuestionLikeMessage(messages).trim();
}

function formatPurgeStatus(data: ProfilePurgeResponse): string {
  const summary = data.summary || {};
  const mode = data.dry_run ? "Preview" : "Purge complete";
  const profile = String(data.profile_id || "").trim() || "unknown-profile";
  const scope =
    `profile=${Number(summary.candidate_rows_by_profile_scope || 0)} ` +
    `group=${Number(summary.candidate_rows_by_group_scope || 0)}`;
  if (data.dry_run) return `${mode} for ${profile}. Candidate rows: ${scope}.`;
  const deleted =
    `profile=${Number(summary.deleted_rows_by_profile_scope || 0)} ` +
    `group=${Number(summary.deleted_rows_by_group_scope || 0)}`;
  const remaining =
    `profile=${Number(summary.remaining_rows_by_profile_scope || 0)} ` +
    `group=${Number(summary.remaining_rows_by_group_scope || 0)}`;
  return `${mode} for ${profile}. Deleted rows: ${deleted}. Remaining rows: ${remaining}.`;
}

function formatDeleteStatus(data: ProfileDeleteResponse): string {
  const db = data.db_report || {};
  const profile = String(db.profile_id || "").trim() || "unknown-profile";
  const dryRun = Boolean(db.dry_run);
  const dbSummary = db.db_summary || {};
  const purgeSummary = db.purge_report?.summary || {};
  const localSummary = data.local_report?.summary || {};
  if (dryRun) {
    return (
      `Delete preview for ${profile}. ` +
      `DB candidate rows=${Number(dbSummary.candidate_rows || 0)}, ` +
      `Supabase candidate profile/group=${Number(purgeSummary.candidate_rows_by_profile_scope || 0)}/` +
      `${Number(purgeSummary.candidate_rows_by_group_scope || 0)}, ` +
      `local candidate paths=${Number(localSummary.candidate_paths || 0)}.`
    );
  }
  return (
    `Profile ${profile} deleted. ` +
    `DB deleted rows=${Number(dbSummary.deleted_rows || 0)}, ` +
    `Supabase deleted profile/group=${Number(purgeSummary.deleted_rows_by_profile_scope || 0)}/` +
    `${Number(purgeSummary.deleted_rows_by_group_scope || 0)}, ` +
    `local deleted paths=${Number(localSummary.deleted_paths || 0)} ` +
    `(failures=${Number(localSummary.failed_paths || 0)}).`
  );
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "system",
      text:
        "Connected to Study Agents backend. Use commands like “Answer: ...”, “RAG: ...”, or “Research: ...”.",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileEntry[]>([]);
  const [profileStatus, setProfileStatus] = useState<string | null>(null);
  const [activeProfile, setActiveProfile] = useState<string>("");

  const [files, setFiles] = useState<FileEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [fileStatus, setFileStatus] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [uploads, setUploads] = useState<{ name: string; path: string }[]>([]);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [preppingUploadPath, setPreppingUploadPath] = useState<string | null>(null);
  const [docs, setDocs] = useState<{ label: string; path: string }[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [docStatus, setDocStatus] = useState<string | null>(null);
  const [deletingDocs, setDeletingDocs] = useState<boolean>(false);
  const [isNarrowViewport, setIsNarrowViewport] = useState<boolean>(false);
  const [cagStatus, setCagStatus] = useState<string | null>(null);
  const [panelWidth, setPanelWidth] = useState<number>(360);
  const [showGraphView, setShowGraphView] = useState<boolean>(false);
  const [graphMinimized, setGraphMinimized] = useState<boolean>(false);
  const [graphLoading, setGraphLoading] = useState<boolean>(false);
  const [graphStatus, setGraphStatus] = useState<string | null>(null);
  const [graphQuery, setGraphQuery] = useState<string>("");
  const [graphUsedQuery, setGraphUsedQuery] = useState<string>("");
  const [graphNodes, setGraphNodes] = useState<GraphPreviewNode[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphPreviewEdge[]>([]);
  const [captureStatus, setCaptureStatus] = useState<string | null>(null);
  const [captureResult, setCaptureResult] = useState<any>(null);
  const [captureMode, setCaptureMode] = useState<"local" | "remote" | "remote_image">("local");
  const [captureSource, setCaptureSource] = useState<"server" | "browser">("browser");
  const [monitorIndex, setMonitorIndex] = useState<number>(1);
  const [captureDpi, setCaptureDpi] = useState<string>("96");
  const [topIn, setTopIn] = useState<string>("");
  const [leftIn, setLeftIn] = useState<string>("");
  const [rightIn, setRightIn] = useState<string>("");
  const [bottomIn, setBottomIn] = useState<string>("");
  const [remoteCagUrl, setRemoteCagUrl] = useState<string>("");
  const [remoteImageUrl, setRemoteImageUrl] = useState<string>("");
  const [reasonPlatform, setReasonPlatform] = useState<string>("");
  const [reasonModel, setReasonModel] = useState<string>("");
  const [ollamaTarget, setOllamaTarget] = useState<string>("");
  const [wizardProfileName, setWizardProfileName] = useState<string>("");
  const [wizardDomainSeed, setWizardDomainSeed] = useState<string>("");
  const [wizardPlatform, setWizardPlatform] = useState<string>("");
  const [wizardModel, setWizardModel] = useState<string>("");
  const [wizardOllamaTarget, setWizardOllamaTarget] = useState<string>("");
  const [wizardNoFallback, setWizardNoFallback] = useState<boolean>(false);
  const [wizardStatus, setWizardStatus] = useState<string | null>(null);
  const [wizardRunning, setWizardRunning] = useState<boolean>(false);
  const [wizardResult, setWizardResult] = useState<DomainWizardRun | null>(null);
  const [wizardHistory, setWizardHistory] = useState<DomainWizardHistoryItem[]>([]);
  const [wizardHistoryStatus, setWizardHistoryStatus] = useState<string | null>(null);
  const [profileArtifacts, setProfileArtifacts] = useState<ProfileArtifact[]>([]);
  const [profileHistoryStatus, setProfileHistoryStatus] = useState<string | null>(null);
  const [profilePurgeIncludeArtifacts, setProfilePurgeIncludeArtifacts] = useState<boolean>(false);

  const captureQuestion = cleanQuestionText(String(captureResult?.question || ""));
  const captureSections = extractAnswerSections(
    String(captureResult?.answer || captureResult?.error || ""),
  );
  const primarySelectedDoc = selectedDocs[0] || null;

  const examplePrompts = useMemo(
    () => [
      "Answer: What are TWIA eligibility requirements?",
      "RAG: Build bundle for /app/data/pdf/TWIA-Commercial-Policy-HB-3208.pdf",
      "Research: https://www.tdi.texas.gov/pubs/consumer/cb025.html depth 2 pages 10 query texas insurance",
    ],
    [],
  );

  const graphLayout = useMemo(() => {
    const width = 980;
    const height = 560;
    const count = graphNodes.length;
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = Math.max(120, Math.min(width, height) * 0.35);
    const points = new Map<string, { x: number; y: number }>();
    if (count === 0) {
      return { width, height, points };
    }
    graphNodes.forEach((node, idx) => {
      const angle = (Math.PI * 2 * idx) / count;
      points.set(node.id, {
        x: centerX + radius * Math.cos(angle),
        y: centerY + radius * Math.sin(angle),
      });
    });
    return { width, height, points };
  }, [graphNodes]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    setError(null);
    setLoading(true);
    setMessages((prev) => [...prev, { role: "user", text: trimmed }]);

    try {
      const res = await fetch(API_BASE, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed, profile_id: activeProfile || undefined }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error || `Request failed (${res.status})`);
      }

      const data = await res.json();
      const reply = typeof data?.reply === "string" ? data.reply : JSON.stringify(data, null, 2);
      setMessages((prev) => [...prev, { role: "assistant", text: reply }]);
    } catch (err: any) {
      setError(err?.message || "Unexpected error");
      setMessages((prev) => [...prev, { role: "assistant", text: err?.message || "Error" }]);
    } finally {
      setLoading(false);
      setInput("");
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  const loadProfiles = async () => {
    try {
      const res = await fetch(PROFILE_API);
      if (!res.ok) throw new Error(`Profile list failed (${res.status})`);
      const data = await res.json();
      const list = (data?.profiles || []) as ProfileEntry[];
      setProfiles(list);
      const active = String(data?.active_profile_id || "").trim();
      if (active) {
        setActiveProfile(active);
        if (!wizardProfileName) setWizardProfileName(active);
      } else if (!activeProfile && list.length > 0) {
        setActiveProfile(list[0].profile_id);
        if (!wizardProfileName) setWizardProfileName(list[0].profile_id);
      }
      setProfileStatus(null);
    } catch (err: any) {
      setProfileStatus(err?.message || "Failed to load profiles");
    }
  };

  const useProfile = async (profileId: string) => {
    setProfileStatus("Switching profile...");
    try {
      const res = await fetch(PROFILE_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "use", profile_id: profileId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `Use profile failed (${res.status})`);
      setActiveProfile(profileId);
      setProfileStatus(`Active profile: ${profileId}`);
      await loadFiles();
      await loadUploads();
    } catch (err: any) {
      setProfileStatus(err?.message || "Failed to switch profile");
    }
  };

  const runProfilePurge = async (dryRun: boolean) => {
    const profileId = String(activeProfile || "").trim();
    if (!profileId) {
      setProfileStatus("Select a profile first.");
      return;
    }

    let confirmText: string | undefined;
    if (!dryRun) {
      const expected = `PURGE ${profileId}`;
      const typed = window.prompt(
        `Type exactly '${expected}' to permanently delete Supabase knowledge rows for this profile.`,
      );
      if (typed === null) {
        setProfileStatus("Purge cancelled.");
        return;
      }
      if (typed.trim() !== expected) {
        setProfileStatus("Confirmation mismatch. No data was deleted.");
        return;
      }
      confirmText = typed.trim();
    }

    setProfileStatus(dryRun ? "Running purge preview..." : "Purging profile knowledge...");
    try {
      const payload: Record<string, unknown> = {
        action: "purge",
        profile_id: profileId,
        dry_run: dryRun,
        include_artifacts: profilePurgeIncludeArtifacts,
      };
      if (confirmText) payload.confirm_text = confirmText;
      const res = await fetch(PROFILE_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await res.json().catch(() => ({}))) as ProfilePurgeResponse & {
        detail?: string;
        error?: string;
      };
      if (!res.ok) throw new Error(data?.detail || data?.error || `Profile purge failed (${res.status})`);
      setProfileStatus(formatPurgeStatus(data));
      if (!dryRun) {
        setProfileArtifacts([]);
        setGraphNodes([]);
        setGraphEdges([]);
        setGraphUsedQuery("");
        await loadProfileHistory(profileId);
        await loadProfiles();
      }
    } catch (err: any) {
      setProfileStatus(err?.message || "Profile purge failed.");
    }
  };

  const runProfileDelete = async (dryRun: boolean) => {
    const profileId = String(activeProfile || "").trim();
    if (!profileId) {
      setProfileStatus("Select a profile first.");
      return;
    }

    let confirmText: string | undefined;
    if (!dryRun) {
      const expected = `DELETE PROFILE ${profileId}`;
      const typed = window.prompt(
        `Type exactly '${expected}' to permanently delete this profile, its Supabase data, and local profile artifacts.`,
      );
      if (typed === null) {
        setProfileStatus("Profile delete cancelled.");
        return;
      }
      if (typed.trim() !== expected) {
        setProfileStatus("Confirmation mismatch. Profile was not deleted.");
        return;
      }
      confirmText = typed.trim();
    }

    setProfileStatus(dryRun ? "Running profile delete preview..." : "Deleting profile...");
    try {
      const payload: Record<string, unknown> = {
        action: "delete",
        profile_id: profileId,
        dry_run: dryRun,
      };
      if (confirmText) payload.confirm_text = confirmText;

      const res = await fetch(PROFILE_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await res.json().catch(() => ({}))) as ProfileDeleteResponse & {
        detail?: string;
        error?: string;
      };
      if (!res.ok) throw new Error(data?.detail || data?.error || `Profile delete failed (${res.status})`);

      setProfileStatus(formatDeleteStatus(data));
      if (!dryRun) {
        setProfileArtifacts([]);
        setGraphNodes([]);
        setGraphEdges([]);
        setGraphUsedQuery("");
        setFiles([]);
        setUploads([]);
        setDocs([]);
        setSelectedDocs([]);
        setSelectedFile(null);
        setFileContent("");
        await loadProfiles();
      }
    } catch (err: any) {
      setProfileStatus(err?.message || "Profile delete failed.");
    }
  };

  const runDomainWizard = async () => {
    const profileName = wizardProfileName.trim();
    if (!profileName) {
      setWizardStatus("Profile name is required.");
      return;
    }
    setWizardRunning(true);
    setWizardStatus("Running domain wizard...");
    setWizardResult(null);
    try {
      const payload = {
        profile_name: profileName,
        domain_seed: wizardDomainSeed.trim() || undefined,
        platform: wizardPlatform.trim() || undefined,
        ai_model: wizardModel.trim() || undefined,
        ollama_target: wizardOllamaTarget.trim() || undefined,
        no_ai_fallback: wizardNoFallback,
      };
      const res = await fetch(DOMAIN_WIZARD_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data?.detail;
        const msg =
          typeof detail === "string"
            ? detail
            : detail?.stderr || detail?.error || data?.error || `Domain wizard failed (${res.status})`;
        throw new Error(msg);
      }
      setWizardResult(data as DomainWizardRun);
      const createdProfile = String(data?.profile_id || "").trim();
      if (createdProfile) {
        setActiveProfile(createdProfile);
        setWizardProfileName(createdProfile);
        await useProfile(createdProfile);
        await loadDomainWizardHistory(createdProfile);
      } else {
        await loadProfiles();
        await loadDomainWizardHistory(profileName);
      }
      setWizardStatus("Domain wizard completed.");
    } catch (err: any) {
      setWizardStatus(err?.message || "Domain wizard failed.");
    } finally {
      setWizardRunning(false);
    }
  };

  const loadDomainWizardHistory = async (profileOverride?: string) => {
    const profileId = String(profileOverride || activeProfile || wizardProfileName || "").trim();
    if (!profileId) {
      setWizardHistory([]);
      setWizardHistoryStatus("Select a profile to view history.");
      return;
    }
    try {
      setWizardHistoryStatus("Loading history...");
      const params = new URLSearchParams({ profile_id: profileId, limit: "10" });
      const res = await fetch(`${DOMAIN_WIZARD_HISTORY_API}?${params.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || `History failed (${res.status})`);
      setWizardHistory(((data?.history || []) as DomainWizardHistoryItem[]).slice(0, 10));
      setWizardHistoryStatus(null);
    } catch (err: any) {
      setWizardHistory([]);
      setWizardHistoryStatus(err?.message || "Failed to load history.");
    }
  };

  const loadProfileHistory = async (profileOverride?: string) => {
    const profileId = String(profileOverride || activeProfile || "").trim();
    if (!profileId) {
      setProfileArtifacts([]);
      setProfileHistoryStatus("Select a profile to view history.");
      return;
    }
    try {
      setProfileHistoryStatus("Loading profile history...");
      const res = await fetch(`${PROFILE_API}?profile_id=${encodeURIComponent(profileId)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || `Profile history failed (${res.status})`);
      setProfileArtifacts(((data?.recent_artifacts || []) as ProfileArtifact[]).slice(0, 10));
      setProfileHistoryStatus(null);
    } catch (err: any) {
      setProfileArtifacts([]);
      setProfileHistoryStatus(err?.message || "Failed to load profile history.");
    }
  };

  const loadGraphPreview = async (profileOverride?: string, queryOverride?: string) => {
    const profileId = String(profileOverride || activeProfile || "").trim();
    const explicit = String(queryOverride ?? graphQuery).trim();
    const fallbackLast = getLastGraphQuery(messages, captureResult).trim();
    const queryText = explicit || fallbackLast;
    if (!profileId) {
      setGraphNodes([]);
      setGraphEdges([]);
      setGraphStatus("Select a profile to load a graph.");
      return;
    }
    if (!queryText) {
      setGraphNodes([]);
      setGraphEdges([]);
      setGraphUsedQuery("");
      setGraphStatus("No QA found yet. Enter a query, ask in chat, or run Vision Capture first.");
      return;
    }
    try {
      setGraphLoading(true);
      setGraphStatus("Loading graph...");
      const params = new URLSearchParams({
        profile_id: profileId,
        query: queryText,
      });
      const res = await fetch(`${GRAPH_PREVIEW_API}?${params.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || `Graph load failed (${res.status})`);
      }
      setGraphNodes((data?.nodes || []) as GraphPreviewNode[]);
      setGraphEdges((data?.edges || []) as GraphPreviewEdge[]);
      setGraphUsedQuery(String(data?.query || queryText));
      const countNodes = Number(data?.counts?.nodes || 0);
      const countEdges = Number(data?.counts?.edges || 0);
      const matched = Number(data?.counts?.matched_nodes || 0);
      const totalNodes = Number(data?.counts?.total_nodes || countNodes);
      setGraphStatus(`${countNodes} nodes, ${countEdges} edges (matched ${matched} / ${totalNodes})`);
    } catch (err: any) {
      setGraphNodes([]);
      setGraphEdges([]);
      setGraphUsedQuery("");
      setGraphStatus(err?.message || "Failed to load graph");
    } finally {
      setGraphLoading(false);
    }
  };

  const loadFiles = async () => {
    try {
      const qp = activeProfile ? `?profile=${encodeURIComponent(activeProfile)}` : "";
      const res = await fetch(`${FILE_API}${qp}`);
      if (!res.ok) throw new Error(`List failed (${res.status})`);
      const data = await res.json();
      setFiles((data?.files || []).map((p: string) => ({ path: p })));
    } catch (err: any) {
      setFileStatus(err?.message || "Failed to list files");
    }
  };

  const loadFileContent = async (path: string) => {
    setSelectedFile(path);
    setFileStatus("Loading...");
    try {
      const params = new URLSearchParams({ path });
      if (activeProfile) params.set("profile", activeProfile);
      const res = await fetch(`${FILE_API}?${params.toString()}`);
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      const data = await res.json();
      setFileContent(data?.content || "");
      setFileStatus(null);
    } catch (err: any) {
      setFileStatus(err?.message || "Failed to load file");
    }
  };

  const saveFile = async () => {
    if (!selectedFile) return;
    setFileStatus("Saving...");
    try {
      const res = await fetch(FILE_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selectedFile, content: fileContent, profile: activeProfile || undefined }),
      });
      if (!res.ok) throw new Error(`Save failed (${res.status})`);
      setFileStatus("Saved");
      await loadFiles();
    } catch (err: any) {
      setFileStatus(err?.message || "Failed to save");
    }
  };

  const sendSelectedToRag = () => {
    if (!selectedFile) return;
    const profilePrefix = activeProfile ? `/profiles/${activeProfile}` : "";
    const cmd = `RAG: Build bundle for ${RESEARCH_ROOT}${profilePrefix}/${selectedFile}`;
    void send(cmd);
  };

  const runRagCagPipeline = async (docPath: string) => {
    const p = String(docPath || "").trim();
    if (!p) return;
    setCagStatus("Running RAG + CAG...");
    try {
      const res = await fetch(RAG_CAG_PIPELINE_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: p, profile_id: activeProfile || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || data?.detail || `Pipeline failed (${res.status})`);
      }
      const groupId = String(data?.cag?.result?.group_id || "").trim();
      const preparedPath = String(data?.prepared_markdown_path || "").trim();
      const sourceDeleted = Boolean(data?.source_deleted);
      const deleteWarning = String(data?.delete_warning || "").trim();
      if (groupId) {
        if (preparedPath && sourceDeleted) {
          setCagStatus(
            `RAG + CAG complete (group: ${groupId}) | prepared markdown: ${preparedPath} | source PDF deleted`,
          );
        } else if (preparedPath) {
          setCagStatus(
            `RAG + CAG complete (group: ${groupId}) | prepared markdown: ${preparedPath}` +
              (deleteWarning ? ` | delete warning: ${deleteWarning}` : ""),
          );
        } else {
          setCagStatus(`RAG + CAG complete (group: ${groupId})`);
        }
      } else if (preparedPath && sourceDeleted) {
        setCagStatus(`RAG + CAG complete | prepared markdown: ${preparedPath} | source PDF deleted`);
      } else if (preparedPath) {
        setCagStatus(
          `RAG + CAG complete | prepared markdown: ${preparedPath}` +
            (deleteWarning ? ` | delete warning: ${deleteWarning}` : ""),
        );
      } else {
        setCagStatus("RAG + CAG complete");
      }
      await loadFiles();
      await loadDocs();
      await loadProfileHistory();
    } catch (err: any) {
      setCagStatus(err?.message || "RAG + CAG error");
    }
  };

  const runSelectedToRagCag = () => {
    if (!selectedFile) return;
    const profilePrefix = activeProfile ? `/profiles/${activeProfile}` : "";
    const p = `${RESEARCH_ROOT}${profilePrefix}/${selectedFile}`;
    void runRagCagPipeline(p);
  };

  const deleteFile = async () => {
    if (!selectedFile) return;
    setDeleting(true);
    setFileStatus("Deleting...");
    try {
      const res = await fetch(FILE_API, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selectedFile, profile: activeProfile || undefined }),
      });
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      setFileStatus("Deleted");
      setSelectedFile(null);
      setFileContent("");
      await loadFiles();
    } catch (err: any) {
      setFileStatus(err?.message || "Failed to delete");
    } finally {
      setDeleting(false);
    }
  };

  useEffect(() => {
    void loadProfiles();
    void loadDocs();
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(PANEL_WIDTH_STORAGE_KEY);
    const parsed = Number.parseInt(raw || "", 10);
    if (Number.isFinite(parsed)) {
      setPanelWidth(Math.max(280, Math.min(760, parsed)));
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(panelWidth));
  }, [panelWidth]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const updateViewport = () => {
      setIsNarrowViewport(window.innerWidth <= 1100);
    };
    updateViewport();
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

  useEffect(() => {
    void loadFiles();
    void loadDocs();
    void loadUploads();
    void loadDomainWizardHistory();
    void loadProfileHistory();
    if (showGraphView) {
      void loadGraphPreview();
    }
  }, [activeProfile]);

  useEffect(() => {
    if (!showGraphView) return;
    if (graphQuery.trim()) return;
    void loadGraphPreview();
  }, [messages, captureResult, graphQuery, showGraphView]);

  const loadUploads = async () => {
    try {
      const qp = activeProfile ? `?profile=${encodeURIComponent(activeProfile)}` : "";
      const res = await fetch(`${UPLOAD_API}${qp}`);
      if (!res.ok) throw new Error(`List failed (${res.status})`);
      const data = await res.json();
      setUploads(data?.files || []);
      setUploadStatus(null);
    } catch (err: any) {
      setUploadStatus(err?.message || "Failed to list uploads");
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    setUploadStatus(`Uploading ${files.length} file(s)...`);
    try {
      const fd = new FormData();
      for (const file of files) {
        fd.append("file", file);
      }
      if (activeProfile) fd.append("profile", activeProfile);
      const res = await fetch(UPLOAD_API, {
        method: "POST",
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `Upload failed (${res.status})`);
      const uploadedCount = Number(data?.count || files.length);
      setUploadStatus(`Uploaded ${uploadedCount} file(s)`);
      await loadUploads();
    } catch (err: any) {
      setUploadStatus(err?.message || "Upload error");
    } finally {
      setUploading(false);
    }
  };

  const deleteUpload = async (path: string) => {
    setUploadStatus("Deleting...");
    try {
      const res = await fetch(UPLOAD_API, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      setUploadStatus("Deleted");
      await loadUploads();
    } catch (err: any) {
      setUploadStatus(err?.message || "Delete error");
    }
  };

  const prepareUploadToMarkdown = async (uploadRelPath: string) => {
    const absPath = `${RESEARCH_ROOT}/${uploadRelPath}`;
      const res = await fetch(PREP_MARKDOWN_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: absPath, profile_id: activeProfile || undefined }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error || `Markdown prep failed (${res.status})`);
    const markdownPath = String(data?.markdown_path || "").trim();
    if (!markdownPath) throw new Error("Markdown prep returned no output path");
    return markdownPath;
  };

  const sendUploadToRag = async (uploadRelPath: string) => {
    setPreppingUploadPath(uploadRelPath);
    setUploadStatus("Preparing markdown...");
    try {
      const markdownPath = await prepareUploadToMarkdown(uploadRelPath);
      setUploadStatus("Sending prepared markdown to RAG...");
      await send(`RAG: Build bundle for ${markdownPath}`);
      setUploadStatus(`RAG started with ${markdownPath}`);
      await loadFiles();
      await loadDocs();
    } catch (err: any) {
      setUploadStatus(err?.message || "Send to RAG failed");
    } finally {
      setPreppingUploadPath(null);
    }
  };

  const sendUploadToRagCag = async (uploadRelPath: string) => {
    setPreppingUploadPath(uploadRelPath);
    setUploadStatus("Preparing markdown...");
    try {
      const markdownPath = await prepareUploadToMarkdown(uploadRelPath);
      setUploadStatus("Running RAG + CAG...");
      await runRagCagPipeline(markdownPath);
      setUploadStatus(`RAG + CAG complete for ${markdownPath}`);
    } catch (err: any) {
      setUploadStatus(err?.message || "RAG + CAG failed");
    } finally {
      setPreppingUploadPath(null);
    }
  };

  const loadDocs = async () => {
    try {
      const qp = activeProfile ? `?profile=${encodeURIComponent(activeProfile)}` : "";
      const res = await fetch(`${DOC_API}${qp}`);
      if (!res.ok) throw new Error(`List failed (${res.status})`);
      const data = await res.json();
      const loadedDocs = (data?.docs || []) as { label: string; path: string }[];
      setDocs(loadedDocs);
      const nextPaths = new Set(loadedDocs.map((d) => d.path));
      setSelectedDocs((prev) => prev.filter((path) => nextPaths.has(path)));
      setDocStatus(null);
    } catch (err: any) {
      setDocStatus(err?.message || "Failed to list documents");
    }
  };

  const sendDocToRag = (docPath?: string) => {
    const p = docPath || primarySelectedDoc;
    if (!p) return;
    const cmd = `RAG: Build bundle for ${p}`;
    void send(cmd);
  };

  const sendDocToCag = async (docPath?: string) => {
    const p = docPath || primarySelectedDoc;
    if (!p) return;
    setCagStatus("Sending to CAG...");
    try {
      const res = await fetch("/api/cag-process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: p, profile_id: activeProfile || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `CAG failed (${res.status})`);
      setCagStatus("CAG complete");
    } catch (err: any) {
      setCagStatus(err?.message || "CAG error");
    }
  };

  const sendDocToRagCag = async (docPath?: string) => {
    const p = docPath || primarySelectedDoc;
    if (!p) return;
    await runRagCagPipeline(p);
  };

  const deleteDocs = async (docPaths?: string[]) => {
    const targets = (docPaths || selectedDocs).map((p) => String(p || "").trim()).filter(Boolean);
    if (targets.length === 0) return;
    setDeletingDocs(true);
    setDocStatus(`Deleting ${targets.length} document(s)...`);
    try {
      const res = await fetch(DOC_API, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: targets }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `Delete failed (${res.status})`);

      const deleted = Array.isArray(data?.deleted) ? data.deleted : [];
      const failed = Array.isArray(data?.failed) ? data.failed : [];
      if (failed.length > 0) {
        setDocStatus(`Deleted ${deleted.length}; ${failed.length} failed`);
      } else {
        setDocStatus(`Deleted ${deleted.length} document(s)`);
      }
      await loadDocs();
      await loadFiles();
      await loadUploads();
    } catch (err: any) {
      setDocStatus(err?.message || "Failed to delete documents");
    } finally {
      setDeletingDocs(false);
    }
  };

  const captureBrowserFrame = async (crop?: {
    top: number;
    left: number;
    right: number;
    bottom: number;
  }): Promise<File> => {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      throw new Error("Browser capture requires a secure context (HTTPS or localhost).");
    }

    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 1 },
      audio: false,
    });

    try {
      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      await video.play();

      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) {
        throw new Error("No frame available from browser screen capture.");
      }

      const cropTop = Math.max(0, Math.min(height - 1, crop?.top ?? 0));
      const cropLeft = Math.max(0, Math.min(width - 1, crop?.left ?? 0));
      const cropRight = Math.max(0, Math.min(width - cropLeft - 1, crop?.right ?? 0));
      const cropBottom = Math.max(0, Math.min(height - cropTop - 1, crop?.bottom ?? 0));
      const sourceWidth = Math.max(1, width - cropLeft - cropRight);
      const sourceHeight = Math.max(1, height - cropTop - cropBottom);

      const maxDim = 1920;
      const scale = Math.min(1, maxDim / Math.max(sourceWidth, sourceHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(sourceWidth * scale));
      canvas.height = Math.max(1, Math.round(sourceHeight * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        throw new Error("Unable to process captured frame.");
      }
      ctx.drawImage(
        video,
        cropLeft,
        cropTop,
        sourceWidth,
        sourceHeight,
        0,
        0,
        canvas.width,
        canvas.height,
      );

      const toBlob = (type: string, quality?: number) =>
        new Promise<Blob | null>((resolve) => canvas.toBlob((b) => resolve(b), type, quality));
      const maxUploadBytes = 9 * 1024 * 1024;

      // Prefer lossless PNG for OCR quality; fallback to JPEG if too large.
      const pngBlob = await toBlob("image/png");
      if (pngBlob && pngBlob.size <= maxUploadBytes) {
        return new File([pngBlob], `browser_capture_${Date.now()}.png`, { type: "image/png" });
      }

      const jpegBlob = (await toBlob("image/jpeg", 0.92)) || (await toBlob("image/jpeg", 0.82));
      if (!jpegBlob) {
        throw new Error("Failed to encode captured image.");
      }
      return new File([jpegBlob], `browser_capture_${Date.now()}.jpg`, { type: "image/jpeg" });
    } finally {
      stream.getTracks().forEach((track) => track.stop());
    }
  };

  const triggerCapture = async () => {
    setCaptureStatus("Capturing...");
    setCaptureResult(null);
    const parseInches = (raw: string): number | undefined => {
      const trimmed = raw.trim();
      if (!trimmed) return undefined;
      const n = Number.parseFloat(trimmed);
      if (!Number.isFinite(n) || n < 0) {
        throw new Error("Inch values must be non-negative numbers.");
      }
      return n;
    };

    try {
      const dpiValue = Number.parseFloat(captureDpi.trim() || "96");
      if (!Number.isFinite(dpiValue) || dpiValue <= 0) {
        setCaptureStatus("DPI must be a positive number.");
        return;
      }
      const toPx = (inches: number | undefined): number | undefined =>
        inches === undefined ? undefined : Math.round(inches * dpiValue);
      const topPx = toPx(parseInches(topIn)) ?? 0;
      const bottomPx = toPx(parseInches(bottomIn)) ?? 0;
      const leftPx = toPx(parseInches(leftIn)) ?? 0;
      const rightPx = toPx(parseInches(rightIn)) ?? 0;
      const platformValue = reasonPlatform.trim() || undefined;
      const modelValue = reasonModel.trim() || undefined;
      const ollamaTargetValue = ollamaTarget.trim() || undefined;

      let res: Response;
      if (captureSource === "browser") {
        setCaptureStatus("Select a screen/tab to capture...");
        const image = await captureBrowserFrame({
          top: topPx,
          bottom: bottomPx,
          left: leftPx,
          right: rightPx,
        });
        const form = new FormData();
        form.append("image", image);
        form.append("mode", captureMode);
        if (remoteCagUrl.trim()) form.append("remote_cag_url", remoteCagUrl.trim());
        if (remoteImageUrl.trim()) form.append("remote_image_url", remoteImageUrl.trim());
        if (platformValue) form.append("platform", platformValue);
        if (modelValue) form.append("model", modelValue);
        if (ollamaTargetValue) form.append("ollama_target", ollamaTargetValue);
        if (activeProfile) form.append("profile_id", activeProfile);

        setCaptureStatus("Uploading captured frame...");
        res = await fetch(CAPTURE_IMAGE_API, {
          method: "POST",
          body: form,
        });
      } else {
        const payload = {
          monitor: monitorIndex,
          mode: captureMode,
          top_offset: topPx,
          bottom_offset: bottomPx,
          left_offset: leftPx,
          right_offset: rightPx,
          remote_cag_url: remoteCagUrl.trim() || undefined,
          remote_image_url: remoteImageUrl.trim() || undefined,
          platform: platformValue,
          model: modelValue,
          ollama_target: ollamaTargetValue,
          profile_id: activeProfile || undefined,
        };
        res = await fetch(CAPTURE_API, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || data?.error || `Capture failed (${res.status})`);
      setCaptureResult(data);
      setCaptureStatus("Done");
    } catch (err: any) {
      setCaptureStatus(err?.message || "Capture error");
    }
  };

  return (
    <div className="shell" style={{ gridTemplateColumns: isNarrowViewport ? "1fr" : `${panelWidth}px 1fr` }}>
      <aside className="panel">
        <div className="card">
          <h3>Study Agents Copilot</h3>
          <p className="muted">Talk to the backend via the proxy at {API_BASE}.</p>
        </div>
        <div className="card">
          <h3>Layout</h3>
          <p className="muted">Left column width: {panelWidth}px</p>
          <input
            type="range"
            min={280}
            max={760}
            step={10}
            value={panelWidth}
            onChange={(e) => setPanelWidth(Number.parseInt(e.target.value, 10))}
            style={{ width: "100%" }}
          />
        </div>
        <div className="card">
          <h3>Graph View</h3>
          <label className="muted graph-query-label">
            Query (defaults to latest QA from chat or Vision Capture)
            <input
              className="graph-query-input"
              value={graphQuery}
              onChange={(e) => setGraphQuery(e.target.value)}
              placeholder="Type a query to filter graph for this profile"
            />
          </label>
          <div className="file-actions">
            <button
              onClick={() => {
                setShowGraphView(true);
                setGraphMinimized(false);
                void loadGraphPreview(undefined, graphQuery);
              }}
              disabled={!activeProfile || graphLoading}
            >
              {graphLoading ? "Loading..." : "Show Query Graph"}
            </button>
            <button
              onClick={() => {
                const lastQA = getLastGraphQuery(messages, captureResult);
                if (lastQA) {
                  setGraphQuery(lastQA);
                }
                setShowGraphView(true);
                setGraphMinimized(false);
                void loadGraphPreview(undefined, lastQA);
              }}
              disabled={!activeProfile || graphLoading}
            >
              Use Last QA
            </button>
            <button onClick={() => setShowGraphView(false)} disabled={!showGraphView}>
              Hide
            </button>
          </div>
          {graphUsedQuery && <p className="muted">Using query: {graphUsedQuery}</p>}
          {graphStatus && <p className="muted">{graphStatus}</p>}
        </div>
        <div className="card">
          <h3>Profile</h3>
          <select
            className="doc-select"
            value={activeProfile}
            onChange={(e) => setActiveProfile(e.target.value)}
          >
            {profiles.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.profile_id} ({p.doc_count} docs)
              </option>
            ))}
          </select>
          <div className="file-actions">
            <button onClick={() => activeProfile && void useProfile(activeProfile)} disabled={!activeProfile}>
              Use Profile
            </button>
            <button onClick={() => void loadProfiles()}>Refresh</button>
            <button onClick={() => void runProfilePurge(true)} disabled={!activeProfile}>
              Preview Clear
            </button>
            <button className="danger-btn" onClick={() => void runProfilePurge(false)} disabled={!activeProfile}>
              Clear Supabase
            </button>
            <button onClick={() => void runProfileDelete(true)} disabled={!activeProfile}>
              Preview Delete
            </button>
            <button className="danger-btn" onClick={() => void runProfileDelete(false)} disabled={!activeProfile}>
              Delete Profile
            </button>
          </div>
          <label className="muted checkbox-row">
            <input
              type="checkbox"
              checked={profilePurgeIncludeArtifacts}
              onChange={(e) => setProfilePurgeIncludeArtifacts(e.target.checked)}
            />
            Also clear profile artifacts/history
          </label>
          {activeProfile && (
            <p className="muted">
              {(profiles.find((p) => p.profile_id === activeProfile)?.summary || "").trim() || "No summary available."}
            </p>
          )}
          {profileStatus && <p className="muted">{profileStatus}</p>}
        </div>
        <div className="card">
          <h3>Profile History</h3>
          <div className="file-actions">
            <button onClick={() => void loadProfileHistory()} disabled={!activeProfile}>
              Refresh
            </button>
          </div>
          {profileHistoryStatus && <p className="muted">{profileHistoryStatus}</p>}
          {profileArtifacts.length === 0 && !profileHistoryStatus && (
            <p className="muted">No recent artifacts for this profile yet.</p>
          )}
          {profileArtifacts.length > 0 && (
            <ul className="muted file-list">
              {profileArtifacts.map((item) => {
                const created = String(item.created_at || "").trim();
                const createdLabel = created ? new Date(created).toLocaleString() : "unknown time";
                return (
                  <li key={item.artifact_id || `${item.agent || "agent"}-${item.path || "path"}-${created}`}>
                    <strong>{createdLabel}</strong>{" "}
                    <span>
                      {item.agent || "agent"} | {item.artifact_type || "artifact"} | {item.path || "n/a"}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="card">
          <h3>Domain Wizard</h3>
          <div className="muted small-grid">
            <label>
              Profile Name
              <input
                value={wizardProfileName}
                onChange={(e) => setWizardProfileName(e.target.value)}
                placeholder="texas_insurance_adjuster"
              />
            </label>
            <label>
              Domain Seed
              <input
                value={wizardDomainSeed}
                onChange={(e) => setWizardDomainSeed(e.target.value)}
                placeholder="texas insurance adjuster"
              />
            </label>
            <label>
              Platform
              <select value={wizardPlatform} onChange={(e) => setWizardPlatform(e.target.value)}>
                <option value="">default</option>
                <option value="openai">openai</option>
                <option value="ollama">ollama</option>
              </select>
            </label>
            <label>
              AI Model
              <input
                value={wizardModel}
                onChange={(e) => setWizardModel(e.target.value)}
                placeholder="gpt-5.2 / deepseek-v3.1:671b-cloud"
              />
            </label>
            <label>
              Ollama Target
              <select value={wizardOllamaTarget} onChange={(e) => setWizardOllamaTarget(e.target.value)}>
                <option value="">default</option>
                <option value="local">local</option>
                <option value="cloud">cloud</option>
              </select>
            </label>
            <label className="inline">
              <input
                type="checkbox"
                checked={wizardNoFallback}
                onChange={(e) => setWizardNoFallback(e.target.checked)}
              />
              no AI fallback
            </label>
          </div>
          <div className="file-actions">
            <button onClick={() => void runDomainWizard()} disabled={wizardRunning || !wizardProfileName.trim()}>
              {wizardRunning ? "Running..." : "Generate Domain Prompts"}
            </button>
          </div>
          {wizardStatus && <p className="muted">{wizardStatus}</p>}
          {wizardResult?.command && <p className="muted">Command: {wizardResult.command}</p>}
          {typeof wizardResult?.exit_code === "number" && (
            <p className="muted">Exit code: {wizardResult.exit_code}</p>
          )}
          {wizardResult?.stderr && <p className="muted">Warnings: {wizardResult.stderr}</p>}
          <div className="file-actions">
            <button onClick={() => void loadDomainWizardHistory()} disabled={!activeProfile && !wizardProfileName.trim()}>
              Refresh History
            </button>
          </div>
          {wizardHistoryStatus && <p className="muted">{wizardHistoryStatus}</p>}
          {wizardHistory.length === 0 && !wizardHistoryStatus && (
            <p className="muted">No domain wizard runs recorded for this profile.</p>
          )}
          {wizardHistory.length > 0 && (
            <ul className="muted file-list">
              {wizardHistory.map((item) => {
                const created = String(item.created_at || "").trim();
                const createdLabel = created ? new Date(created).toLocaleString() : "unknown time";
                const targetCount = Object.keys(item.generated_targets || {}).length;
                return (
                  <li key={item.artifact_id || `${item.run_id || "run"}-${created}`}>
                    <strong>{createdLabel}</strong>{" "}
                    <span>
                      run {item.run_id || "n/a"} | exit {item.exit_code ?? "n/a"} | targets {targetCount}
                      {item.rolled_back ? " | rolled back" : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="card">
          <h3>Examples</h3>
          <ul className="muted">
            {examplePrompts.map((p) => (
              <li key={p}>
                <button className="link-btn" onClick={() => void send(p)} disabled={loading}>
                  {p}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="card">
          <h3>Research .md files</h3>
          {files.length === 0 && <p className="muted">No files found under configured research root.</p>}
          <ul className="muted file-list">
            {files.map((f) => (
              <li key={f.path}>
                <button
                  className={`link-btn ${selectedFile === f.path ? "active" : ""}`}
                  onClick={() => void loadFileContent(f.path)}
                  disabled={loading}
                >
                  {f.path}
                </button>
              </li>
            ))}
          </ul>
          {fileStatus && <p className="muted">{fileStatus}</p>}
          <div className="file-actions">
            <button onClick={saveFile} disabled={!selectedFile}>
              Save
            </button>
            <button onClick={sendSelectedToRag} disabled={!selectedFile}>
              Send to RAG
            </button>
            <button onClick={runSelectedToRagCag} disabled={!selectedFile}>
              RAG + CAG
            </button>
            <button onClick={deleteFile} disabled={!selectedFile || deleting}>
              {deleting ? "Deleting..." : "Delete"}
            </button>
            <button onClick={() => void loadFiles()}>Refresh</button>
          </div>
        </div>
        <div className="card">
          <h3>Uploads (for .md prep)</h3>
          <input
            type="file"
            multiple
            onChange={(e) => {
              const picked = Array.from(e.target.files || []);
              if (picked.length) void uploadFiles(picked);
              e.target.value = "";
            }}
            disabled={uploading}
          />
          {uploadStatus && <p className="muted">{uploadStatus}</p>}
          <ul className="muted file-list">
            {uploads.map((u) => (
              <li key={u.path} className="upload-row">
                <span>{u.name}</span>
                <div className="row-actions">
                  <button
                    onClick={() => void sendUploadToRag(u.path)}
                    disabled={loading || preppingUploadPath === u.path}
                  >
                    {preppingUploadPath === u.path ? "Preparing..." : "Send to RAG"}
                  </button>
                  <button
                    onClick={() => void sendUploadToRagCag(u.path)}
                    disabled={loading || preppingUploadPath === u.path}
                  >
                    {preppingUploadPath === u.path ? "Preparing..." : "RAG + CAG"}
                  </button>
                  <button onClick={() => void sendDocToCag(`${RESEARCH_ROOT}/${u.path}`)}>Send to CAG</button>
                  <button onClick={() => void deleteUpload(u.path)}>Delete</button>
                </div>
              </li>
            ))}
          </ul>
          <button onClick={() => void loadUploads()}>Refresh uploads</button>
          {cagStatus && <p className="muted">{cagStatus}</p>}
        </div>
        <div className="card">
          <h3>Docs in data/ & uploads/</h3>
          {docStatus && <p className="muted">{docStatus}</p>}
          <select
            className="doc-select"
            size={8}
            multiple
            value={selectedDocs}
            onChange={(e) =>
              setSelectedDocs(Array.from(e.target.selectedOptions).map((opt) => opt.value))
            }
          >
            {docs.map((d) => (
              <option key={d.path} value={d.path}>
                {d.label}
              </option>
            ))}
          </select>
          <p className="muted">
            {selectedDocs.length === 0
              ? "Select one or more documents."
              : `${selectedDocs.length} selected`}
          </p>
          <div className="file-actions">
            <button onClick={() => sendDocToRag()} disabled={selectedDocs.length === 0}>
              Send to RAG
            </button>
            <button onClick={() => void sendDocToRagCag()} disabled={selectedDocs.length === 0}>
              RAG + CAG
            </button>
            <button onClick={() => void sendDocToCag()} disabled={selectedDocs.length === 0}>
              Send to CAG
            </button>
            <button
              onClick={() => void deleteDocs()}
              disabled={selectedDocs.length === 0 || deletingDocs}
            >
              {deletingDocs ? "Deleting..." : "Delete Selected"}
            </button>
            <button onClick={() => void loadDocs()}>Refresh</button>
          </div>
          {cagStatus && <p className="muted">{cagStatus}</p>}
        </div>
        <div className="card">
          <h3>Vision Capture</h3>
          <div className="muted small-grid">
            <div className="mode-box">
              <span className="muted">Capture Source</span>
              <label className="inline">
                <input
                  type="radio"
                  name="capture-source"
                  value="browser"
                  checked={captureSource === "browser"}
                  onChange={() => setCaptureSource("browser")}
                />
                browser (client)
              </label>
              <label className="inline">
                <input
                  type="radio"
                  name="capture-source"
                  value="server"
                  checked={captureSource === "server"}
                  onChange={() => setCaptureSource("server")}
                />
                server monitor
              </label>
            </div>
            {captureSource === "browser" ? (
              <p className="muted">
                Browser capture grabs your shared tab/window and sends one frame to Copilot for OCR and CAG.
              </p>
            ) : (
              <p className="muted">
                Server capture reads the screen attached to the backend host/container, not your browser window.
              </p>
            )}
            <label>
              DPI
              <input value={captureDpi} onChange={(e) => setCaptureDpi(e.target.value)} placeholder="96" />
            </label>
            <label>
              Top (in)
              <input value={topIn} onChange={(e) => setTopIn(e.target.value)} placeholder="0.0" />
            </label>
            <label>
              Left (in)
              <input value={leftIn} onChange={(e) => setLeftIn(e.target.value)} placeholder="0.5" />
            </label>
            <label>
              Right (in)
              <input value={rightIn} onChange={(e) => setRightIn(e.target.value)} placeholder="0.5" />
            </label>
            <label>
              Bottom (in)
              <input value={bottomIn} onChange={(e) => setBottomIn(e.target.value)} placeholder="1.5" />
            </label>
            {captureSource === "server" && (
              <>
                <label>
                  Monitor
                  <input
                    type="number"
                    min={1}
                    value={monitorIndex}
                    onChange={(e) => setMonitorIndex(Number(e.target.value))}
                  />
                </label>
              </>
            )}
            <div className="mode-box">
              <span className="muted">Mode</span>
              <label className="inline">
                <input
                  type="radio"
                  name="capture-mode"
                  value="local"
                  checked={captureMode === "local"}
                  onChange={() => setCaptureMode("local")}
                />
                local
              </label>
              <label className="inline">
                <input
                  type="radio"
                  name="capture-mode"
                  value="remote"
                  checked={captureMode === "remote"}
                  onChange={() => setCaptureMode("remote")}
                />
                remote
              </label>
              <label className="inline">
                <input
                  type="radio"
                  name="capture-mode"
                  value="remote_image"
                  checked={captureMode === "remote_image"}
                  onChange={() => setCaptureMode("remote_image")}
                />
                remote_image
              </label>
            </div>
            <label>
              Remote CAG URL
              <input value={remoteCagUrl} onChange={(e) => setRemoteCagUrl(e.target.value)} placeholder="http://cag-service:8000/cag-answer" />
            </label>
            <label>
              Remote Image URL
              <input value={remoteImageUrl} onChange={(e) => setRemoteImageUrl(e.target.value)} placeholder="http://cag-service:8000/cag-ocr-answer" />
            </label>
            <label>
              Platform
              <select value={reasonPlatform} onChange={(e) => setReasonPlatform(e.target.value)}>
                <option value="">default</option>
                <option value="openai">openai</option>
                <option value="ollama">ollama</option>
              </select>
            </label>
            <label>
              Model
              <input value={reasonModel} onChange={(e) => setReasonModel(e.target.value)} placeholder="gpt-4o / llama3.1:8b" />
            </label>
            <label>
              Ollama Target
              <select value={ollamaTarget} onChange={(e) => setOllamaTarget(e.target.value)}>
                <option value="">default</option>
                <option value="local">local</option>
                <option value="cloud">cloud</option>
              </select>
            </label>
          </div>
          <div className="file-actions">
            <button onClick={() => void triggerCapture()}>
              {captureSource === "browser" ? "Capture browser frame" : "Run server capture"}
            </button>
            {captureStatus && <p className="muted">{captureStatus}</p>}
          </div>
          {captureResult && (
            <div className="muted capture-output">
              <div className="capture-section">
                <p><strong>Question:</strong></p>
                <p>{captureQuestion || "n/a"}</p>
              </div>
              <div className="capture-section">
                <p><strong>Answer:</strong></p>
                <p>{captureSections.answer}</p>
              </div>
              <div className="capture-section">
                <p><strong>Rationale:</strong></p>
                <p>{captureSections.rationale}</p>
              </div>
              <div className="capture-section">
                <p><strong>Citations:</strong></p>
                <p>{captureSections.citations}</p>
              </div>
            </div>
          )}
        </div>
        <div className="card">
          <h3>Status</h3>
          <p className="muted">{loading ? "Waiting for backend..." : "Idle"}</p>
          {error && <p className="error">Error: {error}</p>}
        </div>
      </aside>

      <main className="content">
        <div className="card">
          <h3>Chat</h3>
          <div className="chat-window">
            {messages.map((m, idx) => (
              <div key={idx} className={`bubble ${m.role}`}>
                <strong>{m.role === "user" ? "You" : m.role === "assistant" ? "Copilot" : "System"}:</strong>{" "}
                <span>{m.text}</span>
              </div>
            ))}
          </div>
          <form className="chat-input" onSubmit={handleSubmit}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type a command or question..."
              disabled={loading}
            />
            <button type="submit" disabled={loading || !input.trim()}>
              {loading ? "Sending..." : "Send"}
            </button>
          </form>
        </div>

        {showGraphView && (
          <div className="card">
            <div className="graph-header">
              <h3>CAG Graph ({activeProfile || "no profile"})</h3>
              <div className="file-actions">
                <button onClick={() => setGraphMinimized((prev) => !prev)}>
                  {graphMinimized ? "Expand" : "Minimize"}
                </button>
                <button onClick={() => void loadGraphPreview(undefined, graphQuery)} disabled={graphLoading}>
                  {graphLoading ? "Refreshing..." : "Refresh Graph"}
                </button>
              </div>
            </div>
            {graphUsedQuery && <p className="muted">Query: {graphUsedQuery}</p>}
            {graphStatus && <p className="muted">{graphStatus}</p>}
            {!graphMinimized && (
              <>
                {graphNodes.length === 0 ? (
                  <p className="muted">No graph data available for this profile yet.</p>
                ) : (
                  <div className="graph-wrap">
                    <svg
                      className="graph-svg"
                      viewBox={`0 0 ${graphLayout.width} ${graphLayout.height}`}
                      role="img"
                      aria-label="CAG graph preview"
                    >
                      {graphEdges.map((edge, idx) => {
                        const src = graphLayout.points.get(edge.src);
                        const dst = graphLayout.points.get(edge.dst);
                        if (!src || !dst) return null;
                        return (
                          <g key={`${edge.src}-${edge.dst}-${idx}`}>
                            <line x1={src.x} y1={src.y} x2={dst.x} y2={dst.y} className="graph-edge" />
                          </g>
                        );
                      })}
                      {graphNodes.map((node) => {
                        const point = graphLayout.points.get(node.id);
                        if (!point) return null;
                        return (
                          <g key={node.id}>
                            <circle cx={point.x} cy={point.y} r={18} className="graph-node" />
                            <text x={point.x} y={point.y + 4} className="graph-node-text">
                              {truncateLabel(node.title)}
                            </text>
                          </g>
                        );
                      })}
                    </svg>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {selectedFile && (
          <div className="card">
            <h3>Edit {selectedFile}</h3>
            <textarea
              className="file-editor"
              value={fileContent}
              onChange={(e) => setFileContent(e.target.value)}
              rows={12}
            />
          </div>
        )}
      </main>
    </div>
  );
}
