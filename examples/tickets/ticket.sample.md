---
name: Ticket
about: Describe one implementation task
title: ""
labels: ["needs-triage"]
assignees: []
---

## 🧾 Title
Create task API endpoint (POST /tasks)

## 🧠 Summary
The board UI needs a backend endpoint to persist new tasks. This ticket implements
POST /tasks with input validation, DB insert, and the assignee notification trigger.
It is the first API ticket in the task board epic and unblocks the board UI ticket.

## 📦 Scope
- `POST /tasks` endpoint in `src/taskflow/routes/tasks.py`
- Request body: `title` (required), `description`, `assignee_id`, `due_date`
- Validates JWT, extracts team_id from token claims
- Inserts task row into Supabase via supabase-py
- Returns 201 with the created task object
- Enqueues assignee notification email (fire-and-forget, does not block response)

## 🚫 Out of Scope
- File attachment upload (separate ticket)
- GET /tasks or PATCH /tasks (separate tickets)
- Frontend form (separate ticket)

## ✅ Acceptance Criteria
- [ ] `POST /tasks` with valid JWT and required fields returns 201 + task object
- [ ] Missing `title` returns 422 with field-level error
- [ ] Invalid or expired JWT returns 401
- [ ] Task row appears in DB with correct team_id, assignee_id, status="todo"
- [ ] Assignee notification is enqueued (verify via mock in tests)

## 🧪 Testing / Validation
- [ ] `pytest tests/test_tasks.py` passes (create success, missing title, bad auth)
- [ ] Manual: POST via curl with valid staging JWT -- verify row in Supabase dashboard

## 🧰 Implementation Notes
- Use `supabase.table("tasks").insert({...}).execute()` pattern
- Notification: call `notify_assignee.delay(task_id)` (Celery task, already wired)
- Keep route thin -- business logic in `src/taskflow/services/task_service.py`

## 🧰 Notes / Links
- Epic: #3
- SPEC.md ## Flows -- "Create and assign a task" sequence diagram
- Supabase Python docs: https://supabase.com/docs/reference/python

## ✅ Addendum: Bugs Found/Fixed

## ⏳ Addendum: Pending
