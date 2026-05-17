# Backend / Database Plan

## Goal

Persist users, projects, runs, provider settings, and artifact metadata so the console survives refresh and separates user data.

## Minimum DB Tables

### users

```text
id UUID primary key
email text unique not null
username text unique not null
password_hash text not null
created_at timestamp
```

### projects

```text
id UUID primary key
owner_user_id UUID references users(id)
name text not null
description text
visibility text not null default 'private' -- private | public_demo
created_at timestamp
updated_at timestamp
```

### runs

```text
id text primary key
project_id UUID references projects(id)
owner_user_id UUID references users(id)
run_dir text not null
status text
current_stage text
current_agent text
public_url text
created_at timestamp
updated_at timestamp
```

### provider_credentials

```text
id UUID primary key
owner_user_id UUID references users(id)
provider text not null
secret_ciphertext text
secret_mask text
deleted_at timestamp nullable
created_at timestamp
```

### artifact_metadata

```text
id UUID primary key
run_id text references runs(id)
owner_user_id UUID references users(id)
path text not null
artifact_type text
owner_agent text
business_title text
visibility text default 'business'
created_at timestamp
```

## Auth

Implement demo auth:
- password hashing
- session cookie or Streamlit session state + DB user lookup
- logout

## API Key Handling

Minimum:
- save masked secret and encrypted secret if possible.
- never display full value.
- delete operation.

Preferred:
- Fernet encryption with `APP_SECRET_KEY`.
- If no key, store key only in session and warn.

## Public Demo Project

Create seed function:
- if no public demo exists, create one using configured run folder path or sample metadata.

Environment:

```text
PUBLIC_DEMO_RUN_DIR=
PUBLIC_DEMO_PROJECT_NAME=
```

## Migration

Preferred:
- Alembic migration.

Fallback:
- SQLAlchemy `create_all()` with idempotent initialization for hackathon demo.

## User Isolation Rules

Every query must filter by:
- `owner_user_id == current_user.id`
or
- `visibility == public_demo`

Never show private projects from other users.

## Run Directory Mapping

When a run starts:
- create `runs/<run-id>/`
- create DB `runs` row.
- attach project/user.

When loading historical run:
- read DB first;
- fallback to run folder if manually imported.
