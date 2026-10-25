--!nonstrict
-- TachoPulse Desktop Wallpaper Widget (`[[desktop_widget]]`).
--
-- Renders all active sensors marked `on_desktop = true` in a multi-column
-- translucent card grid on the desktop background layer-shell surface.

local snapshot = nil

local function renderDesktopCard(item, idx, stamp)
  local children = {}
  local isRingGraph = item.show_graph and item.graph_type == "ring"

  if item.show_icon or (item.show_label and not isRingGraph) then
    local headerItems = {}
    if item.show_icon then
      table.insert(headerItems, ui.glyph({
        name = item.icon or "cpu",
        fontSize = 14,
        color = "primary",
      }))
    end
    if item.show_label and not isRingGraph then
      table.insert(headerItems, ui.label({
        text = tostring(item.label or ""),
        fontSize = 12,
        fontWeight = "bold",
        color = "primary",
      }))
    end
    if #headerItems > 0 then
      table.insert(children, ui.row({
        gap = 5,
        align = "center",
        justify = "center",
      }, headerItems))
    end
  end

  if item.show_graph and item.desk_img and item.desk_img ~= "" and noctalia.fileExists(item.desk_img) then
    table.insert(children, ui.image({
      key = "tp-desk-" .. tostring(idx) .. "-" .. tostring(stamp),
      path = item.desk_img,
      width = tonumber(item.desk_w) or 96,
      height = tonumber(item.desk_h) or 96,
    }))
  else
    if item.show_value then
      table.insert(children, ui.label({
        text = tostring(item.val_text or ""),
        fontSize = 22,
        fontWeight = "bold",
        color = "on_surface",
      }))
    end
    if item.show_label and isRingGraph then
      table.insert(children, ui.label({
        text = tostring(item.label or ""),
        fontSize = 12,
        fontWeight = "bold",
        color = "primary",
      }))
    end
  end

  if not isRingGraph and item.show_value and item.sub_text and item.sub_text ~= "" then
    table.insert(children, ui.label({
      text = tostring(item.sub_text),
      fontSize = 10,
      color = "on_surface_variant",
    }))
  end

  return ui.column({
    gap = 4,
    padding = 8,
    radius = 14,
    fill = "surface_variant/0.55",
    align = "center",
    justify = "center",
  }, children)
end

local function render()
  if type(snapshot) ~= "table" or type(snapshot.active) ~= "table" then
    desktopWidget.render(ui.column({
      gap = 6,
      padding = 12,
      radius = 14,
      fill = "surface_variant/0.55",
      align = "center",
    }, {
      ui.glyph({ name = "gauge", fontSize = 24, color = "primary" }),
      ui.label({ text = "TachoPulse", fontSize = 14, fontWeight = "bold" }),
    }))
    return
  end

  local cols = tonumber(noctalia.getConfig("desktop_columns")) or 3
  if cols < 1 then
    cols = 1
  end

  local stamp = tostring(snapshot.stamp or "0")
  local cards = {}
  for idx, item in ipairs(snapshot.active) do
    if item.on_desktop then
      table.insert(cards, renderDesktopCard(item, idx, stamp))
    end
  end

  if #cards == 0 then
    desktopWidget.render(ui.column({
      gap = 4,
      padding = 10,
      radius = 12,
      fill = "surface_variant/0.4",
      align = "center",
    }, {
      ui.label({ text = "TachoPulse (No desktop sensors enabled)", fontSize = 11, color = "outline" }),
    }))
    return
  end

  local rows = {}
  local currentRow = {}
  for i, card in ipairs(cards) do
    table.insert(currentRow, card)
    if #currentRow >= cols or i == #cards then
      table.insert(rows, ui.row({
        gap = 8,
        align = "center",
        justify = "center",
      }, currentRow))
      currentRow = {}
    end
  end

  desktopWidget.render(ui.column({
    gap = 8,
    padding = 8,
    align = "center",
  }, rows))
end

noctalia.state.watch("tachopulse.snapshot", function(val)
  snapshot = val
  render()
end)

function onConfigChanged()
  render()
end

function update()
  render()
end

snapshot = noctalia.state.get("tachopulse.snapshot")
render()
