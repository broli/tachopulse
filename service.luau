--!nonstrict
-- TachoPulse Service Daemon (`[[service]]`).
-- Manages the telemetry sensor daemon, streams updates, and publishes
-- real-time state to noctalia.state for all widget instances.

local RUNTIME_BASE = (noctalia.getenv("XDG_RUNTIME_DIR") or "/dev/shm") .. "/tachopulse"
local SNAPSHOT_PATH = RUNTIME_BASE .. "/snapshot.json"
local CMD_PATH = RUNTIME_BASE .. "/cmd.json"

local defaultPollMs = 2000
local isDaemonStreaming = false
local isPollingFallback = false
local lastStreamTick = 0

local function getPollInterval()
  local poll = tonumber(noctalia.getConfig("poll_interval_ms"))
  if not poll or poll < 500 then
    return defaultPollMs
  end
  return poll
end

local function getGaugeSizes()
  local barSize = tonumber(noctalia.getConfig("bar_gauge_size")) or 34
  local deskSize = tonumber(noctalia.getConfig("desktop_gauge_size")) or 96
  return barSize, deskSize
end

local function applySnapshot(snap)
  if type(snap) == "table" and snap.instances then
    noctalia.state.set("tachopulse.snapshot", snap)
  end
end

local function loadSnapshotFromDisk()
  if noctalia.fileExists(SNAPSHOT_PATH) then
    local raw = noctalia.readFile(SNAPSHOT_PATH)
    if raw and raw ~= "" then
      local snap = noctalia.json.decode(raw)
      applySnapshot(snap)
    end
  end
end

local function triggerFallbackPoll()
  if isPollingFallback then
    return
  end
  isPollingFallback = true

  local scriptPath = noctalia.pluginDir() .. "/scripts/sensor_daemon.py"
  local barSize, deskSize = getGaugeSizes()
  local cmd = string.format("python3 %s --bar-size %d --desktop-size %d", scriptPath, barSize, deskSize)

  noctalia.runAsync(cmd, function(res)
    isPollingFallback = false
    if res.exitCode == 0 then
      loadSnapshotFromDisk()
    end
  end, 3000)
end

local function startDaemonStream()
  local scriptPath = noctalia.pluginDir() .. "/scripts/sensor_daemon.py"
  local barSize, deskSize = getGaugeSizes()
  local pollSec = getPollInterval() / 1000.0
  local cmd = string.format("python3 %s --daemon --poll %.2f --bar-size %d --desktop-size %d", scriptPath, pollSec, barSize, deskSize)

  lastStreamTick = os.time()
  isDaemonStreaming = noctalia.runStream(cmd, function(line)
    if line and line ~= "" then
      lastStreamTick = os.time()
      local ok, snap = pcall(noctalia.json.decode, line)
      if ok and type(snap) == "table" and snap.instances then
        applySnapshot(snap)
      end
    end
  end)

  if not isDaemonStreaming then
    triggerFallbackPoll()
  end
end

-- Watch for commands from the Configuration Studio or widgets
noctalia.state.watch("tachopulse.cmd", function(val)
  if type(val) == "table" and val.action then
    local payload = noctalia.json.encode(val)
    if payload then
      noctalia.writeFile(CMD_PATH, payload)
      if not isDaemonStreaming then
        triggerFallbackPoll()
      end
    end
  end
end)

function update()
  local pollSec = getPollInterval() / 1000.0
  local maxGapSec = math.max(6, math.floor(pollSec * 3) + 2)
  if not isDaemonStreaming or (os.time() - lastStreamTick > maxGapSec) then
    isDaemonStreaming = false
    startDaemonStream()
  end
end

local function sendDaemonConfig()
  local barSize, deskSize = getGaugeSizes()
  local pollSec = getPollInterval() / 1000.0
  noctalia.state.set("tachopulse.cmd", {
    action = "configure",
    poll_interval = pollSec,
    bar_size = barSize,
    desktop_size = deskSize,
  })
end

function onConfigChanged()
  noctalia.setUpdateInterval(getPollInterval())
  if isDaemonStreaming then
    sendDaemonConfig()
  else
    startDaemonStream()
  end
end

noctalia.setUpdateInterval(getPollInterval())
loadSnapshotFromDisk()
startDaemonStream()
