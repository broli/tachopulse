--!nonstrict
-- TachoPulse Bar Widget (`[[widget]]`).
-- Thin, highly-optimized presentation wrapper rendering the 4 modular face elements
-- for this named widget instance.

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
  local container = barWidget.isVertical() and ui.column or ui.row
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
    barWidget.render(container({
      align = "center",
      gap = 4,
      padding = 2,
    }, {
      ui.glyph({ name = "gauge", size = 15, color = "primary" }),
      ui.label({ text = instanceId, fontSize = 11, color = "on_surface_variant" }),
    }))
    barWidget.setTooltip({
      { key = "Instance", value = instanceId },
      { key = "Status", value = "Initializing instance profile..." },
      { key = "Action", value = "Click to select hardware sensor" },
    })
    return
  end
  hasRequestedInit = false

  local children = {}

  -- 1. Optional Static Icon
  if inst.show_icon and inst.icon_val and inst.icon_val ~= "" then
    if inst.icon_type == "emoji" then
      table.insert(children, ui.label({
        text = inst.icon_val,
        fontSize = 13,
      }))
    else
      table.insert(children, ui.glyph({
        name = inst.icon_val,
        size = 15,
        color = getColorHex(inst.color),
      }))
    end
  end

  -- 2. Optional Dynamic Graphic
  if inst.show_graph and inst.bar_img and inst.bar_img ~= "" then
    local gaugeSize = tonumber(inst.bar_size) or 34
    table.insert(children, ui.image({
      path = inst.bar_img,
      width = gaugeSize,
      height = gaugeSize,
      radius = 4,
    }))
  end

  -- 3. Optional Numerical Value
  if inst.show_value and inst.val_text and inst.val_text ~= "" then
    table.insert(children, ui.label({
      text = inst.val_text,
      fontSize = 12,
      fontWeight = "bold",
      color = "on_surface",
    }))
  end

  -- 4. Optional Custom Label
  if inst.show_label and inst.label and inst.label ~= "" then
    table.insert(children, ui.label({
      text = inst.label,
      fontSize = 11,
      color = "on_surface_variant",
    }))
  end

  if #children == 0 then
    table.insert(children, ui.label({
      text = "•",
      fontSize = 12,
      color = "outline",
    }))
  end

  barWidget.render(container({
    align = "center",
    gap = 4,
    padding = 2,
  }, children))

  local displayName = (inst.name and inst.name ~= "" and inst.name ~= "default") and inst.name or instanceId
  barWidget.setTooltip({
    { key = "Instance", value = displayName },
    { key = "Sensor", value = inst.sensor_name or inst.sensor_id },
    { key = "Live Reading", value = inst.val_text or tostring(inst.value) },
    { key = "Style", value = inst.graph_type or "quarter" },
  })
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
