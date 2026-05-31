/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TaskResponse = {
    acceptance_criteria: Array<any>;
    assigned_agent_id: (string | null);
    completed_at: (string | null);
    created_at: string;
    depends_on: Array<string>;
    description: (string | null);
    estimated_complexity: (string | null);
    id: string;
    inputs: Record<string, any>;
    max_retries: number;
    plan_id: (string | null);
    priority: string;
    project_id: string;
    retry_count: number;
    reviewer_agent_id: (string | null);
    started_at: (string | null);
    status: string;
    tenant_id: string;
    title: string;
    updated_at: string;
};
