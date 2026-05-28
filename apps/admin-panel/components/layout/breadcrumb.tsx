"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FolderKanban, Home } from "lucide-react";

import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";

export interface BreadcrumbItem {
  label: React.ReactNode;
  href?: string;
  icon?: React.ReactNode;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
  className?: string;
}

export function Breadcrumb({ items, className }: BreadcrumbProps) {
  if (items.length === 0) return null;
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        "bg-muted/50 mb-8 flex items-center rounded-md border px-3 py-2 text-sm",
        className,
      )}
      data-testid="breadcrumb"
    >
      <ol className="flex flex-wrap items-center gap-1">
        {items.map((item, idx) => {
          const isLast = idx === items.length - 1;
          const content = (
            <span className="inline-flex items-center gap-1.5">
              {item.icon}
              <span>{item.label}</span>
            </span>
          );
          return (
            <li key={idx} className="flex items-center gap-1">
              {item.href && !isLast ? (
                <Link
                  href={item.href}
                  className={cn(
                    "text-foreground hover:bg-background hover:text-primary",
                    "rounded px-2 py-1 font-medium transition-colors",
                  )}
                  data-testid={`breadcrumb-link-${idx}`}
                >
                  {content}
                </Link>
              ) : (
                <span
                  className={cn(
                    "px-2 py-1",
                    isLast ? "text-foreground font-semibold" : "text-muted-foreground",
                  )}
                  data-testid={`breadcrumb-item-${idx}`}
                  aria-current={isLast ? "page" : undefined}
                >
                  {content}
                </span>
              )}
              {!isLast && (
                <ChevronRight className="text-muted-foreground/60 h-4 w-4" aria-hidden="true" />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

interface ProjectChild {
  id: string;
  name: string;
}

interface ProjectBreadcrumbProps {
  projectId: string;
  current: string;
  className?: string;
}

export function ProjectBreadcrumb({ projectId, current, className }: ProjectBreadcrumbProps) {
  const { data } = useQuery<ProjectChild>({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<ProjectChild>(`/projects/${projectId}`),
    enabled: !!projectId,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
  return (
    <Breadcrumb
      className={className}
      items={[
        {
          label: "Proyectos",
          href: "/admin/projects",
          icon: <Home className="h-3.5 w-3.5" />,
        },
        {
          label: data?.name ?? projectId.slice(0, 8),
          href: `/admin/projects/${projectId}`,
          icon: <FolderKanban className="h-3.5 w-3.5" />,
        },
        { label: current },
      ]}
    />
  );
}
