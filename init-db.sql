-- Enable required PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgvector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- Allow pgvector extension for public schema
ALTER EXTENSION pgvector SET SCHEMA public;

-- Create tenant schema (optional, for multi-tenancy)
CREATE SCHEMA IF NOT EXISTS tenants;

-- Grant permissions
GRANT USAGE ON SCHEMA public TO postgres;
GRANT CREATE ON SCHEMA public TO postgres;
