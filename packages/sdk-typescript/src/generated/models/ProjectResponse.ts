/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ProjectResponse = {
    budget_amount: (string | null);
    budget_currency: (string | null);
    budget_period: (string | null);
    budget_period_length_days: (number | null);
    budget_period_start_day: (number | null);
    created_at: string;
    deleted_at: (string | null);
    description: (string | null);
    human_approval_policy: (Record<string, any> | null);
    id: string;
    is_template: boolean;
    mcp_servers: Array<Record<string, any>>;
    name: string;
    paused_by_budget: boolean;
    rag_knowledge_bases: Array<Record<string, any>>;
    repository_config: (Record<string, any> | null);
    secrets_vault_id: (string | null);
    status: string;
    team_id: (string | null);
    tenant_id: string;
    updated_at: string;
    worker_config: Record<string, any>;
};
