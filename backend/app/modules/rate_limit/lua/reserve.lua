-- Atomic multi-scope capacity reservation. See docs/architecture/RATE_LIMITING.md.
--
-- KEYS[1] = generation key
-- KEYS[2] = status key
-- KEYS[3..] = one bucket key per scope, in the same order as the `scopes`
--             array in ARGV[5]. Spacing keys and the reservation record key
--             are derived inside this script (bucket_key .. ':sp', and
--             'rl:res:' .. reservation_id) rather than passed as separate
--             KEYS entries -- safe because RATE_LIMITING.md specifies a
--             single primary/shard deployment, not a Redis Cluster one
--             ("Proposed initial limiter uses one primary/shard for the
--             shared multi-scope keys"); see limiter.py's module docstring
--             for the cluster-compatibility caveat if that ever changes.
--
-- ARGV[1] = now_ms
-- ARGV[2] = expected_generation ("" to skip the check -- controller use only)
-- ARGV[3] = reservation_id
-- ARGV[4] = reservation_ttl_seconds
-- ARGV[5] = scopes_json: JSON array of
--   {kind, scope_key, unit, window_seconds, limit_value, cooldown_seconds,
--    min_spacing_seconds, mode ("TOKEN_BUCKET"|"ROLLING_WINDOW"), quantity}
--
-- Returns a JSON-encoded result table:
--   success: {ok=true, reservation_id, generation, granted_at_ms}
--   failure: {ok=false, reason, ...}
--
-- All-or-nothing: every scope is *evaluated* (read-only, except for the
-- always-safe ZREMRANGEBYSCORE garbage-collection prune) before any scope's
-- counters are *mutated*. If any scope would be denied, the script returns
-- immediately with no mutation of any bucket/spacing key, so a denied
-- reservation can never partially consume capacity.

local now_ms = tonumber(ARGV[1])
local expected_generation = ARGV[2]
local reservation_id = ARGV[3]
local reservation_ttl_seconds = tonumber(ARGV[4])
local scopes = cjson.decode(ARGV[5])

local function fail(reason, extra)
    local result = {ok = false, reason = reason}
    if extra then
        for k, v in pairs(extra) do
            result[k] = v
        end
    end
    return cjson.encode(result)
end

-- 1. Generation / readiness gate. A missing generation or status key is
--    never treated as "unused full budget" -- absence fails closed exactly
--    like an explicit mismatch or RECOVERING status.
local current_generation = redis.call('GET', KEYS[1])
if expected_generation ~= '' then
    if (not current_generation) or current_generation ~= expected_generation then
        return fail('GENERATION_MISMATCH', {current_generation = current_generation or false})
    end
end

local status = redis.call('GET', KEYS[2])
if status ~= 'READY' then
    return fail('NOT_READY', {status = status or false})
end

-- 2. Evaluate every scope WITHOUT mutating counters yet.
local evaluations = {}
for i, scope in ipairs(scopes) do
    local bkey = KEYS[2 + i]
    local skey = bkey .. ':sp'
    local granted = true
    local wait_ms = 0

    -- Spacing / cooldown floor applies regardless of bucket mode.
    local min_wait_seconds = math.max(scope.cooldown_seconds or 0, scope.min_spacing_seconds or 0)
    if min_wait_seconds > 0 then
        local last_ms = tonumber(redis.call('GET', skey))
        if last_ms then
            local eligible_at = last_ms + (min_wait_seconds * 1000)
            if now_ms < eligible_at then
                granted = false
                wait_ms = eligible_at - now_ms
            end
        end
    end

    if granted then
        if scope.mode == 'TOKEN_BUCKET' then
            local state = redis.call('HMGET', bkey, 'tokens', 'ts')
            local tokens = tonumber(state[1])
            local last_ts = tonumber(state[2])
            if (not tokens) or (not last_ts) then
                tokens = scope.limit_value
                last_ts = now_ms
            end
            local refill_rate = scope.limit_value / scope.window_seconds -- tokens/sec
            local elapsed_seconds = math.max(0, (now_ms - last_ts) / 1000)
            tokens = math.min(scope.limit_value, tokens + (elapsed_seconds * refill_rate))
            if tokens < scope.quantity then
                granted = false
                local deficit = scope.quantity - tokens
                wait_ms = math.max(wait_ms, math.ceil((deficit / refill_rate) * 1000))
            end
            evaluations[i] = {bkey = bkey, skey = skey, mode = scope.mode, tokens = tokens, ts = now_ms}
        else
            -- ROLLING_WINDOW: pruning expired entries is always safe, even
            -- if this reservation is ultimately denied -- it never grants
            -- unearned capacity, only reclaims already-expired capacity.
            redis.call('ZREMRANGEBYSCORE', bkey, '-inf', now_ms - (scope.window_seconds * 1000))
            local count = redis.call('ZCARD', bkey)
            if count + scope.quantity > scope.limit_value then
                granted = false
                local oldest = redis.call('ZRANGE', bkey, 0, 0, 'WITHSCORES')
                if oldest and oldest[2] then
                    wait_ms = math.max(wait_ms, (tonumber(oldest[2]) + (scope.window_seconds * 1000)) - now_ms)
                else
                    wait_ms = math.max(wait_ms, scope.window_seconds * 1000)
                end
            end
            evaluations[i] = {bkey = bkey, skey = skey, mode = scope.mode}
        end
    end

    if not granted then
        return fail('CAPACITY_DENIED', {
            failing_index = i - 1,
            failing_kind = scope.kind,
            failing_scope_key = scope.scope_key,
            retry_after_ms = wait_ms,
        })
    end
end

-- 3. Every scope passed: commit all mutations together.
local per_scope = {}
for i, scope in ipairs(scopes) do
    local ev = evaluations[i]
    if ev.mode == 'TOKEN_BUCKET' then
        redis.call('HMSET', ev.bkey, 'tokens', ev.tokens - scope.quantity, 'ts', ev.ts)
        redis.call('EXPIRE', ev.bkey, scope.window_seconds * 2)
    else
        redis.call('ZADD', ev.bkey, now_ms, reservation_id)
        redis.call('EXPIRE', ev.bkey, scope.window_seconds * 2)
    end
    local min_wait_seconds = math.max(scope.cooldown_seconds or 0, scope.min_spacing_seconds or 0)
    if min_wait_seconds > 0 then
        redis.call('SET', ev.skey, now_ms, 'EX', min_wait_seconds * 2)
    end
    per_scope[i] = {
        kind = scope.kind,
        scope_key = scope.scope_key,
        unit = scope.unit,
        bucket_key = ev.bkey,
        mode = ev.mode,
        quantity = scope.quantity,
    }
end

-- 4. Persist the reservation record so release.lua can reverse it if the
--    caller never reaches durable Postgres authorization. Self-expires --
--    a reservation that is neither authorized nor explicitly released
--    simply lapses; RATE_LIMITING.md: "underutilization is preferable to
--    oversending."
redis.call('SET', 'rl:res:' .. reservation_id,
    cjson.encode({generation = current_generation, scopes = per_scope}),
    'EX', reservation_ttl_seconds)

return cjson.encode({
    ok = true,
    reservation_id = reservation_id,
    generation = current_generation,
    granted_at_ms = now_ms,
})
