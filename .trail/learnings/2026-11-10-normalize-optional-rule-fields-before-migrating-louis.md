---
title: Normalize optional rule fields before migrating seeded customer defaults
author: Louis
date: 2026-11-10
branch: practice/customer-improvements
id: l-7f9e2aa9
scope: repo
status: active
src: agent
touches:
  - backend/app/seed.py
---

## Decided
When replacing seeded organization rules, compare their meaning after removing null optional fields, and migrate only the exact untouched default rule set. Customized customer rules must remain untouched.

## Tried and ruled out
Direct dictionary equality against the seed literals missed the existing Acme rules: Pydantic had serialized the optional max_total field as null for every unlimited rule. The migration reached head without errors but left the old rules in place.

## Watch out
A successful Alembic revision does not prove JSON settings changed. Verify the actual organization settings after upgrade and test both a database with old defaults and a fresh seed.

In the code: backend/alembic/versions/0007_classification_review.py normalizes null keys before comparing; backend/app/seed.py defines the current defaults. Check with docker compose exec -T db psql -U asklio -d asklio -Atc "select settings::text from organizations where slug='acme'".
