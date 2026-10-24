"use client";

import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, DEMO_MODE, errorMessage, isApiError, type FileItem } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { Banner, Button, ErrorState, Icon, Label, Modal, Select, Skeleton } from "@/components/ui";
import { useUi } from "@/lib/session";
import { acceptFromLabels } from "./shared";

const SUCCESS_DEMO = "Sample file added. No real file was read from your device.";

/** Honest result text, based on what the server reports for this file. */
function uploadResult(f: FileItem): { text: string; tone: "success" | "danger" | "info" } {
  if (f.status === "quarantined" || f.scan_status === "infected")
    return { text: `${f.name}: quarantined — the malware scanner found a threat. It can't be opened or used by any task.`, tone: "danger" };
  if (f.scan_status === "clean") return { text: "Uploaded to private storage and scanned — no malware found. It is not shared with anyone.", tone: "success" };
  return { text: "Uploaded to private storage. It is not shared with anyone. No malware scanner is connected, so it is marked Not scanned.", tone: "success" };
}

/** "Add a file" dialog (prototype modal.is.upload), with a real file input. */
export function UploadModal({
  defaultCollection,
  initialFile,
  onClose,
}: {
  defaultCollection: string | null;
  initialFile?: File | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { toast } = useUi();
  const inputId = useId();
  const collId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(initialFile ?? null);
  const [collection, setCollection] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const limits = useQuery({ queryKey: ["upload-limits"], queryFn: () => api.uploadLimits(), staleTime: 5 * 60_000 });
  const collections = limits.data?.collections ?? [];
  const coll = collection ?? (defaultCollection && collections.includes(defaultCollection) ? defaultCollection : collections[0] ?? "");
  const tooBig = !!(file && limits.data && file.size > limits.data.max_bytes);

  const m = useMutation({
    mutationFn: () => api.uploadFile(file as File, coll),
    onSuccess: (f) => {
      void qc.invalidateQueries({ queryKey: ["files"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
      if (DEMO_MODE) toast(SUCCESS_DEMO, "info");
      else {
        const r = uploadResult(f);
        toast(r.text, r.tone);
      }
      onClose();
    },
  });

  const pick = (f: File | null | undefined) => {
    if (!f) return;
    setFile(f);
    m.reset();
  };

  let serverHint: string | null = null;
  if (isApiError(m.error) && limits.data) {
    if (m.error.status === 415) serverHint = `Allowed types: ${limits.data.allowed_types.join(", ")}.`;
    if (m.error.status === 413) serverHint = `Maximum size: ${fmtBytes(limits.data.max_bytes)} per file.`;
  }
  // scanner_unavailable (503): the server's message already says the file wasn't accepted and to retry.
  const scannerDown = isApiError(m.error) && m.error.code === "scanner_unavailable";

  const canUpload = !!file && !!coll && !tooBig && !m.isPending;

  return (
    <Modal
      title="Add a file"
      onClose={m.isPending ? () => {} : onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={m.isPending}>
            Cancel
          </Button>
          <Button variant="primary" className="px-4" icon="upload" disabled={!canUpload} loading={m.isPending} onClick={() => canUpload && m.mutate()}>
            {m.isPending ? "Uploading…" : "Upload file"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 p-5">
        {limits.isLoading ? (
          <Skeleton lines={3} />
        ) : limits.isError ? (
          <ErrorState error={limits.error} onRetry={() => void limits.refetch()} retrying={limits.isFetching} title="We couldn't load upload limits" />
        ) : (
          <>
            <input
              ref={inputRef}
              id={inputId}
              type="file"
              className="peer sr-only"
              accept={acceptFromLabels(limits.data?.allowed_types ?? [])}
              onChange={(e) => pick(e.target.files?.[0])}
              disabled={m.isPending}
            />
            <label
              htmlFor={inputId}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                pick(e.dataTransfer.files?.[0]);
              }}
              className="cursor-pointer rounded-[10px] peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary border-[1.5px] border-dashed px-6 py-6 text-center text-muted hover:bg-primary-tint"
              style={{ borderColor: dragOver ? "#3448C5" : "#D0D5DD", background: dragOver ? "#F5F7FE" : undefined }}
            >
              <Icon name={file ? "file" : "cloud-upload"} size={22} className="mx-auto text-primary" />
              {file ? (
                <>
                  <div className="mt-1.5 truncate font-medium text-ink">{file.name}</div>
                  <div className="text-[13px]">
                    {fmtBytes(file.size)} · <span className="text-primary">Choose a different file</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="mt-1.5 font-medium text-ink">Drop a file here or browse</div>
                  <div className="text-[13px]">
                    {limits.data?.allowed_types.join(", ")} · up to {fmtBytes(limits.data?.max_bytes ?? 0)}
                  </div>
                </>
              )}
            </label>

            <div>
              <Label htmlFor={collId}>Collection</Label>
              <Select id={collId} value={coll} onChange={(e) => setCollection(e.target.value)} disabled={m.isPending} className="h-[38px]">
                {collections.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </div>

            <p className="text-[13.5px] text-ink-2">
              {DEMO_MODE ? (
                <>
                  Demo: only the file name and size are used — nothing leaves this browser. It goes through Uploading → Scanning → Processing →
                  Ready and is classified <strong>Unknown — sharing blocked</strong> until you classify it.
                </>
              ) : (
                <>
                  The file is type-checked and size-checked on the server, then kept in private storage. It is classified{" "}
                  <strong>Unknown — sharing blocked</strong> until you classify it. If this server has a malware scanner, the file is scanned before it is
                  stored and refused if the scanner can&apos;t be reached; otherwise it is marked Not scanned.
                </>
              )}
            </p>

            {tooBig && limits.data && (
              <Banner tone="danger" icon="circle-alert" role="alert">
                This file is {fmtBytes(file?.size ?? 0)}. The limit is {fmtBytes(limits.data.max_bytes)} per file.
              </Banner>
            )}
            {m.error && (
              <Banner tone="danger" icon={scannerDown ? "shield-alert" : "circle-alert"} role="alert" title="The file wasn't uploaded">
                {errorMessage(m.error)}
                {serverHint && <> {serverHint}</>}
              </Banner>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
