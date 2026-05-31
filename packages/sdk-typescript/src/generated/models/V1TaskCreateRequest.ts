/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TaskPriority } from './TaskPriority';
import type { TaskStatus } from './TaskStatus';
/**
 * Minimal public body to create a task in a project.
 */
export type V1TaskCreateRequest = {
    description?: (string | null);
    priority?: TaskPriority;
    status?: TaskStatus;
    title: string;
};
