--!nonstrict
-- TachoPulse headless background service (`[[service]]`).
--
-- Manages the active sensor configuration in `noctalia.pluginDataDir()/sensors.json`,
-- invokes `scripts/sensor_daemon.py` at the configured polling interval, and
-- broadcasts live snapshots + rendered graph paths to `bar.luau`, `desktop.luau`,
-- and `panel.luau` via `noctalia.state`.

local COLORS = { "blue", "cyan", "green", "orange", "purple", "red", "yellow" }

local runtimeDir = (noctalia.getenv("XDG_RUNTIME_DIR") or "/dev/shm") .. "/tachopulse"
local snapshotPath = runtimeDir .. "/snapshot.json"

local function getSensorsPath()
  local dataDir = noctalia.pluginDataDir()
  if dataDir and dataDir ~= "" then
    return dataDir .. "/sensors.json"
  end
  return runtimeDir .. "/sensors.json"
end

local activeSensors = {}
local isPolling = false
local lastPollStartMs = 0

local function loadSavedSensors()
  local path = getSensorsPath()
  if noctalia.fileExists(path) then
    local raw = noctalia.readFile(path)
    if raw and raw ~= "" then
      local decoded = noctalia.json.decode(raw)
      if type(decoded) == "table" and #decoded > 0 then
        activeSensors = decoded
        return
      end
    end
  end
end

local function saveSensors()
  local path = getSensorsPath()
  local encoded = noctalia.json.encode(activeSensors)
  if encoded then
    noctalia.writeFile(path, encoded)
  end
end

local function applyInterval()
  local interval = tonumber(noctalia.getConfig("poll_interval_ms")) or 2000
  if interval < 500 then
    interval = 500
  end
  noctalia.setUpdateInterval(interval)
end

local function pollHardware()
  local now = noctalia.nowMs()
  if isPolling and (now - lastPollStartMs) < 5000 then
    return
  end
  isPolling = true
  lastPollStartMs = now

  local pluginDir = noctalia.pluginDir()
  local theme = noctalia.isDarkMode() and "dark" or "light"
  local barSize = tostring(tonumber(noctalia.getConfig("bar_gauge_size")) or 34)
  local deskSize = tostring(tonumber(noctalia.getConfig("desktop_gauge_size")) or 96)
  local respectSleep = (noctalia.getConfig("respect_gpu_sleep") == true) and "true" or "false"
  local sensorsPath = getSensorsPath()

  local cmd = string.format(
    'python3 "%s/scripts/sensor_daemon.py" "%s" "%s" "%s" "%s" "%s"',
    pluginDir,
    theme,
    barSize,
    deskSize,
    respectSleep,
    sensorsPath
  )

  noctalia.runAsync(cmd, function(res)
    isPolling = false
    if res and res.exitCode == 0 and noctalia.fileExists(snapshotPath) then
      local raw = noctalia.readFile(snapshotPath)
      if raw and raw ~= "" then
        local snap = noctalia.json.decode(raw)
        if type(snap) == "table" then
          if #activeSensors == 0 then
            loadSavedSensors()
          end
          noctalia.state.set("tachopulse.snapshot", snap)
        end
      end
    end
  end, 4500)
end

noctalia.state.watch("tachopulse.cmd", function(cmd)
  if type(cmd) ~= "table" then
    return
  end
  loadSavedSensors()
  local action = cmd.action
  local idx = tonumber(cmd.index)

  if action == "toggle_field" and idx and activeSensors[idx] and type(cmd.field) == "string" then
    local field = cmd.field
    activeSensors[idx][field] = not (activeSensors[idx][field] == true)
    saveSensors()
    pollHardware()
  elseif action == "set_graph_type" and idx and activeSensors[idx] and type(cmd.graph_type) == "string" then
    activeSensors[idx].graph_type = cmd.graph_type
    saveSensors()
    pollHardware()
  elseif action == "cycle_color" and idx and activeSensors[idx] then
    local cur = activeSensors[idx].color or "blue"
    local nextCol = "blue"
    for i, c in ipairs(COLORS) do
      if c == cur then
        nextCol = COLORS[(i % #COLORS) + 1]
        break
      end
    end
    activeSensors[idx].color = nextCol
    saveSensors()
    pollHardware()
  elseif action == "move" and idx and activeSensors[idx] then
    local delta = tonumber(cmd.delta) or 0
    local target = idx + delta
    if target >= 1 and target <= #activeSensors then
      local tmp = activeSensors[idx]
      activeSensors[idx] = activeSensors[target]
      activeSensors[target] = tmp
      saveSensors()
      pollHardware()
    end
  elseif action == "remove" and idx and activeSensors[idx] then
    table.remove(activeSensors, idx)
    saveSensors()
    pollHardware()
  elseif action == "add" and type(cmd.sensor) == "table" then
    local s = cmd.sensor
    table.insert(activeSensors, {
      id = s.id,
      label = s.short_label or "SENS",
      graph_type = "ring",
      color = s.default_color or "blue",
      show_icon = false,
      show_label = true,
      show_value = true,
      show_graph = true,
      on_bar = true,
      on_desktop = true,
    })
    saveSensors()
    pollHardware()
  elseif action == "reset_defaults" then
    activeSensors = {}
    local path = getSensorsPath()
    if noctalia.fileExists(path) then
      noctalia.removeFile(path)
    end
    pollHardware()
  end
end)

function onConfigChanged()
  applyInterval()
  pollHardware()
end

function update()
  pollHardware()
end

loadSavedSensors()
applyInterval()
pollHardware()
