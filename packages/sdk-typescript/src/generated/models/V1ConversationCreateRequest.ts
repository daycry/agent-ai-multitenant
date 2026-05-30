/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChatMode } from './ChatMode';
/**
 * Minimal public body to start a conversation in a project.
 */
export type V1ConversationCreateRequest = {
    current_mode?: ChatMode;
    title?: (string | null);
};
