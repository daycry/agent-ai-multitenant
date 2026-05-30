/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanStatus } from './PlanStatus';
/**
 * Minimal public body to create a (draft) plan in a project.
 *
 * The specification is left empty; the public API creates the plan
 * shell — filling the canonical-template spec stays a planning-chat /
 * interactive concern.
 */
export type V1PlanCreateRequest = {
    description?: (string | null);
    status?: PlanStatus;
    title: string;
};
