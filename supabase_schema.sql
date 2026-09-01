-- ====================================================================
-- Tajik Rate Monitor: Complete Idempotent Supabase Schema & RPC
-- Run this script in the Supabase SQL Editor (https://supabase.com/dashboard/project/iufslbdtryxspuwsfbqn/sql)
-- ====================================================================

-- 1. Transfer Services Table
CREATE TABLE IF NOT EXISTS public.transfer_services (
    slug text PRIMARY KEY,
    name text NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT now()
);

-- Seed/activate required transfer services
INSERT INTO public.transfer_services (slug, name, is_active)
VALUES 
    ('t-bank', 'Т-Банк (Тинькофф)', true),
    ('sberbank', 'Сбербанк', true),
    ('nbt-reference', 'НБТ Официальный курс', true),
    ('bank-card', 'Курсы по картам банков', true)
ON CONFLICT (slug) DO UPDATE 
SET is_active = true, name = EXCLUDED.name;

-- 2. Audit Runs Table
CREATE TABLE IF NOT EXISTS public.rate_calculation_runs (
    id uuid PRIMARY KEY,
    generated_at timestamptz NOT NULL,
    source_commit text,
    status text NOT NULL,
    anomaly_count integer DEFAULT 0,
    warning_sent boolean DEFAULT false,
    notes text,
    created_at timestamptz DEFAULT now()
);

-- 3. Staging Table
CREATE TABLE IF NOT EXISTS public.rate_calculation_staging (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid REFERENCES public.rate_calculation_runs(id) ON DELETE CASCADE,
    service_slug text NOT NULL,
    bank_code text NOT NULL,
    bank_name text,
    currency_code text NOT NULL,
    base_rate numeric,
    base_source_bank_code text,
    base_source_kind text,
    coefficient numeric,
    raw_calculated_rate numeric,
    final_rate numeric NOT NULL,
    sample_source_amount numeric DEFAULT 1000,
    sample_target_amount numeric,
    status text DEFAULT 'ok',
    anomaly_code text,
    anomaly_message text,
    is_manual_override boolean DEFAULT false,
    manual_note text,
    source_observed_at timestamptz,
    created_at timestamptz DEFAULT now()
);

-- 4. Production Rates Table (Reads from mobile app / dashboard)
CREATE TABLE IF NOT EXISTS public.rates (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service_slug text NOT NULL,
    bank_code text NOT NULL,
    bank_name text,
    currency_code text NOT NULL,
    base_rate numeric,
    base_source_bank_code text,
    base_source_kind text,
    coefficient numeric,
    final_rate numeric NOT NULL,
    sample_source_amount numeric DEFAULT 1000,
    sample_target_amount numeric,
    status text DEFAULT 'ok',
    source_observed_at timestamptz,
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT rates_service_bank_currency_unique UNIQUE (service_slug, bank_code, currency_code)
);

-- 5. Idempotent Publish RPC Function
CREATE OR REPLACE FUNCTION public.publish_rate_calculation_run(p_run_id uuid)
RETURNS json
LANGUAGE plpgsql
SECURITY DEFINER
AS 
DECLARE
    v_anomaly_count integer;
    v_published_count integer := 0;
BEGIN
    -- Block publishing if unresolved anomalies exist
    SELECT COUNT(*) INTO v_anomaly_count
    FROM public.rate_calculation_staging
    WHERE run_id = p_run_id AND status = 'anomaly';

    IF v_anomaly_count > 0 THEN
        RAISE EXCEPTION 'Cannot publish run %: % unresolved anomalies found in staging.', p_run_id, v_anomaly_count;
    END IF;

    -- Upsert staged rates into production rates table without duplicates
    INSERT INTO public.rates (
        service_slug,
        bank_code,
        bank_name,
        currency_code,
        base_rate,
        base_source_bank_code,
        base_source_kind,
        coefficient,
        final_rate,
        sample_source_amount,
        sample_target_amount,
        status,
        source_observed_at,
        updated_at
    )
    SELECT
        s.service_slug,
        s.bank_code,
        s.bank_name,
        s.currency_code,
        s.base_rate,
        s.base_source_bank_code,
        s.base_source_kind,
        s.coefficient,
        s.final_rate,
        s.sample_source_amount,
        s.sample_target_amount,
        s.status,
        s.source_observed_at,
        now()
    FROM public.rate_calculation_staging s
    WHERE s.run_id = p_run_id
    ON CONFLICT (service_slug, bank_code, currency_code) DO UPDATE SET
        bank_name = EXCLUDED.bank_name,
        base_rate = EXCLUDED.base_rate,
        base_source_bank_code = EXCLUDED.base_source_bank_code,
        base_source_kind = EXCLUDED.base_source_kind,
        coefficient = EXCLUDED.coefficient,
        final_rate = EXCLUDED.final_rate,
        sample_source_amount = EXCLUDED.sample_source_amount,
        sample_target_amount = EXCLUDED.sample_target_amount,
        status = EXCLUDED.status,
        source_observed_at = EXCLUDED.source_observed_at,
        updated_at = now();

    GET DIAGNOSTICS v_published_count = ROW_COUNT;

    -- Mark run as published
    UPDATE public.rate_calculation_runs
    SET status = 'published'
    WHERE id = p_run_id;

    RETURN json_build_object(
        'success', true,
        'run_id', p_run_id,
        'published_rows', v_published_count
    );
END;
;
