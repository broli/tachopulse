--!nonstrict
-- TachoPulse Configuration Studio (`[[panel]]`).
-- Flow:
-- 1. Starts by default with the full, filterable Sensor List (62+ hardware & storage metrics).
-- 2. Clicking 'Select & Configure →' on any sensor opens the Face Configurator for that sensor.
-- 3. In the Face Configurator, '← Back to Sensor List' returns seamlessly to the list.
-- 4. Four modular face elements can be toggled on/off independently:
--    {static icon}, {dynamic representation}, {numerical value}, {label}.

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

local COLOR_PALETTES = {
  { id = "cyan", label = "🩵 Neon Cyan", hex = "#56d6d6" },
  { id = "blue", label = "🔵 Electric Blue", hex = "#78b4ff" },
  { id = "green", label = "🟢 Emerald Green", hex = "#73e69b" },
  { id = "orange", label = "🟠 Amber Orange", hex = "#ffb96e" },
  { id = "purple", label = "🟣 Cyber Purple", hex = "#c391ff" },
  { id = "red", label = "🔴 Crimson Red", hex = "#ff6e7d" },
  { id = "yellow", label = "🟡 Golden Yellow", hex = "#fae173" },
}

local GRAPH_STYLES = {
  { id = "tachometer_quarter", label = "Quarter Tachometer (90° Corner Arc)" },
  { id = "tachometer_full", label = "Full Tachometer (~330° Speedometer)" },
  { id = "bar_vertical", label = "Vertical Bar (Linear Capsule)" },
  { id = "geometric_wifi", label = "WiFi Radiating Arcs (Geometric)" },
  { id = "geometric_cellular", label = "Cellular Signal Bars (Stepped)" },
}

local STROKE_WIDTHS = {
  { width = 5, label = "Medium (5px)" },
  { width = 7, label = "Bold (7px) - Recommended" },
  { width = 9, label = "Heavy (9px)" },
}

local COLOR_OPTIONS = {}
local COLOR_INDEX = {}
for i, c in ipairs(COLOR_PALETTES) do
  table.insert(COLOR_OPTIONS, c.label)
  COLOR_INDEX[c.id] = i - 1
end

local GRAPH_OPTIONS = {}
local GRAPH_INDEX = {}
for i, g in ipairs(GRAPH_STYLES) do
  table.insert(GRAPH_OPTIONS, g.label)
  GRAPH_INDEX[g.id] = i - 1
end

local STROKE_OPTIONS = {}
local STROKE_INDEX = {}
for i, s in ipairs(STROKE_WIDTHS) do
  table.insert(STROKE_OPTIONS, s.label)
  STROKE_INDEX[s.width] = i - 1
end

local currentView = "picker" -- "picker" (default sensor list) | "config" (face configurator)
local activeInstanceId = tostring(noctalia.state.get("tachopulse.active_instance") or "default")
local selectedCategory = "ALL"
local searchQuery = ""
local currentLabelDraft = nil
local lastLabelInstanceId = nil
local currentIconDraft = nil
local lastIconInstanceId = nil

local function sendCmd(payload)
  noctalia.state.set("tachopulse.cmd", payload)
end

local function createDefaultInstance(id, s)
  return {
    id = id,
    name = id,
    sensor_id = s and s.id or "cpu/total_usage",
    sensor_name = s and s.name or "CPU Total Load",
    color = s and s.default_color or "cyan",
    show_icon = true,
    icon_type = "glyph",
    icon_val = s and s.icon or "cpu",
    show_graph = true,
    graph_type = "tachometer_quarter",
    stroke_width = 7,
    show_value = true,
    decimals = 0,
    show_label = true,
    label = s and (s.short_label or "Sensor") or "CPU",
  }
end

local function getSnapshot()
  local snap = noctalia.state.get("tachopulse.snapshot")
  if type(snap) == "table" and snap.instances and snap.catalog then
    return snap
  end
  return nil
end

local function saveInstance(inst)
  sendCmd({
    action = "save_instance",
    instance = inst,
  })
end

local render

-- Reusable Fieldset Group Box (using ui.column with border)
local function renderGroupBox(title, isEnabled, onToggle, bodyChildren)
  local headerRow = ui.row({
    align = "center",
    justify = "space_between",
    padding = 2,
  }, {
    ui.row({ align = "center", gap = 8 }, {
      ui.label({
        text = title,
        fontSize = 12,
        fontWeight = "bold",
        color = isEnabled and "on_surface" or "outline",
      }),
    }),
    ui.toggle({
      checked = isEnabled,
      onChange = onToggle,
    }),
  })

  local colChildren = { headerRow }
  if isEnabled then
    table.insert(colChildren, ui.column({ gap = 8, padding = 4 }, bodyChildren))
  else
    table.insert(colChildren, ui.column({ gap = 8, padding = 4, opacity = 0.35, enabled = false }, bodyChildren))
  end

  return ui.column({
    gap = 6,
    padding = 10,
    radius = 8,
    border = isEnabled and "outline" or "outline/0.3",
    borderWidth = 1,
    fill = isEnabled and "surface_variant/0.25" or "surface_variant/0.05",
  }, colChildren)
end

-- ==============================================================================
-- 1. SENSOR PICKER VIEW (Step 1: Default Sensor List)
-- ==============================================================================

local function renderPickerView(inst, snapshot)
  local catalog = snapshot.catalog or {}
  local q = string.lower(searchQuery or "")

  local filtered = {}
  for _, s in ipairs(catalog) do
    local catOk = (selectedCategory == "ALL") or (s.category == selectedCategory)
    if catOk then
      if q == "" then
        table.insert(filtered, s)
      else
        local hay = string.lower(tostring(s.name or "") .. " " .. tostring(s.category or "") .. " " .. tostring(s.id or "") .. " " .. tostring(s.short_label or ""))
        if string.find(hay, q, 1, true) ~= nil then
          table.insert(filtered, s)
        end
      end
    end
  end

  local sections = {}

  -- Instance Context Header
  table.insert(sections, ui.row({
    align = "center",
    justify = "space_between",
    padding = 2,
  }, {
    ui.row({ align = "center", gap = 6 }, {
      ui.label({ text = "INSTANCE:", fontSize = 11, fontWeight = "bold", color = "primary" }),
      ui.label({ text = string.upper(activeInstanceId), fontSize = 11, fontWeight = "bold", color = "on_surface" }),
    }),
    ui.label({ text = "Select a hardware sensor below to bind to this widget instance", fontSize = 11, color = "on_surface_variant" }),
  }))

  -- Search Input
  table.insert(sections, ui.input({
    key = "tp-picker-search",
    value = searchQuery,
    placeholder = "Type to search sensors (e.g. free, fan, nvme, gpu, temp, wifi, cpu)...",
    fontSize = 12,
    controlSize = "md",
    onChange = function(val)
      searchQuery = tostring(val or "")
      render()
    end,
  }))

  -- Category Filter Buttons (flexible responsive wrapping)
  local catButtons = {
    ui.button({
      text = "ALL",
      variant = (selectedCategory == "ALL") and "primary" or "ghost",
      controlSize = "sm",
      onClick = function()
        selectedCategory = "ALL"
        render()
      end,
    }),
  }
  for _, c in ipairs(snapshot.categories or {}) do
    table.insert(catButtons, ui.button({
      text = c,
      variant = (selectedCategory == c) and "primary" or "ghost",
      controlSize = "sm",
      onClick = function()
        selectedCategory = c
        render()
      end,
    }))
  end

  table.insert(sections, ui.row({
    wrap = true,
    gap = 6,
    align = "center",
    padding = 2,
  }, catButtons))

  -- Scrollable Sensor Cards List
  local sensorCards = {}
  for _, s in ipairs(filtered) do
    local isCurrent = (inst and inst.sensor_id == s.id)
    local sHex = getColorHex(s.default_color)

    table.insert(sensorCards, ui.row({
      align = "center",
      gap = 12,
      padding = 8,
      radius = 8,
      border = isCurrent and "primary" or "outline/0.3",
      borderWidth = isCurrent and 2 or 1,
      fill = isCurrent and "primary/0.12" or "surface_variant/0.25",
    }, {
      ui.glyph({
        name = s.icon or "gauge",
        size = 20,
        color = sHex,
      }),
      ui.column({ flexGrow = 1, gap = 2 }, {
        ui.label({
          text = s.name,
          fontSize = 12,
          fontWeight = "bold",
          color = "on_surface",
        }),
        ui.label({
          text = s.category .. "  ·  " .. s.id,
          fontSize = 10,
          color = "on_surface_variant",
        }),
      }),
      -- Live Fluctuating Value
      ui.label({
        text = s.val_text or tostring(s.value or ""),
        fontSize = 14,
        fontWeight = "bold",
        color = sHex,
      }),
      -- Configure Button
      ui.button({
        text = isCurrent and "✓ Selected (Configure)" or "Select & Configure →",
        variant = isCurrent and "primary" or "outline",
        controlSize = "sm",
        onClick = function()
          if not inst then
            inst = createDefaultInstance(activeInstanceId, s)
          else
            inst.id = activeInstanceId
            inst.name = activeInstanceId
            inst.sensor_id = s.id
            inst.sensor_name = s.name
            inst.unit = s.unit or ""
            inst.label = s.short_label or "Sensor"
            if s.icon and s.icon ~= "" then
              inst.icon_val = s.icon
              inst.icon_type = "glyph"
            end
            if s.default_color then
              inst.color = s.default_color
            end
          end
          currentLabelDraft = inst.label
          currentIconDraft = inst.icon_val
          saveInstance(inst)
          currentView = "config"
          render()
        end,
      }),
    }))
  end

  if #sensorCards == 0 then
    table.insert(sensorCards, ui.row({
      padding = 16,
      justify = "center",
    }, {
      ui.label({
        text = "No hardware sensors match '" .. searchQuery .. "'. Try selecting 'ALL' or another search query.",
        fontSize = 12,
        color = "outline",
      }),
    }))
  end

  table.insert(sections, ui.scroll({
    flexGrow = 1,
    gap = 6,
    padding = 2,
  }, sensorCards))

  return ui.column({
    flexGrow = 1,
    gap = 8,
  }, sections)
end

-- ==============================================================================
-- 2. FACE CONFIGURATION VIEW (Step 2: After selecting a sensor)
-- ==============================================================================

local function renderConfigView(inst, snapshot)
  local children = {}
  local accentHex = getColorHex(inst.color)

  -- Top Navigation Banner: Back to Sensor List + Current Sensor info
  local sensorName = inst.sensor_name or inst.sensor_id or "Selected Sensor"
  local valText = inst.val_text or tostring(inst.value or "")

  table.insert(children, ui.row({
    align = "center",
    justify = "space_between",
    gap = 12,
    padding = 8,
    radius = 8,
    border = "primary/0.5",
    borderWidth = 1,
    fill = "primary/0.12",
  }, {
    ui.button({
      text = "← Back to Sensor List",
      glyph = "arrow-left",
      variant = "secondary",
      controlSize = "sm",
      onClick = function()
        currentView = "picker"
        render()
      end,
    }),
    ui.column({ align = "end", gap = 2 }, {
      ui.label({ text = "CONFIGURING INSTANCE: " .. string.upper(activeInstanceId), fontSize = 10, fontWeight = "bold", color = "primary" }),
      ui.label({ text = sensorName .. "  ·  Live: " .. valText, fontSize = 13, fontWeight = "bold", color = "on_surface" }),
    }),
  }))

  -- Global Theme & Color Dropdown
  local colorIndex = COLOR_INDEX[inst.color] or 0
  table.insert(children, ui.row({ align = "center", gap = 12 }, {
    ui.label({ text = "Color Palette Accent:", fontSize = 12, fontWeight = "bold", color = "on_surface" }),
    ui.select({
      options = COLOR_OPTIONS,
      selectedIndex = colorIndex,
      width = 240,
      controlSize = "sm",
      onChange = function(val)
        local idx = tonumber(val) or 0
        local sel = COLOR_PALETTES[idx + 1]
        if sel then
          inst.color = sel.id
          saveInstance(inst)
          render()
        end
      end,
    }),
  }))

  -- Fieldset 1: Static Icon
  if lastIconInstanceId ~= inst.id then
    currentIconDraft = inst.icon_val or ""
    lastIconInstanceId = inst.id
  end

  local iconPreview = ui.spacer()
  if inst.show_icon then
    if inst.icon_type == "emoji" then
      iconPreview = ui.label({ text = inst.icon_val or "⚡", fontSize = 14 })
    else
      iconPreview = ui.glyph({ name = inst.icon_val or "gauge", size = 18, color = accentHex })
    end
  end

  local iconTypeIndex = (inst.icon_type == "emoji") and 1 or 0
  local iconBody = {
    ui.row({ align = "center", gap = 12 }, {
      ui.label({ text = "Type:", fontSize = 11, color = "on_surface_variant" }),
      ui.select({
        options = { "Tabler Nerd Glyph", "Emoji / Custom Text" },
        selectedIndex = iconTypeIndex,
        width = 200,
        controlSize = "sm",
        onChange = function(val)
          local idx = tonumber(val) or 0
          inst.icon_type = (idx == 1) and "emoji" or "glyph"
          saveInstance(inst)
          render()
        end,
      }),
      ui.label({ text = "Icon:", fontSize = 11, color = "on_surface_variant" }),
      ui.input({
        key = "icon-input-" .. tostring(inst.id) .. "-" .. tostring(inst.sensor_id or ""),
        value = currentIconDraft or inst.icon_val or "",
        placeholder = (inst.icon_type == "emoji") and "e.g. 🔥, 💾, ⚡" or "e.g. cpu, flame, database",
        width = 160,
        controlSize = "sm",
        onChange = function(val)
          currentIconDraft = tostring(val or "")
        end,
        onSubmit = function(val)
          local txt = tostring(val or "")
          currentIconDraft = txt
          inst.icon_val = txt
          saveInstance(inst)
          render()
        end,
      }),
      ui.button({
        text = "Apply",
        controlSize = "sm",
        variant = "outline",
        onClick = function()
          inst.icon_val = currentIconDraft or inst.icon_val or ""
          saveInstance(inst)
          render()
        end,
      }),
      iconPreview,
    }),
  }
  table.insert(children, renderGroupBox("Static Icon", inst.show_icon, function()
    inst.show_icon = not inst.show_icon
    saveInstance(inst)
    render()
  end, iconBody))

  -- Fieldset 2: Dynamic Graphic Representation
  local graphIndex = GRAPH_INDEX[inst.graph_type] or 0
  local strokeIndex = STROKE_INDEX[inst.stroke_width] or 1

  local graphicBody = {
    ui.row({ align = "center", gap = 12 }, {
      ui.label({ text = "Style:", fontSize = 11, color = "on_surface_variant" }),
      ui.select({
        options = GRAPH_OPTIONS,
        selectedIndex = graphIndex,
        width = 280,
        controlSize = "sm",
        onChange = function(val)
          local idx = tonumber(val) or 0
          local sel = GRAPH_STYLES[idx + 1]
          if sel then
            inst.graph_type = sel.id
            saveInstance(inst)
            render()
          end
        end,
      }),
      ui.label({ text = "Stroke Width:", fontSize = 11, color = "on_surface_variant" }),
      ui.select({
        options = STROKE_OPTIONS,
        selectedIndex = strokeIndex,
        width = 180,
        controlSize = "sm",
        onChange = function(val)
          local idx = tonumber(val) or 0
          local sel = STROKE_WIDTHS[idx + 1]
          if sel then
            inst.stroke_width = sel.width
            saveInstance(inst)
            render()
          end
        end,
      }),
    }),
  }
  table.insert(children, renderGroupBox("Dynamic Graphic (Gauge)", inst.show_graph, function()
    inst.show_graph = not inst.show_graph
    saveInstance(inst)
    render()
  end, graphicBody))

  -- Fieldset 3: Numerical Value
  local decimalsIndex = math.max(0, math.min(2, inst.decimals or 0))
  local unitStr = inst.unit or ""
  local unitSuffix = (unitStr ~= "") and (" " .. unitStr) or ""
  if unitStr == "%" or unitStr == "°C" then
    unitSuffix = unitStr
  end
  local opt0 = "0 Decimals (Integer, e.g. 34" .. unitSuffix .. ")"
  local opt1 = "1 Decimal (e.g. 34.5" .. unitSuffix .. ")"
  local opt2 = "2 Decimals (e.g. 34.52" .. unitSuffix .. ")"
  local valueBody = {
    ui.row({ align = "center", gap = 12 }, {
      ui.label({ text = "Decimal Precision:", fontSize = 11, color = "on_surface_variant" }),
      ui.select({
        options = { opt0, opt1, opt2 },
        selectedIndex = decimalsIndex,
        width = 260,
        controlSize = "sm",
        onChange = function(val)
          local idx = tonumber(val) or 0
          inst.decimals = idx
          saveInstance(inst)
          render()
        end,
      }),
      ui.label({ text = "Live Reading: " .. tostring(inst.val_text or ""), fontSize = 12, fontWeight = "bold", color = accentHex }),
    }),
  }
  table.insert(children, renderGroupBox("Numerical Value", inst.show_value, function()
    inst.show_value = not inst.show_value
    saveInstance(inst)
    render()
  end, valueBody))

  -- Fieldset 4: Custom Label
  if lastLabelInstanceId ~= inst.id then
    currentLabelDraft = inst.label or ""
    lastLabelInstanceId = inst.id
  end

  local labelBody = {
    ui.row({ align = "center", gap = 8 }, {
      ui.label({ text = "Label Text:", fontSize = 11, color = "on_surface_variant" }),
      ui.input({
        key = "label-input-" .. tostring(inst.id) .. "-" .. tostring(inst.sensor_id or ""),
        value = currentLabelDraft or inst.label or "",
        placeholder = "e.g. CPU, RAM, GPU, /home, Fans",
        width = 200,
        controlSize = "sm",
        onChange = function(val)
          currentLabelDraft = tostring(val or "")
        end,
        onSubmit = function(val)
          local txt = tostring(val or "")
          currentLabelDraft = txt
          inst.label = txt
          saveInstance(inst)
          render()
        end,
      }),
      ui.button({
        text = "Apply",
        controlSize = "sm",
        variant = "outline",
        onClick = function()
          inst.label = currentLabelDraft or inst.label or ""
          saveInstance(inst)
          render()
        end,
      }),
    }),
  }
  table.insert(children, renderGroupBox("Custom Label", inst.show_label, function()
    inst.show_label = not inst.show_label
    saveInstance(inst)
    render()
  end, labelBody))

  -- Live Preview Strip
  local barImg = (inst.bar_img and inst.bar_img ~= "") and inst.bar_img or nil

  local previewItems = {}
  if inst.show_icon and inst.icon_val and inst.icon_val ~= "" then
    if inst.icon_type == "emoji" then
      table.insert(previewItems, ui.label({ text = inst.icon_val, fontSize = 14 }))
    else
      table.insert(previewItems, ui.glyph({ name = inst.icon_val, size = 16, color = accentHex }))
    end
  end
  if inst.show_graph and barImg then
    local previewSize = tonumber(inst.bar_size) or 34
    table.insert(previewItems, ui.image({ path = barImg, width = previewSize, height = previewSize }))
  end
  if inst.show_value and inst.val_text and inst.val_text ~= "" then
    table.insert(previewItems, ui.label({ text = inst.val_text, fontSize = 12, fontWeight = "bold", color = "on_surface" }))
  end
  if inst.show_label and inst.label and inst.label ~= "" then
    table.insert(previewItems, ui.label({ text = inst.label, fontSize = 11, color = "on_surface_variant" }))
  end

  local previewBox = ui.row({
    align = "center",
    gap = 8,
    padding = 8,
    radius = 8,
    fill = "surface_variant/0.4",
  }, previewItems)

  table.insert(children, ui.row({ align = "center", gap = 16, padding = 4 }, {
    ui.label({ text = "STATUS BAR PREVIEW:", fontSize = 11, fontWeight = "bold", color = "outline" }),
    previewBox,
  }))

  return ui.scroll({
    flexGrow = 1,
    gap = 12,
    padding = 4,
  }, children)
end

-- ==============================================================================
-- Main Render Dispatcher
-- ==============================================================================

render = function()
  local snapshot = getSnapshot()
  if not snapshot or not snapshot.instances then
    panel.render(ui.column({
      padding = 24,
      align = "center",
      gap = 12,
    }, {
      ui.glyph({ name = "loader", size = 32, color = "primary" }),
      ui.label({ text = "Discovering hardware and storage sensors...", fontSize = 14, fontWeight = "bold" }),
    }))
    return
  end

  local instances = snapshot.instances or {}
  local inst = instances[activeInstanceId]
  if not inst then
    inst = createDefaultInstance(activeInstanceId)
    instances[activeInstanceId] = inst
    saveInstance(inst)
  end

  local rootChildren = {}

  -- Clean Header: Icon + Title + Close Button
  table.insert(rootChildren, ui.row({
    align = "center",
    justify = "space_between",
    padding = 4,
  }, {
    ui.row({ align = "center", gap = 8 }, {
      ui.glyph({ name = "gauge", size = 26, color = "primary" }),
      ui.column({ gap = 1 }, {
        ui.label({ text = "TachoPulse Studio", fontSize = 16, fontWeight = "bold", color = "on_surface" }),
        ui.label({ text = "Universal Hardware & Storage Telemetry Super-Widget", fontSize = 11, color = "on_surface_variant" }),
      }),
    }),
    ui.button({
      glyph = "x",
      controlSize = "sm",
      variant = "ghost",
      onClick = panel.close,
    }),
  }))

  table.insert(rootChildren, ui.separator())

  -- View Dispatcher: Sensor Picker (step 1) vs Face Config (step 2)
  if currentView == "config" and inst and inst.sensor_id and inst.sensor_id ~= "" then
    table.insert(rootChildren, renderConfigView(inst, snapshot))
  else
    table.insert(rootChildren, renderPickerView(inst, snapshot))
  end

  panel.render(ui.column({
    padding = 14,
    gap = 10,
    flexGrow = 1,
  }, rootChildren))
end

local function switchActiveInstance(target)
  if type(target) == "string" and target ~= "" and target ~= activeInstanceId then
    activeInstanceId = target
    currentLabelDraft = nil
    currentIconDraft = nil
    lastLabelInstanceId = nil
    lastIconInstanceId = nil
    currentView = "picker"
    return true
  end
  return false
end

function onOpen(context)
  local target = (type(context) == "string" and context ~= "") and context or noctalia.state.get("tachopulse.active_instance")
  switchActiveInstance(target)
  render()
end

noctalia.state.watch("tachopulse.active_instance", function(val)
  if switchActiveInstance(val) then
    render()
  end
end)

noctalia.state.watch("tachopulse.snapshot", function(snap)
  if type(snap) == "table" and snap.instances then
    render()
  end
end)

render()
