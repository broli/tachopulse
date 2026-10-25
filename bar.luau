--!nonstrict
-- TachoPulse Status Bar Widget (`[[widget]]`).
--
-- Subscribes to `tachopulse.snapshot` published by `service.luau`, rendering
-- all sensors marked `on_bar = true` with adaptive sizing (`< 52px` compact
-- inline mode vs `>= 52px` full 3-line inner tachometer mode) and per-sensor
-- toggles (`show_icon`, `show_label`, `show_value`, `show_graph`).

local snapshot = nil
local isVertical = false

local function renderSensorCell(item, idx, stamp)
  local parts = {}
  local isFullRing = item.show_graph and item.graph_type == "ring" and (tonumber(item.bar_w) or 34) >= 52

  if item.show_icon then
    table.insert(parts, ui.glyph({
      name = item.icon or "cpu",
      fontSize = 14,
      color = "primary",
    }))
  end

  if item.show_graph and item.bar_img and item.bar_img ~= "" and noctalia.fileExists(item.bar_img) then
    table.insert(parts, ui.image({
      key = "tp-bar-" .. tostring(idx) .. "-" .. tostring(stamp),
      path = item.bar_img,
      width = tonumber(item.bar_w) or 34,
      height = tonumber(item.bar_h) or 34,
    }))
  end

  -- When in full 3-line ring mode (>= 52px), label and value are drawn inside the ring itself.
  -- In compact ring mode (< 52px), bar/histogram mode, or text-only mode, render clean inline text.
  if not isFullRing then
    local textLines = {}
    if item.show_label then
      table.insert(textLines, ui.label({
        text = tostring(item.label or ""),
        fontSize = 10,
        fontWeight = "bold",
        color = "primary",
        maxLines = 1,
      }))
    end

    local showValInline = item.show_value and (not item.show_graph or item.graph_type ~= "ring")
    if showValInline then
      table.insert(textLines, ui.label({
        text = tostring(item.val_text or ""),
        fontSize = 10,
        fontWeight = "medium",
        color = "on_surface",
        maxLines = 1,
      }))
    elseif item.show_label and item.show_value and item.sub_text and item.sub_text ~= "" then
      table.insert(textLines, ui.label({
        text = tostring(item.sub_text),
        fontSize = 9,
        color = "on_surface_variant",
        maxLines = 1,
      }))
    end

    if #textLines > 0 then
      table.insert(parts, ui.column({
        gap = 0,
        justify = "center",
      }, textLines))
    end
  end

  if #parts == 0 then
    table.insert(parts, ui.label({
      text = tostring(item.label or item.val_text or "•"),
      fontSize = 11,
      fontWeight = "bold",
    }))
  end

  if isVertical then
    return ui.column({
      gap = 2,
      align = "center",
      paddingV = 2,
    }, parts)
  end

  return ui.row({
    gap = 4,
    align = "center",
    paddingH = 3,
  }, parts)
end

local function render()
  isVertical = barWidget.isVertical()
  if type(snapshot) ~= "table" or type(snapshot.active) ~= "table" then
    barWidget.render(ui.row({ gap = 4, align = "center", paddingH = 6 }, {
      ui.glyph({ name = "gauge", fontSize = 14, color = "primary" }),
      ui.label({ text = "TachoPulse", fontSize = 11, fontWeight = "bold" }),
    }))
    return
  end

  local stamp = tostring(snapshot.stamp or "0")
  local cells = {}
  local tooltipRows = {}

  for idx, item in ipairs(snapshot.active) do
    local sub = (item.sub_text and item.sub_text ~= "") and (" (" .. tostring(item.sub_text) .. ")") or ""
    table.insert(tooltipRows, {
      key = tostring(item.name or item.label or item.id),
      value = tostring(item.val_text or "") .. sub,
    })

    if item.on_bar then
      table.insert(cells, renderSensorCell(item, idx, stamp))
    end
  end

  if #tooltipRows > 0 then
    barWidget.setTooltip(tooltipRows)
  end

  if #cells == 0 then
    barWidget.render(ui.row({ gap = 4, align = "center", paddingH = 6 }, {
      ui.glyph({ name = "gauge", fontSize = 14, color = "primary" }),
    }))
    return
  end

  if isVertical then
    barWidget.render(ui.column({
      gap = 4,
      align = "center",
      paddingV = 4,
    }, cells))
  else
    barWidget.render(ui.row({
      gap = 6,
      align = "center",
      paddingH = 4,
    }, cells))
  end
end

noctalia.state.watch("tachopulse.snapshot", function(val)
  snapshot = val
  render()
end)

function update()
  render()
end

function onClick()
  noctalia.togglePanel("broli/tachopulse:panel")
end

snapshot = noctalia.state.get("tachopulse.snapshot")
render()
