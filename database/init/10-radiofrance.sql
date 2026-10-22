-- First-start only: the image has already created the airflow database/admin.
\getenv application_password RF_DATABASE_PASSWORD
CREATE ROLE radiofrance LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS PASSWORD :'application_password';
CREATE DATABASE radiofrance OWNER radiofrance;
REVOKE ALL ON DATABASE radiofrance FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE radiofrance TO radiofrance;
REVOKE CONNECT ON DATABASE airflow FROM PUBLIC;
GRANT CONNECT ON DATABASE airflow TO airflow;
\connect radiofrance
BEGIN;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO radiofrance;
CREATE EXTENSION vector;
COMMIT;
