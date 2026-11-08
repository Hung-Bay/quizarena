-- transition.lua: Chuyển đổi trạng thái vòng đời trận đấu một cách Atomic (GM-1, GM-14, GM-15)
-- KEYS[1] = state_key ("game:" .. game_id .. ":state")
-- ARGV[1] = action ("start", "next", "close", "end", "pause")
-- ARGV[2] = expected_q_index / time_limit_s (tùy action)
-- ARGV[3] = override_now_ms (tuỳ chọn, > 0 để test)

local state_key = KEYS[1]
local action = ARGV[1]

-- Helper lấy thời gian hiện tại tính bằng milliseconds (đồng bộ nguồn giờ Redis TIME)
local function get_current_time_ms(override)
    local ov = tonumber(override) or 0
    if ov > 0 then
        return ov
    end
    local time_res = redis.call('TIME')
    local sec = tonumber(time_res[1])
    local usec = tonumber(time_res[2])
    return (sec * 1000) + math.floor(usec / 1000)
end

-- Đọc trạng thái hiện tại
local current_status = redis.call('HGET', state_key, 'status')
if not current_status then
    return {0, 'GAME_NOT_FOUND', ''}
end

local current_q_index = tonumber(redis.call('HGET', state_key, 'q_index')) or 0
local total_questions = tonumber(redis.call('HGET', state_key, 'total_questions')) or 0
local dl_version = tonumber(redis.call('HGET', state_key, 'deadline_version')) or 0

-- -------------------------------------------------------------------------
-- ACTION: start (Bắt đầu trận đấu từ LOBBY -> QUESTION_ACTIVE câu 1)
-- -------------------------------------------------------------------------
if action == 'start' then
    if current_status ~= 'lobby' then
        return {0, 'INVALID_STATE', current_status}
    end

    local time_limit_s = tonumber(ARGV[2]) or 15
    local now_ms = get_current_time_ms(ARGV[3])
    local deadline = now_ms + (time_limit_s * 1000)
    dl_version = dl_version + 1

    redis.call('HSET', state_key,
        'status', 'question_active',
        'q_index', 1,
        'deadline', tostring(deadline),
        'deadline_version', tostring(dl_version),
        'started_at', tostring(now_ms)
    )

    -- Trả về: [1, status, q_index, deadline, server_time, dl_version]
    return {1, 'question_active', '1', tostring(deadline), tostring(now_ms), tostring(dl_version)}

-- -------------------------------------------------------------------------
-- ACTION: next (Chuyển sang câu hỏi tiếp theo từ QUESTION_RESULT -> QUESTION_ACTIVE)
-- -------------------------------------------------------------------------
elseif action == 'next' then
    if current_status ~= 'question_result' then
        return {0, 'INVALID_STATE', current_status}
    end

    local expected_q = tonumber(ARGV[2]) or 0
    local next_q = current_q_index + 1

    -- Chống bấm đúp (Double-click protection): nếu expected_q_index không khớp -> từ chối
    if expected_q > 0 and expected_q ~= next_q then
        return {0, 'VERSION_MISMATCH', tostring(current_q_index)}
    end

    if next_q > total_questions then
        return {0, 'NO_MORE_QUESTIONS', tostring(current_q_index)}
    end

    local time_limit_s = tonumber(ARGV[3]) or 15
    local now_ms = get_current_time_ms(ARGV[4])
    local deadline = now_ms + (time_limit_s * 1000)
    dl_version = dl_version + 1

    redis.call('HSET', state_key,
        'status', 'question_active',
        'q_index', tostring(next_q),
        'deadline', tostring(deadline),
        'deadline_version', tostring(dl_version)
    )

    return {1, 'question_active', tostring(next_q), tostring(deadline), tostring(now_ms), tostring(dl_version)}

-- -------------------------------------------------------------------------
-- ACTION: close (Đóng câu hỏi hiện tại: QUESTION_ACTIVE -> QUESTION_RESULT)
-- Idempotent: Nếu câu đã đóng trước đó bởi worker hoặc host -> Trả kết quả thành công mà không đổi gì
-- -------------------------------------------------------------------------
elseif action == 'close' then
    local expected_q = tonumber(ARGV[2]) or 0

    if current_status == 'question_result' then
        if expected_q == 0 or expected_q == current_q_index then
            -- Idempotent hit: đã đóng thành công trước đó
            return {1, 'question_result', tostring(current_q_index), '0', '0', '1'}
        end
    end

    if current_status ~= 'question_active' then
        return {0, 'INVALID_STATE', current_status}
    end

    if expected_q > 0 and expected_q ~= current_q_index then
        return {0, 'WRONG_QUESTION', tostring(current_q_index)}
    end

    redis.call('HSET', state_key,
        'status', 'question_result',
        'deadline', '0'
    )

    return {1, 'question_result', tostring(current_q_index), '0', '0', '0'}

-- -------------------------------------------------------------------------
-- ACTION: end (Kết thúc trận đấu -> FINISHED)
-- -------------------------------------------------------------------------
elseif action == 'end' then
    local now_ms = get_current_time_ms(ARGV[2])
    redis.call('HSET', state_key,
        'status', 'finished',
        'deadline', '0',
        'ended_at', tostring(now_ms)
    )
    return {1, 'finished', tostring(current_q_index), '0', tostring(now_ms), '0'}

-- -------------------------------------------------------------------------
-- ACTION: pause (Tạm dừng trận đấu -> PAUSED)
-- -------------------------------------------------------------------------
elseif action == 'pause' then
    dl_version = dl_version + 1
    redis.call('HSET', state_key,
        'status', 'paused',
        'deadline_version', tostring(dl_version)
    )
    return {1, 'paused', tostring(current_q_index), '0', '0', tostring(dl_version)}

else
    return {0, 'UNKNOWN_ACTION', action}
end
