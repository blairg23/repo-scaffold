---
name: Epic
about: Track a multi-ticket initiative
title: ""
labels: ["epic", "needs-triage"]
assignees: []
---

## 🧾 Title
Task board -- core CRUD and Kanban lanes

## 🧠 Summary
Users need to create, assign, and move tasks across status lanes on a shared Kanban board.
This epic covers the full task lifecycle from creation to completion, including the board UI,
task detail view, assignment, and status transitions. It is the primary value driver for MVP.

## 📦 Scope
- Task creation form (title, description, assignee, due date)
- Kanban board with Todo / In Progress / Done lanes
- Drag-and-drop status transitions
- Task detail modal with comment thread
- REST API endpoints: GET /tasks, POST /tasks, PATCH /tasks/:id, DELETE /tasks/:id
- Assignee email notification on task creation

## 🚫 Out of Scope
- File attachments (separate epic)
- Recurring tasks
- Time tracking
- Task labels or tags

## ✅ Acceptance Criteria
- [ ] Team member can create a task and see it appear on the board immediately
- [ ] Team lead can assign a task to any team member
- [ ] Dragging a task card changes its status in the DB
- [ ] Task detail shows full history of status changes and comments
- [ ] Assignee receives an email when a task is assigned to them

## 🗂️ Milestones / Phases
- [ ] Phase 1: API endpoints + DB schema
- [ ] Phase 2: Board UI + drag-and-drop
- [ ] Phase 3: Task detail + comments
- [ ] Phase 4: Email notifications

## 🧪 Testing / Validation
- [ ] API: pytest covers create, read, update, delete, auth guard
- [ ] UI: manual smoke test of board load, drag-and-drop, and task detail on Chrome + mobile Safari
- [ ] Email: verify digest and assignment notifications arrive in staging

## 🔗 Child Tickets
- [ ] #4 -- Create task API endpoint
- [ ] #5 -- Board UI with Kanban lanes
- [ ] #6 -- Task detail modal and comment thread
- [ ] #7 -- Assignee email notification

## 🧰 Notes / Links
- See SPEC.md ## Flows for the create-and-assign sequence diagram
- Drag-and-drop: use dnd-kit (MIT, no jQuery)

## ✅ Addendum: Bugs Found/Fixed

## ⏳ Addendum: Pending
