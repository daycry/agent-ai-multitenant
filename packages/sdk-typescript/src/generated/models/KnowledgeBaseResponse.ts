/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { KbCategorySummary } from './KbCategorySummary';
export type KnowledgeBaseResponse = {
    category?: (KbCategorySummary | null);
    created_at: string;
    created_by: (string | null);
    description: (string | null);
    embedding_model_id: string;
    id: string;
    is_builtin?: boolean;
    name: string;
    tenant_id: string;
    updated_at: string;
};
