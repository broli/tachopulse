--!nonstrict
-- TachoPulse Desktop Widget (`[[desktop_widget]]`).
-- Renders the same modular face elements as a card pinned to the desktop wallpaper.

local PALETTE_HEX = {
  cyan = "#56d6d6",
  blue = "#78b4ff",
  green = "#73e69b",
  orange = "#ffb96e",
  purple = "#c391ff",
  red = "#ff6e7d",
  yellow = "#fae173",
  white = "#f0f5ff",
}

local function getColorHex(c)
  if not c or c == "" then
    return "#56d6d6"
  end
  if string.sub(c, 1, 1) == "#" then
    return c
  end
  return PALETTE_HEX[c] or "#56d6d6"
end

local function getInstanceId()
  local id = noctalia.getConfig("instance_id")
  if not id or id == "" then
    return "default"
  end
  return id
end

local currentSnapshot = noctalia.state.get("tachopulse.snapshot")
local hasRequestedInit = false

local function render()
  local instanceId = getInstanceId()
  local inst = nil
  if currentSnapshot and currentSnapshot.instances then
    inst = currentSnapshot.instances[instanceId]
  end

  if not inst then
    if not hasRequestedInit then
      hasRequestedInit = true
      noctalia.state.set("tachopulse.cmd", {
        action = "ensure_instance",
        instance_id = instanceId,
      })
    end
    desktopWidget.render(ui.column({
      padding = 12,
      radius = 12,
      fill = "surface_variant/0.65",
      align = "center",
    }, {
      ui.label({ text = "TachoPulse: " .. instanceId, fontSize = 12, color = "on_surface_variant" }),
      ui.label({ text = "Initializing profile...", fontSize = 10, color = "outline" }),
    }))
    return
  end
  hasRequestedInit = false

  local gaugeSize = tonumber(inst.desktop_size) or 96
  local colChildren = {}

  -- Header: Icon + Custom Label
  local headerChildren = {}
  if inst.show_icon and inst.icon_val and inst.icon_val ~= "" then
    if inst.icon_type == "emoji" then
      table.insert(headerChildren, ui.label({ text = inst.icon_val, fontSize = 14 }))
    else
      table.insert(headerChildren, ui.glyph({ name = inst.icon_val, size = 16, color = getColorHex(inst.color) }))
    end
  end
  if inst.show_label and inst.label and inst.label ~= "" then
    table.insert(headerChildren, ui.label({ text = inst.label, fontSize = 12, fontWeight = "bold", color = "on_surface" }))
  end
  if #headerChildren > 0 then
    table.insert(colChildren, ui.row({ align = "center", gap = 6 }, headerChildren))
  end

  -- Dynamic Graphic
  if inst.show_graph and inst.desk_img and inst.desk_img ~= "" then
    table.insert(colChildren, ui.image({
      path = inst.desk_img,
      width = gaugeSize,
      height = gaugeSize,
      radius = 6,
    }))
  end

  -- Numerical Value
  if inst.show_value and inst.val_text and inst.val_text ~= "" then
    table.insert(colChildren, ui.label({
      text = inst.val_text,
      fontSize = 14,
      fontWeight = "bold",
      color = "on_surface",
    }))
  end

  if #colChildren == 0 then
    table.insert(colChildren, ui.label({
      text = "•",
      fontSize = 14,
      color = "outline",
    }))
  end

  desktopWidget.render(ui.column({
    padding = 10,
    radius = 12,
    fill = "surface_variant/0.75",
    align = "center",
    gap = 6,
  }, colChildren))
end

noctalia.state.watch("tachopulse.snapshot", function(snap)
  if type(snap) == "table" and snap.instances then
    currentSnapshot = snap
    render()
  end
end)

function onConfigChanged()
  render()
end

function onClick()
  local instanceId = getInstanceId()
  noctalia.togglePanel("broli/tachopulse:panel", instanceId)
end

render()
