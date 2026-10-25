--!nonstrict
-- TachoPulse Sensor Studio Panel (`[[panel]]`).
--
-- Interactive KDE System Monitor-style sensor browser and per-sensor visual
-- configurator. Allows browsing all dynamically discovered `/sys`, `/proc`,
-- `hwmon`, `drm`, and `nvtop` sensors, adding/removing/reordering sensors,
-- switching between `Ring`, `Bar`, and `Histogram` graphs, and toggling
-- `Icon`, `Label`, `Value`, `Graph`, `Top Bar`, and `Desktop` per sensor.

local snapshot = nil
local currentTab = "active" -- "active" | "browser"
local selectedCategory = "ALL"
local searchQuery = ""
local searchInputKey = 0

local function sendCmd(payload)
  payload.ts = noctalia.nowMs()
  noctalia.state.set("tachopulse.cmd", payload)
end

local render

local function renderHeader(activeCount, catalogCount)
  return ui.column({ gap = 8 }, {
    ui.row({ align = "center", justify = "space_between" }, {
      ui.row({ gap = 8, align = "center" }, {
        ui.glyph({ name = "gauge", fontSize = 20, color = "primary" }),
        ui.column({ gap = 1 }, {
          ui.label({
            text = noctalia.tr("ui.title"),
            fontSize = 15,
            fontWeight = "bold",
            color = "on_surface",
          }),
          ui.label({
            text = noctalia.tr("ui.subtitle"),
            fontSize = 11,
            color = "on_surface_variant",
          }),
        }),
      }),
      ui.row({ gap = 6, align = "center" }, {
        ui.button({
          text = noctalia.tr("ui.reset_defaults"),
          variant = "ghost",
          controlSize = "sm",
          onClick = function()
            sendCmd({ action = "reset_defaults" })
          end,
        }),
        ui.button({
          glyph = "settings",
          variant = "ghost",
          controlSize = "sm",
          onClick = function()
            noctalia.openSettings()
          end,
        }),
      }),
    }),
    ui.row({ gap = 6, align = "center" }, {
      ui.button({
        text = noctalia.tr("ui.tab_active") .. " (" .. tostring(activeCount) .. ")",
        variant = currentTab == "active" and "primary" or "secondary",
        controlSize = "sm",
        onClick = function()
          currentTab = "active"
          render()
        end,
      }),
      ui.button({
        text = noctalia.tr("ui.tab_browser") .. " (" .. tostring(catalogCount) .. ")",
        variant = currentTab == "browser" and "primary" or "secondary",
        controlSize = "sm",
        onClick = function()
          currentTab = "browser"
          render()
        end,
      }),
    }),
  })
end

local function renderLivePreviewStrip(activeList, stamp)
  local previews = {}
  for idx, item in ipairs(activeList) do
    if item.show_graph and item.desk_img and item.desk_img ~= "" and noctalia.fileExists(item.desk_img) then
      table.insert(previews, ui.column({
        gap = 2,
        padding = 4,
        radius = 10,
        fill = "surface/0.7",
        align = "center",
      }, {
        ui.image({
          key = "tp-pnl-prev-" .. tostring(idx) .. "-" .. tostring(stamp),
          path = item.desk_img,
          width = 68,
          height = item.graph_type == "ring" and 68 or (item.graph_type == "bar" and 24 or 46),
        }),
      }))
    end
  end

  if #previews == 0 then
    return nil
  end

  return ui.scroll({
    horizontal = true,
    padding = 6,
    radius = 12,
    fill = "surface_variant/0.5",
  }, {
    ui.row({ gap = 8, align = "center" }, previews),
  })
end

local function renderActiveTab(activeList, stamp)
  if #activeList == 0 then
    return ui.column({
      flexGrow = 1,
      align = "center",
      justify = "center",
      padding = 24,
    }, {
      ui.label({
        text = noctalia.tr("ui.no_active"),
        fontSize = 13,
        color = "outline",
      }),
    })
  end

  local rows = {}
  for idx, item in ipairs(activeList) do
    local sub = (item.sub_text and item.sub_text ~= "") and (" · " .. tostring(item.sub_text)) or ""
    table.insert(rows, ui.column({
      key = "active-row-" .. tostring(idx) .. "-" .. tostring(item.id),
      gap = 6,
      padding = 8,
      radius = 10,
      fill = "surface_variant/0.45",
      border = "outline/0.3",
      borderWidth = 1,
    }, {
      -- Top line: Sensor title, live value, reorder & delete buttons
      ui.row({ align = "center", justify = "space_between" }, {
        ui.row({ gap = 6, align = "center", flexGrow = 1 }, {
          ui.glyph({
            name = item.icon or "cpu",
            fontSize = 15,
            color = "primary",
          }),
          ui.column({ gap = 1, flexGrow = 1 }, {
            ui.label({
              text = tostring(item.label or "") .. " — " .. tostring(item.name or item.id),
              fontSize = 12,
              fontWeight = "bold",
              color = "on_surface",
              maxLines = 1,
            }),
            ui.label({
              text = tostring(item.category or "") .. " · " .. tostring(item.val_text or "") .. sub,
              fontSize = 10,
              color = "on_surface_variant",
              maxLines = 1,
            }),
          }),
        }),
        ui.row({ gap = 4, align = "center" }, {
          ui.button({
            text = tostring(item.color or "blue"),
            variant = "secondary",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "cycle_color", index = idx })
            end,
          }),
          ui.button({
            text = "▲",
            variant = "ghost",
            controlSize = "sm",
            enabled = idx > 1,
            onClick = function()
              sendCmd({ action = "move", index = idx, delta = -1 })
            end,
          }),
          ui.button({
            text = "▼",
            variant = "ghost",
            controlSize = "sm",
            enabled = idx < #activeList,
            onClick = function()
              sendCmd({ action = "move", index = idx, delta = 1 })
            end,
          }),
          ui.button({
            text = "✕",
            variant = "destructive",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "remove", index = idx })
            end,
          }),
        }),
      }),

      -- Bottom line: Graph type (Ring / Bar / Hist) + Display element toggles
      ui.row({ gap = 4, align = "center", justify = "space_between" }, {
        ui.row({ gap = 4, align = "center" }, {
          ui.button({
            text = noctalia.tr("ui.graph_ring"),
            variant = item.graph_type == "ring" and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "set_graph_type", index = idx, graph_type = "ring" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.graph_bar"),
            variant = item.graph_type == "bar" and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "set_graph_type", index = idx, graph_type = "bar" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.graph_histogram"),
            variant = item.graph_type == "histogram" and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "set_graph_type", index = idx, graph_type = "histogram" })
            end,
          }),
        }),
        ui.row({ gap = 4, align = "center" }, {
          ui.button({
            text = noctalia.tr("ui.toggle_icon"),
            variant = item.show_icon and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "toggle_field", index = idx, field = "show_icon" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.toggle_label"),
            variant = item.show_label and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "toggle_field", index = idx, field = "show_label" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.toggle_value"),
            variant = item.show_value and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "toggle_field", index = idx, field = "show_value" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.toggle_graph"),
            variant = item.show_graph and "primary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "toggle_field", index = idx, field = "show_graph" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.toggle_bar"),
            variant = item.on_bar and "secondary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "toggle_field", index = idx, field = "on_bar" })
            end,
          }),
          ui.button({
            text = noctalia.tr("ui.toggle_desktop"),
            variant = item.on_desktop and "secondary" or "ghost",
            controlSize = "sm",
            onClick = function()
              sendCmd({ action = "toggle_field", index = idx, field = "on_desktop" })
            end,
          }),
        }),
      }),
    }))
  end

  local children = {}
  local previewStrip = renderLivePreviewStrip(activeList, stamp)
  if previewStrip then
    table.insert(children, previewStrip)
  end

  table.insert(children, ui.scroll({
    flexGrow = 1,
    gap = 6,
    padding = 2,
  }, rows))

  return ui.column({
    flexGrow = 1,
    gap = 8,
  }, children)
end

local function renderBrowserTab(catalogList)
  local categories = { "ALL" }
  local seenCat = { ALL = true }
  for _, s in ipairs(catalogList) do
    local c = tostring(s.category or "Other")
    if not seenCat[c] then
      seenCat[c] = true
      table.insert(categories, c)
    end
  end

  local catButtons = {}
  for _, c in ipairs(categories) do
    local label = (c == "ALL") and noctalia.tr("ui.all_categories") or c
    table.insert(catButtons, ui.button({
      text = label,
      variant = (selectedCategory == c) and "primary" or "ghost",
      controlSize = "sm",
      onClick = function()
        selectedCategory = c
        render()
      end,
    }))
  end

  local q = string.lower(searchQuery or "")
  local filtered = {}
  for _, s in ipairs(catalogList) do
    local catOk = (selectedCategory == "ALL") or (tostring(s.category) == selectedCategory)
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

  local itemRows = {}
  for i, s in ipairs(filtered) do
    if i > 120 then
      break
    end
    local sub = (s.sub_text and s.sub_text ~= "") and (" (" .. tostring(s.sub_text) .. ")") or ""
    table.insert(itemRows, ui.row({
      key = "cat-item-" .. tostring(s.id),
      gap = 8,
      padding = 8,
      radius = 8,
      fill = "surface_variant/0.4",
      align = "center",
      justify = "space_between",
    }, {
      ui.row({ gap = 8, align = "center", flexGrow = 1 }, {
        ui.glyph({
          name = s.icon or "cpu",
          fontSize = 15,
          color = "primary",
        }),
        ui.column({ gap = 1, flexGrow = 1 }, {
          ui.label({
            text = tostring(s.name or s.id),
            fontSize = 12,
            fontWeight = "bold",
            color = "on_surface",
            maxLines = 1,
          }),
          ui.label({
            text = tostring(s.category or "") .. " · [" .. tostring(s.short_label or "") .. "] · " .. tostring(s.val_text or "") .. sub,
            fontSize = 10,
            color = "on_surface_variant",
            maxLines = 1,
          }),
        }),
      }),
      ui.button({
        text = noctalia.tr("ui.add_btn"),
        variant = "primary",
        controlSize = "sm",
        onClick = function()
          sendCmd({ action = "add", sensor = s })
          currentTab = "active"
          render()
        end,
      }),
    }))
  end

  if #itemRows == 0 then
    table.insert(itemRows, ui.label({
      text = noctalia.tr("ui.no_matches"),
      fontSize = 12,
      color = "outline",
    }))
  end

  return ui.column({
    flexGrow = 1,
    gap = 8,
  }, {
    ui.input({
      key = "tp-search-" .. tostring(searchInputKey),
      value = searchQuery,
      placeholder = noctalia.tr("ui.search_placeholder"),
      fontSize = 12,
      onChange = "onSearchChange",
    }),
    ui.scroll({
      horizontal = true,
      padding = 2,
    }, {
      ui.row({ gap = 4, align = "center" }, catButtons),
    }),
    ui.scroll({
      flexGrow = 1,
      gap = 4,
      padding = 2,
    }, itemRows),
  })
end

render = function()
  local activeList = (type(snapshot) == "table" and type(snapshot.active) == "table") and snapshot.active or {}
  local catalogList = (type(snapshot) == "table" and type(snapshot.catalog) == "table") and snapshot.catalog or {}
  local stamp = (type(snapshot) == "table" and snapshot.stamp) and tostring(snapshot.stamp) or "0"

  local body
  if currentTab == "browser" then
    body = renderBrowserTab(catalogList)
  else
    body = renderActiveTab(activeList, stamp)
  end

  panel.render(ui.column({
    flexGrow = 1,
    gap = 10,
    padding = 12,
  }, {
    renderHeader(#activeList, #catalogList),
    ui.separator(),
    body,
  }))
end

function onSearchChange(val)
  searchQuery = tostring(val or "")
  render()
end

function onOpen(_context)
  snapshot = noctalia.state.get("tachopulse.snapshot")
  render()
end

noctalia.state.watch("tachopulse.snapshot", function(val)
  snapshot = val
  render()
end)

snapshot = noctalia.state.get("tachopulse.snapshot")
render()
