/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectStatus } from './ProjectStatus';
/**
 * Minimal public body to create a project.
 */
export type V1ProjectCreateRequest = {
    description?: (string | null);
    name: string;
    status?: ProjectStatus;
};
