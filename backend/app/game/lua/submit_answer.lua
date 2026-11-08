-- submit_answer.lua: Xử lý nộp đáp án, chấm điểm và phân bố Atomic 100% (GM-7, GM-10, GM-13)
--
-- KEYS[1]  = state_key     ("game:" .. game_id .. ":state")
-- KEYS[2]  = answered_key  ("game:" .. game_id .. ":q:" .. q_idx .. ":answered")
-- KEYS[3]  = correct_key   ("game:" .. game_id .. ":q:" .. q_idx .. ":correct_keys")
-- KEYS[4]  = meta_key      ("game:" .. game_id .. ":q:" .. q_idx .. ":meta")
-- KEYS[5]  = lb_key        ("game:" .. game_id .. ":lb")
-- KEYS[6]  = streak_key    ("game:" .. game_id .. ":streaks")
-- KEYS[7]  = pts_key       ("game:" .. game_id .. ":q:" .. q_idx .. ":pts")
-- KEYS[8]  = dist_key      ("game:" .. game_id .. ":q:" .. q_idx .. ":dist")
-- KEYS[9]  = stream_key    ("game:" .. game_id .. ":answers")
-- KEYS[10] = players_key   ("game:" .. game_id .. ":players")
--
-- ARGV[1] = player_id
-- ARGV[2] = submitted_q_index
-- ARGV[3] = selected_options_csv (danh sách ID phương án cách nhau bởi dấu phẩy, ví dụ "opt_a,opt_b")
-- ARGV[4] = override_now_ms (tuỳ chọn, > 0 để test)

local state_key    = KEYS[1]
local answered_key = KEYS[2]
local correct_key  = KEYS[3]
local meta_key     = KEYS[4]
local lb_key       = KEYS[5]
local streak_key   = KEYS[6]
local pts_key      = KEYS[7]
local dist_key     = KEYS[8]
local stream_key   = KEYS[9]
local players_key  = KEYS[10]

local player_id            = ARGV[1]
local submitted_q_index    = tonumber(ARGV[2]) or 0
local selected_options_csv = ARGV[3] or ''
local override_now_ms      = tonumber(ARGV[4]) or 0

-- Helper lấy thời gian hiện tại ms đồng bộ với Redis TIME
local function get_current_time_ms(override)
    if override > 0 then
        return override
    end
    local time_res = redis.call('TIME')
    local sec = tonumber(time_res[1])
    local usec = tonumber(time_res[2])
    return (sec * 1000) + math.floor(usec / 1000)
end

-- Helper tách chuỗi CSV thành mảng các option ID
local function split_csv(input)
    local result = {}
    if not input or input == '' then
        return result
    end
    for match in string.gmatch(input, "([^,]+)") do
        local trimmed = string.match(match, "^%s*(.-)%s*$")
        if trimmed and #trimmed > 0 then
            table.insert(result, trimmed)
        end
    end
    return result
end

-- 1. Kiểm tra trạng thái trận đấu
local status = redis.call('HGET', state_key, 'status')
if status ~= 'question_active' then
    return {0, 'NOT_ACTIVE', status or ''}
end

-- 2. Kiểm tra câu hỏi có khớp câu hiện tại không
local current_q_index = tonumber(redis.call('HGET', state_key, 'q_index')) or 0
if submitted_q_index ~= current_q_index then
    return {0, 'WRONG_QUESTION', tostring(current_q_index)}
end

-- 3. Kiểm tra Deadline (dùng cùng nguồn giờ Redis TIME)
local now_ms = get_current_time_ms(override_now_ms)
local deadline = tonumber(redis.call('HGET', state_key, 'deadline')) or 0

if deadline > 0 and now_ms > deadline then
    return {0, 'LATE', tostring(deadline)}
end

-- 4. Kiểm tra xem người chơi đã nộp câu này chưa (Atomic SADD)
-- Nếu SADD trả về 0 -> Người chơi đã trả lời câu này trước đó -> Từ chối ALREADY_ANSWERED
local added = redis.call('SADD', answered_key, player_id)
if added == 0 then
    return {0, 'ALREADY_ANSWERED', ''}
end

-- 5. Đọc Metadata câu hỏi và Chiến lược tính điểm
local base_points = tonumber(redis.call('HGET', meta_key, 'points')) or 1000
local time_limit_s = tonumber(redis.call('HGET', meta_key, 'time_limit_s')) or 15
local q_type = redis.call('HGET', meta_key, 'type') or 'single'
local q_id = redis.call('HGET', meta_key, 'id') or ''
local strategy_id = redis.call('HGET', state_key, 'strategy_id') or 'standard'

local time_limit_ms = math.max(time_limit_s * 1000, 1)

-- Tính response_ms từ deadline
-- deadline = start_time + time_limit_ms  ==>  start_time = deadline - time_limit_ms
local start_time_ms = deadline - time_limit_ms
local response_ms = now_ms - start_time_ms
if response_ms < 0 then
    response_ms = 0
end
if response_ms > time_limit_ms then
    response_ms = time_limit_ms
end

-- 6. Kiểm tra tính đúng/sai của đáp án
local selected_opts = split_csv(selected_options_csv)
local num_selected = #selected_opts
local num_correct = redis.call('SCARD', correct_key)

local is_correct = false
if num_selected > 0 and num_correct > 0 then
    local matched_correct = 0
    for _, opt in ipairs(selected_opts) do
        if redis.call('SISMEMBER', correct_key, opt) == 1 then
            matched_correct = matched_correct + 1
        end
    end

    if q_type == 'multiple' then
        -- Luật: phải chọn đúng và đủ 100% tất cả các đáp án đúng, không chọn thừa đáp án sai
        is_correct = (num_selected == num_correct and matched_correct == num_correct)
    else
        -- single / true_false: chọn đúng duy nhất 1 đáp án đúng
        is_correct = (num_selected == 1 and matched_correct == 1)
    end
end

-- 7. Tính điểm (Strategy Pattern với quy tắc làm tròn nửa lên half-up)
local points_awarded = 0
local new_streak = 0

if not is_correct then
    points_awarded = 0
    new_streak = 0
else
    local current_streak = tonumber(redis.call('HGET', streak_key, player_id)) or 0
    new_streak = current_streak + 1

    local raw_score = 0
    if strategy_id == 'binary' then
        raw_score = base_points
    else
        -- Standard: points = base * (1 - 0.5 * (t / T))
        local t_ratio = response_ms / time_limit_ms
        if t_ratio < 0.0 then t_ratio = 0.0 end
        if t_ratio > 1.0 then t_ratio = 1.0 end
        raw_score = base_points * (1.0 - (0.5 * t_ratio))
    end

    -- Thưởng chuỗi liên tiếp (streak >= 3 -> +10%)
    if new_streak >= 3 then
        raw_score = raw_score * 1.1
    end

    -- Làm tròn nửa lên (half-up) đồng nhất với Python: math.floor(x + 0.5)
    points_awarded = math.floor(raw_score + 0.5)
end

-- 8. Cập nhật dữ liệu Atomic
-- 8.1. Cập nhật Streak
redis.call('HSET', streak_key, player_id, tostring(new_streak))

-- 8.2. Cập nhật điểm câu này vào hash phụ để phục vụ thông điệp cá nhân hóa (question.result)
redis.call('HSET', pts_key, player_id, tostring(points_awarded))

-- 8.3. Tăng điểm trên Bảng xếp hạng ZSET Leaderboard
local new_total_score = redis.call('ZINCRBY', lb_key, points_awarded, player_id)

-- 8.4. Cập nhật phân bố câu trả lời (HINCRBY dist) cho từng option được chọn
for _, opt in ipairs(selected_opts) do
    redis.call('HINCRBY', dist_key, opt, 1)
end

-- 8.5. Ghi vào Redis Stream phục vụ Worker ghi bền (Write-behind sang Postgres)
redis.call('XADD', stream_key, '*',
    'player_id', player_id,
    'question_id', q_id,
    'q_index', tostring(submitted_q_index),
    'option_ids', selected_options_csv,
    'points', tostring(points_awarded),
    'response_ms', tostring(response_ms),
    'is_correct', is_correct and '1' or '0',
    'answered_at', tostring(now_ms)
)

-- 8.6. Kiểm tra xem tất cả người chơi trong phòng đã nộp câu này chưa (phục vụ đóng sớm)
local total_answered = redis.call('SCARD', answered_key)
local total_players = redis.call('HLEN', players_key)
local all_answered = 0
if total_players > 0 and total_answered >= total_players then
    all_answered = 1
end

-- Trả về: [1, is_correct (1/0), points_awarded, new_streak, new_total_score, all_answered, response_ms]
return {
    1,
    is_correct and '1' or '0',
    tostring(points_awarded),
    tostring(new_streak),
    tostring(new_total_score),
    tostring(all_answered),
    tostring(response_ms)
}
