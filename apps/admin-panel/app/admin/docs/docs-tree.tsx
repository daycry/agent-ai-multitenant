"use client";

/**
 * DocsTree — recursive folder→file tree for one project's `/docs` (Plan 07
 * task_07_11). Pure presentational: it gets a fetched {@link DocTree} and
 * renders collapsible folders down to clickable `.md` files. Selecting a file
 * calls `onSelect(projectId, relpath)`; the active file is highlighted by
 * matching `selectedPath`.
 *
 * Tree-rendering only — fetching, project scoping and the empty/loading/error
 * states live in {@link DocsSidebar}.
 */

import { useState } from "react";
import { ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";

import { cn } from "@/lib/utils";
import type { DocTree, DocTreeFile, DocTreeFolder } from "@/lib/docs-api";

interface DocsTreeProps {
  projectId: string;
  tree: DocTree;
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelect: (projectId: string, relpath: string) => void;
}

export function DocsTree({
  projectId,
  tree,
  selectedProjectId,
  selectedPath,
  onSelect,
}: DocsTreeProps) {
  const isEmpty = tree.folders.length === 0 && tree.files.length === 0;
  if (isEmpty) {
    return (
      <p className="text-muted-foreground px-2 py-1 text-xs italic" data-testid="docs-tree-empty">
        Sin documentos en este proyecto.
      </p>
    );
  }

  return (
    <ul className="space-y-0.5" data-testid={`docs-tree-${projectId}`}>
      {tree.folders.map((folder) => (
        <li key={folder.relpath}>
          <FolderNode
            projectId={projectId}
            folder={folder}
            depth={0}
            selectedProjectId={selectedProjectId}
            selectedPath={selectedPath}
            onSelect={onSelect}
          />
        </li>
      ))}
      {tree.files.map((file) => (
        <li key={file.relpath}>
          <FileNode
            projectId={projectId}
            file={file}
            depth={0}
            active={selectedProjectId === projectId && selectedPath === file.relpath}
            onSelect={onSelect}
          />
        </li>
      ))}
    </ul>
  );
}

// Step in `rem` per nesting level so deep folders read as a clear hierarchy.
const INDENT_REM = 0.85;

function FolderNode({
  projectId,
  folder,
  depth,
  selectedProjectId,
  selectedPath,
  onSelect,
}: {
  projectId: string;
  folder: DocTreeFolder;
  depth: number;
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelect: (projectId: string, relpath: string) => void;
}) {
  // Open the folder by default when it (transitively) contains the selected
  // file, so deep-links land with the path already revealed.
  const [open, setOpen] = useState(
    () =>
      selectedProjectId === projectId &&
      selectedPath !== null &&
      selectedPath.startsWith(`${folder.relpath}/`),
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "text-sidebar-muted-foreground hover:bg-sidebar-border hover:text-sidebar-foreground",
          "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm transition-colors",
        )}
        style={{ paddingLeft: `${0.5 + depth * INDENT_REM}rem` }}
        aria-expanded={open}
        data-testid={`docs-folder-${projectId}-${folder.relpath}`}
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
        {open ? (
          <FolderOpen className="h-4 w-4 shrink-0" aria-hidden="true" />
        ) : (
          <Folder className="h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <span className="truncate">{folder.name}</span>
      </button>
      {open && (
        <ul className="space-y-0.5">
          {folder.folders.map((child) => (
            <li key={child.relpath}>
              <FolderNode
                projectId={projectId}
                folder={child}
                depth={depth + 1}
                selectedProjectId={selectedProjectId}
                selectedPath={selectedPath}
                onSelect={onSelect}
              />
            </li>
          ))}
          {folder.files.map((file) => (
            <li key={file.relpath}>
              <FileNode
                projectId={projectId}
                file={file}
                depth={depth + 1}
                active={selectedProjectId === projectId && selectedPath === file.relpath}
                onSelect={onSelect}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function FileNode({
  projectId,
  file,
  depth,
  active,
  onSelect,
}: {
  projectId: string;
  file: DocTreeFile;
  depth: number;
  active: boolean;
  onSelect: (projectId: string, relpath: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(projectId, file.relpath)}
      className={cn(
        "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm transition-colors",
        active
          ? "bg-[hsl(var(--sidebar-active-bg))] text-sidebar-active font-medium"
          : "text-sidebar-muted-foreground hover:bg-sidebar-border hover:text-sidebar-foreground",
      )}
      // +1.25rem aligns the file label with the folder label (past the chevron).
      style={{ paddingLeft: `${0.5 + depth * INDENT_REM + 1.25}rem` }}
      aria-current={active ? "true" : undefined}
      data-testid={`docs-file-${projectId}-${file.relpath}`}
    >
      <FileText className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="truncate">{file.name}</span>
    </button>
  );
}
