"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

type Document = {
  id: string;
  name: string;
};

type SearchResult = {
  article_number?: string | number;
  document_name?: string;
  content?: string;
  similarity?: number;
  embedded_file?: string;
  ai_is_relevant?: boolean;
  ai_relevance_score?: number;
  ai_reason?: string;
};

type AIVerification = {
  enabled?: boolean;
  overall?: "high" | "medium" | "low" | string;
  explanation?: string;
  checked_count?: number;
  relevant_count?: number;
};

type SearchResponse = {
  result_count?: number;
  results?: SearchResult[];
  ai_verification?: AIVerification;
  error?: string;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://localhost:8000";

export default function Home() {
  const [query, setQuery] = useState("");
  const [verifyEnabled, setVerifyEnabled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Ready.");
  const [statusKind, setStatusKind] = useState<"idle" | "ok" | "error">("idle");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selectedForReport, setSelectedForReport] = useState<
    Map<string, SearchResult>
  >(new Map());
  const [reportHtml, setReportHtml] = useState<string>("");
  const [generatingReport, setGeneratingReport] = useState(false);
  const [toast, setToast] = useState("");
  const [toastKind, setToastKind] = useState<"ok" | "error">("ok");
  const toastTimerRef = useRef<number | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [selectedDocuments, setSelectedDocuments] = useState<Set<string>>(
    new Set(),
  );
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [showFilterDropdown, setShowFilterDropdown] = useState(false);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null)
        window.clearTimeout(toastTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const fetchDocuments = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/documents`);
        if (!response.ok) throw new Error("Failed to fetch documents");
        const data = await response.json();
        setDocuments(data.documents || []);
        setSelectedDocuments(
          new Set((data.documents || []).map((d: Document) => d.id)),
        );
      } catch (error) {
        console.error("Error fetching documents:", error);
      } finally {
        setDocumentsLoading(false);
      }
    };
    fetchDocuments();
  }, []);

  const statusClass = useMemo(() => {
    if (statusKind === "ok") return "status status-ok";
    if (statusKind === "error") return "status status-error";
    return "status";
  }, [statusKind]);

  const showToast = (message: string, kind: "ok" | "error" = "ok") => {
    setToast(message);
    setToastKind(kind);
    if (toastTimerRef.current !== null)
      window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => {
      setToast("");
      toastTimerRef.current = null;
    }, 2800);
  };

  const makeReportKey = (item: SearchResult) => {
    return `${item.document_name || "doc"}::${item.article_number ?? "art"}::${item.embedded_file || ""}`;
  };

  const toggleSelectForReport = (item: SearchResult) => {
    const key = makeReportKey(item);
    setSelectedForReport((prev) => {
      const next = new Map(prev);
      if (next.has(key)) next.delete(key);
      else next.set(key, item);
      return next;
    });
  };

  const generateReport = async (title?: string) => {
    if (selectedForReport.size === 0) {
      showToast("Select at least one article to generate a report.", "error");
      return;
    }
    setGeneratingReport(true);
    try {
      const articles = Array.from(selectedForReport.values()).map((a) => ({
        article_number: a.article_number,
        document_name: a.document_name,
        content: a.content,
        embedded_file: a.embedded_file,
      }));

      const res = await fetch(`${API_BASE_URL}/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title || "Generated Legal Report",
          articles,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Report generation failed");
      setReportHtml(data.report_html || "");
      showToast("Report generated.", "ok");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(msg, "error");
    } finally {
      setGeneratingReport(false);
    }
  };

  const saveReportAsPdf = () => {
    if (!reportHtml) {
      showToast("No report to save.", "error");
      return;
    }
    const win = window.open("", "_blank");
    if (!win) {
      showToast("Unable to open print window.", "error");
      return;
    }
    win.document.open();
    win.document.write(
      `<!doctype html><html><head><meta charset="utf-8"><title>Report</title>`,
    );
    win.document.write(
      `<style>body{font-family: Arial, Helvetica, sans-serif; padding:20px; color:#111}</style>`,
    );
    win.document.write(reportHtml);
    win.document.write("</body></html>");
    win.document.close();
    // Wait a moment for content to render then call print
    setTimeout(() => {
      try {
        win.print();
      } catch (e) {
        console.warn(e);
      }
    }, 500);
  };

  const deselectAll = () => {
    setSelectedForReport(new Map());
  };

  const toRelativeSource = (pathValue?: string) => {
    if (!pathValue) return "n/a";
    const normalized = String(pathValue).replace(/\\/g, "/");
    const outputIndex = normalized.toLowerCase().indexOf("output/");
    if (outputIndex >= 0) return normalized.slice(outputIndex);
    const parts = normalized.split("/");
    return parts[parts.length - 1] || normalized;
  };

  const handleDocumentToggle = (docId: string) => {
    const newSelected = new Set(selectedDocuments);
    if (newSelected.has(docId)) newSelected.delete(docId);
    else newSelected.add(docId);
    setSelectedDocuments(newSelected);
  };

  const runSearch = async (nextQuery?: string) => {
    const currentQuery = (nextQuery ?? query).trim();
    if (!currentQuery) {
      setStatusKind("error");
      setStatus("Enter a query first.");
      return;
    }
    if (selectedDocuments.size === 0) {
      setStatusKind("error");
      setStatus("Select at least one document to search.");
      return;
    }

    setLoading(true);
    setStatusKind("idle");
    setStatus("Searching...");

    try {
      const response = await fetch(`${API_BASE_URL}/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: currentQuery,
          verify_results: verifyEnabled,
          verify_top_n: 5,
          documents: Array.from(selectedDocuments),
        }),
      });

      const data = (await response.json()) as SearchResponse;
      if (!response.ok) throw new Error(data.error || "Search failed");

      const nextResults = Array.isArray(data.results) ? data.results : [];
      const verification = data.ai_verification;
      setResults(nextResults);
      setStatusKind("ok");

      let statusText = `Found ${data.result_count || nextResults.length} result(s).`;
      if (verifyEnabled && verification?.enabled) {
        statusText += ` AI check: ${verification.relevant_count || 0}/${verification.checked_count || 0} relevant (${verification.overall || "unknown"}).`;
      } else if (
        verifyEnabled &&
        !verification?.enabled &&
        verification?.explanation
      ) {
        statusText += ` ${verification.explanation}`;
      }
      setStatus(statusText);

      const topResult = nextResults[0];
      if (topResult) {
        if (verifyEnabled && topResult.ai_is_relevant === false) {
          showToast(
            "Top match may be weak. Try a more specific query.",
            "error",
          );
        } else {
          showToast(
            verifyEnabled && verification?.enabled
              ? `Closest match verified: ${verification.overall || "medium"} confidence.`
              : "Closest match found.",
            "ok",
          );
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Search failed";
      setResults([]);
      setStatusKind("error");
      setStatus(message);
      showToast(message, "error");
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await runSearch();
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        background:
          "linear-gradient(to bottom right, #020617, #0f172a, #020617)",
      }}
    >
      {/* ── Header ── */}
      <header>
        <div className="header-content">
          <a href="/">
            <span className="logo-mark">Lexis</span>
            <span className="logo-tag">Legal AI</span>
          </a>
        </div>
      </header>

      {/* Toast notifications */}
      {toast && (
        <div
          className={`toast ${toastKind === "ok" ? "toast-ok" : "toast-error"}`}
        >
          {toast}
        </div>
      )}

      <main>
        {/* ── Hero Section ── */}
        <section className="hero-section">
          <div className="eyebrow">
            <span></span>
            Semantic Corpus Search
          </div>

          <h1>
            Find the Right <span className="highlight">Legal Passage</span>
          </h1>

          <p className="description">
            Ask in French or English — vector search returns the closest legal
            passages across your embedded corpus.
          </p>

          {/* Search form */}
          <form onSubmit={onSubmit}>
            <div className="search-box">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. obligations du bailleur en cas de sinistre"
                aria-label="Search query"
                disabled={loading || documentsLoading}
              />
              <button
                type="submit"
                disabled={loading || documentsLoading}
                className="btn-search"
              >
                <svg
                  style={{ width: "20px", height: "20px" }}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                  />
                </svg>
                {loading ? "Searching…" : "Search"}
              </button>
            </div>

            {/* Filter Documents Button */}
            <div className="filter-controls">
              <div style={{ position: "relative", width: "100%" }}>
                <button
                  type="button"
                  onClick={() => setShowFilterDropdown(!showFilterDropdown)}
                  className="btn-filter"
                  disabled={loading || documentsLoading}
                >
                  <svg
                    style={{ width: "20px", height: "20px" }}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"
                    />
                  </svg>
                  Filter Documents
                  <span className="filter-count">
                    {selectedDocuments.size}/{documents.length}
                  </span>
                </button>

                {/* Dropdown menu */}
                {showFilterDropdown && (
                  <div className="dropdown-menu">
                    <div className="dropdown-header">
                      <h3>Select Documents</h3>
                      <p>
                        {selectedDocuments.size} of {documents.length} selected
                      </p>
                    </div>

                    <div className="dropdown-content">
                      {documentsLoading ? (
                        <div
                          style={{ padding: "32px 16px", textAlign: "center" }}
                        >
                          <div
                            style={{
                              width: "20px",
                              height: "20px",
                              border: "2px solid rgba(245, 158, 11, 0.2)",
                              borderTop: "2px solid #f59e0b",
                              borderRadius: "50%",
                              animation: "spin 1s linear infinite",
                              margin: "0 auto",
                            }}
                          ></div>
                          <p
                            style={{
                              color: "#94a3b8",
                              fontSize: "14px",
                              marginTop: "8px",
                            }}
                          >
                            Loading documents…
                          </p>
                        </div>
                      ) : documents.length === 0 ? (
                        <div
                          style={{
                            padding: "32px 16px",
                            textAlign: "center",
                            color: "#94a3b8",
                          }}
                        >
                          No documents available
                        </div>
                      ) : (
                        <div>
                          {documents.map((doc) => (
                            <label key={doc.id} className="dropdown-item">
                              <input
                                type="checkbox"
                                checked={selectedDocuments.has(doc.id)}
                                onChange={() => handleDocumentToggle(doc.id)}
                                disabled={loading}
                              />
                              <span>{doc.name}</span>
                            </label>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="dropdown-footer">
                      <button
                        type="button"
                        onClick={() => setSelectedDocuments(new Set())}
                        className="btn-secondary"
                      >
                        Clear All
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setSelectedDocuments(
                            new Set(documents.map((d) => d.id)),
                          )
                        }
                        className="btn-secondary"
                      >
                        Select All
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowFilterDropdown(false)}
                        className="btn-done"
                      >
                        Done
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* AI Verify Toggle */}
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  padding: "10px 16px",
                  backgroundColor: "#1e293b",
                  border: "1px solid #475569",
                  borderRadius: "8px",
                  cursor: "pointer",
                  transition: "all 0.2s",
                }}
              >
                <input
                  type="checkbox"
                  checked={verifyEnabled}
                  onChange={(e) => setVerifyEnabled(e.target.checked)}
                  disabled={loading}
                  style={{
                    width: "18px",
                    height: "18px",
                    accentColor: "#f59e0b",
                    cursor: "pointer",
                  }}
                />
                <span style={{ fontSize: "14px", fontWeight: "500" }}>
                  Verify with AI
                </span>
              </label>
            </div>

            {/* Status message */}
            {status && (
              <div
                className={`status-message ${
                  statusKind === "ok"
                    ? "status-ok"
                    : statusKind === "error"
                      ? "status-error"
                      : "status-idle"
                }`}
              >
                {status}
              </div>
            )}
          </form>
        </section>

        {/* ── Results Section ── */}
        <section className="results-section" aria-live="polite">
          {results.length > 0 && (
            <div className="report-toolbar">
              <div className="toolbar-left">
                <button
                  type="button"
                  onClick={() => generateReport()}
                  disabled={generatingReport || selectedForReport.size === 0}
                  className="btn-search"
                >
                  {generatingReport ? "Generating…" : "Generate Report"}
                </button>
                <button
                  type="button"
                  onClick={saveReportAsPdf}
                  disabled={!reportHtml}
                  className="btn-secondary"
                >
                  Save as PDF
                </button>
                <button
                  type="button"
                  onClick={deselectAll}
                  disabled={selectedForReport.size === 0}
                  className="btn-secondary"
                >
                  Deselect All
                </button>
              </div>
              <div className="toolbar-right">
                <span className="selection-count">
                  {selectedForReport.size > 0 && (
                    <>
                      <strong>{selectedForReport.size}</strong> selected
                    </>
                  )}
                </span>
              </div>
            </div>
          )}
          {results.length === 0 ? (
            <div className="results-empty">
              <svg
                style={{ width: "64px", height: "64px" }}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4"
                />
              </svg>
              <p style={{ fontSize: "18px" }}>Run a search to see results.</p>
            </div>
          ) : (
            (() => {
              // Group results by document
              const groupedByDoc = results.reduce<
                Record<string, typeof results>
              >((acc, result) => {
                const docName = result.document_name || "Unknown";
                if (!acc[docName]) acc[docName] = [];
                acc[docName].push(result);
                return acc;
              }, {});

              return Object.entries(groupedByDoc).map(
                ([docName, docResults]) => (
                  <div key={docName} className="document-group">
                    <div className="document-group-header">
                      <h2>{docName}</h2>
                      <span className="document-group-count">
                        {docResults.length} result
                        {docResults.length !== 1 ? "s" : ""}
                      </span>
                    </div>
                    <div className="article-grid">
                      {docResults.map((item, index) => {
                        const similarity = Number(item.similarity || 0) * 100;
                        const content = item.content
                          ? String(item.content).slice(0, 460)
                          : "";
                        const isTop =
                          index === 0 &&
                          Object.keys(groupedByDoc)[0] === docName;

                        return (
                          <article
                            key={`${item.article_number ?? "article"}-${index}`}
                            className={`result-card${isTop ? " top" : ""}`}
                          >
                            <div
                              style={{
                                position: "absolute",
                                top: 12,
                                right: 12,
                              }}
                            >
                              <input
                                type="checkbox"
                                aria-label="Select for report"
                                checked={selectedForReport.has(
                                  makeReportKey(item),
                                )}
                                onChange={() => toggleSelectForReport(item)}
                              />
                            </div>
                            {isTop && (
                              <div className="badge-closest">
                                <span className="badge-dot"></span>
                                Closest Match
                              </div>
                            )}

                            <div className="card-header">
                              <h2 className="article-number">
                                Art. {item.article_number || "—"}
                              </h2>
                              <div className="card-badges">
                                {typeof item.ai_is_relevant === "boolean" && (
                                  <span
                                    className={`badge ${
                                      item.ai_is_relevant
                                        ? "badge-relevant"
                                        : "badge-weak"
                                    }`}
                                  >
                                    {item.ai_is_relevant
                                      ? "✓ Relevant"
                                      : "✗ Weak"}
                                    {typeof item.ai_relevance_score === "number"
                                      ? ` · ${(item.ai_relevance_score * 100).toFixed(0)}%`
                                      : ""}
                                  </span>
                                )}
                                <span className="badge badge-similarity">
                                  {similarity.toFixed(1)}% match
                                </span>
                              </div>
                            </div>

                            {/* Similarity bar */}
                            <div className="similarity-bar-wrapper">
                              <div
                                className={`similarity-bar ${
                                  similarity >= 75
                                    ? "high"
                                    : similarity >= 50
                                      ? "medium"
                                      : "low"
                                }`}
                                style={{
                                  width: `${Math.min(similarity, 100)}%`,
                                }}
                              />
                            </div>

                            <p className="card-content">
                              {content}
                              {item.content && String(item.content).length > 460
                                ? "…"
                                : ""}
                            </p>

                            <div className="card-footer">
                              <span className="tag">
                                <span className="tag-dot"></span>
                                {item.document_name || "Unknown"}
                              </span>
                              <span className="source-tag">
                                {toRelativeSource(item.embedded_file)}
                              </span>
                              {item.ai_reason && (
                                <p className="ai-note">
                                  <span style={{ fontWeight: "600" }}>
                                    Note:
                                  </span>{" "}
                                  {item.ai_reason}
                                </p>
                              )}
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </div>
                ),
              );
            })()
          )}

          {/* Report Preview */}
          {reportHtml && (
            <div className="report-preview">
              <div className="report-preview-header">
                <h3>📄 Generated Report</h3>
                <button
                  type="button"
                  onClick={saveReportAsPdf}
                  className="btn-search"
                  style={{ padding: "10px 20px", fontSize: "14px" }}
                >
                  Download as PDF
                </button>
              </div>
              <div
                className="report-content"
                dangerouslySetInnerHTML={{ __html: reportHtml }}
              />
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
