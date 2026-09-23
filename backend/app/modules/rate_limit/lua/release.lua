-- Best-effort compensating release for a reservation that was taken but
-- never reached durable Postgres authorization (e.g. a later safety gate
-- rejected the message after Redis capacity was already reserved).
--
-- MUST NEVER be called after a provider send has actually been attempted --
-- RATE_LIMITING.md specifies no refunds for uncertain/ambiguous provider
-- outcomes ("Proposed initial policy avoids refunds for uncertain provider
-- usage" / "Unknown outcome never triggers token refund and resend"). That
-- invariant is enforced by the caller (sending/service.py only releases
-- before the authorization transaction commits, never after), not by this
-- script itself.
--
-- KEYS[1] = the reservation record key ('rl:res:' .. reservation_id)
-- ARGV[1] = reservation_id (used to remove this reservation's own rolling-
--           window entry; harmless no-op for token-bucket scopes)

local raw = redis.call('GET', KEYS[1])
if not raw then
    return cjson.encode({ok = false, reason = 'RESERVATION_NOT_FOUND'})
end

local record = cjson.decode(raw)
for _, scope in ipairs(record.scopes) do
    if scope.mode == 'TOKEN_BUCKET' then
        local tokens = tonumber(redis.call('HGET', scope.bucket_key, 'tokens'))
        if tokens then
            -- Refund is capped by the bucket's own limit implicitly: the
            -- next reservation's evaluation clamps tokens to limit_value
            -- regardless of what accumulates here, so an over-refund can
            -- only ever waste capacity (fail closed), never grant extra.
            redis.call('HSET', scope.bucket_key, 'tokens', tokens + scope.quantity)
        end
    else
        redis.call('ZREM', scope.bucket_key, ARGV[1])
    end
end

redis.call('DEL', KEYS[1])
return cjson.encode({ok = true})
