"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge, Button, EmptyState, ErrorState, Icon, PageHeader, Skeleton } from "@/components/ui";
import { IntegrationDrawer } from "./IntegrationDrawer";
import { integrationBadge, isChecking } from "./status";

export function IntegrationsScreen() {
  const [selKey, setSelKey] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["integrations"],
    queryFn: () => api.listIntegrations(),
    // Poll while a WordPress connection is being verified by the tools worker.
    refetchInterval: (q) => (q.state.data?.some(isChecking) ? 2000 : false),
  });
  const items = list.data ?? [];
  const anyConnected = items.some((i) => i.status === "connected");
  const selected = selKey ? items.find((i) => i.key === selKey) ?? null : null;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Integrations"
        subtitle={`Tools the Agent can use, each with its own limited permissions.${anyConnected ? "" : " Nothing is connected yet."}`}
      />

      <div className="flex gap-2.5 rounded-[10px] border border-line bg-surface px-3.5 py-3 text-[13px] text-ink-3">
        <Icon name="key-round" size={15} className="mt-0.5 text-primary" />
        <span>
          No passwords or API keys are needed to read. WordPress is the first real integration: read-only by default, and optionally draft posts with a revocable
          WordPress Application Password (stored encrypted, never shown again). Others are listed as not built yet. Connecting a tool does not give permission to share
          every file with it.
        </span>
      </div>

      {list.isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="rounded-[10px] border border-line bg-surface p-[18px]">
              <Skeleton lines={4} />
            </div>
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 ? (
        <div className="rounded-[10px] border border-line bg-surface">
          <EmptyState icon="plug" title="No integrations available">
            Integrations you can connect will be listed here.
          </EmptyState>
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-3">
          {items.map((i) => (
            <article key={i.key} className="flex flex-col gap-3 rounded-[10px] border border-line bg-surface p-[18px]">
              <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[10px] bg-neutral-soft">
                  <Icon name={i.icon} size={19} className="text-ink-2" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{i.name}</div>
                  <div className="text-xs text-muted">{i.last_activity}</div>
                </div>
                <Badge meta={integrationBadge(i)} className="gap-[5px]" />
              </div>
              <p className="m-0 flex-1 text-[13.5px] text-ink-2 [text-wrap:pretty]">{i.description}</p>
              <div className="flex gap-1.5 text-[12.5px] text-muted">
                <Icon name="send" size={13} className="mt-[3px]" />
                <span>Data goes to: {i.destination}</span>
              </div>
              <Button size="sm" className="w-full" onClick={() => setSelKey(i.key)} aria-label={`Manage ${i.name}`}>
                Manage
              </Button>
            </article>
          ))}
        </div>
      )}

      {selected && <IntegrationDrawer integration={selected} onClose={() => setSelKey(null)} />}
    </div>
  );
}
