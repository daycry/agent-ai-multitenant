/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationResponse } from '../models/ConversationResponse';
import type { KnowledgeBaseResponse } from '../models/KnowledgeBaseResponse';
import type { PlanResponse } from '../models/PlanResponse';
import type { ProjectResponse } from '../models/ProjectResponse';
import type { TaskResponse } from '../models/TaskResponse';
import type { V1ConversationCreateRequest } from '../models/V1ConversationCreateRequest';
import type { V1KnowledgeBaseCreateRequest } from '../models/V1KnowledgeBaseCreateRequest';
import type { V1PlanCreateRequest } from '../models/V1PlanCreateRequest';
import type { V1ProjectCreateRequest } from '../models/V1ProjectCreateRequest';
import type { V1TaskCreateRequest } from '../models/V1TaskCreateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class PublicApiV1Service {
    constructor(public readonly httpRequest: BaseHttpRequest) {}
    /**
     * V1 Get Conversation
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public v1GetConversationApiV1ConversationsConversationIdGet({
        conversationId,
        xApiVersion,
        xApiToken,
    }: {
        conversationId: string,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<ConversationResponse> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/conversations/{conversation_id}',
            path: {
                'conversation_id': conversationId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 List Kbs
     * @returns KnowledgeBaseResponse Successful Response
     * @throws ApiError
     */
    public v1ListKbsApiV1KbsGet({
        limit = 100,
        offset,
        xApiVersion,
        xApiToken,
    }: {
        /**
         * Max rows returned (1..500). Use a smaller value for typeahead/comboboxes; combine with `offset` to page.
         */
        limit?: number,
        /**
         * Number of leading rows to skip (for paging). Must be >= 0.
         */
        offset?: number,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<Array<KnowledgeBaseResponse>> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/kbs',
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Create Kb
     * @returns KnowledgeBaseResponse Successful Response
     * @throws ApiError
     */
    public v1CreateKbApiV1KbsPost({
        requestBody,
        xApiVersion,
        xApiToken,
    }: {
        requestBody: V1KnowledgeBaseCreateRequest,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<KnowledgeBaseResponse> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/api/v1/kbs',
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Get Kb
     * @returns KnowledgeBaseResponse Successful Response
     * @throws ApiError
     */
    public v1GetKbApiV1KbsKbIdGet({
        kbId,
        xApiVersion,
        xApiToken,
    }: {
        kbId: string,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<KnowledgeBaseResponse> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/kbs/{kb_id}',
            path: {
                'kb_id': kbId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Get Plan
     * @returns PlanResponse Successful Response
     * @throws ApiError
     */
    public v1GetPlanApiV1PlansPlanIdGet({
        planId,
        xApiVersion,
        xApiToken,
    }: {
        planId: string,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<PlanResponse> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/plans/{plan_id}',
            path: {
                'plan_id': planId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 List Projects
     * List the token tenant's projects (excludes templates + soft-deleted).
     * @returns ProjectResponse Successful Response
     * @throws ApiError
     */
    public v1ListProjectsApiV1ProjectsGet({
        limit = 100,
        offset,
        xApiVersion,
        xApiToken,
    }: {
        /**
         * Max rows returned (1..500). Use a smaller value for typeahead/comboboxes; combine with `offset` to page.
         */
        limit?: number,
        /**
         * Number of leading rows to skip (for paging). Must be >= 0.
         */
        offset?: number,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<Array<ProjectResponse>> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/projects',
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Create Project
     * @returns ProjectResponse Successful Response
     * @throws ApiError
     */
    public v1CreateProjectApiV1ProjectsPost({
        requestBody,
        xApiVersion,
        xApiToken,
    }: {
        requestBody: V1ProjectCreateRequest,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<ProjectResponse> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/api/v1/projects',
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Get Project
     * @returns ProjectResponse Successful Response
     * @throws ApiError
     */
    public v1GetProjectApiV1ProjectsProjectIdGet({
        projectId,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<ProjectResponse> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/projects/{project_id}',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 List Conversations
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public v1ListConversationsApiV1ProjectsProjectIdConversationsGet({
        projectId,
        limit = 100,
        offset,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        /**
         * Max rows returned (1..500). Use a smaller value for typeahead/comboboxes; combine with `offset` to page.
         */
        limit?: number,
        /**
         * Number of leading rows to skip (for paging). Must be >= 0.
         */
        offset?: number,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<Array<ConversationResponse>> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/projects/{project_id}/conversations',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Create Conversation
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public v1CreateConversationApiV1ProjectsProjectIdConversationsPost({
        projectId,
        requestBody,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        requestBody: V1ConversationCreateRequest,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<ConversationResponse> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/api/v1/projects/{project_id}/conversations',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 List Plans
     * @returns PlanResponse Successful Response
     * @throws ApiError
     */
    public v1ListPlansApiV1ProjectsProjectIdPlansGet({
        projectId,
        limit = 100,
        offset,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        /**
         * Max rows returned (1..500). Use a smaller value for typeahead/comboboxes; combine with `offset` to page.
         */
        limit?: number,
        /**
         * Number of leading rows to skip (for paging). Must be >= 0.
         */
        offset?: number,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<Array<PlanResponse>> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/projects/{project_id}/plans',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Create Plan
     * @returns PlanResponse Successful Response
     * @throws ApiError
     */
    public v1CreatePlanApiV1ProjectsProjectIdPlansPost({
        projectId,
        requestBody,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        requestBody: V1PlanCreateRequest,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<PlanResponse> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/api/v1/projects/{project_id}/plans',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 List Tasks
     * @returns TaskResponse Successful Response
     * @throws ApiError
     */
    public v1ListTasksApiV1ProjectsProjectIdTasksGet({
        projectId,
        limit = 100,
        offset,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        /**
         * Max rows returned (1..500). Use a smaller value for typeahead/comboboxes; combine with `offset` to page.
         */
        limit?: number,
        /**
         * Number of leading rows to skip (for paging). Must be >= 0.
         */
        offset?: number,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<Array<TaskResponse>> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/projects/{project_id}/tasks',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Create Task
     * @returns TaskResponse Successful Response
     * @throws ApiError
     */
    public v1CreateTaskApiV1ProjectsProjectIdTasksPost({
        projectId,
        requestBody,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        requestBody: V1TaskCreateRequest,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<TaskResponse> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/api/v1/projects/{project_id}/tasks',
            path: {
                'project_id': projectId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * V1 Get Task
     * @returns TaskResponse Successful Response
     * @throws ApiError
     */
    public v1GetTaskApiV1ProjectsProjectIdTasksTaskIdGet({
        projectId,
        taskId,
        xApiVersion,
        xApiToken,
    }: {
        projectId: string,
        taskId: string,
        xApiVersion?: (string | null),
        xApiToken?: (string | null),
    }): CancelablePromise<TaskResponse> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/api/v1/projects/{project_id}/tasks/{task_id}',
            path: {
                'project_id': projectId,
                'task_id': taskId,
            },
            headers: {
                'X-API-Version': xApiVersion,
                'X-API-Token': xApiToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
