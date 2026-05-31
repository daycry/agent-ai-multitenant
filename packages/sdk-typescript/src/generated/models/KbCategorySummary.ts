/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Slim embed para `KnowledgeBaseResponse.category` — evita un
 * fetch extra del frontend cuando lista KBs.
 */
export type KbCategorySummary = {
    color: (string | null);
    id: string;
    is_builtin: boolean;
    name: string;
    slug: string;
};
