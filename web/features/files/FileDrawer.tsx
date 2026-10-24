"use client";

import Link from "next/link";
import { useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, DEMO_MODE, errorMessage, isApiError, type FileClassification, type FileItem } from "@/lib/api";
import { CLASSIFICATION, FILE_STATUS } from "@/lib/labels";
import { fmtBytes, fmtDateTime } from "@/lib/format";
import { Drawer, ErrorState, Icon, Skeleton } from "@/components/ui";
import { useUi } from "@/lib/session";
import { DeleteFileModal } from "./DeleteFileModal";
import { fileIcon, isBusy, scanText } from "./shared";

const CLS_ORDER: FileClassification[] = ["confidential", "internal", "public", "unknown"];

const CLS_TOAST = DEMO_MODE
  ? "Classification changed (demo). In production this is logged and checked by the policy engine."
  : "Classification saved and logged. The policy engine uses it before any step that could send this file anywhere.";

const BTN = "inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-[13.5px] font-medium whitespace-nowrap hover:no-underline";

/** File details drawer (prototype fileSel). */
export function FileDrawer({ fileId, fallback, onClose }: { fileId: string; fallback?: FileItem; onClose: () => void }) {
  const qc = useQueryClient();
  const { toast } = useUi();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const radios = useRef<(HTMLButtonElement | null)[]>([]);

  const q = useQuery({
    queryKey: ["file", fileId],
    queryFn: () => api.getFile(fileId),
    placeholderData: fallback,
    refetchInterval: (query) => (query.state.data && isBusy(query.state.data.status) ? 1500 : false),
  });
  const f = q.data;

  const cls = useMutation({
    mutationFn: (classification: FileClassification) => api.updateFile(fileId, { classification }),
    onSuccess: (updated) => {
      qc.setQueryData(["file", fileId], updated);
      void qc.invalidateQueries({ queryKey: ["files"] });
      void qc.invalidateQueries({ queryKey: ["file", fileId] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
      toast(CLS_TOAST, DEMO_MODE ? "info" : "success");
    },
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  const onRadioKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const d = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    radios.current[(i + d + CLS_ORDER.length) % CLS_ORDER.length]?.focus();
  };

  const header = (
    <>
      <Icon name={f ? fileIcon(f) : "file"} size={18} className="text-ink-3" />
      <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold">{f?.name ?? "File details"}</h2>
    </>
  );

  if (!f) {
    // Same element shape as the loaded state, so the drawer isn't remounted when data arrives.
    return (
      <>
        <Drawer label="File details" onClose={onClose} header={header}>
          {q.isError && isApiError(q.error) && q.error.status === 410 ? (
            <div role="status" className="flex flex-col items-start gap-2 text-[13.5px]">
              <Icon name="trash-2" size={20} className="text-muted" />
              <div className="font-semibold">This file was deleted</div>
              <p className="text-muted [text-wrap:pretty]">{q.error.message}</p>
            </div>
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} title="We couldn't load this file" />
          ) : (
            <Skeleton lines={6} />
          )}
        </Drawer>
      </>
    );
  }

  const st = FILE_STATUS[f.status];
  const quarantined = f.status === "quarantined";
  const downloadUrl = !DEMO_MODE && !quarantined ? api.fileDownloadUrl(f.id) : null;
  const pendingCls = cls.isPending ? cls.variables : null;
  const current = pendingCls ?? f.classification;

  const footer = (
    <>
      {f.deletion_requested ? (
        <button type="button" disabled className={`${BTN} border border-line bg-subtle text-muted`}>
          <Icon name="hourglass" size={14} />
          Deletion requested — waiting for approval
        </button>
      ) : (
        <button type="button" onClick={() => setConfirmDelete(true)} className={`${BTN} border border-danger-line bg-surface text-danger hover:bg-danger-soft`}>
          <Icon name="trash-2" size={14} />
          Request deletion
        </button>
      )}
      <div className="flex flex-wrap gap-2">
        {downloadUrl && (
          <a href={downloadUrl} download className={`${BTN} border border-line-strong bg-surface text-ink hover:bg-subtle hover:text-ink`}>
            <Icon name="download" size={14} />
            Download
          </a>
        )}
        {f.status === "ready" && (
          <Link href={`/tasks/new?attach=${encodeURIComponent(f.id)}`} className={`${BTN} bg-primary text-white hover:bg-primary-hover hover:text-white`}>
            Use in a new task
          </Link>
        )}
      </div>
    </>
  );

  return (
    <>
      <Drawer label="File details" onClose={confirmDelete ? () => {} : onClose} header={header} footer={footer}>
        <div className="grid h-[180px] place-items-center rounded-[10px] border border-line bg-[repeating-linear-gradient(135deg,#F6F8FB_0_10px,#EEF1F6_10px_20px)] p-3 text-center font-mono text-xs text-muted">
          file preview · safe formats only
        </div>

        {(f.error_message || quarantined) && (
          <div role="alert" className="flex gap-2 rounded-lg border border-danger-line bg-danger-soft px-3.5 py-3 text-[13.5px] text-danger">
            <Icon name="circle-alert" size={15} className="mt-0.5" />
            <span>
              {quarantined && "Quarantined: malware found — can't be opened. The file is isolated and can't be downloaded or used by any task."}
              {f.error_message && `${quarantined ? " " : ""}${f.error_message}`}
            </span>
          </div>
        )}

        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[13.5px]">
          <dt className="text-muted">Status</dt>
          <dd className="m-0 flex items-center gap-1.5 font-medium" style={{ color: st.fg }}>
            <Icon name={isBusy(f.status) ? "loader" : st.icon} size={14} spin={isBusy(f.status)} />
            {st.label}
          </dd>
          <dt className="text-muted">Type · size</dt>
          <dd className="m-0">
            {f.type_label} · {fmtBytes(f.size_bytes)}
          </dd>
          <dt className="text-muted">Collection</dt>
          <dd className="m-0">{f.collection}</dd>
          <dt className="text-muted">Uploaded</dt>
          <dd className="m-0">{fmtDateTime(f.uploaded_at)}</dd>
          <dt className="text-muted">Stored in</dt>
          <dd className="m-0">
            {DEMO_MODE ? "Demo: this browser tab only. Production: private server storage, not a public folder." : "Private server storage, not a public folder."}
          </dd>
        </dl>

        <div
          className="flex items-start gap-2 text-[13px]"
          style={{ color: quarantined || f.scan_status === "infected" ? "#B42318" : f.scan_status === "clean" ? "#16803C" : "#667085" }}
        >
          <Icon name={f.scan_status === "clean" && !quarantined ? "shield-check" : "shield-alert"} size={15} className="mt-0.5" />
          <span>
            <span className="sr-only">Malware scan: </span>
            {scanText(f)}
            {f.scan_status === "not_configured" && !quarantined && <span className="text-muted"> — no malware scanner is connected on this server.</span>}
          </span>
        </div>

        <div>
          <div id={`cls-${f.id}`} className="mb-2 text-[13px] font-medium">
            Privacy classification
          </div>
          <div role="radiogroup" aria-labelledby={`cls-${f.id}`} className="flex flex-col gap-1.5">
            {CLS_ORDER.map((k, i) => {
              const c = CLASSIFICATION[k];
              const on = current === k;
              return (
                <button
                  key={k}
                  ref={(el) => {
                    radios.current[i] = el;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  tabIndex={on ? 0 : -1}
                  disabled={cls.isPending || quarantined}
                  onKeyDown={(e) => onRadioKey(e, i)}
                  onClick={() => !on && cls.mutate(k)}
                  className="flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-[13.5px] disabled:cursor-not-allowed disabled:opacity-70"
                  style={{ background: on ? "#EEF1FC" : "#FFFFFF", borderColor: on ? "#3448C5" : "#E4E7EC" }}
                >
                  <Icon name={pendingCls === k ? "loader" : c.icon} size={15} spin={pendingCls === k} style={{ color: c.fg }} />
                  {c.label}
                </button>
              );
            })}
          </div>
          {f.classification === "unknown" && (
            <p className="mt-2 text-[12.5px] text-muted">Files marked Unknown can&apos;t be shared with any external service.</p>
          )}
        </div>
      </Drawer>
      {confirmDelete && <DeleteFileModal file={f} onClose={() => setConfirmDelete(false)} />}
    </>
  );
}
