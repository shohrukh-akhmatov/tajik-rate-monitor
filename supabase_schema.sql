-- ====================================================================
-- Tajik Rate Monitor: Complete Idempotent Supabase Schema & RPC
-- Run this script in the Supabase SQL Editor (https://supabase.com/dashboard/project/iufslbdtryxspuwsfbqn/sql)
-- ====================================================================

-- 0. Shared Revision Sequence
CREATE SEQUENCE IF NOT EXISTS public.moneytj_revision_seq START WITH 1 INCREMENT BY 1;

-- 1. Helper / Auth Functions
CREATE OR REPLACE FUNCTION public.moneytj_is_admin()
RETURNS boolean
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO ''
AS $$
    SELECT coalesce((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', false);
$$;

CREATE OR REPLACE FUNCTION public.moneytj_current_revision()
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
    SELECT last_value FROM public.moneytj_revision_seq;
$$;

-- 2. Transfer Services Table
CREATE TABLE IF NOT EXISTS public.transfer_services (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    short_name text,
    logo_url text,
    website_url text,
    is_active boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0,
    service_type text NOT NULL DEFAULT 'other',
    source_country character(2) NOT NULL DEFAULT 'RU',
    destination_country character(2) NOT NULL DEFAULT 'TJ',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    revision bigint NOT NULL DEFAULT nextval('moneytj_revision_seq'::regclass),
    fee_source_currency numeric NOT NULL DEFAULT 0,
    logo_name text,
    is_russian_bank boolean NOT NULL DEFAULT false,
    referral_url text,
    is_pinned boolean NOT NULL DEFAULT false,
    pinned_offer_text text,
    shows_pinned_offer_card_cta boolean NOT NULL DEFAULT true
);

-- Ensure Core Transfer Services Exist
INSERT INTO public.transfer_services (id, slug, name, website_url, is_active, is_russian_bank)
VALUES
    ('10000000-0000-0000-0000-000000000001', 'sberbank', 'Сбербанк', 'https://www.sberbank.ru', true, true),
    ('10000000-0000-0000-0000-000000000002', 't-bank', 'Т-Банк', 'https://tbank.ru', true, true)
ON CONFLICT (slug) DO UPDATE
SET is_active = true,
    name = EXCLUDED.name,
    website_url = EXCLUDED.website_url,
    is_russian_bank = EXCLUDED.is_russian_bank;

-- 3. Rate Calculation Runs (Audit Log)
CREATE TABLE IF NOT EXISTS public.rate_calculation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at timestamptz NOT NULL DEFAULT now(),
    source_commit text,
    source_generated_at timestamptz,
    status text NOT NULL DEFAULT 'staged',
    anomaly_count integer NOT NULL DEFAULT 0,
    warning_sent boolean NOT NULL DEFAULT false,
    published_at timestamptz,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 4. Rate Calculation Staging Table
CREATE TABLE IF NOT EXISTS public.rate_calculation_staging (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES public.rate_calculation_runs(id) ON DELETE CASCADE,
    service_slug text NOT NULL,
    bank_code text NOT NULL,
    bank_name text,
    currency_code character(3) NOT NULL,
    base_rate numeric,
    base_source_bank_code text,
    base_source_kind text,
    coefficient numeric,
    raw_calculated_rate numeric,
    final_rate numeric,
    sample_source_amount numeric NOT NULL DEFAULT 1000,
    sample_target_amount numeric,
    status text NOT NULL DEFAULT 'ok',
    anomaly_code text,
    anomaly_message text,
    is_manual_override boolean NOT NULL DEFAULT false,
    manual_note text,
    source_observed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rate_calc_staging_run_id ON public.rate_calculation_staging(run_id);

-- 5. Production Exchange Rates Table (Mobile App & Web Clients)
CREATE TABLE IF NOT EXISTS public.exchange_rates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id uuid NOT NULL REFERENCES public.transfer_services(id),
    source_currency character(3) NOT NULL DEFAULT 'RUB',
    target_currency character(3) NOT NULL DEFAULT 'TJS',
    sample_source_amount numeric NOT NULL,
    sample_target_amount numeric NOT NULL,
    effective_rate numeric NOT NULL,
    fee_source_currency numeric NOT NULL DEFAULT 0,
    total_received numeric NOT NULL,
    rate_timestamp timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid,
    verification_status text NOT NULL DEFAULT 'unverified',
    source_type text NOT NULL DEFAULT 'remote_manual',
    source_note text,
    source_url text,
    admin_confirmed_at timestamptz,
    revision bigint NOT NULL DEFAULT nextval('moneytj_revision_seq'::regclass),
    is_current boolean NOT NULL DEFAULT true,
    destination_bank_name text,
    deleted_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_exchange_rates_lookup ON public.exchange_rates(service_id, source_currency, target_currency, is_current) WHERE deleted_at IS NULL;

-- 6. National Bank Rates Table (Official NBT FX)
CREATE TABLE IF NOT EXISTS public.national_bank_rates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    currency_code character(3) NOT NULL UNIQUE,
    buy_rate numeric NOT NULL,
    sell_rate numeric NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    revision bigint NOT NULL DEFAULT nextval('moneytj_revision_seq'::regclass)
);

-- 7. App Configuration Table (Card Rates & Mobile Settings)
CREATE TABLE IF NOT EXISTS public.app_configuration (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    minimum_app_version text,
    revision bigint NOT NULL DEFAULT nextval('moneytj_revision_seq'::regclass)
);

-- 8. Rescan State & Cooldown Lock Table
CREATE TABLE IF NOT EXISTS public.rate_monitor_rescan_state (
    id boolean PRIMARY KEY DEFAULT true,
    last_triggered_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 9. Rescan Lock Functions
CREATE OR REPLACE FUNCTION public.claim_rate_monitor_rescan(cooldown_seconds integer DEFAULT 300)
RETURNS TABLE(allowed boolean, retry_after integer, triggered_at timestamp with time zone)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_now timestamptz := now();
    v_last timestamptz;
    v_retry integer;
BEGIN
    INSERT INTO public.rate_monitor_rescan_state (id)
    VALUES (true)
    ON CONFLICT (id) DO NOTHING;

    SELECT last_triggered_at
    INTO v_last
    FROM public.rate_monitor_rescan_state
    WHERE id = true
    FOR UPDATE;

    IF v_last IS NULL OR v_now >= v_last + make_interval(secs => cooldown_seconds) THEN
        UPDATE public.rate_monitor_rescan_state
        SET last_triggered_at = v_now,
            updated_at = v_now
        WHERE id = true;

        RETURN QUERY SELECT true, 0, v_now;
        RETURN;
    END IF;

    v_retry := GREATEST(
        1,
        ceil(extract(epoch from ((v_last + make_interval(secs => cooldown_seconds)) - v_now)))::integer
    );

    RETURN QUERY SELECT false, v_retry, v_last;
END;
$$;

CREATE OR REPLACE FUNCTION public.release_rate_monitor_rescan_claim(claimed_at timestamp with time zone)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
BEGIN
    UPDATE public.rate_monitor_rescan_state
    SET last_triggered_at = NULL,
        updated_at = now()
    WHERE id = true AND last_triggered_at = claimed_at;
    RETURN FOUND;
END;
$$;

-- 10. Production Idempotent Publish RPC Function
CREATE OR REPLACE FUNCTION public.publish_rate_calculation_run(p_run_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_run public.rate_calculation_runs%rowtype;
    r record;
    v_service_id uuid;
    v_revision bigint;
    v_card_rates jsonb := '[]'::jsonb;
    v_count integer := 0;
    v_bank_name text;
    v_card_bank_name text;
    v_has_card_rates boolean := false;
    v_iso_timestamp text;
BEGIN
    SELECT * INTO v_run FROM public.rate_calculation_runs WHERE id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rate_calculation_run % not found', p_run_id;
    END IF;
    IF v_run.status <> 'staged' OR v_run.anomaly_count <> 0 THEN
        RAISE EXCEPTION 'run % is not publishable: status=%, anomalies=%', p_run_id, v_run.status, v_run.anomaly_count;
    END IF;

    -- Load current tajikistan_card_rates snapshot from app_configuration
    SELECT coalesce(value, '[]'::jsonb) INTO v_card_rates
    FROM public.app_configuration
    WHERE key = 'tajikistan_card_rates'
    FOR UPDATE;

    IF v_card_rates IS NULL THEN v_card_rates := '[]'::jsonb; END IF;

    FOR r IN SELECT * FROM public.rate_calculation_staging WHERE run_id = p_run_id ORDER BY id LOOP
        -- Format RFC 3339 / ISO 8601 Zulu timestamp for Swift MoneyTJJSONDecoder compatibility
        v_iso_timestamp := to_char(coalesce(r.source_observed_at, now()) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');

        -- 1. Process RUB transfer calculations for active Russian banks (T-Bank, Sberbank)
        IF r.currency_code = 'RUB' AND r.service_slug IN ('t-bank', 'sberbank') THEN
            SELECT id INTO v_service_id FROM public.transfer_services WHERE slug = r.service_slug AND is_active = true LIMIT 1;
            IF v_service_id IS NULL THEN
                RAISE EXCEPTION 'active transfer service % not found', r.service_slug;
            END IF;

            v_bank_name := CASE r.bank_code
                WHEN 'ibt' THEN 'IBT'
                WHEN 'activbank' THEN 'Активбанк'
                WHEN 'alif' THEN 'Алиф Банк'
                WHEN 'amonat' THEN 'Амонатбанк'
                WHEN 'amonatbank' THEN 'Амонатбанк'
                WHEN 'vasl' THEN 'Васл Банк'
                WHEN 'dc' THEN 'Душанбе Сити'
                WHEN 'dcity' THEN 'Душанбе Сити'
                WHEN 'oriyon' THEN 'Ориёнбанк'
                WHEN 'oriyonbank' THEN 'Ориёнбанк'
                WHEN 'spitamen' THEN 'Спитамен'
                WHEN 'humo' THEN 'Хумо'
                WHEN 'eskhata' THEN 'Эсхата Банк'
                ELSE coalesce(r.bank_name, r.bank_code)
            END;

            SELECT coalesce(max(revision), 0) + 1 INTO v_revision
            FROM public.exchange_rates
            WHERE service_id = v_service_id AND source_currency = 'RUB' AND target_currency = 'TJS' AND destination_bank_name = v_bank_name;

            UPDATE public.exchange_rates
            SET is_current = false
            WHERE service_id = v_service_id AND source_currency = 'RUB' AND target_currency = 'TJS' AND destination_bank_name = v_bank_name AND is_current = true AND deleted_at IS NULL;

            INSERT INTO public.exchange_rates (
                service_id,
                source_currency,
                target_currency,
                sample_source_amount,
                sample_target_amount,
                effective_rate,
                rate_timestamp,
                verification_status,
                source_type,
                source_note,
                source_url,
                revision,
                is_current,
                destination_bank_name
            ) VALUES (
                v_service_id,
                'RUB',
                'TJS',
                r.sample_source_amount,
                r.sample_target_amount,
                r.final_rate,
                coalesce(r.source_observed_at, now()),
                CASE WHEN r.status = 'ok' THEN 'verified' ELSE r.status END,
                'remote_verified',
                concat('base=', r.base_rate, '; source=', r.base_source_kind, '/', r.base_source_bank_code, '; coefficient=', r.coefficient),
                'https://shohrukh-akhmatov.github.io/tajik-rate-monitor/api/calculated.json',
                v_revision,
                true,
                v_bank_name
            );
            v_count := v_count + 1;

        -- 2. Process NBT reference rates (RUB, USD, EUR, CNY, KZT)
        ELSIF r.service_slug = 'nbt-reference' AND r.currency_code IN ('RUB', 'USD', 'EUR', 'CNY', 'KZT') THEN
            SELECT coalesce(max(revision), 0) + 1 INTO v_revision
            FROM public.national_bank_rates
            WHERE currency_code = r.currency_code;

            UPDATE public.national_bank_rates
            SET updated_at = coalesce(r.source_observed_at, now()),
                buy_rate = r.final_rate,
                sell_rate = r.final_rate,
                revision = v_revision
            WHERE currency_code = r.currency_code;

            IF NOT FOUND THEN
                INSERT INTO public.national_bank_rates (currency_code, buy_rate, sell_rate, updated_at, revision)
                VALUES (r.currency_code, r.final_rate, r.final_rate, coalesce(r.source_observed_at, now()), v_revision);
            END IF;
            v_count := v_count + 1;

        -- 3. Process Commercial Bank Card rates (USD, EUR, RUB) for "Rates in Tajikistan"
        ELSIF r.service_slug = 'bank-card' AND r.currency_code IN ('USD', 'EUR', 'RUB') THEN
            IF r.currency_code = 'USD' THEN
                v_card_bank_name := CASE r.bank_code
                    WHEN 'ibt' THEN 'Международный Банк Таджикистана (IBT)'
                    WHEN 'activbank' THEN 'Активбанк'
                    WHEN 'alif' THEN 'Алиф Банк'
                    WHEN 'amonat' THEN 'Амонатбанк'
                    WHEN 'amonatbank' THEN 'Амонатбанк'
                    WHEN 'oriyon' THEN 'Ориёнбанк'
                    WHEN 'oriyonbank' THEN 'Ориёнбанк'
                    WHEN 'spitamen' THEN 'Спитамен'
                    WHEN 'eskhata' THEN 'Эсхата'
                    ELSE coalesce(r.bank_name, r.bank_code)
                END;
            ELSIF r.currency_code = 'EUR' THEN
                v_card_bank_name := CASE r.bank_code
                    WHEN 'ibt' THEN 'IBT'
                    WHEN 'activbank' THEN 'Активбанк'
                    WHEN 'alif' THEN 'Алиф Банк'
                    WHEN 'amonat' THEN 'Амонатбанк'
                    WHEN 'amonatbank' THEN 'Амонатбанк'
                    WHEN 'oriyon' THEN 'Ориёнбанк'
                    WHEN 'oriyonbank' THEN 'Ориёнбанк'
                    WHEN 'spitamen' THEN 'Спитамен'
                    WHEN 'eskhata' THEN 'Эсхата'
                    ELSE coalesce(r.bank_name, r.bank_code)
                END;
            ELSIF r.currency_code = 'RUB' THEN
                v_card_bank_name := CASE r.bank_code
                    WHEN 'activbank' THEN 'Активбанк'
                    WHEN 'alif' THEN 'Алиф Банк'
                    WHEN 'amonat' THEN 'Амонатбанк'
                    WHEN 'amonatbank' THEN 'Амонатбанк'
                    WHEN 'oriyon' THEN 'Ориёнбанк'
                    WHEN 'oriyonbank' THEN 'Ориёнбанк'
                    WHEN 'spitamen' THEN 'Спитамен'
                    WHEN 'eskhata' THEN 'Эсхата, IBT'
                    WHEN 'ibt' THEN 'Эсхата, IBT'
                    ELSE coalesce(r.bank_name, r.bank_code)
                END;
            END IF;

            -- Replace any existing entry for this specific bank + currency
            SELECT coalesce(jsonb_agg(obj ORDER BY ord), '[]'::jsonb) INTO v_card_rates
            FROM (
                SELECT ord, obj FROM jsonb_array_elements(v_card_rates) WITH ORDINALITY q(obj, ord)
                WHERE NOT (upper(coalesce(obj->>'bank_name', '')) = upper(v_card_bank_name) AND upper(coalesce(obj->>'currency_code', '')) = upper(r.currency_code))
            ) s;

            v_card_rates := v_card_rates || jsonb_build_array(jsonb_build_object(
                'bank_name', v_card_bank_name,
                'currency_code', r.currency_code,
                'tjs_per_unit', r.final_rate,
                'updated_at', v_iso_timestamp,
                'source', 'github_pages_calculated'
            ));
            v_has_card_rates := true;
            v_count := v_count + 1;
        END IF;
    END LOOP;

    -- Always persist updated card rates if any were processed
    IF v_has_card_rates THEN
        UPDATE public.app_configuration
        SET value = v_card_rates,
            updated_at = now(),
            revision = coalesce(revision, 0) + 1
        WHERE key = 'tajikistan_card_rates';

        IF NOT FOUND THEN
            INSERT INTO public.app_configuration (key, value, updated_at, revision)
            VALUES ('tajikistan_card_rates', v_card_rates, now(), 1);
        END IF;
    END IF;

    -- Clean up any obsolete duplicate legacy bank names from exchange_rates
    UPDATE public.exchange_rates
    SET is_current = false
    WHERE is_current = true AND destination_bank_name = 'International Bank of Tajikistan';

    -- Ensure inactive services don't have lingering is_current = true rates
    UPDATE public.exchange_rates
    SET is_current = false
    WHERE is_current = true AND service_id IN (SELECT id FROM public.transfer_services WHERE is_active = false);

    UPDATE public.rate_calculation_runs
    SET status = 'published',
        published_at = now(),
        notes = coalesce(notes, '') || ' Published rows=' || v_count
    WHERE id = p_run_id;

    RETURN jsonb_build_object(
        'run_id', p_run_id,
        'status', 'published',
        'rows', v_count
    );
END;
$$;

-- 11. Row Level Security (RLS) Configuration
ALTER TABLE public.transfer_services ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exchange_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.national_bank_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_configuration ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_calculation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_calculation_staging ENABLE ROW LEVEL SECURITY;

-- Public Read Policies
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'transfer_services' AND policyname = 'Public read active services') THEN
        CREATE POLICY "Public read active services" ON public.transfer_services FOR SELECT TO anon, authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'exchange_rates' AND policyname = 'Public read rates') THEN
        CREATE POLICY "Public read rates" ON public.exchange_rates FOR SELECT TO anon, authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'national_bank_rates' AND policyname = 'Public read national bank rates') THEN
        CREATE POLICY "Public read national bank rates" ON public.national_bank_rates FOR SELECT TO anon, authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_configuration' AND policyname = 'Public read configuration') THEN
        CREATE POLICY "Public read configuration" ON public.app_configuration FOR SELECT TO anon, authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'rate_calculation_runs' AND policyname = 'authenticated can view rate calculation runs') THEN
        CREATE POLICY "authenticated can view rate calculation runs" ON public.rate_calculation_runs FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'rate_calculation_staging' AND policyname = 'authenticated can view rate calculation staging') THEN
        CREATE POLICY "authenticated can view rate calculation staging" ON public.rate_calculation_staging FOR SELECT TO authenticated USING (true);
    END IF;
END $$;
