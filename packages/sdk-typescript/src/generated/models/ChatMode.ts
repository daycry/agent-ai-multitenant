/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Built-in chat modes (spec §8.2). ``custom`` is the escape hatch for
 * tenant-defined modes; the human-readable label lives in
 * ``Conversation.custom_mode_name``.
 */
export enum ChatMode {
    PLANNING = 'planning',
    DISCUSSION = 'discussion',
    EXECUTION = 'execution',
    CUSTOM = 'custom',
}
