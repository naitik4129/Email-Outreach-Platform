-- Lead profile fields. Forward migration per CLAUDE.md/AGENTS.md.
--
-- Adds twelve optional profile/company columns to public.leads so campaigns can
-- personalize on more than name/company/title:
--   person:  phone, department, experience_years, linkedin_url, website,
--            city, state, country
--   company: company_website, company_industry, company_founded_year,
--            company_linkedin_url
--
-- Company data is deliberately flat on the lead (there is no company entity in
-- this schema); the existing `company` column is unchanged and `title` remains
-- the job title column.
--
-- Every column is nullable with no default, so existing rows simply read NULL and
-- ADD COLUMN is a metadata-only change. The application layer (backend
-- normalization) is the authority on formats; the CHECKs below are the
-- database backstop for length, numeric range and URL scheme.
--
-- Also required by the existing design, not optional:
--   * app_lead_contact_revision() lists the columns whose change invalidates
--     captured audience snapshots. Every new column is a merge variable, so all
--     of them join that tuple.
--   * app_api / app_worker_general write leads through explicit column-level
--     grants; new columns need their own INSERT/UPDATE grants.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.leads') IS NULL
       OR pg_catalog.to_regprocedure('public.app_lead_contact_revision()') IS NULL THEN
        RAISE EXCEPTION '0021 requires 0002_contacts_content.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.leads'::pg_catalog.regclass
          AND attname = 'phone'
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION '0021 already applied: public.leads.phone exists';
    END IF;
END;
$preflight$;

ALTER TABLE public.leads
    ADD COLUMN phone text,
    ADD COLUMN department text,
    ADD COLUMN experience_years smallint,
    ADD COLUMN linkedin_url text,
    ADD COLUMN website text,
    ADD COLUMN city text,
    ADD COLUMN state text,
    ADD COLUMN country text,
    ADD COLUMN company_website text,
    ADD COLUMN company_industry text,
    ADD COLUMN company_founded_year smallint,
    ADD COLUMN company_linkedin_url text;

ALTER TABLE public.leads
    ADD CONSTRAINT leads_phone_check CHECK (
        phone IS NULL OR pg_catalog.char_length(phone) <= 32
    ),
    ADD CONSTRAINT leads_department_check CHECK (
        department IS NULL OR pg_catalog.char_length(department) <= 200
    ),
    ADD CONSTRAINT leads_experience_years_check CHECK (
        experience_years IS NULL OR experience_years BETWEEN 0 AND 80
    ),
    ADD CONSTRAINT leads_linkedin_url_check CHECK (
        linkedin_url IS NULL OR (
            pg_catalog.char_length(linkedin_url) <= 500
            AND linkedin_url ~* '^https?://'
            AND linkedin_url !~ '[[:space:]]'
        )
    ),
    ADD CONSTRAINT leads_website_check CHECK (
        website IS NULL OR (
            pg_catalog.char_length(website) <= 500
            AND website ~* '^https?://'
            AND website !~ '[[:space:]]'
        )
    ),
    ADD CONSTRAINT leads_city_check CHECK (
        city IS NULL OR pg_catalog.char_length(city) <= 200
    ),
    ADD CONSTRAINT leads_state_check CHECK (
        state IS NULL OR pg_catalog.char_length(state) <= 200
    ),
    ADD CONSTRAINT leads_country_check CHECK (
        country IS NULL OR pg_catalog.char_length(country) <= 200
    ),
    ADD CONSTRAINT leads_company_website_check CHECK (
        company_website IS NULL OR (
            pg_catalog.char_length(company_website) <= 500
            AND company_website ~* '^https?://'
            AND company_website !~ '[[:space:]]'
        )
    ),
    ADD CONSTRAINT leads_company_industry_check CHECK (
        company_industry IS NULL OR pg_catalog.char_length(company_industry) <= 200
    ),
    ADD CONSTRAINT leads_company_founded_year_check CHECK (
        company_founded_year IS NULL OR company_founded_year BETWEEN 1600 AND 2100
    ),
    ADD CONSTRAINT leads_company_linkedin_url_check CHECK (
        company_linkedin_url IS NULL OR (
            pg_catalog.char_length(company_linkedin_url) <= 500
            AND company_linkedin_url ~* '^https?://'
            AND company_linkedin_url !~ '[[:space:]]'
        )
    );

COMMENT ON COLUMN public.leads.website IS
    'The person''s own website or profile page. The company''s site is company_website.';
COMMENT ON COLUMN public.leads.experience_years IS
    'Years of professional experience as a whole number (0-80).';

-- Same body as 0002_contacts_content.sql with the twelve new columns appended to
-- both tuples. CREATE OR REPLACE preserves the function's owner and the REVOKE
-- from PUBLIC/anon/authenticated/service_role; the trigger definition is unchanged.
CREATE OR REPLACE FUNCTION public.app_lead_contact_revision() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.original_address, NEW.canonical_address, NEW.normalization_version, NEW.first_name,
        NEW.last_name, NEW.company, NEW.title, NEW.custom_fields, NEW.status, NEW.validation_status,
        NEW.phone, NEW.department, NEW.experience_years, NEW.linkedin_url, NEW.website,
        NEW.city, NEW.state, NEW.country, NEW.company_website, NEW.company_industry,
        NEW.company_founded_year, NEW.company_linkedin_url)
       IS DISTINCT FROM
       (OLD.original_address, OLD.canonical_address, OLD.normalization_version, OLD.first_name,
        OLD.last_name, OLD.company, OLD.title, OLD.custom_fields, OLD.status, OLD.validation_status,
        OLD.phone, OLD.department, OLD.experience_years, OLD.linkedin_url, OLD.website,
        OLD.city, OLD.state, OLD.country, OLD.company_website, OLD.company_industry,
        OLD.company_founded_year, OLD.company_linkedin_url) THEN
        NEW.contact_revision := OLD.contact_revision + 1;
    ELSE NEW.contact_revision := OLD.contact_revision;
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_lead_contact_revision() FROM PUBLIC, anon, authenticated, service_role;

-- Column-level, additive grants; RLS policies on leads are table-level and unchanged.
GRANT INSERT (phone, department, experience_years, linkedin_url, website, city, state, country, company_website, company_industry, company_founded_year, company_linkedin_url) ON public.leads TO app_api;
GRANT UPDATE (phone, department, experience_years, linkedin_url, website, city, state, country, company_website, company_industry, company_founded_year, company_linkedin_url) ON public.leads TO app_api;
GRANT INSERT (phone, department, experience_years, linkedin_url, website, city, state, country, company_website, company_industry, company_founded_year, company_linkedin_url) ON public.leads TO app_worker_general;
GRANT UPDATE (phone, department, experience_years, linkedin_url, website, city, state, country, company_website, company_industry, company_founded_year, company_linkedin_url) ON public.leads TO app_worker_general;

COMMIT;
