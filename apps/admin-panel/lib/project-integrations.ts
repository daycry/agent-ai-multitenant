/**
 * `project.integrations` (`task_mk_20`, MK-05): el módulo PURO del formulario
 * «Integraciones» del proyecto — forma ↔ payload y los problemas de formato que
 * la API rechazaría con un 422, redactados aquí para que el operador los vea
 * antes de pulsar Guardar.
 *
 * Espejo de `api_server/schemas/integrations.py`: mismas claves, mismos
 * patrones. Si el backend añade un proveedor, esto es lo que hay que ampliar.
 */

import { translate, type Lang, type MessageKey } from "@/lib/i18n";

export interface IntegrationsValue {
  jira?: { project_key: string; parent_issue_key?: string | null } | null;
  confluence?: { space_key: string; root_page_id?: string | null } | null;
}

export interface IntegrationsForm {
  jiraProjectKey: string;
  jiraParentIssueKey: string;
  confluenceSpaceKey: string;
  confluenceRootPageId: string;
}

// Los mismos patrones que `schemas/integrations.py`.
const JIRA_PROJECT_KEY = /^[A-Z][A-Z0-9_]{0,63}$/;
const JIRA_ISSUE_KEY = /^[A-Z][A-Z0-9_]{0,63}-[0-9]{1,10}$/;
const CONFLUENCE_SPACE_KEY = /^~?[A-Za-z0-9_]{1,255}$/;
const CONFLUENCE_PAGE_ID = /^[0-9]{1,20}$/;

export function toForm(value: IntegrationsValue | null | undefined): IntegrationsForm {
  return {
    jiraProjectKey: value?.jira?.project_key ?? "",
    jiraParentIssueKey: value?.jira?.parent_issue_key ?? "",
    confluenceSpaceKey: value?.confluence?.space_key ?? "",
    confluenceRootPageId: value?.confluence?.root_page_id ?? "",
  };
}

/**
 * El JSONB que se manda en `PUT /projects/{id}`: sólo los proveedores con clave
 * principal rellena. Un proveedor vacío desaparece (y `{}` borra las anclas).
 */
export function toPayload(form: IntegrationsForm): { integrations: IntegrationsValue } {
  const integrations: IntegrationsValue = {};
  const jiraKey = form.jiraProjectKey.trim();
  if (jiraKey) {
    const parent = form.jiraParentIssueKey.trim();
    integrations.jira = parent
      ? { project_key: jiraKey, parent_issue_key: parent }
      : { project_key: jiraKey };
  }
  const spaceKey = form.confluenceSpaceKey.trim();
  if (spaceKey) {
    const root = form.confluenceRootPageId.trim();
    integrations.confluence = root
      ? { space_key: spaceKey, root_page_id: root }
      : { space_key: spaceKey };
  }
  return { integrations };
}

/** Problemas de formato, redactados en el idioma del operador. Vacío = se puede guardar. */
export function integrationsProblems(form: IntegrationsForm, lang: Lang): string[] {
  const out: string[] = [];
  const t = (key: MessageKey<"projectIntegrations">) => translate(lang, "projectIntegrations", key);
  const jiraKey = form.jiraProjectKey.trim();
  const parent = form.jiraParentIssueKey.trim();
  const spaceKey = form.confluenceSpaceKey.trim();
  const root = form.confluenceRootPageId.trim();

  if (jiraKey && !JIRA_PROJECT_KEY.test(jiraKey)) out.push(t("problemJiraProjectKey"));
  if (parent && !jiraKey) out.push(t("problemJiraParentWithoutProject"));
  if (parent && !JIRA_ISSUE_KEY.test(parent)) out.push(t("problemJiraParentIssueKey"));
  if (spaceKey && !CONFLUENCE_SPACE_KEY.test(spaceKey)) out.push(t("problemConfluenceSpaceKey"));
  if (root && !spaceKey) out.push(t("problemConfluenceRootWithoutSpace"));
  if (root && !CONFLUENCE_PAGE_ID.test(root)) out.push(t("problemConfluenceRootPageId"));
  return out;
}
