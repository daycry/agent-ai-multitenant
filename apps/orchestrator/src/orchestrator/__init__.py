"""Orchestrator service — Plan 02.

Consumes domain events from a Redis Stream (`events:tasks`) and drives
task assignment. task_02_01 ships the service skeleton + the stream
consumer; assignment policies and the DAG recompute land in
task_02_02..04.
"""
