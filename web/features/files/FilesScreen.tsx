"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  api,
  DEMO_MODE,
  type FileClassification,
  type FileListQuery,
  type FileStatus,
} from "@/lib/api";
import { CLASSIFICATION, FILE_STATUS } from "@/lib/labels";
import { fmtBytes, fmtDateTime } from "@/lib/format";
import {
  Button,
  ErrorState,
  Icon,
  PageHeader,
  Skeleton,
  cx,
} from "@/components/ui";
import { useUi } from "@/lib/session";
import { FileDrawer } from "./FileDrawer";
import { RecentlyDeleted } from "./RecentlyDeleted";
import { UploadModal } from "./UploadModal";
import {
  ClassificationBadge,
  FileStatusBadge,
  fileIcon,
  isBusy,
  scanText,
} from "./shared";

const CLS_KEYS = Object.keys(CLASSIFICATION) as FileClassification[];
const STATUS_KEYS = Object.keys(FILE_STATUS) as FileStatus[];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function FilesScreen() {
  const router = useRouter();
  const pathname = usePathname() ?? "/files";
  const params = useSearchParams();
  const openId = params.get("file");
  const { compactRows } = useUi();

  const [search, setSearch] = useState("");
  const [collection, setCollection] = useState<string | null>(null);
  const [trash, setTrash] = useState(false);
  const [cls, setCls] = useState<FileClassification | "">("");
  const [status, setStatus] = useState<FileStatus | "">("");
  const [upload, setUpload] = useState<{ file: File | null } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const q = useDebounced(search.trim(), 250);

  const query: FileListQuery = useMemo(
    () => ({
      ...(q ? { q } : {}),
      ...(collection ? { collection } : {}),
      ...(cls ? { classification: cls } : {}),
      ...(status ? { status } : {}),
    }),
    [q, collection, cls, status],
  );

  const list = useQuery({
    queryKey: ["files", query],
    queryFn: () => api.listFiles(query),
    placeholderData: keepPreviousData,
    // While anything is uploading / scanning / processing, poll so the status moves on screen.
    refetchInterval: (qq) =>
      qq.state.data?.items.some((f) => isBusy(f.status)) ? 1500 : false,
  });
  const limits = useQuery({
    queryKey: ["upload-limits"],
    queryFn: () => api.uploadLimits(),
    staleTime: 5 * 60_000,
  });

  const setFileParam = (id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("file", id);
    else next.delete("file");
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  // Collections: server counts, plus any configured collection that is still empty.
  const colls = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of list.data?.collections ?? []) counts.set(c.name, c.count);
    const names = [...(limits.data?.collections ?? [])];
    for (const n of counts.keys()) if (!names.includes(n)) names.push(n);
    const total = [...counts.values()].reduce((a, b) => a + b, 0);
    return [
      { label: "All files", value: null as string | null, count: total },
      ...names.map((n) => ({
        label: n,
        value: n as string | null,
        count: counts.get(n) ?? 0,
      })),
    ];
  }, [list.data?.collections, limits.data?.collections]);

  const filtersOn = !!(search || cls || status);
  const clear = () => {
    setSearch("");
    setCls("");
    setStatus("");
  };
  const items = list.data?.items ?? [];
  const openFallback = openId ? items.find((f) => f.id === openId) : undefined;
  const rowPad = compactRows ? "py-2 px-4" : "py-3 px-4";

  const typesText = limits.data
    ? `${limits.data.allowed_types.join(", ")} · up to ${fmtBytes(limits.data.max_bytes)} each`
    : DEMO_MODE
      ? "PDF, DOCX, CSV, XLSX, TXT, MD · up to 25 MB each"
      : "Checking allowed types…";

  return (
    <div data-screen-label="Files" className="flex flex-col gap-4">
      <PageHeader
        title="Files & Knowledge"
        subtitle={
          DEMO_MODE
            ? "Files the Agent may read inside this workspace. Sample files only."
            : "Files the Agent may read inside this workspace, kept in private storage."
        }
        actions={
          <Button
            variant="primary"
            icon="upload"
            onClick={() => setUpload({ file: null })}
          >
            Add file
          </Button>
        }
      />

      <div
        role="note"
        className="flex gap-3 rounded-[10px] border border-primary-line bg-primary-soft px-4 py-3.5 text-[#1F2A7A]"
      >
        <Icon name="shield-check" size={18} className="mt-px text-primary" />
        <span>
          <strong>
            Adding a file to your workspace does not authorise sending it to an
            external AI provider or connected app.
          </strong>{" "}
          Sharing always needs a separate approval, and files marked
          Unclassified can&apos;t be shared at all.
        </span>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <nav
          aria-label="Collections"
          className="flex min-w-[180px] flex-[0_1_210px] flex-col gap-0.5 max-[979px]:flex-[1_1_100%]"
        >
          <div className="px-2.5 pb-1.5 text-xs font-medium text-muted">
            Collections
          </div>
          {list.isLoading && colls.length <= 1 ? (
            <Skeleton lines={4} className="px-2.5" />
          ) : (
            colls.map((c) => {
              const active = !trash && collection === c.value;
              return (
                <button
                  key={c.label}
                  type="button"
                  onClick={() => {
                    setTrash(false);
                    setCollection(c.value);
                  }}
                  aria-current={active || undefined}
                  className={cx(
                    "flex h-[34px] items-center gap-2.5 rounded-lg border-0 px-2.5 text-left text-[13.5px]",
                    !active && "hover:bg-line-soft",
                  )}
                  style={{
                    background: active ? "#EEF1FC" : "transparent",
                    color: active ? "#3448C5" : "#344054",
                  }}
                >
                  <Icon name="folder" size={15} />
                  <span className="flex-1 truncate">{c.label}</span>
                  <span className="text-xs text-muted">{c.count}</span>
                </button>
              );
            })
          )}
          <button
            type="button"
            onClick={() => setTrash(true)}
            aria-current={trash || undefined}
            className={cx(
              "mt-2 flex h-[34px] items-center gap-2.5 rounded-lg border-0 px-2.5 text-left text-[13.5px]",
              !trash && "hover:bg-line-soft",
            )}
            style={{
              background: trash ? "#EEF1FC" : "transparent",
              color: trash ? "#3448C5" : "#344054",
            }}
          >
            <Icon name="trash-2" size={15} />
            <span className="flex-1 truncate">Recently deleted</span>
          </button>
        </nav>

        <div className="flex min-w-0 flex-[999_1_480px] flex-col gap-3">
          {trash ? (
            <RecentlyDeleted />
          ) : (
            <>
              <button
                type="button"
                onClick={() => setUpload({ file: null })}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  setUpload({ file: e.dataTransfer.files?.[0] ?? null });
                }}
                className="flex flex-col items-center gap-1.5 rounded-[10px] border-[1.5px] border-dashed bg-surface p-[22px] text-ink-3 hover:bg-primary-tint"
                style={{
                  borderColor: dragOver ? "#3448C5" : "#C7D0F5",
                  background: dragOver ? "#F5F7FE" : undefined,
                }}
              >
                <Icon name="cloud-upload" size={22} className="text-primary" />
                <span className="font-medium text-ink">
                  Drop files here or browse
                </span>
                <span className="text-[12.5px]">
                  {typesText} ·{" "}
                  {DEMO_MODE
                    ? "scanned before use"
                    : "stored privately · scanned for malware if this server has a scanner"}
                </span>
              </button>

              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-0 flex-[1_1_200px]">
                  <Icon
                    name="search"
                    size={15}
                    className="pointer-events-none absolute left-[11px] top-[11px] text-faint"
                  />
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    aria-label="Search files"
                    placeholder="Search files"
                    className="h-9 w-full rounded-lg border border-line-strong bg-surface pl-[34px] pr-3 text-sm placeholder:text-faint"
                  />
                </div>
                <select
                  aria-label="Classification"
                  value={cls}
                  onChange={(e) =>
                    setCls(e.target.value as FileClassification | "")
                  }
                  className="h-9 rounded-lg border border-line-strong bg-surface px-2 text-[13.5px]"
                >
                  <option value="">All classifications</option>
                  {CLS_KEYS.map((k) => (
                    <option key={k} value={k}>
                      {CLASSIFICATION[k].short}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Processing status"
                  value={status}
                  onChange={(e) => setStatus(e.target.value as FileStatus | "")}
                  className="h-9 rounded-lg border border-line-strong bg-surface px-2 text-[13.5px]"
                >
                  <option value="">All statuses</option>
                  {STATUS_KEYS.map((k) => (
                    <option key={k} value={k}>
                      {FILE_STATUS[k].label}
                    </option>
                  ))}
                </select>
                {filtersOn && (
                  <button
                    type="button"
                    onClick={clear}
                    className="h-9 border-0 bg-transparent px-2.5 text-[13.5px] font-medium text-primary hover:underline"
                  >
                    Clear
                  </button>
                )}
              </div>

              {list.isLoading ? (
                <div className="rounded-[10px] border border-line bg-surface p-4">
                  <Skeleton lines={6} />
                </div>
              ) : list.isError ? (
                <ErrorState
                  error={list.error}
                  onRetry={() => void list.refetch()}
                  retrying={list.isFetching}
                  title="We couldn't load your files"
                />
              ) : items.length === 0 ? (
                <div className="flex flex-col items-center gap-1.5 rounded-[10px] border border-line bg-surface px-6 py-10 text-center">
                  <Icon
                    name={filtersOn ? "search-x" : "folder-open"}
                    size={22}
                    className="text-muted"
                  />
                  <div className="font-semibold">
                    {filtersOn ? "No files match" : "No files here yet"}
                  </div>
                  <div className="max-w-[360px] text-[13.5px] text-muted">
                    {filtersOn
                      ? "Try a different search or clear the filters."
                      : "Add a site crawl export, a brand guide or a spreadsheet you want analysed."}
                  </div>
                  {filtersOn && (
                    <Button size="xs" className="mt-1" onClick={clear}>
                      Clear filters
                    </Button>
                  )}
                </div>
              ) : (
                <div
                  role="list"
                  aria-label="Files"
                  aria-busy={list.isFetching || undefined}
                  className="overflow-hidden rounded-[10px] border border-line bg-surface"
                >
                  {items.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      role="listitem"
                      onClick={() => setFileParam(f.id)}
                      className={cx(
                        "flex w-full flex-wrap items-center gap-3 border-0 border-b border-line-soft bg-surface text-left last:border-b-0 hover:bg-subtle",
                        rowPad,
                      )}
                    >
                      <span className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg bg-line-soft">
                        <Icon
                          name={fileIcon(f)}
                          size={16}
                          className="text-ink-3"
                        />
                      </span>
                      <span className="min-w-0 flex-[1_1_200px]">
                        <span className="block truncate font-medium">
                          {f.name}
                        </span>
                        <span className="block text-xs text-muted">
                          {f.type_label} · {fmtBytes(f.size_bytes)} ·{" "}
                          {fmtDateTime(f.uploaded_at)}
                          {(f.scan_status === "clean" || f.status === "quarantined") && (
                            <span className={f.status === "quarantined" ? "text-danger" : "text-success"}> · {scanText(f)}</span>
                          )}
                          {f.deletion_requested && (
                            <span className="text-danger">
                              {" "}
                              · Deletion requested
                            </span>
                          )}
                        </span>
                        {f.error_message && (
                          <span className="mt-0.5 flex items-center gap-1 text-xs text-danger">
                            <Icon name="circle-alert" size={12} />
                            <span className="truncate">{f.error_message}</span>
                          </span>
                        )}
                      </span>
                      <span className="flex flex-wrap items-center gap-3 max-[979px]:basis-full max-[979px]:pl-[46px]">
                        <ClassificationBadge cls={f.classification} />
                        <FileStatusBadge
                          status={f.status}
                          className="min-w-[96px]"
                        />
                      </span>
                    </button>
                  ))}
                </div>
              )}
              {list.data && list.data.total > items.length && (
                <p className="px-1 text-xs text-muted">
                  Showing {items.length} of {list.data.total} files. Narrow the
                  search to find the rest.
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {upload && (
        <UploadModal
          defaultCollection={collection}
          initialFile={upload.file}
          onClose={() => setUpload(null)}
        />
      )}
      {openId && (
        <FileDrawer
          key={openId}
          fileId={openId}
          fallback={openFallback}
          onClose={() => setFileParam(null)}
        />
      )}
    </div>
  );
}
