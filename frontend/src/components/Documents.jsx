import { useRef, useState } from "react";
import { api } from "../api.js";
import { acceptAttribute, formatDate, typeList } from "../lib.js";

export default function Documents({ docs, loading, fileTypes, token, excluded, onToggle, onChanged, notify }) {
  const input = useRef(null);
  const [uploading, setUploading] = useState("");  // name of the file being indexed
  const [dragging, setDragging] = useState(false);
  const [confirming, setConfirming] = useState(null);  // doc_id waiting for "Yes, delete"
  const [deleting, setDeleting] = useState(null);

  async function upload(files) {
    for (const file of files) {  // one at a time: indexing is the slow part and the server limits concurrency anyway
      setUploading(file.name);
      try {
        const form = new FormData();
        form.append("file", file);
        const doc = await api("/documents", { token, method: "POST", body: form });
        notify("ok", `Added ${doc.file_name} (${doc.num_chunks} chunks). You can ask about it now.`);
        await onChanged();
      } catch (e) {
        notify("error", `${file.name}: ${e.message}`);
      }
    }
    setUploading("");
    if (input.current) input.current.value = "";
  }

  async function remove(doc) {
    setDeleting(doc.doc_id);
    try {
      await api("/documents/" + encodeURIComponent(doc.doc_id), { token, method: "DELETE" });
      notify("ok", `Deleted ${doc.file_name}.`);
      await onChanged();
    } catch (e) {
      notify("error", e.message);
    } finally {
      setDeleting(null);
      setConfirming(null);
    }
  }

  return (
    <section className="card" aria-labelledby="docs-title">
      <h2 id="docs-title" className="text-base font-semibold">Documents</h2>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (!uploading) upload([...e.dataTransfer.files]); }}
        className={`mt-3 rounded-xl border-2 border-dashed p-4 text-center transition-colors ${dragging ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950" : "border-slate-300 dark:border-slate-700"}`}
      >
        <input ref={input} type="file" multiple className="sr-only" tabIndex={-1} accept={acceptAttribute(fileTypes)}
          onChange={(e) => upload([...e.target.files])} aria-label="Choose documents to upload" />
        {uploading ? (
          <p role="status" className="flex items-center justify-center gap-2 py-1">
            <span className="size-4 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" aria-hidden="true" />
            <span className="min-w-0 break-words">Reading and indexing <strong>{uploading}</strong>…</span>
          </p>
        ) : (
          <>
            <button type="button" className="btn-primary" onClick={() => input.current?.click()}>Choose files</button>
            <p className="muted mt-2">or drop them here{fileTypes.length > 0 && <> · {typeList(fileTypes)}</>}</p>
          </>
        )}
      </div>

      {loading ? (
        <p className="muted mt-4">Loading your documents…</p>
      ) : docs.length === 0 ? (
        <p className="muted mt-4">No documents yet. Upload one and you can start asking questions about it.</p>
      ) : (
        <>
          {docs.length > 1 && <p className="muted mt-4">Tick the documents a question should use.</p>}
          <ul className="mt-2 divide-y divide-slate-200 dark:divide-slate-800">
            {docs.map((d) => (
              <li key={d.doc_id} className="flex items-start gap-2 py-3">
                <label className="-ml-2 flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center">
                  <input type="checkbox" className="size-5 accent-indigo-600" checked={!excluded.has(d.doc_id)} onChange={() => onToggle(d.doc_id)}
                    aria-label={`Use ${d.file_name} when answering`} />
                </label>
                <div className="min-w-0 flex-1 pt-2">
                  <p className="break-words font-medium">{d.file_name}</p>
                  <p className="muted">{d.file_type.toUpperCase()} · {d.num_chunks} {d.num_chunks === 1 ? "chunk" : "chunks"} · {formatDate(d.uploaded_at)}</p>
                  {d.flagged && (
                    <p className="mt-1 rounded-lg border border-amber-300 bg-amber-50 px-2 py-1 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
                      This document contains instruction-like text. It is treated as data and never followed.
                    </p>
                  )}
                </div>
                {confirming === d.doc_id ? (
                  <div className="flex shrink-0 flex-col gap-1 sm:flex-row">
                    <button type="button" className="btn-danger" disabled={deleting === d.doc_id} onClick={() => remove(d)}>
                      {deleting === d.doc_id ? "Deleting…" : "Delete"}
                    </button>
                    <button type="button" className="btn-ghost" disabled={deleting === d.doc_id} onClick={() => setConfirming(null)}>Keep</button>
                  </div>
                ) : (
                  <button type="button" className="btn-ghost shrink-0" onClick={() => setConfirming(d.doc_id)} aria-label={`Delete ${d.file_name}`}>
                    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
                    </svg>
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
